"""Real-PostgreSQL adversarial checks for tenant-owned Data Sources."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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
