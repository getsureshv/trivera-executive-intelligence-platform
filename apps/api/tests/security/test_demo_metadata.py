"""Real-PostgreSQL release gates for the frozen seeded-demo foundation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.intelligence.seed import seed_demo
from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


async def _source(client: AsyncClient, token: str) -> dict[str, object]:
    response = await client.post(
        "/v1/data-sources",
        headers={**auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "name": f"Demo source {uuid.uuid4()}",
            "connector_type": "postgresql",
            "endpoint": "demo-source.invalid:5432",
            "configuration": {"username": "reader", "database": "demo", "tls_mode": "require"},
            "credential": "stage-one-test-only",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _record_success(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    source: dict[str, object],
) -> uuid.UUID:
    test_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO connection_test "
                "(id,tenant_id,data_source_id,source_version,requested_by,status,checks,"
                "overall_code,attempt,trace_id,idempotency_key,queued_at,started_at,completed_at) "
                "VALUES (:id,:tenant,:source,:version,:user,'succeeded','[]'::jsonb,"
                "'CONNECTION_OK',1,:trace,:key,:now,:now,:now)"
            ),
            {
                "id": test_id,
                "tenant": tenant_id,
                "source": uuid.UUID(str(source["id"])),
                "version": int(str(source["version"])),
                "user": user_id,
                "trace": f"demo-{test_id}",
                "key": str(test_id),
                "now": now,
            },
        )
    return test_id


def _context(tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="demo-test",
        principal=Principal(user_id, "demo-test", "demo@test.invalid", ActorType.SYSTEM),
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id="demo-test",
        request_id="demo-test",
    )


async def test_seed_requires_same_tenant_success_and_reseeds_exactly(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    platform_sessions: async_sessionmaker[AsyncSession],
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)
    source_a = await _source(client, token_a)
    source_b = await _source(client, token_b)
    async with platform_sessions() as session, session.begin():
        with pytest.raises(ValueError, match="tenant"):
            await seed_demo(
                session,
                seeded.tenant_a.id,
                seeded.user_a.id,
                uuid.UUID(str(source_b["id"])),
            )
    async with platform_sessions() as session, session.begin():
        with pytest.raises(ValueError, match="successful"):
            await seed_demo(
                session,
                seeded.tenant_a.id,
                seeded.user_a.id,
                uuid.UUID(str(source_a["id"])),
            )
    await _record_success(platform_engine, seeded.tenant_a.id, seeded.user_a.id, source_a)
    await _record_success(platform_engine, seeded.tenant_b.id, seeded.user_b.id, source_b)
    async with platform_sessions() as session, session.begin():
        await seed_demo(
            session,
            seeded.tenant_b.id,
            seeded.user_b.id,
            uuid.UUID(str(source_b["id"])),
        )
    for _ in range(2):
        async with platform_sessions() as session, session.begin():
            await seed_demo(
                session,
                seeded.tenant_a.id,
                seeded.user_a.id,
                uuid.UUID(str(source_a["id"])),
            )
    async with platform_engine.connect() as connection:
        facts = (
            await connection.execute(
                text(
                    "SELECT kind,dimension_value_id,value FROM governed_fact "
                    "WHERE tenant_id=:tenant ORDER BY kind,dimension_value_id NULLS FIRST"
                ),
                {"tenant": seeded.tenant_a.id},
            )
        ).all()
        source_links = set(
            await connection.scalars(
                text(
                    "SELECT data_source_id FROM demo_metadata WHERE tenant_id=:tenant "
                    "AND kind='source_object'"
                ),
                {"tenant": seeded.tenant_a.id},
            )
        )
        tenant_b_count = await connection.scalar(
            text("SELECT count(*) FROM governed_fact WHERE tenant_id=:tenant"),
            {"tenant": seeded.tenant_b.id},
        )
    observations = [Decimal(str(row.value)) for row in facts if row.kind == "observation"]
    targets = [Decimal(str(row.value)) for row in facts if row.kind == "target"]
    assert len(facts) == 8
    assert sum(observations[1:]) == observations[0]
    assert sum(targets[1:]) == targets[0]
    assert source_links == {uuid.UUID(str(source_a["id"]))}
    assert tenant_b_count == 8
    async with tenant_session(
        app_sessions, _context(seeded.tenant_a.id, seeded.user_a.id)
    ) as session:
        assert await session.scalar(text("SELECT count(*) FROM governed_fact")) == 8
        assert (
            await session.scalar(
                text("SELECT count(*) FROM governed_fact WHERE tenant_id=:other"),
                {"other": seeded.tenant_b.id},
            )
            == 0
        )
    with pytest.raises(DBAPIError):
        async with platform_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO governed_fact "
                    "(id,tenant_id,dataset_id,metric_version_id,dimension_value_id,kind,"
                    "period_start,period_end,value,prior_value,computed_at,snapshot_id,"
                    "config_version,origin,owner_label,quality_status,quality_code,"
                    "quality_evaluated_at,freshness_status,freshness_code,"
                    "freshness_evaluated_at) SELECT :id,tenant_id,dataset_id,metric_version_id,"
                    "dimension_value_id,kind,period_start,period_end,value,prior_value,computed_at,"
                    "snapshot_id,config_version,origin,owner_label,quality_status,quality_code,"
                    "quality_evaluated_at,freshness_status,freshness_code,freshness_evaluated_at "
                    "FROM governed_fact WHERE tenant_id=:tenant AND kind='observation' "
                    "AND dimension_value_id IS NULL LIMIT 1"
                ),
                {"id": uuid.uuid4(), "tenant": seeded.tenant_a.id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with platform_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE governed_fact SET value=value+1 WHERE tenant_id=:tenant "
                    "AND kind='observation'"
                ),
                {"tenant": seeded.tenant_a.id},
            )


async def test_runtime_grants_are_read_only_and_published_rows_are_immutable(
    app_engine: AsyncEngine, platform_engine: AsyncEngine, seeded: Fixtures
) -> None:
    bundle_id = uuid.uuid4()
    async with platform_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO configuration_bundle "
                "(id,tenant_id,version,status,content_hash,author_id,approver_id,"
                "published_at,change_reason) VALUES "
                "(:id,:tenant,1,'published',:hash,:author,:author,:published_at,:reason)"
            ),
            {
                "id": bundle_id,
                "tenant": seeded.tenant_a.id,
                "hash": "0" * 64,
                "author": seeded.user_a.id,
                "published_at": datetime.now(UTC),
                "reason": "immutability fixture",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO demo_dataset "
                "(id,tenant_id,bundle_id,code,label,origin,description,as_of_at,reset_version) "
                "VALUES (:id,:tenant,:bundle,'immutability_fixture',:label,'seeded_demo',"
                ":description,:as_of_at,1)"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": seeded.tenant_a.id,
                "bundle": bundle_id,
                "label": "Demo dataset / seeded demonstration data",
                "description": "Isolated immutable-row test fixture.",
                "as_of_at": datetime.now(UTC),
            },
        )
    async with app_engine.connect() as connection:
        grants = set(
            await connection.scalars(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee=current_user AND table_name IN "
                    "('configuration_bundle','demo_dataset','demo_metadata','governed_fact')"
                )
            )
        )
        effective = {
            table: {
                privilege: await connection.scalar(
                    text("SELECT has_table_privilege(current_user,:table,:privilege)"),
                    {"table": table, "privilege": privilege},
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
            }
            for table in (
                "configuration_bundle",
                "demo_dataset",
                "demo_metadata",
                "governed_fact",
            )
        }
    assert grants == {"SELECT"}
    assert all(
        permissions == {"SELECT": True, "INSERT": False, "UPDATE": False, "DELETE": False}
        for permissions in effective.values()
    )
    async with platform_engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            await connection.execute(
                text(
                    "UPDATE configuration_bundle SET change_reason='changed' "
                    "WHERE tenant_id=:tenant AND id=:bundle"
                ),
                {"tenant": seeded.tenant_a.id, "bundle": bundle_id},
            )


async def test_schema_enforces_fact_kinds_duplicate_aggregate_and_append_only(
    platform_engine: AsyncEngine,
    seeded: Fixtures,
) -> None:
    async with platform_engine.connect() as connection:
        constraints = set(
            await connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid IN "
                    "('demo_metadata'::regclass,'governed_fact'::regclass)"
                )
            )
        )
        nulls_not_distinct = await connection.scalar(
            text(
                "SELECT indnullsnotdistinct FROM pg_index WHERE indexrelid="
                "'uq_governed_fact_scope'::regclass"
            )
        )
        triggers = set(
            await connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
                    "('demo_metadata'::regclass,'governed_fact'::regclass) AND NOT tgisinternal"
                )
            )
        )
    assert nulls_not_distinct is True
    assert {"fk_governed_fact_metric", "fk_governed_fact_dimension"} <= constraints
    assert {
        "demo_metadata_links",
        "governed_fact_links",
        "governed_observation_append_only",
    } <= triggers
