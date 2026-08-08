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
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from eip.api.deps import (
    DataPlaneDep,
    PlatformContextDep,
    PlatformFactoryDep,
    PlatformSession,
    SettingsDep,
)
from eip.identity.provisioning import TenantProvisioningWorkflow, TenantRecord
from eip.identity.service import PlatformAdminService
from eip.platform.context import RoleCode
from eip.platform.logging import get_logger

_log = get_logger("api.admin")

router = APIRouter(prefix="/v1/admin", tags=["platform-admin"])


class CreateTenantRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", max_length=63)
    name: str = Field(min_length=1, max_length=200)


class TenantResponse(BaseModel):
    """What an operator is shown about a tenant.

    Note the absence: no password, and no secret *reference* either. The
    reference is safe to store (ADR-015), but nothing outside the data plane
    needs it, and a value that never reaches a response model cannot leak
    through one.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    slug: str
    name: str
    status: str
    isolation_mode: str
    analytical_schema: str
    analytical_role: str | None
    provisioning_state: str
    provisioning_attempts: int
    #: Redacted at the source (`summarise_failure`). Present so the operator
    #: who has to fix a failed tenant can see what happened without shelling
    #: into the database.
    provisioning_error: str | None
    provisioned_at: datetime | None

    @classmethod
    def of(cls, record: TenantRecord) -> TenantResponse:
        return cls(
            id=record.id,
            slug=record.slug,
            name=record.name,
            status=record.status,
            isolation_mode=record.isolation_mode,
            analytical_schema=record.analytical_schema,
            analytical_role=record.analytical_role,
            provisioning_state=record.provisioning_state,
            provisioning_attempts=record.provisioning_attempts,
            provisioning_error=record.provisioning_error,
            provisioned_at=record.provisioned_at,
        )


class GrantMembershipRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role_code: RoleCode


class GrantMembershipResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: uuid.UUID


def _workflow(
    factory: PlatformFactoryDep, data_plane: DataPlaneDep, settings: SettingsDep
) -> TenantProvisioningWorkflow:
    """Build the workflow.

    It takes the session *factory*, not a session: provisioning is three
    transactions with DDL between them, and the failure record has to survive
    the transaction that failed. See `eip.identity.provisioning`.
    """
    return TenantProvisioningWorkflow(
        sessions=factory,
        data_plane=data_plane,
        stale_after_seconds=settings.provisioning_stale_after_seconds,
    )


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register and provision a tenant (platform staff only)",
)
async def create_tenant(
    payload: CreateTenantRequest,
    context: PlatformContextDep,
    factory: PlatformFactoryDep,
    data_plane: DataPlaneDep,
    settings: SettingsDep,
) -> TenantResponse:
    """Create a tenant and build its analytical plane.

    **Operator-driven, not self-serve.** Reaching this route requires the
    ``platform_admin`` capability and an elevation reason, and every call is
    audited into the new tenant's own chain. PO-003 raised provisioning's
    priority — TriVera is tenant #1, not a special case — but it did not ask
    for public signup, and this is not where one would be added.

    Safe to repeat. An interrupted create is resumed; a completed one is
    refused with 409 rather than silently rebuilding a live tenant's storage.
    """
    workflow = _workflow(factory, data_plane, settings)
    record = await workflow.create(context, slug=payload.slug, name=payload.name)

    _log.warning(
        "admin.tenant_created",
        tenant_id=str(record.id),
        actor=str(context.principal.user_id),
        reason=context.reason,
    )
    return TenantResponse.of(record)


@router.post(
    "/tenants/{tenant_id}/provision",
    response_model=TenantResponse,
    summary="Retry provisioning for a tenant (platform staff only)",
)
async def provision_tenant(
    tenant_id: uuid.UUID,
    context: PlatformContextDep,
    factory: PlatformFactoryDep,
    data_plane: DataPlaneDep,
    settings: SettingsDep,
) -> TenantResponse:
    """Resume a tenant whose provisioning did not finish.

    The recovery path for exactly the case Phase 1A had no answer to. Returns
    an already-ready tenant unchanged, and 409s if another attempt currently
    holds the claim.
    """
    workflow = _workflow(factory, data_plane, settings)
    return TenantResponse.of(await workflow.provision(context, tenant_id))


@router.get(
    "/tenants",
    response_model=list[TenantResponse],
    summary="List tenants and their provisioning state (platform staff only)",
)
async def list_tenants(
    context: PlatformContextDep,
    factory: PlatformFactoryDep,
    data_plane: DataPlaneDep,
    settings: SettingsDep,
) -> list[TenantResponse]:
    """Every tenant, incomplete ones first.

    This endpoint is what makes "failed tenants are visibly recoverable" a
    property rather than a claim. Without it, a half-built tenant is
    discoverable only by someone querying the database directly.
    """
    workflow = _workflow(factory, data_plane, settings)
    return [TenantResponse.of(record) for record in await workflow.list_tenants(context)]


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
    service = PlatformAdminService()
    membership_id = await service.add_membership(
        session,
        context,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        role=payload.role_code,
    )
    return GrantMembershipResponse(membership_id=membership_id)
