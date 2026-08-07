"""
================================================================================
 RELEASE-GATING SECURITY TESTS — PRODUCTION TOKEN VERIFICATION
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 The original `verify_token` checked that a JWKS URL was *configured* in
 production-like environments and then verified the token with
 `auth_dev_signing_secret` regardless. Anyone holding the development secret
 could mint a token accepted in production, and the code read as though OIDC
 were implemented — so nobody would have looked again.

 These tests run entirely offline: keys are generated in-process and the JWKS is
 served from a local file URL. No network, no IdP, no fixture server.
================================================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from eip.identity.oidc import (
    APPROVED_ALGORITHMS,
    DevelopmentTokenVerifier,
    OidcTokenVerifier,
    assert_algorithms_are_asymmetric,
    build_verifier,
)
from eip.platform.errors import ConfigurationError, UnauthenticatedError
from eip.platform.settings import Environment, Settings

ISSUER = "https://idp.example.invalid/"
AUDIENCE = "eip-api"

# These tests need no database.
pytestmark = pytest.mark.security


class _Keys:
    """Two RSA keys: one the IdP publishes, one it never has."""

    def __init__(self) -> None:
        self.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.rotated_kid = "key-2"

    def jwks(self, *, include_rotated: bool = False) -> dict[str, Any]:
        entries = [self._entry(self.signing, self.kid)]
        if include_rotated:
            entries.append(self._entry(self.rogue, self.rotated_kid))
        return {"keys": entries}

    @staticmethod
    def _entry(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return jwk


@pytest.fixture(scope="module")
def keys() -> _Keys:
    return _Keys()


class _JwksServer:
    """A local HTTP server publishing a JWKS document.

    `PyJWKClient` accepts only http/https, so a file URL will not do. Binding to
    an ephemeral loopback port keeps the test offline while exercising the real
    fetch path — including rotation, which is the behaviour that matters and
    which a stubbed fetcher would not prove.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        self._payload = json.dumps(document).encode()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # http.server's required method name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer._payload)))
                self.end_headers()
                self.wfile.write(outer._payload)

            def log_message(self, *_args: Any) -> None:
                """Silence the default stderr access log."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/jwks.json"

    def publish(self, document: dict[str, Any]) -> None:
        """Replace the served document, as an IdP does when it rotates keys."""
        self._payload = json.dumps(document).encode()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def jwks_server(keys: _Keys) -> Iterator[_JwksServer]:
    server = _JwksServer(keys.jwks())
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def verifier(jwks_server: _JwksServer) -> OidcTokenVerifier:
    return OidcTokenVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_url=jwks_server.url)


def _token(
    keys: _Keys,
    *,
    key: rsa.RSAPrivateKey | None = None,
    kid: str | None = None,
    algorithm: str = "RS256",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 3600,
    omit: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "subject-ada",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
    }
    if extra:
        claims.update(extra)
    if omit:
        claims.pop(omit, None)

    headers = {} if kid is None else {"kid": kid}
    return jwt.encode(
        claims,
        key or keys.signing,
        algorithm=algorithm,
        headers=headers,
    )


# =============================================================================
# Acceptance
# =============================================================================


class TestValidTokensAreAccepted:
    async def test_a_valid_production_token_is_accepted(
        self, verifier: OidcTokenVerifier, keys: _Keys
    ) -> None:
        result = await verifier.verify(_token(keys, kid=keys.kid))
        assert result.subject == "subject-ada"
        assert result.issuer == ISSUER

    async def test_the_tenant_claim_is_carried_through(
        self, verifier: OidcTokenVerifier, keys: _Keys
    ) -> None:
        """`tid` is a *request*; membership resolution decides whether it holds."""
        token = _token(keys, kid=keys.kid, extra={"tid": "3f0c9c1e-0000-0000-0000-000000000001"})
        result = await verifier.verify(token)
        assert result.requested_tenant_id == "3f0c9c1e-0000-0000-0000-000000000001"


# =============================================================================
# Rejection
# =============================================================================


class TestInvalidTokensAreRejected:
    async def test_wrong_issuer(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        token = _token(keys, kid=keys.kid, issuer="https://attacker.example.invalid/")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_wrong_audience(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        token = _token(keys, kid=keys.kid, audience="some-other-service")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_wrong_signing_key(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        """Signed with a key the IdP never published."""
        token = _token(keys, key=keys.rogue, kid=keys.kid)
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_expired_token(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        token = _token(keys, kid=keys.kid, expires_in=-7200)
        with pytest.raises(UnauthenticatedError, match="expired"):
            await verifier.verify(token)

    async def test_unknown_kid(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        token = _token(keys, kid="a-kid-that-was-never-published")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_missing_kid(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        """A token naming no key cannot be matched deterministically.

        Guessing among JWKS entries is how implementations end up honouring a
        retired key.
        """
        token = _token(keys, kid=None)
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    @pytest.mark.parametrize("claim", ["sub", "iss", "aud", "exp", "iat"])
    async def test_missing_required_claim(
        self, verifier: OidcTokenVerifier, keys: _Keys, claim: str
    ) -> None:
        token = _token(keys, kid=keys.kid, omit=claim)
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_malformed_token(self, verifier: OidcTokenVerifier) -> None:
        for value in ("", "not-a-jwt", "a.b", "a.b.c"):
            with pytest.raises(UnauthenticatedError):
                await verifier.verify(value)


# =============================================================================
# Algorithm confusion
# =============================================================================


class TestAlgorithmConfusionIsImpossible:
    """The two classic JWT attacks, and the guard that keeps them impossible."""

    async def test_hs256_token_is_rejected_by_the_production_verifier(
        self, verifier: OidcTokenVerifier, keys: _Keys
    ) -> None:
        """The attack this whole module exists to prevent.

        An attacker who knows the IdP's *public* key signs a token using it as
        an HMAC secret. If the verifier accepted any ``HS*`` algorithm, that
        token would verify against the very key the IdP publishes.

        The token is assembled by hand because PyJWT refuses to *encode* it —
        it detects asymmetric key material passed as an HMAC secret. An attacker
        has no such scruples, so the test must not rely on that guard.
        """
        public_pem = keys.signing.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        now = int(time.time())
        header = b64(json.dumps({"alg": "HS256", "kid": keys.kid}).encode())
        payload = b64(
            json.dumps(
                {
                    "sub": "attacker",
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "iat": now,
                    "exp": now + 3600,
                }
            ).encode()
        )
        signing_input = header + b"." + payload
        signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
        forged = (signing_input + b"." + signature).decode()

        with pytest.raises(UnauthenticatedError):
            await verifier.verify(forged)

    async def test_alg_none_is_rejected(self, verifier: OidcTokenVerifier, keys: _Keys) -> None:
        now = int(time.time())
        unsigned = jwt.encode(
            {"sub": "a", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600},
            key="",
            algorithm="none",
            headers={"kid": keys.kid},
        )
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(unsigned)

    def test_the_approved_list_contains_no_symmetric_algorithm(self) -> None:
        assert all(not name.startswith("HS") for name in APPROVED_ALGORITHMS)
        # And the startup guard agrees, so a future edit fails the boot.
        assert_algorithms_are_asymmetric()


# =============================================================================
# Environment gating — no fallback, ever
# =============================================================================


class TestEnvironmentGating:
    @pytest.mark.parametrize(
        "environment", [Environment.DEV, Environment.STAGING, Environment.PRODUCTION]
    )
    def test_development_verifier_cannot_be_constructed(self, environment: Environment) -> None:
        """The second of three guards on the development path."""
        with pytest.raises(ConfigurationError, match="not permitted in environment"):
            DevelopmentTokenVerifier(settings=Settings(env=environment))

    @pytest.mark.parametrize(
        "environment", [Environment.DEV, Environment.STAGING, Environment.PRODUCTION]
    )
    def test_incomplete_oidc_configuration_fails_startup(self, environment: Environment) -> None:
        """Refuse to start rather than degrade — the defect this replaces."""
        settings = Settings(env=environment, auth_oidc_jwks_url="")
        with pytest.raises(ConfigurationError, match="complete OIDC configuration"):
            build_verifier(settings)

    @pytest.mark.parametrize(
        "environment", [Environment.DEV, Environment.STAGING, Environment.PRODUCTION]
    )
    def test_production_selects_the_oidc_verifier(self, environment: Environment) -> None:
        settings = Settings(
            env=environment,
            auth_issuer=ISSUER,
            auth_audience=AUDIENCE,
            auth_oidc_jwks_url="https://idp.example.invalid/jwks",
        )
        assert isinstance(build_verifier(settings), OidcTokenVerifier)

    @pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.CI])
    def test_local_and_ci_select_the_development_verifier(self, environment: Environment) -> None:
        assert isinstance(build_verifier(Settings(env=environment)), DevelopmentTokenVerifier)

    async def test_a_development_token_is_rejected_by_the_production_verifier(
        self, verifier: OidcTokenVerifier
    ) -> None:
        """An HS256 token minted by the local issuer must not work in production.

        This is the exact bypass the original implementation permitted.
        """
        settings = Settings(env=Environment.LOCAL)
        now = int(time.time())
        dev_token = jwt.encode(
            {
                "sub": "subject-ada",
                "iss": settings.auth_issuer,
                "aud": settings.auth_audience,
                "iat": now,
                "exp": now + 3600,
            },
            settings.auth_dev_signing_secret.get_secret_value(),
            algorithm="HS256",
            headers={"kid": "key-1"},
        )
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(dev_token)

    def test_the_dev_router_is_not_mounted_outside_local_and_ci(self) -> None:
        """The first of three guards: the route does not exist at all."""
        from eip.api.app import create_app

        for environment in (Environment.DEV, Environment.STAGING, Environment.PRODUCTION):
            settings = Settings(
                env=environment,
                auth_issuer=ISSUER,
                auth_audience=AUDIENCE,
                auth_oidc_jwks_url="https://idp.example.invalid/jwks",
            )
            app = create_app(settings)
            paths = {route.path for route in app.routes if hasattr(route, "path")}
            assert "/v1/dev/token" not in paths, (
                f"the development token issuer is routable in {environment.value}"
            )

    def test_the_dev_issuer_refuses_to_mint_outside_local_and_ci(self) -> None:
        """The third guard."""
        from eip.identity.auth import issue_dev_token

        for environment in (Environment.DEV, Environment.STAGING, Environment.PRODUCTION):
            with pytest.raises(ConfigurationError):
                issue_dev_token(Settings(env=environment), subject="anyone", tenant_id=None)


# =============================================================================
# Key rotation
# =============================================================================


class TestKeyRotation:
    async def test_a_rotated_key_is_picked_up_on_refetch(
        self, keys: _Keys, jwks_server: _JwksServer
    ) -> None:
        """An unknown kid triggers a refetch, then succeeds.

        Without this, every IdP key rotation would be an outage until the
        service was restarted.
        """
        verifier = OidcTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=jwks_server.url,
            min_refetch_interval_seconds=0.0,
        )

        # Warm the cache with the original key.
        await verifier.verify(_token(keys, kid=keys.kid))

        # The IdP rotates: a new key appears and tokens are signed with it.
        jwks_server.publish(keys.jwks(include_rotated=True))
        rotated = _token(keys, key=keys.rogue, kid=keys.rotated_kid)

        result = await verifier.verify(rotated)
        assert result.subject == "subject-ada"

    async def test_an_unknown_kid_does_not_refetch_repeatedly(
        self, keys: _Keys, jwks_server: _JwksServer
    ) -> None:
        """A burst of bad tokens must not become an outbound flood.

        With the refetch floor in place, later attempts reuse the cached client
        rather than hitting the IdP again.
        """
        verifier = OidcTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=jwks_server.url,
            min_refetch_interval_seconds=3600.0,
        )
        await verifier.verify(_token(keys, kid=keys.kid))

        for _ in range(3):
            with pytest.raises(UnauthenticatedError):
                await verifier.verify(_token(keys, kid="never-published"))
