"""Tenant-owned connector configuration models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from eip.platform.db import Base


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    configuration: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    secret_name: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_version: Mapped[str] = mapped_column(String(32), nullable=False)
    connectivity_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="direct", server_default="direct"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_data_source_tenant_idempotency"),
        UniqueConstraint("tenant_id", "id", name="uq_data_source_tenant_id"),
        UniqueConstraint("tenant_id", "name", name="uq_data_source_tenant_name"),
        CheckConstraint("connector_type = 'postgresql'", name="ck_data_source_connector_type"),
        CheckConstraint("connectivity_mode = 'direct'", name="ck_data_source_connectivity_mode"),
        CheckConstraint("status IN ('active','disabled')", name="ck_data_source_status"),
        CheckConstraint("version > 0", name="ck_data_source_version"),
        CheckConstraint(
            "endpoint NOT LIKE '%://%' AND endpoint NOT LIKE '%@%'",
            name="ck_data_source_safe_endpoint",
        ),
        Index("ix_data_source_tenant_name", "tenant_id", "name"),
    )


class DataSourceAcl(Base):
    __tablename__ = "data_source_acl"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    access: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "data_source_id", "principal_id", name="uq_data_source_acl_principal"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="CASCADE",
            name="fk_data_source_acl_tenant_source",
        ),
        CheckConstraint("access IN ('view','edit','manage')", name="ck_data_source_acl_access"),
        Index("ix_data_source_acl_lookup", "tenant_id", "principal_id", "data_source_id"),
    )


class ConnectionTest(Base):
    __tablename__ = "connection_test"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    checks: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    overall_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_connection_test_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_connection_test_tenant_idempotency"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="CASCADE",
            name="fk_connection_test_tenant_source",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','stale')",
            name="ck_connection_test_status",
        ),
        CheckConstraint("source_version > 0 AND attempt > 0", name="ck_connection_test_versions"),
        CheckConstraint("jsonb_typeof(checks) = 'array'", name="ck_connection_test_checks_array"),
        CheckConstraint(
            "(status='queued' AND started_at IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL) OR "
            "(status='running' AND started_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded','failed','stale') AND completed_at IS NOT NULL)",
            name="ck_connection_test_lifecycle",
        ),
        Index("ix_connection_test_source_latest", "tenant_id", "data_source_id", "queued_at"),
        Index("ix_connection_test_queued", "queued_at", postgresql_where=text("status='queued'")),
    )
