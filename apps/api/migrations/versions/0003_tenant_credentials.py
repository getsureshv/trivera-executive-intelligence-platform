"""Per-tenant analytical credentials; remove shared role membership.

Completes the elimination of finding G10.

Migration 0002's data plane gave each tenant a ``NOLOGIN`` role and made
``eip_app`` a *member* of every one of them, so a transaction could ``SET ROLE``
into the tenant it was serving. PostgreSQL enforced the boundary after the
switch, but the switch was an application decision: ``eip_app`` remained a
credential capable of reaching every tenant, and code that named the wrong
tenant would have been obeyed.

This migration removes that capability at the database level:

1. **Revokes every ``eip_t_*`` membership from ``eip_app``.** After this,
   ``eip_app`` cannot assume any tenant role, so ``SET ROLE`` would fail even if
   the (now deleted) code path returned.
2. **Revokes any residual privilege ``eip_app`` holds on a tenant schema**, for
   deployments provisioned under the first implementation, which granted
   ``USAGE`` directly.
3. **Adds the credential-reference columns** to ``tenant``: the role name and a
   ``SecretRef`` (logical name plus version). A reference, never a value —
   dumping this table yields no credential material (ADR-015).

Existing tenant roles are left in place but are now unreachable: they are
``NOLOGIN`` with no member. ``provision()`` converts a tenant to a login role
with its own password on next run, which is idempotent by design. Roles for
tenants that are never re-provisioned are inert — they cannot log in and nobody
can assume them — and are removed by ``deprovision``.

Revision ID: 0003_tenant_credentials
Revises: 0002_isolation_audit
Create Date: 2026-08-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_tenant_credentials"
down_revision: str | None = "0002_isolation_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "eip_app"
TENANT_ROLE_PREFIX = "eip_t_"


def upgrade() -> None:
    _add_credential_reference_columns()
    _revoke_tenant_role_memberships()
    _revoke_residual_schema_privileges()


def downgrade() -> None:
    # The memberships are deliberately NOT restored. Re-granting them would
    # hand back the exact capability this migration exists to remove, and a
    # rollback of application code does not make that acceptable. A deployment
    # rolled back to 0002 would need to re-provision tenants, which is the
    # supported path.
    op.drop_column("tenant", "analytical_secret_version")
    op.drop_column("tenant", "analytical_secret_name")
    op.drop_column("tenant", "analytical_role")


def _add_credential_reference_columns() -> None:
    op.add_column("tenant", sa.Column("analytical_role", sa.String(63), nullable=True))
    op.add_column("tenant", sa.Column("analytical_secret_name", sa.String(128), nullable=True))
    op.add_column("tenant", sa.Column("analytical_secret_version", sa.String(32), nullable=True))


def _revoke_tenant_role_memberships() -> None:
    """Remove every tenant-role membership from the runtime role.

    This is the statement that closes G10 for existing deployments. Without it,
    a database provisioned under migration 0002 would keep the memberships even
    though the code no longer uses them — and a dormant capability is still a
    capability.
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            member_role text;
        BEGIN
            FOR member_role IN
                SELECT r.rolname
                  FROM pg_auth_members m
                  JOIN pg_roles r       ON r.oid = m.roleid
                  JOIN pg_roles grantee ON grantee.oid = m.member
                 WHERE grantee.rolname = '{RUNTIME_ROLE}'
                   AND r.rolname LIKE '{TENANT_ROLE_PREFIX}%'
            LOOP
                EXECUTE format('REVOKE %I FROM {RUNTIME_ROLE}', member_role);
            END LOOP;
        END $$;
        """
    )


def _revoke_residual_schema_privileges() -> None:
    """Strip any direct grant the runtime role holds on a tenant schema.

    The first Phase 1A implementation granted ``USAGE`` on every tenant schema
    directly to ``eip_app``. Deployments that passed through that version still
    carry those grants; the code changes alone would not remove them.
    """
    op.execute(
        f"""
        DO $$
        DECLARE
            schema_name text;
        BEGIN
            FOR schema_name IN
                SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant\\_%'
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM {RUNTIME_ROLE}', schema_name
                );
                EXECUTE format('REVOKE ALL ON SCHEMA %I FROM {RUNTIME_ROLE}', schema_name);
            END LOOP;
        END $$;
        """
    )
