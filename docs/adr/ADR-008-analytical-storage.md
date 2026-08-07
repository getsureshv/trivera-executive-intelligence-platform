# ADR-008: Analytical Storage Strategy

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`03_PLATFORM_ARCHITECTURE.md` proposes "PostgreSQL initially where practical, abstracted
so ClickHouse / Snowflake / BigQuery / Databricks can be introduced later without
touching business logic." Phase 0 must decide three things the document leaves open:

1. Is the platform **materializing** data or **federating** to sources? (Answered in
   ADR-007: materializing.)
2. What engine, and when do we change?
3. Is "abstracted so any engine can be swapped in" actually achievable, or is it the kind
   of claim that quietly becomes a lowest-common-denominator SQL builder that performs
   badly everywhere?

The third question deserves scepticism. Snowflake, Databricks, BigQuery, ClickHouse, and
PostgreSQL differ in transactional DDL, exact vs. approximate distinct counts, window
function coverage, semi-structured types, temporary table support, and cost models. A
naive `Engine` interface produces either a crippled feature set or per-engine `if`
branches — the second of which is exactly what principle 4 forbids.

There is also a strategic fork hiding in the vendor list: **Snowflake, Databricks, and
BigQuery are systems the customer probably already owns.** Treating them as
interchangeable back-ends to *our* infrastructure conflates two distinct product modes.

## Decision

### 1. PostgreSQL is the sole analytical engine through the executive-command-center phase

One engine. Schema-per-tenant in the analytical plane (ADR-003), with:

- columnar-friendly modelling (narrow fact tables, surrogate-keyed dimensions);
- partitioning by time on large facts; BRIN indexes on time columns;
- `pg_stat_statements` on from day one for query attribution;
- a strictly read-only role for the governed query path, a separate writer role for
  ingestion.

Explicitly **no** second analytical engine is introduced "just in case." Adding one before
there is load costs operational surface, splits the test matrix, and buys nothing
measurable.

### 2. The port is `AnalyticalEngine` with a **declared capability matrix**, not a
lowest-common-denominator SQL builder

The port accepts a `QueryPlan` (ADR-007) — relational algebra, not SQL — and each engine
adapter compiles it to its own dialect. Alongside, each adapter declares capabilities:

```
exact_distinct_count, window_functions[], percentile_exact, transactional_ddl,
semi_structured_types, array_types, temp_tables, merge_upsert,
max_identifier_length, cost_model, concurrency_profile, supports_rls
```

The compiler negotiates: where a capability is absent it either chooses a supported
strategy or **fails loudly with a specific reason**. It never silently degrades exactness
(an approximate distinct count must be labelled in the result envelope, never substituted
quietly).

### 3. A dialect conformance suite exists from Phase 1, with one implementation

A single, engine-agnostic test suite runs every `QueryPlan` shape the compiler can emit
against every registered adapter and asserts identical results on a golden dataset. With
one engine it is a regression suite; the day a second engine is added it is the thing that
makes the swap credible instead of hopeful. Writing it later means retrofitting semantics
across an already-large plan surface — this is the cheap-now, expensive-later item.

### 4. Named trigger conditions for introducing a second engine

We move — and only then — when **two or more** of these hold, sustained over two weeks:

- a single tenant's largest fact table exceeds ~10⁸ rows, or ~500 GB per tenant;
- p95 governed-query latency on cache miss exceeds 3 s after indexing and partitioning
  work is exhausted;
- concurrent analytical load measurably degrades the metadata workload (if co-located) or
  requires vertical scaling beyond the largest sensible instance;
- insight-engine backfills cannot complete inside their window.

When triggered, the default second engine is **ClickHouse** (self-hosted, columnar,
excellent scan/aggregation performance, low cost per TB, strong fit for metric
observation series). Not Snowflake/Databricks — see §5.

### 5. Two distinct product modes, not one abstraction

- **Mode A — Platform-managed store (default, and what we build).** We ingest into our
  tenant-isolated analytical store (PostgreSQL now, ClickHouse later). We control
  freshness, cost, and performance.
- **Mode B — Bring-your-own-warehouse.** The customer's Snowflake/Databricks/BigQuery is
  the analytical store; we push compute there and their data never leaves their account.
  This is a *different product*: different security posture, different pricing, different
  connector role (the warehouse is both source and compute target), different performance
  ownership.

Mode B is architecturally compatible — it is another `AnalyticalEngine` adapter plus a
"no-copy" ingestion mode — but it is **a go-to-market decision, not a technical one**, and
it is escalated as a product-owner question. It should not be built speculatively; it
should also not be designed out, and the port above does not design it out.

### 6. DuckDB is permitted, scoped to ingestion and profiling only

Parsing, type-inferring, and profiling Excel/CSV/Parquet extracts in-process is exactly
what DuckDB is good at, and doing it in the worker avoids landing junk in the analytical
store to inspect it. It is **not** the analytical store and is not queryable by the
governed query path. This scoping is stated explicitly so the exception does not creep.

### 7. Raw landing zone in object storage

Every extract lands raw and compressed under a tenant prefix before transformation
(ADR-004). It is the replay source when a mapping changes and the audit artifact for what
the source said on a given date. Retention is per-tenant configuration.

### 8. Metric observation store

A dedicated, partitioned `metric_observation` table per tenant records
`(metric_version_id, period, dimension_key, value, computed_at, config_version,
data_snapshot_id, origin)`. This is required — not optional — because the insight engine's
signals (trend reversal, sustained decline, week-over-week) need a **stable history**, not
a recomputation of the past against today's definitions. Recomputing history each run is
both expensive and wrong: it would erase the record of what the business believed at the
time. This store is also what makes restatement visible rather than silent (ADR-012).

## Alternatives Considered

- **ClickHouse from day one.** Rejected. Superb at the eventual workload, but it costs
  operational surface, weaker transactional/DDL ergonomics during rapid schema iteration,
  and a second store to secure and back up — all before there is a single tenant with real
  volume. Adopted as the *named* second engine so the decision is pre-made, not deferred
  indefinitely.
- **DuckDB as the analytical store.** Rejected. Excellent embedded engine, poor fit for
  concurrent multi-tenant serving with durable shared state. Scoped to ingestion instead.
- **Snowflake/Databricks/BigQuery as our managed store.** Rejected for the default mode:
  variable cost per query is hostile to a product that fires many small governed queries,
  and it puts our margin at a vendor's mercy. Retained for Mode B.
- **Trino/Presto federation instead of ingestion.** Rejected as the default (ADR-007), for
  freshness knowability, source-system safety, and insight-engine stability. Genuinely
  attractive for Mode B customers; revisit there.
- **Lakehouse (Parquet + Iceberg/Delta on object storage) from day one.** Rejected as
  premature — it adds a table format, a catalog, and a compute engine to solve a scale
  problem we do not have. It is, however, the most likely *third* step and the raw landing
  zone (§7) is deliberately the first half of it.
- **One database for both metadata and analytics.** Rejected: separate PostgreSQL
  instances (or at minimum separate roles, schemas, and connection pools) so analytical
  scans cannot starve the control plane.

## Rationale

The honest position in Phase 0 is that we do not yet know the data volumes, and choosing a
big analytical engine on speculation trades certain operational cost for uncertain
benefit. PostgreSQL will comfortably serve early tenants, and the things that make a later
migration cheap — a plan-based port, a capability matrix, a conformance suite, a raw
landing zone — cost little now and are nearly impossible to retrofit.

The most valuable part of this ADR is not the engine choice; it is naming the **trigger
conditions** and the **default second engine**, so the migration is a pre-agreed
operation rather than an emergency, and separating **Mode A from Mode B** so a
go-to-market decision does not get made accidentally by an engineer choosing a driver.

## Consequences

- Positive: minimal infrastructure now; one dialect to test; fast iteration on schema.
- Positive: engine change is a bounded project with a pre-written acceptance suite.
- Positive: the observation store makes insights stable and restatement visible.
- Negative: PostgreSQL will become a limit; we accept the certainty of a future migration
  in exchange for speed now.
- Negative: two stores (metadata PG + analytical PG) plus object storage plus Redis is
  already four systems to operate at Phase 1.
- Negative: the conformance suite is real work with no visible product output until the
  second engine exists. It must be defended in planning.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| PostgreSQL limits are hit sooner than expected | Per-tenant row counts, p95 latency, scan volume dashboards | Trigger conditions defined; ClickHouse pre-selected; conformance suite ready |
| The port leaks PostgreSQL assumptions | Architecture test: no `psycopg`/dialect import outside the adapter; conformance suite | Plan-based port; capability negotiation |
| Silent degradation (approximate counts) misleads executives | Result envelope carries exactness flags | Compiler must label, never substitute |
| Analytical load starves the control plane | Separate instances/pools; connection saturation alerts | Physical separation from Phase 1 |
| Per-tenant schema sprawl becomes unmanageable | Schema count and DDL-run telemetry | Analytical DDL is generated and versioned by a provisioning subsystem, not hand-written |
| Object-storage landing zone grows without bound | Cost per tenant | Lifecycle policies; per-tenant retention configuration |
| DuckDB creeps out of ingestion into serving | Import contracts | Explicit scope in this ADR + CI rule |

## Future Considerations

- ClickHouse adapter as the scale-out path; the `metric_observation` series is its most
  natural first workload.
- Iceberg/Delta over the raw landing zone if reprocessing volumes grow.
- Bring-your-own-warehouse (Mode B) as a distinct commercial offering.
- Per-tenant residency by placing analytical schemas in regional instances.
- Automatic aggregate materialization driven by observed plan hashes (ADR-007).
