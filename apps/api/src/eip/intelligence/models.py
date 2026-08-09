"""Frozen tenant-owned metadata models for the seeded executive demonstration."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from eip.platform.db import Base


class ConfigurationBundle(Base):
    __tablename__ = "configuration_bundle"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    approver_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_reason: Mapped[str] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_configuration_bundle_tenant_id"),
        UniqueConstraint("tenant_id", "version", name="uq_configuration_bundle_version"),
        ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            ["membership.tenant_id", "membership.user_id"],
            ondelete="RESTRICT",
            name="fk_configuration_bundle_author_membership",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approver_id"],
            ["membership.tenant_id", "membership.user_id"],
            ondelete="RESTRICT",
            name="fk_configuration_bundle_approver_membership",
        ),
        CheckConstraint("version > 0", name="ck_configuration_bundle_version"),
        CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_configuration_bundle_status"
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_configuration_bundle_hash"),
        CheckConstraint(
            "(status='published') = (published_at IS NOT NULL)",
            name="ck_configuration_bundle_publication",
        ),
    )


class DemoDataset(Base):
    __tablename__ = "demo_dataset"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE")
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(1000))
    as_of_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reset_version: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_demo_dataset_tenant_id"),
        UniqueConstraint("tenant_id", "code", name="uq_demo_dataset_code"),
        ForeignKeyConstraint(
            ["tenant_id", "bundle_id"],
            ["configuration_bundle.tenant_id", "configuration_bundle.id"],
            ondelete="CASCADE",
            name="fk_demo_dataset_bundle",
        ),
        CheckConstraint("origin='seeded_demo'", name="ck_demo_dataset_origin"),
        CheckConstraint("reset_version > 0", name="ck_demo_dataset_reset_version"),
    )


class DemoMetadata(Base):
    __tablename__ = "demo_metadata"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE")
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    related_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    allowed_dimension_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    content_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_demo_metadata_tenant_id"),
        UniqueConstraint("tenant_id", "kind", "code", "version", name="uq_demo_metadata_identity"),
        ForeignKeyConstraint(
            ["tenant_id", "bundle_id"],
            ["configuration_bundle.tenant_id", "configuration_bundle.id"],
            ondelete="CASCADE",
            name="fk_demo_metadata_bundle",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["demo_dataset.tenant_id", "demo_dataset.id"],
            ondelete="CASCADE",
            name="fk_demo_metadata_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_parent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "related_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_related",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "allowed_dimension_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_allowed_dimension",
        ),
        CheckConstraint(
            "kind IN ('source_object','source_field','semantic_entity','semantic_field',"
            "'dimension','dimension_value','entity_binding','field_binding','metric',"
            "'metric_version','quality_result','freshness_result','dashboard','widget',"
            "'attention_rule')",
            name="ck_demo_metadata_kind",
        ),
        CheckConstraint(
            "status IN ('draft','published','retired','pass','warn','fail','fresh','stale')",
            name="ck_demo_metadata_status",
        ),
        CheckConstraint("origin='seeded_demo'", name="ck_demo_metadata_origin"),
        CheckConstraint("version > 0", name="ck_demo_metadata_version"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_demo_metadata_hash"),
    )


class GovernedFact(Base):
    __tablename__ = "governed_fact"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE")
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    metric_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    dimension_value_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    prior_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    config_version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(32))
    owner_label: Mapped[str] = mapped_column(String(200))
    quality_status: Mapped[str | None] = mapped_column(String(16))
    quality_code: Mapped[str | None] = mapped_column(String(64))
    quality_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_status: Mapped[str | None] = mapped_column(String(16))
    freshness_code: Mapped[str | None] = mapped_column(String(64))
    freshness_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_governed_fact_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "kind",
            "metric_version_id",
            "dimension_value_id",
            "period_start",
            "period_end",
            name="uq_governed_fact_scope",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["demo_dataset.tenant_id", "demo_dataset.id"],
            ondelete="CASCADE",
            name="fk_governed_fact_dataset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_version_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_governed_fact_metric",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dimension_value_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_governed_fact_dimension",
        ),
        CheckConstraint("kind IN ('target','observation')", name="ck_governed_fact_kind"),
        CheckConstraint("period_start <= period_end", name="ck_governed_fact_period"),
        CheckConstraint("origin='seeded_demo'", name="ck_governed_fact_origin"),
        CheckConstraint("config_version > 0", name="ck_governed_fact_config_version"),
        CheckConstraint(
            "quality_status IS NULL OR quality_status IN ('pass','warn','fail')",
            name="ck_governed_fact_quality_status",
        ),
        CheckConstraint(
            "freshness_status IS NULL OR freshness_status IN ('fresh','stale')",
            name="ck_governed_fact_freshness_status",
        ),
        CheckConstraint(
            "kind='target' OR (quality_status IS NOT NULL AND quality_code IS NOT NULL "
            "AND quality_evaluated_at IS NOT NULL AND freshness_status IS NOT NULL "
            "AND freshness_code IS NOT NULL AND freshness_evaluated_at IS NOT NULL)",
            name="ck_governed_fact_observation_results",
        ),
    )
