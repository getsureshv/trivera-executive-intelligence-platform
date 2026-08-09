# Phase 2 Stage 2 — Durable background connection testing

## Authority

Authorized and bounded by `docs/22_PHASE_2_DATA_SOURCE_MANAGER_ENTRY_REPORT.md`,
accepted ADR-003, ADR-004, ADR-009, ADR-014, ADR-015, PO-002, and the completed Stage 1
source-management contract at commit `6c811b6`.

## Assignment

Implement the smallest durable, tenant-isolated background PostgreSQL connection-test
workflow:

- add migration/model persistence for `connection_test` with tenant id, source id and
  source version, requested actor, status, safe ordered checks, safe overall/failure code,
  attempt, trace/idempotency fields, and timezone-aware lifecycle timestamps;
- enable and force PostgreSQL RLS, add the fail-closed tenant policy, constrained grants,
  same-tenant composite source foreign key, indexes/checks, and tenant-scoped idempotency;
- expose `POST /v1/data-sources/{id}/test` returning `202` with job id/status/poll and
  `GET /v1/connection-tests/{id}` returning state plus safe diagnostics;
- require `source.test` plus source visibility, return uniform not-found for absent or
  unauthorized sources/jobs, accept no tenant id, and reject disabled sources;
- in one transaction create the queued row, safe audit event, and
  `connection_test.requested` outbox message. Payloads contain identifiers and typed
  execution metadata only—never configuration, endpoint, username, SecretRef, or value;
- dispatch the outbox message through the existing Redis/Dramatiq seam to an interactive
  connection-test actor. Preserve existing non-connection outbox behaviour;
- make the typed worker envelope require job id, tenant id, source id/version, actor,
  trace id, idempotency key, and attempt. Refuse malformed or tenant-less messages before
  database, network, or secret access;
- execute with the constrained application role inside tenant context. Re-read and fence
  the job/source/version before secret or network access, transition queued → running →
  succeeded/failed idempotently, and make duplicate delivery/restart safe;
- construct the existing `PostgreSQLConnector` only after fencing, obtain the credential
  from `SecretStore` only for `test_connection`, use the existing outbound-network policy,
  and persist/return only its closed ordered diagnostic result;
- audit requested/completed/failed/denied transitions with allowlisted identifiers,
  statuses, safe codes and durations. Logs/traces include tenant/source/job/trace and safe
  outcome only;
- add an interactive per-tenant concurrency limit that prevents one tenant from starving
  another without adding a cache or broad scheduling framework;
- add focused unit, real PostgreSQL, real Redis/worker, isolation, idempotency, stale-work,
  rollback, and leakage tests.

## Required adversarial scenario

Attempt A is queued against source version 1. Before A mutates job state, reads a secret,
or opens a network connection, update/rotate the source to version 2 and complete attempt
B. Resume A. Prove A is fenced before secret/network access, cannot overwrite B's latest
result, and cannot emit completion audit/outbox evidence. Only B's safe result remains the
latest source result.

Also prove:

- Tenant B cannot enqueue, poll, infer, or directly query Tenant A's job.
- A cross-tenant source/job/secret mismatch fails before secret/network access.
- Transaction rollback leaves neither runnable work nor a committed job.
- Duplicate Redis/Dramatiq delivery executes at most one terminal transition.
- Wrong password and unreachable host are distinguishable safe results against real
  PostgreSQL/network behaviour; no raw driver error, endpoint, username, SecretRef, or
  credential appears in DB, API, audit, outbox, broker message, logs, traces, or errors.
- One tenant's blocked jobs do not prevent another tenant's interactive test from running.

## Allowed implementation area

- one new Alembic migration and matching connectivity models/services
- Stage 1 data-source service only where needed for the test request boundary
- data-source/connection-test API routers and composition
- governance audit/outbox closed constants and safe publishing
- worker broker, dispatch, actor/handler, lifecycle, and focused worker tests
- settings only for a small typed concurrency/queue setting with safe defaults
- relevant API/worker tests and fixtures
- OpenAPI/shared contracts required by the new endpoints
- this stage's automation result and status

Do not change Stage 1 authorization semantics, the connector diagnostic contract, the
`SecretStore` port, analytical data-plane provisioning, or central migration ownership.
Stop if a required edit exceeds this area.

## Verification gate

- Ruff format/lint and strict mypy for API and worker
- architecture, connectivity, unit, and integration tests
- migration downgrade/re-upgrade and model-drift check
- focused connection-test API/worker tests against real PostgreSQL and Redis with zero
  skips, including the adversarial stale-attempt scenario
- real PostgreSQL success, wrong-password, authorization, metadata, and latency execution
- complete existing PostgreSQL and worker security suites with zero skips; separate real
  Keycloak suite with zero skips
- TypeScript formatting/lint/typecheck/tests and deterministic OpenAPI regeneration
- complete independent diff review and `git diff --check`

## Explicit exclusions

No browser UI (Stage 3), delete/retention cleanup (Stage 4), discovery, profiling,
extraction, ingestion, object storage, semantics, metrics, dashboards, insights, lineage,
alerts, AI, caching, additional connectors, customer-network agents, production secret
adapter, or deployment work.

## Stop conditions

Stop for a new product decision, ADR conflict, security exception, failed/skipped mandatory
verification, permission problem, destructive production action, or scope expansion. One
bounded repair is allowed after Codex review; no second unresolved implementation failure.
