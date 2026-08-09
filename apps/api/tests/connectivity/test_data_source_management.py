"""Focused safe-contract tests for Data Source management."""

from __future__ import annotations

import pytest

from eip.api.routers.data_sources import CreateSourceRequest
from eip.connectivity.service import validate_configuration, validate_endpoint
from eip.platform.context import ROLE_CAPABILITIES, Capability, RoleCode
from eip.platform.errors import ValidationError


def test_postgresql_configuration_is_normalized() -> None:
    assert validate_configuration({"username": "reader", "database": "warehouse"}) == {
        "username": "reader",
        "database": "warehouse",
        "tls_mode": "disable",
        "connect_timeout_seconds": 3.0,
    }


@pytest.mark.parametrize("field", ["password", "secret", "token", "dsn", "url"])
def test_configuration_rejects_credential_like_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        validate_configuration({"username": "reader", "database": "warehouse", field: "sentinel"})


def test_write_only_credential_is_not_serialized() -> None:
    payload = CreateSourceRequest.model_validate(
        {
            "name": "Warehouse",
            "connector_type": "postgresql",
            "endpoint": "db.example.invalid:5432",
            "configuration": {"username": "reader", "database": "warehouse"},
            "credential": "unique-sentinel-password",
        }
    )
    assert "unique-sentinel-password" not in str(payload)
    assert "unique-sentinel-password" not in repr(payload)
    assert CreateSourceRequest.model_json_schema()["properties"]["credential"]["writeOnly"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "postgresql://db.invalid/database",
        "reader:password@db.invalid:5432",
        "db.invalid/path",
        "db.invalid 5432",
    ],
)
def test_endpoint_rejects_connection_strings(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        validate_endpoint(endpoint)


def test_nested_secret_material_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_configuration(
            {
                "username": "reader",
                "database": "warehouse",
                "options": {"nested_password": "sentinel"},
            }
        )


def test_platform_admin_has_no_standing_source_capabilities() -> None:
    source_capabilities = {
        capability for capability in Capability if capability.value.startswith("source.")
    }
    assert not (ROLE_CAPABILITIES[RoleCode.PLATFORM_ADMIN] & source_capabilities)
