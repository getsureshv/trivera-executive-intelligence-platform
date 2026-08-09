"""Fenced execution of PostgreSQL connection-test jobs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.connectivity.egress import EgressValidator
from eip.connectivity.models import ConnectionTest, DataSource
from eip.connectivity.postgresql import PostgreSQLConfig, PostgreSQLConnector, SystemResolver
from eip.connectivity.protocol import ConnectionTarget
from eip.governance import audit
from eip.governance.outbox import Topic, publish
from eip.platform.context import ActorType, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from eip.platform.secrets import SecretStore
from eip.platform.settings import Settings


@dataclass(frozen=True, slots=True)
class ConnectionTestEnvelope:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    source_version: int
    actor_id: uuid.UUID
    trace_id: str
    idempotency_key: str
    attempt: int

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> ConnectionTestEnvelope:
        required = {
            "job_id",
            "tenant_id",
            "source_id",
            "source_version",
            "actor_id",
            "trace_id",
            "idempotency_key",
            "attempt",
        }
        if (
            set(payload) != required
            or not payload.get("trace_id")
            or not payload.get("idempotency_key")
        ):
            raise ValueError("Malformed connection-test envelope")
        try:
            envelope = cls(
                job_id=uuid.UUID(str(payload["job_id"])),
                tenant_id=uuid.UUID(str(payload["tenant_id"])),
                source_id=uuid.UUID(str(payload["source_id"])),
                source_version=int(payload["source_version"]),
                actor_id=uuid.UUID(str(payload["actor_id"])),
                trace_id=str(payload["trace_id"]),
                idempotency_key=str(payload["idempotency_key"]),
                attempt=int(payload["attempt"]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Malformed connection-test envelope") from exc
        if envelope.source_version < 1 or envelope.attempt < 1:
            raise ValueError("Malformed connection-test envelope")
        return envelope


def _context(envelope: ConnectionTestEnvelope) -> TenantContext:
    return TenantContext(
        tenant_id=envelope.tenant_id,
        tenant_slug="",
        principal=Principal(envelope.actor_id, "worker:connection-test", "", ActorType.SERVICE),
        role=RoleCode.VIEWER,
        capabilities=frozenset(),
        trace_id=envelope.trace_id,
        request_id=envelope.trace_id,
    )


async def execute_connection_test(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    secrets: SecretStore,
    payload: dict[str, Any],
    *,
    before_execution_fence: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """Execute one delivery; fence before credential or network access."""
    envelope = ConnectionTestEnvelope.parse(payload)
    context = _context(envelope)
    async with tenant_session(factory, context) as session:
        job = await session.scalar(
            select(ConnectionTest).where(ConnectionTest.id == envelope.job_id).with_for_update()
        )
        source = await session.scalar(select(DataSource).where(DataSource.id == envelope.source_id))
        if (
            job is None
            or source is None
            or job.tenant_id != envelope.tenant_id
            or source.tenant_id != envelope.tenant_id
        ):
            await audit.record(
                session,
                context,
                action=audit.AuditAction.CONNECTION_TEST_DENIED,
                resource_type="connection_test",
                resource_id=str(envelope.job_id),
                outcome="denied",
                detail={"source_id": str(envelope.source_id), "status": "denied"},
            )
            return "denied"
        if job.status in {"succeeded", "failed", "stale"}:
            return job.status
        now = datetime.now(UTC)
        if job.status == "running":
            if job.lease_expires_at is not None and job.lease_expires_at > now:
                return "deferred"
            job.status = "queued"
            job.started_at = None
            job.lease_expires_at = None
        if (
            job.data_source_id != source.id
            or job.source_version != envelope.source_version
            or source.version != envelope.source_version
            or job.attempt != envelope.attempt
            or job.idempotency_key != envelope.idempotency_key
        ):
            job.status = "stale"
            job.completed_at = datetime.now(UTC)
            return "stale"
        # Serialize only this tenant's claim decision. The lock is released at
        # commit, before network I/O, and different tenants use different keys.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :tenant_key)"),
            {"namespace": 0x434F4E4E, "tenant_key": envelope.tenant_id.int % (2**31)},
        )
        running = await session.scalar(
            select(func.count())
            .select_from(ConnectionTest)
            .where(ConnectionTest.status == "running")
        )
        if int(running or 0) >= settings.connection_test_tenant_concurrency:
            return "deferred"
        job.status = "running"
        job.started_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.connection_test_lease_seconds)

    if before_execution_fence is not None:
        await before_execution_fence()

    # The claim transaction intentionally commits before network I/O. Re-read
    # immediately before constructing the connector so a source rotation that
    # happened after claim still fences this delivery before secret/network use.
    async with tenant_session(factory, context) as session:
        job = await session.scalar(
            select(ConnectionTest).where(ConnectionTest.id == envelope.job_id).with_for_update()
        )
        source = await session.scalar(select(DataSource).where(DataSource.id == envelope.source_id))
        if (
            job is None
            or source is None
            or job.status != "running"
            or job.attempt != envelope.attempt
            or job.source_version != envelope.source_version
            or source.version != envelope.source_version
        ):
            if job is not None and job.status == "running" and job.attempt == envelope.attempt:
                job.status = "stale"
                job.completed_at = datetime.now(UTC)
                job.lease_expires_at = None
            return "stale"

    result = None
    safe_exception_code: str | None = None
    try:
        target = ConnectionTarget(
            connector_type="postgresql",
            endpoint=source.endpoint,
            secret_ref=f"tenants/{envelope.tenant_id}/{source.secret_name}/{source.secret_version}",
            connectivity_mode="direct",
        )
        connector = PostgreSQLConnector(
            target,
            PostgreSQLConfig(
                username=str(source.configuration["username"]),
                database=str(source.configuration["database"]),
                tls_mode=str(source.configuration.get("tls_mode", "disable")),
                connect_timeout_seconds=float(
                    source.configuration.get("connect_timeout_seconds", 3.0)
                ),
            ),
            secrets,
            EgressValidator(SystemResolver(), allowlist=settings.connector_egress_allowlist_list),
        )
        result = await connector.test_connection()
    except Exception:
        safe_exception_code = "CONNECTION_TEST_INTERNAL_FAILURE"

    async with tenant_session(factory, context) as session:
        job = await session.scalar(
            select(ConnectionTest).where(ConnectionTest.id == envelope.job_id).with_for_update()
        )
        source_now = await session.scalar(
            select(DataSource).where(DataSource.id == envelope.source_id)
        )
        if job is None:
            return "stale"
        if (
            source_now is None
            or job.status != "running"
            or job.attempt != envelope.attempt
            or job.source_version != envelope.source_version
            or source_now.version != envelope.source_version
        ):
            if job.attempt == envelope.attempt and job.status == "running":
                job.status = "stale"
                job.completed_at = datetime.now(UTC)
                job.lease_expires_at = None
            return "stale"
        if result is None:
            job.checks = []
            job.status = "failed"
            job.overall_code = safe_exception_code
            job.completed_at = datetime.now(UTC)
            job.lease_expires_at = None
            await audit.record(
                session,
                context,
                action=audit.AuditAction.CONNECTION_TEST_FAILED,
                resource_type="connection_test",
                resource_id=str(job.id),
                outcome="failure",
                detail={
                    "source_id": str(source_now.id),
                    "status": "failed",
                    "code": safe_exception_code,
                },
            )
            await publish(
                session,
                tenant_id=envelope.tenant_id,
                topic=Topic.CONNECTION_TEST_FAILED,
                payload={
                    "job_id": str(job.id),
                    "source_id": str(source_now.id),
                    "source_version": envelope.source_version,
                    "status": "failed",
                    "code": safe_exception_code,
                    "attempt": envelope.attempt,
                },
                trace_id=envelope.trace_id,
            )
            return "failed"
        job.checks = [check.to_dict() for check in result.checks]
        job.status = "succeeded" if result.success else "failed"
        job.overall_code = (
            "CONNECTION_OK"
            if result.success
            else next(check.code for check in result.checks if check.status == "fail")
        )
        job.completed_at = datetime.now(UTC)
        job.lease_expires_at = None
        await audit.record(
            session,
            context,
            action=audit.AuditAction.CONNECTION_TEST_COMPLETED
            if result.success
            else audit.AuditAction.CONNECTION_TEST_FAILED,
            resource_type="connection_test",
            resource_id=str(job.id),
            outcome="success" if result.success else "failure",
            detail={
                "source_id": str(source_now.id),
                "status": job.status,
                "code": job.overall_code,
            },
        )
        await publish(
            session,
            tenant_id=envelope.tenant_id,
            topic=(
                Topic.CONNECTION_TEST_COMPLETED if result.success else Topic.CONNECTION_TEST_FAILED
            ),
            payload={
                "job_id": str(job.id),
                "source_id": str(source_now.id),
                "source_version": envelope.source_version,
                "status": job.status,
                "code": job.overall_code,
                "attempt": envelope.attempt,
            },
            trace_id=envelope.trace_id,
        )
        return str(job.status)
