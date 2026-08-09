# 22 — Phase 2 Data Source Manager Entry Report

Date: 2026-08-08

Status: **Entry review accepted — READY WITH CONDITIONS; execution authorized**

Scope: the smallest usable PostgreSQL **Add Source → Test Connection** slice only

Product-owner authorization: 2026-08-08. All four stages are authorized subject to the
handshakes and exclusions in this report.

## 1. Recommendation

**READY WITH CONDITIONS.** The repository has enough reviewed foundation to begin the
Data Source Manager. The provider-neutral connector contract, outbound-network safety
policy, PostgreSQL diagnostics, tenant context, forced PostgreSQL row-level security
(RLS), `SecretStore` abstraction, append-only audit trail, transactional outbox, worker
process, API shell, and authenticated web shell already exist and have passed CI.

Implementation may proceed through the stages in section 11 without a new ADR. It must
stop before production release unless the production `SecretStore` adapter is selected
and implemented; that adapter is explicitly excluded from this development slice. The
product owner has accepted the deletion, credential-recovery, and connection-test
retention policies recorded in section 10.

This entry review changes no application code.

## 2. Governing decisions and reviewed evidence

This report follows, in authority order:

- PO-001 through PO-005 in `20_PRODUCT_OWNER_DECISIONS.md`;
- accepted ADR-003, ADR-004, ADR-009, ADR-010, ADR-014, ADR-015, and ADR-016;
- the corrected Phase 2 roadmap in `10_IMPLEMENTATION_ROADMAP.md`;
- the first vertical-slice contract in `18_FIRST_VERTICAL_SLICE.md`;
- the Phase 1B exit conditions in `21_PHASE_1B_ENTRY_REPORT.md`.

The review also inspected all current migrations, API routers and dependencies,
connectivity code, identity and governance services, worker code, web application,
shared TypeScript contracts, and relevant architecture, connectivity, integration, and
security tests.

The controlling conclusions are:

1. Phase 2 is Data Source Manager plus the first PostgreSQL connector. The roadmap's old
   separate file-connector phase is superseded by its Phase 0 resequencing note.
2. V1 uses direct outbound connections from stable platform addresses. Customer-network
   agents, PrivateLink, and VPN remain extension points, not work for this slice.
3. A `DataSource` is tenant-owned control-plane metadata. It therefore requires a
   non-null `tenant_id`, forced PostgreSQL RLS, explicit tenant context, and a constrained
   application role.
4. Credential values never enter configuration, ordinary metadata, logs, telemetry,
   audit details, job payloads, or responses. A data source stores only a tenant-bound
   `SecretRef`.
5. Connection testing is background work. State and the work request must commit in one
   transaction through the outbox, and a worker must refuse work without tenant context.
6. Authorization is role capability plus resource access, default deny. A missing source
   and a source the caller may not access must be indistinguishable.

## 3. What Phase 2 already provides

### Connector and safety foundation

- `eip.connectivity.protocol` contains serializable provider-neutral connector values,
  `ConnectionTarget`, capability declarations, configuration schemas, discovery page
  contracts, and deterministically ordered connection diagnostics.
- `eip.connectivity.egress` denies unsafe destinations and revalidates the actual peer to
  defend against address substitution after name lookup.
- `eip.connectivity.postgresql` implements PostgreSQL configuration and the six required
  checks in order: network, TLS, authentication, authorization, metadata access, latency.
- The PostgreSQL connector obtains the password from `SecretStore` for the explicit
  `test_connection` purpose and returns safe codes and remediation text, not driver
  errors, connection strings, or credential material.
- Unit, architecture, and real-PostgreSQL connector tests cover the three completed
  connector stages. Their separate commits are `c3d46d1`, `df13813`, and `9daedae`.

### Platform foundation that can be reused

- Tenant context is derived from an authenticated membership, never from a tenant header
  or request body, and is passed explicitly to database work.
- The application database role is asserted at startup to be non-owner, non-superuser,
  and unable to bypass RLS. CI compares tenant-owned tables with live PostgreSQL policies.
- `SecretStore`, `SecretRef`, and non-serializable `SecretValue` exist. Local and CI have
  an adapter; production-like startup fails closed because G14, a production adapter,
  remains unresolved.
- Audit events are append-only and hash-chained per tenant. Audit details and outbox
  payloads pass through central redaction.
- The transactional outbox and a tenant-isolated worker relay exist. Redis/Dramatiq is a
  declared dependency and health dependency, but the relay currently logs publication;
  it does not yet dispatch or execute a connection-test actor.
- The FastAPI application already has authentication, tenant-scoped sessions, capability
  checks, uniform error handling, and versioned `/v1` routing.
- The Next.js application signs in, consumes the HTTP API only, and shows tenant and
  governance status. It has no database access and sends no tenant identifier.

## 4. What remains to complete the Data Source Manager

There is currently no `data_source`, `data_source_acl`, `connection_test`, or general job
record; no source-management capability; no repository or service; no data-source API;
no connection-test worker; no generated/committed client contract for these endpoints;
and no source-management screen.

Completion of this deliberately narrow manager requires:

- tenant-owned data-source and connection-test persistence with forced RLS;
- source-specific capabilities and default-deny resource access;
- authorized, tenant-scoped CRUD (create, read, update, and delete/deactivate under the
  accepted retention policy);
- write-only credential capture that writes to `SecretStore` and persists only its
  returned reference;
- a durable background connection-test request, execution, result, and polling path;
- safe diagnostic persistence and response projection;
- audit events and operational signals for mutations, authorization denials, queueing,
  completion, and failure;
- shared web/API contract updates and the smallest Add Source / Test Connection screen;
- real PostgreSQL, Redis/worker, browser, cross-tenant, and secret-leakage verification.

## 5. Proposed persistence and migrations

Ship every change through Alembic. Add each tenant-owned table to the repository's RLS
registry, enable and force RLS, create the standard `tenant_isolation` policy, and grant
only the constrained runtime role the required operations.

### `data_source`

| Column | Purpose and constraint |
| --- | --- |
| `id uuid` | Primary key, generated by the platform |
| `tenant_id uuid` | Non-null tenant foreign key; forced RLS key |
| `name varchar(200)` | Tenant-visible name; unique within a tenant among non-deleted sources |
| `connector_type varchar(64)` | Closed platform connector code; initially `postgresql` |
| `connectivity_mode varchar(32)` | Initially `direct`; future modes remain representable |
| `config jsonb` | Non-secret validated connector configuration only: endpoint, username, database, TLS mode, timeout |
| `secret_logical_name varchar` / `secret_version varchar` | The `SecretRef` components only; no value and no connection URI |
| `status varchar(32)` | `active`, `disabled`, or `deleted` once deletion policy is accepted |
| `version integer` | Optimistic-concurrency value used by `ETag`/`If-Match` |
| `created_by`, `updated_by` | Actor identifiers for governance |
| `created_at`, `updated_at` | Time-zone-aware timestamps |

Database checks must reject unknown connector/mode/status values, empty names, and JSON
keys that can carry obvious credential material. The application must also validate
configuration against the connector schema; database checks are a backstop, not the
only control. `secret_ref` must be reconstructable only for the row's `tenant_id`.

### `data_source_acl`

Tenant-owned resource grants implementing ADR-010 layer 2:
`id`, `tenant_id`, `data_source_id`, `subject_type`, `subject_id`, `permission`
(`view`, `edit`, `manage`), and audit timestamps. It has forced RLS and uniqueness on one
subject/source/permission tuple. Creation grants `manage` to the creator and tenant
administrators retain policy-defined administration; everyone else is default denied.

### `connection_test`

`id`, `tenant_id`, `data_source_id`, `requested_by`, `source_version`, `status`
(`queued`, `running`, `succeeded`, `failed`), `overall`, safe `checks jsonb`, `attempt`,
`trace_id`, `idempotency_key`, `queued_at`, `started_at`, `finished_at`, and a sanitized
`failure_code`. It has forced RLS and an idempotency uniqueness constraint scoped to the
tenant and operation. Stored checks use the closed diagnostic schema; raw exceptions,
resolved addresses, connection strings, usernames, hostnames in error strings, and
secret references are not result fields.

For this slice, the connection-test row is the pollable job resource. A general-purpose
pipeline model should not be invented until another pipeline kind needs it; the row still
implements ADR-009's required durable state and typed envelope. The outbox carries only
identifiers and safe execution metadata, never source configuration or a secret reference.

## 6. Proposed API

All routes resolve tenant context before data access, apply capability and resource checks,
accept no tenant identifier, use RFC 9457 errors, and return the same 404 for absent and
unauthorized object identifiers.

| Route | Authorization and behaviour |
| --- | --- |
| `POST /v1/data-sources` | `source.create`; accepts non-secret config plus a write-only credential value, stores the value in `SecretStore`, persists only the returned reference, writes audit and outbox state atomically |
| `GET /v1/data-sources` | `source.read`; returns only sources the caller may view, never credential or secret-reference fields |
| `GET /v1/data-sources/{id}` | `source.read` plus `view`; safe projection only |
| `PATCH /v1/data-sources/{id}` | `source.update` plus `edit`; requires `If-Match`; an optional replacement credential is write-only and rotates the reference only after successful secret storage |
| `DELETE /v1/data-sources/{id}` | `source.delete` plus `manage`; immediately disables, retains the audit tombstone, and schedules credential destruction after 30 days |
| `POST /v1/data-sources/{id}/test` | `source.test` plus `view`; returns `202` with `{job_id,status,poll}` and commits job plus outbox atomically |
| `GET /v1/connection-tests/{id}` | Same source visibility; returns job state and safe ordered diagnostics |

Create/update input types must keep `credential` separate from `config`, mark it
write-only in OpenAPI, and prevent response-model reuse. Responses may say
`credential_configured: true`; they must not expose the credential, `SecretRef`, endpoint
credentials, or internal driver detail. Idempotency keys apply to create and test.

## 7. Authorization rules

Add closed capabilities: `source.read`, `source.create`, `source.update`,
`source.delete`, `source.test`, and `source.acl.manage`.

- `tenant_admin`: every source capability and policy-level administration of all tenant
  sources.
- `data_steward`: read, create, update, and test; manage sources it creates or is granted;
  no delete capability by default. Deletion remains a tenant-administrator action under
  least privilege.
- `executive` and `viewer`: no source capability by default.
- `platform_admin`: no standing tenant-source access. Existing explicit, reason-logged,
  time-bounded elevation rules apply.
- A capability is necessary but not sufficient for an existing resource: the resource ACL
  must also grant the action, except for the tenant-administrator policy above.
- The worker uses a system actor inside the source's tenant context. It receives no
  cross-tenant credential and cannot construct work from an unscoped payload.

Capability seeds and the Python capability mapping must change together and be checked
against each other in tests.

## 8. Background connection-test execution

1. The API validates authorization and source state, creates a queued `connection_test`,
   and adds `connection_test.requested` to the outbox in the same transaction.
2. The relay publishes a typed message containing only job id, tenant id, source id,
   source version, actor id/type, trace id, idempotency key, and attempt.
3. The worker rejects malformed or tenant-less work, opens a tenant-scoped transaction,
   verifies the source and pinned version, and marks the job running idempotently.
4. Only then does the worker load the non-secret source configuration and tenant-bound
   secret reference, build the existing `PostgreSQLConnector`, and run its diagnostics.
5. The worker stores only the connector's safe ordered result, marks the job complete,
   and records audit/observability evidence. Retried delivery must not create a second
   result or re-run a completed job.
6. A replaced or deleted source, mismatched source version, cross-tenant secret reference,
   or unavailable secret fails closed with a safe code. No fallback credential exists.

Use the interactive queue and impose a per-tenant concurrency limit. The existing worker
currently has only a relay loop; adding the actual Dramatiq actor, idempotent state
transition, and dispatch is part of this scope.

## 9. Frontend boundary and smallest usable experience

Add one authenticated data-sources area with:

- a list showing name, type, status, and last safe test outcome;
- an **Add PostgreSQL source** form whose non-secret fields come from the connector
  configuration schema;
- a separate password input that is write-only, never prefilled, and cleared after submit;
- a **Test connection** action with queued/running feedback and polling;
- a diagnostics panel showing the six checks, pass/fail/skipped, safe remediation, and a
  correlation identifier for support.

The browser continues to use the versioned API through shared contracts. It sends no
tenant id, stores no credential in browser persistence, never displays a `SecretRef`, and
does not call the database or connector directly. Update/edit and delete UI may follow the
API, but the minimum acceptance walk is Add Source → Test Connection.

## 10. Audit, observability, decisions, and conflicts

### Required audit events

Add closed actions for `data_source.created`, `data_source.updated`,
`data_source.credential_rotated`, `data_source.deleted` (only after approval),
`data_source.access_denied`, `connection_test.requested`, and
`connection_test.completed`. Details contain identifiers, connector type, mode, changed
field names, outcome, safe diagnostic codes and durations, but no config values, endpoint,
username, secret reference, credential, or raw exception.

Operational logs and traces carry tenant id, source id, job id, trace id, operation,
outcome, duration, and safe codes. Metrics cover queue delay, execution duration,
outcomes, retries, and per-tenant fairness without high-cardinality or secret-bearing
labels. Central redaction remains defense in depth; every call site must still emit an
allowlisted shape.

### Accepted product-owner decisions

1. **Delete and tombstone.** Deletion disables the source immediately and retains an
   audit tombstone. New jobs are refused and already-running work fences on source
   version.
2. **Credential recovery.** The disabled source's credential has a 30-day recovery period
   before destruction. Destruction must be idempotent, auditable, and safe in the presence
   of retries.
3. **Connection-test retention.** Retain audit-safe connection-test history for 90 days
   and show the latest result in the Data Source Manager.
4. **Production secrets.** A production `SecretStore` adapter remains mandatory before
   production deployment, but its selection and implementation are excluded from this
   development slice.

### Conditions, not architectural conflicts

- **G14 remains open:** no production `SecretStore` adapter exists. Local/CI
  implementation and verification can proceed; production deployment cannot.
- The roadmap text below its correction still lists a separate file-connector Phase 3 and
  says the repository is documentation-only. The correction at the top and the actual
  repository state supersede those stale lines; no ADR conflict exists.
- ADR-009 describes general `PipelineRun`/`PipelineStep` records. Using the purpose-built
  `connection_test` row as the first durable job is compatible because it preserves the
  mandated state, tenant envelope, outbox, idempotency, and observability without building
  extraction machinery. Generalize when a second pipeline kind arrives.
- The accepted architecture requires resource ACLs; omitting them in favour of roles alone
  would be an ADR-010 conflict. They are therefore included in the persistence stage.

## 11. Four small implementation stages

### Stage 1 — Tenant-owned source persistence and authorization

Add migrations, `DataSource`/ACL models and repository/service boundaries, capability
seeds, forced RLS, safe projections, create/list/read/update API contracts, and audit
events. Integrate write-only credential storage and rotation with compensating cleanup if
the database transaction fails. Do not add delete yet.

**Acceptance:** two tenants cannot observe or mutate each other's sources through API,
repository, guessed identifiers, ACLs, or forged tenant inputs; configuration and every
observable surface contain no credential; a cross-tenant `SecretRef` is rejected; RLS
schema inspection passes against real PostgreSQL.

### Stage 2 — Durable background connection testing

Add the connection-test migration/model, job envelope, request and polling endpoints,
outbox topic, Dramatiq dispatch/actor, tenant-scoped idempotent state transitions, existing
PostgreSQL diagnostic invocation, safe result persistence, audit, logs, traces, and queue
fairness.

**Acceptance:** real PostgreSQL and Redis execute the job outside the request; wrong
password differs safely from unreachable host; duplicate delivery is idempotent; missing
tenant, wrong tenant, stale source version, deleted/disabled source, and cross-tenant
secret reference fail closed; rollback produces no dispatched work.

### Stage 3 — Small Add Source / Test Connection experience

Update committed OpenAPI/shared contracts and add the source list, generic PostgreSQL
configuration form, write-only password control, test action, job polling, and diagnostic
panel. Keep the experience within the authenticated web shell.

**Acceptance:** a data steward can add a PostgreSQL source and see a real completed test;
the browser sends no tenant identifier, never stores or renders credential/reference
material, distinguishes authentication from network failure, and handles denial/not-found
without revealing object existence.

### Stage 4 — Adversarial closeout and approved deletion semantics

Implement DELETE using the accepted immediate-disable, audit-tombstone, and 30-day
credential-recovery policy. Add the full security and browser closeout suites, 90-day
connection-test retention, documentation, and release evidence.

**Acceptance:** all criteria in section 12 pass against real infrastructure, no test is
skipped, each earlier stage is independently green, and the repository is clean.

## 12. Complete acceptance criteria

### Functional

- An authorized tenant administrator or steward creates a PostgreSQL data source and
  lists/reads/updates it within the accepted permission model.
- Metadata stores only non-secret configuration and a tenant-bound secret reference.
- A connection test returns `202`, executes in the background, and can be polled to a safe
  six-check result.
- Correct credentials succeed against real PostgreSQL. Wrong credentials and unreachable
  endpoints fail at distinguishable, correct stages without unsafe detail.
- The smallest browser walk Add Source → Test Connection completes against the real API,
  worker, Redis, metadata PostgreSQL, and source PostgreSQL.

### Security and isolation

- Every new tenant-owned table has non-null `tenant_id`, enabled and forced RLS, the
  expected policy, constrained grants, and coverage in the live schema-policy test.
- Tenant B receives the same 404 for Tenant A's source/job identifiers as for random ones,
  and cannot reach them through API, repository, ACL manipulation, worker payload, or
  direct SQL as the application role.
- A job without tenant id is refused. A payload whose tenant, source, job, or secret
  reference disagree is refused before network or secret access.
- Adversarial secret tests inject unique sentinel credentials and assert the sentinel is
  absent from database JSON/text columns, API/OpenAPI responses, audit rows, outbox rows,
  job payloads/results, logs, traces, metrics, browser HTML, JavaScript state, exception
  messages, and test artifacts.
- The worker requests the credential only for `test_connection`; no API route reveals it,
  and no environment-variable fallback exists.
- Unsafe-network and address-substitution tests remain green.

### Background correctness

- Data-source change, audit event, job row, and outbox request commit or roll back as the
  documented transaction boundaries require.
- Duplicate delivery, worker restart, and retry cannot create conflicting final results.
- A source update/credential rotation during a queued test is fenced by `source_version`;
  the stale job cannot overwrite the newer source's last result.
- Per-tenant limits ensure one tenant cannot starve another tenant's interactive test.

### Quality and delivery

- Ruff format/lint, strict mypy for API and worker, TypeScript typecheck/lint/tests,
  architecture tests, all unit/integration/security suites, connector conformance, real
  PostgreSQL diagnostics, real Redis/worker tests, and Playwright acceptance tests pass
  with no skips.
- OpenAPI and shared client contracts regenerate with no uncommitted difference.
- Secret/log scans and the complete existing Phase 1 security suites remain green.
- Each stage is separately committed and pushed; its GitHub Actions run is green and the
  repository is clean before the next stage begins.

## 13. Explicit exclusions

This plan does **not** include source discovery, schema browsing, field profiling,
sampling, extraction, ingestion, scheduling, watermarks, object-storage landing, data
loading, semantic entities or mapping, configuration bundles, governed metrics, queries,
dashboards, insights, alerts, lineage, AI, additional connector types, customer-network
agents, PrivateLink, VPN, or production secret-manager selection/implementation.

No excluded capability may be pulled into a stage as “helpful preparation.”

## 14. Exact implementation handshakes

Use the following sequence for every stage; never overlap stages.

1. **Assignment — Codex:** write `automation/stages/<nn>_<name>.md` with governing
   references, allowed files, acceptance criteria, real-infrastructure commands,
   exclusions, and stop conditions. Update `automation/status.md` to `ASSIGNED`.
2. **Implementation — Claude:** receive only the stage assignment and the smallest
   relevant code context. Implement the stage and return a file list, decisions made,
   tests run, and remaining risks. Claude does not approve its own work.
3. **Independent review — Codex:** inspect the complete repository diff and trace every
   acceptance and security claim to code and tests. Check architecture boundaries,
   tenant/authorization flow, transactions, secret surfaces, job idempotency, and scope.
4. **One repair — Claude or Codex:** if findings are bounded and introduce no product
   decision or ADR conflict, issue one compact repair assignment. After repair, Codex
   repeats the complete review. A second unresolved implementation failure stops the
   workflow.
5. **Verification — Codex:** run Ruff, strict mypy, architecture/unit/integration/security
   suites, contract checks, and the stage's real PostgreSQL/Redis/browser tests in the
   documented Docker paths. Skips do not count. Record exact commands, counts, and results
   in `automation/results/<nn>_<name>.md` and update `automation/status.md`.
6. **Commit — Codex:** confirm only intended files changed, commit the single stage with a
   focused message, and record the full commit hash. Never combine stages.
7. **Push and CI — Codex/CI:** push the stage, link its GitHub Actions run, wait for every
   required job to pass, and investigate any failure. CI success on another commit does
   not count.
8. **Clean handoff — Codex:** confirm `git status --short` is empty, mark the stage
   `PASSED`, and only then assign the next stage. Stop immediately for an unresolved
   product choice, ADR conflict, permission denial, failed real verification, failed CI,
   or unexpected user change.

The Phase 2 Data Source Manager is complete only after all accepted stages finish this
handshake. This report records the product owner's authorization; implementation starts
only through the staged handshake above.
