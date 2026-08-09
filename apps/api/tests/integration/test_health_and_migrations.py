"""Integration tests: health, readiness, migrations, and the outbox.

Health/readiness are tested because the distinction between them is
operationally load-bearing (ADR-014 §8) and easy to get subtly wrong — a
readiness probe that touches no dependency, or a liveness probe that does, both
cause the wrong response to an outage.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.db import GLOBAL_TABLES
from tests.conftest import Fixtures

pytestmark = pytest.mark.integration


class TestHealthAndReadiness:
    async def test_health_reports_liveness_without_touching_dependencies(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"]
        assert body["version"]
        # Liveness must not report on dependencies; if it did, a database blip
        # would restart every replica.
        assert "checks" not in body

    async def test_readiness_verifies_dependencies(self, client: AsyncClient) -> None:
        response = await client.get("/ready")
        assert response.status_code == 200, response.text

        checks = {check["name"]: check for check in response.json()["checks"]}
        assert checks["database"]["status"] == "pass"
        assert checks["migrations"]["status"] == "pass"
        # The platform's core guarantee is checked on every readiness probe.
        assert checks["tenant_isolation"]["status"] == "pass"

    async def test_readiness_does_not_leak_dependency_detail(self, client: AsyncClient) -> None:
        """The probe is often unauthenticated; it must not echo DSNs or hosts."""
        body = (await client.get("/ready")).text
        for fragment in ("password", "local_dev_only", "postgresql://", "@localhost"):
            assert fragment not in body

    async def test_correlation_headers_are_returned(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.headers["x-request-id"]
        assert response.headers["x-trace-id"]

    async def test_inbound_traceparent_is_continued(self, client: AsyncClient) -> None:
        """A trace begun in the browser must continue server-side (ADR-014 §2)."""
        trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
        response = await client.get(
            "/health",
            headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
        )
        assert response.headers["x-trace-id"] == trace_id


class TestMigrations:
    async def test_every_tenant_scoped_table_has_forced_rls_and_a_policy(
        self, app_engine: AsyncEngine
    ) -> None:
        """A migration that adds a table without a policy must fail the build.

        This is the check that turns "remember to add RLS" into an enforced
        invariant. Without it, the isolation model degrades one migration at a
        time, silently.
        """
        async with app_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policies p WHERE p.schemaname='public' "
                        " AND p.tablename=c.relname) AS policies "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relkind='r'"
                    )
                )
            ).all()

        assert rows, "no tables found — did migrations run?"

        problems = [
            f"{row.relname}: rls={row.relrowsecurity} forced={row.relforcerowsecurity} "
            f"policies={row.policies}"
            for row in rows
            if row.relname not in GLOBAL_TABLES
            and not (row.relrowsecurity and row.relforcerowsecurity and row.policies > 0)
        ]
        assert not problems, "Tenant-scoped tables without enforced RLS:\n  " + "\n  ".join(
            problems
        )

    async def test_every_tenant_scoped_table_has_a_tenant_id_column(
        self, app_engine: AsyncEngine
    ) -> None:
        async with app_engine.connect() as conn:
            tables = (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            ).scalars()
            missing = []
            for table in tables:
                if table in GLOBAL_TABLES:
                    continue
                has_column = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = :t AND column_name = 'tenant_id'"
                        ),
                        {"t": table},
                    )
                ).scalar_one_or_none()
                if not has_column:
                    missing.append(table)

        assert not missing, f"tenant-scoped tables without tenant_id: {missing}"

    async def test_audit_event_is_append_only_for_the_runtime_role(
        self, app_engine: AsyncEngine
    ) -> None:
        async with app_engine.connect() as conn:
            grants = set(
                (
                    await conn.execute(
                        text(
                            "SELECT privilege_type FROM information_schema.role_table_grants "
                            "WHERE grantee = current_user AND table_name = 'audit_event'"
                        )
                    )
                ).scalars()
            )
        assert "SELECT" in grants
        assert "INSERT" in grants
        assert "UPDATE" not in grants, "audit trail is rewritable — it is not an audit trail"
        assert "DELETE" not in grants, "audit trail is erasable — it is not an audit trail"

    async def test_migration_state_is_current(self, app_engine: AsyncEngine) -> None:
        async with app_engine.connect() as conn:
            revision = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        # The current head. Update when a migration is added, so that a
        # forgotten `alembic upgrade` in an environment is caught here rather
        # than by a confusing failure elsewhere.
        assert revision == "0007_source_retention"

    async def test_seed_roles_are_present(self, app_engine: AsyncEngine) -> None:
        async with app_engine.connect() as conn:
            roles = set((await conn.execute(text("SELECT code FROM role"))).scalars())
        assert {"platform_admin", "tenant_admin", "data_steward", "executive", "viewer"} <= roles

    async def test_runtime_role_cannot_create_tables(self, app_engine: AsyncEngine) -> None:
        """Schema changes must ship as migrations (guardrail 17).

        The runtime role has no DDL rights at all, so an accidental
        ``create_all()`` in application code cannot mutate production schema.
        """
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            async with app_engine.begin() as conn:
                await conn.execute(text("CREATE TABLE should_not_exist (id int)"))


class TestOutbox:
    async def test_outbox_rows_are_tenant_scoped(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """The relay's claim query carries no tenant predicate; RLS supplies it."""
        from eip.platform.context import (
            ActorType,
            Principal,
            RoleCode,
            TenantContext,
        )
        from eip.platform.db import tenant_session

        def context(tenant_id: uuid.UUID) -> TenantContext:
            return TenantContext(
                tenant_id=tenant_id,
                tenant_slug="t",
                principal=Principal(
                    user_id=uuid.uuid4(),
                    external_subject="s",
                    email="e@example.invalid",
                    actor_type=ActorType.SYSTEM,
                ),
                role=RoleCode.VIEWER,
                capabilities=frozenset(),
                trace_id="t",
                request_id="r",
            )

        for tenant in (seeded.tenant_a, seeded.tenant_b):
            async with tenant_session(app_sessions, context(tenant.id)) as session:
                await session.execute(
                    text(
                        "INSERT INTO outbox (id, tenant_id, topic, payload, trace_id) "
                        "VALUES (:id, :tenant_id, 'test.topic', '{}', 'trace')"
                    ),
                    {"id": uuid.uuid4(), "tenant_id": tenant.id},
                )

        async with tenant_session(app_sessions, context(seeded.tenant_a.id)) as session:
            visible = (await session.execute(text("SELECT tenant_id FROM outbox"))).all()

        assert len(visible) == 1
        assert visible[0].tenant_id == seeded.tenant_a.id
