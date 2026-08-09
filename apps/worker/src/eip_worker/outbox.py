"""Transactional outbox relay (ADR-009 §3).

The relay is the second half of the outbox pattern. A request handler writes an
``outbox`` row **in the same transaction** as its state change; this loop
publishes those rows afterwards.

That ordering removes two failures that are otherwise endemic:

* *the transaction rolled back but the job already ran* — impossible, because
  the row would have rolled back too;
* *the job was never enqueued because the broker blipped* — impossible, because
  the row is durable and the relay retries.

``FOR UPDATE SKIP LOCKED`` lets several relay instances run concurrently without
coordination: each claims a disjoint batch. Delivery is at-least-once, so every
handler must be idempotent — which ADR-009 requires of steps anyway.

Tenant isolation in a background process
----------------------------------------

The first implementation gave the worker ``EIP_DB_PLATFORM_DSN`` — a reusable,
general-purpose ``BYPASSRLS`` credential — for one purpose: answering "which
tenants have pending messages?". That was a serious mistake. A compromised
worker held unrestricted cross-tenant read access over the entire control plane,
permanently, in exchange for a question whose answer is a list of UUIDs. The
module's own docstring claimed to avoid "the lazy way" while doing exactly that.

**The worker now holds no privileged credential at all.** It connects only as
``eip_app``, and the enumeration question is answered by
``eip_outbox_pending_tenants()`` — a ``SECURITY DEFINER`` function created in
migration 0002 that returns **tenant identifiers and nothing else**. No payload,
no topic, no other table. ``eip_app`` is granted ``EXECUTE`` on that one
function.

The privileged surface is therefore a single function with a fixed result shape,
rather than a credential that can read anything. Everything after enumeration
runs inside an ordinary ``tenant_session``, so RLS applies to every row the
worker actually touches.

``tests/test_worker_isolation.py`` inspects the real database privileges to
prove this, rather than only observing that RLS filtered a tenant session.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.governance import audit
from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from eip.platform.logging import bind_context, get_logger, new_trace_id

_log = get_logger("worker.outbox")

#: The synthetic principal used for system-initiated work. Recorded in the audit
#: trail as ``actor_type='system'`` so machine actions are never
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


async def pending_tenants(
    app_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
) -> list[uuid.UUID]:
    """Return tenants with unpublished outbox rows.

    Runs on the **constrained** ``eip_app`` role. The function it calls is
    ``SECURITY DEFINER`` and returns only tenant identifiers, so this is the
    entire cross-tenant surface the worker has — a fixed-shape answer to one
    question, not a credential.
    """
    async with app_factory() as session, session.begin():
        rows = (
            await session.execute(
                text("SELECT tenant_id FROM eip_outbox_pending_tenants(:limit)"),
                {"limit": limit},
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
    dispatch: Callable[[PublishedMessage], None] | None = None,
) -> list[PublishedMessage]:
    """Claim and publish one batch for a single tenant.

    Runs inside a ``tenant_session``, so every statement is RLS-scoped even
    though the SQL below carries no ``tenant_id`` predicate — isolation is the
    database's job, and the test suite proves it.

    When messages are actually published, an audit event is written into that
    tenant's own chain, in the same transaction as the publication. Recording it
    only when work occurs keeps an idle poll loop from flooding the trail while
    still leaving durable evidence of every dispatch that happened.
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
                if dispatch is not None:
                    dispatch(message)
                _log.info("outbox.published", topic=message.topic, message_id=str(message.id))

                await session.execute(
                    text(
                        "UPDATE outbox SET published_at = :now, attempts = attempts + 1 "
                        "WHERE id = :id"
                    ),
                    {"now": datetime.now(UTC), "id": row.id},
                )
                published.append(message)

            if published:
                await audit.record(
                    session,
                    context,
                    action=audit.AuditAction.OUTBOX_RELAYED,
                    resource_type="outbox",
                    detail={"message_count": len(published)},
                )

    return published


async def relay_once(
    app_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int,
    dispatch: Callable[[PublishedMessage], None] | None = None,
) -> int:
    """Run one full relay pass across all tenants with pending work."""
    total = 0
    for tenant_id in await pending_tenants(app_factory):
        published = await relay_tenant_batch(
            app_factory, tenant_id, batch_size=batch_size, dispatch=dispatch
        )
        total += len(published)
    return total
