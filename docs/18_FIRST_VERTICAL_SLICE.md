# 18 — First Vertical Slice (Design Only)

Date: 2026-08-07
Status: **Design. Not implemented. Do not implement until Phase 0 is approved.**
Depends on: [`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md),
ADR-001 … ADR-015

---

## Purpose

Prove the whole spine end to end with the smallest feature set that can **falsify** the
architecture. This slice is not a demo; it is an experiment. It succeeds only if it
demonstrates that the four riskiest Phase 0 assumptions hold:

1. An **entity binding** can be declared and **automatically validated** against a
   semantic contract (ADR-005) — including grain.
2. A **typed metric AST** compiles into a governed query that produces a number equal to a
   hand-computed control (ADR-006/007).
3. **Lineage derives** from the binding graph with no hand-maintained lineage records
   (ADR-012).
4. **Tenant isolation** holds under adversarial test — including the cache (ADR-003/007).

If any of these fails, the architecture changes before breadth is added. That is the point
of doing this first.

## The walk

```
Create Tenant
  → Add PostgreSQL Data Source
  → Test Connection
  → Discover Schema
  → Discover Table
  → Profile Fields
  → Bind source amount field to Revenue.Amount
  → Define Revenue YTD
  → Execute governed metric query
  → Display KPI
  → Drill into KPI
  → Show complete lineage
```

## Deliberate scope boundaries

**In scope:** one tenant, one PostgreSQL source, one table, one semantic entity
(`Revenue`), one dimension, one metric, one KPI card, one drill-down, one lineage view,
two users with different row scopes (for the isolation test).

**Out of scope, and stated so it is not smuggled in:** AI mapping suggestions, the
assistant, insights and signals, alerts, dashboards-as-a-builder, file connectors,
multi-source union, multi-currency, forecasts, the onboarding wizard's industry packs, and
SSO (the slice uses a single configured OIDC provider).

**The one thing that looks optional but is not:** the slice must include a
tenant-configured **dimension** (not a hard-coded one) and a **drill-down by that
dimension**, because that is what proves the workbook selector is genuinely metadata and
that join-path resolution works. A single ungrouped scalar KPI would pass without proving
anything.

---

## 1. Database Entities

Metadata plane (shared schema, `tenant_id`, forced RLS — ADR-003). Types are indicative.

### Identity & Tenant
| Entity | Key fields | Notes |
| --- | --- | --- |
| `tenant` | `id`, `slug`, `name`, `analytical_schema`, `status`, `created_at` | The only non-tenant-scoped table in the slice |
| `user` | `id`, `external_subject`, `email`, `status` | Identity delegated to OIDC (ADR-010) |
| `membership` | `id`, `tenant_id`, `user_id`, `role_code` | A user's role within a tenant |
| `role` / `role_capability` | `code`, `capability` | Platform roles; `tenant_admin`, `data_steward`, `executive` for the slice |
| `row_policy` | `id`, `tenant_id`, `subject_type`, `subject_id`, `dimension_code`, `operator`, `values` | Needed for the isolation test |
| `fiscal_calendar` | `id`, `tenant_id`, `type`, `year_start_month`, `year_start_day` | `fiscal_ytd` cannot resolve without it (M5) |

### Configuration governance
| Entity | Key fields | Notes |
| --- | --- | --- |
| `configuration_bundle` | `id`, `tenant_id`, `config_version`, `status`, `content_hash`, `parent_version`, `change_reason`, `author_id`, `approver_id`, `published_at`, `validation_report` | ADR-013; immutable when published |
| `bundle_manifest_entry` | `bundle_id`, `object_type`, `object_id`, `object_version` | The exact versions the bundle pins |
| `audit_event` | `id`, `tenant_id`, `occurred_at`, `actor_id`, `action`, `object_type`, `object_id`, `detail`, `prev_hash`, `hash` | Append-only, hash-chained (ADR-014) |
| `outbox` | `id`, `tenant_id`, `topic`, `payload`, `created_at`, `published_at` | ADR-009 |

### Source connectivity
| Entity | Key fields | Notes |
| --- | --- | --- |
| `data_source` | `id`, `tenant_id`, `name`, `connector_type`, `config` (jsonb, **no secrets**), `secret_ref`, `connectivity_mode`, `status` | ADR-015: reference only |
| `connection_test` | `id`, `tenant_id`, `data_source_id`, `run_at`, `overall_status`, `checks` (jsonb) | Structured diagnostics per ADR-004 |
| `source_namespace` | `id`, `tenant_id`, `data_source_id`, `name` | A discovered schema |
| `source_object` | `id`, `tenant_id`, `namespace_id`, `name`, `object_type`, `discovered_at`, `row_estimate` | A discovered table |
| `source_field` | `id`, `tenant_id`, `source_object_id`, `name`, `ordinal`, `native_type`, `canonical_type`, `nullable`, `is_primary_key` | Canonical type per ADR-004 |
| `field_profile` | `id`, `tenant_id`, `source_field_id`, `profiled_at`, `null_rate`, `distinct_count`, `min`, `max`, `mean`, `sample_values_masked`, `pipeline_run_id` | Baseline for quality signals |

### Data operations
| Entity | Key fields | Notes |
| --- | --- | --- |
| `pipeline_run` | `id`, `tenant_id`, `kind`, `data_source_id`, `config_version`, `status`, `trigger`, `requested_by`, `started_at`, `finished_at`, `snapshot_id`, `error_summary` | ADR-009; **product data**, feeds freshness |
| `pipeline_step` | `id`, `run_id`, `ordinal`, `name`, `status`, `attempt`, `input_ref`, `output_ref`, `metrics`, `error` | |
| `source_watermark` | `tenant_id`, `source_object_id`, `cursor`, `committed_at`, `run_id` | Committed atomically with its batch |
| `quality_check_result` | `id`, `tenant_id`, `subject_type`, `subject_id`, `check_code`, `status`, `detail`, `run_id` | Drives the KPI card's quality badge |

### Semantic model (ADR-005)
| Entity | Key fields | Notes |
| --- | --- | --- |
| `semantic_entity` | `id`, `tenant_id`, `code`, `kind`, `grain_description`, `natural_key_fields`, `default_time_anchor`, `version`, `status` | `Revenue` (from the platform pack) |
| `semantic_field` | `id`, `tenant_id`, `entity_id`, `code`, `data_type`, `semantic_type`, `unit`, `additivity`, `sign_convention`, `classification`, `required` | `Revenue.Amount`, `Revenue.RecognizedAt`, `Revenue.SegmentRef` |
| `semantic_relationship` | `id`, `tenant_id`, `from_entity_id`, `from_fields`, `to_entity_id`, `to_fields`, `cardinality`, `role`, `join_type` | Present in the slice even if the dimension is on the same object — the resolver must be exercised |
| `dimension` | `id`, `tenant_id`, `code`, `name`, `semantic_field_id`, `value_mode` | The tenant-configured slicing axis |
| `dimension_value` | `id`, `tenant_id`, `dimension_id`, `code`, `label`, `ordinal` | **No `total` value** (ADR-005 §5) |
| `entity_binding` | `id`, `tenant_id`, `semantic_entity_id`, `source_object_id`, `grain_assertion`, `row_filter` (AST jsonb), `currency_policy`, `source_priority`, `version`, `status`, `author_id`, `approver_id`, `change_reason` | **The unit of mapping** |
| `field_binding` | `id`, `tenant_id`, `entity_binding_id`, `source_field_id`, `semantic_field_id`, `transformation` (AST jsonb), `origin`, `confidence` | Component of a binding |
| `time_binding` | `id`, `tenant_id`, `entity_binding_id`, `time_anchor_code`, `source_field_id` | |
| `binding_validation` | `id`, `tenant_id`, `entity_binding_id`, `run_at`, `status`, `checks` (jsonb) | Publish gate (M1) |

### Metric governance (ADR-006)
| Entity | Key fields | Notes |
| --- | --- | --- |
| `metric` | `id`, `tenant_id`, `code`, `name`, `description`, `domain`, `owner_id`, `format`, `unit`, `direction`, `status` | Identity, not definition |
| `metric_version` | `id`, `metric_id`, `version`, `ast` (jsonb), `time_anchor`, `default_period`, `allowed_dimensions`, `additivity`, `effective_from`, `effective_to`, `content_hash`, `published_at`, `author_id`, `approver_id` | **Immutable once published** |
| `metric_target` | `id`, `tenant_id`, `metric_version_id`, `period`, `dimension_scope`, `value`, `source`, `owner_id` | Entity, not a scalar field |
| `metric_threshold` | `id`, `tenant_id`, `metric_version_id`, `kind`, `comparator`, `value`, `severity` | |
| `metric_assertion` | `id`, `tenant_id`, `metric_version_id`, `expression`, `expected`, `tolerance`, `last_run_at`, `last_status` | Publish gate (M2) |
| `metric_observation` | `tenant_id`, `metric_version_id`, `period_start`, `period_end`, `dimension_key`, `value`, `computed_at`, `config_version`, `data_snapshot_id`, `origin` | Append-only (ADR-008 §8) |
| `lineage_projection` | `tenant_id`, `config_version`, `root_type`, `root_id`, `graph` (jsonb), `built_at` | **Explicitly a cache** (ADR-012) |

### Experience
| Entity | Key fields |
| --- | --- |
| `dashboard` | `id`, `tenant_id`, `code`, `name`, `version`, `status` |
| `widget` | `id`, `tenant_id`, `dashboard_id`, `kind`, `metric_code`, `default_period`, `default_group_by`, `position` |

### Analytical plane (per-tenant schema, ADR-003)
| Object | Notes |
| --- | --- |
| `<tenant_schema>.raw_<source_object>` | Landed rows plus `_batch_id`, `_ingested_at`, `_snapshot_id` |
| `<tenant_schema>.sem_revenue` | Materialized semantic entity at declared grain, produced by the binding |
| Time partitioning on `sem_revenue.recognized_at`; BRIN index | |
| Raw batches also land in object storage under `t/<tenant_id>/raw/...` (ADR-004) | |

---

## 2. API Contracts

Versioned, tenant-scoped, authorized before any data access. Errors are RFC 9457
`application/problem+json`. Mutations accept `Idempotency-Key`. Governed objects use
`ETag`/`If-Match`. Long-running operations return a **job envelope**, never block.

### Common envelopes

```jsonc
// Async job
202 Accepted
{ "job_id": "...", "status": "queued", "poll": "/v1/jobs/{job_id}" }

// Problem
{ "type": "https://.../problem/binding-validation-failed",
  "title": "Binding validation failed", "status": 422,
  "detail": "Grain assertion violated",
  "instance": "/v1/bindings/...", "correlation_id": "...",
  "errors": [ { "code": "GRAIN_NOT_UNIQUE", "field": "grain_assertion",
                "message": "transaction_id is not unique in 3 sampled partitions" } ] }
```

### Endpoints

**Tenant & identity**
```
POST   /v1/tenants                          # platform-admin only; provisions analytical schema
GET    /v1/me                               # principal, tenant, effective capabilities
```

**Data sources**
```
POST   /v1/data-sources                     # body carries secret VALUE once; stored as secret_ref
GET    /v1/data-sources
GET    /v1/data-sources/{id}
POST   /v1/data-sources/{id}/test           # 202 + job → ConnectionTestResult
POST   /v1/data-sources/{id}/discover       # 202 + job → namespaces/objects/fields
GET    /v1/data-sources/{id}/namespaces
GET    /v1/data-sources/{id}/objects?namespace=&cursor=
GET    /v1/source-objects/{id}/fields
POST   /v1/source-objects/{id}/profile      # 202 + job → field profiles
GET    /v1/source-objects/{id}/profile
```

`ConnectionTestResult`:
```jsonc
{ "overall": "fail",
  "checks": [
    {"name":"network",        "status":"pass",   "duration_ms": 41},
    {"name":"tls",            "status":"pass",   "duration_ms": 12},
    {"name":"authentication", "status":"fail",   "code":"AUTH_INVALID_CREDENTIALS",
     "remediation":"The username or password was rejected by the server."},
    {"name":"authorization",  "status":"skipped"},
    {"name":"metadata_access","status":"skipped"},
    {"name":"latency",        "status":"skipped"} ] }
```
No secret, host credential, or raw driver error string ever appears here.

**Semantic model**
```
GET    /v1/semantic/entities
GET    /v1/semantic/entities/{code}          # the contract: required fields, grain, anchors
POST   /v1/bindings                          # draft entity binding
PATCH  /v1/bindings/{id}                     # If-Match
POST   /v1/bindings/{id}/validate            # 202 + job → BindingValidationReport
POST   /v1/bindings/{id}/approve             # human approval; author ≠ approver where required
GET    /v1/dimensions
POST   /v1/dimensions                        # tenant-configured slicing axis
```

**Metrics**
```
GET    /v1/metrics
POST   /v1/metrics                           # creates metric + draft version (AST body)
POST   /v1/metrics/{code}/versions/{v}/validate
POST   /v1/metrics/{code}/versions/{v}/assertions/run
GET    /v1/metrics/{code}/lineage            # derived; authorization-redacted
```

**Governed query — the primary contract**
```
POST   /v1/query                             # batch, multi-metric
POST   /v1/metrics/{code}/query              # single-metric sugar
```
```jsonc
// request
{ "metrics": ["revenue_ytd"],
  "period":  {"type":"fiscal_ytd","as_of":"2026-08-07"},
  "group_by":[{"dimension":"operating_model"}],
  "filters": [],
  "compare": {"to":"prior_year"},
  "options": {"limit":500,"include_lineage":true} }

// response (abridged) — the envelope is mandatory
{ "results": [ { "metric":"revenue_ytd",
    "rows": [ {"group":{"operating_model":"people"},"value":4210500.00,
               "comparison":{"prior_year":3980000.00,"delta_pct":5.79},
               "target":{"value":4500000.00,"variance_pct":-6.43}} ],
    "period_resolved": {"start":"2026-01-01","end":"2026-08-07","calendar":"gregorian_jan"},
    "filters_applied": [],
    "freshness": {"as_of":"2026-08-07T06:14:22Z","is_stale":false,
                  "source_watermarks":[{"source_object":"billing.transactions",
                                        "cursor":"2026-08-07T06:10:00Z"}]},
    "quality":   {"status":"ok","failed_checks":[],"coverage":1.0},
    "provenance":{"config_version":7,"metric_version_id":"...","plan_hash":"...",
                  "data_snapshot_id":"...","computed_at":"...","cache_hit":false,
                  "engine":"postgresql"},
    "authorization":{"row_scope_applied":true,"fields_masked":[]},
    "lineage":   {"available":true,"handle":"/v1/metrics/revenue_ytd/lineage?config_version=7"} } ] }
```

**Configuration bundle**
```
GET    /v1/config/bundles
POST   /v1/config/bundles/draft
POST   /v1/config/bundles/{id}/validate
POST   /v1/config/bundles/{id}/publish       # atomic; requires passing validation
POST   /v1/config/bundles/{version}/activate # rollback = activate an earlier bundle
GET    /v1/config/bundles/{version}/export   # secrets excluded by construction
```

**Jobs, dashboard, audit**
```
GET    /v1/jobs/{id}
GET    /v1/dashboards/{code}
GET    /v1/audit/events?cursor=
```

### Hard API rules for the slice
- No endpoint accepts SQL, a formula, or a field path from a client.
- Every identifier in a query request is validated against governed metadata for that
  tenant **and** that `config_version`.
- Unauthorized and non-existent return the **same** response (ADR-010 §4).
- All list endpoints are cursor-paginated.

---

## 3. Backend Modules

Per ADR-001; `import-linter` contracts enforce the arrows.

| Module | Responsibility | May depend on |
| --- | --- | --- |
| `eip.platform` | `TenantContext`, config-bundle resolution, error taxonomy, ports (`SecretStore`, `AnalyticalEngine`, `ObjectStore`, `JobQueue`), telemetry, outbox | — |
| `eip.identity` | Principals, memberships, roles, capabilities, row policies, `EffectiveAuthorizationScope` | `platform` |
| `eip.connectivity` | `Connector` protocol, `PostgresConnector`, capability declaration, connection diagnostics, discovery, sampling, profiling, egress guard | `platform` |
| `eip.dataops` | Pipeline state machine, steps, watermarks, extraction/landing/load, quality checks, freshness | `platform`, `connectivity.interfaces` |
| `eip.semantic` | Contracts, bindings, field/time bindings, transformations, relationships, dimensions, binding validation | `platform` |
| `eip.metrics` | Metric identity/versions, AST model + validator, targets, thresholds, assertions, **AST → QueryPlan compiler** | `platform`, `semantic.interfaces` |
| `eip.query` | Governed Query Service: authorize → resolve → compile → join/fan-out check → cache → execute → envelope | `platform`, `identity.interfaces`, `metrics.interfaces`, `semantic.interfaces` |
| `eip.lineage` | Derived lineage traversal, impact analysis, projection cache | `platform`, `semantic.interfaces`, `metrics.interfaces` |
| `eip.governance` | Bundles, draft workspaces, publish/rollback, approvals, audit chain | `platform` |
| `eip.experience` | Dashboards, widgets, KPI card assembly, drill-down orchestration | `platform`, `query.interfaces` |
| `eip.api` | FastAPI routers, request/response schemas, OpenAPI generation | all `*.interfaces` only |
| `eip.adapters.*` | `postgres_engine`, `secret_manager`, `object_store`, `redis_cache` — the **only** places vendor SDKs appear | `platform` |

**Purity requirement:** `eip.metrics` compiler and `eip.semantic` validators perform **no
I/O**. They take data in and return a plan or a report. This is what makes them
exhaustively testable, and it is where the product's correctness lives.

---

## 4. Frontend Screens / Components

| # | Screen | Key components | Notes |
| --- | --- | --- | --- |
| F1 | **Tenant setup** | `TenantForm`, `FiscalCalendarForm` | Fiscal calendar is required before any period resolves |
| F2 | **Data sources list** | `DataSourceTable`, `HealthBadge` | |
| F3 | **Add data source** | `ConnectorTypePicker`, `ConnectorConfigForm` (rendered from `config_schema()` — generic, not per-connector code), `SecretInput` (write-only, never reveals) | Proves connector neutrality reaches the UI |
| F4 | **Connection test** | `DiagnosticsPanel` (per-check pass/fail/skipped + remediation), `JobProgress` | Must make "wrong password" visibly different from "unreachable" |
| F5 | **Schema explorer** | `NamespaceTree`, `ObjectList`, `FieldTable`, `DiscoveryJobStatus` | |
| F6 | **Field profile** | `ProfileSummary` (null rate, distinct, min/max), `SampleRowsTable` (masked, permission-gated) | |
| F7 | **Binding editor** | `SemanticContractPanel` (what the contract requires), `GrainAssertionPicker`, `RowFilterBuilder` (structured predicate, no SQL box), `FieldBindingRow`, `TimeAnchorBinder`, `CurrencyPolicyForm`, `ValidationReportPanel` | **The most important screen in the slice.** It must make an unbound required field impossible to miss |
| F8 | **Dimension config** | `DimensionForm`, `DimensionValueList` | Proves the workbook selector is metadata; no `total` value |
| F9 | **Metric editor** | `MetricAstBuilder` (aggregation · field · filter · period · anchor), `AllowedDimensionsPicker`, `TargetEditor`, `AssertionEditor`, `MetricValidationPanel` | No formula text box, by design |
| F10 | **Publish / bundle review** | `BundleDiff`, `ImpactAnalysisPanel`, `ValidationSummary`, `ApprovalControl` | Author ≠ approver where required |
| F11 | **Executive command center** | `KpiCard` (value · target · comparison · **freshness** · **quality** · owner · drill · lineage), `BusinessHealthRow` | A card that cannot render freshness and quality does not render |
| F12 | **Drill-down** | `MetricBreakdownTable`, `DimensionSelector`, `PeriodSelector` | Group by the tenant-configured dimension |
| F13 | **Lineage view** | `LineageGraph` (widget → metric → AST → semantic field → binding → transformation → source field → object → source), `NodeDetailPanel`, `RedactedNode` | Redacted nodes preserve shape, not content |

All screens consume the **generated API client** (`packages/api-client`). No screen calls a
database. No screen constructs a query in anything but the closed request contract.

---

## 5. Background Jobs

All jobs carry the typed envelope `{job_id, tenant_id, config_version, actor, trace_id,
idempotency_key, attempt}` and are refused without a resolvable tenant (ADR-009 §4).

| Job | Queue | Trigger | Steps | Idempotency |
| --- | --- | --- | --- | --- |
| `connection_test` | `interactive` | user action | network → tls → auth → authz → metadata → latency | Naturally idempotent; result upserted per `(source, run)` |
| `discover_source` | `interactive` | user action | list namespaces → list objects → describe fields → **diff vs. previous** → persist | Upsert on `(data_source, namespace, object, field)`; drift recorded, not overwritten |
| `profile_object` | `interactive` | user action | sample → compute stats → persist baseline | Upsert on `(source_field, run)` |
| `validate_binding` | `interactive` | draft save / explicit | grain uniqueness → required fields bound → type/unit compatibility → filter references → profile-policy checks | Pure over a pinned snapshot |
| `ingest_object` | `ingestion` | schedule / manual | plan (capabilities) → extract batches → land raw (object storage) → validate → load → **commit watermark with batch** → quality checks → snapshot | Batch keyed on natural key; watermark atomic with batch |
| `materialize_entity` | `compute` | after ingest / after binding publish | apply row filter → apply transformations → assert grain → write `sem_revenue` | Rebuild is idempotent per `snapshot_id` |
| `run_metric_assertions` | `compute` | on publish + daily | compile → execute → compare to expected ± tolerance | Pure per `(metric_version, config_version, snapshot)` |
| `record_observations` | `compute` | after materialize | compute metric per period × dimension key → append | Append-only; unique on `(metric_version, period, dimension_key, snapshot)` |
| `rebuild_lineage_projection` | `maintenance` | on bundle publish | traverse graph → persist projection | Keyed on `(config_version, root)` |
| `outbox_relay` | `maintenance` | continuous | publish outbox rows | At-least-once with idempotent handlers |

**Per-tenant concurrency caps apply on every queue** so one tenant's backfill cannot delay
another's connection test.

---

## 6. Security Controls

| Control | Where enforced | Verified by |
| --- | --- | --- |
| Authentication via OIDC; no password storage | `eip.api` middleware | Auth integration tests |
| Tenant derived from the authenticated principal only — never a header, subdomain, or body | `eip.platform` tenant resolution | Test: forged `X-Tenant-Id` is ignored |
| Authorization before any data access | `eip.query` requires `EffectiveAuthorizationScope` as a typed argument | Architecture test: no compiler constructor omits it |
| Row policy injected into the plan, never post-filtered | `eip.query` compile step | Two-user aggregate test |
| Fail closed when a row policy's dimension is unreachable | `eip.query` | Explicit denial test |
| Field classification inherited by metrics through the AST | `eip.metrics` + `eip.identity` | Classification-inheritance test |
| Uniform not-found-or-not-permitted responses | `eip.api` error mapper | Error-taxonomy security test |
| Secrets stored as references only; typed `SecretValue` that cannot be logged or serialized | `eip.platform` + `adapters.secret_manager` | Log-scanning test; DB schema review |
| Egress deny-list (RFC1918, loopback, link-local, metadata endpoints); resolve-then-connect | `eip.connectivity` egress guard | SSRF test suite targeting `169.254.169.254`, `127.0.0.1`, `10.0.0.0/8` |
| Read-only analytical role; governed path cannot write | `adapters.postgres_engine` | Startup assertion + write-attempt test |
| Application role is not table owner and not superuser (RLS cannot be bypassed) | Startup assertion | Boot-time check test |
| Cache key includes `auth_scope_hash`, `config_version`, `metric_version`, `data_snapshot_id` | `eip.query` cache key type | Two-user differential cache test |
| Audit append-only with per-tenant hash chain | `eip.governance` | Chain verification test; no `UPDATE`/`DELETE` grant |
| Human approval for binding and metric publish; author ≠ approver where required | `eip.governance` | Separation-of-duties test |
| Sample rows masked for classified fields; permission-gated | `eip.connectivity` + `eip.api` | Masking test |
| Telemetry attribute allowlist; no metric values or field values emitted | collector config + `eip.platform` | Emitted-attribute test |

---

## 7. Tenant Isolation

Isolation is not a control in this slice; it is a **test subject**. The slice provisions
**two tenants and three users** specifically so isolation can be attacked.

| Layer | Mechanism | Adversarial test |
| --- | --- | --- |
| Metadata | `tenant_id` + **forced RLS**, `SET LOCAL app.tenant_id` per transaction, non-owner role | Tenant B's token requests Tenant A's `data_source_id` → 404, and a direct repository call without context raises |
| Connection pooling | Reset hook; assertion on checkout | Checkout → set → return → checkout → assert `app.tenant_id` unset |
| Analytical plane | Schema-per-tenant; role granted `USAGE` on the current tenant's schema only | Attempt a cross-schema query → permission denied at the database |
| Cache | `auth_scope_hash` + tenant + config/metric version + snapshot in the key | User X (region=East) and User Y (region=West) query the same metric; assert different keys and different values, and that neither can be served the other's entry |
| Object storage | `t/<tenant_id>/` prefix; prefix-scoped IAM | Attempt to read another prefix → denied |
| Jobs | Envelope requires `tenant_id`; worker refuses otherwise | Enqueue without tenant → rejected, not defaulted |
| Secrets | Tenant-namespaced paths, prefix-scoped policies | Attempt cross-tenant `SecretRef` → denied |
| Telemetry | `tenant_id` on every span/log/metric | Assert presence on a representative trace |
| Lineage | Authorization-redacted nodes | Restricted user's lineage view shows shape, not source names |

---

## 8. Error Handling

Principles: **fail closed**, **be specific to the operator, opaque to the attacker**, and
**never let a partial success look like a success**.

| Situation | Behaviour |
| --- | --- |
| Connection test fails at authentication | Overall `fail`; the failing check named with a remediation hint; later checks `skipped`, not `fail`; no driver string, host, or credential echoed |
| Discovery partially succeeds (one schema unreadable) | Job completes `partial`; readable objects persisted; unreadable namespaces listed with reasons; the UI shows partial state explicitly rather than an implied complete catalog |
| Binding validation fails | `422` with a **per-check** error list (`GRAIN_NOT_UNIQUE`, `REQUIRED_FIELD_UNBOUND`, `TYPE_INCOMPATIBLE`, `TIME_ANCHOR_UNBOUND`, `CURRENCY_UNRESOLVED`); publish blocked |
| Metric AST references a missing or unauthorized semantic field | `422` on validate; on query, the uniform not-found-or-not-permitted response |
| Join path ambiguous | `422 AMBIGUOUS_JOIN_PATH` naming the candidate paths; **never** an arbitrary choice |
| Fan-out detected | Pre-aggregate if provably safe; otherwise `422 FANOUT_UNSAFE` with the offending relationship named |
| Row policy dimension unreachable from the metric | `403` — fail closed |
| Analytical query exceeds limits | `413`/`429` with the limit stated; partial results are never returned as if complete |
| Data stale beyond policy | Query **succeeds** with `freshness.is_stale = true`; the KPI card renders a stale badge; the assistant would refuse (out of slice scope) |
| Ingestion step fails mid-run | Step marked failed with attempt count; watermark **not** advanced; run resumable from the last committed batch; a partial load is never visible to queries (snapshot not published) |
| Secret manager unavailable | Ingestion fails with a distinct `SECRET_UNAVAILABLE` state, not a generic connection error — the operator must be able to tell these apart |
| Cache backend unavailable | Degrade to direct execution; emit a signal; never serve a stale or unkeyed value |
| Bundle publish validation fails | Atomic: nothing is published; the draft remains editable; the validation report is persisted |
| Concurrent edit conflict | `412 Precondition Failed` on `If-Match` with the current version |

Every error response carries a `correlation_id` matching the `trace_id`; detail stays
server-side.

---

## 9. Logging

Structured JSON, correlated by `trace_id`, per ADR-014.

**On every record:** `tenant_id`, `principal_id` (pseudonymous), `trace_id`, `span_id`,
`config_version`, `component`, `operation`, `environment`, `release`.

**Key events**
| Event | Fields |
| --- | --- |
| `data_source.created` | `data_source_id`, `connector_type`, `connectivity_mode`, `secret_ref` (reference only) |
| `connection_test.completed` | `data_source_id`, `overall`, per-check status and duration |
| `discovery.completed` | `data_source_id`, counts of namespaces/objects/fields, drift count |
| `profile.completed` | `source_object_id`, field count, duration |
| `binding.validated` | `entity_binding_id`, status, failed check codes |
| `binding.published` | `entity_binding_id`, `version`, `author_id`, `approver_id`, `change_reason` |
| `metric.published` | `metric_code`, `version`, assertion results summary |
| `bundle.published` | `config_version`, object counts, validation status, approver |
| `query.executed` | `metric_codes`, `metric_version`, `plan_hash`, `cache_hit`, `rows_scanned`, `rows_returned`, `duration_by_stage`, `row_scope_applied` |
| `pipeline.step` | `run_id`, `step`, `status`, `attempt`, `rows`, `duration`, `watermark` |
| `authz.denied` | `capability`, `object_type`, `object_id`, `reason_code` |

**Never logged:** secrets, metric *values*, dimension *values*, source field *values*,
sample rows, raw prompts/completions, connection strings, or driver error strings
containing host or credential fragments. Enforced by an attribute allowlist at the
collector and asserted by a CI test.

---

## 10. Tests

### Unit
- Metric AST validator: well-formedness, dimension reachability, additivity conformance.
- Metric compiler: one golden `QueryPlan` per node kind; **property-based** — e.g. for any
  partition of a dimension, the sum of grouped results equals the ungrouped result for an
  additive metric.
- Binding validator: each check independently, positive and negative.
- Transformation AST evaluation, including decimal precision (no float coercion).
- Fiscal-period resolution across calendar types and year boundaries.
- Cache-key construction: asserts `auth_scope_hash` and versions are present; the key type
  rejects construction without them.
- Egress guard: allow/deny across public, RFC1918, loopback, link-local, and DNS-rebinding
  cases.
- Audit hash chain: append, verify, detect tampering.

### Integration (real PostgreSQL, real Redis, fake source)
- Connector conformance suite (ADR-004) run against `PostgresConnector`.
- Discovery → profile → bind → validate → publish → materialize → query, end to end.
- Ingestion resumability: kill mid-run, restart, assert no duplicates and no gaps
  (row-count and checksum reconciliation).
- Watermark atomicity under induced failure.
- RLS enforcement: direct repository access without `TenantContext` raises; forged tenant
  id ignored.
- Pool hygiene: `app.tenant_id` never survives a connection checkout.
- Outbox: transaction rollback leaves no job; commit always yields exactly one.

### Correctness (the ones that matter most)
- **Golden dataset with a hand-computed control.** A fixture where `revenue_ytd` and each
  dimension slice have independently calculated expected values, checked into the
  repository. The number the platform produces must equal the control exactly.
- **The Company A / Company B test.** Two fixture schemas — one invoice-header-grained with
  cents and gross amounts, one line-grained with refunds, intercompany rows, and test rows
  — bound to the same `Revenue` contract with **no code differences**, producing the same
  correct total. This is the single test that validates the product thesis.
- Grain violation is detected: a fixture whose declared natural key is not unique must fail
  validation.
- Row filter correctness: refunds and intercompany rows are excluded, provably.
- Restatement: re-ingest with changed history; assert both observations retained, a
  restatement event recorded, and no spurious anomaly signal.

### Security
- Two-user differential cache test (the S1 regression test).
- Cross-tenant access attempts across metadata, analytical schema, cache, object storage,
  jobs, and secrets.
- Classification inheritance: a user without clearance cannot query a metric derived from a
  restricted field.
- Row policy: aggregate results differ per user scope; unreachable dimension → 403.
- SSRF suite against the REST/JDBC configuration surface.
- Secret leakage: scan all test log output for credential patterns.
- Error taxonomy: unauthorized and non-existent are indistinguishable.

### Architecture
- `import-linter` contracts pass.
- `apps/web` has no database driver dependency.
- Vendor SDKs appear only in `eip.adapters.*`.
- `/contracts` and `packages/api-client` regenerate with an empty diff.
- Every tenant-scoped table has `tenant_id` and FORCE RLS (schema-vs-`pg_policies` test).
- The analytical engine port has exactly one caller.

### End-to-end (Playwright)
The full walk, F1 → F13, as a scripted acceptance run against a seeded environment.

### Performance (baseline, not tuning)
- Single KPI card, cache hit and cache miss.
- Drill-down across ~20 dimension values.
- Ingestion of ~1M rows, measuring rows/sec and resumability overhead.
Recorded as a baseline so ADR-008's trigger conditions have a starting point.

---

## 11. Acceptance Criteria

The slice is complete only when **every** item passes. Partial completion is not
completion.

### Functional
1. A platform admin creates a tenant; a per-tenant analytical schema is provisioned; a
   fiscal calendar is configured.
2. A steward adds a PostgreSQL data source; the credential is stored **only** as a
   `secret_ref` — verified by inspecting the metadata database, which contains no
   credential material.
3. Connection test returns per-check diagnostics; a deliberately wrong password produces a
   message distinguishable from an unreachable host, with no secret echoed.
4. Discovery enumerates schemas, tables, and fields with canonical types; a second run
   detects and reports drift rather than silently overwriting.
5. Profiling produces null rate, distinct count, and min/max per field.
6. The steward creates an `EntityBinding` from the source table to `Revenue`, declaring
   grain, row filter, `Revenue.Amount`, and the `recognized_at` time anchor.
7. **Binding validation passes** — and a deliberately broken variant (non-unique grain,
   unbound required field, wrong type) **fails with specific, actionable check codes**.
8. A tenant-configured dimension with tenant-defined values exists. **No dimension value
   named `total`.** No KPI name or dimension value appears anywhere in source code.
9. The steward defines `revenue_ytd` as a metric AST with a target and at least one
   acceptance assertion.
10. Publishing creates an immutable `ConfigurationBundle`; publishing with a failing
    assertion is **blocked**.
11. `POST /v1/query` returns the correct value, matching the hand-computed control exactly.
12. The response carries the **full envelope** — freshness, quality, provenance
    (`config_version`, `metric_version`, `plan_hash`, `data_snapshot_id`), and
    authorization flags.
13. The KPI card renders value, target, comparison, freshness, quality, owner, drill
    affordance, and lineage affordance. A card missing freshness or quality does not
    render.
14. Drill-down groups by the tenant-configured dimension and the grouped values sum to the
    ungrouped total.
15. The lineage view renders the complete chain from widget to source field, **derived**
    from the binding graph — verified by deleting the `lineage_projection` cache and
    confirming the view rebuilds identically.
16. Rolling back to the previous bundle instantly restores the prior number, and the
    difference between the two is explainable from provenance plus the governance record
    (author, approver, reason).

### The falsification criteria (why this slice exists)
17. **The Company A / Company B fixtures both produce the correct `Revenue.Amount` with
    zero code differences.**
18. **Two users with different row scopes receive different, correct results — and the
    cache never serves one to the other.**
19. **Grain violation, fan-out, and ambiguous join paths are refused with specific errors,
    never silently resolved.**
20. **Restatement is recorded and visible, and does not produce a spurious signal.**

### Non-functional
21. All security tests in §6/§7 pass, including the SSRF and cross-tenant suites.
22. No secret, metric value, dimension value, or source value appears in any log or
    telemetry attribute.
23. `import-linter` contracts, `mypy --strict`, lint, and the full test suite pass in CI.
24. Every schema change ships as an Alembic migration; the analytical schema is created by
    the provisioning subsystem, never by hand.
25. Performance baselines are recorded for the ADR-008 trigger conditions.
26. Every ADR-relevant deviation discovered during implementation is captured as a new ADR
    or an amendment — not absorbed silently.

---

## What this slice deliberately does not prove

Stated so nobody mistakes a green build for a validated platform: it does not prove that
the connector framework generalizes beyond one relational source, that the insight engine
scales, that the assistant's plan validation is airtight, that onboarding is fast enough to
be commercially viable, or that the analytical store holds at volume. Those are the
subjects of later phases. This slice proves the **spine** — and if the spine is wrong,
nothing built on it can be right.
