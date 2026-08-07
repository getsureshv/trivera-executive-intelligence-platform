"""Platform administration — the explicit privileged path (ADR-003 §3, ADR-010 §5).

Everything here runs on the ``eip_platform`` database role, which carries
``BYPASSRLS``. That is precisely why it is a separate module, a separate
dependency, and a separate test file: privileged access should be small,
obvious, and hard to reach by accident.

Three conditions gate every route:

1. the caller holds ``platform.tenant.provision`` — granted only by the
   ``platform_admin`` role, which cannot be assigned as a tenant membership;
2. an ``X-Elevation-Reason`` header states why cross-tenant access is needed;
3. the operation writes an audit event into the *target tenant's* own chain, so
   the tenant can see that platform staff acted on their data.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from eip.api.deps import DataPlaneDep, PlatformContextDep, PlatformSession
from eip.identity.service import TenantProvisioningService
from eip.platform.context import RoleCode
from eip.platform.logging import get_logger

_log = get_logger("api.admin")

router = APIRouter(prefix="/v1/admin", tags=["platform-admin"])


class CreateTenantRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", max_length=63)
    name: str = Field(min_length=1, max_length=200)


class CreateTenantResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    slug: str
    name: str
    status: str
    isolation_mode: str


class GrantMembershipRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role_code: RoleCode


class GrantMembershipResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: uuid.UUID


@router.post(
    "/tenants",
    response_model=CreateTenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a tenant (platform staff only)",
)
async def create_tenant(
    payload: CreateTenantRequest,
    context: PlatformContextDep,
    session: PlatformSession,
    data_plane: DataPlaneDep,
) -> CreateTenantResponse:
    """Create a tenant and provision its analytical namespace.

    Phase 1A deliberately keeps this a manual, audited, platform-staff
    operation rather than self-serve. Per the Phase 0 review's answer to Q3,
    the isolation that would be irreversible if skipped is built now;
    provisioning *automation* waits until there is a second tenant to justify
    it.
    """
    service = TenantProvisioningService(data_plane)
    tenant = await service.create_tenant(session, context, slug=payload.slug, name=payload.name)

    # DDL runs after the control-plane transaction. Provisioning is idempotent,
    # so a crash between the two leaves a tenant whose plane can simply be
    # re-provisioned — the failure mode is a retry, not corruption.
    await service.provision_data_plane(tenant)

    _log.warning(
        "admin.tenant_created",
        tenant_id=str(tenant.id),
        actor=str(context.principal.user_id),
        reason=context.reason,
    )
    return CreateTenantResponse(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        isolation_mode=tenant.isolation_mode,
    )


@router.post(
    "/memberships",
    response_model=GrantMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a user access to a tenant (platform staff only)",
)
async def grant_membership(
    payload: GrantMembershipRequest,
    context: PlatformContextDep,
    session: PlatformSession,
    data_plane: DataPlaneDep,
) -> GrantMembershipResponse:
    """Grant a membership.

    ``platform_admin`` is refused by the service: it is not a tenant role, and
    permitting it here would let a tenant-scoped principal acquire
    cross-tenant capability.
    """
    service = TenantProvisioningService(data_plane)
    membership_id = await service.add_membership(
        session,
        context,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        role=payload.role_code,
    )
    return GrantMembershipResponse(membership_id=membership_id)
