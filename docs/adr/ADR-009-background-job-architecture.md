# ADR-009: Background Job and Pipeline Architecture

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`03_PLATFORM_ARCHITECTURE.md` proposes "Celery or Dramatiq initially; Temporal later, only
if durable, long-running workflows justify it."

Phase 0 review rejects the framing of that sentence, for a reason that is easy to miss:
**the platform's background work is not incidental infrastructure — its run history is
product data.**

Consider what the product must show an executive: a freshness badge ("as of 06:14 today"),
a quality badge, a lineage trail, and an answer to "why did this number change?"
(ADR-007's provenance envelope). Every one of those is a projection of *what ran, when,
against which source watermark, producing which snapshot*. If that state lives inside a
Celery result backend or inside Temporal's internal event history, then the product has to
either duplicate it or query a workflow engine to render a KPI card. Both are wrong.

Second consideration: ingestion is a multi-step, partially-failing, resumable pipeline
(`discover → extract → land → validate → transform → load → materialize → observe`).
Celery canvases (chains/chords/groups) model this badly — they lose state on broker
failure, have poor visibility, and are notoriously fragile at exactly the retry/partial-
failure cases that dominate real ingestion. Migrating from Celery canvases to Temporal
later is a rewrite of the pipeline layer, not a swap of a dependency.

## Decision

**Own the pipeline state in PostgreSQL. Use a simple task queue for execution. Do not
adopt Temporal now, and do not model pipelines as Celery canvases ever.**

### 1. Pipelines are explicit, persisted state machines in our own schema

```
PipelineRun    (id, tenant_id, kind, data_source_id, config_version,
                status, trigger, requested_by, started_at, finished_at,
                snapshot_id, error_summary)
PipelineStep   (id, run_id, ordinal, name, status, attempt, started_at,
                finished_at, input_ref, output_ref, metrics jsonb, error jsonb)
SourceWatermark(tenant_id, source_object_id, cursor, committed_at, run_id)
```

- Steps are **idempotent** and keyed so a retry is safe.
- Watermarks commit **atomically with** the step that produced them.
- `snapshot_id` is what the query envelope reports as `data_snapshot_id` (ADR-007).
- Run/step history is queryable by the application, because it *is* the freshness,
  ingestion-audit, and provenance feature.

### 2. Task execution: **Dramatiq**, with Redis as broker

Chosen over Celery for simpler semantics, better defaults, less implicit behaviour, and a
much smaller surface to reason about. The choice is deliberately low-stakes: because the
workflow state lives in our tables, the broker is only responsible for "run this step
soon," and replacing it is a contained change.

**Postgres-as-broker (`SELECT ... FOR UPDATE SKIP LOCKED`) is the sanctioned fallback** if
we want to drop a piece of infrastructure early; the job envelope is designed to work with
either.

### 3. The transactional outbox is mandatory

Jobs are never enqueued directly from request handlers. A handler writes an `outbox` row
in the same transaction as its state change; a relay publishes it. This eliminates the
classic "the database rolled back but the job already ran" and "the job never got enqueued
because the broker blipped" failures. In a governance product, a publish that emits an
audit event but no reindex job — or vice versa — is a correctness bug.

### 4. Every job carries a typed envelope; tenant context is mandatory

```
JobEnvelope { job_id, tenant_id, config_version, actor, trace_id,
              idempotency_key, attempt, payload }
```

Workers **refuse** any job without a resolvable `tenant_id` (ADR-003). Tenant context is
established from the envelope before any handler code runs, and the same
`TenantContext`/RLS mechanism applies as in a request. `trace_id` propagates so an
ingestion span is continuous with the request that triggered it.

### 5. Queue classes and fairness

Separate queues with separate worker pools: `interactive` (connection tests, discovery,
sampling), `ingestion` (extract/load), `compute` (metric observation, signal detection),
`ai` (LLM calls), `maintenance`. Rationale: a twelve-hour backfill for one tenant must not
delay another tenant's "Test Connection" click.

Per-tenant fairness is enforced by a concurrency cap per tenant per queue class, so no
single tenant can monopolize workers. This is the multi-tenancy requirement most often
forgotten in the job tier and it is cheap to build in from the start.

### 6. Scheduling

A single leader-elected scheduler evaluates per-tenant, per-source schedules (cron
expressions in the tenant's timezone, honouring the fiscal calendar where relevant) and
enqueues runs through the outbox. Overlap policy is explicit per pipeline
(`skip` | `queue` | `cancel_previous`). Missed windows are recorded, not silently dropped
— a skipped ingestion is a freshness fact the executive surface must be able to show.

### 7. Temporal: deferred, with named adoption triggers

Adopt Temporal when **two or more** hold:

- workflows routinely span days with human-in-the-loop waits (e.g. a multi-week onboarding
  approval saga);
- we operate multiple services requiring distributed sagas with compensation;
- pipeline step counts and branching outgrow a readable state machine;
- we need durable timers at a scale a scheduler table cannot serve.

Because our pipeline state is already explicit and persisted, adopting Temporal later
means moving *orchestration* while the *state model stays* — a far cheaper migration than
from Celery canvases, which was the trap in the original recommendation.

## Alternatives Considered

- **Celery (as documented).** Rejected. Heavier and more implicit than Dramatiq, with
  canvas/chord semantics that are unreliable precisely at partial-failure. Its result
  backend would also become a shadow copy of pipeline state.
- **Temporal from day one.** Rejected. Real operational weight (server, its own database,
  versioning discipline, workers) plus a learning curve, all before we have a workflow
  complex enough to need it. Its bigger drawback here is that it wants to own the run
  history that we need to own as product data.
- **Airflow / Dagster / Prefect.** Rejected. They are analyst-facing, DAG-authoring
  orchestrators for a fixed set of pipelines. Our pipelines are **per-tenant,
  metadata-driven, and dynamically shaped by configuration**; a DAG-file model fights that
  directly, and multi-tenant isolation in these tools is weak.
- **Serverless functions + a cloud queue (SQS/Pub-Sub + Lambda).** Rejected: vendor
  coupling, cold-start hostility to long extractions, and awkward long-running work.
- **Pipeline state inside the job framework (any framework).** Rejected on the central
  argument of this ADR: that state is product data.
- **ARQ / RQ.** Viable and similar to Dramatiq; no meaningful advantage. Dramatiq's
  middleware model and retry semantics are marginally better suited.

## Rationale

The decisive insight is the ownership of run state. Freshness badges, ingestion audit,
provenance, replay, and "what ran when" are all features on the roadmap; building them on
top of a queue's internal bookkeeping is a category error. Once run state is ours, the
queue becomes a small, replaceable component — which is why choosing Dramatiq over Celery
is a low-risk decision and why deferring Temporal is safe rather than a gamble.

The outbox and per-tenant fairness are included now because both are cheap at the start
and expensive to retrofit once dozens of enqueue sites exist.

## Consequences

- Positive: freshness, provenance, and ingestion audit are direct reads of our own tables.
- Positive: resumable, idempotent pipelines from the first connector.
- Positive: no lost or phantom jobs (outbox).
- Positive: noisy-neighbour protection in the worker tier from day one.
- Positive: a later Temporal adoption is additive, not a rewrite.
- Negative: we build the state machine, retry policy, and scheduler ourselves — perhaps
  1–2 weeks of work that a framework would have supplied. Accepted deliberately.
- Negative: two more moving parts to operate (scheduler leader election, outbox relay).
- Negative: Redis now carries both cache and broker duty; they must be separated
  logically (different instances or databases) so a cache flush cannot destroy the queue.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Home-grown orchestration accretes complexity and becomes a bad Temporal | Step-count and branching complexity per pipeline; incident review | Named Temporal adoption triggers; keep the state machine linear with explicit branches |
| Non-idempotent step retried, duplicating data | Reconciliation counts; unique keys on load | Idempotency key per step; upsert-on-natural-key loads |
| Watermark advances without data committed | Row-count reconciliation per run | Watermark committed in the same transaction as the batch |
| Outbox relay lags or stalls | Outbox depth and age alarms | Relay health check; at-least-once with idempotent handlers |
| One tenant's backfill starves others | Per-queue depth by tenant | Per-tenant concurrency caps; separate queue classes |
| Scheduler double-fires after leader failover | Duplicate run detection on `(tenant, pipeline, window)` | Unique constraint on the scheduling window; fencing token |
| Redis loss drops queued work | Broker health alarms | Outbox is durable in Postgres; unacknowledged work is re-derivable from `PipelineRun` status |
| Long-running extraction blocks a worker for hours | Step duration telemetry | Batched, resumable steps; each batch is a short unit of work |

## Future Considerations

- Temporal adoption per the triggers above, keeping `PipelineRun`/`PipelineStep` as the
  product-facing projection.
- Backpressure and adaptive concurrency based on source rate-limit profiles (ADR-004).
- A tenant-visible "pipeline activity" surface — run history is already the right shape
  for it.
- Priority boosting for the interactive queue during onboarding, when responsiveness
  matters most.
- Executing connector work inside a tenant-deployed agent (ADR-004) — the envelope-driven
  design already supports remote step execution.
