"""Identity & Tenant application services.

Two distinct surfaces, deliberately separated by *type*:

* ``TenantReadService`` takes a ``TenantContext`` and can only ever see one
  tenant. Every method requires the context as an argument, so a caller cannot
  forget to scope.
* ``PlatformAdminService`` takes a ``PlatformContext``, uses the ``BYPASSRLS``
  role, and is the only code path here that spans tenants. Its every operation
  writes an audit event.

The split is the point. If privileged administration shared a service with
reads, one mistaken parameter would turn a tenant-scoped call into a
cross-tenant one.

Tenant *provisioning* lived here until Phase 1B and now lives in
``eip.identity.provisioning``. It moved because it stopped fitting this shape:
everything here is one unit of work inside the caller's transaction, whereas
provisioning owns three transactions with DDL between them and has to record a
failure after the transaction that failed. Leaving the simpler `create_tenant`
behind would have been worse than moving it — two provisioning paths, one of
which silently cannot record a partial failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eip.governance import audit
from eip.identity.models import AppUser, Membership, Tenant
from eip.platform.context import Capability, PlatformContext, RoleCode, TenantContext
from eip.platform.errors import ConflictError, NotFoundError
from eip.platform.logging import get_logger

_log = get_logger("identity.service")


@dataclass(frozen=True, slots=True)
class TenantSummary:
    id: uuid.UUID
    slug: str
    name: str
    status: str
    isolation_mode: str


@dataclass(frozen=True, slots=True)
class MembershipSummary:
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    role_code: str
    status: str


class TenantReadService:
    """Tenant-scoped reads. Cannot express a cross-tenant query."""

    async def get_tenant(self, session: AsyncSession, context: TenantContext) -> TenantSummary:
        """Return the caller's own tenant.

        Note there is no ``tenant_id`` parameter. The tenant is whichever one
        the authenticated membership resolved to, so "fetch a different
        tenant" is not expressible through this method at all.
        """
        context.require(Capability.TENANT_READ)

        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == context.tenant_id))
        ).scalar_one_or_none()

        if tenant is None:  # pragma: no cover - implies a deleted tenant mid-request
            raise NotFoundError()

        return TenantSummary(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            isolation_mode=tenant.isolation_mode,
        )

    async def get_tenant_by_id(
        self, session: AsyncSession, context: TenantContext, tenant_id: uuid.UUID
    ) -> TenantSummary:
        """Return a tenant **named by the caller**.

        This method exists specifically so that identifier manipulation can be
        tested. It accepts an id from the request and then refuses anything
        that is not the caller's own tenant, with ``NotFoundError`` — the same
        response as a genuinely absent tenant, so the endpoint cannot be used
        to enumerate which tenants exist (ADR-010 §4).
        """
        context.require(Capability.TENANT_READ)

        if tenant_id != context.tenant_id:
            _log.warning(
                "tenant.cross_tenant_access_attempted",
                requested_tenant_id=str(tenant_id),
            )
            raise NotFoundError()

        return await self.get_tenant(session, context)

    async def list_memberships(
        self, session: AsyncSession, context: TenantContext
    ) -> list[MembershipSummary]:
        """List memberships of the caller's tenant.

        No ``tenant_id`` filter appears in this query. It does not need one:
        the session was opened by ``tenant_session``, so RLS restricts the rows
        to the active tenant. The repository *also* filters in the routes that
        join across tables — belt and braces, per ADR-003's "application
        scoping primary, RLS backstop" model. Here the absence is deliberate,
        and the isolation test proves RLS alone is sufficient.
        """
        context.require(Capability.MEMBERSHIP_READ)

        rows = (
            await session.execute(
                select(Membership, AppUser)
                .join(AppUser, AppUser.id == Membership.user_id)
                .order_by(AppUser.email)
            )
        ).all()

        return [
            MembershipSummary(
                id=membership.id,
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                role_code=membership.role_code,
                status=membership.status,
            )
            for membership, user in rows
        ]


class PlatformAdminService:
    """Privileged, cross-tenant membership administration (ADR-010 §5).

    Takes a ``PlatformContext`` — unconstructable without a recorded reason —
    runs on the ``eip_platform`` role, and audits into the target tenant's own
    chain.
    """

    async def add_membership(
        self,
        session: AsyncSession,
        context: PlatformContext,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        role: RoleCode,
    ) -> uuid.UUID:
        """Grant a user access to a tenant.

        Platform-staff roles are refused: ``platform_admin`` is not a tenant
        role and must never be assignable inside one, or a tenant admin could
        escalate to cross-tenant access (ADR-010 layer 1).
        """
        if role is RoleCode.PLATFORM_ADMIN:
            msg = "platform_admin is not assignable as a tenant membership role."
            raise ConflictError(msg)

        membership = Membership(
            tenant_id=tenant_id,
            user_id=user_id,
            role_code=role.value,
            status="active",
        )
        session.add(membership)

        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("That user is already a member of this organization.") from exc

        await audit.record_platform_action(
            session,
            context,
            tenant_id=tenant_id,
            action=audit.AuditAction.MEMBERSHIP_GRANTED,
            resource_type="membership",
            resource_id=str(membership.id),
            detail={"user_id": str(user_id), "role_code": role.value},
        )
        return membership.id
