"""Transactional outbox relay (ADR-009 §3).

The relay is the second half of the outbox pattern. A request handler writes an
``outbox`` row **in the same transaction** as its state change; this loop
publishes those rows afterwards.

That ordering removes two failures that are otherwise endemic:

* *the transaction rolled back but the job already ran* — impossible, because
  the row would have rolled back too;
* *the job was never enqueued because the broker blipped* — impossible, because
  the row is durable and the relay retries.

In a governance product these are correctness defects, not annoyances: a
publish that emits an audit event but no follow-up job, or the reverse, leaves
the system describing a state it is not in.

``FOR UPDATE SKIP LOCKED`` lets several relay instances run concurrently
without coordination: each claims a disjoint batch. Delivery is at-least-once,
so every handler must be idempotent — which ADR-009 requires of steps anyway.

**Tenant isolation in a background process.** This loop deliberately runs on
the constrained ``eip_app`` role. It does *not* use the privileged BYPASSRLS
role, which would have been the lazy way to let one process see every tenant's
rows. Instead it discovers pending tenants through a narrow, audited window
(see ``_claim_pending_tenants``) and then processes each tenant inside a proper
``tenant_session``. Background access must not be a hole in the isolation model
(Phase 1A requirement).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.platform.context import (
    ActorType,
    PlatformContext,
    Principal,
    RoleCode,
    TenantContext,
)
from eip.platform.db import platform_session, tenant_session
from eip.platform.logging import bind_context, get_logger, new_trace_id

_log = get_logger("worker.outbox")

#: The synthetic principal used for system-initiated work. Recorded in the
#: audit trail as ``actor_type='system'`` so machine actions are never
#: indistinguishable from a person's.
SYSTEM_PRINCIPAL = Principal(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    external_subject="system:outbox-relay",
    email="system@trivera.invalid",
    actor_type=ActorType.SYSTEM,
)


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    id: uuid.UUID
    tenant_id: uuid.UUID
    topic: str
    payload: dict[str, Any]


async def _claim_pending_tenants(
    platform_factory: async_sessionmaker[AsyncSession],
) -> list[uuid.UUID]:
    """Discover which tenants have unpublished outbox rows.

    This is the one query the relay cannot express inside a single tenant's
    scope — it is asking *which* tenants need work. It therefore runs on the
    privileged session, with an explicit reason, and returns **only tenant
    identifiers**: no payloads, no business data. The actual message reads
    happen afterwards, per tenant, under normal RLS.

    Keeping the privileged surface this thin is the point. A relay that simply
    selected every unpublished row with BYPASSRLS would work identically and
    would have quietly removed row-level security from the busiest write path
    in the system.
    """
    context = PlatformContext(
        principal=SYSTEM_PRINCIPAL,
        reason="outbox relay: enumerate tenants with pending messages",
        trace_id=new_trace_id(),
        request_id=new_trace_id(),
    )
    async with platform_session(platform_factory, context) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT tenant_id FROM outbox "
                    "WHERE published_at IS NULL "
                    "ORDER BY tenant_id "
                    "LIMIT 100"
                )
            )
        ).scalars()
        return [uuid.UUID(str(value)) for value in rows]


def _system_context(tenant_id: uuid.UUID, trace_id: str) -> TenantContext:
    """Build a tenant context for system work.

    Job execution is subject to exactly the same tenant scoping as a request:
    the worker refuses to act without a resolved tenant, and the session sets
    ``app.tenant_id`` so RLS applies (ADR-009 §4).
    """
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="",
        principal=SYSTEM_PRINCIPAL,
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id=trace_id,
        request_id=trace_id,
    )


async def relay_tenant_batch(
    app_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    batch_size: int,
) -> list[PublishedMessage]:
    """Claim and publish one batch for a single tenant.

    Runs inside a ``tenant_session``, so every statement is RLS-scoped even
    though the SQL below carries no ``tenant_id`` predicate — the isolation is
    the database's job, and the test suite proves it.
    """
    trace_id = new_trace_id()
    context = _system_context(tenant_id, trace_id)
    published: list[PublishedMessage] = []

    with bind_context(trace_id=trace_id, tenant_id=tenant_id, component="worker.outbox"):
        async with tenant_session(app_factory, context) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, tenant_id, topic, payload FROM outbox "
                        "WHERE published_at IS NULL "
                        "ORDER BY created_at "
                        "LIMIT :limit "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    {"limit": batch_size},
                )
            ).all()

            for row in rows:
                message = PublishedMessage(
                    id=uuid.UUID(str(row.id)),
                    tenant_id=uuid.UUID(str(row.tenant_id)),
                    topic=str(row.topic),
                    payload=dict(row.payload or {}),
                )
                # Phase 1A has no real consumers. Dispatch is a logged no-op so
                # the durable path is exercised and observable end to end;
                # Phase 2 replaces this with a Dramatiq send.
                _log.info("outbox.published", topic=message.topic, message_id=str(message.id))

                await session.execute(
                    text(
                        "UPDATE outbox SET published_at = :now, attempts = attempts + 1 "
                        "WHERE id = :id"
                    ),
                    {"now": datetime.now(UTC), "id": row.id},
                )
                published.append(message)

    return published


async def relay_once(
    app_factory: async_sessionmaker[AsyncSession],
    platform_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int,
) -> int:
    """Run one full relay pass across all tenants with pending work."""
    total = 0
    for tenant_id in await _claim_pending_tenants(platform_factory):
        published = await relay_tenant_batch(app_factory, tenant_id, batch_size=batch_size)
        total += len(published)
    return total
