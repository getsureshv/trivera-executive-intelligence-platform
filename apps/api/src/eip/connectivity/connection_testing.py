"""Durable connection-test request and polling service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eip.connectivity.models import ConnectionTest
from eip.connectivity.service import DataSourceService
from eip.platform.context import Capability, TenantContext
from eip.platform.errors import ConflictError, NotFoundError


class ConnectionTestService:
    def __init__(self) -> None:
        self._sources = DataSourceService()

    async def request(
        self,
        session: AsyncSession,
        context: TenantContext,
        source_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> tuple[ConnectionTest, bool]:
        context.require(Capability.SOURCE_TEST)
        source = await self._sources.get(session, context, source_id)
        if source.status != "active":
            raise ConflictError("Disabled data sources cannot be tested.")
        existing = await session.scalar(
            select(ConnectionTest).where(
                ConnectionTest.tenant_id == context.tenant_id,
                ConnectionTest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.data_source_id != source_id:
                raise ConflictError("Idempotency key was already used for another request.")
            return existing, False
        job = ConnectionTest(
            tenant_id=context.tenant_id,
            data_source_id=source.id,
            source_version=source.version,
            requested_by=context.principal.user_id,
            status="queued",
            checks=[],
            attempt=1,
            trace_id=context.trace_id,
            idempotency_key=idempotency_key,
        )
        session.add(job)
        await session.flush()
        return job, True

    async def get(
        self, session: AsyncSession, context: TenantContext, job_id: uuid.UUID
    ) -> ConnectionTest:
        context.require(Capability.SOURCE_TEST)
        job = await session.scalar(
            select(ConnectionTest).where(
                ConnectionTest.tenant_id == context.tenant_id, ConnectionTest.id == job_id
            )
        )
        if job is None:
            raise NotFoundError()
        await self._sources.get(session, context, job.data_source_id)
        return job

    async def latest(
        self, session: AsyncSession, context: TenantContext, source_id: uuid.UUID
    ) -> ConnectionTest:
        context.require(Capability.SOURCE_TEST)
        await self._sources.get(session, context, source_id)
        job = await session.scalar(
            select(ConnectionTest)
            .where(ConnectionTest.data_source_id == source_id)
            .order_by(ConnectionTest.queued_at.desc(), ConnectionTest.id.desc())
            .limit(1)
        )
        if job is None:
            raise NotFoundError()
        return job
