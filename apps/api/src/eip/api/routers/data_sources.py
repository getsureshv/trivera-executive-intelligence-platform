"""Authorized Data Source management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from eip.api.deps import SecretStoreDep, SessionFactoryDep, TenantSession, require
from eip.connectivity.models import DataSource
from eip.connectivity.service import DataSourceService, SourceInput
from eip.governance.audit import AuditAction, record
from eip.platform.context import Capability, TenantContext
from eip.platform.db import tenant_session
from eip.platform.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/v1/data-sources", tags=["data-sources"])
service = DataSourceService()


class CreateSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    connector_type: str = Field(pattern="^postgresql$")
    endpoint: str = Field(min_length=1, max_length=500)
    configuration: dict[str, Any]
    credential: SecretStr = Field(json_schema_extra={"writeOnly": True})


class UpdateSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)
    configuration: dict[str, Any] | None = None
    credential: SecretStr | None = Field(default=None, json_schema_extra={"writeOnly": True})


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: uuid.UUID
    name: str
    connector_type: str
    endpoint: str
    configuration: dict[str, Any]
    connectivity_mode: str
    status: str
    version: int
    credential_configured: bool = True
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None
    credential_destroy_after: datetime | None
    credential_destroyed_at: datetime | None


ReadContext = Annotated[TenantContext, Depends(require(Capability.SOURCE_READ))]
CreateContext = Annotated[TenantContext, Depends(require(Capability.SOURCE_CREATE))]
UpdateContext = Annotated[TenantContext, Depends(require(Capability.SOURCE_UPDATE))]
DeleteContext = Annotated[TenantContext, Depends(require(Capability.SOURCE_DELETE))]


def source_response(row: DataSource) -> SourceResponse:
    return SourceResponse.model_validate(row)


def _set_etag(response: Response, row: DataSource) -> None:
    response.headers["ETag"] = f'"{row.version}"'


async def _record_denial(
    factory: SessionFactoryDep,
    context: TenantContext,
    source_id: uuid.UUID,
) -> None:
    """Persist denial in its own transaction while preserving a uniform 404."""
    async with tenant_session(factory, context) as audit_session:
        await record(
            audit_session,
            context,
            action=AuditAction.SOURCE_ACCESS_DENIED,
            resource_type="data_source",
            resource_id=str(source_id),
            outcome="denied",
            detail={"operation": "resource_access"},
        )


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    payload: CreateSourceRequest,
    session: TenantSession,
    context: CreateContext,
    secrets: SecretStoreDep,
    http_response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SourceResponse:
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise ValidationError("A valid Idempotency-Key header is required.")
    row = await service.create(
        session,
        context,
        secrets,
        SourceInput(payload.name, payload.endpoint, payload.configuration),
        credential=payload.credential.get_secret_value(),
        idempotency_key=key,
    )
    created_ids: set[uuid.UUID] = session.info.get("source_created_ids", set())
    if row.id in created_ids:
        await record(
            session,
            context,
            action=AuditAction.SOURCE_CREATED,
            resource_type="data_source",
            resource_id=str(row.id),
            detail={"changed_fields": ["configuration", "credential", "endpoint", "name"]},
        )
    _set_etag(http_response, row)
    return source_response(row)


@router.get("", response_model=list[SourceResponse])
async def list_sources(session: TenantSession, context: ReadContext) -> list[SourceResponse]:
    return [source_response(row) for row in await service.list(session, context)]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: uuid.UUID,
    session: TenantSession,
    context: ReadContext,
    factory: SessionFactoryDep,
    http_response: Response,
) -> SourceResponse:
    try:
        row = await service.get(session, context, source_id)
    except NotFoundError:
        await _record_denial(factory, context, source_id)
        raise
    _set_etag(http_response, row)
    return source_response(row)


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    payload: UpdateSourceRequest,
    session: TenantSession,
    context: UpdateContext,
    secrets: SecretStoreDep,
    factory: SessionFactoryDep,
    http_response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> SourceResponse:
    try:
        expected = int((if_match or "").strip('"'))
    except ValueError as exc:
        raise ValidationError("A numeric If-Match header is required.") from exc
    try:
        row = await service.update(
            session,
            context,
            secrets,
            source_id,
            expected_version=expected,
            changes=payload.model_dump(exclude={"credential"}, exclude_unset=True),
            credential=payload.credential.get_secret_value() if payload.credential else None,
        )
    except NotFoundError:
        await _record_denial(factory, context, source_id)
        raise
    changed = sorted(payload.model_fields_set)
    if payload.credential is not None:
        await record(
            session,
            context,
            action=AuditAction.SOURCE_CREDENTIAL_ROTATED,
            resource_type="data_source",
            resource_id=str(row.id),
            detail={"changed_fields": ["credential"]},
        )
    if changed:
        await record(
            session,
            context,
            action=AuditAction.SOURCE_UPDATED,
            resource_type="data_source",
            resource_id=str(row.id),
            detail={"changed_fields": changed},
        )
    _set_etag(http_response, row)
    return source_response(row)


@router.delete("/{source_id}", response_model=SourceResponse)
async def delete_source(
    source_id: uuid.UUID,
    session: TenantSession,
    context: DeleteContext,
    factory: SessionFactoryDep,
    http_response: Response,
) -> SourceResponse:
    try:
        row, changed = await service.disable(session, context, source_id)
    except NotFoundError:
        await _record_denial(factory, context, source_id)
        raise
    if changed:
        await record(
            session,
            context,
            action=AuditAction.SOURCE_DELETED,
            resource_type="data_source",
            resource_id=str(row.id),
            detail={
                "changed_fields": ["status", "version", "disabled_at", "credential_destroy_after"]
            },
        )
    _set_etag(http_response, row)
    return source_response(row)
