"""FastAPI application factory.

Startup order is a security property, not a convenience:

1. configure logging and telemetry;
2. build engines;
3. **assert the runtime database role is genuinely constrained** — not a
   superuser, no ``BYPASSRLS``, not the table owner;
4. **assert every tenant-scoped table has FORCE RLS and a policy**;
5. only then accept traffic.

Steps 3 and 4 fail the boot rather than logging a warning. A process that
starts with isolation silently disabled would pass every functional test while
being catastrophically wrong, which is exactly the failure mode ADR-003 exists
to prevent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Registers every ORM mapper before any request can touch one.
import eip.models  # noqa: F401
from eip.api.middleware import (
    CorrelationMiddleware,
    handle_eip_error,
    handle_unexpected_error,
)
from eip.api.routers import admin, dev_auth, health, tenancy
from eip.dataplane.registry import build_data_plane
from eip.identity.oidc import (
    assert_algorithms_are_asymmetric,
    build_verifier,
    discover_jwks_url,
)
from eip.platform.db import (
    assert_rls_covers_tenant_tables,
    assert_runtime_role_is_constrained,
    create_engines,
    create_session_factory,
)
from eip.platform.errors import EipError
from eip.platform.logging import configure_logging, get_logger
from eip.platform.settings import Settings, get_settings
from eip.platform.telemetry import configure_telemetry, instrument_app

_log = get_logger("api.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire dependencies and verify isolation invariants before serving."""
    settings: Settings = app.state.settings

    engines = create_engines(settings)
    app.state.engines = engines
    app.state.session_factory = create_session_factory(engines.app)
    app.state.platform_session_factory = create_session_factory(engines.platform)
    app.state.data_plane = build_data_plane(settings, engines.platform)

    # Isolation invariants. These raise ConfigurationError and abort startup.
    await assert_runtime_role_is_constrained(engines.app)
    await assert_rls_covers_tenant_tables(engines.app)

    # Authentication. A production-like environment with incomplete OIDC
    # configuration fails here rather than at every sign-in (ADR-010 §1).
    assert_algorithms_are_asymmetric()
    jwks_url = settings.auth_oidc_jwks_url
    if settings.is_production_like and not jwks_url:
        # Fall back to discovery, not to a weaker verifier.
        jwks_url = await discover_jwks_url(settings.auth_issuer)
    app.state.token_verifier = build_verifier(settings, jwks_url=jwks_url or None)

    _log.info(
        "api.started",
        environment=settings.env.value,
        data_plane_mode=settings.data_plane_mode.value,
        dev_auth_enabled=settings.env.allows_dev_auth,
    )
    try:
        yield
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()
        _log.info("api.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    resolved = settings or get_settings()

    configure_logging(resolved)
    configure_telemetry(resolved)

    app = FastAPI(
        title="TriVera Executive Intelligence Platform API",
        version="0.1.0",
        summary="Phase 1A — platform skeleton. No business intelligence functionality.",
        description=(
            "Every capability is exposed through this versioned API; the web application "
            "is one client among potential others (principle 8). No endpoint accepts SQL, "
            "a formula, or a field path from a client."
        ),
        lifespan=lifespan,
        # Interactive docs are a discovery aid for an internal API; they stay
        # off in production-like environments.
        docs_url=None if resolved.is_production_like else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.is_production_like else "/openapi.json",
    )
    app.state.settings = resolved

    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Explicit, not "*": the elevation-reason header is deliberate and the
        # tenant headers are deliberately absent (they are ignored anyway).
        allow_headers=["authorization", "content-type", "traceparent", "x-elevation-reason"],
        expose_headers=["x-request-id", "x-trace-id"],
    )

    app.add_exception_handler(EipError, handle_eip_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    app.include_router(health.router)
    app.include_router(tenancy.router)
    app.include_router(admin.router)

    # Guard #1 of three: the development token issuer does not exist as a route
    # outside local/ci. See dev_auth.py for the other two.
    if resolved.env.allows_dev_auth:
        app.include_router(dev_auth.router)
        _log.warning("api.dev_auth_router_enabled", environment=resolved.env.value)

    instrument_app(app, resolved)
    return app
