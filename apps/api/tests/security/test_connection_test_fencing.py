"""Live-PostgreSQL fencing proof for superseded connection-test attempts."""

from __future__ import annotations

import asyncio
import socket
import uuid
from typing import Any

import pytest
from eip_worker.connection_tests import execute_connection_test
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.secrets import SecretRef, SecretStore, SecretValue
from eip.platform.settings import Settings
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


class RecordingSecretStore:
    def __init__(self, inner: SecretStore) -> None:
        self.inner = inner
        self.reads: list[SecretRef] = []

    async def put(self, tenant_id: uuid.UUID, logical_name: str, value: SecretValue) -> SecretRef:
        return await self.inner.put(tenant_id, logical_name, value)

    async def get(self, ref: SecretRef, *, purpose: str) -> SecretValue:
        self.reads.append(ref)
        return await self.inner.get(ref, purpose=purpose)

    async def rotate(self, ref: SecretRef, value: SecretValue) -> SecretRef:
        return await self.inner.rotate(ref, value)

    async def delete(self, ref: SecretRef) -> None:
        await self.inner.delete(ref)


def _payload(row: Any, actor_id: uuid.UUID) -> dict[str, object]:
    return {
        "job_id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "source_id": str(row.data_source_id),
        "source_version": row.source_version,
        "actor_id": str(actor_id),
        "trace_id": row.trace_id,
        "idempotency_key": row.idempotency_key,
        "attempt": row.attempt,
    }


async def test_superseded_attempt_cannot_read_old_secret_or_overwrite_new_result(
    client: AsyncClient,
    seeded: Fixtures,
    platform_engine: AsyncEngine,
    app_sessions: async_sessionmaker[AsyncSession],
    platform_sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """A pauses before side effects; B rotates and wins; A is permanently fenced."""
    database_url = make_url(settings.db_app_dsn)
    database_host = database_url.host
    assert database_host is not None
    database_port = database_url.port or 5432
    token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
    created = await client.post(
        "/v1/data-sources",
        headers={**auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={
            "name": "Fencing PostgreSQL",
            "connector_type": "postgresql",
            "endpoint": f"{database_host}:{database_port}",
            "configuration": {"username": "postgres", "database": "eip", "tls_mode": "disable"},
            "credential": "local_dev_only",
        },
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    request_a = await client.post(
        f"/v1/data-sources/{source_id}/test",
        headers={**auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert request_a.status_code == 202
    token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)
    foreign = await client.get(
        f"/v1/connection-tests/{request_a.json()['id']}", headers=auth(token_b)
    )
    absent = await client.get(f"/v1/connection-tests/{uuid.uuid4()}", headers=auth(token_b))
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["code"] == absent.json()["code"]

    from eip.connectivity.models import ConnectionTest

    async with platform_sessions() as session:
        row_a = await session.get(ConnectionTest, uuid.UUID(request_a.json()["id"]))
        assert row_a is not None
        payload_a = _payload(row_a, seeded.user_a.id)
    async with platform_engine.connect() as connection:
        old_secret_name = await connection.scalar(
            text("SELECT secret_name FROM data_source WHERE id=:id"),
            {"id": uuid.UUID(source_id)},
        )

    app: Any = client._transport.app
    secrets = RecordingSecretStore(app.state.secret_store)
    reached_pause, resume_a = asyncio.Event(), asyncio.Event()

    async def pause_a() -> None:
        reached_pause.set()
        await resume_a.wait()

    pg_address = socket.gethostbyname(database_host)
    live_settings = settings.model_copy(update={"connector_egress_allowlist": f"{pg_address}/32"})
    task_a = asyncio.create_task(
        execute_connection_test(
            app_sessions, live_settings, secrets, payload_a, before_execution_fence=pause_a
        )
    )
    await asyncio.wait_for(reached_pause.wait(), timeout=5)

    rotated = await client.patch(
        f"/v1/data-sources/{source_id}",
        headers={**auth(token), "If-Match": created.headers["etag"]},
        json={"credential": "local_dev_only"},
    )
    assert rotated.status_code == 200
    request_b = await client.post(
        f"/v1/data-sources/{source_id}/test",
        headers={**auth(token), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert request_b.status_code == 202
    async with platform_sessions() as session:
        row_b = await session.get(ConnectionTest, uuid.UUID(request_b.json()["id"]))
        assert row_b is not None
        payload_b = _payload(row_b, seeded.user_a.id)

    assert (
        await execute_connection_test(app_sessions, live_settings, secrets, payload_b)
        == "succeeded"
    )
    resume_a.set()
    assert await asyncio.wait_for(task_a, timeout=5) == "stale"

    async with platform_engine.connect() as connection:
        final = (
            await connection.execute(
                text(
                    "SELECT source_version,status,overall_code FROM connection_test "
                    "WHERE data_source_id=:source_id ORDER BY source_version"
                ),
                {"source_id": uuid.UUID(source_id)},
            )
        ).all()
        new_secret_name = await connection.scalar(
            text("SELECT secret_name FROM data_source WHERE id=:id"), {"id": uuid.UUID(source_id)}
        )
        terminal_audit = (
            await connection.execute(
                text(
                    "SELECT resource_id,action FROM audit_event "
                    "WHERE resource_type='connection_test' AND action IN "
                    "('connection_test.completed','connection_test.failed')"
                )
            )
        ).all()
        terminal_outbox = (
            await connection.execute(
                text(
                    "SELECT payload->>'job_id',topic FROM outbox WHERE topic IN "
                    "('connection_test.completed','connection_test.failed')"
                )
            )
        ).all()

    assert [tuple(row) for row in final] == [(1, "stale", None), (2, "succeeded", "CONNECTION_OK")]
    b_id = request_b.json()["id"]
    assert terminal_audit == [(b_id, "connection_test.completed")]
    assert terminal_outbox == [(b_id, "connection_test.completed")]
    assert old_secret_name != new_secret_name
    assert [ref.logical_name for ref in secrets.reads] == [new_secret_name]
