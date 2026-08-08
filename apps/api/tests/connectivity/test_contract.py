"""Contract tests for the connectivity bounded context.

Proves:
- JSON round-trip serialization/deserialization for all value objects
- Exact decimal representation without float coercion
- Diagnostic check ordering enforcement
- Canonical type system correctness
- Unknown type handling
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from eip.connectivity.protocol import (
    CanonicalType,
    ColumnSchema,
    ColumnType,
    ConnectionTarget,
    ConnectionTestResult,
    ConnectorCapabilities,
    DiagnosticCheck,
    DiagnosticCheckStatus,
    DiagnosticCheckType,
    DiscoveryPage,
    ExtractPlan,
    HealthStatus,
    JsonSchema,
    Namespace,
    ObjectRef,
    ObjectSchema,
    ProfileStats,
    RecordBatch,
    SampleRows,
    SourceObject,
    UnknownValue,
)


class TestCanonicalTypes:
    """Canonical type system correctness."""

    def test_canonical_type_enum_values(self) -> None:
        """All canonical types are available."""
        assert CanonicalType.STRING.value == "string"
        assert CanonicalType.INTEGER.value == "integer"
        assert CanonicalType.DECIMAL.value == "decimal"
        assert CanonicalType.FLOAT.value == "float"
        assert CanonicalType.BOOLEAN.value == "boolean"
        assert CanonicalType.DATE.value == "date"
        assert CanonicalType.TIMESTAMP.value == "timestamp"
        assert CanonicalType.TIMESTAMPTZ.value == "timestamptz"
        assert CanonicalType.JSON.value == "json"
        assert CanonicalType.BINARY.value == "binary"
        assert CanonicalType.UNKNOWN.value == "unknown"

    def test_column_type_round_trip(self) -> None:
        """ColumnType serializes and deserializes correctly."""
        col_type = ColumnType(
            canonical=CanonicalType.DECIMAL,
            native="NUMERIC(19,4)",
        )
        serialized = {
            "canonical": col_type.canonical.value,
            "native": col_type.native,
        }
        deserialized = ColumnType(
            canonical=CanonicalType(serialized["canonical"]),
            native=serialized["native"],
        )
        assert deserialized.canonical == col_type.canonical
        assert deserialized.native == col_type.native


class TestConnectorCapabilities:
    """ConnectorCapabilities serialization contract."""

    def test_capabilities_serialize_to_dict(self) -> None:
        """Capabilities convert to dict for JSON."""
        caps = ConnectorCapabilities(
            supports_incremental=True,
            supports_predicate_pushdown=True,
            max_parallel_streams=4,
        )
        d = caps.to_dict()
        assert d["supports_incremental"] is True
        assert d["supports_predicate_pushdown"] is True
        assert d["max_parallel_streams"] == 4
        assert d["supports_cdc"] is False

    def test_capabilities_json_round_trip(self) -> None:
        """Capabilities survive JSON serialization."""
        original = ConnectorCapabilities(
            supports_incremental=True,
            supports_statistics=True,
            max_parallel_streams=8,
            rate_limit_profile="burst_100_per_second",
        )
        json_str = json.dumps(original.to_dict())
        restored = ConnectorCapabilities.from_dict(json.loads(json_str))

        assert restored.supports_incremental == original.supports_incremental
        assert restored.supports_statistics == original.supports_statistics
        assert restored.max_parallel_streams == original.max_parallel_streams
        assert restored.rate_limit_profile == original.rate_limit_profile

    def test_capabilities_defaults(self) -> None:
        """Capabilities have sensible defaults."""
        caps = ConnectorCapabilities()
        assert caps.supports_incremental is False
        assert caps.supports_cdc is False
        assert caps.max_parallel_streams == 1
        assert caps.rate_limit_profile is None


class TestJsonSchema:
    """JsonSchema serialization contract."""

    def test_json_schema_round_trip(self) -> None:
        """JsonSchema survives serialization."""
        schema_dict = {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required": ["host"],
        }
        original = JsonSchema(schema=schema_dict)
        json_str = json.dumps(original.to_dict())
        restored = JsonSchema.from_dict(json.loads(json_str))
        assert restored.schema == original.schema


class TestNamespace:
    """Namespace value object contract."""

    def test_namespace_round_trip(self) -> None:
        """Namespace survives serialization."""
        ns = Namespace(name="public", description="The public schema", object_count=42)
        json_str = json.dumps(ns.to_dict())
        restored = Namespace.from_dict(json.loads(json_str))
        assert restored.name == ns.name
        assert restored.description == ns.description
        assert restored.object_count == ns.object_count

    def test_namespace_optional_fields(self) -> None:
        """Namespace works with minimal fields."""
        ns = Namespace(name="data")
        d = ns.to_dict()
        restored = Namespace.from_dict(d)
        assert restored.name == "data"
        assert restored.description is None
        assert restored.object_count is None


class TestSourceObject:
    """SourceObject value object contract."""

    def test_source_object_round_trip(self) -> None:
        """SourceObject survives serialization."""
        obj = SourceObject(
            name="users",
            type="table",
            description="User directory",
            row_count=1000,
        )
        json_str = json.dumps(obj.to_dict())
        restored = SourceObject.from_dict(json.loads(json_str))
        assert restored.name == obj.name
        assert restored.type == obj.type
        assert restored.description == obj.description
        assert restored.row_count == obj.row_count


class TestObjectRef:
    """ObjectRef value object contract."""

    def test_object_ref_with_namespace(self) -> None:
        """ObjectRef with namespace round-trips."""
        ref = ObjectRef(namespace="public", name="users")
        json_str = json.dumps(ref.to_dict())
        restored = ObjectRef.from_dict(json.loads(json_str))
        assert restored.namespace == ref.namespace
        assert restored.name == ref.name

    def test_object_ref_without_namespace(self) -> None:
        """ObjectRef without namespace (e.g., CSV) round-trips."""
        ref = ObjectRef(namespace=None, name="data.csv")
        json_str = json.dumps(ref.to_dict())
        restored = ObjectRef.from_dict(json.loads(json_str))
        assert restored.namespace is None
        assert restored.name == "data.csv"


class TestColumnSchema:
    """ColumnSchema value object contract."""

    def test_column_schema_round_trip(self) -> None:
        """ColumnSchema survives serialization."""
        col = ColumnSchema(
            name="id",
            type=ColumnType(CanonicalType.INTEGER, "BIGINT"),
            primary_key=True,
            nullable=False,
            description="Primary key",
        )
        json_str = json.dumps(col.to_dict())
        restored = ColumnSchema.from_dict(json.loads(json_str))
        assert restored.name == col.name
        assert restored.type.canonical == col.type.canonical
        assert restored.type.native == col.type.native
        assert restored.primary_key is True
        assert restored.nullable is False

    def test_column_schema_unknown_type(self) -> None:
        """Unknown types are preserved exactly."""
        col = ColumnSchema(
            name="mystery",
            type=ColumnType(CanonicalType.UNKNOWN, "custom_type"),
        )
        json_str = json.dumps(col.to_dict())
        restored = ColumnSchema.from_dict(json.loads(json_str))
        assert restored.type.canonical == CanonicalType.UNKNOWN
        assert restored.type.native == "custom_type"


class TestObjectSchema:
    """ObjectSchema value object contract."""

    def test_object_schema_round_trip(self) -> None:
        """ObjectSchema with multiple columns survives serialization."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="id",
                    type=ColumnType(CanonicalType.INTEGER, "BIGINT"),
                    primary_key=True,
                ),
                ColumnSchema(
                    name="name",
                    type=ColumnType(CanonicalType.STRING, "VARCHAR(255)"),
                ),
            )
        )
        json_str = json.dumps(schema.to_dict())
        restored = ObjectSchema.from_dict(json.loads(json_str))
        assert len(restored.columns) == 2
        assert restored.columns[0].name == "id"
        assert restored.columns[1].name == "name"


class TestExtractPlan:
    """ExtractPlan value object contract."""

    def test_extract_plan_full_mode(self) -> None:
        """Full extraction plan round-trips."""
        plan = ExtractPlan(mode="full", batch_size=50000)
        json_str = json.dumps(plan.to_dict())
        restored = ExtractPlan.from_dict(json.loads(json_str))
        assert restored.mode == "full"
        assert restored.cursor is None
        assert restored.batch_size == 50000

    def test_extract_plan_incremental_mode(self) -> None:
        """Incremental extraction plan with cursor round-trips."""
        plan = ExtractPlan(
            mode="incremental",
            cursor="2026-08-08T12:00:00Z",
            column_projection=("id", "name", "updated_at"),
        )
        json_str = json.dumps(plan.to_dict())
        restored = ExtractPlan.from_dict(json.loads(json_str))
        assert restored.mode == "incremental"
        assert restored.cursor == "2026-08-08T12:00:00Z"
        assert restored.column_projection == ("id", "name", "updated_at")

    def test_extract_plan_with_pushdown(self) -> None:
        """Extraction plan with predicate pushdown round-trips."""
        plan = ExtractPlan(
            mode="full",
            pushdown_predicate="created_at > '2026-01-01'",
        )
        json_str = json.dumps(plan.to_dict())
        restored = ExtractPlan.from_dict(json.loads(json_str))
        assert restored.pushdown_predicate == "created_at > '2026-01-01'"


class TestRecordBatchExactDecimals:
    """RecordBatch exact decimal handling (critical for money fields)."""

    def test_decimal_exact_round_trip(self) -> None:
        """Decimals survive serialization without float coercion."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="amount",
                    type=ColumnType(CanonicalType.DECIMAL, "NUMERIC(19,4)"),
                ),
            )
        )
        batch = RecordBatch(
            rows=({"amount": Decimal("1234.5678")},),
            schema=schema,
            batch_number=1,
        )
        json_str = json.dumps(batch.to_dict())
        restored = RecordBatch.from_dict(json.loads(json_str))

        # Critical: the decimal must be exact, not a float approximation
        assert isinstance(restored.rows[0]["amount"], Decimal)
        assert restored.rows[0]["amount"] == Decimal("1234.5678")

    def test_decimal_precision_not_lost(self) -> None:
        """High-precision decimals are preserved."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="price",
                    type=ColumnType(CanonicalType.DECIMAL, "NUMERIC(15,8)"),
                ),
            )
        )
        precise_value = Decimal("123.45678901")
        batch = RecordBatch(
            rows=({"price": precise_value},),
            schema=schema,
        )
        json_str = json.dumps(batch.to_dict())
        restored = RecordBatch.from_dict(json.loads(json_str))
        assert restored.rows[0]["price"] == precise_value

    def test_null_decimal_values(self) -> None:
        """Null decimals survive round-trip."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="amount",
                    type=ColumnType(CanonicalType.DECIMAL, "NUMERIC(19,4)"),
                    nullable=True,
                ),
            )
        )
        batch = RecordBatch(
            rows=({"amount": None},),
            schema=schema,
        )
        json_str = json.dumps(batch.to_dict())
        restored = RecordBatch.from_dict(json.loads(json_str))
        assert restored.rows[0]["amount"] is None


class TestRecordBatchCanonicalTypes:
    """RecordBatch canonical type handling."""

    def test_all_canonical_types_round_trip(self) -> None:
        """All canonical types serialize and deserialize correctly."""
        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        today = date(2026, 8, 8)

        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="str_col",
                    type=ColumnType(CanonicalType.STRING, "VARCHAR"),
                ),
                ColumnSchema(
                    name="int_col",
                    type=ColumnType(CanonicalType.INTEGER, "BIGINT"),
                ),
                ColumnSchema(
                    name="dec_col",
                    type=ColumnType(CanonicalType.DECIMAL, "NUMERIC(10,2)"),
                ),
                ColumnSchema(
                    name="float_col",
                    type=ColumnType(CanonicalType.FLOAT, "DOUBLE"),
                ),
                ColumnSchema(
                    name="bool_col",
                    type=ColumnType(CanonicalType.BOOLEAN, "BOOLEAN"),
                ),
                ColumnSchema(
                    name="date_col",
                    type=ColumnType(CanonicalType.DATE, "DATE"),
                ),
                ColumnSchema(
                    name="ts_col",
                    type=ColumnType(CanonicalType.TIMESTAMP, "TIMESTAMP"),
                ),
                ColumnSchema(
                    name="tstz_col",
                    type=ColumnType(CanonicalType.TIMESTAMPTZ, "TIMESTAMPTZ"),
                ),
                ColumnSchema(
                    name="json_col",
                    type=ColumnType(CanonicalType.JSON, "JSONB"),
                ),
                ColumnSchema(
                    name="binary_col",
                    type=ColumnType(CanonicalType.BINARY, "BYTEA"),
                ),
                ColumnSchema(
                    name="unknown_col",
                    type=ColumnType(CanonicalType.UNKNOWN, "custom_type"),
                ),
            )
        )

        batch = RecordBatch(
            rows=(
                {
                    "str_col": "hello",
                    "int_col": 42,
                    "dec_col": Decimal("99.99"),
                    "float_col": 3.14,
                    "bool_col": True,
                    "date_col": today,
                    "ts_col": now,
                    "tstz_col": now,
                    "json_col": {"key": "value"},
                    "binary_col": b"\x00\x01\x02",
                    "unknown_col": "anything",
                },
            ),
            schema=schema,
        )

        json_str = json.dumps(batch.to_dict())
        restored = RecordBatch.from_dict(json.loads(json_str))

        row = restored.rows[0]
        assert row["str_col"] == "hello"
        assert row["int_col"] == 42
        assert row["dec_col"] == Decimal("99.99")
        assert abs(row["float_col"] - 3.14) < 0.0001
        assert row["bool_col"] is True
        assert row["date_col"] == today
        assert row["ts_col"].replace(tzinfo=None) == now.replace(tzinfo=None)
        assert row["json_col"] == {"key": "value"}
        assert row["binary_col"] == b"\x00\x01\x02"


class TestRecordBatchResumable:
    """RecordBatch resumable cursor for extraction recovery."""

    def test_batch_with_resumable_cursor(self) -> None:
        """Batches with resumable cursor round-trip."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="id",
                    type=ColumnType(CanonicalType.INTEGER, "BIGINT"),
                ),
            )
        )
        batch = RecordBatch(
            rows=({"id": 1}, {"id": 2}, {"id": 3}),
            schema=schema,
            resumable_cursor="2026-08-08T12:30:00Z",
            batch_number=5,
            is_final=False,
        )
        json_str = json.dumps(batch.to_dict())
        restored = RecordBatch.from_dict(json.loads(json_str))

        assert restored.resumable_cursor == "2026-08-08T12:30:00Z"
        assert restored.batch_number == 5
        assert restored.is_final is False
        assert len(restored.rows) == 3


class TestSampleRows:
    """SampleRows value object contract."""

    def test_sample_rows_round_trip(self) -> None:
        """SampleRows with mixed types survive serialization."""
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="id",
                    type=ColumnType(CanonicalType.INTEGER, "BIGINT"),
                ),
                ColumnSchema(
                    name="price",
                    type=ColumnType(CanonicalType.DECIMAL, "NUMERIC(10,2)"),
                ),
            ),
        )
        sample = SampleRows(
            rows=(
                {"id": 1, "price": Decimal("10.50")},
                {"id": 2, "price": Decimal("20.75")},
            ),
            schema=schema,
            actual_count=1000,
        )
        json_str = json.dumps(sample.to_dict())
        restored = SampleRows.from_dict(json.loads(json_str))

        assert len(restored.rows) == 2
        assert restored.rows[0]["price"] == Decimal("10.50")
        assert restored.actual_count == 1000


class TestProfileStats:
    """ProfileStats serialization contract."""

    def test_profile_stats_round_trip(self) -> None:
        """ProfileStats survive serialization."""
        stats = ProfileStats(
            column_stats={
                "id": {"null_count": 0, "distinct_count": 1000},
                "name": {"null_count": 5, "distinct_count": 995},
            },
            row_count=1000,
            sampling_rate=1.0,
        )
        json_str = json.dumps(stats.to_dict())
        restored = ProfileStats.from_dict(json.loads(json_str))

        assert restored.column_stats["id"]["distinct_count"] == 1000
        assert restored.row_count == 1000


class TestRemoteExecutionEnvelopes:
    """Connector work envelopes remain lossless across JSON transport."""

    def test_connection_target_round_trip(self) -> None:
        target = ConnectionTarget(
            connector_type="postgresql",
            endpoint="db.example.test:5432/warehouse",
            secret_ref="tenants/tenant-a/postgres/1",
            options={"sslmode": "verify-full"},
        )
        restored = ConnectionTarget.from_dict(json.loads(json.dumps(target.to_dict())))
        assert restored == target
        assert "password" not in restored.options

    def test_discovery_page_round_trip(self) -> None:
        page = DiscoveryPage(
            items=(
                Namespace(name="public"),
                SourceObject(name="orders", type="table", row_count=10),
            ),
            next_page_token="page-2",
        )
        restored = DiscoveryPage.from_dict(json.loads(json.dumps(page.to_dict())))
        assert restored == page


class TestExplicitUnknownValues:
    """Unknown is a tagged value and never confused with SQL null."""

    def test_unknown_value_round_trip(self) -> None:
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="opaque",
                    type=ColumnType(CanonicalType.UNKNOWN, "vendor_extension"),
                ),
            )
        )
        unknown = UnknownValue(reason="unmappable", native_type="vendor_extension")
        batch = RecordBatch(rows=({"opaque": unknown},), schema=schema)
        restored = RecordBatch.from_dict(json.loads(json.dumps(batch.to_dict())))
        assert restored.rows[0]["opaque"] == unknown
        assert restored.rows[0]["opaque"] is not None


class TestTimestampSafety:
    """Connector transport never invents timezone information."""

    def test_rejects_timezone_naive_datetime(self) -> None:
        schema = ObjectSchema(
            columns=(
                ColumnSchema(
                    name="occurred_at",
                    type=ColumnType(CanonicalType.TIMESTAMPTZ, "TIMESTAMPTZ"),
                ),
            )
        )
        batch = RecordBatch(
            rows=({"occurred_at": datetime(2026, 8, 8, 12, 0, 0)},),  # noqa: DTZ001
            schema=schema,
        )
        with pytest.raises(ValueError, match="Timezone-naive"):
            batch.to_dict()


class TestDiagnosticCheckOrdering:
    """Diagnostic checks enforce ordering (ADR-004 §5)."""

    def test_diagnostic_check_status_order(self) -> None:
        """Diagnostic check status enum is usable."""
        assert DiagnosticCheckStatus.PASS.value == "pass"
        assert DiagnosticCheckStatus.FAIL.value == "fail"
        assert DiagnosticCheckStatus.SKIPPED.value == "skipped"

    def test_diagnostic_check_type_canonical_order(self) -> None:
        """Diagnostic check types have the canonical order (network→latency)."""
        order = [
            DiagnosticCheckType.NETWORK,
            DiagnosticCheckType.TLS,
            DiagnosticCheckType.AUTHENTICATION,
            DiagnosticCheckType.AUTHORIZATION,
            DiagnosticCheckType.METADATA_ACCESS,
            DiagnosticCheckType.LATENCY,
        ]
        for i, check_type in enumerate(order):
            assert (
                check_type.value
                == [
                    "network",
                    "tls",
                    "authentication",
                    "authorization",
                    "metadata_access",
                    "latency",
                ][i]
            )

    def test_diagnostic_check_round_trip(self) -> None:
        """DiagnosticCheck survives serialization."""
        check = DiagnosticCheck(
            type=DiagnosticCheckType.AUTHENTICATION,
            status=DiagnosticCheckStatus.PASS,
            code="AUTH_SUCCESS",
            message="Successfully authenticated",
            remediation_hint=None,
            duration_ms=150,
        )
        json_str = json.dumps(check.to_dict())
        restored = DiagnosticCheck.from_dict(json.loads(json_str))

        assert restored.type == DiagnosticCheckType.AUTHENTICATION
        assert restored.status == DiagnosticCheckStatus.PASS
        assert restored.code == "AUTH_SUCCESS"
        assert restored.duration_ms == 150

    def test_connection_test_result_ordered(self) -> None:
        """ConnectionTestResult preserves check order."""
        checks = (
            DiagnosticCheck(
                type=DiagnosticCheckType.NETWORK,
                status=DiagnosticCheckStatus.PASS,
                code="NETWORK_OK",
                message="Network reachable",
                duration_ms=10,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.TLS,
                status=DiagnosticCheckStatus.PASS,
                code="TLS_OK",
                message="TLS handshake successful",
                duration_ms=50,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.AUTHENTICATION,
                status=DiagnosticCheckStatus.FAIL,
                code="AUTH_FAILED",
                message="Invalid credentials",
                remediation_hint="Check your password",
                duration_ms=30,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.AUTHORIZATION,
                status=DiagnosticCheckStatus.SKIPPED,
                code="SKIPPED",
                message="Skipped due to earlier failure",
                duration_ms=0,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.METADATA_ACCESS,
                status=DiagnosticCheckStatus.SKIPPED,
                code="SKIPPED",
                message="Skipped due to earlier failure",
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.LATENCY,
                status=DiagnosticCheckStatus.SKIPPED,
                code="SKIPPED",
                message="Skipped due to earlier failure",
            ),
        )

        result = ConnectionTestResult(success=False, checks=checks)
        json_str = json.dumps(result.to_dict())
        restored = ConnectionTestResult.from_dict(json.loads(json_str))

        assert len(restored.checks) == 6
        assert restored.checks[0].type == DiagnosticCheckType.NETWORK
        assert restored.checks[1].type == DiagnosticCheckType.TLS
        assert restored.checks[2].type == DiagnosticCheckType.AUTHENTICATION
        assert restored.checks[2].status == DiagnosticCheckStatus.FAIL
        assert restored.checks[3].type == DiagnosticCheckType.AUTHORIZATION
        assert restored.checks[3].status == DiagnosticCheckStatus.SKIPPED
        assert restored.success is False

    def test_rejects_out_of_order_diagnostics(self) -> None:
        """The value object rejects diagnostics whose sequence is ambiguous."""
        checks = tuple(
            DiagnosticCheck(
                type=check_type,
                status=DiagnosticCheckStatus.PASS,
                code="OK",
                message="ok",
            )
            for check_type in reversed(tuple(DiagnosticCheckType))
        )
        with pytest.raises(ValueError, match="canonical order"):
            ConnectionTestResult(success=True, checks=checks)

    def test_rejects_non_skipped_check_after_failure(self) -> None:
        """A failed check fences all later diagnostic work."""
        statuses = (
            DiagnosticCheckStatus.PASS,
            DiagnosticCheckStatus.FAIL,
            DiagnosticCheckStatus.PASS,
            DiagnosticCheckStatus.SKIPPED,
            DiagnosticCheckStatus.SKIPPED,
            DiagnosticCheckStatus.SKIPPED,
        )
        checks = tuple(
            DiagnosticCheck(type=kind, status=status, code="RESULT", message="result")
            for kind, status in zip(DiagnosticCheckType, statuses, strict=True)
        )
        with pytest.raises(ValueError, match="after the failure"):
            ConnectionTestResult(success=False, checks=checks)


class TestHealthStatus:
    """HealthStatus serialization contract."""

    def test_health_status_round_trip(self) -> None:
        """HealthStatus survives serialization."""
        check_time = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        status = HealthStatus(
            healthy=True,
            last_check=check_time,
            message="All systems operational",
        )
        json_str = json.dumps(status.to_dict())
        restored = HealthStatus.from_dict(json.loads(json_str))

        assert restored.healthy is True
        assert restored.message == "All systems operational"


class TestConnectionTestResultSuccess:
    """ConnectionTestResult contract for successful connections."""

    def test_successful_connection(self) -> None:
        """A successful connection test round-trips correctly."""
        checks = (
            DiagnosticCheck(
                type=DiagnosticCheckType.NETWORK,
                status=DiagnosticCheckStatus.PASS,
                code="NETWORK_OK",
                message="Connected",
                duration_ms=5,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.TLS,
                status=DiagnosticCheckStatus.PASS,
                code="TLS_OK",
                message="TLS verified",
                duration_ms=25,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.AUTHENTICATION,
                status=DiagnosticCheckStatus.PASS,
                code="AUTH_OK",
                message="Authenticated",
                duration_ms=30,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.AUTHORIZATION,
                status=DiagnosticCheckStatus.PASS,
                code="AUTHZ_OK",
                message="Authorized",
                duration_ms=20,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.METADATA_ACCESS,
                status=DiagnosticCheckStatus.PASS,
                code="METADATA_OK",
                message="Can read metadata",
                duration_ms=40,
            ),
            DiagnosticCheck(
                type=DiagnosticCheckType.LATENCY,
                status=DiagnosticCheckStatus.PASS,
                code="LATENCY_OK",
                message="Latency acceptable",
                duration_ms=10,
            ),
        )
        result = ConnectionTestResult(success=True, checks=checks)
        json_str = json.dumps(result.to_dict())
        restored = ConnectionTestResult.from_dict(json.loads(json_str))

        assert restored.success is True
        assert len(restored.checks) == 6
        for _i, check in enumerate(restored.checks):
            assert check.status == DiagnosticCheckStatus.PASS
