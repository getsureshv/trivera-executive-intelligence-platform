"""Audit recording and chain verification (ADR-014 §5).

Two properties define this module.

**Transactional consistency.** ``record`` takes the *caller's* session and
writes inside the caller's transaction. An audit row and the change it
describes commit together or not at all. This is why audit is a database table
and not a log stream: log pipelines drop records under load, and an audit trail
that drops records is not an audit trail.

**Tamper evidence.** Each tenant's events form a hash chain over *every*
immutable field, anchored by a checkpoint (``audit_chain_head``) that no runtime
or platform role may write. Together these detect mutation, interior deletion,
tail deletion, truncation to a prefix, and total erasure — see ``verify_chain``
for the exact matrix and for the one blind spot that remains.

Note what changed and why: the first implementation hashed only a subset of
columns and had no checkpoint, so ``occurred_at``, ``actor_type``, ``trace_id``,
and ``request_id`` could be rewritten undetected, and deleting the tail or the
whole chain left a perfectly valid remainder. The guarantee documented at the
time was therefore stronger than the implementation.

Sequence allocation is serialised per tenant with a transaction-scoped advisory
lock. Without it, two concurrent writers could read the same ``prev_hash`` and
produce a fork; the unique constraint on ``(tenant_id, seq)`` would catch it,
but as a failed request rather than a correct one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from eip.governance.models import AuditEvent
from eip.platform.context import ActorType, PlatformContext, RoleCode, TenantContext
from eip.platform.logging import get_logger, redact

_log = get_logger("governance.audit")

#: The chain's anchor. 64 zeros keeps every hash column a fixed width.
GENESIS_HASH: Final = "0" * 64

#: Advisory-lock namespace, so audit locks cannot collide with other features'.
_AUDIT_LOCK_NAMESPACE: Final = 0x41554449  # "AUDI"


class AuditAction:
    """Audited action names.

    A closed set of constants rather than free strings: audit queries and
    alerting rules are written against these, and a typo in an action name
    silently removes an event from every downstream rule.
    """

    # authentication / session
    SIGN_IN_SUCCEEDED = "auth.sign_in.succeeded"
    SIGN_IN_FAILED = "auth.sign_in.failed"
    TOKEN_ISSUED = "auth.token.issued"  # noqa: S105 - an action name, not a credential

    # tenant context / access
    TENANT_CONTEXT_ESTABLISHED = "tenant.context.established"
    TENANT_ACCESS_DENIED = "tenant.access.denied"
    CROSS_TENANT_ACCESS_ATTEMPTED = "tenant.access.cross_tenant_attempted"

    # administrative / configuration mutations
    TENANT_REGISTERED = "tenant.registered"
    TENANT_PROVISIONED = "tenant.provisioned"
    TENANT_PROVISIONING_FAILED = "tenant.provisioning_failed"
    TENANT_DEPROVISIONED = "tenant.deprovisioned"
    MEMBERSHIP_GRANTED = "membership.granted"
    MEMBERSHIP_REVOKED = "membership.revoked"
    SOURCE_CREATED = "source.created"
    SOURCE_UPDATED = "source.updated"
    SOURCE_CREDENTIAL_ROTATED = "source.credential_rotated"
    SOURCE_ACCESS_DENIED = "source.access.denied"
    SOURCE_DELETED = "source.deleted"
    SOURCE_CREDENTIAL_DESTROYED = "source.credential_destroyed"
    CONNECTION_TESTS_PRUNED = "connection_test.pruned"
    CONNECTION_TEST_REQUESTED = "connection_test.requested"
    CONNECTION_TEST_COMPLETED = "connection_test.completed"
    CONNECTION_TEST_FAILED = "connection_test.failed"
    CONNECTION_TEST_DENIED = "connection_test.denied"

    # background processing (ADR-009)
    OUTBOX_RELAYED = "outbox.relayed"

    # privileged access (ADR-010 §5)
    PLATFORM_ELEVATION_USED = "platform.elevation.used"


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(
    *,
    prev_hash: str,
    tenant_id: uuid.UUID,
    seq: int,
    occurred_at: datetime,
    actor_type: str,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    trace_id: str,
    request_id: str,
    detail: dict[str, Any],
) -> str:
    """Compute a chain hash over **every immutable field** of the event.

    The original implementation covered only a subset, leaving ``occurred_at``,
    ``actor_type``, ``trace_id``, and ``request_id`` outside the digest — so
    those columns could be rewritten and the chain would still verify. Backdating
    an event or reattributing it from ``user`` to ``system`` is exactly the kind
    of alteration an audit trail exists to detect, so they are covered now.

    ``occurred_at`` is normalised to UTC and serialised at microsecond
    resolution, matching what PostgreSQL stores in a ``timestamptz``. Without
    that normalisation, verification would fail spuriously whenever the reading
    session's timezone differed from the writing session's.

    Pure, so it can be re-derived during verification.
    """
    body = _canonical(
        {
            "prev": prev_hash,
            "tenant_id": str(tenant_id),
            "seq": seq,
            "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "actor_type": actor_type,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "trace_id": trace_id,
            "request_id": request_id,
            "detail": detail,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def _lock_tenant_chain(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Serialise chain appends for one tenant, for this transaction only.

    Released automatically at commit or rollback. Two-argument form so the
    namespace keeps audit locks disjoint from any other advisory-lock use.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": _AUDIT_LOCK_NAMESPACE, "key": tenant_id.int % (2**31)},
    )


async def record(
    session: AsyncSession,
    context: TenantContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event **inside the caller's transaction**.

    ``detail`` is passed through ``redact()``: secrets and business values are
    dropped, not masked (ADR-014 §6). Callers should not rely on this as their
    only protection, but it is the backstop that makes a careless ``detail``
    payload non-catastrophic.
    """
    safe_detail = redact(detail or {})

    await _lock_tenant_chain(session, context.tenant_id)

    previous = (
        await session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.tenant_id == context.tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
    ).one_or_none()

    seq = (previous.seq + 1) if previous else 1
    prev_hash = previous.hash if previous else GENESIS_HASH

    # Set explicitly rather than leaving it to the column's server default:
    # `occurred_at` is inside the digest, so the value hashed must be exactly
    # the value stored. A server default would be assigned after the hash was
    # computed and every event would fail verification.
    occurred_at = datetime.now(UTC)
    actor_type = context.principal.actor_type.value

    event = AuditEvent(
        tenant_id=context.tenant_id,
        seq=seq,
        occurred_at=occurred_at,
        actor_type=actor_type,
        actor_user_id=context.principal.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        trace_id=context.trace_id,
        request_id=context.request_id,
        detail=safe_detail,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash=prev_hash,
            tenant_id=context.tenant_id,
            seq=seq,
            occurred_at=occurred_at,
            actor_type=actor_type,
            actor_user_id=context.principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            trace_id=context.trace_id,
            request_id=context.request_id,
            detail=safe_detail,
        ),
    )
    session.add(event)
    await session.flush()

    _log.info(
        "audit.recorded",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        seq=seq,
    )
    return event


async def record_platform_action(
    session: AsyncSession,
    context: PlatformContext,
    *,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    outcome: str = "success",
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event for a privileged, cross-tenant operation.

    The event is written into the *target tenant's* chain, so the tenant's own
    audit view shows that platform staff acted on their data — a requirement of
    the break-glass model (ADR-010 §5), not a nicety.
    """
    safe_detail = redact(detail or {})
    safe_detail["elevation_reason"] = context.reason

    synthetic = TenantContext(
        tenant_id=tenant_id,
        tenant_slug="",
        principal=context.principal,
        role=RoleCode.PLATFORM_ADMIN,
        capabilities=frozenset(),
        trace_id=context.trace_id,
        request_id=context.request_id,
    )
    return await record(
        session,
        synthetic,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=safe_detail,
    )


class ChainStatus(StrEnum):
    """Outcome of a chain verification."""

    INTACT = "intact"
    #: No events and no checkpoint — a tenant that has never been audited.
    EMPTY = "empty"
    #: The tenant was offboarded; its events were erased by the sanctioned path.
    OFFBOARDED = "offboarded"
    #: An event's content no longer reproduces its hash.
    MUTATED = "mutated"
    #: A sequence gap: an event was removed from inside the chain.
    GAP = "gap"
    #: Surviving events are internally valid but stop short of the checkpoint —
    #: the tail was deleted, or the chain was truncated to an earlier prefix.
    TRUNCATED = "truncated"
    #: Every event is gone but the checkpoint proves they existed.
    ERASED = "erased"
    #: Events exist beyond the checkpoint, which the trigger should make
    #: impossible — the checkpoint itself was tampered with, or the trigger was
    #: disabled.
    CHECKPOINT_MISSING = "checkpoint_missing"


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """The result of verifying one tenant's audit chain."""

    status: ChainStatus
    #: Sequence number where the problem was detected, where applicable.
    at_seq: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (ChainStatus.INTACT, ChainStatus.EMPTY, ChainStatus.OFFBOARDED)


async def verify_chain(session: AsyncSession, tenant_id: uuid.UUID) -> ChainVerification:
    """Re-derive a tenant's hash chain and compare it against the checkpoint.

    The hash chain alone detects *mutation* and *middle* deletion: altering or
    removing an interior event breaks the links that follow it. It cannot detect
    deletion of the **final** event, truncation to an earlier prefix, or deletion
    of the **entire** chain, because in each case the survivors form a perfectly
    valid chain — and an empty chain is trivially valid.

    ``audit_chain_head`` closes that gap. It records the highest sequence and
    hash ever written, is maintained by a ``SECURITY DEFINER`` trigger, and is
    writable by no runtime or platform role (migration 0002). Comparing the
    surviving chain against it turns all three cases into detections:

    ==========================  ==========================================
    Tampering                   Detected as
    ==========================  ==========================================
    field mutated               ``MUTATED``   (hash no longer reproduces)
    interior event deleted      ``GAP``       (sequence gap)
    final event deleted         ``TRUNCATED`` (max seq < checkpoint)
    truncated to prefix         ``TRUNCATED``
    all events deleted          ``ERASED``    (checkpoint survives)
    tenant offboarded           ``OFFBOARDED``(sanctioned; checkpoint marked)
    ==========================  ==========================================

    The remaining blind spot is stated honestly: an attacker holding
    **database-owner or superuser** credentials can drop the trigger, rewrite the
    checkpoint, and reconstruct a consistent chain. Detecting that requires
    exporting checkpoints to storage outside this database, which Phase 1A does
    not do. The guarantee is therefore: *tampering by any application or
    platform role is detectable; tampering by a database owner is not.*
    """
    head = (
        await session.execute(
            text(
                "SELECT last_seq, last_hash, offboarded_at FROM audit_chain_head "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
    ).one_or_none()

    events = list(
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
            )
        ).scalars()
    )

    if head is None:
        if not events:
            return ChainVerification(ChainStatus.EMPTY)
        # Events without a checkpoint means the trigger never fired for them.
        return ChainVerification(
            ChainStatus.CHECKPOINT_MISSING,
            at_seq=events[0].seq,
            detail="audit events exist but no chain checkpoint was recorded",
        )

    if head.offboarded_at is not None and not events:
        return ChainVerification(
            ChainStatus.OFFBOARDED,
            detail=f"tenant offboarded; {head.last_seq} events erased by the sanctioned path",
        )

    if not events:
        return ChainVerification(
            ChainStatus.ERASED,
            detail=(
                f"checkpoint records {head.last_seq} events but none survive; the chain was deleted"
            ),
        )

    expected_prev = GENESIS_HASH
    expected_seq = 1
    for event in events:
        if event.seq != expected_seq:
            return ChainVerification(
                ChainStatus.GAP,
                at_seq=event.seq,
                detail=f"expected sequence {expected_seq}, found {event.seq}",
            )
        if event.prev_hash != expected_prev:
            return ChainVerification(
                ChainStatus.MUTATED,
                at_seq=event.seq,
                detail="predecessor hash does not match",
            )
        recomputed = compute_hash(
            prev_hash=event.prev_hash,
            tenant_id=event.tenant_id,
            seq=event.seq,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_user_id=event.actor_user_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            trace_id=event.trace_id,
            request_id=event.request_id,
            detail=event.detail,
        )
        if recomputed != event.hash:
            return ChainVerification(
                ChainStatus.MUTATED,
                at_seq=event.seq,
                detail="event content no longer reproduces its hash",
            )
        expected_prev = event.hash
        expected_seq += 1

    last = events[-1]
    if last.seq < head.last_seq:
        return ChainVerification(
            ChainStatus.TRUNCATED,
            at_seq=last.seq,
            detail=(
                f"checkpoint records sequence {head.last_seq} but the chain ends at "
                f"{last.seq}; {head.last_seq - last.seq} event(s) were deleted"
            ),
        )
    if last.seq > head.last_seq or last.hash != head.last_hash:
        return ChainVerification(
            ChainStatus.CHECKPOINT_MISSING,
            at_seq=last.seq,
            detail="the chain does not match its checkpoint; the checkpoint was altered",
        )

    return ChainVerification(ChainStatus.INTACT, at_seq=last.seq)


async def count_events(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    )
    return int(result.scalar_one())


__all__ = [
    "GENESIS_HASH",
    "ActorType",
    "AuditAction",
    "ChainStatus",
    "ChainVerification",
    "compute_hash",
    "count_events",
    "record",
    "record_platform_action",
    "verify_chain",
]
