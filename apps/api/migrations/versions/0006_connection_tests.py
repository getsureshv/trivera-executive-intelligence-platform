"""Durable tenant-owned connection tests.

Revision ID: 0006_connection_tests
Revises: 0005_data_sources
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_connection_tests"
down_revision: str | None = "0005_data_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_test",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("checks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("overall_code", sa.String(64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_connection_test_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_connection_test_tenant_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="CASCADE",
            name="fk_connection_test_tenant_source",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','stale')",
            name="ck_connection_test_status",
        ),
        sa.CheckConstraint(
            "source_version > 0 AND attempt > 0", name="ck_connection_test_versions"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(checks) = 'array'", name="ck_connection_test_checks_array"
        ),
        sa.CheckConstraint(
            "(status='queued' AND started_at IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL) OR "
            "(status='running' AND started_at IS NOT NULL AND lease_expires_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded','failed','stale') AND completed_at IS NOT NULL)",
            name="ck_connection_test_lifecycle",
        ),
    )
    op.create_index(
        "ix_connection_test_source_latest",
        "connection_test",
        ["tenant_id", "data_source_id", "queued_at"],
    )
    op.create_index(
        "ix_connection_test_queued",
        "connection_test",
        ["queued_at"],
        postgresql_where=sa.text("status='queued'"),
    )
    op.execute("ALTER TABLE connection_test ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connection_test FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON connection_test USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute("REVOKE ALL ON connection_test FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON connection_test TO eip_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON connection_test")
    op.drop_table("connection_test")
