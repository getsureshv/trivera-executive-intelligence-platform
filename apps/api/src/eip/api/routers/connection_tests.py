"""Asynchronous connection-test request and polling endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict

from eip.api.deps import TenantSession, require
from eip.connectivity.connection_testing import ConnectionTestService
from eip.connectivity.models import ConnectionTest
from eip.governance.audit import AuditAction, record
from eip.governance.outbox import Topic, publish
from eip.platform.context import Capability, TenantContext
from eip.platform.errors import ValidationError

router = APIRouter(prefix="/v1", tags=["connection-tests"])
service = ConnectionTestService()
TestContext = Annotated[TenantContext, Depends(require(Capability.SOURCE_TEST))]


class ConnectionTestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: uuid.UUID
    data_source_id: uuid.UUID
    source_version: int
    status: Literal["queued", "running", "succeeded", "failed", "stale"]
    checks: list[DiagnosticResponse]
    overall_code: str | None
    attempt: int
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    poll_url: str


class DiagnosticResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["network", "tls", "authentication", "authorization", "metadata_access", "latency"]
    status: Literal["pass", "fail", "skipped"]
    code: str
    message: str
    remediation_hint: str | None = None
    duration_ms: int


def _response(job: ConnectionTest) -> ConnectionTestResponse:
    return ConnectionTestResponse(
        id=job.id,
        data_source_id=job.data_source_id,
        source_version=job.source_version,
        status=cast(Literal["queued", "running", "succeeded", "failed", "stale"], job.status),
        checks=[DiagnosticResponse.model_validate(check) for check in job.checks],
        overall_code=job.overall_code,
        attempt=job.attempt,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        poll_url=f"/v1/connection-tests/{job.id}",
    )


@router.post(
    "/data-sources/{source_id}/test", response_model=ConnectionTestResponse, status_code=202
)
async def request_test(
    source_id: uuid.UUID,
    session: TenantSession,
    context: TestContext,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ConnectionTestResponse:
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise ValidationError("A valid Idempotency-Key header is required.")
    job, created = await service.request(session, context, source_id, idempotency_key=key)
    if created:
        envelope = {
            "job_id": str(job.id),
            "tenant_id": str(context.tenant_id),
            "source_id": str(source_id),
            "source_version": job.source_version,
            "actor_id": str(context.principal.user_id),
            "trace_id": context.trace_id,
            "idempotency_key": key,
            "attempt": job.attempt,
        }
        await publish(
            session,
            tenant_id=context.tenant_id,
            topic=Topic.CONNECTION_TEST_REQUESTED,
            payload=envelope,
            trace_id=context.trace_id,
        )
        await record(
            session,
            context,
            action=AuditAction.CONNECTION_TEST_REQUESTED,
            resource_type="connection_test",
            resource_id=str(job.id),
            detail={"source_id": str(source_id), "status": "queued"},
        )
    return _response(job)


@router.get("/connection-tests/{job_id}", response_model=ConnectionTestResponse)
async def get_test(
    job_id: uuid.UUID, session: TenantSession, context: TestContext
) -> ConnectionTestResponse:
    return _response(await service.get(session, context, job_id))


@router.get(
    "/data-sources/{source_id}/connection-tests/latest",
    response_model=ConnectionTestResponse,
)
async def get_latest_test(
    source_id: uuid.UUID, session: TenantSession, context: TestContext
) -> ConnectionTestResponse:
    return _response(await service.latest(session, context, source_id))
