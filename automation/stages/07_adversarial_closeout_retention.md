# Phase 2 Stage 4 — adversarial closeout and deletion/retention

## Objective

Close the approved Data Source Manager slice. Implement the already-decided deletion and
retention behavior, prove it on the real PostgreSQL/Redis/browser stack, and produce the
Phase 2 completion and Thursday walkthrough records. Do not begin the CEO dashboard or
any other roadmap capability.

## Accepted behavior

- `DELETE /v1/data-sources/{id}` requires `source.delete` and manage access. Only tenant
  administrators receive `source.delete`; existence remains concealed with the same 404
  and durable safe denial audit used by read/update.
- Deletion is idempotent and immediately changes the source to `disabled`, increments its
  version, prevents new tests, and fences queued/running older-version attempts before
  secret or network access. Keep the source row as the audit tombstone.
- Keep the credential recoverable for exactly 30 days. Persist an explicit UTC destruction
  deadline and destruction timestamp. An idempotent background maintenance operation
  deletes only the tenant-bound referenced secret after the deadline and records a safe
  audit event. It must tolerate retries and must never delete another source's credential.
  No restore API or product behavior is authorized in this slice.
- Retain safe connection-test rows for 90 days. An idempotent tenant-safe maintenance
  operation removes expired rows without weakening audit retention or RLS. “Latest” is the
  newest retained test; after all tests expire it is absent.
- Returned/stored/logged/audited data must not expose credential values or SecretStore
  references. Timestamps must be timezone-aware UTC.

## Implementation boundary

- Add one migration after `0006`, with model parity, constraints, indexes, forced RLS and
  least-privilege grants preserved.
- Add the DELETE contract/router/service behavior and closed audit actions.
- Add a bounded maintenance command/job seam for 30-day credential destruction and
  90-day test pruning. Reuse existing SecretStore and tenant/database boundaries; do not
  add a production adapter, scheduler product, new queue architecture, or restore API.
- Add the smallest clear browser delete/disable control and status feedback. Do not put
  credentials or secret references into browser state, artifacts, URLs, or responses.
- Add focused unit/architecture tests and real PostgreSQL + Redis/worker + Playwright
  adversarial tests. Use injected clocks/fault seams where time must advance.
- Produce `docs/23_PHASE_2_DATA_SOURCE_MANAGER_COMPLETION_REPORT.md` and
  `docs/24_PHASE_2_THURSDAY_DEMO_SCRIPT.md`. The script must cover setup, Add PostgreSQL
  Source → Test Connection, security proof, expected results, and fallback/recovery notes.
  It must state that the production SecretStore adapter remains an open deployment gate.
- Produce deterministic screenshot evidence with no credential/reference material.

## Required adversarial proof

- Tenant B cannot delete, observe, test, retain, prune, or destroy Tenant A resources by
  API, guessed identifier, direct application-role SQL, forged worker input, or secret ref.
- Deleting while an older attempt pauses before secret/network access makes that attempt
  stale; it performs no SecretStore or network side effect and emits no completion event.
- Repeated DELETE and repeated destruction/pruning are safe and cannot duplicate terminal
  audit evidence or destroy a replacement/unrelated credential.
- Before day 30 the credential remains; at/after the deadline only the intended credential
  is destroyed. Failed deletion is retryable without an inconsistent database claim.
- Rows younger than 90 days remain; older rows are removed; the latest endpoint reflects
  only retained results. Audit rows remain intact.
- A unique credential sentinel is absent from database text/JSON, APIs/OpenAPI, audit,
  outbox, jobs/results, logs, traces/metrics, browser HTML/state, screenshots, and test
  artifacts.
- Complete Add Source → Test Connection browser flow still succeeds on real PostgreSQL,
  Redis, worker and API; deletion disables it immediately and is shown safely.

## Verification and handoff

Claude returns an uncommitted patch and exact local evidence. Codex independently reviews
the complete repository change and may allow one compact repair. Codex then runs Ruff,
strict mypy, TypeScript lint/typecheck/unit/build, migrations/contracts, the complete API
and worker suites on real PostgreSQL/Redis with zero skips, and the complete Playwright
suite with zero skips. Secret scans and Phase 1 security suites are mandatory.

Only after all checks pass: update `automation/results/07_adversarial_closeout_retention.md`
and `automation/status.md`, commit Stage 4 separately, push, require every GitHub Actions
job green, and confirm a clean repository. Phase 2 is not PASS before those gates.

## Exclusions

No discovery, profiling, extraction, ingestion, object-storage landing, semantic mapping,
metrics, dashboards, insights, lineage, alerts, AI, extra connector types, customer-network
agents, caching, production deployment, or production SecretStore implementation.
