"""Real-PostgreSQL adversarial checks for tenant-owned Data Sources."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from eip_worker.maintenance import due_tenants, run_tenant_maintenance
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    AsyncSessionTransaction,
    async_sessionmaker,
)

from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from eip.platform.secrets import SecretRef
from eip.platform.secretstore import FileSecretStore
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


async def _create(client: AsyncClient, token: str, *, key: str, credential: str) -> object:
    return await client.post(
        "/v1/data-sources",
        headers={**auth(token), "Idempotency-Key": key},
        json={
            "name": f"Warehouse {key}",
            "connector_type": "postgresql",
            "endpoint": "source-db.invalid:5432",
            "configuration": {
                "username": "reader",
                "database": "warehouse",
                "tls_mode": "require",
            },
            "credential": credential,
        },
    )


async def test_tables_force_rls_policy_and_constrained_grants(
    platform_engine: AsyncEngine,
) -> None:
    async with platform_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,"
                    "(SELECT count(*) FROM pg_policies p WHERE p.tablename=c.relname) "
                    "FROM pg_class c WHERE c.relname IN ('data_source','data_source_acl')"
                )
            )
        ).all()
        assert {row[0] for row in rows} == {"data_source", "data_source_acl"}
        assert all(row[1] and row[2] and row[3] == 1 for row in rows)
        grants = await connection.execute(
            text(
                "SELECT table_name, privilege, "
                "has_table_privilege('eip_app', table_name, privilege) "
                "FROM (VALUES ('data_source'),('data_source_acl')) AS tables(table_name) "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE')) "
                "AS privileges(privilege)"
            )
        )
        assert all(row[2] for row in grants)


async def test_api_cross_tenant_is_uniform_and_sentinel_never_persists(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
) -> None:
    token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)
    sentinel = f"stage1-{uuid.uuid4()}-password"
    created = await _create(client, token_a, key=str(uuid.uuid4()), credential=sentinel)
    assert created.status_code == 201
    body = created.json()
    assert body["credential_configured"] is True
    assert sentinel not in created.text
    source_id = body["id"]

    unauthorized = await client.get(f"/v1/data-sources/{source_id}", headers=auth(token_b))
    absent = await client.get(f"/v1/data-sources/{uuid.uuid4()}", headers=auth(token_b))
    assert unauthorized.status_code == absent.status_code == 404
    for field in ("title", "status", "detail", "code"):
        assert unauthorized.json()[field] == absent.json()[field]

    async with platform_engine.connect() as connection:
        leaked = await connection.scalar(
            text(
                "SELECT count(*) FROM data_source WHERE configuration::text LIKE :needle "
                "OR endpoint LIKE :needle OR secret_name LIKE :needle"
            ),
            {"needle": f"%{sentinel}%"},
        )
        audit_leaked = await connection.scalar(
            text("SELECT count(*) FROM audit_event WHERE detail::text LIKE :needle"),
            {"needle": f"%{sentinel}%"},
        )
        denial_count = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE tenant_id=:tenant_id "
                "AND action='source.access.denied'"
            ),
            {"tenant_id": seeded.tenant_b.id},
        )
        assert leaked == audit_leaked == 0
        assert denial_count == 2


async def test_create_idempotency_and_if_match_conflict(
    client: AsyncClient,
    seeded: Fixtures,
) -> None:
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    key = str(uuid.uuid4())
    first = await _create(client, token, key=key, credential="first-credential")
    retry = await _create(client, token, key=key, credential="ignored-on-retry")
    assert first.status_code == 201
    assert retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert first.headers["etag"] == retry.headers["etag"] == '"1"'
    stale = await client.patch(
        f"/v1/data-sources/{first.json()['id']}",
        headers={**auth(token), "If-Match": "99"},
        json={"name": "Changed"},
    )
    assert stale.status_code == 412


async def test_delete_is_idempotent_and_cross_tenant_uniform(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
) -> None:
    token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)
    created = await _create(
        client, token_a, key=str(uuid.uuid4()), credential="delete-recovery-sentinel"
    )
    source_id = created.json()["id"]
    forbidden = await client.delete(f"/v1/data-sources/{source_id}", headers=auth(token_b))
    absent = await client.delete(f"/v1/data-sources/{uuid.uuid4()}", headers=auth(token_b))
    assert forbidden.status_code == absent.status_code == 404
    for field in ("title", "status", "detail", "code"):
        assert forbidden.json()[field] == absent.json()[field]

    first = await client.delete(f"/v1/data-sources/{source_id}", headers=auth(token_a))
    second = await client.delete(f"/v1/data-sources/{source_id}", headers=auth(token_a))
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "disabled"
    assert first.json()["version"] == second.json()["version"] == 2
    disabled_at = datetime.fromisoformat(first.json()["disabled_at"])
    destroy_after = datetime.fromisoformat(first.json()["credential_destroy_after"])
    assert destroy_after - disabled_at == timedelta(days=30)
    refused = await client.post(
        f"/v1/data-sources/{source_id}/test",
        headers={**auth(token_a), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 409
    async with platform_engine.connect() as connection:
        audit_count = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE tenant_id=:tenant_id "
                "AND resource_id=:source_id AND action='source.deleted'"
            ),
            {"tenant_id": seeded.tenant_a.id, "source_id": source_id},
        )
        assert audit_count == 1


def _secret_files(store: FileSecretStore) -> set[Path]:
    return set(store._root.rglob("*.json"))


async def test_create_and_rotation_rollback_remove_only_the_new_secret(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database/audit failure cannot orphan a secret or destroy the prior one."""
    app: Any = client._transport.app
    store = app.state.secret_store
    assert isinstance(store, FileSecretStore)
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    before_create = _secret_files(store)

    async def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fault-injected audit failure")

    monkeypatch.setattr("eip.api.routers.data_sources.record", fail_audit)
    with pytest.raises(RuntimeError, match="fault-injected audit failure"):
        await _create(
            client,
            token,
            key=str(uuid.uuid4()),
            credential="rollback-create-sentinel",
        )
    assert _secret_files(store) == before_create

    monkeypatch.undo()
    created = await _create(
        client,
        token,
        key=str(uuid.uuid4()),
        credential="prior-usable-sentinel",
    )
    assert created.status_code == 201
    source_id = uuid.UUID(created.json()["id"])
    async with platform_engine.connect() as connection:
        old_name, old_version = (
            await connection.execute(
                text("SELECT secret_name,secret_version FROM data_source WHERE id=:source_id"),
                {"source_id": source_id},
            )
        ).one()
    old_ref = SecretRef(seeded.tenant_a.id, old_name, old_version)
    before_rotation = _secret_files(store)

    monkeypatch.setattr("eip.api.routers.data_sources.record", fail_audit)
    with pytest.raises(RuntimeError, match="fault-injected audit failure"):
        await client.patch(
            f"/v1/data-sources/{source_id}",
            headers={**auth(token), "If-Match": '"1"'},
            json={"credential": "rollback-rotation-sentinel"},
        )
    assert _secret_files(store) == before_rotation
    assert (await store.get(old_ref, purpose="verify rollback")).reveal() == "prior-usable-sentinel"
    async with platform_engine.connect() as connection:
        persisted = (
            await connection.execute(
                text(
                    "SELECT secret_name,secret_version,version FROM data_source WHERE id=:source_id"
                ),
                {"source_id": source_id},
            )
        ).one()
    assert tuple(persisted) == (old_name, old_version, 1)


async def test_retention_maintenance_boundaries_are_tenant_safe_and_idempotent(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Exact retention boundaries preserve rows; only older/due state is removed."""
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    created = await _create(
        client, token, key=str(uuid.uuid4()), credential="maintenance-secret-sentinel"
    )
    source_id = uuid.UUID(created.json()["id"])
    disabled = await client.delete(f"/v1/data-sources/{source_id}", headers=auth(token))
    assert disabled.status_code == 200
    deadline = datetime.fromisoformat(disabled.json()["credential_destroy_after"])
    app: Any = client._transport.app
    store = app.state.secret_store
    assert isinstance(store, FileSecretStore)
    async with platform_engine.connect() as connection:
        secret_name, secret_version = (
            await connection.execute(
                text("SELECT secret_name,secret_version FROM data_source WHERE id=:id"),
                {"id": source_id},
            )
        ).one()
    ref = SecretRef(seeded.tenant_a.id, secret_name, secret_version)

    assert await due_tenants(app_sessions, now=deadline - timedelta(microseconds=1)) == []
    assert await run_tenant_maintenance(
        app_sessions, store, seeded.tenant_a.id, now=deadline - timedelta(microseconds=1)
    ) == (0, 0)
    assert (await store.get(ref, purpose="retention boundary")).reveal()
    assert seeded.tenant_a.id in await due_tenants(app_sessions, now=deadline)
    assert await run_tenant_maintenance(app_sessions, store, seeded.tenant_a.id, now=deadline) == (
        1,
        0,
    )
    assert await run_tenant_maintenance(
        app_sessions, store, seeded.tenant_a.id, now=deadline + timedelta(seconds=1)
    ) == (0, 0)

    now = datetime.now(UTC)
    test_ids = [uuid.uuid4() for _ in range(4)]
    async with platform_engine.begin() as connection:
        for test_id, status, queued_at in (
            (test_ids[0], "succeeded", now - timedelta(days=90)),
            (test_ids[1], "failed", now - timedelta(days=90, microseconds=1)),
            (test_ids[2], "queued", now - timedelta(days=91)),
            (test_ids[3], "succeeded", now - timedelta(days=91)),
        ):
            await connection.execute(
                text(
                    "INSERT INTO connection_test "
                    "(id,tenant_id,data_source_id,source_version,requested_by,status,checks,"
                    "overall_code,attempt,trace_id,idempotency_key,queued_at,completed_at) "
                    "VALUES (:id,:tenant,:source,2,:user,:status,'[]'::jsonb,NULL,1,:trace,:key,"
                    ":queued,:completed)"
                ),
                {
                    "id": test_id,
                    "tenant": seeded.tenant_a.id,
                    "source": source_id,
                    "user": seeded.user_a.id,
                    "status": status,
                    "trace": f"retention-{test_id}",
                    "key": str(test_id),
                    "queued": queued_at,
                    "completed": (
                        queued_at if status in {"succeeded", "failed", "stale"} else None
                    ),
                },
            )
    assert await run_tenant_maintenance(app_sessions, store, seeded.tenant_a.id, now=now) == (0, 2)
    async with platform_engine.connect() as connection:
        remaining = set(
            await connection.scalars(
                text("SELECT id FROM connection_test WHERE id = ANY(:ids)"), {"ids": test_ids}
            )
        )
        destroyed_audits = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE tenant_id=:tenant "
                "AND resource_id=:source AND action='source.credential_destroyed'"
            ),
            {"tenant": seeded.tenant_a.id, "source": str(source_id)},
        )
    assert remaining == {test_ids[0], test_ids[2]}
    assert destroyed_audits == 1
    latest = await client.get(
        f"/v1/data-sources/{source_id}/connection-tests/latest", headers=auth(token)
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == str(test_ids[0])
    async with platform_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM connection_test WHERE id=:id"), {"id": test_ids[2]}
        )
    assert await run_tenant_maintenance(
        app_sessions, store, seeded.tenant_a.id, now=now + timedelta(microseconds=1)
    ) == (0, 1)
    absent = await client.get(
        f"/v1/data-sources/{source_id}/connection-tests/latest", headers=auth(token)
    )
    assert absent.status_code == 404
    async with platform_engine.connect() as connection:
        audit_rows = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE tenant_id=:tenant "
                "AND action IN ('source.credential_destroyed','connection_test.pruned')"
            ),
            {"tenant": seeded.tenant_a.id},
        )
    assert audit_rows == 3


async def test_maintenance_enumerator_is_identifier_only_and_rls_blocks_cross_tenant(
    app_sessions: async_sessionmaker[AsyncSession],
    platform_engine: AsyncEngine,
    seeded: Fixtures,
) -> None:
    async with app_sessions() as session, session.begin():
        result_type = await session.scalar(
            text(
                "SELECT pg_get_function_result(oid) FROM pg_proc "
                "WHERE proname='eip_maintenance_due_tenants'"
            )
        )
    assert result_type == "TABLE(tenant_id uuid)"
    context = TenantContext(
        tenant_id=seeded.tenant_a.id,
        tenant_slug="tenant-a",
        principal=Principal(
            uuid.UUID("00000000-0000-0000-0000-000000000002"),
            "system:maintenance-test",
            "system@trivera.invalid",
            ActorType.SYSTEM,
        ),
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id="maintenance-rls",
        request_id="maintenance-rls",
    )
    async with tenant_session(app_sessions, context) as session:
        result = await session.execute(
            text("UPDATE data_source SET status='disabled' WHERE tenant_id=:other"),
            {"other": seeded.tenant_b.id},
        )
        assert result.rowcount == 0


async def test_credential_delete_retries_after_transaction_commit_failure(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    app_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    created = await _create(
        client, token, key=str(uuid.uuid4()), credential="commit-failure-sentinel"
    )
    source_id = uuid.UUID(created.json()["id"])
    disabled = await client.delete(f"/v1/data-sources/{source_id}", headers=auth(token))
    deadline = datetime.fromisoformat(disabled.json()["credential_destroy_after"])
    app: Any = client._transport.app
    store = app.state.secret_store
    assert isinstance(store, FileSecretStore)

    original_exit = AsyncSessionTransaction.__aexit__
    fail_once = True

    async def fail_first_commit(
        transaction: AsyncSessionTransaction,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool | None:
        nonlocal fail_once
        if fail_once and exc_type is None:
            fail_once = False
            await transaction.rollback()
            raise RuntimeError("injected transaction commit failure")
        return await original_exit(transaction, exc_type, exc, traceback)  # type: ignore[arg-type]

    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", fail_first_commit)
    with pytest.raises(RuntimeError, match="injected transaction commit failure"):
        await run_tenant_maintenance(app_sessions, store, seeded.tenant_a.id, now=deadline)
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", original_exit)

    async with platform_engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT credential_destroyed_at FROM data_source WHERE id=:id"),
                {"id": source_id},
            )
        ).scalar_one_or_none()
    assert state is None
    assert await run_tenant_maintenance(app_sessions, store, seeded.tenant_a.id, now=deadline) == (
        1,
        0,
    )
    assert await run_tenant_maintenance(app_sessions, store, seeded.tenant_a.id, now=deadline) == (
        0,
        0,
    )
    async with platform_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT credential_destroyed_at, "
                    "(SELECT count(*) FROM audit_event WHERE tenant_id=:tenant "
                    "AND resource_id=:source "
                    "AND action='source.credential_destroyed') AS audit_count "
                    "FROM data_source WHERE id=:id"
                ),
                {"id": source_id, "tenant": seeded.tenant_a.id, "source": str(source_id)},
            )
        ).one()
    assert row.credential_destroyed_at == deadline
    assert row.audit_count == 1
