"""Tenant and principal context.

This module is the single place where "who is asking, on behalf of which
tenant" is represented. Three rules from ADR-003 §3 and ADR-010 shape it:

1. **Tenant context is derived from the authenticated principal's membership.**
   Never from a header, a subdomain, a query parameter, or a request body. A
   browser may *request* a tenant; the server *verifies* the membership.

2. **There is no ambient global.** ``TenantContext`` is passed explicitly as a
   typed argument into every data-access call, so "forgot to scope the query"
   is a type error rather than a silent cross-tenant read.

3. **Privileged access is a different type.** ``PlatformContext`` is not a
   ``TenantContext`` with a flag set — it is a separate class reached through a
   separate database role and a separate engine, so the two can never be
   confused at a call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import final


class Capability(StrEnum):
    """Grantable capabilities (ADR-010 layer 1).

    Roles grant capabilities; capabilities are checked. Phase 1A ships the
    subset the platform skeleton needs. Resource ACLs, field policy, and row
    policy (layers 2-4) arrive with the semantic and metric layers.
    """

    TENANT_READ = "tenant.read"
    TENANT_MANAGE = "tenant.manage"
    MEMBERSHIP_READ = "membership.read"
    MEMBERSHIP_MANAGE = "membership.manage"
    AUDIT_READ = "audit.read"
    SOURCE_READ = "source.read"
    SOURCE_CREATE = "source.create"
    SOURCE_UPDATE = "source.update"
    SOURCE_DELETE = "source.delete"
    SOURCE_TEST = "source.test"
    SOURCE_ACL_MANAGE = "source.acl.manage"
    EXECUTIVE_READ = "executive.read"
    METRIC_QUERY = "metric.query"
    LINEAGE_READ = "lineage.read"
    # Platform-staff only. Never granted to a tenant role.
    PLATFORM_TENANT_PROVISION = "platform.tenant.provision"


class RoleCode(StrEnum):
    """Platform-shipped roles (ADR-010 layer 1).

    Tenants may later compose custom roles from platform capabilities; they
    may never introduce new capabilities, which would be tenant-specific code
    by another name (principle 1).
    """

    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    DATA_STEWARD = "data_steward"
    EXECUTIVE = "executive"
    VIEWER = "viewer"


ROLE_CAPABILITIES: dict[RoleCode, frozenset[Capability]] = {
    RoleCode.PLATFORM_ADMIN: frozenset(
        {
            Capability.PLATFORM_TENANT_PROVISION,
            Capability.TENANT_READ,
            Capability.TENANT_MANAGE,
            Capability.MEMBERSHIP_READ,
            Capability.MEMBERSHIP_MANAGE,
            Capability.AUDIT_READ,
        }
    ),
    RoleCode.TENANT_ADMIN: frozenset(
        {
            Capability.TENANT_READ,
            Capability.TENANT_MANAGE,
            Capability.MEMBERSHIP_READ,
            Capability.MEMBERSHIP_MANAGE,
            Capability.AUDIT_READ,
            Capability.SOURCE_READ,
            Capability.SOURCE_CREATE,
            Capability.SOURCE_UPDATE,
            Capability.SOURCE_DELETE,
            Capability.SOURCE_TEST,
            Capability.SOURCE_ACL_MANAGE,
            Capability.EXECUTIVE_READ,
            Capability.METRIC_QUERY,
            Capability.LINEAGE_READ,
        }
    ),
    RoleCode.DATA_STEWARD: frozenset(
        {
            Capability.TENANT_READ,
            Capability.MEMBERSHIP_READ,
            Capability.AUDIT_READ,
            Capability.SOURCE_READ,
            Capability.SOURCE_CREATE,
            Capability.SOURCE_UPDATE,
            Capability.SOURCE_TEST,
            Capability.SOURCE_ACL_MANAGE,
            Capability.EXECUTIVE_READ,
            Capability.METRIC_QUERY,
            Capability.LINEAGE_READ,
        }
    ),
    RoleCode.EXECUTIVE: frozenset(
        {
            Capability.TENANT_READ,
            Capability.MEMBERSHIP_READ,
            Capability.EXECUTIVE_READ,
            Capability.METRIC_QUERY,
            Capability.LINEAGE_READ,
        }
    ),
    RoleCode.VIEWER: frozenset({Capability.TENANT_READ, Capability.EXECUTIVE_READ}),
}


class ActorType(StrEnum):
    """What kind of principal performed an action, for the audit trail."""

    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


@final
@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity.

    ``external_subject`` is the OIDC ``sub``. We store it but never a password:
    the platform is not an identity provider (ADR-010 §1).
    """

    user_id: uuid.UUID
    external_subject: str
    email: str
    actor_type: ActorType = ActorType.USER


@final
@dataclass(frozen=True, slots=True)
class TenantContext:
    """A resolved, verified tenant scope for exactly one request or job.

    Construction is the assertion that membership was checked. Nothing in the
    codebase builds one from untrusted input; the only production paths are
    ``eip.identity.auth.resolve_context`` (requests) and the job envelope
    (workers).
    """

    tenant_id: uuid.UUID
    tenant_slug: str
    principal: Principal
    role: RoleCode
    capabilities: frozenset[Capability]
    # Correlates every log record, span, and audit event for this unit of work.
    trace_id: str
    request_id: str

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        """Raise if the principal lacks ``capability``.

        Import is local to avoid a cycle: ``errors`` is allowed to describe
        authorization failures without depending on the context type.
        """
        if not self.has(capability):
            from eip.platform.errors import ForbiddenError

            raise ForbiddenError(capability=capability.value)

    @property
    def audit_actor_id(self) -> uuid.UUID:
        return self.principal.user_id


@final
@dataclass(frozen=True, slots=True)
class PlatformContext:
    """The explicit privileged path (ADR-003 §3, ADR-010 §5).

    Deliberately *not* a ``TenantContext``. Operations that legitimately span
    tenants — provisioning a tenant, platform reporting — take this type, use
    the ``eip_platform`` database role (which carries ``BYPASSRLS``), and are
    always audited.

    ``reason`` is required, not optional: privileged access without a recorded
    justification is exactly what the break-glass model exists to prevent.
    """

    principal: Principal
    reason: str
    trace_id: str
    request_id: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            msg = "PlatformContext requires a non-empty reason (ADR-010 §5)"
            raise ValueError(msg)
