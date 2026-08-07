"""Development-only token issuer.

    ##################################################################
    #  THIS ROUTER MUST NEVER BE REACHABLE OUTSIDE local / ci.       #
    #  It mints access tokens for any known subject, without proof   #
    #  of identity. In production it would be a total authentication #
    #  bypass.                                                       #
    ##################################################################

It is guarded three times, because a single guard on a control with this blast
radius is not enough:

1. ``create_app`` only includes this router when ``settings.env.allows_dev_auth``;
2. every handler re-checks the environment;
3. ``issue_dev_token`` itself refuses outside ``local``/``ci``.

Why it exists at all: ADR-010 delegates authentication to an OIDC provider and
forbids storing passwords. Standing up a real identity provider for local
development and CI would add a dependency for no security benefit, so instead
the API mints tokens *in the same shape* it will later verify from a real
issuer. The verification path, membership resolution, and every downstream
authorization check are byte-for-byte identical to production — only the key
source differs. That is what makes the isolation tests meaningful.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from eip.api.deps import SettingsDep, UnscopedSession
from eip.identity.auth import issue_dev_token
from eip.identity.models import AppUser
from eip.platform.errors import NotFoundError, UnauthenticatedError
from eip.platform.logging import get_logger

_log = get_logger("api.dev_auth")

router = APIRouter(prefix="/v1/dev", tags=["development"])


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    #: Which organization to act in. A *request*, not an assertion: the token
    #: pipeline verifies membership before this has any effect (ADR-003 §3).
    tenant_id: uuid.UUID | None = None


class DevTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - a scheme name, not a credential
    expires_in: int


@router.post(
    "/token",
    response_model=DevTokenResponse,
    summary="[local/ci only] Mint a development access token",
)
async def create_dev_token(
    payload: DevTokenRequest,
    settings: SettingsDep,
    session: UnscopedSession,
) -> DevTokenResponse:
    """Mint a token for a known user.

    Note that the resulting token grants nothing by itself: it merely asserts
    an identity. Whether that identity may act in the requested organization is
    decided later, by ``resolve_context``, against the membership table.
    """
    if not settings.env.allows_dev_auth:  # pragma: no cover - router is not mounted
        raise UnauthenticatedError("Not available in this environment.")

    user = (
        await session.execute(
            select(AppUser).where(AppUser.email == payload.email, AppUser.status == "active")
        )
    ).scalar_one_or_none()

    if user is None:
        raise NotFoundError("No such user.")

    token, expires_in = issue_dev_token(
        settings, subject=user.external_subject, tenant_id=payload.tenant_id
    )

    _log.warning(
        "dev_auth.token_issued",
        principal_id=str(user.id),
        requested_tenant_id=str(payload.tenant_id) if payload.tenant_id else None,
    )
    return DevTokenResponse(access_token=token, expires_in=expires_in)
