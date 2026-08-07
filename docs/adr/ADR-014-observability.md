# ADR-014: Observability

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`03_PLATFORM_ARCHITECTURE.md` specifies OpenTelemetry, structured logs, distributed
tracing, and metrics. That is the correct foundation and Phase 0 confirms it.

What it does not address is that this platform has **three distinct observability
subjects**, and conventional application observability covers only one of them:

1. **Service health** — latency, errors, saturation. Standard.
2. **Data health** — is the data fresh, complete, and correct? A perfectly healthy service
   serving three-day-old revenue is a product failure that no HTTP metric will surface.
3. **Governance and trust health** — are metrics passing their assertions? Are stewards
   publishing without validation? Is the assistant refusing answers because evidence is
   insufficient? These are the leading indicators of whether the product is actually
   trusted.

There is also a multi-tenant requirement that generic observability misses: **everything
must be attributable per tenant**, both to diagnose a specific customer's complaint and to
compute per-tenant unit economics (query cost, LLM cost, storage) — which the business
needs to price the product.

And a hazard: this system's telemetry naturally wants to carry business data. A trace
attribute containing a metric *value*, a log line containing a customer name from a
sampled row, or an error containing a connection string are all one careless line away.

## Decision

### 1. OpenTelemetry for all three signals, vendor-neutral by default

OTel SDK in the API, the worker, and (for RUM/web vitals) the frontend, exporting via OTLP
to a collector. The collector is the swap point for any backend. No vendor SDK in
application code — the same provider-neutrality discipline applied to connectors and the
LLM (guardrail 15), enforced by import contracts (ADR-001).

Logs are structured JSON, correlated to traces by `trace_id`, emitted to stdout and
shipped by the platform — never written to files by the application.

### 2. Mandatory context on every signal

Every span, log record, and metric carries:

```
tenant_id, principal_id (pseudonymous), trace_id, span_id,
config_version, deployment/version, environment,
component (bounded context), operation
```

Plus, on governed-query spans:

```
metric_code(s), metric_version, plan_hash, cache_hit,
rows_scanned, rows_returned, engine, duration_by_stage
```

`tenant_id` on every signal is non-negotiable (`07`, guardrail 8). It is injected by the
same context mechanism that scopes data access (ADR-003), so a signal without a tenant is
structurally impossible in tenant-scoped code paths.

### 3. Data-plane observability as a first-class subsystem

Emitted continuously, and — critically — **the same data powers the product's freshness
and quality badges**. There is one source of truth for "how fresh is this," used by both
the on-call engineer and the executive's KPI card. Two separate implementations would
inevitably disagree, and the one the customer sees would be the one nobody monitors.

Signals: per-source freshness lag versus policy, ingestion run success/duration/rows,
watermark progression, schema-drift detections, data-quality check pass rates, null-rate
and cardinality deviation from profile baselines, and reconciliation deltas.

**Freshness SLOs are per tenant per source**, defined as configuration, because "fresh"
means something different for a nightly ERP extract and a five-minute CRM sync.

### 4. Governance and AI observability

- metric assertion pass rate; publishes with validation overridden; approvals per author
  (separation-of-duties monitoring);
- assistant: plan-validation failure rate, repair-loop invocation rate, refusal rate by
  cause (unauthorized / stale / unresolvable), numeric-grounding rejection rate;
- lineage cache staleness; impact-analysis acknowledgement rate.

The assistant's **refusal rate by cause** is the single most valuable product metric in
this list: a rising "unresolvable" rate means the semantic model has gaps, and a rising
"stale" rate means ingestion is failing the executive rather than the engineer.

### 5. Audit is not telemetry

The audit trail (`07`) is a **separate, durable, append-only store in PostgreSQL**, not a
log stream. Reasons: it must be transactionally consistent with the change it records
(written in the same transaction, or via the outbox — ADR-009), it must be queryable by
tenant admins as a product feature, and it must be retained on a compliance schedule that
differs from telemetry retention.

Tamper-evidence is concrete, not aspirational: audit rows are append-only (no `UPDATE`/
`DELETE` grant to the application role) and each row carries a hash chained to its
predecessor within the tenant, with periodic checkpoint hashes exported to write-once
storage. This detects modification without requiring an expensive ledger database.

Audited events: authentication, authorization denials, permission changes, break-glass
elevation, data-source and secret-reference changes, all governed publishes/rollbacks with
author/approver/reason, mapping approvals, exports, assistant queries, and access to
`restricted`-classified fields.

### 6. Telemetry must not leak business data — enforced, not requested

- **Allowlist, not denylist**, for span attributes and log fields. Anything not explicitly
  permitted is dropped at the collector.
- **Metric values, dimension values, source data rows, field *values*, credentials, and
  raw prompts/completions are never emitted as telemetry.** Cardinalities, counts,
  durations, and identifiers are.
- Prompts/completions may be retained for debugging only in a **separate, tenant-scoped,
  access-controlled, short-retention** store — never in general logs.
- Error responses carry a correlation id; the detail stays server-side.
- A CI test asserts that a representative set of operations emits no disallowed attribute.

### 7. Cost attribution per tenant

Analytical query cost (rows scanned, duration), LLM tokens by model class, storage by
tenant, and worker seconds are all attributed per tenant. This is required for pricing,
for detecting a runaway tenant, and for the per-tenant budgets in ADR-007 and ADR-011.

### 8. SLOs defined in Phase 0, measured from Phase 1

| SLO | Target (initial, to be calibrated) |
| --- | --- |
| Governed query p95, cache hit | < 300 ms |
| Governed query p95, cache miss | < 3 s |
| Executive home page fully rendered | < 2 s p95 |
| Assistant end-to-end p95 | < 10 s |
| Ingestion freshness within policy | 99% of source-days |
| API availability | 99.9% |
| Metric assertion pass rate | 100%, alert on any failure |

Error budgets are tracked per tenant as well as globally, because a platform-wide 99.9%
that is entirely consumed by one customer is not a healthy platform for that customer.

## Alternatives Considered

- **A vendor SDK (Datadog/New Relic) directly in application code.** Rejected — vendor
  coupling in cross-cutting code, and expensive to unwind. The OTel collector gives the
  same backends without the coupling.
- **Logs-only observability.** Rejected — no distributed causality; the ingestion→query
  path spans processes.
- **Audit events as log records.** Rejected — no transactional consistency, no
  queryability as a product feature, wrong retention, and log pipelines drop records under
  load. An audit trail that drops records is not an audit trail.
- **A separate data-observability product (Monte Carlo, Soda, Great Expectations as a
  service).** Rejected as the core: our quality signals must feed the product's badges and
  the insight engine, so they must be first-class internal data. Great Expectations-style
  *check definitions* are a reasonable inspiration for the check library.
- **Sampling traces aggressively to cut cost.** Rejected for governed queries and
  ingestion; tail-based sampling is used so errors and slow requests are always retained,
  while healthy high-volume traffic is sampled.

## Rationale

The insight that shapes this ADR is that **data health is product health**. In an
executive intelligence platform, the failure that matters is rarely a 500 — it is a number
that is quietly stale, quietly wrong, or quietly restated. Building data-plane
observability as the same subsystem that renders the freshness and quality badges
guarantees the engineer and the executive are looking at the same truth.

Per-tenant attribution everywhere is the second load-bearing decision: without it, neither
support nor pricing is possible, and both are retrofits that touch every emission site.

## Consequences

- Positive: one telemetry stack, vendor-swappable.
- Positive: freshness and quality badges are backed by monitored infrastructure.
- Positive: per-tenant diagnosis and unit economics available from day one.
- Positive: the audit trail is a durable product feature, not a log query.
- Negative: mandatory context enrichment touches every emission path — must be automatic
  via context propagation, not manual.
- Negative: `tenant_id` on every metric raises cardinality and therefore backend cost;
  requires care in choosing which metrics are per-tenant dimensioned.
- Negative: the attribute allowlist will occasionally hinder debugging. Accepted — the
  alternative is business data in a third-party telemetry system.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Business data leaks into telemetry | CI test over emitted attributes; periodic backend scan | Collector-side allowlist; drop-by-default |
| High-cardinality `tenant_id` labels blow up cost | Backend cost monitoring | Per-tenant dimension only on a curated metric set; exemplars for the rest |
| Audit write fails and the change still commits | Reconciliation between governance changes and audit rows | Same-transaction write or outbox (ADR-009) |
| Audit hash chain broken by a bug | Periodic chain verification job | Verification alerts; checkpoints in write-once storage |
| Alert fatigue from data-quality noise | Alert volume per tenant | Severity tiers; suppress restatement-driven noise (ADR-012) |
| Freshness badge and monitoring diverge | Single implementation asserted by test | One source of truth by construction |
| Telemetry outage blinds operations during an incident | Collector health monitoring | Buffering; degraded-mode logging to stdout always available |

## Future Considerations

- Per-tenant status pages showing freshness and pipeline health — a differentiating trust
  feature.
- Continuous profiling for the query compiler once it is hot.
- Anomaly detection on operational telemetry using the same statistical library as the
  insight engine.
- Cost-per-answer as a tracked business metric.
- Exporting audit events to a customer's SIEM for enterprise tenants.
