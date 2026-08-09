# 25 — CEO demonstration vertical-slice entry report

Date: 2026-08-09
Recommendation: **READY — product defaults approved 2026-08-09**

## 1. Objective

Deliver a production-quality, explicitly seeded demonstration slice by the Tuesday
engineering freeze:

`Connect PostgreSQL → Test Connection → Executive Command Center → configured drill-down → trust/provenance traversal`

The experience must never imply that seeded observations are live customer analytics. The
visible label is **Demo dataset / seeded demonstration data** in the dashboard, response
envelope, trust view, walkthrough, screenshots, and fallback material.

This is not authorization to claim the full platform, full production vertical slice, live
discovery/extraction, or production deployment is complete.

## 2. Accepted authority and architecture

This review reconciles the explicit CEO demonstration authorization with the accepted
roadmap, ADR-003, ADR-005 through ADR-008, ADR-010, ADR-012 through ADR-016, the first
vertical-slice specification, and the completed Phase 2 repository.

The binding decisions are:

- PostgreSQL RLS and server-derived tenant context protect every tenant-owned metadata and
  observation row. The application role remains a non-owner without RLS bypass.
- The semantic layer remains between source metadata and metric definitions. Even seeded
  observations reference a published semantic binding; dashboard code never points directly
  to source fields.
- Metric definitions use a closed typed JSON AST. No arbitrary SQL, formula string, Python,
  or tenant-specific code is accepted.
- Published configuration and metric versions are immutable. Corrections create a new
  version; the demo seed is deterministic and idempotent.
- Governed reads authorize before access and return a mandatory envelope carrying period,
  value, comparison, target, freshness, quality, provenance, authorization, and seeded-data
  origin.
- Monetary values use PostgreSQL `numeric` and Python `Decimal`; JSON returns decimal strings
  or another contractually lossless representation, never binary floating-point math.
- Lineage is produced by traversing dashboard widget → metric version/AST → semantic field →
  field binding → source field/object → DataSource. It is not stored as an asserted JSON
  picture. A future projection may cache traversal output, but no cache is added now.
- Telemetry includes identifiers, versions, outcomes, durations, and counts, but never metric
  values, dimension values, source values, credentials, or raw payloads. Audit remains
  durable and separate.

## 3. What the repository already provides

- authenticated tenant context, role/capability authorization, durable denial auditing;
- forced-RLS metadata conventions, same-tenant foreign keys, constrained runtime grants,
  migration reversal/model-drift tests, and bounded-context enforcement;
- tenant-owned PostgreSQL DataSource persistence with SecretStore references only;
- real PostgreSQL connection diagnostics executed through the outbox, Redis, and worker;
- source version/status fencing before credential or network effects;
- Add PostgreSQL Source → Test Connection → Disable Source browser flow;
- safe logs/errors, secret scanning, real Keycloak tests, and a seven-job CI release gate.

There is no semantic, metric, governed-query, lineage, dashboard, or executive package yet.
No migration after `0007_source_retention` exists.

## 4. Narrow demonstration model

All new tenant-owned tables require non-null `tenant_id`, forced RLS, the standard policy,
same-tenant composite foreign keys where applicable, explicit checks, least-privilege grants,
and live schema inspection.

### Configuration and semantic metadata

| Entity | Minimum persisted fields |
| --- | --- |
| `configuration_bundle` | id, tenant_id, version, status, content_hash, author_id, approver_id, published_at, change_reason; published rows immutable |
| `demo_dataset` | id, tenant_id, code, label, origin=`seeded_demo`, description, as_of_at, reset_version; visible honesty boundary |
| `source_object` / `source_field` | tenant/source identifiers, stable codes, safe table/field names and types; no sampled values or credentials |
| `semantic_entity` / `semantic_field` | Revenue contract, declared grain/time anchor, Amount money field, SegmentRef category field, classification and additivity |
| `dimension` / `dimension_value` | one tenant-configured dimension and ordered values; no `total` value |
| `entity_binding` / `field_binding` | published source-object-to-Revenue binding, declared grain and closed transformations; seeded-demo origin |

### Governed metric and experience metadata

| Entity | Minimum persisted fields |
| --- | --- |
| `metric` / `metric_version` | stable code/name/owner, immutable published version, closed sum AST over Revenue.Amount, fiscal period, allowed dimension, direction, format/unit, content hash |
| `metric_target` | metric version, period, optional dimension scope, Decimal value, owner/source |
| `metric_observation` | tenant, metric version, exact period, dimension key or aggregate, Decimal value, prior value, computed_at, snapshot id, config version, origin=`seeded_demo`; append-only |
| `quality_result` / `freshness_result` | observation/snapshot identifiers, closed status/code, evaluated_at, safe detail; no business values in telemetry |
| `dashboard` / `widget` | immutable published executive dashboard metadata and Revenue YTD KPI widget references; no values in UI source code |
| `attention_rule` | closed comparator/threshold or scoped target reference and linked dimension; the basis for Requires Attention |

The checked-in seed must create the required aggregate observations:

- headline `4210500`;
- prior `3980000`;
- target `4500000`;
- People `1850000`, Process `1410500`, Technology `950000`;
- a database constraint/test proving the three configured slices reconcile exactly to the
  headline.

Numbers live only in deterministic seed data or persisted database rows—not React
components, API service code, metric compiler code, or migration defaults.

## 5. Closed metric expression and validation

The deadline slice implements the smallest accepted AST subset:

```json
{
  "kind": "aggregation",
  "function": "sum",
  "field": "Revenue.Amount"
}
```

The parser rejects unknown keys/nodes, arbitrary expressions, SQL fragments, missing or
unauthorized semantic fields, non-additive aggregation, unreachable dimensions, unpublished
configuration, and non-Decimal literals. The validator resolves the configured time anchor,
dimension reachability, field classification, and published bundle version before a query.

The query service reads immutable materialized observations for this demo. It does not query
the connected PostgreSQL source, perform discovery, profile data, extract rows, or pretend
the observation was calculated live.

## 6. Read-only tenant API

Proposed authenticated routes, aligned with the first-slice contract:

- `GET /v1/dashboards/executive` — dashboard/widget metadata and assembled KPI envelope;
- `POST /v1/metrics/revenue_ytd/query` — closed request containing period and optional
  configured `group_by`; no SQL/formula input;
- `GET /v1/metrics/revenue_ytd/lineage?config_version={v}` — authorization-redacted derived
  traversal;
- optional `GET /v1/data-sources/{id}/connection-tests/latest` link reuses Phase 2 source
  health and does not become metric freshness.

The mandatory metric envelope contains:

```text
metric identity/version; exact period/as-of; Decimal value/unit/format;
prior value plus computed absolute/percent comparison; target plus variance;
freshness status/as-of; quality status/checks; accountable owner;
provenance(config version, snapshot, calculation time, demo-dataset identity/origin);
authorization(row scope applied, redactions); allowed drill-down; lineage handle
```

Tenant B receives the same response for Tenant A identifiers as for random identifiers.
Authorization occurs before reading definitions, observations, source metadata, or lineage.

## 7. Executive web boundary

`/app/executive` is an authenticated responsive route. It renders only the typed API
envelope and contains no KPI names, values, segment labels, thresholds, source table/field
names, or lineage edges as source-code constants.

Minimum experience:

- polished Revenue YTD hero KPI with headline, prior and target comparisons;
- visible Demo dataset / seeded demonstration data label;
- period/as-of, freshness, quality, accountable owner, and calculation time;
- Requires Attention item linking to the configured underperforming segment;
- one drill-down by the configured dimension; displayed slices sum exactly to the headline;
- one-click trust view showing widget → metric version → semantic binding → source object and
  fields, configuration version, snapshot and calculation time;
- optional link to the Phase 2 source-health result, clearly separate from metric freshness;
- deterministic desktop and responsive browser walkthrough and credential-free screenshots.

## 8. Authorization and audit

Use new closed read capabilities such as `executive.read`, `metric.query`, and
`lineage.read`, granted only through accepted tenant roles. Platform administrators receive
no standing tenant-data access. Field classification and row-scope checks fail closed.

Audit allowlisted identifiers and outcomes for `dashboard.viewed`, `metric.queried`,
`metric.drilldown_queried`, and `lineage.viewed`, including metric/config versions and
row-scope/redaction flags. Never audit metric values, dimension values, target values,
source values, endpoint/user details, or lineage field names.

## 9. Four sequential implementation stages

### Stage 1 — Frozen shared metadata and contracts

Add migrations/models, RLS/policies/grants, immutable configuration/semantic/metric/
experience records, closed AST types/validator, deterministic seed/reset, capabilities,
audit actions, API/shared contracts, and bounded-context rules. Freeze migration chain,
central model registry, app composition, and generated contract before other lanes.

**Acceptance:** migration upgrade/downgrade/model parity; published-row mutation refused;
seed/reset is repeatable and labels origin; exact Decimal reconciliation; two-tenant RLS;
no values hard-coded outside seed data; Ruff/mypy/contracts and real PostgreSQL tests pass.

### Stage 2 — Governed query and derived lineage backend

Implement authorize → resolve published bundle → validate AST/dimension → read seeded
observations → assemble mandatory envelope, plus lineage graph traversal and audit-safe
observability. No cache.

**Acceptance:** correct headline/comparisons/target/drill-down with Decimal math; deletion of
any optional lineage projection does not change traversal; cross-tenant/forged identifiers
fail closed; classification and unreachable dimension deny; no values leak to logs/audit.

### Stage 3 — Executive Command Center browser experience

Implement `/app/executive`, hero KPI, trust badges, Requires Attention link, configured
drill-down, trust view, seeded-data disclosure, responsive layout, and optional source-health
link using only typed API data.

**Acceptance:** deterministic real-browser happy path and responsive pass; all values and
labels originate from API metadata; drill-down reconciles; trust opens in one action;
tenant/session manipulation and browser artifact leakage tests pass.

### Stage 4 — Adversarial release and demonstration pack

Run complete security/correctness/browser suites, finalize immutable seed tag/reset command,
perform two full rehearsals, save safe screenshots/video fallback, and write the concise CEO
script/evidence pack with remaining production gaps.

**Acceptance:** real PostgreSQL tests have zero skips; exact two-tenant isolation,
reconciliation, secret/log/artifact scans, Ruff, strict mypy, TypeScript, migrations,
contracts, browser tests and all seven CI jobs pass; repository clean. Report explicitly
says production-quality seeded demo vertical slice, not complete platform/live analytics.

## 10. Handshakes

For the baseline and every stage:

1. Write the bounded assignment under `automation/stages/` and update the durable ledger.
2. Claude implements and returns an uncommitted patch with evidence.
3. Codex reviews the complete change independently, including SQL/RLS and rendered UI.
4. Permit at most one focused repair.
5. Run real PostgreSQL and browser/infrastructure tests with zero skips.
6. Update `automation/results/` and `automation/status.md`.
7. Create one focused commit, push, and wait for all seven GitHub Actions jobs.
8. Confirm a clean repository before the next stage. Shared migrations/models/app
   composition/contracts must be frozen before any authorized parallel lane begins.

## 11. Explicit exclusions

No live discovery, profiling, extraction, ingestion, object-storage landing, customer data,
semantic authoring UI, arbitrary SQL/formulas, caching, alerts, general dashboards/builder,
insights, AI, additional connectors, customer-network agents, production SecretStore,
production deployment, purchases, or customer-system access.

## 12. Approved product defaults

The product owner approved the three visible demo claims on 2026-08-09:

1. **Period and as-of date:** calendar YTD, America/Chicago, as of
   2026-08-11 17:00 America/Chicago.
2. **Accountable owner label:** `Chief Revenue Officer`, a role label rather than a named
   person.
3. **Underperformance rule:** persist segment targets People `1900000`, Process `1450000`,
   and Technology `1150000` (total `4500000`) and select the largest negative target
   variance. Technology is the Requires Attention segment.

These values are immutable seeded demo configuration. No ADR conflict or unresolved product
decision remains. The recommendation is **READY** and Stage 1 may begin after this entry
baseline is committed, pushed, green in CI, and the repository is clean.
