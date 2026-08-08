"""Operator-driven tenant provisioning.

Phase 1A provisioned a tenant inside one request handler: insert the row, then
run the DDL. It had no answer for the second half failing. The tenant existed,
its analytical schema did not, nothing recorded which, and the only way to find
out was for somebody to try to use it.

PO-003 turns that from untidy into unacceptable. TriVera is tenant #1 and not a
special case, so provisioning is something staff do repeatedly rather than once
by hand — and anything done repeatedly will be interrupted eventually.

**Operator-driven, not self-serve.** Every entry point requires a
``PlatformContext``: the ``platform_admin`` capability, an elevation reason, and
an audit event in the target tenant's own chain. There is no public signup path
and this module is not where one would be added.

Design
------

*The workflow owns its transactions.* It takes a session **factory**, not a
session. Provisioning is three transactions and a piece of DDL between them,
and it has to be, because ``CREATE SCHEMA`` cannot roll back with the row that
describes it:

    1. **claim**   — move ``pending``/``failed`` → ``in_progress``, atomically.
    2. *(no transaction)* — data-plane DDL: schema, login role, password.
    3. **settle**  — record the credential reference and mark ``ready``;
       or, on failure, mark ``failed`` with a redacted reason.

Step 3's failure branch is why the claim is a separate transaction. A workflow
that held one transaction throughout would roll the failure record back along
with everything else, and the tenant would be left in ``pending`` looking
untouched — which is precisely the half-created state this exists to prevent.

*The claim is a conditional UPDATE, not a lock-then-check.*

.. code-block:: sql

    UPDATE tenant SET provisioning_state = 'in_progress' ...
    WHERE id = :id AND (provisioning_state IN ('pending','failed') OR <stale>)

Two concurrent callers: the second blocks on the row lock, and under READ
COMMITTED re-evaluates the ``WHERE`` after the first commits. It matches zero
rows and is told the truth — *someone else is provisioning this tenant*. No
advisory lock, no lease table, no window between check and act.

*Idempotent by state, not by hope.* Provisioning an already-``ready`` tenant
returns it unchanged. Registering an existing slug resumes it if it is
incomplete and refuses if it is not. A retried job is safe; a duplicate request
is not silently a second tenant.

*Stale claims expire.* A process that dies mid-provision leaves ``in_progress``
behind. Without an expiry that state blocks every future attempt, so a claim
older than ``stale_after_seconds`` may be taken over — recorded in the attempt
count, never silently.

What never appears anywhere
---------------------------

The tenant's analytical password is generated inside the data plane, handed to
the ``SecretStore``, and never returned. What reaches this module is a
``SecretRef``. But a *failure* can still leak one: SQLAlchemy appends the
failing statement to its exception message, and the statement that creates a
tenant role contains that role's password. :func:`summarise_failure` exists for
that single reason, and there is a test that feeds it a password and checks.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.dataplane.interfaces import ProvisioningFence, TenantDataPlane, TenantRef
from eip.governance import audit, outbox
from eip.identity.models import Tenant
from eip.platform.context import PlatformContext
from eip.platform.db import platform_session
from eip.platform.errors import ConflictError, EipError, NotFoundError
from eip.platform.logging import get_logger

_log = get_logger("identity.provisioning")

#: How long an ``in_progress`` claim is honoured before another attempt may
#: take it over. Long enough that a slow but living attempt is not stolen from,
#: short enough that a crashed one is not a support ticket.
DEFAULT_STALE_AFTER_SECONDS: Final = 300.0

#: Redaction for driver messages. Anything quoted is assumed to be a value,
#: because in the statement that matters it is a password.
_QUOTED_LITERAL = re.compile(r"'[^']*'")
_MAX_ERROR_LENGTH: Final = 480


class ProvisioningState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    FAILED = "failed"


class ProvisioningError(EipError):
    """Provisioning failed and the tenant has been left recoverable.

    Deliberately a 500. It is not the caller's fault, the request was correct,
    and the retry is a deliberate operator action rather than something a
    client should loop on.
    """

    status_code = 500
    code = "PROVISIONING_FAILED"


@dataclass(frozen=True, slots=True)
class TenantRecord:
    """A tenant as an operator needs to see it.

    Carries the provisioning state and the last failure, because "what is
    stuck, and why" is the only question this surface exists to answer.

    Carries **no credential material** — not the password, and not the secret
    reference either. The reference is safe to store (ADR-015) but nothing
    outside the data plane needs it, and the cheapest way to keep a value out
    of a response is for the value never to reach the response model.
    """

    id: uuid.UUID
    slug: str
    name: str
    status: str
    isolation_mode: str
    analytical_schema: str
    analytical_role: str | None
    provisioning_state: str
    provisioning_attempts: int
    provisioning_error: str | None
    provisioned_at: datetime | None

    @property
    def is_ready(self) -> bool:
        return self.provisioning_state == ProvisioningState.READY.value


def summarise_failure(exc: BaseException) -> str:
    """Render an exception as a stored, **credential-free** reason.

    Three things happen, in order, and each removes a real leak:

    1. everything from ``[SQL:`` onward is dropped — SQLAlchemy appends the
       failing statement *and its parameters*;
    2. every single-quoted literal becomes ``'***'`` — the statement that
       creates a tenant role carries its password as exactly that;
    3. the result is truncated to fit the column.

    Masking rather than dropping here, unlike :func:`eip.platform.logging.redact`.
    A log record's key is metadata an attacker learns something from; a
    truncated error string with ``'***'`` in it is a diagnostic aid for the
    operator who has to fix the tenant, and they need the shape of the
    statement to do it.
    """
    text_value = f"{type(exc).__name__}: {exc}"
    text_value = re.split(r"\[SQL:", text_value, maxsplit=1)[0]
    text_value = _QUOTED_LITERAL.sub("'***'", text_value)
    text_value = " ".join(text_value.split())
    return text_value[:_MAX_ERROR_LENGTH]


def _record(tenant: Tenant) -> TenantRecord:
    return TenantRecord(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        isolation_mode=tenant.isolation_mode,
        analytical_schema=tenant.analytical_schema,
        analytical_role=tenant.analytical_role,
        provisioning_state=tenant.provisioning_state,
        provisioning_attempts=tenant.provisioning_attempts,
        provisioning_error=tenant.provisioning_error,
        provisioned_at=tenant.provisioned_at,
    )


class TenantProvisioningWorkflow:
    """Register and provision tenants. The only sanctioned path.

    Runs on the ``eip_platform`` role via :func:`platform_session`, which logs
    every privileged transaction. Provisioning is inherently cross-tenant —
    that is why it is privileged, and why it is here rather than in
    ``TenantReadService``.
    """

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        data_plane: TenantDataPlane,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._sessions = sessions
        self._data_plane = data_plane
        self._stale_after = stale_after_seconds

    # --- public surface ---------------------------------------------------

    async def create(self, context: PlatformContext, *, slug: str, name: str) -> TenantRecord:
        """Register a tenant and provision it. The operator's one call.

        Safe to repeat: an interrupted create is resumed rather than
        duplicated, and a completed one is refused rather than rebuilt.
        """
        registered = await self.register(context, slug=slug, name=name)
        return await self.provision(context, registered.id)

    async def register(self, context: PlatformContext, *, slug: str, name: str) -> TenantRecord:
        """Create the control-plane row, or adopt an incomplete one.

        The slug's unique constraint is the duplicate guard, not the ``SELECT``
        below it: two concurrent registrations both pass the check and one is
        refused by PostgreSQL. Checking first only improves the error message.
        """
        async with platform_session(self._sessions, context) as session:
            existing = await self._find_by_slug(session, slug)
            if existing is not None:
                return self._adopt_or_refuse(existing, slug)

            tenant_id = uuid.uuid4()
            # The namespace is derived from the tenant id by the plane itself.
            # Deriving it here would hard-wire one isolation mode into the
            # control plane, which is the thing the port exists to prevent.
            planned = await self._data_plane.handle(TenantRef(tenant_id=tenant_id, slug=slug))

            tenant = Tenant(
                id=tenant_id,
                slug=slug,
                name=name,
                # Not 'active'. Nothing exists yet to be active about.
                status="provisioning",
                analytical_schema=planned.namespace,
                isolation_mode=self._data_plane.mode.value,
                provisioning_state=ProvisioningState.PENDING.value,
                provisioning_attempts=0,
            )
            session.add(tenant)

            try:
                await session.flush()
            except IntegrityError:
                # Lost the race. Roll back to a usable session and re-read:
                # whoever won may already have finished.
                await session.rollback()
                async with platform_session(self._sessions, context) as retry:
                    winner = await self._find_by_slug(retry, slug)
                    if winner is None:  # pragma: no cover - only if the row vanished
                        raise
                    return self._adopt_or_refuse(winner, slug)

            await audit.record_platform_action(
                session,
                context,
                tenant_id=tenant_id,
                action=audit.AuditAction.TENANT_REGISTERED,
                resource_type="tenant",
                resource_id=str(tenant_id),
                detail={"slug": slug, "isolation_mode": self._data_plane.mode.value},
            )
            await outbox.publish(
                session,
                tenant_id=tenant_id,
                topic=outbox.Topic.TENANT_REGISTERED,
                payload={"tenant_id": str(tenant_id), "slug": slug},
                trace_id=context.trace_id,
            )

            _log.info("provisioning.registered", tenant_id=str(tenant_id), slug=slug)
            return _record(tenant)

    async def provision(self, context: PlatformContext, tenant_id: uuid.UUID) -> TenantRecord:
        """Build the tenant's analytical plane, or record why it could not.

        Returns an already-``ready`` tenant untouched. Raises ``ConflictError``
        if another attempt holds the claim, and ``ProvisioningError`` if this
        attempt fails — in which case the tenant is left in ``failed`` with a
        redacted reason, visible to :meth:`list_tenants` and retryable.
        """
        claimed = await self._claim(context, tenant_id)
        if claimed.is_ready:
            _log.info("provisioning.already_ready", tenant_id=str(tenant_id))
            return claimed

        try:
            handle = await self._data_plane.provision(
                TenantRef(tenant_id=claimed.id, slug=claimed.slug),
                fence=ProvisioningFence(attempt=claimed.provisioning_attempts),
            )
        except Exception as exc:
            await self._record_failure(context, claimed, exc)
            raise ProvisioningError(
                "Provisioning failed; the tenant is recorded as failed and can be retried."
            ) from exc

        try:
            return await self._settle(context, claimed, handle)
        except ProvisioningError:
            # _settle uses this to report that a newer stale-takeover attempt
            # now owns the row. That is not a failure of this tenant, and must
            # not flow through _record_failure and compete with the winner.
            raise
        except Exception as exc:
            # The plane exists but the record of it does not. Marking failed is
            # correct: provision() is idempotent, so the retry rebuilds nothing
            # and simply records what is already there.
            await self._record_failure(context, claimed, exc)
            raise ProvisioningError(
                "The analytical plane was created but could not be recorded; retry is safe."
            ) from exc

    async def list_tenants(self, context: PlatformContext) -> list[TenantRecord]:
        """Every tenant with its provisioning state.

        The surface that makes "visibly recoverable" true rather than claimed.
        Incomplete tenants sort first: an operator opening this list is looking
        for what is broken, not for an inventory.
        """
        async with platform_session(self._sessions, context) as session:
            rows = (
                await session.execute(
                    select(Tenant).order_by(
                        text("(provisioning_state = 'ready')"), Tenant.created_at
                    )
                )
            ).scalars()
            return [_record(row) for row in rows]

    # --- steps ------------------------------------------------------------

    async def _claim(self, context: PlatformContext, tenant_id: uuid.UUID) -> TenantRecord:
        """Atomically take ownership of the next attempt.

        The conditional UPDATE *is* the concurrency control. See the module
        docstring for why this needs no lock.
        """
        async with platform_session(self._sessions, context) as session:
            claimed = (
                await session.execute(
                    update(Tenant)
                    .where(
                        Tenant.id == tenant_id,
                        text(
                            "(provisioning_state IN ('pending','failed') "
                            "OR (provisioning_state = 'in_progress' "
                            "AND provisioning_started_at < now() - make_interval(secs => :stale)))"
                        ).bindparams(stale=self._stale_after),
                    )
                    .values(
                        provisioning_state=ProvisioningState.IN_PROGRESS.value,
                        provisioning_attempts=Tenant.provisioning_attempts + 1,
                        provisioning_started_at=datetime.now(UTC),
                        provisioning_error=None,
                    )
                    .returning(Tenant)
                )
            ).scalar_one_or_none()

            if claimed is not None:
                _log.info(
                    "provisioning.claimed",
                    tenant_id=str(tenant_id),
                    attempt=claimed.provisioning_attempts,
                )
                return _record(claimed)

            # Nothing claimed. Say which of the three reasons it was.
            current = (
                await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            ).scalar_one_or_none()

            if current is None:
                raise NotFoundError("No such tenant.")
            if current.provisioning_state == ProvisioningState.READY.value:
                return _record(current)

            _log.warning("provisioning.claim_contended", tenant_id=str(tenant_id))
            raise ConflictError(
                "Provisioning for this tenant is already in progress. "
                "A stale attempt is taken over automatically after "
                f"{int(self._stale_after)}s."
            )

    async def _settle(
        self, context: PlatformContext, claimed: TenantRecord, handle: Any
    ) -> TenantRecord:
        """Record the credential reference and mark the tenant ready."""
        async with platform_session(self._sessions, context) as session:
            values: dict[str, Any] = {
                "analytical_role": handle.role,
                "analytical_schema": handle.namespace,
                "provisioning_state": ProvisioningState.READY.value,
                "status": "active",
                "provisioned_at": datetime.now(UTC),
                "provisioning_error": None,
            }
            if handle.secret_ref is not None:
                # A pointer and a version. Never a value (ADR-015).
                values["analytical_secret_name"] = handle.secret_ref.logical_name
                values["analytical_secret_version"] = handle.secret_ref.version

            tenant = (
                await session.execute(
                    update(Tenant)
                    .where(
                        Tenant.id == claimed.id,
                        Tenant.provisioning_state == ProvisioningState.IN_PROGRESS.value,
                        Tenant.provisioning_attempts == claimed.provisioning_attempts,
                    )
                    .values(**values)
                    .returning(Tenant)
                )
            ).scalar_one_or_none()

            if tenant is None:
                _log.warning(
                    "provisioning.settle_superseded",
                    tenant_id=str(claimed.id),
                    attempt=claimed.provisioning_attempts,
                )
                raise ProvisioningError(
                    "This provisioning attempt was superseded by a newer attempt; "
                    "its result was not recorded."
                )

            await audit.record_platform_action(
                session,
                context,
                tenant_id=claimed.id,
                action=audit.AuditAction.TENANT_PROVISIONED,
                resource_type="tenant",
                resource_id=str(claimed.id),
                detail={
                    "slug": claimed.slug,
                    "isolation_mode": tenant.isolation_mode,
                    # The schema and role name are derived from the tenant id
                    # and are not secret. The password is not here, and neither
                    # is the reference that would locate it.
                    "analytical_schema": tenant.analytical_schema,
                    "attempt": tenant.provisioning_attempts,
                },
            )
            await outbox.publish(
                session,
                tenant_id=claimed.id,
                topic=outbox.Topic.TENANT_PROVISIONED,
                payload={
                    "tenant_id": str(claimed.id),
                    "slug": claimed.slug,
                    "isolation_mode": tenant.isolation_mode,
                },
                trace_id=context.trace_id,
            )

            _log.info("provisioning.ready", tenant_id=str(claimed.id), slug=claimed.slug)
            return _record(tenant)

    async def _record_failure(
        self, context: PlatformContext, claimed: TenantRecord, exc: BaseException
    ) -> None:
        """Leave the tenant visibly failed, in its own transaction.

        Its own transaction because the one that failed is unusable, and
        because a failure record that rolls back with the failure is not a
        failure record.

        Swallows secondary exceptions: the caller is already raising, and
        losing the original cause to a bookkeeping error would be the worse
        outcome. The tenant is then stuck in ``in_progress`` and the staleness
        window recovers it.
        """
        reason = summarise_failure(exc)
        superseded = False
        try:
            async with platform_session(self._sessions, context) as session:
                result = await session.execute(
                    update(Tenant)
                    .where(
                        Tenant.id == claimed.id,
                        Tenant.provisioning_state == ProvisioningState.IN_PROGRESS.value,
                        Tenant.provisioning_attempts == claimed.provisioning_attempts,
                    )
                    .values(
                        provisioning_state=ProvisioningState.FAILED.value,
                        provisioning_error=reason,
                    )
                    .returning(Tenant.id)
                )
                if result.scalar_one_or_none() is None:
                    _log.warning(
                        "provisioning.failure_superseded",
                        tenant_id=str(claimed.id),
                        attempt=claimed.provisioning_attempts,
                    )
                    superseded = True
                else:
                    await audit.record_platform_action(
                        session,
                        context,
                        tenant_id=claimed.id,
                        action=audit.AuditAction.TENANT_PROVISIONING_FAILED,
                        resource_type="tenant",
                        resource_id=str(claimed.id),
                        outcome="failure",
                        detail={"slug": claimed.slug, "reason": reason},
                    )
                    await outbox.publish(
                        session,
                        tenant_id=claimed.id,
                        topic=outbox.Topic.TENANT_PROVISIONING_FAILED,
                        payload={"tenant_id": str(claimed.id), "slug": claimed.slug},
                        trace_id=context.trace_id,
                    )
        except Exception:  # pragma: no cover - defensive
            _log.error("provisioning.failure_not_recorded", tenant_id=str(claimed.id))
            return

        if superseded:
            raise ProvisioningError(
                "This provisioning attempt was superseded by a newer attempt; "
                "its failure was not recorded."
            ) from exc

        _log.warning(
            "provisioning.failed",
            tenant_id=str(claimed.id),
            slug=claimed.slug,
            reason=reason,
        )

    # --- helpers ----------------------------------------------------------

    @staticmethod
    async def _find_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
        return (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()

    @staticmethod
    def _adopt_or_refuse(existing: Tenant, slug: str) -> TenantRecord:
        """Resume an incomplete tenant; refuse a finished one.

        This is what makes ``create`` safe to retry without making it a way to
        rebuild a live tenant's data plane by accident.
        """
        if existing.provisioning_state == ProvisioningState.READY.value:
            raise ConflictError(f"A tenant with slug {slug!r} already exists.")
        _log.info(
            "provisioning.resuming",
            tenant_id=str(existing.id),
            slug=slug,
            state=existing.provisioning_state,
        )
        return _record(existing)
