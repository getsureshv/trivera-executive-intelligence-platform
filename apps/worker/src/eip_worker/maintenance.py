"""Tenant-scoped credential destruction and connection-test retention."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.connectivity.models import ConnectionTest, DataSource
from eip.governance import audit
from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from eip.platform.secrets import SecretRef, SecretStore


def _context(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="",
        principal=Principal(
            uuid.UUID("00000000-0000-0000-0000-000000000002"),
            "system:source-maintenance",
            "system@trivera.invalid",
            ActorType.SYSTEM,
        ),
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id=f"maintenance-{tenant_id}",
        request_id=f"maintenance-{tenant_id}",
    )


async def due_tenants(
    factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> list[uuid.UUID]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("maintenance timestamp must be timezone-aware")
    async with factory() as session, session.begin():
        rows = await session.scalars(
            text("SELECT tenant_id FROM eip_maintenance_due_tenants(:now)"), {"now": now}
        )
        return [uuid.UUID(str(value)) for value in rows]


async def run_tenant_maintenance(
    factory: async_sessionmaker[AsyncSession],
    secrets: SecretStore,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Destroy due referenced secrets and prune terminal tests, idempotently."""
    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("maintenance timestamp must be timezone-aware")
    context = _context(tenant_id)
    destroyed = 0
    async with tenant_session(factory, context) as session:
        sources = (
            await session.scalars(
                select(DataSource)
                .where(
                    DataSource.status == "disabled",
                    DataSource.credential_destroyed_at.is_(None),
                    DataSource.credential_destroy_after <= observed_at,
                )
                .order_by(DataSource.id)
                .with_for_update()
            )
        ).all()
        for source in sources:
            # The ref is rebuilt from this RLS-scoped row and the active tenant;
            # no worker envelope can supply or substitute it.
            ref = SecretRef(tenant_id, source.secret_name, source.secret_version)
            # External deletion precedes the database claim. If commit fails,
            # retry repeats the idempotent delete and then records completion.
            await secrets.delete(ref)
            source.credential_destroyed_at = observed_at
            destroyed += 1
            await audit.record(
                session,
                context,
                action=audit.AuditAction.SOURCE_CREDENTIAL_DESTROYED,
                resource_type="data_source",
                resource_id=str(source.id),
                detail={"status": "destroyed"},
            )

        result = await session.execute(
            delete(ConnectionTest).where(
                ConnectionTest.status.in_(("succeeded", "failed", "stale")),
                ConnectionTest.queued_at < observed_at - timedelta(days=90),
            )
        )
        pruned = int(result.rowcount or 0)  # type: ignore[attr-defined]
        if pruned:
            await audit.record(
                session,
                context,
                action=audit.AuditAction.CONNECTION_TESTS_PRUNED,
                resource_type="connection_test",
                detail={"row_count": pruned, "retention_days": 90},
            )
    return destroyed, pruned
