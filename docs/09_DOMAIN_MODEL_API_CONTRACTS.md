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
  ([`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)).
- Mutations to governed objects are versioned and audited.
