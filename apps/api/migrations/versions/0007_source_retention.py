"""Source tombstones, credential recovery, and test retention support.

Revision ID: 0007_source_retention
Revises: 0006_connection_tests
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_source_retention"
down_revision: str | None = "0006_connection_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_source", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "data_source",
        sa.Column("credential_destroy_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "data_source",
        sa.Column("credential_destroyed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_data_source_retention_lifecycle",
        "data_source",
        "(status='active' AND disabled_at IS NULL AND credential_destroy_after IS NULL AND credential_destroyed_at IS NULL) OR "
        "(status='disabled' AND disabled_at IS NOT NULL AND credential_destroy_after IS NOT NULL "
        "AND credential_destroy_after = disabled_at + interval '30 days' "
        "AND (credential_destroyed_at IS NULL OR credential_destroyed_at >= credential_destroy_after))",
    )
    op.create_index(
        "ix_data_source_credential_destruction_due",
        "data_source",
        ["credential_destroy_after"],
        postgresql_where=sa.text("credential_destroyed_at IS NULL AND status='disabled'"),
    )
    op.execute("GRANT DELETE ON connection_test TO eip_app")
    op.execute(
        """
        CREATE FUNCTION eip_maintenance_due_tenants(p_now timestamptz)
        RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT tenant_id FROM data_source
           WHERE status='disabled' AND credential_destroyed_at IS NULL
             AND credential_destroy_after <= p_now
          UNION
          SELECT tenant_id FROM connection_test
           WHERE status IN ('succeeded','failed','stale')
             AND queued_at < p_now - interval '90 days'
        $$
        """
    )
    op.execute("ALTER FUNCTION eip_maintenance_due_tenants(timestamptz) OWNER TO eip_platform")
    op.execute("REVOKE ALL ON FUNCTION eip_maintenance_due_tenants(timestamptz) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION eip_maintenance_due_tenants(timestamptz) TO eip_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS eip_maintenance_due_tenants(timestamptz)")
    op.execute("REVOKE DELETE ON connection_test FROM eip_app")
    op.drop_index("ix_data_source_credential_destruction_due", table_name="data_source")
    op.drop_constraint("ck_data_source_retention_lifecycle", "data_source", type_="check")
    op.drop_column("data_source", "credential_destroyed_at")
    op.drop_column("data_source", "credential_destroy_after")
    op.drop_column("data_source", "disabled_at")
