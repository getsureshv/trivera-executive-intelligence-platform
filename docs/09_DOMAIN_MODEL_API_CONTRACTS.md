# 09 — Domain Model & API Contracts

This document proposes the domain entities and the conceptual API surface. It is a
**direction**, not a frozen schema; concrete data models and endpoint specifications are
produced during implementation phases and refined via ADRs.

## Proposed domain entities

Grouped by bounded context (see
[`03_PLATFORM_ARCHITECTURE.md`](03_PLATFORM_ARCHITECTURE.md)). Every tenant-owned entity
carries a `tenant_id`; governed entities carry status/version where relevant.

**Identity & Tenant**

- `Tenant` — an organization.
- `User` — a person.
- `Membership` — a user's membership in a tenant.
- `Role` — a named role.
- `Permission` — a grantable capability.

**Source Connectivity**

- `DataSource` — a configured connection to an external system.
- `SourceObject` — a discovered object (table/endpoint/sheet).
- `SourceField` — a field on a source object.
- `ConnectionTest` — the result of testing a data source.
- `IngestionJob` — an extraction/load job.

**Semantic Model**

- `SemanticEntity` — a business object.
- `SemanticField` — a business attribute.
- `Dimension` — a slicing axis.
- `DimensionValue` — an allowed value of a dimension.
- `FieldMapping` — source field → semantic field, governed.
- `Transformation` — a normalization/derivation.
- `GlossaryTerm` — a business definition.

**Metric Governance**

- `Metric` — a governed metric definition.
- `MetricDimension` — a dimension a metric can be sliced by.
- `MetricFilter` — a default/allowed filter on a metric.
- `MetricTarget` — a target/plan for a metric.
- `MetricThreshold` — a threshold that drives status/signals.
- `MetricVersion` — a versioned snapshot of a metric.
- `MetricLineage` — the traceable derivation of a metric.

**Dashboard / Experience**

- `Dashboard` — a collection of widgets.
- `Widget` — a single visualization/KPI bound to a metric.
- `SavedView` — a saved set of filters/slices.

**Insight**

- `Signal` — a deterministic/statistical detection over a metric.
- `Insight` — a fact/correlation/hypothesis/question derived from signals.
- `AlertRule` — a rule that triggers alerts.
- `AlertEvent` — a fired alert.

**Audit / Governance**

- `AuditEvent` — a recorded governance/security event.

> **Phase 0 update.** The entity list changes as follows. Details in the ADRs and in
> [`18_FIRST_VERTICAL_SLICE.md`](18_FIRST_VERTICAL_SLICE.md) §1, which carries the full
> field-level model.
>
> **Removed**
> - `MetricLineage` — lineage is **derived** from the metric AST and binding graph, never
>   stored as a system of record ([ADR-012](adr/ADR-012-data-lineage.md)). A
>   `lineage_projection` exists only as an explicitly-labelled cache.
>
> **Changed**
> - `FieldMapping` is demoted to a component of `EntityBinding`, which becomes the
>   governed unit of mapping ([ADR-005](adr/ADR-005-semantic-model.md)).
> - `Metric` splits into `Metric` (identity, ownership) and `MetricVersion` (the typed
>   AST, **immutable once published**) ([ADR-006](adr/ADR-006-metric-definition-and-kpi-engine.md)).
>
> **Added**
> - `SemanticRelationship` — cardinality-aware join model; without it a metric can only be
>   sliced by columns on its own physical table.
> - `EntityBinding`, `FieldBinding`, `TimeBinding`, `BindingValidation` — grain, row
>   filter, units, time anchors, and the automated publish gate.
> - `DimensionHierarchy` — metadata-driven drill-down.
> - `ConfigurationBundle`, `BundleManifestEntry` — the atomic release unit
>   ([ADR-013](adr/ADR-013-configuration-versioning.md)).
> - `MetricAssertion`, `MetricObservation` (append-only history), `FiscalCalendar`.
> - `PipelineRun`, `PipelineStep`, `SourceWatermark`, `QualityCheckResult`, `Outbox`
>   ([ADR-009](adr/ADR-009-background-job-architecture.md)) — run history is **product
>   data**, feeding freshness badges and provenance.
> - `RowPolicy` — semantic row-level security
>   ([ADR-010](adr/ADR-010-authentication-and-authorization.md)).
> - `FieldProfile` — profiling baselines behind data-quality signals.

These entities line up with the metric model in
[`05_KPI_INSIGHT_ENGINE.md`](05_KPI_INSIGHT_ENGINE.md), the semantic concepts in
[`04_DATA_CONNECTORS_SEMANTIC_LAYER.md`](04_DATA_CONNECTORS_SEMANTIC_LAYER.md), and the
versioning rules in [`07_SECURITY_MULTITENANCY_GOVERNANCE.md`](07_SECURITY_MULTITENANCY_GOVERNANCE.md).

## Conceptual API surface

API-first: every capability is available through a versioned API, and the UI is a client
of it. These endpoints are **conceptual direction**, to be firmed up during
implementation.

**Data sources**

```
POST /v1/data-sources                 # create/configure a data source
GET  /v1/data-sources                 # list data sources
POST /v1/data-sources/{id}/test       # run connection diagnostics
POST /v1/data-sources/{id}/discover   # discover objects and fields
```

**Semantic model**

```
GET  /v1/semantic/entities            # list semantic entities
POST /v1/semantic/entities            # create a semantic entity
POST /v1/mappings/suggest             # AI-suggest field mappings
POST /v1/mappings                     # create a mapping (draft)
POST /v1/mappings/{id}/approve        # human approval → publish
```

**Metrics**

```
GET  /v1/metrics                      # list governed metrics
POST /v1/metrics                      # create a metric (draft)
POST /v1/metrics/{code}/query         # governed metric query (dimensions/filters)
GET  /v1/metrics/{code}/lineage       # metric lineage
```

**Dashboards**

```
GET  /v1/dashboards                   # list dashboards
POST /v1/dashboards                   # create a dashboard
```

**Assistant**

```
POST /v1/assistant/query              # "Ask Your Business" governed query
```

> **Phase 0 update ([ADR-007](adr/ADR-007-governed-query-engine.md) §6 and the review's
> W14).** The surface above is under-specified for real use. Required additions:
>
> - **`POST /v1/query` is the primary query contract and accepts multiple metrics.** A
>   twelve-card executive home page must be **one** request, not twelve — each of which
>   would otherwise repeat authorization, bundle resolution, and compilation.
>   `POST /v1/metrics/{code}/query` is retained as single-metric sugar.
> - **Long-running operations return a job envelope** (`202` + `job_id` +
>   `GET /v1/jobs/{id}`). `POST /v1/data-sources/{id}/discover` and `.../profile` are
>   asynchronous; a synchronous POST against a large catalog will not work.
> - **Mutations accept `Idempotency-Key`**; governed objects use **`ETag` / `If-Match`**
>   for optimistic concurrency (the steward UI needs it).
> - **All list endpoints are cursor-paginated.**
> - **Errors are RFC 9457 `application/problem+json`** with a machine-readable code, a
>   correlation id, and per-check detail (e.g. `GRAIN_NOT_UNIQUE`,
>   `AMBIGUOUS_JOIN_PATH`, `FANOUT_UNSAFE`).
> - **Configuration bundle endpoints** for draft/validate/publish/activate/export
>   ([ADR-013](adr/ADR-013-configuration-versioning.md)).
> - **Binding endpoints** replace bare mapping endpoints: `POST /v1/bindings`,
>   `POST /v1/bindings/{id}/validate`, `POST /v1/bindings/{id}/approve`.
>
> Concrete request/response shapes are in
> [`18_FIRST_VERTICAL_SLICE.md`](18_FIRST_VERTICAL_SLICE.md) §2.

## Hard API rule

**No arbitrary SQL is ever accepted from browser clients.** Clients request governed
metrics by code with dimensions and filters (`POST /v1/metrics/{code}/query`) or ask the
governed assistant (`POST /v1/assistant/query`). The `mappings/suggest` and
`mappings/{id}/approve` split encodes principle 6: AI suggests, a human approves before
publish. This is the API-level expression of "governed metrics over arbitrary SQL."

## Cross-cutting contract expectations

- Every endpoint is tenant-scoped and authorized before data access.
- Metric query responses carry value **plus** period, filters, comparison, freshness,
  quality, and lineage availability — the same answer contract the assistant honors
  ([`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)) — **plus a `provenance`
  block** (`config_version`, `metric_version`, `plan_hash`, `data_snapshot_id`,
  `computed_at`, `cache_hit`, `engine`) and an `authorization` block (`row_scope_applied`,
  `fields_masked`). Provenance is what makes "why did this number change since last
  Tuesday?" answerable ([ADR-007](adr/ADR-007-governed-query-engine.md) §3,
  [ADR-012](adr/ADR-012-data-lineage.md)). The envelope is **mandatory**: a surface that
  cannot render freshness and quality does not render a number.
- Mutations to governed objects are versioned and audited.
