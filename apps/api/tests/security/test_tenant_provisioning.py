"""
================================================================================
 RELEASE-GATING SECURITY TESTS — OPERATOR-DRIVEN TENANT PROVISIONING
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 Phase 1A provisioned a tenant inside one request handler: insert the row, run
 the DDL. It worked, and it had no answer for the second half failing — the
 tenant existed, its analytical schema did not, nothing recorded which, and the
 only way to find out was for somebody to try to use it.

 PO-003 made that unacceptable rather than untidy: TriVera is tenant #1 and not
 a special case, so provisioning is something staff do repeatedly, and anything
 done repeatedly gets interrupted eventually.

 Two of these tests matter more than the rest.

 `test_the_generated_password_appears_in_no_observable_surface` reads the
 tenant's ACTUAL password out of the SecretStore and then searches for that
 exact string in the API response, every column of the tenant row, every audit
 event, every outbox message, and every log record emitted during provisioning.
 Not "does the code look careful" — does the credential appear anywhere.

 `test_a_failure_message_containing_the_password_is_redacted` exists because it
 nearly did. SQLAlchemy appends the failing statement to its exception message,
 and the statement that creates a tenant role contains that role's password. A
 provisioning failure would have written the credential into a database column
 an operator reads from a console.
================================================================================
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.dataplane.credentials import ANALYTICAL_SECRET_NAME, AnalyticalCredentialProvider
from eip.dataplane.interfaces import (
    DataPlaneHandle,
    ProvisioningFence,
    TenantDataPlane,
    TenantRef,
)
from eip.dataplane.pool import TenantPoolRegistry
from eip.dataplane.schema_per_tenant import SchemaPerTenantDataPlane
from eip.dataplane.session import analytical_session
from eip.identity.provisioning import (
    ProvisioningError,
    ProvisioningState,
    TenantProvisioningWorkflow,
    summarise_failure,
)
from eip.platform.context import (
    ActorType,
    Capability,
    PlatformContext,
    Principal,
    RoleCode,
    TenantContext,
)
from eip.platform.errors import ConflictError, NotFoundError
from eip.platform.secrets import SecretRef
from eip.platform.secretstore import FileSecretStore
from eip.platform.settings import Settings
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.integration]

ELEVATION = {"X-Elevation-Reason": "provisioning a tenant for an onboarding customer"}


def _secret_ref(tenant_id: uuid.UUID, logical_name: str, version: str) -> SecretRef:
    """Rebuild the reference the tenant row stores.

    The row keeps a logical name and a version, never a value. Reassembling it
    here is what lets a test fetch the real password and then hunt for it.
    """
    return SecretRef(tenant_id=tenant_id, logical_name=logical_name, version=version)


# =============================================================================
# fixtures
# =============================================================================


def _platform_context(principal_id: uuid.UUID) -> PlatformContext:
    return PlatformContext(
        principal=Principal(
            user_id=principal_id,
            external_subject="subject-ops",
            email="ops@trivera.invalid",
            actor_type=ActorType.USER,
        ),
        reason="provisioning test",
        trace_id="trace-provisioning",
        request_id="request-provisioning",
    )


@pytest.fixture
def secret_store(tmp_path: Path) -> FileSecretStore:
    return FileSecretStore(tmp_path / "secrets")


@pytest.fixture
def credentials(secret_store: FileSecretStore, settings: Settings) -> AnalyticalCredentialProvider:
    return AnalyticalCredentialProvider(secret_store=secret_store, template_dsn=settings.db_app_dsn)


@pytest.fixture
def data_plane(
    platform_engine: AsyncEngine,
    settings: Settings,
    credentials: AnalyticalCredentialProvider,
) -> SchemaPerTenantDataPlane:
    return SchemaPerTenantDataPlane(
        platform_engine=platform_engine,
        schema_prefix=settings.data_plane_schema_prefix,
        credentials=credentials,
    )


class _BrokenDataPlane:
    """A plane that fails partway, the way a real one does.

    Not a mock that raises before doing anything: it takes the same arguments
    and fails at the step that actually fails in production — the DDL. The
    message deliberately carries a password-shaped literal, because the point
    of the partial-failure tests is what gets *recorded*.
    """

    def __init__(self, real: SchemaPerTenantDataPlane, message: str) -> None:
        self._real = real
        self._message = message
        self.calls = 0

    @property
    def mode(self) -> Any:
        return self._real.mode

    async def handle(self, tenant: TenantRef, secret_ref: Any = None) -> DataPlaneHandle:
        return await self._real.handle(tenant, secret_ref)

    async def provision(
        self, tenant: TenantRef, *, fence: ProvisioningFence | None = None
    ) -> DataPlaneHandle:
        self.calls += 1
        raise ProgrammingError(self._message, {}, Exception("permission denied"))

    async def deprovision(self, tenant: TenantRef, secret_ref: Any = None) -> None:
        await self._real.deprovision(tenant, secret_ref)

    async def describe(self, tenant: TenantRef) -> Any:
        return await self._real.describe(tenant)

    async def health(self, tenant: TenantRef) -> Any:
        return await self._real.health(tenant)


@pytest.fixture
def workflow(
    platform_sessions: async_sessionmaker[AsyncSession],
    data_plane: SchemaPerTenantDataPlane,
) -> TenantProvisioningWorkflow:
    return TenantProvisioningWorkflow(sessions=platform_sessions, data_plane=data_plane)


@pytest.fixture
async def cleanup_tenants(
    platform_engine: AsyncEngine, data_plane: SchemaPerTenantDataPlane
) -> AsyncIterator[list[uuid.UUID]]:
    """Drop anything the tests provisioned.

    Schemas and roles are cluster-level objects and outlive the transaction
    that made them, so a test that forgets to clean up poisons the next run.
    """
    provisioned: list[uuid.UUID] = []
    yield provisioned
    for tenant_id in provisioned:
        # Suppressed on purpose: a tenant whose provisioning failed has no
        # schema to drop, and a teardown error would mask the assertion that
        # already reported the real problem.
        with contextlib.suppress(Exception):
            await data_plane.deprovision(TenantRef(tenant_id=tenant_id, slug="cleanup"))
    async with platform_engine.begin() as conn:
        for table in ("audit_event", "outbox", "membership", "tenant"):
            await conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)")
                if table != "tenant"
                else text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                {"ids": provisioned},
            )


async def _tenant_row(engine: AsyncEngine, tenant_id: uuid.UUID) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT * FROM tenant WHERE id = :id"), {"id": tenant_id})
        ).mappings()
        return dict(next(iter(row)))


async def _provisioning_side_effects(
    engine: AsyncEngine, tenant_id: uuid.UUID
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Snapshot the durable effects a losing attempt must never append."""
    async with engine.connect() as conn:
        audit_rows = (
            await conn.execute(
                text(
                    "SELECT seq, action, outcome, detail FROM audit_event "
                    "WHERE tenant_id = :id ORDER BY seq"
                ),
                {"id": tenant_id},
            )
        ).all()
        outbox_rows = (
            await conn.execute(
                text(
                    "SELECT id, topic, payload FROM outbox "
                    "WHERE tenant_id = :id ORDER BY created_at, id"
                ),
                {"id": tenant_id},
            )
        ).all()
    return [tuple(row) for row in audit_rows], [tuple(row) for row in outbox_rows]


# =============================================================================
# the happy path, and its idempotence
# =============================================================================


class TestSuccessfulProvisioning:
    async def test_a_tenant_is_registered_provisioned_and_marked_ready(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        context = _platform_context(seeded.user_platform.id)
        record = await workflow.create(context, slug="northwind-freight", name="Northwind Freight")
        cleanup_tenants.append(record.id)

        assert record.provisioning_state == ProvisioningState.READY.value
        assert record.status == "active"
        assert record.provisioning_attempts == 1
        assert record.provisioning_error is None
        assert record.analytical_role

        row = await _tenant_row(platform_engine, record.id)
        assert row["provisioned_at"] is not None
        # A pointer and a version — never a value (ADR-015).
        assert row["analytical_secret_name"]
        assert row["analytical_secret_version"]

    async def test_the_analytical_schema_and_login_role_really_exist(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """The negative control for every isolation assertion below.

        If provisioning quietly created nothing, "tenant A cannot read tenant
        B" would pass for the least interesting reason imaginable.
        """
        context = _platform_context(seeded.user_platform.id)
        record = await workflow.create(context, slug="cedar-logistics", name="Cedar Logistics")
        cleanup_tenants.append(record.id)

        async with platform_engine.connect() as conn:
            schema = (
                await conn.execute(
                    text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
                    {"name": record.analytical_schema},
                )
            ).scalar_one_or_none()
            role = (
                await conn.execute(
                    text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :name"),
                    {"name": record.analytical_role},
                )
            ).scalar_one_or_none()

        assert schema == 1, "The analytical schema was not created."
        assert role is True, "The tenant role was not created, or cannot log in."

    async def test_provisioning_an_already_ready_tenant_is_a_no_op(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Idempotent by state. A retried job must not rebuild live storage."""
        context = _platform_context(seeded.user_platform.id)
        first = await workflow.create(context, slug="orchard-health", name="Orchard Health")
        cleanup_tenants.append(first.id)

        again = await workflow.provision(context, first.id)

        assert again.provisioning_state == ProvisioningState.READY.value
        # Unchanged: a no-op does not count as an attempt.
        assert again.provisioning_attempts == first.provisioning_attempts

    async def test_provisioning_an_unknown_tenant_is_not_found(
        self, workflow: TenantProvisioningWorkflow, seeded: Fixtures
    ) -> None:
        with pytest.raises(NotFoundError):
            await workflow.provision(_platform_context(seeded.user_platform.id), uuid.uuid4())


# =============================================================================
# durable audit and outbox
# =============================================================================


class TestDurableEvents:
    async def test_registration_and_provisioning_both_emit_audit_and_outbox(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        context = _platform_context(seeded.user_platform.id)
        record = await workflow.create(context, slug="summit-metals", name="Summit Metals")
        cleanup_tenants.append(record.id)

        async with platform_engine.connect() as conn:
            actions = list(
                (
                    await conn.execute(
                        text("SELECT action FROM audit_event WHERE tenant_id = :id ORDER BY seq"),
                        {"id": record.id},
                    )
                ).scalars()
            )
            topics = list(
                (
                    await conn.execute(
                        text("SELECT topic FROM outbox WHERE tenant_id = :id ORDER BY created_at"),
                        {"id": record.id},
                    )
                ).scalars()
            )

        # Subsequence, not equality: if a worker is running against the same
        # database it relays these messages and appends `outbox.relayed` to the
        # chain while the test is still going. That is the outbox working, not
        # a failure, and an equality assertion would make this suite pass or
        # fail on whether a container happened to be up.
        provisioning_actions = [action for action in actions if action.startswith("tenant.")]
        assert provisioning_actions == ["tenant.registered", "tenant.provisioned"]
        assert topics == ["tenant.registered", "tenant.provisioned"]

    async def test_a_failure_is_audited_into_the_tenants_own_chain(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """A failed provisioning is a governance event, not just a log line."""
        broken = TenantProvisioningWorkflow(
            sessions=platform_sessions,
            data_plane=_BrokenDataPlane(data_plane, "CREATE ROLE failed"),
        )
        context = _platform_context(seeded.user_platform.id)

        with pytest.raises(ProvisioningError):
            await broken.create(context, slug="harbor-freight-co", name="Harbor Freight Co")

        async with platform_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT a.action, a.outcome FROM audit_event a "
                        "JOIN tenant t ON t.id = a.tenant_id "
                        "WHERE t.slug = :slug ORDER BY a.seq"
                    ),
                    {"slug": "harbor-freight-co"},
                )
            ).all()
            tenant_id = (
                await conn.execute(
                    text("SELECT id FROM tenant WHERE slug = :slug"), {"slug": "harbor-freight-co"}
                )
            ).scalar_one()
        cleanup_tenants.append(tenant_id)

        assert [(row.action, row.outcome) for row in rows] == [
            ("tenant.registered", "success"),
            ("tenant.provisioning_failed", "failure"),
        ]


# =============================================================================
# partial failure and retry
# =============================================================================


class TestPartialFailureIsRecoverable:
    async def test_a_failed_tenant_is_left_visible_not_half_created(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        secret_store: FileSecretStore,
        credentials: AnalyticalCredentialProvider,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """The defect this task exists to fix.

        Phase 1A would have left a tenant row with no analytical schema and
        nothing recording that fact.
        """
        broken = TenantProvisioningWorkflow(
            sessions=platform_sessions,
            data_plane=_BrokenDataPlane(data_plane, "relation does not exist"),
        )
        context = _platform_context(seeded.user_platform.id)

        with pytest.raises(ProvisioningError):
            await broken.create(context, slug="quarry-systems", name="Quarry Systems")

        visible = [t for t in await broken.list_tenants(context) if t.slug == "quarry-systems"]
        assert len(visible) == 1
        stuck = visible[0]
        cleanup_tenants.append(stuck.id)

        assert stuck.provisioning_state == ProvisioningState.FAILED.value
        assert stuck.provisioning_attempts == 1
        assert stuck.provisioning_error, "A failed tenant must say why."
        assert stuck.status == "provisioning", "A tenant with no data plane is not active."

    async def test_retry_after_a_partial_failure_succeeds(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Same slug, working plane, no duplicate tenant."""
        context = _platform_context(seeded.user_platform.id)
        broken = TenantProvisioningWorkflow(
            sessions=platform_sessions,
            data_plane=_BrokenDataPlane(data_plane, "connection reset"),
        )
        with pytest.raises(ProvisioningError):
            await broken.create(context, slug="ridgeline-power", name="Ridgeline Power")

        healthy = TenantProvisioningWorkflow(sessions=platform_sessions, data_plane=data_plane)
        recovered = await healthy.create(context, slug="ridgeline-power", name="Ridgeline Power")
        cleanup_tenants.append(recovered.id)

        assert recovered.provisioning_state == ProvisioningState.READY.value
        assert recovered.status == "active"
        # Two attempts on ONE tenant. The retry resumed rather than duplicated.
        assert recovered.provisioning_attempts == 2

        all_matching = [
            t for t in await healthy.list_tenants(context) if t.slug == "ridgeline-power"
        ]
        assert len(all_matching) == 1

    async def test_incomplete_tenants_are_listed_first(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """ "Visibly recoverable" means an operator sees it without looking."""
        context = _platform_context(seeded.user_platform.id)
        good = await workflow.create(context, slug="lantern-media", name="Lantern Media")
        cleanup_tenants.append(good.id)

        broken = TenantProvisioningWorkflow(
            sessions=platform_sessions, data_plane=_BrokenDataPlane(data_plane, "boom")
        )
        with pytest.raises(ProvisioningError):
            await broken.create(context, slug="tidewater-marine", name="Tidewater Marine")

        listed = await workflow.list_tenants(context)
        cleanup_tenants.extend(t.id for t in listed if t.slug == "tidewater-marine")

        slugs = [tenant.slug for tenant in listed]
        # Relative, not absolute: the seeded fixtures are inserted directly and
        # are legitimately 'pending' too. What matters is that nothing ready
        # ever sorts above something that is not.
        assert slugs.index("tidewater-marine") < slugs.index("lantern-media")
        ready_from = next(index for index, t in enumerate(listed) if t.is_ready)
        assert all(not t.is_ready for t in listed[:ready_from])
        assert all(t.is_ready for t in listed[ready_from:])


# =============================================================================
# duplicates and concurrency
# =============================================================================


class TestDuplicatesAndRaces:
    async def test_a_second_create_for_a_ready_tenant_is_refused(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        context = _platform_context(seeded.user_platform.id)
        first = await workflow.create(context, slug="pinnacle-foods", name="Pinnacle Foods")
        cleanup_tenants.append(first.id)

        with pytest.raises(ConflictError):
            await workflow.create(context, slug="pinnacle-foods", name="Pinnacle Foods Again")

    async def test_concurrent_creates_of_the_same_slug_produce_one_tenant(
        self,
        workflow: TenantProvisioningWorkflow,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Two operators, same slug, same instant.

        The unique constraint is the guard — not the SELECT above it, which two
        concurrent callers both pass.
        """
        context = _platform_context(seeded.user_platform.id)

        results = await asyncio.gather(
            workflow.create(context, slug="vertex-analytics", name="Vertex Analytics"),
            workflow.create(context, slug="vertex-analytics", name="Vertex Analytics"),
            return_exceptions=True,
        )

        async with platform_engine.connect() as conn:
            ids = list(
                (
                    await conn.execute(
                        text("SELECT id FROM tenant WHERE slug = :slug"),
                        {"slug": "vertex-analytics"},
                    )
                ).scalars()
            )
        cleanup_tenants.extend(ids)

        assert len(ids) == 1, f"Concurrent creates produced {len(ids)} tenants."
        succeeded = [r for r in results if not isinstance(r, BaseException)]
        assert succeeded, f"Both concurrent creates failed: {results}"

    async def test_a_second_provisioning_attempt_is_refused_while_one_holds_the_claim(
        self,
        workflow: TenantProvisioningWorkflow,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        seeded: Fixtures,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """The claim is the concurrency control, and it is testable.

        A tenant is left in `in_progress` by a plane that never returns, and a
        second attempt is told the truth rather than racing it.
        """
        context = _platform_context(seeded.user_platform.id)
        registered = await workflow.register(context, slug="anchor-shipping", name="Anchor")
        cleanup_tenants.append(registered.id)

        started = asyncio.Event()
        release = asyncio.Event()

        class _HangingPlane(_BrokenDataPlane):
            async def provision(
                self, tenant: TenantRef, *, fence: ProvisioningFence | None = None
            ) -> DataPlaneHandle:
                started.set()
                await release.wait()
                return await self._real.provision(tenant, fence=fence)

        slow = TenantProvisioningWorkflow(
            sessions=platform_sessions, data_plane=_HangingPlane(data_plane, "unused")
        )
        first = asyncio.create_task(slow.provision(context, registered.id))
        await asyncio.wait_for(started.wait(), timeout=10)

        try:
            with pytest.raises(ConflictError):
                await workflow.provision(context, registered.id)
        finally:
            release.set()
            await first

    async def test_a_stale_claim_can_be_taken_over(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """A crashed attempt must not block the tenant forever.

        Without an expiry, `in_progress` is a tombstone: the process that set
        it is gone and nothing will ever clear it.
        """
        context = _platform_context(seeded.user_platform.id)
        impatient = TenantProvisioningWorkflow(
            sessions=platform_sessions, data_plane=data_plane, stale_after_seconds=0.001
        )
        registered = await impatient.register(context, slug="beacon-utilities", name="Beacon")
        cleanup_tenants.append(registered.id)

        # Exactly the state a killed process leaves behind.
        async with platform_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE tenant SET provisioning_state = 'in_progress', "
                    "provisioning_started_at = now() - interval '1 hour' WHERE id = :id"
                ),
                {"id": registered.id},
            )

        recovered = await impatient.provision(context, registered.id)
        assert recovered.provisioning_state == ProvisioningState.READY.value


class TestStaleAttemptFencing:
    async def test_late_success_cannot_overwrite_the_takeover_winner(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        secret_store: FileSecretStore,
        credentials: AnalyticalCredentialProvider,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Attempt 1 returns after attempt 2 is ready; its settle is fenced out."""
        context = _platform_context(seeded.user_platform.id)
        started = asyncio.Event()
        release = asyncio.Event()

        class _LateSuccessPlane(_BrokenDataPlane):
            async def provision(
                self, tenant: TenantRef, *, fence: ProvisioningFence | None = None
            ) -> DataPlaneHandle:
                started.set()
                await release.wait()
                return await self._real.provision(tenant, fence=fence)

        loser = TenantProvisioningWorkflow(
            sessions=platform_sessions,
            data_plane=_LateSuccessPlane(data_plane, "unused"),
            stale_after_seconds=0,
        )
        winner = TenantProvisioningWorkflow(
            sessions=platform_sessions, data_plane=data_plane, stale_after_seconds=0
        )
        registered = await loser.register(context, slug="fenced-success", name="Fenced Success")
        cleanup_tenants.append(registered.id)

        late = asyncio.create_task(loser.provision(context, registered.id))
        await asyncio.wait_for(started.wait(), timeout=10)
        winning = await winner.provision(context, registered.id)
        before_row = await _tenant_row(platform_engine, registered.id)
        before_effects = await _provisioning_side_effects(platform_engine, registered.id)

        release.set()
        with pytest.raises(ProvisioningError, match="superseded"):
            await late

        after_row = await _tenant_row(platform_engine, registered.id)
        after_effects = await _provisioning_side_effects(platform_engine, registered.id)
        assert winning.provisioning_attempts == 2
        assert before_row == after_row
        assert before_effects == after_effects

        winner_ref = _secret_ref(
            registered.id,
            str(after_row["analytical_secret_name"]),
            str(after_row["analytical_secret_version"]),
        )
        stale_ref = _secret_ref(registered.id, f"{ANALYTICAL_SECRET_NAME}-attempt-1", "1")
        with pytest.raises(NotFoundError):
            await secret_store.describe(stale_ref)

        winner_handle = DataPlaneHandle(
            tenant_id=registered.id,
            mode=workflow_mode(winner),
            namespace=str(after_row["analytical_schema"]),
            role=str(after_row["analytical_role"]),
            secret_ref=winner_ref,
        )
        pools = TenantPoolRegistry(
            credentials=credentials,
            max_tenants=1,
            pool_size=1,
            max_overflow=0,
            idle_ttl_seconds=300,
        )
        try:
            async with analytical_session(
                pools,
                _tenant_context(registered.id, seeded.user_platform.id),
                winner_handle,
            ) as session:
                identity = (
                    await session.execute(
                        text(
                            "SELECT current_user, "
                            "has_schema_privilege(current_user, :schema, 'USAGE')"
                        ),
                        {"schema": winner_handle.namespace},
                    )
                ).one()
            assert identity == (winner_handle.role, True)
        finally:
            await pools.close()

    async def test_late_failure_cannot_mark_the_takeover_winner_failed(
        self,
        platform_sessions: async_sessionmaker[AsyncSession],
        data_plane: SchemaPerTenantDataPlane,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Attempt 1 fails after attempt 2 is ready; its failure is fenced out."""
        context = _platform_context(seeded.user_platform.id)
        started = asyncio.Event()
        release = asyncio.Event()

        class _LateFailurePlane(_BrokenDataPlane):
            async def provision(
                self, tenant: TenantRef, *, fence: ProvisioningFence | None = None
            ) -> DataPlaneHandle:
                started.set()
                await release.wait()
                raise RuntimeError("late failure from superseded attempt")

        loser = TenantProvisioningWorkflow(
            sessions=platform_sessions,
            data_plane=_LateFailurePlane(data_plane, "unused"),
            stale_after_seconds=0,
        )
        winner = TenantProvisioningWorkflow(
            sessions=platform_sessions, data_plane=data_plane, stale_after_seconds=0
        )
        registered = await loser.register(context, slug="fenced-failure", name="Fenced Failure")
        cleanup_tenants.append(registered.id)

        late = asyncio.create_task(loser.provision(context, registered.id))
        await asyncio.wait_for(started.wait(), timeout=10)
        winning = await winner.provision(context, registered.id)
        before_row = await _tenant_row(platform_engine, registered.id)
        before_effects = await _provisioning_side_effects(platform_engine, registered.id)

        release.set()
        with pytest.raises(ProvisioningError, match="superseded"):
            await late

        after_row = await _tenant_row(platform_engine, registered.id)
        after_effects = await _provisioning_side_effects(platform_engine, registered.id)
        assert winning.provisioning_attempts == 2
        assert after_row["provisioning_state"] == ProvisioningState.READY.value
        assert after_row["provisioning_error"] is None
        assert before_row == after_row
        assert before_effects == after_effects


# =============================================================================
# credentials appear nowhere
# =============================================================================


class TestCredentialsAreNeverExposed:
    async def test_the_generated_password_appears_in_no_observable_surface(
        self,
        workflow: TenantProvisioningWorkflow,
        secret_store: FileSecretStore,
        credentials: AnalyticalCredentialProvider,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Search for the ACTUAL credential, not for careful-looking code.

        Everything a person or a downstream system can see is checked against
        the literal password: the returned record, every column of the tenant
        row, every audit event, every outbox message, and every log record
        emitted while provisioning ran.
        """
        context = _platform_context(seeded.user_platform.id)

        with structlog.testing.capture_logs() as captured:
            record = await workflow.create(context, slug="keystone-rail", name="Keystone Rail")
        cleanup_tenants.append(record.id)

        row = await _tenant_row(platform_engine, record.id)
        secret_ref = _secret_ref(
            record.id,
            str(row["analytical_secret_name"]),
            str(row["analytical_secret_version"]),
        )
        password = (
            await secret_store.get(secret_ref, purpose="assert the credential leaks nowhere")
        ).reveal()

        assert password, "The negative control failed: no password was stored."
        assert len(password) >= 16

        haystacks: dict[str, str] = {
            "returned record": repr(record),
            "tenant row": repr(row),
            "log records": repr(captured),
        }

        async with platform_engine.connect() as conn:
            haystacks["audit events"] = repr(
                (
                    await conn.execute(
                        text("SELECT * FROM audit_event WHERE tenant_id = :id"), {"id": record.id}
                    )
                ).all()
            )
            haystacks["outbox messages"] = repr(
                (
                    await conn.execute(
                        text("SELECT * FROM outbox WHERE tenant_id = :id"), {"id": record.id}
                    )
                ).all()
            )

        for where, haystack in haystacks.items():
            assert password not in haystack, f"The tenant's password is exposed in the {where}."

    def test_a_failure_message_containing_the_password_is_redacted(self) -> None:
        """**This one nearly shipped as a leak.**

        SQLAlchemy appends the failing statement to its exception message, and
        the statement that creates a tenant role carries that role's password
        as a quoted literal. Recording the raw message would have written the
        credential into a column an operator reads from a console.
        """
        password = "s3cr3t-generated-password-value"
        raw = ProgrammingError(
            f"CREATE ROLE \"eip_t_x\" LOGIN PASSWORD '{password}'",
            {"pw": password},
            Exception("syntax error"),
        )

        summary = summarise_failure(raw)

        assert password not in summary
        # Still useful: the operator needs to know what kind of failure it was.
        assert "ProgrammingError" in summary
        assert len(summary) <= 500

    def test_a_quoted_credential_outside_the_sql_block_is_masked(self) -> None:
        """The second limb of the redaction, tested on its own.

        Dropping everything after ``[SQL:`` handles SQLAlchemy's own format.
        A driver that puts the literal in the *message* would slip past it, so
        quoted literals are masked as well. Two defences, two tests — a single
        case covering both would pass if either were removed.
        """
        password = "another-generated-secret"
        summary = summarise_failure(RuntimeError(f"role creation failed for '{password}'"))

        assert password not in summary
        assert "'***'" in summary
        assert "RuntimeError" in summary

    def test_redaction_leaves_a_message_that_is_still_diagnostic(self) -> None:
        """Redaction must not reduce every failure to the same string."""
        first = summarise_failure(DBAPIError("stmt", {}, Exception("connection refused")))
        second = summarise_failure(ProgrammingError("stmt", {}, Exception("schema exists")))

        assert first != second
        assert "connection refused" in first
        assert "schema exists" in second

    async def test_the_api_response_carries_no_credential_material(
        self,
        client: AsyncClient,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Not even the SecretRef.

        A reference is safe to store (ADR-015). It is still not something an
        HTTP response needs, and a value that never reaches the response model
        cannot leak through one.
        """
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "willow-brands", "name": "Willow Brands"},
            headers={**auth(token), **ELEVATION},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        cleanup_tenants.append(uuid.UUID(body["id"]))

        assert body["provisioning_state"] == "ready"
        assert "password" not in response.text.lower()
        assert "secret" not in response.text.lower()
        assert set(body) == {
            "id",
            "slug",
            "name",
            "status",
            "isolation_mode",
            "analytical_schema",
            "analytical_role",
            "provisioning_state",
            "provisioning_attempts",
            "provisioning_error",
            "provisioned_at",
        }


# =============================================================================
# the provisioned tenants are actually isolated
# =============================================================================


class TestProvisionedTenantsAreIsolated:
    async def test_a_provisioned_tenant_cannot_read_another_provisioned_tenant(
        self,
        workflow: TenantProvisioningWorkflow,
        credentials: AnalyticalCredentialProvider,
        seeded: Fixtures,
        platform_engine: AsyncEngine,
        cleanup_tenants: list[uuid.UUID],
    ) -> None:
        """Provisioning must produce the isolation G10 established.

        A workflow that created schemas without the per-tenant credential model
        would pass every other test in this file and silently undo the most
        expensive fix of Phase 1A.
        """
        context = _platform_context(seeded.user_platform.id)
        first = await workflow.create(context, slug="alpha-mining", name="Alpha Mining")
        second = await workflow.create(context, slug="beta-shipping", name="Beta Shipping")
        cleanup_tenants.extend([first.id, second.id])

        # Give each tenant a table only it should be able to read.
        async with platform_engine.begin() as conn:
            for record in (first, second):
                await conn.execute(
                    text(f'CREATE TABLE "{record.analytical_schema}".sem_marker (id int)')
                )
                await conn.execute(
                    text(
                        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{record.analytical_schema}" '
                        f'TO "{record.analytical_role}"'
                    )
                )

        pools = TenantPoolRegistry(
            credentials=credentials,
            max_tenants=4,
            pool_size=1,
            max_overflow=1,
            idle_ttl_seconds=300.0,
        )
        try:
            row_first = await _tenant_row(platform_engine, first.id)
            handle = DataPlaneHandle(
                tenant_id=first.id,
                mode=workflow_mode(workflow),
                namespace=first.analytical_schema,
                role=str(first.analytical_role),
                secret_ref=_secret_ref(
                    first.id,
                    str(row_first["analytical_secret_name"]),
                    str(row_first["analytical_secret_version"]),
                ),
            )
            tenant_context = _tenant_context(first.id, seeded.user_platform.id)

            async with analytical_session(pools, tenant_context, handle) as session:
                # Positive control: its own schema is readable.
                own = await session.execute(
                    text(f'SELECT count(*) FROM "{first.analytical_schema}".sem_marker')
                )
                assert own.scalar_one() == 0

                # The hostile query. Fully qualified, naming the other tenant.
                with pytest.raises(DBAPIError) as raised:
                    await session.execute(
                        text(f'SELECT * FROM "{second.analytical_schema}".sem_marker')
                    )
            assert "permission denied" in str(raised.value).lower()
        finally:
            await pools.close()


def workflow_mode(workflow: TenantProvisioningWorkflow) -> Any:
    plane: TenantDataPlane = workflow._data_plane
    return plane.mode


def _tenant_context(tenant_id: uuid.UUID, principal_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="provisioned",
        principal=Principal(
            user_id=principal_id,
            external_subject="subject-ops",
            email="ops@trivera.invalid",
            actor_type=ActorType.USER,
        ),
        role=RoleCode.TENANT_ADMIN,
        capabilities=frozenset(Capability),
        trace_id="trace-provisioning",
        request_id="request-provisioning",
    )


# =============================================================================
# it is operator-driven, not self-serve
# =============================================================================


class TestProvisioningIsOperatorDriven:
    async def test_an_ordinary_tenant_admin_cannot_provision(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "should-not-exist", "name": "Should Not Exist"},
            headers={**auth(token), **ELEVATION},
        )
        assert response.status_code == 403

    async def test_platform_staff_still_need_an_elevation_reason(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "no-reason-given", "name": "No Reason Given"},
            headers=auth(token),
        )
        assert response.status_code == 403

    async def test_unauthenticated_provisioning_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "anonymous-tenant", "name": "Anonymous"},
            headers=ELEVATION,
        )
        assert response.status_code == 401

    async def test_the_tenant_list_is_platform_staff_only(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """The recovery console must not become a tenant directory."""
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        response = await client.get("/v1/admin/tenants", headers={**auth(token), **ELEVATION})
        assert response.status_code == 403
