"""Data-plane selection.

The registry is the one place that maps a configured ``IsolationMode`` to an
implementation. Business code asks for a ``TenantDataPlane`` and receives the
approved one; it never names ``SchemaPerTenantDataPlane`` directly.

Unimplemented modes raise ``NotImplementedModeError``. They are *declared* in
``IsolationMode`` (so the abstraction is shaped by all four) but not built, per
the Phase 1A instruction to implement only the currently approved mode.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from eip.dataplane.credentials import AnalyticalCredentialProvider
from eip.dataplane.interfaces import TenantDataPlane
from eip.dataplane.pool import TenantPoolRegistry
from eip.dataplane.schema_per_tenant import SchemaPerTenantDataPlane
from eip.platform.errors import NotImplementedModeError
from eip.platform.secrets import SecretStore
from eip.platform.settings import IsolationMode, Settings

#: Modes declared by ADR-003 but not built. Listed explicitly so the error
#: message can distinguish "not yet built" from "not a real mode" — the former
#: is a roadmap question, the latter a configuration typo.
DECLARED_BUT_UNIMPLEMENTED: frozenset[IsolationMode] = frozenset(
    {
        IsolationMode.SHARED_RLS,
        IsolationMode.DATABASE_PER_TENANT,
        IsolationMode.DEDICATED_DEPLOYMENT,
    }
)


def build_credential_provider(
    settings: Settings, secret_store: SecretStore
) -> AnalyticalCredentialProvider:
    """Build the provider that turns a SecretRef into a tenant connection URL."""
    return AnalyticalCredentialProvider(
        secret_store=secret_store,
        # Host, port, and database come from the application DSN; only the user
        # and password differ per tenant, so a tenant URL cannot point at a
        # different server.
        template_dsn=settings.db_app_dsn,
    )


def build_pool_registry(
    settings: Settings, credentials: AnalyticalCredentialProvider
) -> TenantPoolRegistry:
    """Build the bounded, LRU-evicting per-tenant pool registry."""
    return TenantPoolRegistry(
        credentials=credentials,
        max_tenants=settings.analytical_pool_max_tenants,
        pool_size=settings.analytical_pool_size,
        max_overflow=settings.analytical_pool_max_overflow,
        idle_ttl_seconds=settings.analytical_pool_idle_ttl_seconds,
    )


def build_data_plane(
    settings: Settings,
    platform_engine: AsyncEngine,
    credentials: AnalyticalCredentialProvider,
) -> TenantDataPlane:
    """Return the data plane for the configured isolation mode."""
    mode = settings.data_plane_mode

    if mode is IsolationMode.SCHEMA_PER_TENANT:
        return SchemaPerTenantDataPlane(
            platform_engine=platform_engine,
            schema_prefix=settings.data_plane_schema_prefix,
            credentials=credentials,
        )

    if mode in DECLARED_BUT_UNIMPLEMENTED:
        msg = (
            f"Isolation mode {mode.value!r} is declared by ADR-003 but not implemented. "
            "Phase 1A implements schema_per_tenant only. Refusing to start rather than "
            "silently serving a different isolation guarantee."
        )
        raise NotImplementedModeError(msg)

    msg = f"Unknown isolation mode: {mode!r}"
    raise NotImplementedModeError(msg)
