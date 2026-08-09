"""Fast connection-test persistence contract checks."""

from __future__ import annotations

from eip.connectivity.models import ConnectionTest


def test_connection_test_has_no_secret_or_endpoint_columns() -> None:
    names = set(ConnectionTest.__table__.columns.keys())
    assert not names & {"endpoint", "username", "secret_ref", "credential", "password"}
    assert {"tenant_id", "data_source_id", "source_version", "checks", "status"} <= names
