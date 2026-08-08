"""PostgreSQL connection diagnostics with secret and network-policy boundaries."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from eip.connectivity.egress import DnsResolution, EgressValidator
from eip.connectivity.protocol import (
    ConnectionTarget,
    ConnectionTestResult,
    ConnectorCapabilities,
    DiagnosticCheck,
    DiagnosticCheckStatus,
    DiagnosticCheckType,
    JsonSchema,
)
from eip.platform.secrets import SecretRef, SecretStore

_PURPOSE: Final = "test_connection"


class SystemResolver:
    """System DNS resolver that returns every address for policy evaluation."""

    def resolve(self, hostname: str) -> DnsResolution:
        try:
            answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError:
            return DnsResolution(addresses=(), error="resolution_failed")
        addresses = tuple(sorted({str(answer[4][0]) for answer in answers}))
        return DnsResolution(addresses=addresses)


@dataclass(frozen=True, slots=True)
class PostgreSQLConfig:
    """Non-secret PostgreSQL connection configuration."""

    username: str
    database: str
    tls_mode: str = "disable"
    connect_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not self.username or not self.database:
            raise ValueError("PostgreSQL username and database are required")
        if self.tls_mode not in {"disable", "require"}:
            raise ValueError("PostgreSQL tls_mode must be disable or require")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("PostgreSQL connect timeout must be positive")


def _secret_ref(encoded: str) -> SecretRef:
    """Decode ``tenants/<uuid>/<logical-name>/<version>`` without a secret value."""
    parts = encoded.split("/")
    if len(parts) != 4 or parts[0] != "tenants" or not parts[2] or not parts[3]:
        raise ValueError("Invalid SecretRef")
    try:
        tenant_id = uuid.UUID(parts[1])
    except ValueError as exc:
        raise ValueError("Invalid SecretRef") from exc
    return SecretRef(tenant_id=tenant_id, logical_name=parts[2], version=parts[3])


def _check(
    kind: DiagnosticCheckType,
    status: DiagnosticCheckStatus,
    code: str,
    message: str,
    *,
    duration_ms: int = 0,
    remediation: str | None = None,
) -> DiagnosticCheck:
    return DiagnosticCheck(
        type=kind,
        status=status,
        code=code,
        message=message,
        remediation_hint=remediation,
        duration_ms=duration_ms,
    )


def _skipped(after: DiagnosticCheckType) -> tuple[DiagnosticCheck, ...]:
    kinds = tuple(DiagnosticCheckType)
    start = kinds.index(after) + 1
    return tuple(
        _check(kind, DiagnosticCheckStatus.SKIPPED, "SKIPPED", "Skipped after earlier failure")
        for kind in kinds[start:]
    )


class PostgreSQLConnector:
    """The PostgreSQL connection-test slice of the provider-neutral connector."""

    def __init__(
        self,
        target: ConnectionTarget,
        config: PostgreSQLConfig,
        secret_store: SecretStore,
        egress: EgressValidator,
    ) -> None:
        self._target = target
        self._config = config
        self._secret_store = secret_store
        self._egress = egress

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_incremental=True,
            supports_predicate_pushdown=True,
            supports_column_projection=True,
            supports_transactional_snapshot=True,
            supports_schema_discovery=True,
            supports_statistics=True,
        )

    def config_schema(self) -> JsonSchema:
        return JsonSchema(
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["endpoint", "username", "database", "secret_ref"],
                "properties": {
                    "endpoint": {"type": "string"},
                    "username": {"type": "string"},
                    "database": {"type": "string"},
                    "secret_ref": {"type": "string", "writeOnly": True},
                    "tls_mode": {"enum": ["disable", "require"]},
                },
            }
        )

    async def test_connection(self) -> ConnectionTestResult:
        checks: list[DiagnosticCheck] = []
        decision = self._egress.validate_endpoint(self._target.endpoint)
        if not decision.allowed or decision.selected_address is None:
            denial = decision.denial_code.value.upper() if decision.denial_code else "DENIED"
            failed = _check(
                DiagnosticCheckType.NETWORK,
                DiagnosticCheckStatus.FAIL,
                f"NETWORK_{denial}",
                "The source endpoint is not permitted or reachable",
                remediation="Check the endpoint and network allowlist",
            )
            return ConnectionTestResult(success=False, checks=(failed, *_skipped(failed.type)))

        port = _port(self._target.endpoint)
        started = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(decision.selected_address, port),
                timeout=self._config.connect_timeout_seconds,
            )
            del reader
            peer = writer.get_extra_info("peername")
            peer_address = str(peer[0]) if peer else ""
            peer_decision = self._egress.validate_peer_pre_connection(
                self._target.endpoint, peer_address
            )
            writer.close()
            await writer.wait_closed()
            if not peer_decision.allowed:
                raise ConnectionError("peer_policy_denied")
        except (OSError, TimeoutError, ConnectionError):
            failed = _check(
                DiagnosticCheckType.NETWORK,
                DiagnosticCheckStatus.FAIL,
                "NETWORK_UNREACHABLE",
                "The PostgreSQL network endpoint could not be reached safely",
                duration_ms=_elapsed_ms(started),
                remediation="Check routing, firewall, DNS, and the endpoint allowlist",
            )
            return ConnectionTestResult(success=False, checks=(failed, *_skipped(failed.type)))

        checks.append(
            _check(
                DiagnosticCheckType.NETWORK,
                DiagnosticCheckStatus.PASS,
                "NETWORK_OK",
                "Network endpoint reached and peer address revalidated",
                duration_ms=_elapsed_ms(started),
            )
        )
        checks.append(
            _check(
                DiagnosticCheckType.TLS,
                DiagnosticCheckStatus.PASS,
                "TLS_REQUIRED" if self._config.tls_mode == "require" else "TLS_DISABLED",
                "TLS policy is valid for this connection configuration",
            )
        )

        try:
            ref = _secret_ref(self._target.secret_ref)
            password = await self._secret_store.get(ref, purpose=_PURPOSE)
        except Exception:
            failed = _check(
                DiagnosticCheckType.AUTHENTICATION,
                DiagnosticCheckStatus.FAIL,
                "SECRET_UNAVAILABLE",
                "The credential reference could not be resolved",
                remediation="Replace or restore the credential reference",
            )
            return ConnectionTestResult(
                success=False, checks=(*checks, failed, *_skipped(failed.type))
            )

        engine = _engine(
            address=decision.selected_address,
            port=port,
            config=self._config,
            password=password.reveal(),
        )
        try:
            return await self._database_checks(engine, checks)
        finally:
            await engine.dispose()

    async def _database_checks(
        self, engine: AsyncEngine, checks: list[DiagnosticCheck]
    ) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            connection = await engine.connect()
        # Driver exceptions are deliberately normalized here. Importing a
        # vendor driver to enumerate them would violate the adapter boundary,
        # and no exception text is returned or logged.
        except Exception:
            failed = _check(
                DiagnosticCheckType.AUTHENTICATION,
                DiagnosticCheckStatus.FAIL,
                "AUTHENTICATION_FAILED",
                "PostgreSQL rejected the supplied identity",
                duration_ms=_elapsed_ms(started),
                remediation="Verify the username, credential, database, and pg_hba policy",
            )
            return ConnectionTestResult(
                success=False, checks=(*checks, failed, *_skipped(failed.type))
            )

        try:
            checks.append(
                _check(
                    DiagnosticCheckType.AUTHENTICATION,
                    DiagnosticCheckStatus.PASS,
                    "AUTHENTICATION_OK",
                    "PostgreSQL authentication succeeded",
                    duration_ms=_elapsed_ms(started),
                )
            )
            try:
                writable = bool(
                    await connection.scalar(
                        text(
                            "SELECT has_database_privilege(current_user, current_database(), "
                            "'CREATE') OR has_database_privilege(current_user, "
                            "current_database(), 'TEMP')"
                        )
                    )
                )
            except DBAPIError:
                failed = _check(
                    DiagnosticCheckType.AUTHORIZATION,
                    DiagnosticCheckStatus.FAIL,
                    "AUTHORIZATION_CHECK_FAILED",
                    "Database privileges could not be inspected",
                )
                return ConnectionTestResult(
                    success=False, checks=(*checks, failed, *_skipped(failed.type))
                )
            checks.append(
                _check(
                    DiagnosticCheckType.AUTHORIZATION,
                    DiagnosticCheckStatus.PASS,
                    "AUTHORIZATION_WRITE_CAPABLE" if writable else "AUTHORIZATION_READ_ONLY",
                    "Database privileges inspected; write capability is reported without mutation",
                    remediation="Use a read-only source login" if writable else None,
                )
            )
            try:
                await connection.scalar(text("SELECT 1 FROM information_schema.tables LIMIT 1"))
            except DBAPIError:
                failed = _check(
                    DiagnosticCheckType.METADATA_ACCESS,
                    DiagnosticCheckStatus.FAIL,
                    "METADATA_ACCESS_FAILED",
                    "PostgreSQL metadata could not be read",
                    remediation="Grant read access to source metadata",
                )
                return ConnectionTestResult(
                    success=False, checks=(*checks, failed, *_skipped(failed.type))
                )
            checks.append(
                _check(
                    DiagnosticCheckType.METADATA_ACCESS,
                    DiagnosticCheckStatus.PASS,
                    "METADATA_ACCESS_OK",
                    "PostgreSQL metadata is readable",
                )
            )
            latency_started = time.perf_counter()
            try:
                await connection.scalar(text("SELECT 1"))
            except Exception:
                failed = _check(
                    DiagnosticCheckType.LATENCY,
                    DiagnosticCheckStatus.FAIL,
                    "LATENCY_CHECK_FAILED",
                    "PostgreSQL latency could not be measured",
                )
                return ConnectionTestResult(success=False, checks=(*checks, failed))
            checks.append(
                _check(
                    DiagnosticCheckType.LATENCY,
                    DiagnosticCheckStatus.PASS,
                    "LATENCY_MEASURED",
                    "Round-trip latency measured",
                    duration_ms=_elapsed_ms(latency_started),
                )
            )
            return ConnectionTestResult(success=True, checks=tuple(checks))
        finally:
            await connection.close()


def _port(endpoint: str) -> int:
    if endpoint.startswith("[") and "]:" in endpoint:
        return int(endpoint.rsplit(":", 1)[1])
    if endpoint.count(":") == 1:
        return int(endpoint.rsplit(":", 1)[1])
    return 5432


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _engine(*, address: str, port: int, config: PostgreSQLConfig, password: str) -> AsyncEngine:
    query: dict[str, str] = {}
    if config.tls_mode == "require":
        query["ssl"] = "require"
    url = URL.create(
        "postgresql+asyncpg",
        username=config.username,
        password=password,
        host=str(ipaddress.ip_address(address)),
        port=port,
        database=config.database,
        query=query,
    )
    return create_async_engine(
        url,
        pool_pre_ping=False,
        connect_args={"timeout": config.connect_timeout_seconds},
    )
