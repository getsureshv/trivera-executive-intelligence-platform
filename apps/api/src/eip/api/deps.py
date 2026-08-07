"""FastAPI dependencies.

This module is where "authorize before data access" becomes structural rather
than aspirational (ADR-010 §3).

A route that needs tenant data declares ``session: TenantSession``. Resolving
that dependency *requires* first resolving ``TenantContext``, which requires a
verified token and a verified membership. There is no dependency that yields a
tenant-scoped session without a context, so a route physically cannot query
tenant data unauthenticated.

Privileged, cross-tenant access is a *different* dependency
(``PlatformSession``) requiring a *different* type (``PlatformContext``) on a
*different* database role. The two cannot be confused at a call site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.dataplane.interfaces import TenantDataPlane
from eip.identity.auth import resolve_context
from eip.platform.context import Capability, PlatformContext, RoleCode, TenantContext
from eip.platform.db import platform_session, tenant_session, unscoped_session
from eip.platform.errors import ForbiddenError, UnauthenticatedError
from eip.platform.logging import bind_context
from eip.platform.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_platform_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.platform_session_factory
    return factory


def get_data_plane(request: Request) -> TenantDataPlane:
    plane: TenantDataPlane = request.app.state.data_plane
    return plane


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
PlatformFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_platform_session_factory)
]
DataPlaneDep = Annotated[TenantDataPlane, Depends(get_data_plane)]


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise UnauthenticatedError("An access token is required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthenticatedError("An access token is required.")
    return token.strip()


async def get_tenant_context(
    request: Request,
    settings: SettingsDep,
    factory: SessionFactoryDep,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """Resolve the caller's verified tenant context.

    Note what is *not* a parameter: any tenant identifier from the path, query,
    body, or headers. The tenant comes from ``resolve_context``, which reads the
    membership table. That is the whole isolation model in one function
    signature (ADR-003 §3).
    """
    context = await resolve_context(
        factory=factory,
        settings=settings,
        token=_bearer_token(authorization),
        trace_id=getattr(request.state, "trace_id", ""),
        request_id=getattr(request.state, "request_id", ""),
    )
    # Enrich every subsequent log record and audit event for this request.
    request.state.tenant_context = context
    return context


TenantContextDep = Annotated[TenantContext, Depends(get_tenant_context)]


async def get_tenant_session(
    context: TenantContextDep,
    factory: SessionFactoryDep,
) -> AsyncIterator[AsyncSession]:
    """Yield a transaction scoped to the caller's tenant.

    Depends on ``TenantContextDep``, so authentication and membership
    verification have already happened by the time a session exists. The
    session sets ``app.tenant_id``, so PostgreSQL RLS backs the application's
    own filtering.
    """
    with bind_context(tenant_id=context.tenant_id, principal_id=context.principal.user_id):
        async with tenant_session(factory, context) as session:
            yield session


TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]


async def get_unscoped_session(factory: SessionFactoryDep) -> AsyncIterator[AsyncSession]:
    """Yield a session with no tenant bound, for global reads only.

    Safe by construction: with ``app.tenant_id`` unset, every tenant-scoped
    table returns zero rows. Used by the health and readiness probes.
    """
    async with unscoped_session(factory) as session:
        yield session


UnscopedSession = Annotated[AsyncSession, Depends(get_unscoped_session)]


# --- privileged path ---------------------------------------------------------


async def get_platform_context(
    request: Request,
    context: TenantContextDep,
    x_elevation_reason: Annotated[str | None, Header()] = None,
) -> PlatformContext:
    """Build a ``PlatformContext`` for a platform administrator.

    Three gates, all required (ADR-010 §5):

    1. the caller must hold ``PLATFORM_TENANT_PROVISION``, a capability granted
       only by the ``platform_admin`` role, which migration 0001 marks
       ``is_platform_role`` and which ``add_membership`` refuses to assign
       inside a tenant;
    2. an explicit elevation reason must be supplied — privileged access
       without a recorded justification is what break-glass exists to prevent;
    3. the elevation is logged at WARNING and audited by the caller.
    """
    if not context.has(Capability.PLATFORM_TENANT_PROVISION):
        raise ForbiddenError("Platform administration capability is required.")

    if context.role is not RoleCode.PLATFORM_ADMIN:  # pragma: no cover - defence in depth
        raise ForbiddenError("Platform administration capability is required.")

    reason = (x_elevation_reason or "").strip()
    if not reason:
        raise ForbiddenError(
            "Privileged operations require an X-Elevation-Reason header stating why "
            "cross-tenant access is necessary (ADR-010)."
        )

    return PlatformContext(
        principal=context.principal,
        reason=reason,
        trace_id=getattr(request.state, "trace_id", ""),
        request_id=getattr(request.state, "request_id", ""),
    )


PlatformContextDep = Annotated[PlatformContext, Depends(get_platform_context)]


async def get_platform_session(
    context: PlatformContextDep,
    factory: PlatformFactoryDep,
) -> AsyncIterator[AsyncSession]:
    """Yield a **privileged** cross-tenant transaction on the BYPASSRLS role."""
    with bind_context(principal_id=context.principal.user_id):
        async with platform_session(factory, context) as session:
            yield session


PlatformSession = Annotated[AsyncSession, Depends(get_platform_session)]


def require(capability: Capability) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    """Build a dependency asserting a capability.

    Used where a route needs a capability beyond the one implied by its
    session. Authorization stays declarative and at the boundary — never
    scattered through handlers or, worse, UI components (ADR-010 §3).
    """

    async def _dependency(
        context: TenantContextDep,
    ) -> TenantContext:
        context.require(capability)
        return context

    return _dependency
