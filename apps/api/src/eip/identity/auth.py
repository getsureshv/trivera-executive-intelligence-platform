"""Authentication and tenant-context resolution (ADR-010).

The single rule this module exists to enforce:

    **Tenant context is derived from the authenticated principal's verified
    membership. A browser-supplied tenant identifier is, at most, a request.**

An access token may carry a ``tid`` claim naming the tenant the user wants to
operate in — a user with several memberships has to say which one. That claim is
never trusted on its own: ``resolve_context`` looks up the membership row and
refuses if it does not exist or is not active. An ``X-Tenant-Id`` header is
ignored entirely, and there is a test asserting so
(``tests/security/test_tenant_isolation.py``).

Phase 1A verifies HS256 tokens minted by the local development issuer. Nothing
here stores or checks a password — the platform is not an identity provider
(ADR-010 §1). Production verification swaps key resolution for the tenant's
OIDC JWKS and leaves the rest of this pipeline byte-for-byte identical, which is
the point of writing it this way now.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.identity.models import AppUser, Membership, Role, RoleCapability, Tenant
from eip.identity.oidc import TokenVerifier, VerifiedToken
from eip.platform.context import (
    ROLE_CAPABILITIES,
    ActorType,
    Capability,
    Principal,
    RoleCode,
    TenantContext,
)
from eip.platform.db import principal_session, unscoped_session
from eip.platform.errors import ConfigurationError, ForbiddenError, UnauthenticatedError
from eip.platform.logging import get_logger
from eip.platform.settings import Settings

_log = get_logger("identity.auth")

_ALGORITHM: Final = "HS256"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Cryptographically verified claims — not merely decoded ones."""

    subject: str
    issuer: str
    requested_tenant_id: uuid.UUID | None
    expires_at: datetime


def issue_dev_token(
    settings: Settings,
    *,
    subject: str,
    tenant_id: uuid.UUID | None,
) -> tuple[str, int]:
    """Mint a short-lived development token.

    Guarded twice, because a token issuer reachable in production would be a
    complete authentication bypass: the router is not registered outside
    ``local``/``ci``, *and* this function refuses. Defence in depth is
    warranted for a control whose failure mode is total.
    """
    if not settings.env.allows_dev_auth:
        msg = (
            f"The development token issuer is not available in environment "
            f"{settings.env.value!r}. Authentication must be delegated to the tenant's "
            "OIDC provider (ADR-010)."
        )
        raise ConfigurationError(msg)

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.auth_access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": settings.auth_issuer,
        "aud": settings.auth_audience,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if tenant_id is not None:
        # A *request* for a tenant. resolve_context verifies membership before
        # this claim has any effect whatsoever.
        payload["tid"] = str(tenant_id)

    token = jwt.encode(
        payload,
        settings.auth_dev_signing_secret.get_secret_value(),
        algorithm=_ALGORITHM,
    )
    return token, settings.auth_access_token_ttl_seconds


def _to_claims(verified: VerifiedToken) -> TokenClaims:
    raw_tid = verified.requested_tenant_id
    requested_tenant_id: uuid.UUID | None = None
    if raw_tid is not None:
        try:
            requested_tenant_id = uuid.UUID(raw_tid)
        except (ValueError, AttributeError, TypeError) as exc:
            raise UnauthenticatedError("The access token is not valid.") from exc

    return TokenClaims(
        subject=verified.subject,
        issuer=verified.issuer,
        requested_tenant_id=requested_tenant_id,
        expires_at=datetime.fromtimestamp(float(verified.claims["exp"]), tz=UTC),
    )


async def _load_principal(session: AsyncSession, claims: TokenClaims) -> Principal:
    """Resolve a verified subject to a platform user.

    Runs on an *unscoped* session because no tenant is known yet. That is safe
    by construction: ``app_user`` is a global table, and every tenant-scoped
    table returns zero rows while ``app.tenant_id`` is unset.
    """
    user = (
        await session.execute(
            select(AppUser).where(
                AppUser.issuer == claims.issuer,
                AppUser.external_subject == claims.subject,
                AppUser.status == "active",
            )
        )
    ).scalar_one_or_none()

    if user is None:
        # An authenticated-but-unknown subject. Logged for detection; the
        # caller sees the same message as a bad signature.
        _log.warning("auth.unknown_subject", issuer=claims.issuer)
        raise UnauthenticatedError("The access token is not valid.")

    return Principal(
        user_id=user.id,
        external_subject=user.external_subject,
        email=user.email,
        actor_type=ActorType.USER,
    )


async def _load_capabilities(session: AsyncSession, role_code: str) -> frozenset[Capability]:
    """Load a role's capabilities.

    The database catalog is authoritative at runtime so that tenant-composed
    roles work without a deployment; ``ROLE_CAPABILITIES`` in code is the seed
    and the fallback. Unknown capability strings are dropped rather than
    guessed at — an unrecognised grant must never widen access.
    """
    rows = (
        await session.execute(
            select(RoleCapability.capability)
            .join(Role, Role.code == RoleCapability.role_code)
            .where(Role.code == role_code)
        )
    ).scalars()

    known = {item.value for item in Capability}
    capabilities = {Capability(value) for value in rows if value in known}
    if capabilities:
        return frozenset(capabilities)

    try:
        return ROLE_CAPABILITIES.get(RoleCode(role_code), frozenset())
    except ValueError:
        return frozenset()


async def resolve_context(
    *,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    verifier: TokenVerifier,
    token: str,
    trace_id: str,
    request_id: str,
) -> TenantContext:
    """Turn a bearer token into a verified ``TenantContext``.

    The order is the security property and is enforced here:

    1. verify the token cryptographically;
    2. resolve the subject to a platform user;
    3. **look up the membership rows** for that user — the point at which a
       browser-supplied tenant id is either substantiated or rejected;
    4. build the context from the *membership row*, never from the token.

    A user with exactly one active membership need not name a tenant. A user
    with several must, because silently choosing one would pick an
    authorization scope on their behalf. A user with none is refused:
    authenticated is not authorized.
    """
    claims = _to_claims(await verifier.verify(token))

    async with unscoped_session(factory) as session:
        principal = await _load_principal(session, claims)

    # A second, user-scoped transaction. `app.user_id` is set, `app.tenant_id`
    # is not, so the only readable rows are this principal's own memberships
    # (policy `membership_self_select`). Notably this does NOT use the
    # privileged BYPASSRLS role — sign-in must not run with isolation disabled.
    async with principal_session(factory, principal.user_id) as session:
        memberships = (
            await session.execute(
                select(Membership.tenant_id, Membership.role_code)
                .where(
                    Membership.user_id == principal.user_id,
                    Membership.status == "active",
                )
                .order_by(Membership.created_at)
            )
        ).all()

        if not memberships:
            _log.warning("auth.no_membership", principal_id=str(principal.user_id))
            raise ForbiddenError("This account is not a member of any organization.")

        available: dict[uuid.UUID, str] = {row.tenant_id: row.role_code for row in memberships}

        if claims.requested_tenant_id is not None:
            # THE CHECK. A token naming a tenant the user does not belong to is
            # rejected here, before any tenant-scoped query is ever compiled.
            if claims.requested_tenant_id not in available:
                _log.warning(
                    "auth.tenant_not_permitted",
                    principal_id=str(principal.user_id),
                    requested_tenant_id=str(claims.requested_tenant_id),
                )
                raise ForbiddenError("You do not have access to the requested organization.")
            tenant_id = claims.requested_tenant_id
        elif len(available) == 1:
            tenant_id = next(iter(available))
        else:
            raise ForbiddenError(
                "This account belongs to multiple organizations; the access token must name one."
            )

        role_code = available[tenant_id]
        capabilities = await _load_capabilities(session, role_code)

    # The tenant row is global, so this read needs no tenant setting. It runs
    # after membership was proven, so it cannot be used to probe tenant
    # existence.
    async with unscoped_session(factory) as session:
        slug = (
            await session.execute(select(Tenant.slug).where(Tenant.id == tenant_id))
        ).scalar_one()

    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug=slug,
        principal=principal,
        role=RoleCode(role_code),
        capabilities=capabilities,
        trace_id=trace_id,
        request_id=request_id,
    )
