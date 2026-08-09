# Phase 2 Stage 1 — Tenant-owned source persistence and authorization

## Authority

Authorized by the product owner and bounded by
`docs/22_PHASE_2_DATA_SOURCE_MANAGER_ENTRY_REPORT.md`, accepted ADR-003, ADR-010,
ADR-014, ADR-015, ADR-016, and the accepted first vertical slice.

## Assignment

Implement only the tenant-owned Data Source persistence and authorized HTTP management
boundary:

- add an Alembic migration for `data_source` and `data_source_acl` with non-null
  `tenant_id`, constrained foreign keys/checks/indexes/grants, enabled and forced
  PostgreSQL RLS, and the standard fail-closed tenant policy;
- add DataSource and ACL models, repository/service boundaries, and model registration;
- add closed capabilities `source.read`, `source.create`, `source.update`, `source.delete`,
  `source.test`, and `source.acl.manage`, keeping database seeds and Python mappings equal;
- grant tenant administrators all source capabilities; grant data stewards read/create/
  update/test/ACL management but not delete; executives/viewers receive none;
- enforce capability plus default-deny resource ACL checks, with creator `manage` and
  tenant-administrator policy administration; unauthorized and absent identifiers must
  return the same not-found response;
- add `POST`, list `GET`, item `GET`, and `PATCH /v1/data-sources`; do not add DELETE or
  connection-test routes in this stage;
- accept PostgreSQL non-secret configuration separately from a write-only credential;
  validate configuration against the existing PostgreSQL connector contract;
- write/rotate credentials only through `SecretStore`, persist only tenant-bound logical
  name and version, compensate safely if the database transaction fails, and return only
  `credential_configured: true`;
- require idempotency for create and `If-Match` for update; use stable safe response and
  error shapes;
- add closed, redacted audit actions for source creation, update, credential rotation,
  and denied access. Audit details must contain identifiers and changed field names only,
  never configuration values, endpoints, usernames, secret references, or credentials;
- add focused unit, API, migration, RLS, authorization, cross-tenant, rollback, and secret
  leakage tests against real PostgreSQL.

## Allowed implementation area

- `apps/api/migrations/versions/`
- `apps/api/src/eip/connectivity/` and its public composition points
- `apps/api/src/eip/api/` router/composition/dependency files
- `apps/api/src/eip/platform/context.py` only for closed capability declarations/mapping
- `apps/api/src/eip/governance/audit.py` only for closed audit actions
- `apps/api/src/eip/models.py`
- relevant `apps/api/tests/` files and fixtures
- shared API contracts only if required to keep the committed OpenAPI surface honest
- this stage's `automation/results/` record and `automation/status.md`

Do not weaken existing architecture boundaries or change the `SecretStore` port. If a
needed edit falls outside this list, stop and return the reason before making it.

## Required adversarial verification

- Real PostgreSQL schema inspection proves both new tables have `tenant_id`, enabled and
  forced RLS, the expected policy, and constrained runtime grants.
- Tenant B cannot list, fetch, update, grant access to, or infer Tenant A's source through
  the API, service, guessed identifier, forged tenant header/body, or direct application
  SQL. Absent and unauthorized identifiers are indistinguishable.
- A cross-tenant secret reference is rejected before persistence or secret retrieval.
- Unique sentinel credentials are absent from all database text/JSON columns, API bodies,
  audit rows, logs, exceptions, and source configuration.
- A failed database commit after secret creation triggers compensating secret deletion;
  a failed rotation leaves the prior stored reference consistent and usable.
- Create idempotency and update version conflicts are deterministic.

## Verification gate

- Ruff format check and lint for API and worker
- strict mypy for API and worker
- architecture and connectivity unit suites
- migration upgrade, downgrade, re-upgrade, and model-drift check
- focused Stage 1 API/security tests against real PostgreSQL with zero skips
- complete existing real-PostgreSQL security suites with zero skips
- `git diff --check` and independent complete diff review by Codex

## Explicit exclusions

No deletion route or retention job, connection-test persistence/jobs/UI, discovery,
profiling, extraction, ingestion, object storage, semantic mapping, metrics, dashboards,
insights, lineage, alerts, AI, additional connectors, customer-network agents, production
secret adapter, or deployment work.

## Stop conditions

Stop and report the exact issue for a new product choice, ADR conflict, security exception,
required edit outside the allowed area, failed real-infrastructure test, permission
problem, or scope expansion. Do not silently reinterpret the assignment.
