"""
================================================================================
 RELEASE-GATING SECURITY TESTS — ANALYTICAL DATA-PLANE ISOLATION
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 These tests exist because the first Phase 1A implementation got this wrong.
 `provision()` granted the shared `eip_app` role USAGE on every tenant schema,
 so every tenant's analytical data was reachable with the one credential the
 application always holds. Isolation was claimed on the strength of
 `TenantContext`, `DataPlaneHandle`, and schema qualification — none of which
 the database enforces.

 Every test below therefore does the same thing: it issues **fully-qualified
 SQL naming another tenant's schema** and requires PostgreSQL to refuse it.
 A test that only observed our own code declining to build such a query would
 prove nothing.

 The suite includes a privileged negative control (`TestNegativeControl`) which
 proves the cross-tenant data genuinely exists and is readable by a role that is
 supposed to read it. Without that control, every denial below could be passing
 because the table was empty or misnamed.
================================================================================
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.dataplane.interfaces import DataPlaneHandle, TenantRef
from eip.dataplane.schema_per_tenant import SchemaPerTenantDataPlane
from eip.dataplane.session import analytical_session
from eip.platform.context import ActorType, Capability, Principal, RoleCode, TenantContext
from eip.platform.errors import ConfigurationError
from eip.platform.settings import Settings

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: A denial from PostgreSQL, not from us.
_DENIED = ("permission denied", "does not exist")


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    ref: TenantRef
    handle: DataPlaneHandle
    #: A value unique to this tenant, so a leak is unambiguous in the assertion.
    marker: str


def _context(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="analytical-test",
        principal=Principal(
            user_id=uuid.uuid4(),
            external_subject="test",
            email="test@example.invalid",
            actor_type=ActorType.USER,
        ),
        role=RoleCode.TENANT_ADMIN,
        capabilities=frozenset(Capability),
        trace_id="trace-test",
        request_id="request-test",
    )


@pytest.fixture
async def provisioned(
    platform_engine: AsyncEngine, settings: Settings
) -> AsyncIterator[tuple[ProvisionedTenant, ProvisionedTenant]]:
    """Provision two real tenant schemas, each with a representative table."""
    plane = SchemaPerTenantDataPlane(
        platform_engine=platform_engine,
        schema_prefix=settings.data_plane_schema_prefix,
    )

    tenants: list[ProvisionedTenant] = []
    for index in range(2):
        ref = TenantRef(tenant_id=uuid.uuid4(), slug=f"analytical-{index}")
        handle = await plane.provision(ref)
        marker = f"marker-for-tenant-{index}-{ref.tenant_id}"

        # A representative analytical table, created by eip_platform exactly as
        # the ingestion subsystem will create them. The ALTER DEFAULT PRIVILEGES
        # issued during provisioning grants SELECT on it to the tenant role.
        async with platform_engine.begin() as conn:
            await conn.execute(
                text(
                    f'CREATE TABLE "{handle.namespace}".sem_revenue '
                    "(id uuid PRIMARY KEY, marker text NOT NULL, amount numeric(18,2))"
                )
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{handle.namespace}".sem_revenue (id, marker, amount) '
                    "VALUES (:id, :marker, 1234.56)"
                ),
                {"id": uuid.uuid4(), "marker": marker},
            )
        tenants.append(ProvisionedTenant(ref=ref, handle=handle, marker=marker))

    yield tenants[0], tenants[1]

    for tenant in tenants:
        await plane.deprovision(tenant.ref)


# =============================================================================
# The control: the data really is there
# =============================================================================


class TestNegativeControl:
    """Prove the fixture data exists before asserting that it cannot be read.

    Without this, every denial in this file could be a false pass — a query
    against an empty or misnamed table also returns nothing useful.
    """

    async def test_privileged_role_reads_both_tenants(
        self, platform_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned
        async with platform_engine.connect() as conn:
            for tenant in (tenant_a, tenant_b):
                marker = (
                    await conn.execute(
                        text(f'SELECT marker FROM "{tenant.handle.namespace}".sem_revenue')
                    )
                ).scalar_one()
                assert marker == tenant.marker, "fixture data is not what the test assumes"


# =============================================================================
# The mechanism
# =============================================================================


class TestRoleModel:
    """The privileges must be arranged as the design claims."""

    async def test_runtime_role_is_noinherit(self, app_engine: AsyncEngine) -> None:
        """NOINHERIT is what makes membership harmless.

        `eip_app` is a member of every per-tenant role. With INHERIT it would
        hold the union of every tenant's privileges automatically, and every
        other test in this file would fail — or worse, pass for the wrong
        reason. This is the single most important assertion here.
        """
        async with app_engine.connect() as conn:
            inherits = (
                await conn.execute(
                    text("SELECT rolinherit FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
        assert inherits is False, (
            "eip_app has INHERIT: it silently holds every tenant's analytical privileges"
        )

    async def test_runtime_role_holds_no_direct_schema_privilege(
        self, app_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """Without assuming a tenant role, `eip_app` can reach no tenant schema."""
        tenant_a, tenant_b = provisioned
        async with app_engine.connect() as conn:
            for tenant in (tenant_a, tenant_b):
                usable = (
                    await conn.execute(
                        text("SELECT has_schema_privilege(current_user, :ns, 'USAGE')"),
                        {"ns": tenant.handle.namespace},
                    )
                ).scalar_one()
                assert usable is False, (
                    f"eip_app holds USAGE on {tenant.handle.namespace} directly — this is the "
                    "exact defect the remediation removed"
                )

    async def test_each_tenant_role_reaches_only_its_own_schema(
        self, platform_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned
        async with platform_engine.connect() as conn:
            own = (
                await conn.execute(
                    text("SELECT has_schema_privilege(:role, :ns, 'USAGE')"),
                    {"role": tenant_a.handle.role, "ns": tenant_a.handle.namespace},
                )
            ).scalar_one()
            other = (
                await conn.execute(
                    text("SELECT has_schema_privilege(:role, :ns, 'USAGE')"),
                    {"role": tenant_a.handle.role, "ns": tenant_b.handle.namespace},
                )
            ).scalar_one()

        assert own is True, "tenant role cannot reach its own schema"
        assert other is False, "tenant role can reach another tenant's schema"


# =============================================================================
# The property: PostgreSQL denies the cross-tenant query
# =============================================================================


class TestDatabaseDeniesCrossTenantQueries:
    async def test_tenant_a_reads_its_own_analytical_table(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        tenant_a, _ = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            marker = (
                await session.execute(
                    text(f"SELECT marker FROM {tenant_a.handle.qualify('sem_revenue')}")
                )
            ).scalar_one()
        assert marker == tenant_a.marker

    async def test_tenant_b_reads_its_own_analytical_table(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        _, tenant_b = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_b.ref.tenant_id), tenant_b.handle
        ) as session:
            marker = (
                await session.execute(
                    text(f"SELECT marker FROM {tenant_b.handle.qualify('sem_revenue')}")
                )
            ).scalar_one()
        assert marker == tenant_b.marker

    async def test_tenant_a_cannot_query_tenant_b_fully_qualified(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """THE decisive test.

        Scoped to tenant A, issue SQL that explicitly names tenant B's schema.
        Nothing in our code prevents this statement from being constructed — the
        refusal must come from PostgreSQL.
        """
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_b.handle.namespace}".sem_revenue')
                )

        message = str(excinfo.value).lower()
        assert any(fragment in message for fragment in _DENIED), (
            f"expected a PostgreSQL denial, got: {message}"
        )

    async def test_tenant_b_cannot_query_tenant_a_fully_qualified(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_b.ref.tenant_id), tenant_b.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_a.handle.namespace}".sem_revenue')
                )

        message = str(excinfo.value).lower()
        assert any(fragment in message for fragment in _DENIED)

    async def test_cross_tenant_join_is_denied(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """A join is the shape a compiler bug would most plausibly produce."""
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(
                        "SELECT a.marker, b.marker FROM "
                        f'"{tenant_a.handle.namespace}".sem_revenue a '
                        f'CROSS JOIN "{tenant_b.handle.namespace}".sem_revenue b'
                    )
                )

    async def test_search_path_manipulation_does_not_help(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """Setting search_path to the other tenant's schema must not grant access.

        `search_path` resolves names; it does not confer privileges. Asserting
        it here forecloses a plausible "but what if code changed the search
        path" objection.
        """
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            await session.execute(text(f'SET LOCAL search_path TO "{tenant_b.handle.namespace}"'))
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(text("SELECT marker FROM sem_revenue"))


# =============================================================================
# Credential reuse and pooling
# =============================================================================


class TestCredentialReuseAcrossPooledConnections:
    async def test_assumed_role_does_not_survive_the_transaction(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """`SET LOCAL ROLE` must revert when the transaction ends.

        If it leaked, the next checkout of that pooled connection would run as
        the previous request's tenant — the worst failure this design could
        have, and the least likely to be noticed.
        """
        tenant_a, _ = provisioned
        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            assumed = (await session.execute(text("SELECT current_user"))).scalar_one()
            assert assumed == tenant_a.handle.role

        async with app_sessions() as session, session.begin():
            after = (await session.execute(text("SELECT current_user"))).scalar_one()
        assert after == "eip_app", f"assumed role leaked across checkout: {after}"

    async def test_a_later_session_cannot_reach_the_earlier_tenant(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """Tenant A's session, then tenant B's, then a probe back at A."""
        tenant_a, tenant_b = provisioned

        async with analytical_session(
            app_sessions, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            await session.execute(
                text(f"SELECT marker FROM {tenant_a.handle.qualify('sem_revenue')}")
            )

        async with analytical_session(
            app_sessions, _context(tenant_b.ref.tenant_id), tenant_b.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_a.handle.namespace}".sem_revenue')
                )

    async def test_plain_app_session_cannot_reach_any_tenant_schema(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """The credential the application always holds reaches nothing by itself.

        A fresh session per probe: a failed statement aborts its transaction, so
        reusing one would test SQLAlchemy's error handling rather than
        PostgreSQL's privilege checks.
        """
        for tenant in provisioned:
            async with app_sessions() as session, session.begin():
                with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                    await session.execute(
                        text(f'SELECT marker FROM "{tenant.handle.namespace}".sem_revenue')
                    )
            message = str(excinfo.value).lower()
            assert any(fragment in message for fragment in _DENIED)


# =============================================================================
# The session guard
# =============================================================================


class TestAnalyticalSessionGuards:
    async def test_mismatched_handle_and_context_is_refused(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """Tenant A's context with tenant B's handle must not open a session.

        This is the residual risk the design documents: `eip_app` *can* assume
        any tenant role. The guard makes the mistake impossible to reach by
        accident — the two values must agree.
        """
        tenant_a, tenant_b = provisioned
        with pytest.raises(ConfigurationError, match="handle belongs to tenant"):
            async with analytical_session(
                app_sessions, _context(tenant_a.ref.tenant_id), tenant_b.handle
            ):
                pass

    async def test_handle_without_a_role_is_refused(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        provisioned: tuple[ProvisionedTenant, ...],
    ) -> None:
        """A roleless handle would silently fall back to schema qualification."""
        tenant_a, _ = provisioned
        roleless = DataPlaneHandle(
            tenant_id=tenant_a.ref.tenant_id,
            mode=tenant_a.handle.mode,
            namespace=tenant_a.handle.namespace,
            role="",
        )
        with pytest.raises(ConfigurationError, match="no analytical role"):
            async with analytical_session(app_sessions, _context(tenant_a.ref.tenant_id), roleless):
                pass


# =============================================================================
# Offboarding
# =============================================================================


class TestDeprovisioning:
    async def test_deprovision_removes_schema_and_role(
        self, platform_engine: AsyncEngine, settings: Settings
    ) -> None:
        """Offboarding must revoke the means of access, not only the data."""
        plane = SchemaPerTenantDataPlane(
            platform_engine=platform_engine,
            schema_prefix=settings.data_plane_schema_prefix,
        )
        ref = TenantRef(tenant_id=uuid.uuid4(), slug="offboard-me")
        handle = await plane.provision(ref)

        await plane.deprovision(ref)

        async with platform_engine.connect() as conn:
            schema = (
                await conn.execute(
                    text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :n"),
                    {"n": handle.namespace},
                )
            ).scalar_one_or_none()
            role = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": handle.role}
                )
            ).scalar_one_or_none()

        assert schema is None, "tenant schema survived deprovisioning"
        assert role is None, "tenant role survived deprovisioning"
