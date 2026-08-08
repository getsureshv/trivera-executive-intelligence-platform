"""PostgreSQL connector diagnostics, including real-container execution."""

from __future__ import annotations

import json
import uuid

import pytest

from eip.connectivity.egress import EgressValidator
from eip.connectivity.postgresql import PostgreSQLConfig, PostgreSQLConnector, SystemResolver
from eip.connectivity.protocol import (
    ConnectionTarget,
    DiagnosticCheckStatus,
    DiagnosticCheckType,
)
from eip.platform.secrets import SecretRef, SecretValue
from eip.platform.secretstore import InMemorySecretStore


class RecordingSecretStore(InMemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.purposes: list[str] = []

    async def get(self, ref: SecretRef, *, purpose: str) -> SecretValue:
        self.purposes.append(purpose)
        return await super().get(ref, purpose=purpose)


def _encoded(ref: SecretRef) -> str:
    return f"{ref.path}/{ref.version}"


def _allow_current_postgres() -> EgressValidator:
    resolver = SystemResolver()
    resolution = resolver.resolve("postgres")
    assert resolution.addresses
    networks = [
        f"{address}/32" if ":" not in address else f"{address}/128"
        for address in resolution.addresses
    ]
    return EgressValidator(resolver, allowlist=networks)


async def _connector(password: str) -> tuple[PostgreSQLConnector, RecordingSecretStore]:
    tenant_id = uuid.uuid4()
    store = RecordingSecretStore()
    ref = await store.put(tenant_id, "source-postgresql", SecretValue(password))
    target = ConnectionTarget(
        connector_type="postgresql",
        endpoint="postgres:5432",
        secret_ref=_encoded(ref),
    )
    connector = PostgreSQLConnector(
        target,
        PostgreSQLConfig(username="eip_app", database="eip"),
        store,
        _allow_current_postgres(),
    )
    return connector, store


def test_postgresql_capabilities_and_schema_contain_no_secret_value() -> None:
    connector = PostgreSQLConnector(
        ConnectionTarget("postgresql", "db.example:5432", "tenants/ref/version"),
        PostgreSQLConfig("reader", "warehouse"),
        RecordingSecretStore(),
        EgressValidator(SystemResolver()),
    )
    assert connector.capabilities().supports_schema_discovery is True
    document = json.dumps(connector.config_schema().to_dict())
    assert "password" not in document.lower()
    assert "secret_ref" in document


@pytest.mark.parametrize(
    "config",
    [
        PostgreSQLConfig("reader", "warehouse", tls_mode="require"),
        PostgreSQLConfig("reader", "warehouse", connect_timeout_seconds=0.1),
    ],
)
def test_valid_postgresql_configuration(config: PostgreSQLConfig) -> None:
    assert config.username == "reader"


def test_invalid_postgresql_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="tls_mode"):
        PostgreSQLConfig("reader", "warehouse", tls_mode="prefer")
    with pytest.raises(ValueError, match="positive"):
        PostgreSQLConfig("reader", "warehouse", connect_timeout_seconds=0)


@pytest.mark.integration
async def test_real_postgresql_success_is_ordered_and_secret_backed() -> None:
    connector, store = await _connector("local_dev_only")
    result = await connector.test_connection()
    assert result.success is True
    assert tuple(check.type for check in result.checks) == tuple(DiagnosticCheckType)
    assert all(check.status == DiagnosticCheckStatus.PASS for check in result.checks)
    assert result.checks[3].code in {"AUTHORIZATION_READ_ONLY", "AUTHORIZATION_WRITE_CAPABLE"}
    assert result.checks[4].code == "METADATA_ACCESS_OK"
    assert result.checks[5].code == "LATENCY_MEASURED"
    assert store.purposes == ["test_connection"]
    rendered = json.dumps(result.to_dict())
    assert "local_dev_only" not in rendered


@pytest.mark.integration
async def test_real_postgresql_authentication_failure_fences_later_checks() -> None:
    connector, store = await _connector("definitely-wrong-password")
    result = await connector.test_connection()
    assert result.success is False
    assert result.checks[2].code == "AUTHENTICATION_FAILED"
    assert result.checks[2].status == DiagnosticCheckStatus.FAIL
    assert all(check.status == DiagnosticCheckStatus.SKIPPED for check in result.checks[3:])
    assert store.purposes == ["test_connection"]
    rendered = json.dumps(result.to_dict())
    assert "definitely-wrong-password" not in rendered


async def test_network_policy_failure_does_not_retrieve_secret() -> None:
    tenant_id = uuid.uuid4()
    store = RecordingSecretStore()
    ref = await store.put(tenant_id, "source-postgresql", SecretValue("not-read"))
    connector = PostgreSQLConnector(
        ConnectionTarget("postgresql", "127.0.0.1:5432", _encoded(ref)),
        PostgreSQLConfig("reader", "warehouse"),
        store,
        EgressValidator(SystemResolver()),
    )
    result = await connector.test_connection()
    assert result.success is False
    assert result.checks[0].status == DiagnosticCheckStatus.FAIL
    assert all(check.status == DiagnosticCheckStatus.SKIPPED for check in result.checks[1:])
    assert store.purposes == []
