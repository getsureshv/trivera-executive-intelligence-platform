"""Audit and outbox ORM models (ADR-014 §5, ADR-009 §3).

Both tables are tenant-scoped and RLS-protected.

``audit_event`` is append-only, and that is enforced at the *grant* level:
migration 0001 revokes ``UPDATE`` and ``DELETE`` on it from the application
role. Application-level discipline alone would not survive a bug.

Tamper-evidence is a per-tenant hash chain rather than an aspiration: each row
carries ``prev_hash`` and a ``hash`` computed over its own canonical content
plus its predecessor's hash. Removing or editing a row breaks the chain, which
a verification job detects (ADR-014 §5).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from eip.platform.db import Base


class AuditEvent(Base):
    """A recorded governance or security event. Tenant-scoped, append-only."""

    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )

    #: Monotonic per tenant. Serialised by a transaction-scoped advisory lock,
    #: which is what makes the hash chain well-defined under concurrency.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- actor ---
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # --- what happened ---
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success", server_default="success"
    )

    # --- correlation (ADR-014 §2) ---
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Structured, redacted detail. Never secrets, never business values —
    #: `AuditService.record` passes everything through `redact()` first.
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )

    # --- tamper evidence ---
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_audit_event_tenant_seq"),
        Index("ix_audit_event_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_audit_event_actor", "tenant_id", "actor_user_id"),
        CheckConstraint("outcome IN ('success','failure','denied')", name="ck_audit_outcome"),
        CheckConstraint("actor_type IN ('user','service','system')", name="ck_audit_actor_type"),
    )


class OutboxMessage(Base):
    """A durable, transactionally-published message (ADR-009 §3).

    Jobs are never enqueued directly from a request handler. The handler writes
    an outbox row **in the same transaction** as its state change; the relay in
    the worker publishes it afterwards.

    This removes two classic failures at once: "the transaction rolled back but
    the job already ran", and "the job was never enqueued because the broker
    blipped". In a governance product, a publish that emits an audit event but
    no follow-up job — or the reverse — is a correctness defect.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )

    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )

    #: Correlates the publish with the request that caused it.
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        # Partial index: the relay only ever scans unpublished rows, so the
        # index stays small regardless of history retained.
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=published_at.is_(None),
        ),
        Index("ix_outbox_tenant", "tenant_id"),
    )


class AuditChainHead(Base):
    """Per-tenant audit checkpoint (migration 0002).

    Records the highest sequence and hash ever written for a tenant. Deleting
    audit events leaves this behind, which is what makes tail deletion,
    truncation, and total erasure detectable — the hash chain alone cannot see
    any of them, because the survivors form a valid chain
    (:func:`eip.governance.audit.verify_chain`).

    Two deliberate departures from the other models:

    * **No foreign key to ``tenant``.** The checkpoint must outlive the tenant,
      so that deleting the tenant row cannot also erase the proof that a chain
      existed. Offboarding marks ``offboarded_at`` through a privileged
      function instead.
    * **No runtime write path.** Only the ``SECURITY DEFINER`` trigger created
      in migration 0002 writes it; ``INSERT``/``UPDATE``/``DELETE`` are revoked
      from both runtime roles. It is mapped here so the ORM and the migrated
      schema agree — not because the application writes it.
    """

    __tablename__ = "audit_chain_head"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Set by ``eip_audit_chain_offboard()`` so that sanctioned erasure is
    #: distinguishable from tampering.
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
