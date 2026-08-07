# 04 — Data Connectors & Semantic Layer

This document covers the two layers that turn arbitrary external systems into governed
business meaning: the **connector framework** (how we reach data) and the **semantic
layer** (how we give it business meaning). Together they guarantee **source-system
independence** — nothing downstream cares where a number physically came from.

## Connector framework

Every connector implements one common abstraction. Programming against this interface —
never against a specific vendor — is what keeps connectors provider-neutral (principle 4).

```python
class Connector(Protocol):
    def test_connection(self) -> ConnectionTestResult: ...
    def list_namespaces(self) -> list[Namespace]: ...     # schemas / databases / API groups
    def list_objects(self, namespace) -> list[SourceObject]: ...  # tables / endpoints / sheets
    def describe_object(self, object_ref) -> ObjectSchema: ...     # fields, types, keys
    def sample(self, object_ref, limit) -> SampleRows: ...         # a few rows for review
    def profile(self, object_ref) -> ProfileStats: ...             # nulls, cardinality, ranges
    def extract(self, object_ref, options) -> ExtractResult: ...   # pull data for ingestion
    def health(self) -> HealthStatus: ...                          # ongoing liveness
```

The method names are the contract:

- `test_connection()` — validate that we can reach and use the source (see diagnostics
  below).
- `list_namespaces()` — enumerate the top-level containers (schemas, databases, API
  resource groups, workbooks).
- `list_objects()` — enumerate queryable objects within a namespace (tables, views,
  endpoints, sheets).
- `describe_object()` — return an object's fields, types, and keys.
- `sample()` — return a small set of rows so a steward can eyeball the data.
- `profile()` — return column-level statistics (null rate, cardinality, min/max) to
  support mapping and data-quality signals.
- `extract()` — pull data for ingestion, supporting incremental options where possible.
- `health()` — report ongoing connector health for monitoring and alerting.

### Initial connectors

- PostgreSQL
- SQL Server
- REST API
- Excel
- CSV

### Future connectors

- MySQL
- Oracle
- Snowflake
- Databricks
- BigQuery
- Salesforce
- HubSpot
- NetSuite
- QuickBooks
- Google Sheets
- SharePoint

New connectors are added by implementing the abstraction; nothing downstream changes.

### Connection testing and diagnostics

`test_connection()` must verify, and report on, each of these independently:

- **Network** — can we reach the host/endpoint at all?
- **Authentication** — are the supplied credentials valid?
- **Authorization** — does the authenticated principal have the access we need?
- **Metadata access** — can we enumerate objects and describe schemas?
- **Latency** — how responsive is the source?

The result must return **meaningful diagnostics**, not a boolean. A steward should be
able to tell "wrong password" from "network unreachable" from "connected, but no
permission to read the schema" from the message alone. Connector credentials always use
**least privilege** — read-only, scoped to what discovery and extraction require.

## Semantic layer

Raw source fields **must not** directly drive dashboards, metrics, or chat. Between the
source and the metric sits the semantic layer, a set of configurable concepts that give
data business meaning independent of any source system.

### Core concepts

- **SemanticEntity** — a business object (e.g. `Revenue`, `Client`, `Opportunity`).
- **SemanticField** — a business attribute of an entity (e.g. `Revenue.Amount`).
- **Dimension** — a way to slice metrics (e.g. `operating_model`, `region`,
  `service_line`).
- **DimensionValue** — an allowed value of a dimension (e.g. `people`, `process`).
- **BusinessGlossaryTerm** — a human definition of a business term, for shared
  understanding and for the assistant.
- **SourceField** — a concrete field on a source object (e.g. `dbo.InvoiceHdr.inv_amt`).
- **FieldMapping** — the governed link from a source field to a semantic field.
- **Transformation** — the normalization/derivation applied along the way.

The workbook's `Total / People / Process / Technology / Enterprise` selector is modeled
here as a `Dimension` with its `DimensionValue`s — configuration, never code (see
[`02_WORKBOOK_FINDINGS.md`](02_WORKBOOK_FINDINGS.md)).

### Example mapping

```
dbo.InvoiceHdr.inv_amt        (SourceField)
      → normalization         (Transformation: cast to decimal, currency-normalize)
      → Revenue.Amount        (SemanticField on SemanticEntity "Revenue")
```

Downstream, `Revenue.Amount` is all the metric engine ever sees. If the source column is
renamed, or revenue starts coming from a different system, only the mapping changes —
the metric `revenue_ytd` and every dashboard and chat answer that uses it stay intact.

### AI-assisted mapping with human approval

AI may **suggest** mappings — proposing that `dbo.InvoiceHdr.inv_amt` maps to
`Revenue.Amount` with some confidence — but a **human must approve** before anything is
published (principle 6). This keeps the semantic model trustworthy and auditable.

Every mapping record must include:

- **source field** — the concrete `SourceField` being mapped.
- **semantic field** — the target `SemanticField`.
- **transformation** — the normalization/derivation applied.
- **confidence** — how sure the suggester is (relevant for AI suggestions).
- **origin** — who/what proposed it (AI suggestion, manual, imported).
- **approved by** — the human approver.
- **status** — draft / published / archived.
- **version** — for change tracking and rollback.

### Why this layer earns its keep

The semantic layer is what makes **source-system independence**, **governed metrics**,
and **explainability** all possible at once. It absorbs the messiness of real source
systems, presents clean business concepts upward, and records exactly how each concept
was derived — which is precisely the information lineage needs (see
[`05_KPI_INSIGHT_ENGINE.md`](05_KPI_INSIGHT_ENGINE.md)).
