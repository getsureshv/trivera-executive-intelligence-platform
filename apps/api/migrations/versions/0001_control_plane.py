"""Control plane: tenants, users, roles, memberships, audit, outbox.

Establishes the Phase 1A control plane **and its tenant-isolation guarantees**.

The tables are the easy part. The security-relevant parts of this migration are:

1. ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` on every tenant-scoped table.
   ``FORCE`` matters: without it the table *owner* — the migrator role — is
   exempt, and an owner-privileged code path would silently see everything.

2. Policies resolve the tenant from ``current_setting('app.tenant_id', true)``,
   wrapped in ``NULLIF(..., '')`` so an unset or blank value yields NULL. The
   comparison then yields NULL, which is not TRUE, so **zero rows** are
   returned. Fail-closed by construction rather than by an ``IF`` somewhere.

3. ``membership_self_select`` is the one policy keyed on ``app.user_id``. It
   lets a principal read *their own* membership rows before a tenant is known,
   so sign-in does not have to run on the BYPASSRLS role. It is ``FOR SELECT``
   only and matches on ``user_id`` alone.

4. ``UPDATE`` and ``DELETE`` are revoked on ``audit_event`` from the runtime
   role, making the audit trail append-only at the grant level rather than by
   application convention (ADR-014 §5).

Revision ID: 0001_control_plane
Revises:
Create Date: 2026-08-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_control_plane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Tables that carry ``tenant_id`` and must be isolated.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("membership", "audit_event", "outbox")

#: The runtime role. Grants target it explicitly; it is never a superuser and
#: never owns these tables (see infra/postgres/init/00-roles.sql).
RUNTIME_ROLE = "eip_app"

#: Seed roles and capabilities (ADR-010 layer 1). Shipped by the platform and
#: identical for every tenant — a tenant may compose custom roles from these
#: capabilities but may never introduce new ones (principle 1).
SEED_ROLES: tuple[tuple[str, str, str, bool], ...] = (
    (
        "platform_admin",
        "Platform Administrator",
        "TriVera staff. Not assignable in a tenant.",
        True,
    ),
    ("tenant_admin", "Organization Administrator", "Manages the organization.", False),
    ("data_steward", "Data Steward", "Curates sources, semantics, and metrics.", False),
    ("executive", "Executive", "Consumes the executive experience.", False),
    ("viewer", "Viewer", "Read-only access.", False),
)

SEED_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("platform_admin", "platform.tenant.provision"),
    ("platform_admin", "tenant.read"),
    ("platform_admin", "tenant.manage"),
    ("platform_admin", "membership.read"),
    ("platform_admin", "membership.manage"),
    ("platform_admin", "audit.read"),
    ("tenant_admin", "tenant.read"),
    ("tenant_admin", "tenant.manage"),
    ("tenant_admin", "membership.read"),
    ("tenant_admin", "membership.manage"),
    ("tenant_admin", "audit.read"),
    ("data_steward", "tenant.read"),
    ("data_steward", "membership.read"),
    ("data_steward", "audit.read"),
    ("executive", "tenant.read"),
    ("executive", "membership.read"),
    ("viewer", "tenant.read"),
)


def upgrade() -> None:
    _create_tables()
    _seed_roles()
    _enable_row_level_security()
    _harden_audit_trail()


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute("DROP POLICY IF EXISTS membership_self_select ON membership")

    op.drop_table("outbox")
    op.drop_table("audit_event")
    op.drop_table("membership")
    op.drop_table("role_capability")
    op.drop_table("role")
    op.drop_table("app_user")
    op.drop_table("tenant")


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def _create_tables() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("analytical_schema", sa.String(63), nullable=False),
        sa.Column(
            "isolation_mode", sa.String(32), nullable=False, server_default="schema_per_tenant"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','offboarding')", name="ck_tenant_status"
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="ck_tenant_slug_format"
        ),
    )

    op.create_table(
        "app_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("external_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("issuer", "external_subject", name="uq_app_user_issuer_subject"),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_app_user_status"),
    )

    op.create_table(
        "role",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_platform_role", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "role_capability",
        sa.Column(
            "role_code",
            sa.String(64),
            sa.ForeignKey("role.code", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("capability", sa.String(64), primary_key=True),
    )

    op.create_table(
        "membership",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_code",
            sa.String(64),
            sa.ForeignKey("role.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        sa.CheckConstraint("status IN ('active','suspended')", name="ck_membership_status"),
    )
    op.create_index("ix_membership_user", "membership", ["user_id"])
    op.create_index("ix_membership_tenant", "membership", ["tenant_id"])

    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="success"),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_audit_event_tenant_seq"),
        sa.CheckConstraint("outcome IN ('success','failure','denied')", name="ck_audit_outcome"),
        sa.CheckConstraint("actor_type IN ('user','service','system')", name="ck_audit_actor_type"),
    )
    op.create_index("ix_audit_event_tenant_time", "audit_event", ["tenant_id", "occurred_at"])
    op.create_index("ix_audit_event_actor", "audit_event", ["tenant_id", "actor_user_id"])

    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000), nullable=True),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_index("ix_outbox_tenant", "outbox", ["tenant_id"])


def _seed_roles() -> None:
    role_table = sa.table(
        "role",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_platform_role", sa.Boolean),
    )
    op.bulk_insert(
        role_table,
        [
            {"code": code, "name": name, "description": description, "is_platform_role": platform}
            for code, name, description, platform in SEED_ROLES
        ],
    )

    capability_table = sa.table(
        "role_capability",
        sa.column("role_code", sa.String),
        sa.column("capability", sa.String),
    )
    op.bulk_insert(
        capability_table,
        [{"role_code": role, "capability": capability} for role, capability in SEED_CAPABILITIES],
    )


# ---------------------------------------------------------------------------
# tenant isolation (ADR-003)
# ---------------------------------------------------------------------------


def _enable_row_level_security() -> None:
    """Enable FORCE RLS and install the tenant policy on every scoped table.

    ``NULLIF(current_setting('app.tenant_id', true), '')::uuid`` is the exact
    expression the application sets in ``tenant_session``. The two-argument
    ``current_setting`` returns NULL rather than raising when the setting is
    absent, and ``NULLIF`` turns a blank string into NULL so the cast cannot
    fail. An unset tenant therefore matches nothing.
    """
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE also subjects the table owner. Without it, any code path that
        # connected as the owner would silently bypass isolation.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            FOR ALL
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )

    # The single policy keyed on the principal rather than the tenant. It
    # exists so that authentication can discover a user's tenants without
    # running on the BYPASSRLS role — sign-in must never disable isolation.
    # SELECT-only, and matched on user_id alone.
    op.execute(
        """
        CREATE POLICY membership_self_select ON membership
        FOR SELECT
        USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
        """
    )


def _harden_audit_trail() -> None:
    """Make the audit trail append-only at the grant level (ADR-014 §5).

    Application discipline is not enough: a bug or a compromised code path
    could otherwise rewrite history. Revoking the privilege means modification
    requires a role the application does not have.
    """
    op.execute(f"REVOKE UPDATE, DELETE ON audit_event FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_event TO {RUNTIME_ROLE}")

    # The privileged role (eip_platform) deliberately RETAINS delete. Tenant
    # offboarding and GDPR erasure delete the tenant row, which cascades to its
    # audit events, and PostgreSQL requires DELETE on the referencing table for
    # that cascade. The append-only guarantee is therefore scoped to the
    # runtime role — which is the role that serves every request, and the only
    # one an application-level compromise would obtain. Deletion by the
    # privileged role is still detectable: the hash chain breaks, and
    # `audit.verify_chain` reports the sequence where it does.
    #
    # Note that TRUNCATE remains owner-only for every role; it is never
    # granted, so no runtime path can clear the trail in one statement.
