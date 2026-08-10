"""Real-PostgreSQL governed-query, provenance, lineage, and leakage gates."""

from __future__ import annotations

import json
import uuid

import pytest
import structlog.testing
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from eip.intelligence.seed import seed_demo
from eip.platform.settings import Settings
from tests.conftest import Fixtures, auth, token_for
from tests.security.test_demo_metadata import _record_success, _source

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


async def _seed(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    platform_sessions: async_sessionmaker[AsyncSession],
) -> tuple[str, dict[str, object]]:
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    source = await _source(client, token)
    await _record_success(platform_engine, seeded.tenant_a.id, seeded.user_a.id, source)
    async with platform_sessions() as session, session.begin():
        await seed_demo(
            session,
            seeded.tenant_a.id,
            seeded.user_a.id,
            uuid.UUID(str(source["id"])),
        )
    return token, source


async def test_exact_dashboard_drilldown_attention_provenance_and_lineage(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    platform_sessions: async_sessionmaker[AsyncSession],
) -> None:
    token, source = await _seed(client, seeded, platform_engine, platform_sessions)
    dashboard = await client.get("/v1/dashboards/executive", headers=auth(token))
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert (body["value"], body["prior_value"], body["target"]) == (
        "4210500",
        "3980000",
        "4500000",
    )
    assert body["comparison"]["absolute"] == "230500"
    assert body["comparison"]["percent"] == "5.791457286432160804020100503"
    assert body["target_variance"]["absolute"] == "-289500"
    assert body["target_variance"]["percent"] == "-6.433333333333333333333333333"
    assert body["quality_status"] == "pass"
    assert body["freshness_status"] == "fresh"
    assert body["provenance"]["origin_label"] == "Demo dataset / seeded demonstration data"
    assert body["provenance"]["observation_basis"] == (
        "seeded_demo_observations_not_live_extraction"
    )
    health = body["provenance"]["selected_source"]
    assert health["data_source_id"] == source["id"]
    assert health["relationship"] == "selected_source_connection_health_only"
    assert body["drill_down"] == []

    query = await client.post(
        "/v1/metrics/revenue_ytd/query",
        headers=auth(token),
        json={
            "period": {
                "kind": "calendar_ytd",
                "timezone": "America/Chicago",
                "as_of_at": "2026-08-11T17:00:00-05:00",
            },
            "group_by": "segment",
        },
    )
    assert query.status_code == 200, query.text
    slices = query.json()["drill_down"]
    assert [item["label"] for item in slices] == ["People", "Process", "Technology"]
    assert sum(int(item["value"]) for item in slices) == int(query.json()["value"])
    assert query.json()["attention"]["label"] == "Technology"
    plain_query = await client.post(
        "/v1/metrics/revenue_ytd/query",
        headers=auth(token),
        json={
            "period": {
                "kind": "calendar_ytd",
                "timezone": "America/Chicago",
                "as_of_at": "2026-08-11T17:00:00-05:00",
            }
        },
    )
    assert plain_query.status_code == 200

    lineage = await client.get(
        "/v1/metrics/revenue_ytd/lineage?config_version=1", headers=auth(token)
    )
    assert lineage.status_code == 200, lineage.text
    assert [node["kind"] for node in lineage.json()["nodes"]] == [
        "widget",
        "metric_version",
        "semantic_field",
        "field_binding",
        "source_field",
        "source_object",
        "data_source",
    ]
    assert len(lineage.json()["edges"]) == 6
    lineage_provenance = lineage.json()["provenance"]
    assert lineage_provenance["configuration_version"] == 1
    assert lineage_provenance["snapshot_id"]
    assert lineage_provenance["calculated_at"]
    assert lineage_provenance["origin_label"] == "Demo dataset / seeded demonstration data"
    assert lineage_provenance["observation_basis"] == (
        "seeded_demo_observations_not_live_extraction"
    )
    assert lineage_provenance["selected_source"]["relationship"] == (
        "selected_source_connection_health_only"
    )
    with structlog.testing.capture_logs() as captured:
        logged = await client.get("/v1/dashboards/executive", headers=auth(token))
    assert logged.status_code == 200
    captured_text = json.dumps(captured, default=str)
    for forbidden in (
        "4210500",
        "3980000",
        "4500000",
        "People",
        "Process",
        "Technology",
        "Revenue.Amount",
        "segment_ref",
        "demo-source.invalid",
    ):
        assert forbidden not in captured_text

    async with platform_engine.connect() as connection:
        leaked = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE tenant_id=:tenant AND "
                "detail::text ~ "
                "'(4210500|3980000|4500000|People|Process|Technology|Revenue.Amount|"
                "amount|segment_ref|demo-source.invalid)'"
            ),
            {"tenant": seeded.tenant_a.id},
        )
        actions = set(
            await connection.scalars(
                text(
                    "SELECT action FROM audit_event WHERE tenant_id=:tenant AND action IN "
                    "('dashboard.viewed','metric.queried','metric.drilldown_queried','lineage.viewed')"
                ),
                {"tenant": seeded.tenant_a.id},
            )
        )
    assert leaked == 0
    assert actions == {
        "dashboard.viewed",
        "metric.queried",
        "metric.drilldown_queried",
        "lineage.viewed",
    }


async def test_cross_tenant_malformed_config_and_disabled_source_fail_closed(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    platform_sessions: async_sessionmaker[AsyncSession],
) -> None:
    token, source = await _seed(client, seeded, platform_engine, platform_sessions)
    token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)
    platform_token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
    foreign = await client.get(
        "/v1/metrics/revenue_ytd/lineage?config_version=1", headers=auth(token_b)
    )
    missing = await client.get(
        "/v1/metrics/revenue_ytd/lineage?config_version=999", headers=auth(token)
    )
    assert foreign.status_code == missing.status_code == 404
    for field in ("title", "status", "detail", "code"):
        assert foreign.json()[field] == missing.json()[field]
    assert (
        await client.get("/v1/dashboards/executive", headers=auth(platform_token))
    ).status_code == 403
    assert (
        await client.get("/v1/metrics/revenue_ytd/lineage?config_version=999", headers=auth(token))
    ).status_code == 404
    malformed = await client.post(
        "/v1/metrics/revenue_ytd/query",
        headers=auth(token),
        json={
            "period": {
                "kind": "calendar_ytd",
                "timezone": "America/Chicago",
                "as_of_at": "2026-08-11T17:00:00-05:00",
            },
            "sql": "SELECT secret FROM somewhere",
        },
    )
    assert malformed.status_code == 422
    naive = await client.post(
        "/v1/metrics/revenue_ytd/query",
        headers=auth(token),
        json={
            "period": {
                "kind": "calendar_ytd",
                "timezone": "America/Chicago",
                "as_of_at": "2026-08-11T17:00:00",
            }
        },
    )
    assert naive.status_code == 422
    unknown_dimension = await client.post(
        "/v1/metrics/revenue_ytd/query",
        headers=auth(token),
        json={
            "period": {
                "kind": "calendar_ytd",
                "timezone": "America/Chicago",
                "as_of_at": "2026-08-11T17:00:00-05:00",
            },
            "group_by": "unknown",
        },
    )
    assert unknown_dimension.status_code == 404
    malformed_bundle = uuid.uuid4()
    malformed_dataset = uuid.uuid4()
    async with platform_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO configuration_bundle "
                "(id,tenant_id,version,status,content_hash,author_id,approver_id,published_at,"
                "change_reason) VALUES (:id,:tenant,2,'published',:hash,:author,:author,now(),"
                "'isolated malformed test fixture')"
            ),
            {
                "id": malformed_bundle,
                "tenant": seeded.tenant_a.id,
                "hash": "f" * 64,
                "author": seeded.user_a.id,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO demo_dataset "
                "(id,tenant_id,bundle_id,code,label,origin,description,as_of_at,reset_version) "
                "VALUES (:id,:tenant,:bundle,'malformed_fixture','Malformed fixture',"
                "'seeded_demo','Isolated test-only malformed configuration',now(),1)"
            ),
            {
                "id": malformed_dataset,
                "tenant": seeded.tenant_a.id,
                "bundle": malformed_bundle,
            },
        )
    assert (await client.get("/v1/dashboards/executive", headers=auth(token))).status_code == 404
    async with platform_sessions() as session, session.begin():
        await session.execute(
            text("SELECT eip_reset_seeded_demo(:tenant_id,:bundle_id)"),
            {"tenant_id": seeded.tenant_a.id, "bundle_id": malformed_bundle},
        )
    assert (await client.get("/v1/dashboards/executive", headers=auth(token))).status_code == 200
    disabled = await client.delete(f"/v1/data-sources/{source['id']}", headers=auth(token))
    assert disabled.status_code == 200
    assert (await client.get("/v1/dashboards/executive", headers=auth(token))).status_code == 404


async def test_persisted_nonadditive_and_missing_fact_state_fails_closed(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    platform_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    token, source = await _seed(client, seeded, platform_engine, platform_sessions)
    migrator = create_async_engine(settings.db_migrator_dsn)
    try:
        async with migrator.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id',:tenant_id,true)"),
                {"tenant_id": str(seeded.tenant_a.id)},
            )
            malformed = await connection.execute(
                text(
                    "UPDATE demo_metadata SET attributes=jsonb_set(attributes,'{additive}',"
                    "'false'::jsonb) WHERE tenant_id=:tenant AND kind='semantic_field' "
                    "AND attributes->>'classification'='measure'"
                ),
                {"tenant": seeded.tenant_a.id},
            )
            assert malformed.rowcount == 1
        assert (
            await client.get("/v1/dashboards/executive", headers=auth(token))
        ).status_code == 404
        async with migrator.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id',:tenant_id,true)"),
                {"tenant_id": str(seeded.tenant_a.id)},
            )
            restored = await connection.execute(
                text(
                    "UPDATE demo_metadata SET attributes=jsonb_set(attributes,'{additive}',"
                    "'true'::jsonb) WHERE tenant_id=:tenant AND kind='semantic_field' "
                    "AND attributes->>'classification'='measure'"
                ),
                {"tenant": seeded.tenant_a.id},
            )
            assert restored.rowcount == 1
            removed_target = await connection.execute(
                text(
                    "DELETE FROM governed_fact WHERE tenant_id=:tenant AND kind='target' "
                    "AND dimension_value_id IS NULL"
                ),
                {"tenant": seeded.tenant_a.id},
            )
            assert removed_target.rowcount == 1
        assert (
            await client.get("/v1/dashboards/executive", headers=auth(token))
        ).status_code == 404
        async with platform_sessions() as session, session.begin():
            await seed_demo(
                session,
                seeded.tenant_a.id,
                seeded.user_a.id,
                uuid.UUID(str(source["id"])),
            )
    finally:
        await migrator.dispose()
