"""Provisioning lifecycle state on ``tenant``.

Phase 1A provisioned a tenant inside a request handler: insert the row, then run
the DDL. It worked, and it had no answer for the case where the second half
failed. The tenant existed, its analytical schema did not, nothing recorded
which, and the only way to find out was to try to use it.

PO-003 makes that unacceptable rather than merely untidy — TriVera is tenant #1
and not a special case, so provisioning is an operation staff will run
repeatedly rather than once by hand.

This migration adds the state a retry needs:

* ``provisioning_state`` — pending → in_progress → ready, or failed. Distinct
  from ``status``, which is what the *business* thinks of the tenant. A tenant
  can be `active` with a data plane that never built.
* ``provisioning_attempts`` — a tenant that keeps failing should look like one.
* ``provisioning_started_at`` — the staleness clock. An attempt whose process
  died leaves ``in_progress`` behind; without a timestamp that state would
  block every future retry.
* ``provisioned_at``, ``provisioning_error`` — when it worked, and why it did
  not. The error is redacted at the application boundary
  (``eip.identity.provisioning.summarise_failure``): driver errors quote the
  failing statement, and the statement that creates a tenant role contains that
  role's password.

``status`` gains ``'provisioning'`` so a tenant is not described as active
before its storage exists.

Existing rows are backfilled to ``ready``: they were provisioned by the Phase 1A
path, which only ever returned after the data plane had been created.

Revision ID: 0004_tenant_provisioning
Revises: 0003_tenant_credentials
Create Date: 2026-08-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tenant_provisioning"
down_revision: str | None = "0003_tenant_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "provisioning_state",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "tenant",
        sa.Column("provisioning_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenant",
        sa.Column("provisioning_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("tenant", sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenant", sa.Column("provisioning_error", sa.String(500), nullable=True))

    # Anything already here came through the Phase 1A path, which returned only
    # after the data plane existed.
    op.execute(
        "UPDATE tenant SET provisioning_state = 'ready', provisioned_at = created_at "
        "WHERE analytical_schema IS NOT NULL"
    )

    op.create_check_constraint(
        "ck_tenant_provisioning_state",
        "tenant",
        "provisioning_state IN ('pending','in_progress','ready','failed')",
    )

    # A tenant is not 'active' until its storage exists.
    op.drop_constraint("ck_tenant_status", "tenant", type_="check")
    op.create_check_constraint(
        "ck_tenant_status",
        "tenant",
        "status IN ('provisioning','active','suspended','offboarding')",
    )

    # The operator console's only query: "what is stuck?". Partial, because
    # ready tenants are the overwhelming majority and are never the answer.
    op.create_index(
        "ix_tenant_provisioning_incomplete",
        "tenant",
        ["provisioning_state", "provisioning_started_at"],
        postgresql_where=sa.text("provisioning_state <> 'ready'"),
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_provisioning_incomplete", table_name="tenant")

    # Tenants mid-provisioning would violate the narrower constraint. Settle
    # them as 'active' rather than fail the rollback: the row and its schema
    # both exist by then or the operator has a failed tenant to deal with
    # either way, and a migration that cannot run backwards cannot ship.
    op.execute("UPDATE tenant SET status = 'active' WHERE status = 'provisioning'")

    op.drop_constraint("ck_tenant_status", "tenant", type_="check")
    op.create_check_constraint(
        "ck_tenant_status", "tenant", "status IN ('active','suspended','offboarding')"
    )
    op.drop_constraint("ck_tenant_provisioning_state", "tenant", type_="check")

    op.drop_column("tenant", "provisioning_error")
    op.drop_column("tenant", "provisioned_at")
    op.drop_column("tenant", "provisioning_started_at")
    op.drop_column("tenant", "provisioning_attempts")
    op.drop_column("tenant", "provisioning_state")
