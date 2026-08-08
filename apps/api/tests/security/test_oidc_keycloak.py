"""
================================================================================
 RELEASE-GATING SECURITY TESTS — OIDC AGAINST A REAL IDENTITY PROVIDER (G13)
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 Phase 1A verified the OIDC adapter against RSA keys generated in-process and a
 JWKS served by a local `http.server`. That proved the cryptography. It did not
 prove the *protocol*: discovery, a realm-scoped issuer, the provider's own
 `aud` behaviour, and live key rotation were all untested, and the report said
 so — gap G13, "the OIDC adapter has never run against a real IdP".

 This suite closes it. Every token here is produced by Keycloak or signed by a
 key that Keycloak genuinely publishes in its JWKS, fetched over HTTP from the
 URL its discovery document advertises.

 The distinction that makes the negative cases worth anything: a forged token
 must differ from a valid one in *exactly the property under test*. Signing a
 wrong-audience token with a key the provider never heard of proves only that
 bad signatures are rejected — which was never in doubt. So the suite registers
 its own RSA key through Keycloak's admin API, and the provider then advertises
 it as a legitimate signing key. An expired token is therefore a token that is
 perfect except for being expired.

 Start the provider with:

     docker compose -f infra/docker-compose.yml --profile oidc up -d --wait

 Absent, every test here skips rather than failing — a developer without the
 container gets a clean run, and CI runs the container.
================================================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from eip.identity.auth import issue_dev_token, resolve_context
from eip.identity.oidc import (
    DevelopmentTokenVerifier,
    OidcTokenVerifier,
    build_verifier,
    discover_jwks_url,
)
from eip.platform.errors import ConfigurationError, ForbiddenError, UnauthenticatedError
from eip.platform.settings import Environment, Settings
from tests.conftest import Fixtures

pytestmark = [pytest.mark.security, pytest.mark.integration, pytest.mark.oidc]

#: Where the provider is published. `localhost:8081` on a developer machine and
#: on the CI runner; `keycloak:8080` when the suite runs inside the compose
#: network (`docker compose run --rm api pytest ...`).
OIDC_BASE_URL = os.environ.get("EIP_TEST_OIDC_BASE_URL", "http://localhost:8081").rstrip("/")

REALM = "eip-test"
OTHER_REALM = "eip-other"
CLIENT_ID = "eip-web"
AUDIENCE = "eip-api"

#: Fixed in the imported realm JSON, so it is also the `sub` claim.
ADA_SUBJECT = "aaaaaaaa-0000-4000-8000-000000000001"
ORPHAN_SUBJECT = "aaaaaaaa-0000-4000-8000-000000000002"
#: Local-only. This realm exists to be attacked; it authenticates nothing real.
USER_PASSWORD = "local-dev-only-password"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "local_dev_only"


# =============================================================================
# talking to the provider
# =============================================================================


def _issuer(realm: str) -> str:
    return f"{OIDC_BASE_URL}/realms/{realm}"


def _pem_body(data: bytes) -> str:
    """Keycloak's key import wants the base64 body without PEM armour."""
    return "".join(line for line in data.decode().splitlines() if not line.startswith("-----"))


@dataclass(frozen=True, slots=True)
class ImportedKey:
    """An RSA key that Keycloak has been persuaded to publish as its own."""

    private_key: rsa.RSAPrivateKey
    kid: str
    component_url: str


class Keycloak:
    """A thin admin/token client. Deliberately not a general-purpose wrapper."""

    def __init__(self, client: httpx.Client) -> None:
        self._http = client

    # --- tokens ----------------------------------------------------------

    def password_grant(self, *, realm: str, username: str) -> str:
        """Obtain a **genuine** access token by direct grant."""
        response = self._http.post(
            f"{_issuer(realm)}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": username,
                "password": USER_PASSWORD,
            },
        )
        response.raise_for_status()
        token: str = response.json()["access_token"]
        return token

    def jwks(self, realm: str = REALM) -> dict[str, Any]:
        response = self._http.get(f"{_issuer(realm)}/protocol/openid-connect/certs")
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        return document

    # --- administration ---------------------------------------------------

    def _admin_headers(self) -> dict[str, str]:
        response = self._http.post(
            f"{OIDC_BASE_URL}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
            },
        )
        response.raise_for_status()
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def _realm_uuid(self, realm: str) -> str:
        """Keycloak's *internal* realm id.

        Not the realm name. A key-provider component whose ``parentId`` is the
        name is accepted with ``201 Created`` and then silently never loaded —
        no error, no key, no log line.
        """
        response = self._http.get(
            f"{OIDC_BASE_URL}/admin/realms/{realm}", headers=self._admin_headers()
        )
        response.raise_for_status()
        realm_id: str = response.json()["id"]
        return realm_id

    def _create_key_component(self, realm: str, payload: dict[str, Any]) -> str:
        payload = {**payload, "parentId": self._realm_uuid(realm)}
        response = self._http.post(
            f"{OIDC_BASE_URL}/admin/realms/{realm}/components",
            headers=self._admin_headers(),
            json=payload,
        )
        response.raise_for_status()
        location = response.headers.get("location")
        assert location, "Keycloak did not return the created component's location."
        return str(location)

    def import_signing_key(self, realm: str = REALM) -> ImportedKey:
        """Register a locally generated RSA key as a realm signing key.

        After this, Keycloak advertises the key in its JWKS exactly as it does
        its own. That is what lets a test mint a token which is genuinely
        signed by the provider's published key material and differs from a
        valid token in one property alone.
        """
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "eip-test-imported")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=365))
            .sign(key, hashes.SHA256())
        )

        component_url = self._create_key_component(
            realm,
            {
                "name": "eip-suite-imported",
                "providerId": "rsa",
                "providerType": "org.keycloak.keys.KeyProvider",
                "config": {
                    # Below the realm's own generated key, so ordinary tokens
                    # are still signed by Keycloak's key and the positive cases
                    # stay honest.
                    "priority": ["50"],
                    "enabled": ["true"],
                    "active": ["true"],
                    "algorithm": ["RS256"],
                    "keyUse": ["SIG"],
                    "privateKey": [
                        _pem_body(
                            key.private_bytes(
                                serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption(),
                            )
                        )
                    ],
                    "certificate": [
                        _pem_body(certificate.public_bytes(serialization.Encoding.PEM))
                    ],
                },
            },
        )

        modulus = key.public_key().public_numbers().n
        encoded = (
            base64.urlsafe_b64encode(modulus.to_bytes(256, "big")).rstrip(b"=").decode("ascii")
        )
        matching = [entry for entry in self.jwks(realm)["keys"] if entry.get("n") == encoded]
        assert matching, "Keycloak accepted the key component but does not publish the key."
        return ImportedKey(
            private_key=key, kid=str(matching[0]["kid"]), component_url=component_url
        )

    def rotate_signing_key(self, realm: str = REALM) -> str:
        """Force a rotation and return the component URL for cleanup.

        A new generated provider at a higher priority becomes the active
        signing key, which is how a real rotation presents itself to a relying
        party: tokens simply start arriving with an unfamiliar ``kid``.
        """
        return self._create_key_component(
            realm,
            {
                "name": "eip-suite-rotated",
                "providerId": "rsa-generated",
                "providerType": "org.keycloak.keys.KeyProvider",
                "config": {
                    "priority": ["500"],
                    "enabled": ["true"],
                    "active": ["true"],
                    "algorithm": ["RS256"],
                },
            },
        )

    def delete_component(self, component_url: str) -> None:
        self._http.delete(component_url, headers=self._admin_headers())


#: Set in CI. A suite that skips is a suite that proves nothing, and a green
#: job whose security tests never executed is the exact failure this project
#: has already been caught by once. Where the provider is supposed to exist,
#: its absence is a failure rather than a skip.
PROVIDER_REQUIRED = os.environ.get("EIP_TEST_OIDC_REQUIRED", "").lower() in ("1", "true", "yes")


def _provider_is_running() -> tuple[bool, str]:
    try:
        response = httpx.get(f"{_issuer(REALM)}/.well-known/openid-configuration", timeout=3.0)
    except httpx.HTTPError as exc:
        return False, (
            f"No OIDC provider at {OIDC_BASE_URL} ({exc.__class__.__name__}). Start it with: "
            "docker compose -f infra/docker-compose.yml --profile oidc up -d --wait"
        )
    if response.status_code != 200:
        return False, f"{OIDC_BASE_URL} answered {response.status_code} for OIDC discovery."
    return True, ""


# =============================================================================
# fixtures
# =============================================================================


@pytest.fixture(scope="session")
def provider_available() -> tuple[bool, str]:
    return _provider_is_running()


def _require_or_skip(provider_available: tuple[bool, str]) -> None:
    running, reason = provider_available
    if running:
        return
    if PROVIDER_REQUIRED:
        pytest.fail(f"EIP_TEST_OIDC_REQUIRED is set but the provider is unreachable. {reason}")
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def _skip_without_provider(provider_available: tuple[bool, str]) -> None:
    _require_or_skip(provider_available)


@pytest.fixture(scope="session")
def keycloak(provider_available: tuple[bool, str]) -> Iterator[Keycloak]:
    _require_or_skip(provider_available)
    with httpx.Client(timeout=20.0) as client:
        yield Keycloak(client)


@pytest.fixture(scope="session")
def jwks_url(keycloak: Keycloak) -> str:
    """Resolved by real discovery, not hard-coded.

    Every verifier below therefore depends on discovery having worked, which
    makes `TestDiscovery` a precondition of the whole suite rather than one
    isolated assertion.
    """
    import asyncio

    return asyncio.run(discover_jwks_url(_issuer(REALM)))


@pytest.fixture(scope="session")
def signing_key(keycloak: Keycloak) -> Iterator[ImportedKey]:
    imported = keycloak.import_signing_key()
    try:
        yield imported
    finally:
        keycloak.delete_component(imported.component_url)


@pytest.fixture
def verifier(jwks_url: str) -> OidcTokenVerifier:
    """The real adapter, pointed at the real provider.

    ``min_refetch_interval_seconds=0`` because the rotation test needs the
    unknown-``kid`` refetch to be permitted immediately; the rate limit itself
    is covered by the offline suite.
    """
    return OidcTokenVerifier(
        issuer=_issuer(REALM),
        audience=AUDIENCE,
        jwks_url=jwks_url,
        cache_ttl_seconds=3600.0,
        min_refetch_interval_seconds=0.0,
    )


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": ADA_SUBJECT,
        "iss": _issuer(REALM),
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    return payload


def _mint(key: ImportedKey, **overrides: Any) -> str:
    """Sign a token with the key Keycloak publishes as its own."""
    return jwt.encode(
        _claims(**overrides),
        key.private_key,
        algorithm="RS256",
        headers={"kid": key.kid},
    )


# =============================================================================
# 1. discovery and key retrieval
# =============================================================================


class TestDiscoveryAndJwksRetrieval:
    """The part Phase 1A could not test: the provider is really out there."""

    async def test_discovery_resolves_the_issuer_to_its_jwks_uri(self) -> None:
        resolved = await discover_jwks_url(_issuer(REALM))
        assert resolved == f"{_issuer(REALM)}/protocol/openid-connect/certs"

    async def test_discovery_of_an_unknown_issuer_fails_loudly(self) -> None:
        """A typo in the issuer must break the boot, not degrade the check."""
        with pytest.raises(httpx.HTTPError):
            await discover_jwks_url(f"{OIDC_BASE_URL}/realms/no-such-realm")

    def test_the_published_jwks_contains_a_usable_signing_key(self, keycloak: Keycloak) -> None:
        signing = [
            entry
            for entry in keycloak.jwks()["keys"]
            if entry.get("use") == "sig" and entry.get("kty") == "RSA"
        ]
        assert signing, "The provider publishes no RSA signing key."
        assert all(entry.get("kid") for entry in signing)
        assert all(entry.get("alg", "RS256").startswith(("RS", "ES")) for entry in signing)


# =============================================================================
# 2. genuine tokens are accepted
# =============================================================================


class TestGenuineTokensAreAccepted:
    async def test_a_token_issued_by_the_provider_is_accepted(
        self, keycloak: Keycloak, verifier: OidcTokenVerifier
    ) -> None:
        token = keycloak.password_grant(realm=REALM, username="ada")
        verified = await verifier.verify(token)

        assert verified.subject == ADA_SUBJECT
        assert verified.issuer == _issuer(REALM)

    async def test_the_production_selection_path_accepts_it_too(
        self, keycloak: Keycloak, jwks_url: str
    ) -> None:
        """`build_verifier` in production, wired to a real provider.

        The positive control for every "no fallback" assertion below: the
        production path is not merely strict, it *works*.
        """
        settings = Settings(
            env=Environment.PRODUCTION,
            auth_issuer=_issuer(REALM),
            auth_audience=AUDIENCE,
            auth_oidc_jwks_url=jwks_url,
        )
        selected = build_verifier(settings)
        assert isinstance(selected, OidcTokenVerifier)

        token = keycloak.password_grant(realm=REALM, username="ada")
        assert (await selected.verify(token)).subject == ADA_SUBJECT

    async def test_a_token_signed_by_the_imported_key_is_accepted(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        """The negative control for the whole forgery section.

        Every rejection below is minted the same way. If this passed for the
        wrong reason — the key not actually being published, say — the
        rejections would all be false passes.
        """
        assert (await verifier.verify(_mint(signing_key))).subject == ADA_SUBJECT


# =============================================================================
# 3. everything else is rejected
# =============================================================================


class TestInvalidTokensAreRejected:
    """Each token below is signed by a key the provider genuinely publishes.

    Only the named property differs from a token that was just accepted.
    """

    async def test_wrong_issuer(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        token = _mint(signing_key, iss="https://impostor.invalid/")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_a_genuine_token_from_a_different_provider(
        self, keycloak: Keycloak, verifier: OidcTokenVerifier
    ) -> None:
        """The realistic form of the issuer attack.

        `eip-other` is a real realm with real keys at a real JWKS endpoint. Its
        tokens are valid — for it. Controlling *an* identity provider must not
        confer control of this one.
        """
        foreign = keycloak.password_grant(realm=OTHER_REALM, username="mallory")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(foreign)

    async def test_wrong_audience(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        """A token minted for another relying party of the same provider.

        This is why the realm carries an audience mapper: without it Keycloak
        issues `aud: account` and the check could not be exercised at all.
        """
        token = _mint(signing_key, aud="some-other-service")
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_wrong_signing_key(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        """A rogue key, presented under a `kid` the provider does publish."""
        rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(_claims(), rogue, algorithm="RS256", headers={"kid": signing_key.kid})
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_expired_token(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        now = int(time.time())
        # Comfortably outside LEEWAY_SECONDS, so this is expiry and not skew.
        token = _mint(signing_key, iat=now - 7200, exp=now - 3600)
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_unknown_kid(self, verifier: OidcTokenVerifier, signing_key: ImportedKey) -> None:
        """Signed by a published key, but naming a `kid` that does not exist.

        Reaches the rotation path — one refetch, then a refusal. A verifier
        that fell back to "try every key in the JWKS" would accept this.
        """
        token = jwt.encode(
            _claims(),
            signing_key.private_key,
            algorithm="RS256",
            headers={"kid": "not-a-key-this-provider-has"},
        )
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_missing_kid(self, verifier: OidcTokenVerifier, signing_key: ImportedKey) -> None:
        token = jwt.encode(_claims(), signing_key.private_key, algorithm="RS256", headers={})
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    async def test_unsupported_algorithm_hs256_confusion(
        self, keycloak: Keycloak, verifier: OidcTokenVerifier
    ) -> None:
        """The classic attack, run against the provider's real public key.

        An attacker knows the public key — it is published. If HS256 were in
        the allowlist, signing with that public key as an HMAC secret would
        forge an accepted token. PyJWT refuses to *encode* this, so the token
        is assembled by hand; refusing to build the attack would not be proof
        that the attack fails.
        """
        entry = next(
            item
            for item in keycloak.jwks()["keys"]
            if item.get("use") == "sig" and item.get("kty") == "RSA"
        )
        public_pem = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(entry)).public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": entry["kid"]}).encode())
        payload = b64(json.dumps(_claims()).encode())
        signed = header + b"." + payload
        signature = b64(hmac.new(public_pem, signed, hashlib.sha256).digest())
        forged = (signed + b"." + signature).decode()

        with pytest.raises(UnauthenticatedError):
            await verifier.verify(forged)

    async def test_unsupported_algorithm_none(
        self, verifier: OidcTokenVerifier, signing_key: ImportedKey
    ) -> None:
        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        header = b64(json.dumps({"alg": "none", "typ": "JWT", "kid": signing_key.kid}).encode())
        payload = b64(json.dumps(_claims()).encode())
        unsigned = (header + b"." + payload + b".").decode()

        with pytest.raises(UnauthenticatedError):
            await verifier.verify(unsigned)


# =============================================================================
# 4. key rotation
# =============================================================================


class TestKeyRotation:
    async def test_a_rotated_provider_key_is_picked_up_without_a_restart(
        self, keycloak: Keycloak, verifier: OidcTokenVerifier
    ) -> None:
        """Rotation must heal by itself.

        An IdP rotates on its own schedule. If the relying party needed a
        deploy to notice, every rotation would be an outage — which is how
        operators learn to stop rotating.
        """
        before = keycloak.password_grant(realm=REALM, username="ada")
        assert (await verifier.verify(before)).subject == ADA_SUBJECT
        kid_before = jwt.get_unverified_header(before)["kid"]

        rotated_component = keycloak.rotate_signing_key()
        try:
            after = keycloak.password_grant(realm=REALM, username="ada")
            kid_after = jwt.get_unverified_header(after)["kid"]

            # Without this the test could pass having rotated nothing.
            assert kid_after != kid_before, "The provider did not actually rotate its key."

            assert (await verifier.verify(after)).subject == ADA_SUBJECT
        finally:
            keycloak.delete_component(rotated_component)


# =============================================================================
# 5. tenant context still comes from membership
# =============================================================================


@pytest.fixture
async def keycloak_principals(
    platform_engine: AsyncEngine, seeded: Fixtures
) -> AsyncIterator[Fixtures]:
    """Register the Keycloak subjects as platform users.

    `seeded` creates two tenants and its own users against the development
    issuer; these rows are the same people arriving through Keycloak instead.
    Ada is a member of tenant A **only**. Orphan is a member of nothing.

    Cleanup rides on `seeded`, which clears the control plane on teardown.
    """
    async with platform_engine.begin() as conn:
        subjects = (
            (ADA_SUBJECT, "ada@acme.invalid"),
            (ORPHAN_SUBJECT, "orphan@nowhere.invalid"),
        )
        for subject, email in subjects:
            await conn.execute(
                text(
                    "INSERT INTO app_user "
                    "(id, issuer, external_subject, email, display_name, status) "
                    "VALUES (:id, :issuer, :subject, :email, :name, 'active')"
                ),
                {
                    "id": uuid.uuid4(),
                    "issuer": _issuer(REALM),
                    "subject": subject,
                    "email": f"kc-{email}",
                    "name": subject[:8],
                },
            )
        await conn.execute(
            text(
                "INSERT INTO membership (id, tenant_id, user_id, role_code, status) "
                "SELECT :id, :tenant_id, u.id, 'tenant_admin', 'active' FROM app_user u "
                "WHERE u.external_subject = :subject AND u.issuer = :issuer"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": seeded.tenant_a.id,
                "subject": ADA_SUBJECT,
                "issuer": _issuer(REALM),
            },
        )
    yield seeded


class TestTenantContextComesFromMembership:
    """A perfect token still does not get to choose its tenant."""

    async def test_a_real_token_resolves_to_the_membership_tenant(
        self,
        keycloak: Keycloak,
        verifier: OidcTokenVerifier,
        app_sessions: Any,
        settings: Settings,
        keycloak_principals: Fixtures,
    ) -> None:
        token = keycloak.password_grant(realm=REALM, username="ada")
        context = await resolve_context(
            factory=app_sessions,
            settings=settings,
            verifier=verifier,
            token=token,
            trace_id="trace-oidc",
            request_id="request-oidc",
        )
        assert context.tenant_id == keycloak_principals.tenant_a.id
        assert context.principal.external_subject == ADA_SUBJECT

    async def test_a_valid_token_naming_another_tenant_is_refused(
        self,
        verifier: OidcTokenVerifier,
        signing_key: ImportedKey,
        app_sessions: Any,
        settings: Settings,
        keycloak_principals: Fixtures,
    ) -> None:
        """**The assertion this whole task exists to make.**

        The token is signed by a key the provider publishes, has the right
        issuer, the right audience, is unexpired, and names a real user. It
        claims `tid` = tenant B. Ada is a member of tenant A only.

        Cryptographic validity is not authorization. The claim is a request,
        and the membership table answers it.
        """
        token = _mint(signing_key, tid=str(keycloak_principals.tenant_b.id))

        # Proof the token itself is beyond reproach — so the refusal below is
        # an authorization decision, not a verification failure.
        assert (await verifier.verify(token)).subject == ADA_SUBJECT

        with pytest.raises(ForbiddenError):
            await resolve_context(
                factory=app_sessions,
                settings=settings,
                verifier=verifier,
                token=token,
                trace_id="trace-oidc",
                request_id="request-oidc",
            )

    async def test_a_valid_token_for_a_user_with_no_membership_is_refused(
        self,
        keycloak: Keycloak,
        verifier: OidcTokenVerifier,
        app_sessions: Any,
        settings: Settings,
        keycloak_principals: Fixtures,
    ) -> None:
        """Authenticated is not authorized. Orphan signs in successfully."""
        token = keycloak.password_grant(realm=REALM, username="orphan")
        assert (await verifier.verify(token)).subject == ORPHAN_SUBJECT

        with pytest.raises(ForbiddenError):
            await resolve_context(
                factory=app_sessions,
                settings=settings,
                verifier=verifier,
                token=token,
                trace_id="trace-oidc",
                request_id="request-oidc",
            )

    async def test_a_subject_unknown_to_the_platform_is_refused(
        self,
        verifier: OidcTokenVerifier,
        signing_key: ImportedKey,
        app_sessions: Any,
        settings: Settings,
        keycloak_principals: Fixtures,
    ) -> None:
        """The provider vouching for someone does not create an account here."""
        token = _mint(signing_key, sub="cccccccc-0000-4000-8000-000000000009")
        with pytest.raises(UnauthenticatedError):
            await resolve_context(
                factory=app_sessions,
                settings=settings,
                verifier=verifier,
                token=token,
                trace_id="trace-oidc",
                request_id="request-oidc",
            )


# =============================================================================
# 6. no development fallback
# =============================================================================


class TestNoDevelopmentFallbackExists:
    """The Phase 1A defect, re-tested against a provider that really exists.

    The original code checked that OIDC was *configured* and then verified with
    the development secret regardless. These assertions are the reason that
    cannot recur.
    """

    async def test_a_development_token_is_rejected_by_the_real_verifier(
        self, verifier: OidcTokenVerifier
    ) -> None:
        settings = Settings(
            env=Environment.LOCAL,
            auth_issuer=_issuer(REALM),
            auth_audience=AUDIENCE,
        )
        token, _ = issue_dev_token(settings, subject=ADA_SUBJECT, tenant_id=None)

        # Correct issuer, correct audience, unexpired, correct subject. It is
        # rejected for the only reason that matters: the provider did not sign
        # it.
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)

    @pytest.mark.parametrize(
        "environment", [Environment.DEV, Environment.STAGING, Environment.PRODUCTION]
    )
    def test_the_development_verifier_is_unconstructable_even_when_oidc_works(
        self, environment: Environment, jwks_url: str
    ) -> None:
        settings = Settings(
            env=environment,
            auth_issuer=_issuer(REALM),
            auth_audience=AUDIENCE,
            auth_oidc_jwks_url=jwks_url,
        )
        with pytest.raises(ConfigurationError):
            DevelopmentTokenVerifier(settings=settings)

    @pytest.mark.parametrize(
        "environment", [Environment.DEV, Environment.STAGING, Environment.PRODUCTION]
    )
    def test_a_half_configured_provider_fails_startup_rather_than_degrading(
        self, environment: Environment
    ) -> None:
        """Knowing the issuer is not knowing the keys.

        The tempting failure is to accept a configured issuer and quietly use
        the development secret for verification. Startup must stop instead.
        """
        settings = Settings(
            env=environment,
            auth_issuer=_issuer(REALM),
            auth_audience=AUDIENCE,
            auth_oidc_jwks_url="",
        )
        with pytest.raises(ConfigurationError):
            build_verifier(settings)

    def test_the_dev_issuer_refuses_to_mint_against_a_real_issuer(self) -> None:
        settings = Settings(
            env=Environment.PRODUCTION,
            auth_issuer=_issuer(REALM),
            auth_audience=AUDIENCE,
            auth_oidc_jwks_url="https://example.invalid/jwks",
        )
        with pytest.raises(ConfigurationError):
            issue_dev_token(settings, subject=ADA_SUBJECT, tenant_id=None)
