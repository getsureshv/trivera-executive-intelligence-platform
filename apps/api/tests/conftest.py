"""Shared test fixtures.

Two tenants and four users are seeded for every integration test, because
tenant isolation cannot be tested with one tenant. The fixture names
(``tenant_a``, ``user_a``, …) are used verbatim throughout
``tests/security/`` so the acceptance scenario reads like its specification.

Tests that need a database are marked ``integration`` (or ``security``) and
skip cleanly when PostgreSQL or the driver is unavailable, so the unit suite
runs on a developer machine without infrastructure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.settings import Environment, Settings

# Registers every ORM mapper. conftest is imported before any test module, so
# doing it here covers the whole suite (see eip/models.py for why it matters).
import eip.models  # noqa: F401  # isort: skip

# ---------------------------------------------------------------------------
# database availability
# ---------------------------------------------------------------------------


def _database_available(settings: Settings) -> tuple[bool, str]:
    """Report whether integration tests can run, and why not if they cannot."""
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return False, (
            "asyncpg is not installed. It is a C extension without wheels on some "
            "platforms; run the integration suite in the container instead "
            "(see README, 'Running the tests')."
        )

    import asyncio
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(settings.db_app_dsn.replace("postgresql+asyncpg", "postgresql"))
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError:
        return False, f"PostgreSQL is not reachable at {host}:{port}."
    del asyncio
    return True, ""


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings for the test run, pinned to the CI environment."""
    os.environ.setdefault("EIP_ENV", "ci")
    return Settings(env=Environment.CI)


@pytest.fixture(scope="session")
def database_available(settings: Settings) -> tuple[bool, str]:
    return _database_available(settings)


@pytest.fixture(autouse=True)
def _skip_without_database(
    request: pytest.FixtureRequest, database_available: tuple[bool, str]
) -> None:
    """Skip database-dependent tests when infrastructure is absent."""
    needs_db = request.node.get_closest_marker("integration") or request.node.get_closest_marker(
        "security"
    )
    if not needs_db:
        return
    available, reason = database_available
    if not available:
        pytest.skip(reason)


# ---------------------------------------------------------------------------
# engines and sessions
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The constrained runtime engine (``eip_app``): RLS applies to it."""
    from eip.platform.db import create_engines

    engines = create_engines(settings)
    try:
        yield engines.app
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()


@pytest.fixture
async def platform_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The privileged engine (``eip_platform``, BYPASSRLS).

    Used by fixtures to seed data across tenants, and by
    ``tests/security/test_privileged_platform_access.py`` to prove the
    privileged path works *and* is distinguishable from the normal one.
    """
    from eip.platform.db import create_engines

    engines = create_engines(settings)
    try:
        yield engines.platform
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()


@pytest.fixture
def app_sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    from eip.platform.db import create_session_factory

    return create_session_factory(app_engine)


@pytest.fixture
def platform_sessions(platform_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    from eip.platform.db import create_session_factory

    return create_session_factory(platform_engine)


# ---------------------------------------------------------------------------
# seeded fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededTenant:
    id: uuid.UUID
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class SeededUser:
    id: uuid.UUID
    email: str
    subject: str


@dataclass(frozen=True, slots=True)
class Fixtures:
    """The full two-tenant scenario used by the isolation tests."""

    tenant_a: SeededTenant
    tenant_b: SeededTenant
    user_a: SeededUser
    user_b: SeededUser
    #: Belongs to neither tenant. Authenticated, but not authorized.
    user_orphan: SeededUser
    #: Platform staff. Belongs to no tenant, holds platform_admin via tenant A
    #: so the privileged path can be exercised end to end.
    user_platform: SeededUser


#: Deleted in foreign-key-safe order. Not ``TRUNCATE``: that privilege is
#: owner-only and is deliberately not granted, which is a side effect of
#: hardening ``audit_event`` to append-only. ``DELETE`` works because the
#: privileged role retains it for tenant offboarding (a tenant delete cascades
#: to its audit rows — see migration 0001). The *runtime* role has neither,
#: which is the guarantee that actually matters and is asserted in
#: ``tests/security/test_audit_and_authorization.py``.
_RESET_ORDER = (
    "connection_test",
    "data_source_acl",
    "data_source",
    "audit_event",
    "outbox",
    "membership",
    "tenant",
    "app_user",
)


async def _reset(engine: AsyncEngine) -> None:
    """Clear the control plane between tests.

    Runs on the privileged engine so it is not itself subject to RLS — an
    isolation-constrained cleanup could not remove the other tenant's rows and
    would leak state between tests.
    """
    async with engine.begin() as conn:
        demo_bundles = (
            await conn.execute(
                text(
                    "SELECT DISTINCT b.tenant_id,b.id FROM configuration_bundle b "
                    "JOIN demo_dataset d ON d.tenant_id=b.tenant_id AND d.bundle_id=b.id "
                    "WHERE d.origin='seeded_demo'"
                )
            )
        ).all()
        for tenant_id, bundle_id in demo_bundles:
            await conn.execute(
                text("SELECT eip_reset_seeded_demo(:tenant_id,:bundle_id)"),
                {"tenant_id": tenant_id, "bundle_id": bundle_id},
            )
        for table in _RESET_ORDER:
            await conn.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
async def seeded(platform_engine: AsyncEngine, settings: Settings) -> AsyncIterator[Fixtures]:
    """Create two isolated tenants with one member each, plus two outsiders."""
    await _reset(platform_engine)

    tenant_a = SeededTenant(uuid.uuid4(), "acme-industrial", "Acme Industrial")
    tenant_b = SeededTenant(uuid.uuid4(), "borealis-capital", "Borealis Capital")
    user_a = SeededUser(uuid.uuid4(), "ada@acme.invalid", "subject-ada")
    user_b = SeededUser(uuid.uuid4(), "ben@borealis.invalid", "subject-ben")
    user_orphan = SeededUser(uuid.uuid4(), "nobody@nowhere.invalid", "subject-nobody")
    user_platform = SeededUser(uuid.uuid4(), "ops@trivera.invalid", "subject-ops")

    async with platform_engine.begin() as conn:
        for tenant in (tenant_a, tenant_b):
            await conn.execute(
                text(
                    "INSERT INTO tenant "
                    "(id, slug, name, status, analytical_schema, isolation_mode) "
                    "VALUES (:id, :slug, :name, 'active', :schema, 'schema_per_tenant')"
                ),
                {
                    "id": tenant.id,
                    "slug": tenant.slug,
                    "name": tenant.name,
                    "schema": f"tenant_{str(tenant.id).replace('-', '_')}",
                },
            )

        for user in (user_a, user_b, user_orphan, user_platform):
            await conn.execute(
                text(
                    "INSERT INTO app_user "
                    "(id, issuer, external_subject, email, display_name, status) "
                    "VALUES (:id, :issuer, :subject, :email, :name, 'active')"
                ),
                {
                    "id": user.id,
                    "issuer": settings.auth_issuer,
                    "subject": user.subject,
                    "email": user.email,
                    "name": user.email.split("@")[0].title(),
                },
            )

        for tenant, user, role in (
            (tenant_a, user_a, "tenant_admin"),
            (tenant_b, user_b, "tenant_admin"),
            (tenant_a, user_platform, "platform_admin"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO membership (id, tenant_id, user_id, role_code, status) "
                    "VALUES (:id, :tenant_id, :user_id, :role, 'active')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant.id,
                    "user_id": user.id,
                    "role": role,
                },
            )

    yield Fixtures(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        user_a=user_a,
        user_b=user_b,
        user_orphan=user_orphan,
        user_platform=user_platform,
    )

    await _reset(platform_engine)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the real ASGI app, lifespan included.

    Running the lifespan matters: it executes the startup isolation assertions,
    so any test that gets a client has already proved the runtime role is
    constrained and RLS is in force.
    """
    from eip.api.app import create_app

    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http_client,
        # Driven manually: ASGITransport does not run the lifespan, and the
        # lifespan is where the startup isolation assertions live.
        app.router.lifespan_context(app),
    ):
        yield http_client


async def token_for(client: AsyncClient, email: str, tenant_id: uuid.UUID | None = None) -> str:
    """Obtain a development access token for ``email``.

    The ``tenant_id`` argument is a *request*: whether it is honoured is
    decided by membership resolution, which is exactly what the isolation
    tests probe.
    """
    payload: dict[str, object] = {"email": email}
    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)
    response = await client.post("/v1/dev/token", json=payload)
    response.raise_for_status()
    token: str = response.json()["access_token"]
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
