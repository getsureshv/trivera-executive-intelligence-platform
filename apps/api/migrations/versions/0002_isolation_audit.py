"""Phase 1A remediation: audit chain head, outbox dispatch, privilege hardening.

Addresses three of the four blocking findings in the Phase 1A review. The
fourth (analytical data-plane isolation) is enforced by per-tenant roles created
at provisioning time and by ``eip_app`` being ``NOINHERIT``, both of which live
in ``infra/postgres/init/00-roles.sql`` and
``eip/dataplane/schema_per_tenant.py`` rather than in a migration.

**1. Audit chain head (finding 4).**

The original chain detected *mutation* of an event and *middle* deletion. It did
not detect deletion of the final event, truncation to an earlier valid prefix,
or deletion of the entire chain — in all three cases the survivors form a
perfectly valid chain, and an empty chain trivially verified. The claim that
"deletion by the privileged role remains detectable" was therefore false.

``audit_chain_head`` fixes that by recording, per tenant, the highest sequence
and hash ever observed. It is maintained by a ``SECURITY DEFINER`` trigger owned
by ``eip_migrator``, and **no runtime or platform role may write it**:

* ``eip_app``       — SELECT only
* ``eip_platform``  — SELECT only (it may delete audit events; it may not
                      retract the evidence that they existed)
* the trigger       — runs as ``eip_migrator`` and advances monotonically, so a
                      lower sequence cannot rewind the head

Any deletion now leaves ``head.last_seq`` greater than the surviving maximum,
which ``verify_chain`` reports.

The head deliberately carries **no foreign key** to ``tenant``. It must outlive
the tenant so that deleting the tenant row cannot also erase the proof that a
chain existed. Tenant offboarding is the sanctioned erasure path and marks the
head through ``eip_audit_chain_offboard()`` instead.

**2. Outbox dispatch function (finding 3).**

The worker previously held ``EIP_DB_PLATFORM_DSN`` — a reusable, general-purpose
``BYPASSRLS`` credential — solely to answer "which tenants have pending
messages?". A compromised worker therefore had unrestricted cross-tenant read
access, and the privileged path was logged but never audited.

``eip_outbox_pending_tenants()`` replaces it: a ``SECURITY DEFINER`` function
returning **only tenant identifiers**, never payloads. ``eip_app`` may execute
it and can obtain nothing else through it. The worker no longer receives the
platform DSN at all.

**3. Append-only hardening extended.**

``UPDATE``/``DELETE`` on ``audit_event`` were already revoked from ``eip_app``.
The same revocation is now applied to ``audit_chain_head`` for both runtime
roles.

Revision ID: 0002_isolation_audit
Revises: 0001_control_plane
Create Date: 2026-08-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_isolation_audit"
down_revision: str | None = "0001_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "eip_app"
PLATFORM_ROLE = "eip_platform"


def upgrade() -> None:
    _create_chain_head()
    _create_chain_trigger()
    _create_offboard_function()
    _create_outbox_dispatch_function()


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS eip_outbox_pending_tenants(integer)")
    op.execute("DROP FUNCTION IF EXISTS eip_audit_chain_offboard(uuid)")
    op.execute("DROP TRIGGER IF EXISTS audit_event_advance_chain ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS eip_audit_chain_advance()")
    op.drop_table("audit_chain_head")


# ---------------------------------------------------------------------------
# audit chain head
# ---------------------------------------------------------------------------


def _create_chain_head() -> None:
    op.create_table(
        "audit_chain_head",
        # No FK to tenant, deliberately: the checkpoint must survive tenant
        # deletion so that total erasure of a chain remains detectable.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("offboarded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Readable by both runtime roles; writable by neither. The only writer is
    # the SECURITY DEFINER trigger, which runs as the table's owner.
    op.execute(
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_chain_head "
        f"FROM {RUNTIME_ROLE}, {PLATFORM_ROLE}"
    )
    op.execute(f"GRANT SELECT ON audit_chain_head TO {RUNTIME_ROLE}, {PLATFORM_ROLE}")


def _create_chain_trigger() -> None:
    """Advance the head on every audit insert, monotonically.

    ``SECURITY DEFINER`` so it writes a table the calling role cannot.
    ``search_path`` is pinned — a SECURITY DEFINER function with a mutable
    search_path is a privilege-escalation primitive.

    The ``WHERE`` clause on the upsert makes the head monotonic: an attacker who
    could insert a *lower* sequence cannot use it to rewind the checkpoint.
    """
    op.execute(
        """
        CREATE FUNCTION eip_audit_chain_advance() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            INSERT INTO audit_chain_head (tenant_id, last_seq, last_hash, updated_at)
            VALUES (NEW.tenant_id, NEW.seq, NEW.hash, now())
            ON CONFLICT (tenant_id) DO UPDATE
                SET last_seq   = EXCLUDED.last_seq,
                    last_hash  = EXCLUDED.last_hash,
                    updated_at = now()
                WHERE audit_chain_head.last_seq < EXCLUDED.last_seq;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION eip_audit_chain_advance() FROM PUBLIC")
    op.execute(
        """
        CREATE TRIGGER audit_event_advance_chain
        AFTER INSERT ON audit_event
        FOR EACH ROW EXECUTE FUNCTION eip_audit_chain_advance();
        """
    )


def _create_offboard_function() -> None:
    """Mark a tenant's chain as legitimately terminated.

    Tenant offboarding deletes the tenant row, which cascades to its audit
    events. Without this, that sanctioned erasure would be indistinguishable
    from tampering. Calling it is itself a privileged act: EXECUTE is granted
    only to the platform role.
    """
    op.execute(
        """
        CREATE FUNCTION eip_audit_chain_offboard(p_tenant_id uuid) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            UPDATE audit_chain_head
               SET offboarded_at = now()
             WHERE tenant_id = p_tenant_id;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION eip_audit_chain_offboard(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION eip_audit_chain_offboard(uuid) TO {PLATFORM_ROLE}")


# ---------------------------------------------------------------------------
# outbox dispatch
# ---------------------------------------------------------------------------


def _create_outbox_dispatch_function() -> None:
    """Return tenant ids with unpublished outbox rows — and nothing else.

    This is the entire privileged surface the worker needs. It returns
    identifiers, never payloads, so a compromised worker gains the knowledge
    that a tenant has pending work and no access to what that work contains.

    Replaces the general-purpose BYPASSRLS credential the worker previously
    held (finding 3).
    """
    op.execute(
        """
        CREATE FUNCTION eip_outbox_pending_tenants(p_limit integer DEFAULT 100)
        RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT DISTINCT o.tenant_id
              FROM outbox o
             WHERE o.published_at IS NULL
             ORDER BY o.tenant_id
             LIMIT LEAST(GREATEST(p_limit, 1), 1000);
        $$;
        """
    )
    # The function must be owned by a role that can actually read `outbox`.
    # `outbox` carries FORCE ROW LEVEL SECURITY, which applies to the table
    # owner too, so a function owned by eip_migrator returns zero rows when no
    # app.tenant_id is set — silently, which is the worst way to fail.
    # Ownership therefore moves to eip_platform, whose BYPASSRLS lets the
    # function see across tenants.
    #
    # This is the design rather than a workaround: the *function* is the
    # narrowly privileged dispatcher. Its result type is
    # `TABLE(tenant_id uuid)`, so the entire cross-tenant capability it confers
    # is "which tenants have work", while the worker's own credential stays
    # fully constrained. A test asserts the result shape exactly, so widening it
    # fails the build.
    op.execute("ALTER FUNCTION eip_outbox_pending_tenants(integer) OWNER TO eip_platform")
    op.execute("REVOKE ALL ON FUNCTION eip_outbox_pending_tenants(integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION eip_outbox_pending_tenants(integer) TO {RUNTIME_ROLE}")
