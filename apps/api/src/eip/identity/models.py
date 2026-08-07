"""Identity & Tenant ORM models (ADR-003, ADR-010).

Tenancy classification of each table — this is the security-relevant part:

``tenant``      GLOBAL. The registry itself. Readable only through the
                privileged path or via a membership the caller holds.
``app_user``    GLOBAL. A person may belong to several tenants, so the user
                record cannot itself be tenant-scoped. Nothing sensitive to a
                tenant lives here, and user lookups always traverse
                ``membership`` — a user is never enumerated directly.
``role`` /
``role_capability``  GLOBAL. The platform-shipped role catalog. Identical for
                every tenant by design (principle 1).
``membership``  TENANT-SCOPED. RLS-protected. This is the join that grants a
                user access to a tenant, and the object an attacker would need
                to forge.

Named ``app_user`` because ``user`` is reserved in PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eip.platform.db import Base


class Tenant(Base):
    """An organization. The isolation and billing boundary.

    Organizational structure *inside* a tenant (divisions, subsidiaries) is a
    ``Dimension``, not a nested tenant — see ADR-003 Future Considerations and
    open question Q11.
    """

    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    #: The tenant's analytical namespace (ADR-003 §2). Derived from the tenant
    #: record by the provisioning subsystem, never from request input.
    analytical_schema: Mapped[str] = mapped_column(String(63), nullable=False)

    #: The isolation mode this tenant is served under. Stored so that a tenant
    #: can later be promoted to a dedicated instance as *configuration*
    #: (ADR-003 Tier 2) without a code change.
    isolation_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="schema_per_tenant"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list[Membership]] = relationship(back_populates="tenant")

    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','offboarding')", name="ck_tenant_status"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="ck_tenant_slug_format"),
    )


class AppUser(Base):
    """A person.

    No password column exists and none may be added: authentication is
    delegated to an OIDC provider (ADR-010 §1). ``external_subject`` is the
    OIDC ``sub`` claim, scoped by ``issuer`` so two identity providers cannot
    collide.
    """

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")

    __table_args__ = (
        UniqueConstraint("issuer", "external_subject", name="uq_app_user_issuer_subject"),
        CheckConstraint("status IN ('active','disabled')", name="ck_app_user_status"),
    )


class Role(Base):
    """A platform-shipped role. Global; identical for every tenant."""

    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    #: Platform-staff roles are never assignable inside a tenant.
    is_platform_role: Mapped[bool] = mapped_column(nullable=False, default=False)


class RoleCapability(Base):
    """A capability granted by a role (ADR-010 layer 1)."""

    __tablename__ = "role_capability"

    role_code: Mapped[str] = mapped_column(
        ForeignKey("role.code", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(64), primary_key=True)


class Membership(Base):
    """A user's membership of a tenant. **Tenant-scoped, RLS-protected.**

    This row is the authorization decision. Tenant context is resolved by
    looking it up for the authenticated principal (ADR-003 §3); it is never
    taken from a header, subdomain, or request body.
    """

    __tablename__ = "membership"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role.code", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[AppUser] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        Index("ix_membership_user", "user_id"),
        Index("ix_membership_tenant", "tenant_id"),
        CheckConstraint("status IN ('active','suspended')", name="ck_membership_status"),
    )
