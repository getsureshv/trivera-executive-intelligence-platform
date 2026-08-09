# Phase 2 Data Source Manager — Completion Report

Date: 2026-08-09

## Delivered slice

Phase 2 now provides tenant-owned PostgreSQL data sources protected by forced PostgreSQL
row-level security, write-only credential handling through `SecretStore`, authorized source
management, durable background connection testing through PostgreSQL/outbox/Redis/Dramatiq,
safe ordered diagnostics, and the smallest Add Source / Test Connection browser experience.

The approved closeout behavior disables a source immediately while retaining its audit
tombstone. Deletion increments the source version, which fences older queued or running
attempts before credential or network access. The referenced credential remains recoverable
until the explicit UTC deadline exactly 30 days later. Tenant-scoped maintenance then deletes
that reference idempotently and records the committed result. Safe terminal connection-test
rows are retained for 90 days; audit events are not pruned.

## Security properties verified by the implementation

- Tenant context is derived from authenticated membership; APIs and browser requests accept
  no tenant identifier.
- Source, ACL, and connection-test tables use non-null tenant identifiers, forced RLS,
  fail-closed policies, same-tenant foreign keys, and constrained runtime grants.
- Credentials never enter source configuration, job envelopes, diagnostics, responses,
  audit details, or browser state. Only `SecretStore` receives values.
- Deletion and maintenance are idempotent. Secret destruction occurs before its database
  completion claim, so a failed commit is safely retryable; the target reference is rebuilt
  only from the locked RLS-scoped source row.
- Source status and version are checked immediately before secret/network access and again
  before result persistence. A disabled or superseded attempt cannot overwrite newer work.
- Unauthorized and absent source/job identifiers retain the same client-visible response.

## Retention and operational behavior

- Source tombstones: retained.
- Credential recovery: 30 days from `disabled_at`.
- Safe terminal connection-test history: 90 days from `queued_at`.
- Audit history: unchanged and append-only.
- Latest connection test: newest retained row; absent after all retained rows expire.

## Explicitly not delivered

Discovery, profiling, extraction, ingestion, object-storage landing, semantic mapping,
metrics, dashboards, insights, lineage, alerts, AI, other connector types, customer-network
agents, production deployment, and a production `SecretStore` adapter remain excluded.

## Deployment condition

The production `SecretStore` adapter remains an open deployment gate. Local and CI file
storage is not approved for production. Phase 2 completion does not authorize deployment.

## Release verdict

Codex independently reviewed the implementation and required one focused test repair.
Observed local results were: 10 focused PostgreSQL adversarial tests, 349 complete API and
PostgreSQL tests, 28 real identity-provider tests, 22 worker/PostgreSQL/Redis tests, and 12
browser tests passed with zero skips. Ruff, strict mypy, frontend and contract type checks,
unit tests, and the production web build also passed. The credential-free browser evidence
is `docs/evidence/phase-2-data-source-manager.png`.

Stage 4 implementation commit `c981cda1ed710ddf34d5a00118e7c93dd6d7f8e0` and its
approved generated-record repair `e4563cf4164b8d76ed2f374d7f457d4f8d561606` were pushed.
All seven jobs passed in GitHub Actions run 31320964652. The records-only closeout commit
and its green CI are the final repository-integrity gate; no Phase 2 implementation remains.
