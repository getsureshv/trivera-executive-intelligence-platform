"""Application settings.

Configuration is environment-driven and validated at import time. Secrets never
appear here as literals; the database DSNs carried below are local-development
credentials supplied by the environment, and production deployments resolve
credentials through the ``SecretStore`` port (ADR-015).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment.

    ``LOCAL`` and ``CI`` are the only environments in which the development
    token issuer is permitted to exist (ADR-010).
    """

    LOCAL = "local"
    CI = "ci"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def allows_dev_auth(self) -> bool:
        return self in (Environment.LOCAL, Environment.CI)


class IsolationMode(StrEnum):
    """Tenant data-plane isolation modes declared by ADR-003.

    All four are named so that application code can be written against the
    concept rather than against schema-per-tenant assumptions. Only
    ``SCHEMA_PER_TENANT`` is implemented in Phase 1A; requesting another mode
    fails loudly at startup rather than silently degrading.
    """

    SHARED_RLS = "shared_rls"
    SCHEMA_PER_TENANT = "schema_per_tenant"
    DATABASE_PER_TENANT = "database_per_tenant"
    DEDICATED_DEPLOYMENT = "dedicated_deployment"


class Settings(BaseSettings):
    """Validated application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="EIP_",
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- environment -----------------------------------------------------
    env: Environment = Environment.LOCAL
    service_name: str = "eip-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    # --- database (ADR-003) ----------------------------------------------
    # Three roles, deliberately distinct. See infra/postgres/init/00-roles.sql.
    db_app_dsn: str = "postgresql+asyncpg://eip_app:local_dev_only@localhost:5432/eip"
    db_platform_dsn: str = "postgresql+asyncpg://eip_platform:local_dev_only@localhost:5432/eip"
    db_migrator_dsn: str = "postgresql+asyncpg://eip_migrator:local_dev_only@localhost:5432/eip"

    db_pool_size: Annotated[int, Field(ge=1, le=100)] = 5
    db_max_overflow: Annotated[int, Field(ge=0, le=100)] = 5
    db_echo: bool = False

    # --- broker (ADR-009) -------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- api --------------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - containers bind all interfaces
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    cors_origins: str = "http://localhost:3000"

    # --- authentication (ADR-010) ----------------------------------------
    auth_dev_signing_secret: SecretStr = SecretStr("local-dev-only-not-a-real-secret")
    auth_issuer: str = "https://local.eip.invalid/"
    auth_audience: str = "eip-api"
    auth_access_token_ttl_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3600
    auth_oidc_jwks_url: str = ""

    # --- tenant data plane (ADR-003) --------------------------------------
    data_plane_mode: IsolationMode = IsolationMode.SCHEMA_PER_TENANT
    data_plane_schema_prefix: str = "tenant_"

    # --- observability (ADR-014) ------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    # --- worker (ADR-009) -------------------------------------------------
    worker_health_port: Annotated[int, Field(ge=1, le=65535)] = 8001
    outbox_poll_interval_seconds: Annotated[float, Field(gt=0, le=60)] = 1.0
    outbox_batch_size: Annotated[int, Field(ge=1, le=1000)] = 100

    @field_validator("data_plane_schema_prefix")
    @classmethod
    def _validate_schema_prefix(cls, value: str) -> str:
        """Reject a prefix that could contribute to identifier injection.

        Analytical schema names are derived from tenant records, never from
        request input (ADR-003 Risks), but the prefix is configuration and is
        validated defensively all the same.
        """
        if not value.isidentifier():
            msg = f"data_plane_schema_prefix must be a valid SQL identifier, got {value!r}"
            raise ValueError(msg)
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production_like(self) -> bool:
        return self.env in (Environment.STAGING, Environment.PRODUCTION)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
