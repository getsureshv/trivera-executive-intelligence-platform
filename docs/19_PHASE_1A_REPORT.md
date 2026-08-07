# 19 — Phase 1A Completion Report (Platform Skeleton)

Date: 2026-08-07
Commit: `d766783` — *feat: Phase 1A platform skeleton with enforced tenant isolation*
Preceded by: [`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md)
Governed by: [ADR-001](adr/ADR-001-repository-architecture.md) … [ADR-015](adr/ADR-015-secrets-management.md)

---

## Executive Summary

Phase 1A delivers the platform foundation and **nothing else**. There is no
business-intelligence functionality in the repository: no connectors, no schema
discovery, no profiling, no semantic model, no field mappings, no metric engine, no
dashboards, no lineage, no insight engine, and no AI integration. That absence is the
plan, not an omission.

**All ten Phase 1A objectives are met and verified by execution**, not by inspection.
The decisive objective — that Tenant A cannot reach Tenant B's data — is proven at three
independent layers and demonstrated against a live stack with four distinct attack
vectors.

Two findings qualify the result and are stated plainly rather than buried:

1. **The CI pipeline has never executed.** It is authored and committed, but the two
   commits on `main` are unpushed and no workflow run exists. Everything CI would check
   was run locally; the pipeline itself is unproven.
2. **The web application has zero tests.** `pnpm -r test` passes vacuously (0 tests, 0
   failures). Frontend correctness currently rests on `tsc --noEmit`, ESLint, a
   successful production build, and manual verification against the running API.

Neither blocks Phase 1B. Both are named in *Known Gaps* with a recommended owner phase.

---

## Objectives vs. Outcome

| # | Objective | Status | Evidence |
| --- | --- | --- | --- |
| 1 | The application runs locally | **Met** | Full stack from an empty volume; every healthcheck green |
| 2 | Frontend and backend communicate | **Met** | `/app` renders live tenant, capability, and readiness data from the API |
| 3 | PostgreSQL persistence works | **Met** | 8 tables migrated; tenants, users, memberships, audit rows persisted |
| 4 | Tenant context is enforced | **Met** | Resolved from verified membership; typed argument required by the data-access layer |
| 5 | Tenant A cannot access Tenant B | **Met** | 45 security tests + 4 live attack vectors, all refused |
| 6 | AuthN/AuthZ boundaries exist | **Met** | Delegated OIDC-shaped auth, no password storage; capability model |
| 7 | Database migrations work | **Met** | Alembic; runtime role has no DDL rights at all |
| 8 | Audit events can be recorded | **Met** | Append-only, hash-chained per tenant, transactionally consistent |
| 9 | Health/readiness information | **Met** | Liveness and readiness split with distinct semantics |
| 10 | Automated verification exists | **Met (with gap)** | 116 tests passing; CI authored but never executed |

---

## What Was Built

### Size

| Area | Files | Lines |
| --- | --- | --- |
| Python — application source | 51 (`apps/api`, `apps/worker`) | 4,482 |
| Python — tests | — | 1,614 |
| Python — migrations | 1 | 353 |
| TypeScript / React | 21 | 1,153 |
| Infrastructure + CI | 4 | — |
| **Total committed** | **97 files** | **9,783 insertions** |

The test-to-source ratio on the backend is roughly 1:2.8, which is appropriate for a
phase whose entire product is a security property.

### Backend — `apps/api` (ADR-001, ADR-002)

FastAPI modular monolith, package-by-context:

| Package | Responsibility |
| --- | --- |
| `eip.platform` | Tenancy, settings, errors, structured logging, telemetry, DB engines, ports |
| `eip.identity` | Principals, tenants, memberships, roles, token verification, context resolution |
| `eip.governance` | Audit trail (hash chain), transactional outbox |
| `eip.dataplane` | `TenantDataPlane` port + the approved implementation |
| `eip.api` | HTTP routers — the only package permitted to import FastAPI |
| `eip.scripts` | Operational entry points (local bootstrap) |

`mypy --strict` passes across all 37 modules with no suppressions beyond three
declared third-party stub overrides.

### API surface

```
GET    /health                     liveness — touches no dependency
GET    /ready                      readiness — DB, migrations, isolation self-check
GET    /v1/me                      caller identity + resolved tenant (takes no input)
GET    /v1/tenants/{tenant_id}     accepts an id specifically so manipulation is testable
GET    /v1/memberships             tenant-scoped
GET    /v1/audit-events            tenant-scoped, capability-gated
POST   /v1/admin/tenants           privileged; requires X-Elevation-Reason
POST   /v1/admin/memberships       privileged; refuses to grant platform_admin
POST   /v1/dev/token               local/ci only; triple-guarded
```

No endpoint accepts SQL, a formula, or a field path. No endpoint reads a tenant
identifier from a header, body, or subdomain.

### Control plane — 8 tables

| Table | Scope |
| --- | --- |
| `tenant`, `app_user`, `role`, `role_capability`, `alembic_version` | **Global** — enumerated and RLS-exempt by design |
| `membership`, `audit_event`, `outbox` | **Tenant-scoped** — `tenant_id` + FORCE RLS + policy |

`app_user` is global deliberately: a person may belong to several tenants, so the user
record cannot itself be tenant-scoped. Membership is the object that grants access, and
it is the one an attacker would need to forge.

### Frontend — `apps/web`

Next.js 15 / React 19 application **shell**: layout, placeholder authenticated
experience, tenant-context display, API status display, loading states, error boundary,
development sign-in. No dashboards, charts, KPI components, or source-configuration
screens.

The web tier holds **no database credentials in any environment**, which is what keeps
the API the only path to data.

### Worker — `apps/worker` (ADR-009)

Transactional outbox relay, broker connectivity check, health/readiness server. No
ingestion pipelines. The relay runs on the **constrained** role and processes each tenant
inside a proper tenant-scoped session.

---

## Tenant Isolation — the Central Result

### How it is implemented

**Three PostgreSQL roles, deliberately distinct:**

| Role | `rolsuper` | `rolbypassrls` | Owns tables | Used by |
| --- | --- | --- | --- | --- |
| `eip_app` | no | **no** | no | every request and every job |
| `eip_platform` | no | **yes** | no | audited platform-admin operations only |
| `eip_migrator` | no | no | **yes** | Alembic only |

**Two independent enforcement layers.** Application code filters by tenant *and* every
tenant-scoped table carries `ENABLE` + `FORCE ROW LEVEL SECURITY` with a policy resolving
`NULLIF(current_setting('app.tenant_id', true), '')::uuid`. The setting is applied
transaction-locally, so it cannot survive a pooled connection returning to the pool. If
application filtering is ever forgotten, the query returns **zero rows** — the comparison
against NULL is never TRUE, so the failure mode is fail-closed by construction.

**Startup refuses to proceed** unless the runtime role is genuinely constrained and every
tenant-scoped table has an enforced policy. A process that booted with isolation silently
disabled would pass every functional test while being catastrophically wrong.

**One deliberate exception, tightly scoped:** `membership_self_select` is a `FOR SELECT`
policy keyed on `app.user_id`, letting a principal read *their own* membership rows before
a tenant is known. Without it, sign-in would have to run on the BYPASSRLS role — which
would mean every login executed with row-level security disabled.

### Live acceptance test (executed against the running stack)

Two tenants (`acme-industrial`, `borealis-capital`), two users, four attack vectors:

| # | Scenario | Expected | Actual |
| --- | --- | --- | --- |
| 1 | User A → Tenant A | 200 | **200**, `acme-industrial` |
| 2 | User B → Tenant B | 200 | **200**, `borealis-capital` |
| 3 | User A requests a token scoped to Tenant B | 403 | **403** |
| 4 | User A fetches Tenant B by identifier | 404 | **404** |
| 5 | Control: a tenant that does not exist | 404 | **404, byte-identical to #4** |
| 6 | User A forges `X-Tenant-Id: <B>` | ignored | **ignored**, still `acme-industrial` |

Test #5 is the one most often skipped and matters most: returning 403 for
"exists but forbidden" and 404 for "does not exist" lets an attacker enumerate the
platform's customer list by probing identifiers. The two responses are identical in
status, `code`, `title`, and `detail`.

The frontend was separately checked with a real session cookie: User A's rendered page
contains no Borealis string of any kind, and header forgery has no effect there either.

### Defence in depth, demonstrated rather than asserted

The database-layer tests deliberately issue queries that **name the other tenant's id**
with no `WHERE tenant_id` filter of their own. Nothing but RLS prevents the read. A
negative-control test proves the `eip_platform` role genuinely *can* cross tenants —
without it, the isolation suite might be passing because the privileged path was broken
rather than because RLS works.

---

## Verification Performed

Every item below was executed, with output observed.

| Gate | Command | Result |
| --- | --- | --- |
| Python format | `ruff format --check` | **55 files already formatted** |
| Python lint | `ruff check` | **All checks passed** |
| Type safety | `mypy` (strict) | **32 + 5 modules, no issues** |
| API tests | `pytest tests` | **111 passed** |
| Worker tests | `pytest` | **5 passed** |
| TS format | `pnpm format:check` | **clean** |
| TS lint | `pnpm -r lint` | **clean** (`--max-warnings 0`) |
| TS types | `pnpm -r typecheck` | **clean** (strict + `exactOptionalPropertyTypes`) |
| Production build | `next build` | **4 routes compiled** |
| Stack startup | `compose up --build --wait` from empty volume | **all healthy** |
| API liveness/readiness | `/health`, `/ready` | **ok / ready**, all 3 checks pass |
| Worker liveness/readiness | `/health`, `/ready` | **ok / ready** |
| Secret scan | pattern scan + `.env` check | **none found** |
| Dead markers | TODO/FIXME/XXX/HACK | **zero** |

### Test breakdown — 116 total

| Suite | Count | Covers |
| --- | --- | --- |
| `tests/unit` | 47 | Secret handling, redaction, audit hash, context, data-plane naming, settings guards |
| `tests/security` | **45** | Tenant isolation (3 layers), privileged path, audit integrity, capabilities |
| `tests/integration` | 12 | Health/readiness, migration invariants, outbox scoping |
| `tests/architecture` | 7 | Bounded-context import contracts |
| `apps/worker/tests` | 5 | Background-processing isolation |

**39% of the suite is security tests.** For a phase whose deliverable is a security
property, that ratio is the point.

### Verified infrastructure state

```
Roles:        eip_app f|f   eip_migrator f|f   eip_platform f|t   (super|bypassrls)
RLS:          membership t|t|2   audit_event t|t|1   outbox t|t|1  (enabled|forced|policies)
Audit grants: eip_app → SELECT, INSERT only (no UPDATE, no DELETE)
Data plane:   2 tenant schemas; eip_app has USAGE=true, CREATE=false on each
Audit chain:  per-tenant, each starting seq 1 from genesis, links verified
```

---

## Decisions Made During Implementation

Four decisions were taken during the build that were not settled by an ADR. None
contradicts an accepted decision; each is recorded here rather than left implicit.

**1. `import-linter` replaced by a dependency-free AST test.** ADR-001 specifies
`import-linter` for bounded-context contracts. Its `grimp` backend is a compiled
extension without wheels on Windows/ARM64, and it could not be installed on the
development machine. A boundary check that cannot run on a developer's machine is a
boundary check that rots, so the contracts are enforced by a ~160-line AST test instead.
The guarantee is equivalent and the dependency is gone. *This is a deviation from ADR-001
and should be ratified or reverted.*

**2. Python services run in containers.** No PostgreSQL driver (`asyncpg`, `psycopg`) has
a prebuilt wheel for Windows/ARM64, and MSVC build tools are absent. Rather than demand a
compiler toolchain, the API and worker run in containers — which is what makes the
environment genuinely reproducible rather than "works if you have a compiler", and which
Phase 1A asked for anyway. The driver is an optional extra (`apps/api[postgres]`), so
lint, typecheck, and unit tests still run natively.

**3. `eip_platform` retains `DELETE` on `audit_event`.** The append-only guarantee is
scoped to the *runtime* role. Tenant offboarding and GDPR erasure delete the tenant row,
which cascades to its audit events, and PostgreSQL requires `DELETE` on the referencing
table for that cascade. Deletion by the privileged role remains **detectable**: the hash
chain breaks and `verify_chain` reports the sequence where it does. `TRUNCATE` is never
granted to any role.

**4. Development token issuer instead of a local IdP.** ADR-010 forbids password storage
and delegates authentication. Standing up a real IdP for local development and CI would
add a dependency for no security benefit, so the API mints tokens *in the shape it will
later verify from a real issuer*. The verification path, membership resolution, and every
downstream authorization check are identical to production — only the key source differs,
which is what makes the isolation tests meaningful. It is guarded three times.

---

## Defects Found and Fixed

Two bugs were caught by the tests, both reachable only through application startup and
both invisible to inspection:

| Defect | Impact | Fix |
| --- | --- | --- |
| `tablename NOT IN :globals` with a tuple bindparam | asyncpg binds one parameter per placeholder; the startup role assertion raised a syntax error, so **the API could not boot** | `<> ALL(:globals)` with array semantics |
| `TRUNCATE` not granted to any role | Test cleanup failed; surfaced as 50 fixture errors | Ordered `DELETE`; the underlying grant behaviour is correct and now documented |

The first is worth noting: it was in the code that *verifies isolation is enforced*. Had
that assertion been written without a test exercising real startup, it could have been
silently broken while appearing to protect the system.

---

## Known Gaps

Stated explicitly so none of them is discovered later as a surprise.

| # | Gap | Severity | Recommended owner |
| --- | --- | --- | --- |
| G1 | **CI has never executed.** Authored and committed; 2 commits unpushed; no workflow run exists. The `stack-smoke` and model-drift jobs in particular are unproven. | **High** | Immediate — push and confirm green before Phase 1B |
| G2 | **No frontend tests.** `pnpm -r test` passes with 0 tests. Correctness rests on types, lint, build, and manual checks. | Medium | Phase 1B, alongside the first real screens |
| G3 | **No end-to-end browser test.** The `tests/e2e` directory from ADR-001 does not exist; the acceptance flow was driven by `curl`. | Medium | Phase 1B |
| G4 | **`SecretStore` is a port with no adapter.** Types and interface only; no cloud implementation, because Phase 1A stores no secrets. | Low — by design | Phase 2 (first connector) |
| G5 | **OpenTelemetry is wired but never exercised.** Disabled by default; no collector has received a span. | Low | Phase 1B |
| G6 | **`packages/contracts` types are hand-written.** The `sync-openapi` script exists but `openapi.json` is not yet committed, so the CI drift check has nothing to compare against. | Medium | Phase 1B |
| G7 | **Dramatiq is a connectivity check only.** No actors, no queues, no per-tenant fairness caps yet. | Low — by design | Phase 2 |
| G8 | **`import-linter` deviation from ADR-001** (see Decisions §1) is unratified. | Low | Ratify or revert in Phase 1B |
| G9 | **Health checks in `docker-compose` use `urllib`**, so they exercise the HTTP surface but not the dependency graph the way `/ready` does. | Informational | — |

---

## Risks Carried Into Phase 1B

| Risk | Why it matters now |
| --- | --- |
| **Every new tenant-scoped table is an opportunity to forget RLS.** | The invariant is currently enforced by a test and a startup assertion. Both work; neither prevents a developer from adding a table and *also* adding it to `GLOBAL_TABLES` to make the test pass. Code review of that list is load-bearing. |
| **The `membership_self_select` policy is a permitted widening.** | It is correct and minimal today. Any future policy keyed on `app.user_id` should be treated as a security change requiring the same scrutiny. |
| **Cache does not yet exist.** | ADR-007 §4 requires `auth_scope_hash` in every cache key. There is no cache in Phase 1A, so the highest-severity defect found in Phase 0 review is *not yet possible* — and must be prevented the moment caching is introduced. |
| **Tenant provisioning is manual.** | Deliberate, per the Phase 0 answer to Q3. It becomes a bottleneck the moment a second real customer exists. |

---

## Product-Owner Items Still Open

**PO-001 through PO-005 do not exist in this repository.** Verified by `git ls-files` and
a repository-wide grep. Phase 1A proceeded under ADR-003/009/010/014/015 as the governing
authority, per the phase brief's own instruction to adapt to the accepted ADRs.

Phase 1A touches none of the areas those decisions gate, so nothing built here is at risk
if they differ. Confirmation is still needed on:

- **PO-005 / tenant data plane** — Phase 1A implements ADR-003's schema-per-tenant. If
  PO-005 selected a different mode, the `TenantDataPlane` port absorbs the change, but the
  implementation would need replacing.
- **Q1–Q4 from the Phase 0 review** remain the gate on wider Phase 1 work: bring-your-own
  warehouse, private-network connectivity, SaaS-now vs. TriVera-first, and restatement
  policy.

---

## Readiness for Phase 1B

**Ready, conditional on G1.** The foundation holds: isolation is enforced and proven,
the module boundaries are mechanically checked, migrations are reversible and
drift-checked, and the audit trail is tamper-evident. Nothing in Phase 1B needs to
revisit these.

The single thing that should happen before new feature work: **push the branch and
confirm CI is green.** A pipeline that has never run is a pipeline that does not work
yet, and every guarantee in this report currently depends on a local machine having been
used correctly.

Recommended Phase 1B opening scope, in order:

1. Get CI green (G1); commit `openapi.json` and enable the drift check (G6).
2. Add the frontend test harness and the first end-to-end browser test (G2, G3).
3. Ratify or revert the `import-linter` deviation (G8).
4. Begin the Data Source Manager — ADR-004's connector framework with the PostgreSQL
   connector, per the Phase 0 resequencing that put a typed relational source before
   file connectors.
