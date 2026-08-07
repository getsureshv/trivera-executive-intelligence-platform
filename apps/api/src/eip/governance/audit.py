"""Audit recording and chain verification (ADR-014 §5).

Two properties define this module.

**Transactional consistency.** ``record`` takes the *caller's* session and
writes inside the caller's transaction. An audit row and the change it
describes commit together or not at all. This is why audit is a database table
and not a log stream: log pipelines drop records under load, and an audit trail
that drops records is not an audit trail.

**Tamper evidence.** Each tenant's events form a hash chain. ``hash`` covers the
row's canonical content plus ``prev_hash``, so removing or editing any row makes
every subsequent hash fail to reproduce. Combined with the revocation of
``UPDATE``/``DELETE`` from the application role (migration 0001), modification
requires database-superuser access *and* is still detectable.

Sequence allocation is serialised per tenant with a transaction-scoped advisory
lock. Without it, two concurrent writers could read the same ``prev_hash`` and
produce a fork; the unique constraint on ``(tenant_id, seq)`` would catch it,
but as a failed request rather than a correct one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
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
    TOKEN_ISSUED = "auth.token.issued"  # noqa: S105 - an action name, not a credential  # noqa: S105 - an action name, not a credential

    # tenant context / access
    TENANT_CONTEXT_ESTABLISHED = "tenant.context.established"
    TENANT_ACCESS_DENIED = "tenant.access.denied"
    CROSS_TENANT_ACCESS_ATTEMPTED = "tenant.access.cross_tenant_attempted"

    # administrative / configuration mutations
    TENANT_PROVISIONED = "tenant.provisioned"
    TENANT_DEPROVISIONED = "tenant.deprovisioned"
    MEMBERSHIP_GRANTED = "membership.granted"
    MEMBERSHIP_REVOKED = "membership.revoked"

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
    action: str,
    resource_type: str,
    resource_id: str | None,
    actor_user_id: uuid.UUID | None,
    outcome: str,
    detail: dict[str, Any],
) -> str:
    """Compute a chain hash. Pure, so it can be re-derived during verification."""
    body = _canonical(
        {
            "prev": prev_hash,
            "tenant_id": str(tenant_id),
            "seq": seq,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "outcome": outcome,
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

    event = AuditEvent(
        tenant_id=context.tenant_id,
        seq=seq,
        actor_type=context.principal.actor_type.value,
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
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=context.principal.user_id,
            outcome=outcome,
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
        detail=safe_detail,
    )


async def verify_chain(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[bool, int | None]:
    """Re-derive a tenant's hash chain.

    Returns ``(ok, first_bad_seq)``. Run periodically as a job and after any
    suspected incident; a broken chain is a high-severity finding.
    """
    events = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
        )
    ).scalars()

    expected_prev = GENESIS_HASH
    expected_seq = 1
    for event in events:
        if event.seq != expected_seq or event.prev_hash != expected_prev:
            return False, event.seq
        recomputed = compute_hash(
            prev_hash=event.prev_hash,
            tenant_id=event.tenant_id,
            seq=event.seq,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            actor_user_id=event.actor_user_id,
            outcome=event.outcome,
            detail=event.detail,
        )
        if recomputed != event.hash:
            return False, event.seq
        expected_prev = event.hash
        expected_seq += 1

    return True, None


async def count_events(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    )
    return int(result.scalar_one())


__all__ = [
    "GENESIS_HASH",
    "ActorType",
    "AuditAction",
    "compute_hash",
    "count_events",
    "record",
    "record_platform_action",
    "verify_chain",
]
