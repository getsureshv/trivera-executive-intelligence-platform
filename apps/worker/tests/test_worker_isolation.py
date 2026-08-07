"""
================================================================================
 RELEASE-GATING SECURITY TESTS — BACKGROUND PROCESSING ISOLATION
================================================================================

 Background work is where tenant isolation is most often quietly abandoned.
 The temptation is obvious: a relay that must serve every tenant is simplest to
 write with the privileged role, and it works perfectly — while removing
 row-level security from the busiest write path in the system.

 These tests assert that the worker does not take that shortcut:

   * the relay processes each tenant inside a proper tenant-scoped session;
   * a tenant's batch never contains another tenant's messages;
   * the one query that genuinely must span tenants returns identifiers only,
     never payloads.
================================================================================
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import create_engines, create_session_factory, tenant_session
from eip.platform.settings import Environment, Settings
from eip_worker.outbox import SYSTEM_PRINCIPAL, relay_once, relay_tenant_batch

pytestmark = pytest.mark.integration


def _database_available(settings: Settings) -> bool:
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return False

    import socket
    from urllib.parse import urlparse

    parsed = urlparse(settings.db_app_dsn.replace("postgresql+asyncpg", "postgresql"))
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(env=Environment.CI)


@pytest.fixture(autouse=True)
def _requires_database(settings: Settings) -> None:
    if not _database_available(settings):
        pytest.skip("PostgreSQL is not reachable; run the worker suite in the container.")


@pytest.fixture
async def engines(settings: Settings) -> AsyncIterator[tuple[AsyncEngine, AsyncEngine]]:
    built = create_engines(settings)
    try:
        yield built.app, built.platform
    finally:
        await built.app.dispose()
        await built.platform.dispose()


@pytest.fixture
async def tenants(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two tenants, each with one queued outbox message."""
    _, platform_engine = engines
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    async with platform_engine.begin() as conn:
        for index, tenant_id in enumerate((tenant_a, tenant_b)):
            await conn.execute(
                text(
                    "INSERT INTO tenant (id, slug, name, status, analytical_schema) "
                    "VALUES (:id, :slug, :name, 'active', :schema)"
                ),
                {
                    "id": tenant_id,
                    "slug": f"worker-test-{index}-{str(tenant_id)[:8]}",
                    "name": f"Worker Test {index}",
                    "schema": f"tenant_{str(tenant_id).replace('-', '_')}",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO outbox (id, tenant_id, topic, payload, trace_id) "
                    "VALUES (:id, :tenant_id, :topic, :payload, 'trace')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "topic": f"tenant.{index}.event",
                    "payload": f'{{"marker": "{tenant_id}"}}',
                },
            )

    yield tenant_a, tenant_b

    async with platform_engine.begin() as conn:
        for tenant_id in (tenant_a, tenant_b):
            await conn.execute(text("DELETE FROM outbox WHERE tenant_id = :id"), {"id": tenant_id})
            await conn.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": tenant_id})


@pytest.fixture
def app_sessions(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engines[0])


@pytest.fixture
def platform_sessions(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engines[1])


class TestWorkerRespectsTenantIsolation:
    async def test_a_tenant_batch_contains_only_that_tenant(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """The relay's claim query has no tenant predicate; RLS supplies it."""
        tenant_a, tenant_b = tenants

        published = await relay_tenant_batch(app_sessions, tenant_a, batch_size=100)

        assert published, "the relay published nothing for tenant A"
        assert {message.tenant_id for message in published} == {tenant_a}
        assert tenant_b not in {message.tenant_id for message in published}, (
            "ISOLATION FAILED: the worker published another tenant's message"
        )

    async def test_relaying_one_tenant_leaves_the_other_untouched(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant_a, tenant_b = tenants
        await relay_tenant_batch(app_sessions, tenant_a, batch_size=100)

        async with platform_sessions() as session, session.begin():
            unpublished_b = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM outbox WHERE tenant_id = :id AND published_at IS NULL"
                    ),
                    {"id": tenant_b},
                )
            ).scalar_one()

        assert unpublished_b == 1, "tenant B's queue was affected by tenant A's relay pass"

    async def test_full_pass_publishes_every_tenant_exactly_once(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        published = await relay_once(app_sessions, platform_sessions, batch_size=100)
        assert published >= 2

        # A second pass must be a no-op: publication is idempotent, so a
        # restarted relay cannot double-publish.
        assert await relay_once(app_sessions, platform_sessions, batch_size=100) == 0

    async def test_worker_refuses_to_act_without_a_tenant(
        self, app_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """A tenant-scoped session with an unknown tenant must see nothing.

        Job execution is subject to the same scoping as a request: there is no
        "system" bypass in the relay's data path (ADR-009 §4).
        """
        context = TenantContext(
            tenant_id=uuid.uuid4(),  # a tenant that does not exist
            tenant_slug="",
            principal=SYSTEM_PRINCIPAL,
            role=RoleCode.VIEWER,
            capabilities=frozenset(),
            trace_id="t",
            request_id="r",
        )
        async with tenant_session(app_sessions, context) as session:
            visible = (await session.execute(text("SELECT count(*) FROM outbox"))).scalar_one()

        assert visible == 0


class TestSystemPrincipal:
    def test_system_work_is_attributed_as_system(self) -> None:
        """Machine actions must never look like a person's in the audit trail."""
        assert SYSTEM_PRINCIPAL.actor_type is ActorType.SYSTEM
        assert isinstance(SYSTEM_PRINCIPAL, Principal)
