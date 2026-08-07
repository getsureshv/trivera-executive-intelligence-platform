# ADR-002: Backend Framework and Language

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`03_PLATFORM_ARCHITECTURE.md` proposes FastAPI + Python + Pydantic + SQLAlchemy +
Alembic for the backend, and Next.js + React + TypeScript for the frontend. Phase 0 must
either confirm these or change them for a **meaningful architectural advantage**, not
merely because alternatives exist.

The workload the backend must actually carry is not a generic CRUD API. It is:

1. **Metadata-heavy governance** — versioned semantic entities, mappings, metric
   definitions, config bundles. Mostly transactional CRUD with hard invariants.
2. **A compiler** — the metric expression AST → analytical SQL compiler (ADR-006,
   ADR-007). This is the intellectually hardest component and the one where a type error
   becomes a wrong number on a CEO's screen.
3. **Data movement** — driver-level access to Postgres, SQL Server, REST, Excel, CSV,
   and eventually Snowflake/Databricks/BigQuery/Salesforce.
4. **Statistics** — deterministic and statistical signal detection (`05_KPI_INSIGHT_ENGINE.md`):
   seasonality, robust z-scores, changepoint detection, forecast deviation.
5. **LLM orchestration** — constrained plan generation, validation, explanation.

Items 3, 4, and 5 are where Python's ecosystem is not merely adequate but decisively
ahead. Item 2 is where Python is weakest.

## Decision

**Confirm FastAPI + Python 3.12+ as the backend, with three non-negotiable additions the
current documentation does not state:**

1. **Strict static typing is mandatory.** `mypy --strict` (or Pyright strict) over the
   whole `eip` package, no untyped defs, no implicit `Any`, CI-blocking. The metric
   compiler and the semantic model are metadata-driven; Python's dynamism is a liability
   there and must be bought back deliberately.
2. **Pydantic v2 at every boundary, plain dataclasses/domain objects inside.** Pydantic
   models are for HTTP, connector configuration, LLM output validation, and persisted
   JSON documents (the metric AST, config bundles). Domain logic does not depend on
   Pydantic — that keeps the metric engine testable and portable.
3. **Async by default at the I/O edges, sync for CPU-bound compilation.** SQLAlchemy 2.x
   async sessions for request handling; connector extraction runs in the worker
   (ADR-009), never inside a request. No blocking driver calls on the event loop —
   enforced by lint rule and by connectors declaring their execution mode.

**Confirm Next.js + React + TypeScript for the frontend**, with the constraint from
ADR-001: the Node tier is a rendering and aggregation tier with **no database access and
no business logic**. It calls the versioned API like any other client.

## Alternatives Considered

- **Go.** Genuinely attractive: static typing, trivial concurrency for a
  connector/ingestion tier, single-binary deployment, and a compiler that would catch the
  class of bug that most endangers this product. Rejected because the statistical
  layer (item 4) and the connector breadth (item 3) would be built from scratch, and
  because the metric compiler benefits more from fast iteration than from raw
  concurrency. Reconsider Go for a *dedicated extraction service* if ingestion becomes a
  throughput bottleneck — that is a narrow, well-bounded extraction.
- **JVM (Kotlin/Java + Spring Boot).** The strongest technical rival, chiefly because of
  **Apache Calcite** — a mature relational algebra, parser, and multi-dialect SQL
  generator that is close to what ADR-007's governed query compiler must build. Adopting
  Calcite would be a real advantage for the compiler and for multi-engine dialect support.
  Rejected because it would cost us the Python data/LLM ecosystem, raise the team's
  operational surface, and slow the phases where the product risk actually lives
  (semantics and trust, not query planning breadth). **This is the closest call in Phase 0
  and it is recorded here honestly**; if the compiler becomes the dominant source of
  defects, revisit via a new ADR rather than drifting.
- **Node/TypeScript backend (one language end to end).** Rejected. One language is a real
  benefit, but the analytics/statistics ecosystem is materially weaker, database driver
  coverage for enterprise sources (SQL Server, Oracle, Snowflake) is thinner, and we would
  still end up shelling out to Python for signal detection. Worst of both.
- **Django + DRF.** Rejected. Django's admin and ORM are excellent for classic CRUD, but
  the platform's core object is a *versioned governed definition compiled into a query*,
  not a table row edited in an admin. FastAPI's schema-first, dependency-injected model
  aligns better with API-first (principle 8) and with generating `/contracts`.
- **Litestar instead of FastAPI.** Rejected — a modest ergonomics improvement against a
  materially smaller ecosystem and hiring pool. No meaningful architectural advantage.
- **Vite SPA instead of Next.js.** Reasonable and simpler; rejected because the steward
  and governance surfaces are large, form-heavy, and route-rich, where Next's routing,
  server components, and streaming pay off, and because Next gives a clean, conventional
  BFF seam. The decision is close enough that it is recorded as a low-cost reversal.

## Rationale

The dominant risks in this platform are **semantic correctness** and **trust**, not raw
request throughput. Python maximizes iteration speed on semantics, statistics, and LLM
orchestration; strict typing buys back the safety that Python nominally lacks; and the
components that would most benefit from a different language (extraction throughput,
query planning) are isolated behind ports and can be extracted later without a rewrite of
the governed spine.

Confirming rather than changing the stack is the correct Phase 0 outcome here: none of
the alternatives offers an advantage large enough to justify the change, and two of them
(Go for extraction, Calcite for compilation) are available later as targeted, bounded
adoptions rather than as up-front bets.

## Consequences

- Positive: one language covers connectors, statistics, and AI orchestration.
- Positive: FastAPI generates the OpenAPI document that drives `/contracts` and the
  generated TypeScript client (ADR-001), making API-first mechanical.
- Positive: SQLAlchemy 2.x + Alembic give us typed models and first-class migrations
  (guardrail 17).
- Negative: strict typing slows early velocity and will feel punitive in the connector
  layer where source schemas are dynamic. Accepted deliberately.
- Negative: Python's GIL makes CPU-bound work (compiling many metric ASTs, computing
  signals) a per-process concern. Mitigated by moving that work to the worker tier and
  by process-level scaling.
- Negative: we must build the query compiler ourselves rather than adopting Calcite.
  This is the largest single engineering cost accepted in Phase 0.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Metric compiler defects produce silently wrong numbers | Golden-dataset tests; property-based tests; metric acceptance assertions (ADR-006) | Compiler is pure and typed; no I/O; 100% branch coverage requirement |
| Blocking calls on the async event loop | Latency regression alerts; `flake8-async`-style lint | Connectors declare sync/async; sync connectors run only in the worker |
| Strict typing gets disabled "temporarily" | CI config diff review | Loosening type strictness requires an ADR reference |
| Next.js Node tier accretes business logic | Code review; ADR-001 dependency policy | No DB credentials in the web deployment; BFF may only fan out to the API |
| Team cannot hire for strict-typed Python at scale | Hiring funnel | Mainstream stack; strictness is learnable in days |

## Future Considerations

- If ingestion throughput dominates cost, extract an extraction service in Go behind the
  connector port (ADR-004) — a bounded change, not a rewrite.
- If the query compiler becomes the dominant defect source, evaluate Calcite (via a JVM
  sidecar) or SQLGlot's optimizer as a compilation backend, keeping our AST as the input
  contract.
- Evaluate a Rust/PyO3 extension only if signal detection over many series becomes the
  bottleneck; prefer pushing that computation into the analytical store first.
- Re-evaluate Next.js vs. a plain SPA after the executive command center ships, when we
  know whether SSR actually earns its cost for an authenticated, data-heavy app.
