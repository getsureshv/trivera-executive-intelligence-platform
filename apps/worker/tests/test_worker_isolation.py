"""
================================================================================
 RELEASE-GATING SECURITY TESTS — BACKGROUND PROCESSING PRIVILEGES
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 The original Phase 1A worker held ``EIP_DB_PLATFORM_DSN`` — a reusable,
 general-purpose ``BYPASSRLS`` credential — to answer one question: which
 tenants have pending outbox rows. A compromised worker therefore had permanent,
 unrestricted cross-tenant read access over the entire control plane. The tests
 that shipped alongside it observed only that RLS filtered an ordinary tenant
 session — a session the privileged path bypassed entirely and which those tests
 never forced the worker to use.

 These tests therefore **inspect the database privileges the worker actually
 holds**, rather than observing behaviour. A test that only checks the happy
 path cannot detect an over-privileged credential, because an over-privileged
 credential produces an identical happy path.
================================================================================
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import create_app_engine, create_session_factory, tenant_session
from eip.platform.settings import Environment, Settings
from eip_worker.outbox import SYSTEM_PRINCIPAL, pending_tenants, relay_once, relay_tenant_batch

pytestmark = [pytest.mark.integration, pytest.mark.security]


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
async def worker_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The engine the worker actually builds — constrained only.

    ``create_app_engine``, deliberately, not ``create_engines``: the worker
    process has no way to construct a privileged engine at all.
    """
    engine = create_app_engine(settings)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def worker_sessions(worker_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(worker_engine)


@pytest.fixture
async def privileged_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """A privileged engine used only by fixtures, to set up and verify state.

    The worker never has this. It exists here so the tests can prove that data
    the worker cannot see does in fact exist — without that control, every
    denial below could be a false pass.
    """
    from eip.platform.db import create_engines

    engines = create_engines(settings)
    try:
        yield engines.platform
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()


@pytest.fixture
async def tenants(privileged_engine: AsyncEngine) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two tenants, each with one queued outbox message."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    async with privileged_engine.begin() as conn:
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

    async with privileged_engine.begin() as conn:
        for tenant_id in (tenant_a, tenant_b):
            for table in ("audit_event", "outbox"):
                await conn.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = :id"),  # noqa: S608
                    {"id": tenant_id},
                )
            await conn.execute(text("DELETE FROM tenant WHERE id = :id"), {"id": tenant_id})


def _system_context(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="",
        principal=SYSTEM_PRINCIPAL,
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id="trace-test",
        request_id="request-test",
    )


# =============================================================================
# The privileges the worker actually holds
# =============================================================================


class TestWorkerCredentialIsConstrained:
    """Inspect the role itself, not the behaviour of one session."""

    async def test_worker_role_has_no_bypassrls(self, worker_engine: AsyncEngine) -> None:
        """The heart of the finding.

        Previously the worker connected as ``eip_platform``, which carries
        ``BYPASSRLS``. Every isolation observation made about the worker was
        therefore about a session it was not obliged to use.
        """
        async with worker_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT rolname, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()

        assert row.rolname == "eip_app", f"worker connected as {row.rolname}, not eip_app"
        assert row.rolsuper is False
        assert row.rolbypassrls is False, (
            "the worker holds a BYPASSRLS credential — a compromised worker can read "
            "every tenant's data"
        )

    async def test_worker_credential_cannot_read_another_tenants_rows_directly(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """The negative test the finding asks for.

        Scoped to tenant A, issue SQL that explicitly names tenant B's rows. The
        worker's credential must return nothing — not because our code declined
        to ask, but because the database refuses to answer.
        """
        tenant_a, tenant_b = tenants

        async with tenant_session(worker_sessions, _system_context(tenant_a)) as session:
            visible = (
                await session.execute(
                    text("SELECT count(*) FROM outbox WHERE tenant_id = :other"),
                    {"other": tenant_b},
                )
            ).scalar_one()

        assert visible == 0, (
            "ISOLATION FAILED: the worker credential read another tenant's outbox rows"
        )

    async def test_worker_credential_sees_nothing_without_tenant_context(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """With no tenant bound the worker must see zero rows, not all rows."""
        async with worker_sessions() as session, session.begin():
            for table in ("outbox", "membership", "audit_event"):
                count = (
                    await session.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
                assert count == 0, f"worker read {table} with no tenant context"

    async def test_worker_cannot_call_privileged_functions(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """EXECUTE is granted on the dispatch function and on nothing else."""
        tenant_a, _ = tenants
        async with worker_sessions() as session, session.begin():
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(text("SELECT eip_audit_chain_offboard(:t)"), {"t": tenant_a})
        assert "permission denied" in str(excinfo.value).lower()

    async def test_worker_cannot_write_the_audit_checkpoint(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        async with worker_sessions() as session, session.begin():
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(text("UPDATE audit_chain_head SET last_seq = 0"))
        assert "permission denied" in str(excinfo.value).lower()

    async def test_worker_cannot_rewrite_the_audit_trail(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant_a, _ = tenants
        async with tenant_session(worker_sessions, _system_context(tenant_a)) as session:
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(text("UPDATE audit_event SET action = 'tampered'"))
        assert "permission denied" in str(excinfo.value).lower()


# =============================================================================
# The dispatch function: least privilege, fixed shape
# =============================================================================


class TestDispatchFunctionIsNarrow:
    async def test_it_returns_only_tenant_identifiers(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """The entire cross-tenant surface the worker has.

        Asserting the *shape* matters: if the function ever grew a payload
        column, the worker would silently regain broad cross-tenant read access
        through a path nobody would think to re-review.
        """
        async with worker_sessions() as session, session.begin():
            result_type = (
                await session.execute(
                    text(
                        "SELECT pg_get_function_result(oid) FROM pg_proc "
                        "WHERE proname = 'eip_outbox_pending_tenants'"
                    )
                )
            ).scalar_one()

        assert result_type == "TABLE(tenant_id uuid)", (
            f"the dispatch function returns more than identifiers: {result_type}"
        )

    async def test_it_is_security_definer_with_a_pinned_search_path(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """A SECURITY DEFINER function with a mutable ``search_path`` is an
        escalation primitive: the caller could shadow a referenced object."""
        async with worker_sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT prosecdef, proconfig FROM pg_proc "
                        "WHERE proname = 'eip_outbox_pending_tenants'"
                    )
                )
            ).one()

        assert row.prosecdef is True
        assert row.proconfig is not None
        assert any(entry.startswith("search_path=") for entry in row.proconfig)

    async def test_it_finds_both_tenants(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant_a, tenant_b = tenants
        found = set(await pending_tenants(worker_sessions))
        assert {tenant_a, tenant_b} <= found


# =============================================================================
# Relay behaviour
# =============================================================================


class TestRelayRespectsTenantIsolation:
    async def test_a_tenant_batch_contains_only_that_tenant(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant_a, tenant_b = tenants
        published = await relay_tenant_batch(worker_sessions, tenant_a, batch_size=100)

        assert published, "the relay published nothing for tenant A"
        assert {message.tenant_id for message in published} == {tenant_a}
        assert tenant_b not in {message.tenant_id for message in published}

    async def test_relaying_one_tenant_leaves_the_other_untouched(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        privileged_engine: AsyncEngine,
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        tenant_a, tenant_b = tenants
        await relay_tenant_batch(worker_sessions, tenant_a, batch_size=100)

        async with privileged_engine.connect() as conn:
            unpublished_b = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM outbox WHERE tenant_id = :id AND published_at IS NULL"
                    ),
                    {"id": tenant_b},
                )
            ).scalar_one()

        assert unpublished_b == 1, "tenant B's queue was affected by tenant A's relay pass"

    async def test_full_pass_publishes_every_tenant_exactly_once(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        assert await relay_once(worker_sessions, batch_size=100) >= 2
        # Publication is idempotent: a restarted relay cannot double-publish.
        assert await relay_once(worker_sessions, batch_size=100) == 0

    async def test_relaying_writes_a_durable_audit_event(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        privileged_engine: AsyncEngine,
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """Dispatch leaves evidence, in the tenant's own chain.

        The original implementation logged the privileged path and audited
        nothing, so a dispatch left no durable trace at all.
        """
        tenant_a, _ = tenants
        await relay_tenant_batch(worker_sessions, tenant_a, batch_size=100)

        async with privileged_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT action, actor_type, detail FROM audit_event WHERE tenant_id = :t"),
                    {"t": tenant_a},
                )
            ).one()

        assert row.action == "outbox.relayed"
        # Machine actions must never be indistinguishable from a person's.
        assert row.actor_type == "system"
        assert row.detail["message_count"] == 1

    async def test_an_idle_pass_writes_no_audit_event(
        self,
        worker_sessions: async_sessionmaker[AsyncSession],
        privileged_engine: AsyncEngine,
        tenants: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        """A one-second poll loop must not flood the trail with empty passes."""
        tenant_a, _ = tenants
        await relay_tenant_batch(worker_sessions, tenant_a, batch_size=100)
        await relay_tenant_batch(worker_sessions, tenant_a, batch_size=100)

        async with privileged_engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM audit_event WHERE tenant_id = :t"),
                    {"t": tenant_a},
                )
            ).scalar_one()

        assert count == 1, "an idle relay pass recorded an audit event"

    async def test_worker_refuses_to_act_without_a_tenant(
        self, worker_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """A tenant-scoped session for an unknown tenant must see nothing."""
        async with tenant_session(worker_sessions, _system_context(uuid.uuid4())) as session:
            visible = (await session.execute(text("SELECT count(*) FROM outbox"))).scalar_one()
        assert visible == 0


class TestSystemPrincipal:
    def test_system_work_is_attributed_as_system(self) -> None:
        assert SYSTEM_PRINCIPAL.actor_type is ActorType.SYSTEM
        assert isinstance(SYSTEM_PRINCIPAL, Principal)
