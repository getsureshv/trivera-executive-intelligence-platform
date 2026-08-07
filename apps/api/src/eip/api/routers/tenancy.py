"""Tenant-scoped read endpoints.

Every route here depends on ``TenantSession``, which cannot be resolved without
a verified ``TenantContext``. Authorization therefore precedes data access as a
property of the dependency graph, not as a line of code someone must remember
to write (ADR-010 §3).

``GET /v1/tenants/{tenant_id}`` exists specifically so that identifier
manipulation is testable: it takes a tenant id from the URL and refuses
anything that is not the caller's own tenant, returning the same 404 a
non-existent tenant would produce.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from eip.api.deps import TenantContextDep, TenantSession
from eip.governance.models import AuditEvent
from eip.identity.service import TenantReadService
from eip.platform.context import Capability

router = APIRouter(prefix="/v1", tags=["tenancy"])

_tenants = TenantReadService()


class PrincipalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    email: str
    actor_type: str


class TenantResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    slug: str
    name: str
    status: str
    isolation_mode: str


class MeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal: PrincipalResponse
    tenant: TenantResponse
    role: str
    capabilities: list[str]


class MembershipResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    role_code: str
    status: str


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    trace_id: str


@router.get("/me", response_model=MeResponse, summary="The caller's identity and tenant context")
async def get_me(context: TenantContextDep, session: TenantSession) -> MeResponse:
    """Return who the caller is and which organization they are acting in.

    This is the endpoint the frontend uses to render tenant context. Note that
    it takes no input: the answer is derived entirely from the verified token
    and the membership row.
    """
    tenant = await _tenants.get_tenant(session, context)
    return MeResponse(
        principal=PrincipalResponse(
            user_id=context.principal.user_id,
            email=context.principal.email,
            actor_type=context.principal.actor_type.value,
        ),
        tenant=TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            isolation_mode=tenant.isolation_mode,
        ),
        role=context.role.value,
        capabilities=sorted(capability.value for capability in context.capabilities),
    )


@router.get(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    summary="Fetch a tenant by id (rejects any tenant but the caller's own)",
)
async def get_tenant_by_id(
    tenant_id: uuid.UUID,
    context: TenantContextDep,
    session: TenantSession,
) -> TenantResponse:
    """Fetch a tenant named in the URL.

    Deliberately accepts an identifier so that the manipulation path is real
    and testable. Any id other than the caller's own tenant yields 404 — the
    same response as a tenant that does not exist, so this endpoint cannot be
    used to discover which tenants the platform hosts (ADR-010 §4).
    """
    tenant = await _tenants.get_tenant_by_id(session, context, tenant_id)
    return TenantResponse(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        isolation_mode=tenant.isolation_mode,
    )


@router.get(
    "/memberships",
    response_model=list[MembershipResponse],
    summary="List memberships of the caller's organization",
)
async def list_memberships(
    context: TenantContextDep, session: TenantSession
) -> list[MembershipResponse]:
    memberships = await _tenants.list_memberships(session, context)
    return [
        MembershipResponse(
            id=item.id,
            user_id=item.user_id,
            email=item.email,
            display_name=item.display_name,
            role_code=item.role_code,
            status=item.status,
        )
        for item in memberships
    ]


@router.get(
    "/audit-events",
    response_model=list[AuditEventResponse],
    summary="List the caller's organization's audit trail",
)
async def list_audit_events(
    context: TenantContextDep,
    session: TenantSession,
    limit: int = 50,
) -> list[AuditEventResponse]:
    """Return recent audit events for the caller's tenant.

    The audit trail is a product feature, not merely an operational log — a
    tenant admin can read their own organization's governance history
    (ADR-014 §5). RLS scopes the rows; no ``tenant_id`` filter is needed and
    none would help if the session were wrong.
    """
    context.require(Capability.AUDIT_READ)

    events = (
        await session.execute(
            select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(min(max(limit, 1), 200))
        )
    ).scalars()

    return [
        AuditEventResponse(
            id=event.id,
            seq=event.seq,
            occurred_at=event.occurred_at,
            actor_type=event.actor_type,
            actor_user_id=event.actor_user_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            trace_id=event.trace_id,
        )
        for event in events
    ]
