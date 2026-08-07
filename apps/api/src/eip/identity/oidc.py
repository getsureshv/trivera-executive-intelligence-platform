"""OIDC token verification (ADR-010 §1).

The Phase 1A review found that ``verify_token`` checked a JWKS URL was
*configured* in production-like environments and then verified the token with
``auth_dev_signing_secret`` anyway. That is worse than having no production path
at all: it reads as implemented, so nobody looks again, while any holder of the
development secret can mint a valid production token.

This module is the real adapter. Two verifiers exist and they are selected by
environment, not by fallback:

``OidcTokenVerifier``       ``dev`` | ``staging`` | ``production``
                            Asymmetric signatures only, keys from the tenant
                            IdP's JWKS.
``DevelopmentTokenVerifier`` ``local`` | ``ci`` only.
                            HS256 with a local secret.

There is no code path in which a production-like environment falls back to the
development verifier. ``build_verifier`` raises rather than degrade, and the
process refuses to start (``eip.api.app`` calls it during lifespan).

Design notes worth stating explicitly:

* **Algorithms are an allowlist, and symmetric algorithms are absent.** Passing
  the token's own ``alg`` is the classic confusion attack; permitting ``HS*``
  alongside ``RS*`` is the second variant, where an attacker signs with the
  public key as an HMAC secret. Neither is possible here.
* **``kid`` is required.** A token whose header names no key, or names an
  unknown one, is rejected. Unknown ``kid`` triggers exactly one JWKS refetch
  (rate-limited) so that key rotation heals automatically; a second miss is a
  rejection, not a retry storm.
* **Discovery is cached with a TTL and a floor on refetches.** An IdP outage
  must not take authentication down while cached keys remain valid, and a burst
  of tokens carrying an unknown ``kid`` must not become an outbound flood.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx
import jwt
from jwt import PyJWKClient
from jwt.algorithms import get_default_algorithms

from eip.platform.errors import ConfigurationError, UnauthenticatedError
from eip.platform.logging import get_logger
from eip.platform.settings import Settings

_log = get_logger("identity.oidc")

#: Asymmetric algorithms we accept. Deliberately no ``HS*``: with a symmetric
#: algorithm in this list, an attacker who knows the IdP's *public* key could
#: sign a token using it as an HMAC secret and we would verify it.
APPROVED_ALGORITHMS: Final[tuple[str, ...]] = ("RS256", "RS384", "RS512", "ES256", "ES384")

#: Claims that must be present. ``sub`` identifies the principal; the rest bound
#: the token in time and audience. A token missing any of them is malformed for
#: our purposes even if the IdP considered it valid.
REQUIRED_CLAIMS: Final[tuple[str, ...]] = ("sub", "iss", "aud", "exp", "iat")

#: Clock skew tolerated on ``exp``/``iat``. Small on purpose: a generous leeway
#: silently extends the life of every revoked token.
LEEWAY_SECONDS: Final = 60

_DISCOVERY_SUFFIX: Final = "/.well-known/openid-configuration"


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    """A cryptographically verified token."""

    subject: str
    issuer: str
    claims: dict[str, Any]

    @property
    def requested_tenant_id(self) -> str | None:
        value = self.claims.get("tid")
        return str(value) if value is not None else None


class TokenVerifier(Protocol):
    """The port. ``resolve_context`` depends on this, never on an algorithm."""

    async def verify(self, token: str) -> VerifiedToken: ...


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------


@dataclass
class _JwksCache:
    """Cached signing keys with a TTL and a refetch floor."""

    ttl_seconds: float
    min_refetch_interval: float
    client: PyJWKClient | None = None
    fetched_at: float = 0.0
    last_refetch_attempt: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_stale(self, now: float) -> bool:
        return self.client is None or (now - self.fetched_at) > self.ttl_seconds

    def may_refetch(self, now: float) -> bool:
        return (now - self.last_refetch_attempt) >= self.min_refetch_interval


class OidcTokenVerifier:
    """Verify tokens against an OIDC provider's published keys.

    ``PyJWKClient`` maintains its own small key cache; the wrapper around it
    adds the TTL, the rotation-triggered refetch, and the rate limit — the parts
    that decide how the system behaves when an IdP rotates keys or goes down.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        cache_ttl_seconds: float = 3600.0,
        min_refetch_interval_seconds: float = 30.0,
    ) -> None:
        if not issuer or not audience or not jwks_url:
            msg = "OidcTokenVerifier requires issuer, audience, and jwks_url."
            raise ConfigurationError(msg)
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._cache = _JwksCache(
            ttl_seconds=cache_ttl_seconds,
            min_refetch_interval=min_refetch_interval_seconds,
        )

    async def _client(self, *, force_refresh: bool = False) -> PyJWKClient:
        now = time.monotonic()
        async with self._cache.lock:
            if force_refresh and not self._cache.may_refetch(now):
                # Rate-limited. A burst of tokens with an unknown kid must not
                # become an outbound request flood against the IdP.
                if self._cache.client is None:
                    msg = "JWKS unavailable and refetch is rate-limited."
                    raise UnauthenticatedError(msg)
                return self._cache.client

            if force_refresh or self._cache.is_stale(now):
                self._cache.last_refetch_attempt = now
                # PyJWKClient fetches synchronously; run it off the event loop.
                # A brand-new client each time the wrapper decides the keys are
                # stale, so its internal cache starts empty and the TTL policy
                # lives in one place rather than being split between two layers.
                client = await asyncio.to_thread(
                    PyJWKClient,
                    self._jwks_url,
                    cache_keys=True,
                    lifespan=max(1, int(self._cache.ttl_seconds)),
                )
                self._cache.client = client
                self._cache.fetched_at = now
                _log.info("oidc.jwks_fetched", jwks_url=self._jwks_url)

            assert self._cache.client is not None
            return self._cache.client

    async def _signing_key(self, token: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise UnauthenticatedError("The access token is not valid.") from exc

        # A token with no `kid` cannot be matched to a key deterministically.
        # Guessing among the JWKS entries is how implementations end up
        # accepting tokens signed by a retired key.
        if not header.get("kid"):
            _log.warning("oidc.missing_kid")
            raise UnauthenticatedError("The access token is not valid.")

        # Reject a disallowed algorithm before touching the network, so a bad
        # `alg` cannot be used to probe the IdP.
        if header.get("alg") not in APPROVED_ALGORITHMS:
            _log.warning("oidc.algorithm_rejected", alg=str(header.get("alg")))
            raise UnauthenticatedError("The access token is not valid.")

        client = await self._client()
        try:
            return await asyncio.to_thread(client.get_signing_key_from_jwt, token)
        except (jwt.PyJWKClientError, jwt.InvalidTokenError, httpx.HTTPError):
            # Most likely a rotated key. Refetch once, then give up.
            _log.info("oidc.kid_unknown_refetching", kid=str(header.get("kid")))
            client = await self._client(force_refresh=True)
            try:
                return await asyncio.to_thread(client.get_signing_key_from_jwt, token)
            except Exception as exc:
                _log.warning("oidc.kid_unknown", kid=str(header.get("kid")))
                raise UnauthenticatedError("The access token is not valid.") from exc

    async def verify(self, token: str) -> VerifiedToken:
        signing_key = await self._signing_key(token)

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                # Pinned. Never the token's own `alg`, and never any HS*.
                algorithms=list(APPROVED_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthenticatedError("The access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            # Uniform message: never disclose *why* verification failed.
            raise UnauthenticatedError("The access token is not valid.") from exc

        return VerifiedToken(subject=str(claims["sub"]), issuer=str(claims["iss"]), claims=claims)


async def discover_jwks_url(issuer: str, *, http_timeout: float = 5.0) -> str:
    """Resolve an issuer to its JWKS URL via OIDC discovery.

    Used at startup so a misconfigured issuer fails the boot rather than every
    subsequent sign-in.
    """
    url = issuer.rstrip("/") + _DISCOVERY_SUFFIX
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = response.json()

    jwks_uri = document.get("jwks_uri")
    if not jwks_uri:
        msg = f"OIDC discovery document at {url} has no jwks_uri."
        raise ConfigurationError(msg)
    return str(jwks_uri)


# ---------------------------------------------------------------------------
# development
# ---------------------------------------------------------------------------


class DevelopmentTokenVerifier:
    """HS256 verification for ``local`` and ``ci`` only.

    Constructing one outside those environments raises. That is the second of
    three guards on the development authentication path — the router is not
    mounted, this class refuses to exist, and ``issue_dev_token`` refuses to
    mint. A single guard on a control whose failure is total authentication
    bypass is not enough.
    """

    ALGORITHM: Final = "HS256"

    def __init__(self, *, settings: Settings) -> None:
        if not settings.env.allows_dev_auth:
            msg = (
                f"DevelopmentTokenVerifier is not permitted in environment "
                f"{settings.env.value!r}. Authentication must be delegated to the tenant's "
                "OIDC provider (ADR-010 §1)."
            )
            raise ConfigurationError(msg)
        self._settings = settings

    async def verify(self, token: str) -> VerifiedToken:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._settings.auth_dev_signing_secret.get_secret_value(),
                algorithms=[self.ALGORITHM],
                audience=self._settings.auth_audience,
                issuer=self._settings.auth_issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthenticatedError("The access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthenticatedError("The access token is not valid.") from exc

        return VerifiedToken(subject=str(claims["sub"]), issuer=str(claims["iss"]), claims=claims)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def build_verifier(settings: Settings, *, jwks_url: str | None = None) -> TokenVerifier:
    """Select the verifier for this environment. Never falls back.

    In a production-like environment, incomplete OIDC configuration is a
    startup failure. The alternative — degrading to development verification —
    is precisely the defect this module was written to remove.
    """
    if settings.env.allows_dev_auth:
        _log.warning("oidc.development_verifier_selected", environment=settings.env.value)
        return DevelopmentTokenVerifier(settings=settings)

    missing = [
        name
        for name, value in (
            ("EIP_AUTH_ISSUER", settings.auth_issuer),
            ("EIP_AUTH_AUDIENCE", settings.auth_audience),
            ("EIP_AUTH_OIDC_JWKS_URL", jwks_url or settings.auth_oidc_jwks_url),
        )
        if not value
    ]
    if missing:
        msg = (
            f"Environment {settings.env.value!r} requires complete OIDC configuration; "
            f"missing: {', '.join(missing)}. Refusing to start rather than fall back to "
            "development token verification (ADR-010 §1)."
        )
        raise ConfigurationError(msg)

    return OidcTokenVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        jwks_url=jwks_url or settings.auth_oidc_jwks_url,
    )


def assert_algorithms_are_asymmetric() -> None:
    """Fail loudly if a symmetric algorithm ever enters the allowlist.

    Called at startup. ``APPROVED_ALGORITHMS`` is a constant, so this can only
    fire after someone edits it — which is exactly when it needs to fire.
    """
    symmetric = {
        name
        for name in APPROVED_ALGORITHMS
        if name.startswith("HS") or name not in get_default_algorithms()
    }
    if symmetric:
        msg = (
            f"Approved algorithm list contains symmetric or unknown algorithms: "
            f"{sorted(symmetric)}. A symmetric algorithm alongside asymmetric ones allows "
            "an attacker to sign tokens with the IdP's public key (ADR-010 §1)."
        )
        raise ConfigurationError(msg)
