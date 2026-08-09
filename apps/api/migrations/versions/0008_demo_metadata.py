"""Frozen tenant-owned metadata foundation for the seeded CEO demonstration.

Revision ID: 0008_demo_metadata
Revises: 0007_source_retention
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_demo_metadata"
down_revision: str | None = "0007_source_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("configuration_bundle", "demo_dataset", "demo_metadata", "governed_fact")
CAPABILITIES = (
    ("tenant_admin", "executive.read"),
    ("tenant_admin", "metric.query"),
    ("tenant_admin", "lineage.read"),
    ("data_steward", "executive.read"),
    ("data_steward", "metric.query"),
    ("data_steward", "lineage.read"),
    ("executive", "executive.read"),
    ("executive", "metric.query"),
    ("executive", "lineage.read"),
    ("viewer", "executive.read"),
)


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "configuration_bundle",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tenant_id", uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("author_id", uuid, nullable=False),
        sa.Column("approver_id", uuid),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_configuration_bundle_tenant_id"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_configuration_bundle_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            ["membership.tenant_id", "membership.user_id"],
            ondelete="RESTRICT",
            name="fk_configuration_bundle_author_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approver_id"],
            ["membership.tenant_id", "membership.user_id"],
            ondelete="RESTRICT",
            name="fk_configuration_bundle_approver_membership",
        ),
        sa.CheckConstraint("version > 0", name="ck_configuration_bundle_version"),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_configuration_bundle_status"
        ),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_configuration_bundle_hash"),
        sa.CheckConstraint(
            "(status='published') = (published_at IS NOT NULL)",
            name="ck_configuration_bundle_publication",
        ),
    )
    op.create_table(
        "demo_dataset",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tenant_id", uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("bundle_id", uuid, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("as_of_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_demo_dataset_tenant_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_demo_dataset_code"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bundle_id"],
            ["configuration_bundle.tenant_id", "configuration_bundle.id"],
            ondelete="CASCADE",
            name="fk_demo_dataset_bundle",
        ),
        sa.CheckConstraint("origin='seeded_demo'", name="ck_demo_dataset_origin"),
        sa.CheckConstraint("reset_version > 0", name="ck_demo_dataset_reset_version"),
    )
    op.create_table(
        "demo_metadata",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tenant_id", uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("bundle_id", uuid, nullable=False),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("parent_id", uuid),
        sa.Column("related_id", uuid),
        sa.Column("data_source_id", uuid),
        sa.Column("allowed_dimension_id", uuid),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_demo_metadata_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "kind", "code", "version", name="uq_demo_metadata_identity"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bundle_id"],
            ["configuration_bundle.tenant_id", "configuration_bundle.id"],
            ondelete="CASCADE",
            name="fk_demo_metadata_bundle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["demo_dataset.tenant_id", "demo_dataset.id"],
            ondelete="CASCADE",
            name="fk_demo_metadata_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_parent",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "related_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_related",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "data_source_id"],
            ["data_source.tenant_id", "data_source.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "allowed_dimension_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_demo_metadata_allowed_dimension",
        ),
        sa.CheckConstraint(
            "kind IN ('source_object','source_field','semantic_entity','semantic_field','dimension','dimension_value','entity_binding','field_binding','metric','metric_version','quality_result','freshness_result','dashboard','widget','attention_rule')",
            name="ck_demo_metadata_kind",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','retired','pass','warn','fail','fresh','stale')",
            name="ck_demo_metadata_status",
        ),
        sa.CheckConstraint("origin='seeded_demo'", name="ck_demo_metadata_origin"),
        sa.CheckConstraint("version > 0", name="ck_demo_metadata_version"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_demo_metadata_hash"),
    )
    op.create_table(
        "governed_fact",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "tenant_id", uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column("metric_version_id", uuid, nullable=False),
        sa.Column("dimension_value_id", uuid),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("prior_value", sa.Numeric(24, 6)),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", uuid, nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("owner_label", sa.String(200), nullable=False),
        sa.Column("quality_status", sa.String(16)),
        sa.Column("quality_code", sa.String(64)),
        sa.Column("quality_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("freshness_status", sa.String(16)),
        sa.Column("freshness_code", sa.String(64)),
        sa.Column("freshness_evaluated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_governed_fact_tenant_id"),
        sa.UniqueConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["demo_dataset.tenant_id", "demo_dataset.id"],
            ondelete="CASCADE",
            name="fk_governed_fact_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_version_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_governed_fact_metric",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dimension_value_id"],
            ["demo_metadata.tenant_id", "demo_metadata.id"],
            ondelete="RESTRICT",
            name="fk_governed_fact_dimension",
        ),
        sa.CheckConstraint("kind IN ('target','observation')", name="ck_governed_fact_kind"),
        sa.CheckConstraint("period_start <= period_end", name="ck_governed_fact_period"),
        sa.CheckConstraint("origin='seeded_demo'", name="ck_governed_fact_origin"),
        sa.CheckConstraint("config_version > 0", name="ck_governed_fact_config_version"),
        sa.CheckConstraint(
            "quality_status IS NULL OR quality_status IN ('pass','warn','fail')",
            name="ck_governed_fact_quality_status",
        ),
        sa.CheckConstraint(
            "freshness_status IS NULL OR freshness_status IN ('fresh','stale')",
            name="ck_governed_fact_freshness_status",
        ),
        sa.CheckConstraint(
            "kind='target' OR (quality_status IS NOT NULL AND quality_code IS NOT NULL AND quality_evaluated_at IS NOT NULL AND freshness_status IS NOT NULL AND freshness_code IS NOT NULL AND freshness_evaluated_at IS NOT NULL)",
            name="ck_governed_fact_observation_results",
        ),
    )
    op.execute("""
      CREATE FUNCTION reject_frozen_demo_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF current_user <> 'eip_migrator' THEN
          RAISE EXCEPTION 'published demo configuration is immutable';
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION reject_frozen_demo_mutation() FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION validate_demo_metadata_links() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE parent_kind text; related_kind text; dimension_kind text;
      BEGIN
        SELECT kind INTO parent_kind FROM demo_metadata
         WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_id;
        SELECT kind INTO related_kind FROM demo_metadata
         WHERE tenant_id=NEW.tenant_id AND id=NEW.related_id;
        SELECT kind INTO dimension_kind FROM demo_metadata
         WHERE tenant_id=NEW.tenant_id AND id=NEW.allowed_dimension_id;
        IF (NEW.kind='source_object') <> (NEW.data_source_id IS NOT NULL) THEN
          RAISE EXCEPTION 'source_object requires exactly one selected data source';
        END IF;
        IF NEW.kind='dimension_value' AND parent_kind <> 'dimension' THEN
          RAISE EXCEPTION 'dimension_value parent must be a dimension';
        END IF;
        IF NEW.kind='metric_version' AND
           (parent_kind <> 'metric' OR related_kind <> 'semantic_field' OR
            dimension_kind <> 'dimension') THEN
          RAISE EXCEPTION 'metric_version links are invalid';
        END IF;
        RETURN NEW;
      END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION validate_demo_metadata_links() FROM PUBLIC")
    op.execute("""
      CREATE FUNCTION validate_governed_fact_links() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE metric_kind text; allowed_dimension uuid; actual_dimension uuid;
      BEGIN
        SELECT kind, allowed_dimension_id INTO metric_kind, allowed_dimension
          FROM demo_metadata WHERE tenant_id=NEW.tenant_id AND id=NEW.metric_version_id;
        IF metric_kind <> 'metric_version' THEN
          RAISE EXCEPTION 'fact metric reference is not a metric version';
        END IF;
        IF NEW.dimension_value_id IS NOT NULL THEN
          SELECT parent_id INTO actual_dimension FROM demo_metadata
           WHERE tenant_id=NEW.tenant_id AND id=NEW.dimension_value_id
             AND kind='dimension_value';
          IF actual_dimension IS DISTINCT FROM allowed_dimension THEN
            RAISE EXCEPTION 'fact dimension is not allowed by metric version';
          END IF;
        END IF;
        RETURN NEW;
      END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION validate_governed_fact_links() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER configuration_bundle_frozen BEFORE UPDATE OR DELETE ON "
        "configuration_bundle FOR EACH ROW WHEN (OLD.status='published') "
        "EXECUTE FUNCTION reject_frozen_demo_mutation()"
    )
    op.execute(
        "CREATE TRIGGER demo_metadata_frozen BEFORE UPDATE OR DELETE ON demo_metadata "
        "FOR EACH ROW WHEN (OLD.status='published') "
        "EXECUTE FUNCTION reject_frozen_demo_mutation()"
    )
    op.execute(
        "CREATE TRIGGER demo_dataset_frozen BEFORE UPDATE OR DELETE ON demo_dataset "
        "FOR EACH ROW EXECUTE FUNCTION reject_frozen_demo_mutation()"
    )
    op.execute(
        "CREATE TRIGGER governed_observation_append_only BEFORE UPDATE OR DELETE ON "
        "governed_fact FOR EACH ROW WHEN (OLD.kind='observation') "
        "EXECUTE FUNCTION reject_frozen_demo_mutation()"
    )
    op.execute(
        "CREATE TRIGGER demo_metadata_links BEFORE INSERT OR UPDATE ON demo_metadata "
        "FOR EACH ROW EXECUTE FUNCTION validate_demo_metadata_links()"
    )
    op.execute(
        "CREATE TRIGGER governed_fact_links BEFORE INSERT OR UPDATE ON governed_fact "
        "FOR EACH ROW EXECUTE FUNCTION validate_governed_fact_links()"
    )
    op.execute("""
      CREATE FUNCTION eip_reset_seeded_demo(p_tenant_id uuid, p_bundle_id uuid)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,public AS $$
      DECLARE previous_tenant text;
      BEGIN
        IF p_tenant_id IS NULL OR p_bundle_id IS NULL THEN
          RAISE EXCEPTION 'tenant and bundle identifiers are required';
        END IF;
        previous_tenant := current_setting('app.tenant_id', true);
        PERFORM set_config('app.tenant_id', p_tenant_id::text, true);
        DELETE FROM configuration_bundle b
         WHERE b.tenant_id=p_tenant_id AND b.id=p_bundle_id
           AND EXISTS (SELECT 1 FROM demo_dataset d WHERE d.tenant_id=p_tenant_id
             AND d.bundle_id=b.id AND d.origin='seeded_demo');
        PERFORM set_config('app.tenant_id', COALESCE(previous_tenant, ''), true);
      END $$
    """)
    op.execute("ALTER FUNCTION eip_reset_seeded_demo(uuid,uuid) OWNER TO eip_migrator")
    op.execute("REVOKE ALL ON FUNCTION eip_reset_seeded_demo(uuid,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION eip_reset_seeded_demo(uuid,uuid) TO eip_platform")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON {table} FROM eip_app CASCADE")
        op.execute(f"GRANT SELECT ON {table} TO eip_app")
    capabilities = sa.table(
        "role_capability", sa.column("role_code", sa.String), sa.column("capability", sa.String)
    )
    op.bulk_insert(
        capabilities,
        [{"role_code": role, "capability": capability} for role, capability in CAPABILITIES],
    )


def downgrade() -> None:
    for role, capability in CAPABILITIES:
        op.execute(
            sa.text(
                "DELETE FROM role_capability WHERE role_code=:role AND capability=:capability"
            ).bindparams(role=role, capability=capability)
        )
    op.execute("DROP FUNCTION IF EXISTS eip_reset_seeded_demo(uuid,uuid)")
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS validate_governed_fact_links()")
    op.execute("DROP FUNCTION IF EXISTS validate_demo_metadata_links()")
    op.execute("DROP FUNCTION IF EXISTS reject_frozen_demo_mutation()")
