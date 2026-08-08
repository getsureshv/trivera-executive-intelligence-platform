"""
================================================================================
 RELEASE-GATING SECURITY TESTS — PER-TENANT ANALYTICAL CREDENTIALS (G10)
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 This suite exists because the previous design was *nearly* right and therefore
 harder to argue with. Each tenant had its own database role, PostgreSQL did
 enforce the boundary, and the cross-schema tests passed. But `eip_app` was a
 member of every tenant role and reached them with `SET ROLE`, so:

   * one credential could reach every tenant, and
   * which tenant it reached was an application decision.

 Code naming tenant B while serving tenant A would have been obeyed. That was
 finding G10 — a residual risk, documented and bounded, but real.

 The mechanism is now different in kind rather than degree. Each tenant has its
 own **login** role and its own password. There is no membership and no role to
 assume. A connection *is* tenant A and has no means of becoming tenant B, so
 the same coding error produces `permission denied` instead of data.

 These tests attack that claim from six directions, one per stated requirement.
================================================================================
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from eip.dataplane.credentials import (
    AnalyticalCredential,
    AnalyticalCredentialProvider,
)
from eip.dataplane.interfaces import DataPlaneHandle, TenantRef
from eip.dataplane.pool import TenantPoolRegistry
from eip.dataplane.schema_per_tenant import TENANT_ROLE_PREFIX, SchemaPerTenantDataPlane
from eip.dataplane.session import analytical_session
from eip.platform.context import ActorType, Capability, Principal, RoleCode, TenantContext
from eip.platform.secretstore import FileSecretStore
from eip.platform.settings import Settings

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: A refusal from PostgreSQL, not from us.
_DENIED = ("permission denied", "does not exist")


@dataclass(frozen=True, slots=True)
class ProvisionedTenant:
    ref: TenantRef
    handle: DataPlaneHandle
    marker: str

    @property
    def credential(self) -> AnalyticalCredential:
        assert self.handle.secret_ref is not None
        return AnalyticalCredential(
            tenant_id=self.ref.tenant_id,
            role=self.handle.role,
            secret_ref=self.handle.secret_ref,
        )


def _context(tenant_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="credential-test",
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
def secret_store(tmp_path: Path) -> FileSecretStore:
    return FileSecretStore(tmp_path / "secrets")


@pytest.fixture
def credentials(secret_store: FileSecretStore, settings: Settings) -> AnalyticalCredentialProvider:
    return AnalyticalCredentialProvider(secret_store=secret_store, template_dsn=settings.db_app_dsn)


@pytest.fixture
def pools(credentials: AnalyticalCredentialProvider) -> TenantPoolRegistry:
    return TenantPoolRegistry(
        credentials=credentials,
        max_tenants=4,
        pool_size=1,
        max_overflow=1,
        idle_ttl_seconds=300.0,
    )


@pytest.fixture
async def provisioned(
    platform_engine: AsyncEngine,
    settings: Settings,
    credentials: AnalyticalCredentialProvider,
    pools: TenantPoolRegistry,
) -> AsyncIterator[tuple[ProvisionedTenant, ProvisionedTenant]]:
    """Two fully provisioned tenants, each with its own login role and data."""
    plane = SchemaPerTenantDataPlane(
        platform_engine=platform_engine,
        schema_prefix=settings.data_plane_schema_prefix,
        credentials=credentials,
    )

    tenants: list[ProvisionedTenant] = []
    for index in range(2):
        ref = TenantRef(tenant_id=uuid.uuid4(), slug=f"cred-{index}")
        handle = await plane.provision(ref)
        marker = f"marker-{index}-{ref.tenant_id}"

        async with platform_engine.begin() as conn:
            await conn.execute(
                text(
                    f'CREATE TABLE "{handle.namespace}".sem_revenue '
                    "(id uuid PRIMARY KEY, marker text NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{handle.namespace}".sem_revenue (id, marker) '
                    "VALUES (:id, :marker)"
                ),
                {"id": uuid.uuid4(), "marker": marker},
            )
            # Granted after creation because ALTER DEFAULT PRIVILEGES applies to
            # objects created *after* it, and the table is created here.
            await conn.execute(
                text(
                    f'GRANT SELECT ON ALL TABLES IN SCHEMA "{handle.namespace}" TO "{handle.role}"'
                )
            )
        tenants.append(ProvisionedTenant(ref=ref, handle=handle, marker=marker))

    yield tenants[0], tenants[1]

    await pools.close()
    for tenant in tenants:
        await plane.deprovision(tenant.ref, tenant.handle.secret_ref)


# =============================================================================
# The privilege model
# =============================================================================


class TestRuntimeRoleHoldsNothing:
    """`eip_app` must have no path to any tenant's analytical data."""

    async def test_runtime_role_has_no_privilege_on_any_tenant_schema(
        self, app_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        async with app_engine.connect() as conn:
            for tenant in provisioned:
                usable = (
                    await conn.execute(
                        text("SELECT has_schema_privilege(current_user, :ns, 'USAGE')"),
                        {"ns": tenant.handle.namespace},
                    )
                ).scalar_one()
                assert usable is False, f"eip_app holds USAGE on {tenant.handle.namespace}"

    async def test_runtime_role_is_a_member_of_no_tenant_role(
        self, app_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """The assertion that closes G10.

        Membership was the whole mechanism: it is what made `SET ROLE` possible
        and what kept `eip_app` a credential that could reach every tenant.
        """
        async with app_engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_auth_members m "
                        "JOIN pg_roles r ON r.oid = m.roleid "
                        "JOIN pg_roles grantee ON grantee.oid = m.member "
                        "WHERE grantee.rolname = current_user AND r.rolname LIKE :prefix"
                    ),
                    {"prefix": f"{TENANT_ROLE_PREFIX}%"},
                )
            ).scalar_one()
        assert count == 0, f"eip_app is a member of {count} tenant role(s) and could assume them"

    async def test_runtime_role_cannot_set_role_to_a_tenant(
        self, app_sessions: object, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """Even if the deleted code path returned, PostgreSQL would refuse it."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        assert isinstance(app_sessions, async_sessionmaker)
        tenant_a, _ = provisioned

        async with app_sessions() as session, session.begin():
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(text(f'SET LOCAL ROLE "{tenant_a.handle.role}"'))
        assert "permission denied" in str(excinfo.value).lower()

    async def test_each_tenant_role_can_login_and_is_otherwise_unprivileged(
        self, platform_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        async with platform_engine.connect() as conn:
            for tenant in provisioned:
                row = (
                    await conn.execute(
                        text(
                            "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, "
                            "rolinherit FROM pg_roles WHERE rolname = :r"
                        ),
                        {"r": tenant.handle.role},
                    )
                ).one()
                assert row.rolcanlogin is True, "the tenant role must be able to connect"
                assert row.rolsuper is False
                assert row.rolbypassrls is False
                assert row.rolcreaterole is False
                assert row.rolinherit is False


# =============================================================================
# Requirement 1 & 2 — own data yes, other tenant no
# =============================================================================


class TestCrossTenantAccessIsDeniedByPostgres:
    async def test_tenant_a_reads_tenant_a(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, _ = provisioned
        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            marker = (
                await session.execute(
                    text(f"SELECT marker FROM {tenant_a.handle.qualify('sem_revenue')}")
                )
            ).scalar_one()
        assert marker == tenant_a.marker

    async def test_tenant_b_reads_tenant_b(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        _, tenant_b = provisioned
        async with analytical_session(
            pools, _context(tenant_b.ref.tenant_id), tenant_b.handle
        ) as session:
            marker = (
                await session.execute(
                    text(f"SELECT marker FROM {tenant_b.handle.qualify('sem_revenue')}")
                )
            ).scalar_one()
        assert marker == tenant_b.marker

    async def test_tenant_a_cannot_read_tenant_b_fully_qualified(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """THE requirement: a coding error naming tenant B must be denied.

        Nothing in our code stops this statement being constructed or sent. The
        refusal comes from PostgreSQL, because the connection authenticated as
        tenant A simply does not hold the privilege.
        """
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_b.handle.namespace}".sem_revenue')
                )
        assert any(f in str(excinfo.value).lower() for f in _DENIED)

    async def test_tenant_b_cannot_read_tenant_a_fully_qualified(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            pools, _context(tenant_b.ref.tenant_id), tenant_b.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_a.handle.namespace}".sem_revenue')
                )
        assert any(f in str(excinfo.value).lower() for f in _DENIED)

    async def test_cross_tenant_join_is_denied(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(
                        "SELECT a.marker, b.marker FROM "
                        f'"{tenant_a.handle.namespace}".sem_revenue a '
                        f'CROSS JOIN "{tenant_b.handle.namespace}".sem_revenue b'
                    )
                )

    async def test_tenant_credential_cannot_read_the_control_plane(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """A tenant credential must not reach memberships or the audit trail.

        Per-tenant roles are new logins; without this, a compromise of one
        tenant's password would expose the control plane to it.
        """
        tenant_a, _ = provisioned
        for table in ("membership", "audit_event", "tenant"):
            async with analytical_session(
                pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
            ) as session:
                with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                    await session.execute(text(f"SELECT count(*) FROM public.{table}"))
            assert any(f in str(excinfo.value).lower() for f in _DENIED), table


# =============================================================================
# Requirement 3 — a tenant credential cannot assume another tenant
# =============================================================================


class TestTenantCredentialCannotEscalate:
    async def test_tenant_a_cannot_set_role_to_tenant_b(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """The escalation the old design made trivially available."""
        tenant_a, tenant_b = provisioned
        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                await session.execute(text(f'SET LOCAL ROLE "{tenant_b.handle.role}"'))
        assert "permission denied" in str(excinfo.value).lower()

    async def test_tenant_a_cannot_set_role_to_the_runtime_or_platform_role(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, _ = provisioned
        for target in ("eip_app", "eip_platform", "eip_migrator"):
            async with analytical_session(
                pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
            ) as session:
                with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
                    await session.execute(text(f'SET LOCAL ROLE "{target}"'))
            assert "permission denied" in str(excinfo.value).lower(), target

    async def test_tenant_role_is_a_member_of_nothing(
        self, platform_engine: AsyncEngine, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        async with platform_engine.connect() as conn:
            for tenant in provisioned:
                count = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_auth_members m "
                            "JOIN pg_roles grantee ON grantee.oid = m.member "
                            "WHERE grantee.rolname = :r"
                        ),
                        {"r": tenant.handle.role},
                    )
                ).scalar_one()
                assert count == 0, f"{tenant.handle.role} is a member of {count} role(s)"


# =============================================================================
# Requirement 4 — pooled connections cannot change tenants
# =============================================================================


class TestPoolsAreIsolatedByTenant:
    async def test_each_tenant_gets_a_distinct_pool(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned
        sessions_a = await pools.sessions_for(tenant_a.credential)
        sessions_b = await pools.sessions_for(tenant_b.credential)
        assert sessions_a is not sessions_b

    async def test_a_connection_is_always_the_same_tenant(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """Use A, then B, then A again, on a registry small enough to reuse.

        Under the old design a returned connection was a general credential
        awaiting its next `SET ROLE`. Here `current_user` is fixed by
        authentication and cannot be changed by anything the pool does.
        """
        tenant_a, tenant_b = provisioned

        for tenant in (tenant_a, tenant_b, tenant_a, tenant_b):
            async with analytical_session(
                pools, _context(tenant.ref.tenant_id), tenant.handle
            ) as session:
                current = (await session.execute(text("SELECT current_user"))).scalar_one()
                assert current == tenant.handle.role, (
                    f"pooled connection served {current} while acting for {tenant.handle.role}"
                )

    async def test_a_reused_connection_still_cannot_reach_the_other_tenant(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        tenant_a, tenant_b = provisioned

        # Warm both pools, then probe across on a recycled connection.
        for tenant in (tenant_a, tenant_b):
            async with analytical_session(
                pools, _context(tenant.ref.tenant_id), tenant.handle
            ) as session:
                await session.execute(text("SELECT 1"))

        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_b.handle.namespace}".sem_revenue')
                )

    async def test_registry_is_bounded_and_evicts(
        self,
        pools: TenantPoolRegistry,
        provisioned: tuple[ProvisionedTenant, ...],
        platform_engine: AsyncEngine,
        settings: Settings,
        credentials: AnalyticalCredentialProvider,
    ) -> None:
        """A pool per tenant must not mean unbounded connections.

        PostgreSQL's max_connections is a hard cluster limit; exhausting it
        takes every tenant down at once.
        """
        plane = SchemaPerTenantDataPlane(
            platform_engine=platform_engine,
            schema_prefix=settings.data_plane_schema_prefix,
            credentials=credentials,
        )
        extra: list[TenantRef] = []
        try:
            for index in range(5):
                ref = TenantRef(tenant_id=uuid.uuid4(), slug=f"bound-{index}")
                handle = await plane.provision(ref)
                extra.append(ref)
                assert handle.secret_ref is not None
                await pools.sessions_for(
                    AnalyticalCredential(
                        tenant_id=ref.tenant_id,
                        role=handle.role,
                        secret_ref=handle.secret_ref,
                    )
                )
                assert pools.size <= 4, f"registry grew to {pools.size}, above its bound"
        finally:
            for ref in extra:
                await plane.deprovision(ref)

    async def test_eviction_does_not_break_a_later_session(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """A pool is a cache: evicting it costs a reconnection, never access."""
        tenant_a, _ = provisioned
        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            await session.execute(text("SELECT 1"))

        await pools.evict(tenant_a.ref.tenant_id)
        assert tenant_a.ref.tenant_id not in pools.tracked_tenants()

        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            marker = (
                await session.execute(
                    text(f"SELECT marker FROM {tenant_a.handle.qualify('sem_revenue')}")
                )
            ).scalar_one()
        assert marker == tenant_a.marker


# =============================================================================
# Requirement 5 — a worker holds only the active tenant's credential
# =============================================================================


class TestWorkerHoldsOnlyTheActiveTenantCredential:
    async def test_a_worker_processing_tenant_a_cannot_reach_tenant_b(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """Background work uses the same mechanism as a request.

        A worker acquires a session by presenting a tenant's credential. It has
        no ambient analytical access, so "processing tenant A" is the only state
        in which it can read anything at all.
        """
        tenant_a, tenant_b = provisioned

        async with analytical_session(
            pools, _context(tenant_a.ref.tenant_id), tenant_a.handle
        ) as session:
            assert (
                await session.execute(text("SELECT current_user"))
            ).scalar_one() == tenant_a.handle.role
            with pytest.raises((ProgrammingError, DBAPIError)):
                await session.execute(
                    text(f'SELECT marker FROM "{tenant_b.handle.namespace}".sem_revenue')
                )

    async def test_a_handle_without_a_credential_cannot_open_a_session(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """No credential, no access — not a silent fall-back to shared access."""
        from eip.platform.errors import ConfigurationError

        tenant_a, _ = provisioned
        bare = DataPlaneHandle(
            tenant_id=tenant_a.ref.tenant_id,
            mode=tenant_a.handle.mode,
            namespace=tenant_a.handle.namespace,
            role=tenant_a.handle.role,
            secret_ref=None,
        )
        with pytest.raises(ConfigurationError, match="no analytical credential"):
            async with analytical_session(pools, _context(tenant_a.ref.tenant_id), bare):
                pass

    async def test_a_mismatched_handle_is_refused(
        self, pools: TenantPoolRegistry, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        from eip.platform.errors import ConfigurationError

        tenant_a, tenant_b = provisioned
        with pytest.raises(ConfigurationError, match="handle belongs to tenant"):
            async with analytical_session(pools, _context(tenant_a.ref.tenant_id), tenant_b.handle):
                pass


# =============================================================================
# Requirement 6 — no credential in logs, payloads, or metadata
# =============================================================================


class TestCredentialsNeverLeak:
    async def test_the_handle_carries_a_reference_not_a_value(
        self,
        provisioned: tuple[ProvisionedTenant, ...],
        secret_store: FileSecretStore,
    ) -> None:
        """A handle travels through job payloads, so it must be inert.

        `repr` is what a log line or a serialiser reaches for. The assertion is
        that the *password value* is absent — not that the word "password" is,
        since the logical name is literally `analytical-db-password`. A
        reference naming what it points at is the point; a reference carrying
        the value would be the defect.
        """
        tenant_a, _ = provisioned
        assert tenant_a.handle.secret_ref is not None

        password = (
            await secret_store.get(tenant_a.credential.secret_ref, purpose="test assertion")
        ).reveal()
        rendered = repr(tenant_a.handle)

        assert password not in rendered, "the handle rendered the password"
        # A pointer and a version, and nothing that could be used to connect.
        assert tenant_a.handle.secret_ref.logical_name in rendered
        assert tenant_a.handle.secret_ref.version in rendered

    async def test_the_password_is_not_in_the_tenant_row(
        self,
        platform_engine: AsyncEngine,
        provisioned: tuple[ProvisionedTenant, ...],
        secret_store: FileSecretStore,
    ) -> None:
        """A dump of the control plane must contain no credential material."""
        tenant_a, _ = provisioned
        password = (
            await secret_store.get(tenant_a.credential.secret_ref, purpose="test assertion")
        ).reveal()

        async with platform_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT * FROM tenant WHERE analytical_role = :r"),
                    {"r": tenant_a.handle.role},
                )
            ).one_or_none()

        if row is not None:
            assert password not in " ".join(str(value) for value in row), (
                "the analytical password is stored on the tenant row"
            )

    async def test_the_connection_url_masks_the_password(
        self, credentials: AnalyticalCredentialProvider, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """`str(url)` is what ends up in an exception or a debug line."""
        tenant_a, _ = provisioned
        url = await credentials.url_for(tenant_a.credential)
        assert "***" in str(url)
        assert url.password is not None
        assert url.password not in str(url)
        assert url.password not in repr(url)

    async def test_secret_values_cannot_be_rendered_or_serialised(
        self, secret_store: FileSecretStore, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        import json
        import pickle

        tenant_a, _ = provisioned
        secret = await secret_store.get(tenant_a.credential.secret_ref, purpose="test assertion")

        assert secret.reveal() not in repr(secret)
        assert secret.reveal() not in str(secret)
        assert secret.reveal() not in f"{secret}"
        with pytest.raises(TypeError):
            json.dumps({"password": secret})
        with pytest.raises(TypeError):
            pickle.dumps(secret)

    async def test_provisioning_logs_no_credential(
        self,
        platform_engine: AsyncEngine,
        settings: Settings,
        credentials: AnalyticalCredentialProvider,
        secret_store: FileSecretStore,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Capture provisioning's own output and search it for the password."""
        plane = SchemaPerTenantDataPlane(
            platform_engine=platform_engine,
            schema_prefix=settings.data_plane_schema_prefix,
            credentials=credentials,
        )
        ref = TenantRef(tenant_id=uuid.uuid4(), slug="log-check")
        capsys.readouterr()
        try:
            handle = await plane.provision(ref)
            captured = capsys.readouterr()
            assert handle.secret_ref is not None
            password = (
                await secret_store.get(handle.secret_ref, purpose="test assertion")
            ).reveal()

            for stream in (captured.out, captured.err):
                assert password not in stream, "the password was written to the log"
        finally:
            await plane.deprovision(ref)

    async def test_the_secret_file_is_not_world_readable(
        self, secret_store: FileSecretStore, provisioned: tuple[ProvisionedTenant, ...]
    ) -> None:
        """Reading enforces the mode, so a loosened file fails closed."""
        import os

        if os.name != "posix":
            pytest.skip("POSIX file modes do not apply on this platform")

        tenant_a, _ = provisioned
        # Succeeds only because the mode is still 0600; FileSecretStore.get
        # raises ConfigurationError otherwise.
        value = await secret_store.get(tenant_a.credential.secret_ref, purpose="test assertion")
        assert value.reveal()
