"""Authorized tenant-scoped Data Source management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from eip.connectivity.models import DataSource, DataSourceAcl
from eip.connectivity.postgresql import PostgreSQLConfig
from eip.platform.context import Capability, RoleCode, TenantContext
from eip.platform.errors import (
    ConflictError,
    NotFoundError,
    PreconditionFailedError,
    ValidationError,
)
from eip.platform.secrets import SecretStore, SecretValue


@dataclass(frozen=True, slots=True)
class SourceInput:
    name: str
    endpoint: str
    configuration: dict[str, Any]


def validate_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise ValidationError("Data source name is invalid.")
    return normalized


def validate_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"password", "credential", "secret", "token", "dsn", "uri", "url"}

    def contains_secret(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                any(word in str(key).lower() for word in forbidden) or contains_secret(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_secret(item) for item in value)
        if isinstance(value, str):
            lowered = value.lower()
            return "://" in lowered or "password=" in lowered or "token=" in lowered
        return False

    if contains_secret(configuration):
        raise ValidationError("Configuration contains a credential-like field.")
    allowed = {"username", "database", "tls_mode", "connect_timeout_seconds"}
    if set(configuration) - allowed:
        raise ValidationError("PostgreSQL configuration contains unsupported fields.")
    try:
        validated = PostgreSQLConfig(**configuration)
    except (TypeError, ValueError) as exc:
        raise ValidationError("PostgreSQL configuration is invalid.") from exc
    return {
        "username": validated.username,
        "database": validated.database,
        "tls_mode": validated.tls_mode,
        "connect_timeout_seconds": validated.connect_timeout_seconds,
    }


def validate_endpoint(endpoint: str) -> str:
    normalized = endpoint.strip()
    if (
        not normalized
        or "://" in normalized
        or "@" in normalized
        or "/" in normalized
        or any(char.isspace() for char in normalized)
    ):
        raise ValidationError("Endpoint must be a host name or IP address with optional port.")
    return normalized


def _can_manage_all(context: TenantContext) -> bool:
    return context.role is RoleCode.TENANT_ADMIN


def _visible(context: TenantContext, *, manage: bool = False) -> Any:
    if _can_manage_all(context):
        return DataSource.tenant_id == context.tenant_id
    access = ("edit", "manage") if manage else ("view", "edit", "manage")
    return and_(
        DataSource.tenant_id == context.tenant_id,
        DataSourceAcl.principal_id == context.principal.user_id,
        DataSourceAcl.access.in_(access),
    )


class DataSourceService:
    async def create(
        self,
        session: AsyncSession,
        context: TenantContext,
        secret_store: SecretStore,
        source: SourceInput,
        *,
        credential: str,
        idempotency_key: str,
    ) -> DataSource:
        context.require(Capability.SOURCE_CREATE)
        configuration = validate_configuration(source.configuration)
        endpoint = validate_endpoint(source.endpoint)
        name = validate_name(source.name)
        existing = await session.scalar(
            select(DataSource).where(
                DataSource.tenant_id == context.tenant_id,
                DataSource.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (existing.name, existing.endpoint, existing.configuration) != (
                name,
                endpoint,
                configuration,
            ):
                raise ConflictError("Idempotency key was already used for another request.")
            return existing

        source_id = uuid.uuid4()
        logical_name = f"data-source-{source_id}"
        ref = await secret_store.put(context.tenant_id, logical_name, SecretValue(credential))
        session.info.setdefault("secret_compensations", []).append((secret_store, ref))
        row = DataSource(
            id=source_id,
            tenant_id=context.tenant_id,
            name=name,
            connector_type="postgresql",
            endpoint=endpoint,
            configuration=configuration,
            secret_name=ref.logical_name,
            secret_version=ref.version,
            connectivity_mode="direct",
            status="active",
            version=1,
            idempotency_key=idempotency_key,
            created_by=context.principal.user_id,
        )
        session.add(row)
        session.add(
            DataSourceAcl(
                tenant_id=context.tenant_id,
                data_source_id=source_id,
                principal_id=context.principal.user_id,
                access="manage",
            )
        )
        await session.flush()
        session.info.setdefault("source_created_ids", set()).add(source_id)
        return row

    async def list(self, session: AsyncSession, context: TenantContext) -> list[DataSource]:
        context.require(Capability.SOURCE_READ)
        statement = select(DataSource)
        if not _can_manage_all(context):
            statement = statement.join(DataSourceAcl).where(_visible(context))
        else:
            statement = statement.where(_visible(context))
        return list(
            (await session.scalars(statement.order_by(DataSource.created_at, DataSource.id))).all()
        )

    async def get(
        self,
        session: AsyncSession,
        context: TenantContext,
        source_id: uuid.UUID,
        *,
        manage: bool = False,
    ) -> DataSource:
        context.require(Capability.SOURCE_UPDATE if manage else Capability.SOURCE_READ)
        statement = select(DataSource)
        if not _can_manage_all(context):
            statement = statement.join(DataSourceAcl).where(_visible(context, manage=manage))
        else:
            statement = statement.where(_visible(context, manage=manage))
        row = await session.scalar(statement.where(DataSource.id == source_id))
        if row is None:
            raise NotFoundError()
        return row

    async def update(
        self,
        session: AsyncSession,
        context: TenantContext,
        secret_store: SecretStore,
        source_id: uuid.UUID,
        *,
        expected_version: int,
        changes: dict[str, Any],
        credential: str | None,
    ) -> DataSource:
        context.require(Capability.SOURCE_UPDATE)
        row = await self.get(session, context, source_id, manage=True)
        if row.version != expected_version:
            raise PreconditionFailedError("The data source was changed by another request.")
        changed_fields: list[str] = []
        for field in ("name", "endpoint"):
            if field in changes and changes[field] is not None:
                value = (
                    validate_endpoint(changes[field])
                    if field == "endpoint"
                    else validate_name(changes[field])
                )
                setattr(row, field, value)
                changed_fields.append(field)
        if changes.get("configuration") is not None:
            row.configuration = validate_configuration(changes["configuration"])
            changed_fields.append("configuration")
        if credential is not None:
            logical_name = f"data-source-{row.id}-rotation-{uuid.uuid4()}"
            rotated = await secret_store.put(
                context.tenant_id, logical_name, SecretValue(credential)
            )
            session.info.setdefault("secret_compensations", []).append((secret_store, rotated))
            row.secret_name, row.secret_version = rotated.logical_name, rotated.version
            changed_fields.append("credential")
        if not changed_fields:
            return row
        row.version += 1
        await session.flush()
        # SQLAlchemy expires server-generated on-update columns after flush.
        # Refresh before the router serializes the row; implicit async IO from
        # Pydantic attribute access would otherwise raise MissingGreenlet.
        await session.refresh(row, attribute_names=["updated_at"])
        return row

    async def grant(
        self,
        session: AsyncSession,
        context: TenantContext,
        source_id: uuid.UUID,
        principal_id: uuid.UUID,
        *,
        access: str,
    ) -> DataSourceAcl:
        context.require(Capability.SOURCE_ACL_MANAGE)
        if access not in {"view", "edit", "manage"}:
            raise ValidationError("ACL access must be view, edit, or manage.")
        await self.get(session, context, source_id, manage=True)
        membership_exists = await session.scalar(
            text(
                "SELECT 1 FROM membership "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND status='active'"
            ),
            {"tenant_id": context.tenant_id, "user_id": principal_id},
        )
        if membership_exists is None:
            raise NotFoundError()
        acl = DataSourceAcl(
            tenant_id=context.tenant_id,
            data_source_id=source_id,
            principal_id=principal_id,
            access=access,
        )
        session.add(acl)
        await session.flush()
        return acl
