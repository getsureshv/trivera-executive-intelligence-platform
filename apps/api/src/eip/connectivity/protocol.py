"""Provider-neutral connector protocol and value objects (ADR-004).

The Connector protocol defines discovery, sampling, profiling, and extraction
against any external data source. All value objects are serializable and
remotely executable, supporting the tenant-deployed agent mode (ADR-004).

Type normalization: every connector maps native types into a canonical system
(string, integer, decimal, float, boolean, date, timestamp, timestamptz, json,
binary, unknown) and preserves the original native type alongside. Decimals are
never silently converted to floats. Unmappable types surface as `unknown`.

Diagnostics are ordered and structured: network → tls → authentication →
authorization → metadata_access → latency. Each check reports pass|fail|skipped
with a machine-readable code, remediation hint, and duration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

# =============================================================================
# Canonical type system
# =============================================================================


class CanonicalType(StrEnum):
    """Platform canonical scalar types. Unknown is explicit."""

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    JSON = "json"
    BINARY = "binary"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ColumnType:
    """A column's canonical type and native representation.

    The canonical type drives query planning and transformations. The native
    type is preserved for audit and is visible to the steward.
    """

    canonical: CanonicalType
    native: str


# =============================================================================
# Connector capabilities
# =============================================================================


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    """Declarative connector capabilities (ADR-004).

    Callers branch on these capabilities, never on connector identity.
    This is what makes principle 4 (provider neutrality) mechanically true
    rather than aspirational.
    """

    supports_incremental: bool = False
    supports_cdc: bool = False
    supports_predicate_pushdown: bool = False
    supports_column_projection: bool = False
    supports_server_side_aggregation: bool = False
    supports_exact_distinct_count: bool = False
    supports_transactional_snapshot: bool = False
    supports_schema_discovery: bool = False
    supports_statistics: bool = False
    max_parallel_streams: int = 1
    rate_limit_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON transport."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectorCapabilities:
        """Deserialize from a dict."""
        return cls(**data)


# =============================================================================
# Configuration schema
# =============================================================================


@dataclass(frozen=True, slots=True)
class JsonSchema:
    """A JSON Schema fragment (minimal subset needed for UI generation).

    A connector's config_schema() returns this to drive generic form
    generation in the UI. Credentials are never schema values; operational
    envelopes carry only an opaque SecretRef (ADR-015).
    """

    schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JsonSchema:
        return cls(schema=data.get("schema", {}))


# =============================================================================
# Connection targets and discovery
# =============================================================================


@dataclass(frozen=True, slots=True)
class Namespace:
    """A namespace (schema, database, catalog) discovered by the connector.

    Namespaces are the first level of discovery hierarchy. Not all sources
    have meaningful namespaces (e.g., CSV files).
    """

    name: str
    description: str | None = None
    object_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Namespace:
        fields = {"name", "description", "object_count"}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass(frozen=True, slots=True)
class NamespaceRef:
    """A reference to a namespace for subsequent operations."""

    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NamespaceRef:
        return cls(name=data["name"])


@dataclass(frozen=True, slots=True)
class SourceObject:
    """An object (table, view, file, topic) discoverable within a namespace."""

    name: str
    type: Literal["table", "view", "materialized_view", "file", "topic", "unknown"] = "unknown"
    description: str | None = None
    row_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceObject:
        fields = {"name", "type", "description", "row_count"}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A reference to a specific object for sampling, profiling, or extraction.

    If namespace is None, the source has no namespace concept (e.g., files).
    """

    namespace: str | None
    name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObjectRef:
        return cls(namespace=data.get("namespace"), name=data["name"])


@dataclass(frozen=True, slots=True)
class ConnectionTarget:
    """Serializable routing data for executing connector work remotely.

    ``secret_ref`` is an opaque reference. Credential values are never part of
    this envelope (ADR-015).
    """

    connector_type: str
    endpoint: str
    secret_ref: str
    connectivity_mode: Literal["direct", "private_link", "agent"] = "direct"
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_type": self.connector_type,
            "endpoint": self.endpoint,
            "secret_ref": self.secret_ref,
            "connectivity_mode": self.connectivity_mode,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionTarget:
        return cls(
            connector_type=data["connector_type"],
            endpoint=data["endpoint"],
            secret_ref=data["secret_ref"],
            connectivity_mode=data.get("connectivity_mode", "direct"),
            options=data.get("options", {}),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    """A serializable page of source discovery results."""

    items: tuple[Namespace | SourceObject, ...] = field(default_factory=tuple)
    next_page_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "kind": "namespace" if isinstance(item, Namespace) else "object",
                    "value": item.to_dict(),
                }
                for item in self.items
            ],
            "next_page_token": self.next_page_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryPage:
        items: list[Namespace | SourceObject] = []
        for item in data.get("items", []):
            value = item["value"]
            items.append(
                Namespace.from_dict(value)
                if item["kind"] == "namespace"
                else SourceObject.from_dict(value)
            )
        return cls(items=tuple(items), next_page_token=data.get("next_page_token"))


# =============================================================================
# Object schema
# =============================================================================


@dataclass(frozen=True, slots=True)
class ColumnSchema:
    """A column in the object schema."""

    name: str
    type: ColumnType
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": {
                "canonical": self.type.canonical.value,
                "native": self.type.native,
            },
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnSchema:
        type_data = data["type"]
        return cls(
            name=data["name"],
            type=ColumnType(
                canonical=CanonicalType(type_data["canonical"]),
                native=type_data["native"],
            ),
            nullable=data.get("nullable", True),
            primary_key=data.get("primary_key", False),
            unique=data.get("unique", False),
            description=data.get("description"),
        )


@dataclass(frozen=True, slots=True)
class ObjectSchema:
    """The schema of a discovered object."""

    columns: tuple[ColumnSchema, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"columns": [c.to_dict() for c in self.columns]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObjectSchema:
        columns = tuple(ColumnSchema.from_dict(c) for c in data.get("columns", []))
        return cls(columns=columns)


# =============================================================================
# Extraction plan
# =============================================================================


@dataclass(frozen=True, slots=True)
class ExtractPlan:
    """A plan for extracting data from a source object.

    mode: 'full' (full reload), 'incremental' (from cursor), 'cdc' (change data)
    cursor: watermark/checkpoint for resumable extraction (None for full mode)
    column_projection: if provided, only extract these columns
    pushdown_predicate: optional WHERE clause for capable sources
    batch_size: target rows per batch
    """

    mode: Literal["full", "incremental", "cdc"] = "full"
    cursor: str | None = None
    column_projection: tuple[str, ...] | None = None
    pushdown_predicate: str | None = None
    batch_size: int = 10000

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cursor": self.cursor,
            "column_projection": list(self.column_projection) if self.column_projection else None,
            "pushdown_predicate": self.pushdown_predicate,
            "batch_size": self.batch_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractPlan:
        return cls(
            mode=data.get("mode", "full"),
            cursor=data.get("cursor"),
            column_projection=tuple(data["column_projection"])
            if data.get("column_projection")
            else None,
            pushdown_predicate=data.get("pushdown_predicate"),
            batch_size=data.get("batch_size", 10000),
        )


# =============================================================================
# Record batch
# =============================================================================


@dataclass(frozen=True, slots=True)
class UnknownValue:
    """Explicit value for an unmappable source value; distinct from null."""

    reason: str
    native_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"__eip_unknown__": {"reason": self.reason, "native_type": self.native_type}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnknownValue:
        payload = data["__eip_unknown__"]
        return cls(reason=payload["reason"], native_type=payload.get("native_type"))


def _serialize_value(value: Any, col_type: CanonicalType = CanonicalType.UNKNOWN) -> Any:
    """Serialize a Python value for JSON."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UnknownValue):
        return value.to_dict()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "Timezone-naive datetime values are not serializable"
            raise ValueError(msg)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    return str(value)


def _deserialize_value(value: Any, col_type: CanonicalType) -> Any:
    """Deserialize a JSON value back to Python, respecting the canonical type."""
    if value is None:
        return None
    if col_type == CanonicalType.UNKNOWN:
        if isinstance(value, dict) and "__eip_unknown__" in value:
            return UnknownValue.from_dict(value)
        return value
    if col_type == CanonicalType.STRING:
        return str(value) if value is not None else None
    if col_type == CanonicalType.INTEGER:
        return int(value) if value is not None else None
    if col_type == CanonicalType.DECIMAL:
        return Decimal(str(value)) if value is not None else None
    if col_type == CanonicalType.FLOAT:
        return float(value) if value is not None else None
    if col_type == CanonicalType.BOOLEAN:
        return bool(value) if value is not None else None
    if col_type == CanonicalType.DATE:
        return date.fromisoformat(value) if isinstance(value, str) else value
    if col_type == CanonicalType.TIMESTAMP:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if col_type == CanonicalType.TIMESTAMPTZ:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if col_type == CanonicalType.JSON:
        return value
    if col_type == CanonicalType.BINARY:
        return bytes.fromhex(value) if isinstance(value, str) else value


@dataclass(frozen=True, slots=True)
class RecordBatch:
    """A batch of rows from an extraction, with resumable cursor.

    rows: list of dicts mapping column names to values
    schema: the schema of the columns
    resumable_cursor: watermark/checkpoint for resuming from this batch
    batch_number: ordinal position for ordering/reconciliation
    is_final: whether this is the final batch
    """

    rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    schema: ObjectSchema = field(default_factory=ObjectSchema)
    resumable_cursor: str | None = None
    batch_number: int = 0
    is_final: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON transport.

        Values are serialized respecting canonical types: decimals stay as
        strings, timestamps include zone info, unknown types pass through.
        """
        # Build a map of column names to their canonical types
        type_map: dict[str, CanonicalType] = {}
        for col in self.schema.columns:
            type_map[col.name] = col.type.canonical

        return {
            "rows": [
                {
                    col_name: _serialize_value(value, type_map.get(col_name, CanonicalType.UNKNOWN))
                    for col_name, value in row.items()
                }
                for row in self.rows
            ],
            "schema": self.schema.to_dict(),
            "resumable_cursor": self.resumable_cursor,
            "batch_number": self.batch_number,
            "is_final": self.is_final,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordBatch:
        """Deserialize from JSON, respecting canonical types."""
        schema = ObjectSchema.from_dict(data["schema"])

        # Build type map
        type_map: dict[str, CanonicalType] = {}
        for col in schema.columns:
            type_map[col.name] = col.type.canonical

        # Deserialize rows
        rows = []
        for row_data in data.get("rows", []):
            row = {}
            for col_name, value in row_data.items():
                col_type = type_map.get(col_name, CanonicalType.UNKNOWN)
                row[col_name] = _deserialize_value(value, col_type)
            rows.append(row)

        return cls(
            rows=tuple(rows),
            schema=schema,
            resumable_cursor=data.get("resumable_cursor"),
            batch_number=data.get("batch_number", 0),
            is_final=data.get("is_final", False),
        )


# =============================================================================
# Sampling and profiling
# =============================================================================


@dataclass(frozen=True, slots=True)
class SampleRows:
    """Sample rows from an object for inspection and validation."""

    rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    schema: ObjectSchema = field(default_factory=ObjectSchema)
    actual_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        type_map = {column.name: column.type.canonical for column in self.schema.columns}
        return {
            "rows": [
                {
                    col_name: _serialize_value(value, type_map.get(col_name, CanonicalType.UNKNOWN))
                    for col_name, value in row.items()
                }
                for row in self.rows
            ],
            "schema": self.schema.to_dict(),
            "actual_count": self.actual_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SampleRows:
        schema = ObjectSchema.from_dict(data["schema"])
        type_map: dict[str, CanonicalType] = {}
        for col in schema.columns:
            type_map[col.name] = col.type.canonical

        rows = []
        for row_data in data.get("rows", []):
            row = {}
            for col_name, value in row_data.items():
                col_type = type_map.get(col_name, CanonicalType.UNKNOWN)
                row[col_name] = _deserialize_value(value, col_type)
            rows.append(row)

        return cls(
            rows=tuple(rows),
            schema=schema,
            actual_count=data.get("actual_count", 0),
        )


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    """Specification for what profiling statistics to compute."""

    compute_nullability: bool = True
    compute_distinct_count: bool = True
    compute_min_max: bool = True
    compute_histogram: bool = False
    sample_size: int = 10000


@dataclass(frozen=True, slots=True)
class ProfileStats:
    """Profiling statistics for a column or object."""

    column_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    row_count: int | None = None
    sampling_rate: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_stats": self.column_stats,
            "row_count": self.row_count,
            "sampling_rate": self.sampling_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileStats:
        return cls(
            column_stats=data.get("column_stats", {}),
            row_count=data.get("row_count"),
            sampling_rate=data.get("sampling_rate", 1.0),
        )


# =============================================================================
# Connection diagnostics
# =============================================================================


class DiagnosticCheckStatus(StrEnum):
    """Ordered status values for diagnostic checks."""

    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    SKIPPED = "skipped"


class DiagnosticCheckType(StrEnum):
    """Ordered diagnostic check sequence (ADR-004).

    Order matters: skipped checks after a failure are reported as skipped,
    not as failures. This enum defines the canonical order.
    """

    NETWORK = "network"
    TLS = "tls"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    METADATA_ACCESS = "metadata_access"
    LATENCY = "latency"


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """A single diagnostic check result.

    Secrets never appear in diagnostics. All fields are machine-readable
    for automation and user-readable for troubleshooting.
    """

    type: DiagnosticCheckType
    status: DiagnosticCheckStatus
    code: str
    message: str
    remediation_hint: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "status": self.status.value,
            "code": self.code,
            "message": self.message,
            "remediation_hint": self.remediation_hint,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiagnosticCheck:
        return cls(
            type=DiagnosticCheckType(data["type"]),
            status=DiagnosticCheckStatus(data["status"]),
            code=data["code"],
            message=data["message"],
            remediation_hint=data.get("remediation_hint"),
            duration_ms=data.get("duration_ms", 0),
        )


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """Result of a connection test, with ordered diagnostics.

    Diagnostics are ordered: if a check fails, subsequent checks are
    skipped rather than executed.
    """

    success: bool
    checks: tuple[DiagnosticCheck, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        expected = tuple(DiagnosticCheckType)
        actual = tuple(check.type for check in self.checks)
        if actual != expected:
            msg = "Connection diagnostics must contain all checks in canonical order"
            raise ValueError(msg)

        failed = [index for index, check in enumerate(self.checks) if check.status == "fail"]
        if self.success:
            if any(check.status != "pass" for check in self.checks):
                msg = "Successful diagnostics require every check to pass"
                raise ValueError(msg)
            return
        if len(failed) != 1:
            msg = "Failed diagnostics require exactly one failed check"
            raise ValueError(msg)
        failure_index = failed[0]
        if any(check.status != "pass" for check in self.checks[:failure_index]):
            msg = "Checks before the failure must pass"
            raise ValueError(msg)
        if any(check.status != "skipped" for check in self.checks[failure_index + 1 :]):
            msg = "Checks after the failure must be skipped"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "checks": [c.to_dict() for c in self.checks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionTestResult:
        checks = tuple(DiagnosticCheck.from_dict(c) for c in data.get("checks", []))
        return cls(success=data["success"], checks=checks)


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Current health status of a connector."""

    healthy: bool
    last_check: datetime | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthStatus:
        last_check = None
        if data.get("last_check"):
            last_check = datetime.fromisoformat(data["last_check"])
        return cls(
            healthy=data["healthy"],
            last_check=last_check,
            message=data.get("message"),
        )


# =============================================================================
# Connector protocol
# =============================================================================


@runtime_checkable
class Connector(Protocol):
    """Provider-neutral connector abstraction (ADR-004).

    A connector models discovery, sampling, profiling, and extraction of data
    from an external source. Every method is async to support both long-running
    I/O and cancellation. Extraction is streaming and resumable, supporting
    large data volumes and unreliable networks.

    All return types are serializable for remote execution (tenant-deployed
    agent mode).
    """

    # --- static, cheap, no I/O ---

    def capabilities(self) -> ConnectorCapabilities:
        """Return the connector's static capabilities.

        Used by the ingestion planner to decide which operations are possible
        (incremental vs. full, pushdown vs. local filter, etc.).
        """
        ...

    def config_schema(self) -> JsonSchema:
        """Return a JSON Schema fragment that drives connector configuration UI.

        Used by the web application to generate a generic form for entering
        non-secret connector configuration. Credentials are supplied only by
        opaque SecretRef.
        """
        ...

    # --- diagnostics ---

    async def test_connection(self) -> ConnectionTestResult:
        """Test the connection and return ordered diagnostics.

        Executes checks in order: network, tls, authentication, authorization,
        metadata_access, latency. If a check fails, subsequent checks are
        skipped. Secrets never appear in diagnostics.

        Returns:
            A ConnectionTestResult with ordered DiagnosticCheck objects.
        """
        ...

    async def health(self) -> HealthStatus:
        """Return the current health status of the connector."""
        ...

    # --- discovery (async, paginated, cancellable) ---

    async def list_namespaces(self) -> AsyncIterator[Namespace]:
        """Discover namespaces (schemas, databases, catalogs) in the source.

        Not all sources have meaningful namespaces (e.g., CSV files).
        Yields Namespace objects. The caller may cancel iteration at any time.
        """
        ...

    async def list_objects(self, ns: NamespaceRef) -> AsyncIterator[SourceObject]:
        """Discover objects (tables, views, files, topics) within a namespace.

        Yields SourceObject objects. The caller may cancel iteration at any time.
        """
        ...

    async def describe_object(self, ref: ObjectRef) -> ObjectSchema:
        """Get the schema of a specific object.

        Returns:
            An ObjectSchema with ColumnSchema for each column.
        """
        ...

    # --- inspection ---

    async def sample(self, ref: ObjectRef, limit: int) -> SampleRows:
        """Return sample rows from an object.

        Used for preview and validation during mapping configuration.
        """
        ...

    async def profile(self, ref: ObjectRef, spec: ProfileSpec) -> ProfileStats:
        """Compute profiling statistics on an object.

        Used for data-quality signals and to guide sampling/extraction
        strategies.
        """
        ...

    # --- extraction (streaming, resumable) ---

    async def extract(self, ref: ObjectRef, plan: ExtractPlan) -> AsyncIterator[RecordBatch]:
        """Extract data from an object, streaming batches.

        Each batch includes a resumable_cursor so extraction can restart
        from the last committed batch. Supports full, incremental, and
        CDC extraction modes (if supported by the connector).

        Yields:
            RecordBatch objects with rows and resumable cursor.
        """
        ...
