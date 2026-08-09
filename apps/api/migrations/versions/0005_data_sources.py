"""Tenant-owned data sources and resource ACLs.

Revision ID: 0005_data_sources
Revises: 0004_tenant_provisioning
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_data_sources"
down_revision: str | None = "0004_tenant_provisioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "eip_app"
SOURCE_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("tenant_admin", "source.read"),
    ("tenant_admin", "source.create"),
    ("tenant_admin", "source.update"),
    ("tenant_admin", "source.delete"),
    ("tenant_admin", "source.test"),
    ("tenant_admin", "source.acl.manage"),
    ("data_steward", "source.read"),
    ("data_steward", "source.create"),
    ("data_steward", "source.update"),
    ("data_steward", "source.test"),
    ("data_steward", "source.acl.manage"),
)


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("connector_type", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("secret_name", sa.String(128), nullable=False),
        sa.Column("secret_version", sa.String(32), nullable=False),
        sa.Column("connectivity_mode", sa.String(32), nullable=False, server_default="direct"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_data_source_tenant_idempotency"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_data_source_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_data_source_tenant_name"),
        sa.CheckConstraint("connector_type = 'postgresql'", name="ck_data_source_connector_type"),
        sa.CheckConstraint("connectivity_mode = 'direct'", name="ck_data_source_connectivity_mode"),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_data_source_status"),
        sa.CheckConstraint("version > 0", name="ck_data_source_version"),
        sa.CheckConstraint(
            "endpoint NOT LIKE '%://%' AND endpoint NOT LIKE '%@%'",
            name="ck_data_source_safe_endpoint",
        ),
    )
    op.create_index("ix_data_source_tenant_name", "data_source", ["tenant_id", "name"])
    op.create_table(
        "data_source_acl",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "data_source_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "data_source_id", "principal_id", name="uq_data_source_acl_principal"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="CASCADE",
            name="fk_data_source_acl_tenant_source",
        ),
        sa.CheckConstraint("access IN ('view','edit','manage')", name="ck_data_source_acl_access"),
    )
    op.create_index(
        "ix_data_source_acl_lookup",
        "data_source_acl",
        ["tenant_id", "principal_id", "data_source_id"],
    )
    for table in ("data_source", "data_source_acl"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING "
            "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")

    capability = sa.table(
        "role_capability", sa.column("role_code", sa.String), sa.column("capability", sa.String)
    )
    op.bulk_insert(
        capability,
        [{"role_code": role, "capability": value} for role, value in SOURCE_CAPABILITIES],
    )


def downgrade() -> None:
    for role, capability in SOURCE_CAPABILITIES:
        op.execute(
            sa.text(
                "DELETE FROM role_capability WHERE role_code=:role AND capability=:capability"
            ).bindparams(role=role, capability=capability)
        )
    for table in ("data_source_acl", "data_source"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("data_source_acl")
    op.drop_table("data_source")
