# ADR-007: Governed Query Engine

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

The Governed Query Service is the single path to every number (principle 10, guardrail 6).
Dashboards, alerts, the insight engine, and the assistant all go through it; none of them
gets a private path. It is therefore simultaneously the correctness chokepoint, the
authorization chokepoint, the performance chokepoint, and the audit chokepoint.

`03`/`05`/`09` describe it as "the safe path to query metrics with dimensions and filters,
no arbitrary SQL from clients" but do not specify what it actually does, in what order, or
what its output contract is. Phase 0 must specify it, because several other decisions
(caching correctness, row-level security, lineage, multi-engine portability) hang off it.

## Decision

### 1. A fixed, ordered pipeline. No stage may be skipped or reordered.

```
QueryRequest (metric codes, period, grouping, filters, options)
  1. Authenticate & resolve TenantContext            (ADR-003)
  2. Resolve config_version                          (ADR-013) — pin the whole config
  3. Resolve MetricVersion(s) for each metric code
  4. AUTHORIZE — metric, dimension, semantic-field, and row-scope policies (ADR-010)
  5. Validate request against metric metadata (allowed dims, filter types, period)
  6. Compile → QueryPlan  (relational algebra over bindings; ADR-005/006)
     6a. resolve join paths; reject ambiguity
     6b. detect fan-out; pre-aggregate or reject
     6c. inject row-level predicates from step 4
     6d. inject tenant schema binding
  7. Cache lookup on the structured key
  8. Execute via AnalyticalEngine port (ADR-008), with limits
  9. Post-process: ratios, variances, formatting, non-additive guards
 10. Attach envelope: freshness, quality, provenance, lineage handle
 11. Emit telemetry + audit event
  → QueryResult
```

**Authorization precedes compilation, and row-level policy is injected into the plan** —
never applied as a post-filter over fetched rows. Post-filtering leaks through
aggregates: a user restricted to one region who receives a company-wide `SUM` has already
seen data they may not see, no matter what the UI renders.

### 2. The request contract is closed. Clients cannot express anything not in metadata.

```
POST /v1/query
{
  "metrics":  ["revenue_ytd", "win_rate"],          # by code, plural — see §6
  "period":   {"type": "fiscal_ytd", "as_of": "2026-08-07"},
  "group_by": [{"dimension": "region"}],
  "filters":  [{"dimension": "service_line", "op": "in", "values": ["advisory"]}],
  "compare":  {"to": "prior_year"},
  "options":  {"limit": 500, "include_lineage": true}
}
```

There is no field in which SQL, a formula, or a field path can be supplied. Every
identifier is validated against governed metadata for that tenant and that
`config_version`. Unknown identifiers produce a specific, non-leaking error (the error
must not reveal whether a metric exists but is unauthorized — see Risks).

### 3. The result envelope is mandatory and uniform

Every result — dashboard, chat, alert, insight — carries:

```
value(s), grouping, period_resolved (concrete start/end from the fiscal calendar),
filters_applied, comparison,
freshness   { as_of, source_watermarks[], is_stale, staleness_reason },
quality     { status, failed_checks[], coverage },
provenance  { config_version, metric_version, ingestion_batch/snapshot,
              computed_at, cache_hit, engine, plan_hash },
lineage     { available: true, handle: "..." },
authorization { row_scope_applied: bool, fields_masked[] }
```

`provenance` is the addition Phase 0 insists on. `05`/`06`/`08` describe *design-time*
lineage ("how is this defined") but nothing answers *"why does yesterday's screenshot show
a different number?"* — which is the question that destroys executive trust. `plan_hash` +
`config_version` + `metric_version` + `snapshot` answers it exactly.

### 4. Caching correctness

Cache key components, all mandatory:

```
tenant_id | config_version | metric_version | plan_hash
          | normalized_request | auth_scope_hash | data_snapshot_id
```

- `auth_scope_hash` covers the caller's effective row-level predicates and field
  restrictions. **Omitting it is an intra-tenant data leak** and is the most severe
  latent defect identified in Phase 0's review of the existing documentation, which
  specifies only tenant-prefixed cache keys.
- `data_snapshot_id` (the ingestion watermark set the plan read) makes invalidation
  correct by construction: new data produces a new key rather than requiring cache
  eviction to be reliable.
- Cached entries store the **full envelope**, not just the value, so a cache hit cannot
  lose freshness/quality/provenance.

### 5. Safety limits, always applied

Row limits, result-cell limits, a query timeout, a per-tenant concurrency cap, and a
per-tenant cost budget. Every executed statement carries a comment tag
(`/* tenant=... metric=... plan=... trace=... */`) so a DBA can attribute load. Statements
are issued through a **read-only role**; the governed path can never write.

### 6. Batch by default

`POST /v1/query` accepts multiple metrics and returns them together. A twelve-card
executive home page must be **one** request, not twelve — each of which would otherwise
repeat authorization, compilation, and round-trips. `09_DOMAIN_MODEL_API_CONTRACTS.md`'s
`POST /v1/metrics/{code}/query` is retained as sugar for the single-metric case but the
batch endpoint is the primary contract.

### 7. Execution mode: materialized, not federated (for now)

The platform **ingests into a tenant-isolated analytical store and queries that**
(ADR-008). It does not, initially, push queries down to the customer's operational
systems. The current documentation implies both models in different places and never
decides.

Rationale for materializing: deterministic reproducibility, stable history for the insight
engine, profiling and data-quality checks that would otherwise hammer production systems,
freshness that is *knowable* rather than assumed, and — decisively — no risk of the
platform degrading a customer's operational database. Federated/pushdown execution is
retained as a future mode behind the same `QueryPlan` (ADR-004 capabilities make it
expressible), for customers who forbid data movement.

## Alternatives Considered

- **Accept parameterized SQL from trusted internal callers.** Rejected. "Trusted internal
  caller" becomes "the assistant" becomes "a power user" within two quarters. One path or
  no governance.
- **Post-filter row-level security after execution.** Rejected — aggregates leak.
- **Push row-level security entirely into database RLS on the analytical store.**
  Attractive (defense in depth) but insufficient alone: our policies are semantic
  (`region IN user.regions` where `region` is reachable via a join), not physical. Adopt
  plan injection as primary; add analytical-store RLS later as a backstop where the engine
  supports it.
- **Generate SQL strings directly from metric definitions.** Rejected. A relational-algebra
  `QueryPlan` intermediate is what makes multi-engine support, plan hashing, lineage
  extraction, fan-out analysis, and optimization possible. String templating makes all of
  them guesswork.
- **Use an off-the-shelf query engine's governance (Cube's / Trino's).** Deferred; see
  ADR-005/008. Our `QueryPlan` is deliberately close enough to standard relational algebra
  that adopting a third-party compiler as a *backend* stays possible.
- **Cache keyed on tenant only (as currently documented).** Rejected — see §4.

## Rationale

Every property the product sells — reproducibility, explainability, authorization,
freshness, trust — is a property of this pipeline. Fixing its order and making its output
envelope mandatory is what prevents the "fast path" that inevitably appears when a
dashboard feels slow and someone adds a shortcut. The envelope in particular is a forcing
function: a surface that cannot render freshness and quality cannot render a number.

## Consequences

- Positive: exactly one code path produces numbers; the assistant and dashboards cannot
  disagree (principle 10).
- Positive: authorization, audit, caching, and telemetry are implemented once.
- Positive: provenance makes number changes explainable, which is the executive-trust
  failure mode nobody plans for.
- Positive: the plan intermediate keeps engine portability real.
- Negative: the service is a single point of failure and a bottleneck; it must be scaled
  and monitored as tier-1 infrastructure from Phase 1.
- Negative: compilation adds latency on cache miss; mitigated by caching compiled plans
  keyed on `(metric_version, request shape, config_version)`.
- Negative: the closed contract will frustrate analysts who want exploratory freedom. This
  is a genuine product tension and is escalated as an open product-owner question.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Cache key omits auth scope → intra-tenant leak | Two-user differential test in CI; key type has no untyped constructor | `auth_scope_hash` is a required field of the key type |
| Fan-out produces inflated aggregates | Property tests; additivity assertions (ADR-006) | Cardinality-aware compiler; reject rather than guess |
| Error messages disclose existence of unauthorized metrics | Security review of error taxonomy | Uniform `not found or not permitted` for authorization failures |
| Governed query becomes the latency bottleneck | p95/p99 per tenant per metric; cache hit rate | Plan cache, result cache, batch endpoint, selective materialization (ADR-008) |
| A "temporary" bypass path is added | Architecture test asserting the analytical engine port has exactly one caller | Import contracts (ADR-001); read-only DB role |
| Stale cache served after a config rollback | `config_version` in key | Rollback changes the key space; no eviction required |
| Unbounded queries exhaust the analytical store | Cost/row telemetry; per-tenant caps | Hard limits applied in the plan, not by convention |

## Future Considerations

- Federated/pushdown execution for no-data-movement customers.
- Approximate distinct counts as an explicit, labelled option (never silently).
- Automatic materialized-aggregate selection driven by observed plan hashes.
- Result streaming for large drill-downs.
- Query-plan explanation surfaced in the UI ("this number was computed by joining X to Y
  at grain Z") — a natural product feature once the plan is a first-class object.
