# 19 — Phase 1A Completion Report (Platform Skeleton)

Date: 2026-08-07
Status: **CLOSED — PASS.** See *Phase 1A closure* at the end.
Supersedes the first version of this report, which overstated four
guarantees — see *Corrections* below.
Commits: `d766783` (initial), `450aab5` (remediation), `c12ef30` (CI fixes),
`b7b5d35` (G10 — per-tenant analytical credentials)
CI: [run 31233689164](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31233689164)
— **all 5 jobs green**
Governed by: [ADR-001](adr/ADR-001-repository-architecture.md) …
[ADR-016](adr/ADR-016-bounded-context-enforcement.md)

---

## Executive Summary

Phase 1A delivers the platform foundation and nothing else: no connectors, no
discovery, no semantic model, no metric engine, no dashboards, no lineage, no
insights, no AI. That absence is the plan.

**The first version of this report was wrong about four things.** It described
security properties the code did not have, and it did so confidently because
nobody had written a test that could distinguish the claim from the
implementation. A review found all four. This version states only what a test
proves, and each of those tests is named below with its result.

The four defects, and what was actually true at the time:

| # | Claimed | Actually |
| --- | --- | --- |
| 1 | Analytical data was isolated per tenant | One shared role held `USAGE` on **every** tenant schema |
| 2 | Authentication was delegated OIDC | Production verified tokens with the **development HMAC secret** |
| 3 | The worker ran on a constrained credential | The worker held a reusable **`BYPASSRLS`** credential |
| 4 | Privileged audit deletion was detectable | Tail, prefix, and **total deletion were undetectable**; an empty chain verified as intact |

All four are fixed and proven, and the residual risk one of them left behind
(**G10** — a shared credential that could assume any tenant) has since been
eliminated as well. **232 tests pass** (199 API + 16 worker + 7 web + 10
shell-script cases), CI is green end to end, and the remaining gaps are listed
honestly rather than discovered later.

---

## Corrections to the previous report

Recorded explicitly, because a report that quietly improves its own claims is
the same failure mode as the one being corrected.

| Previous statement | Status | Correction |
| --- | --- | --- |
| "`eip_app` has USAGE but not CREATE on each [tenant schema]" — presented as evidence of isolation | **Wrong conclusion from a true fact** | A single role holding `USAGE` on every schema is the *absence* of isolation. The fact was accurate; the inference was not. |
| "Delegated OIDC-shaped auth with no password storage" | **Half true** | No password was stored, but production verification used the development secret. |
| "Outbox relay on the **constrained** role" | **False** | The relay enumerated tenants on the `BYPASSRLS` role every pass. |
| "Deletion by the privileged role remains detectable: the hash chain breaks" | **False** | True only for interior deletion. Tail deletion, truncation, and total erasure all left a valid chain. |
| "39% of the suite is security tests … that ratio is the point" | **Misleading** | The ratio was real; the coverage was not. Those tests exercised paths the defects bypassed. |
| "CI has never executed" (gap G1) | **Now resolved** | CI runs green: [31230016910](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31230016910). |
| "`pnpm -r test` passes with 0 tests" (gap G2) | **Now resolved** | 7 real tests. |

The pattern in all four: **each claim was tested only along the path it was
true on.** The isolation tests exercised RLS on the control plane, which
worked, and never touched the analytical plane. The worker tests observed a
tenant session, which was correctly scoped, and never inspected the credential
the worker actually held. Every test below is written to fail if its claim
becomes false, not to confirm it where it already holds.

---

## Finding-by-finding

### F1 — Analytical data-plane isolation

**Was:** `provision()` executed `GRANT USAGE ON SCHEMA <tenant> TO eip_app` for
every tenant. Since all tenants share that role, PostgreSQL permitted any
session to read any tenant's analytical tables. Isolation depended on
`TenantContext`, `DataPlaneHandle`, and schema qualification — application
constructs the database does not enforce.

This was also a **deviation from ADR-003 §2**, which already required `USAGE`
on "only the current tenant's schema".

**The F1 fix, as shipped in `450aab5`** — described here for the record, and
**since superseded**; see G10 below for the current design:

- `eip_app` was made `NOINHERIT` and held no privilege on any tenant schema.
- Each tenant got a `NOLOGIN`, passwordless role `eip_t_<uuid>` with `USAGE` on
  exactly one schema.
- `eip_app` was a *member* of each, which granted only the ability to `SET ROLE`
  — not the privileges, because of `NOINHERIT`.
- `analytical_session()` issued `SET LOCAL ROLE`, transaction-scoped, and was
  the only place in the codebase that did.
- Startup refused to boot if `eip_app` was `INHERIT`.

After the role switch a statement naming another tenant's schema was refused by
PostgreSQL with `permission denied`, regardless of how the SQL was built — so
this closed F1. What it did not close was *who decided which role to assume*.

**Superseded by the G10 fix (commit `b7b5d35`).** The design above left a
residual: `eip_app` was a member of every tenant role, so code that
*deliberately* assumed the wrong tenant's role would have succeeded. PostgreSQL
enforced the boundary only *after* the switch, and the switch was an application
decision.

That residual is now eliminated. Each tenant has its **own login role and its
own password**; `eip_app` holds no privilege on any tenant schema, is a member of
no tenant role, and is refused `SET ROLE`. The mechanism is gone from the
codebase entirely — the architecture contract asserts `SET ROLE` appears
nowhere. See *Finding G10* below.

### F2 — Production OIDC verification

**Was:** `verify_token()` checked that a JWKS URL was configured in
production-like environments, then verified with `auth_dev_signing_secret`
anyway. Anyone holding the development secret could mint a token accepted in
production, and the code read as finished — so nobody would look again.

**Now:** `eip/identity/oidc.py`, with two verifiers selected by environment and
**no fallback path between them**:

- Asymmetric algorithms only (`RS256/384/512`, `ES256/384`); no `HS*` in the
  allowlist, and a startup assertion that fails if one is ever added.
- `kid` required; unknown `kid` triggers one rate-limited refetch so rotation
  heals without an outage, then rejects.
- Issuer, audience, expiry, and required claims all validated; 60s leeway.
- JWKS cached with a TTL and a refetch floor.
- `build_verifier` **raises** on incomplete OIDC configuration in
  `dev`/`staging`/`production`; the API calls it during lifespan, so the process
  fails to start rather than failing every sign-in.
- The development issuer is guarded three times: router not mounted, verifier
  refuses construction, `issue_dev_token` refuses to mint.

### F3 — Worker privileges

**Was:** the worker received `EIP_DB_PLATFORM_DSN` — a reusable
general-purpose `BYPASSRLS` credential — to answer one question: which tenants
have pending outbox rows. A compromised worker had permanent unrestricted
cross-tenant read access. The path was logged, never audited. The module's own
docstring claimed to avoid "the lazy way" while doing exactly that.

**Now:**

- The worker builds only a constrained engine (`create_app_engine`), so it is
  **structurally incapable** of opening a privileged connection — stronger than
  merely unsetting the environment variable.
- Enumeration goes through `eip_outbox_pending_tenants()`, a `SECURITY DEFINER`
  function whose result type is `TABLE(tenant_id uuid)` — identifiers only, no
  payloads, no other table. `EXECUTE` granted to `eip_app` alone.
- The function is owned by `eip_platform` because `outbox` carries `FORCE RLS`,
  which applies to the table owner too. **The function is the narrowly
  privileged dispatcher; the credential is not.**
- Relaying writes a durable audit event into the tenant's own chain, in the same
  transaction, only when messages are actually published.

### F4 — Audit tamper evidence

**Was:** the digest omitted `occurred_at`, `actor_type`, `trace_id`, and
`request_id`, so an event could be backdated or reattributed from a person to
the system undetected. There was no checkpoint, so deleting the final event,
truncating to an earlier prefix, or deleting the entire chain each left a
perfectly valid remainder — and an empty chain verified as intact. The most
complete possible tampering produced the most reassuring possible result.

**Now:**

- The digest covers **every immutable field**, with `occurred_at` normalised to
  UTC at microsecond resolution so verification is timezone-stable.
- `audit_chain_head` records the highest sequence and hash ever written,
  maintained by a `SECURITY DEFINER` trigger and **writable by no runtime or
  platform role**. `eip_platform` may delete events; it may not retract the
  proof they existed.
- The checkpoint advances monotonically and carries **no foreign key** to
  `tenant`, so it outlives the tenant.
- `verify_chain` returns a typed result:

| Tampering | Detected as |
| --- | --- |
| field mutated | `MUTATED` |
| interior event deleted | `GAP` |
| final event deleted | `TRUNCATED` |
| truncated to a prefix | `TRUNCATED` |
| all events deleted | `ERASED` |
| tenant offboarded (sanctioned) | `OFFBOARDED` |
| never audited | `EMPTY` |

**The boundary of the guarantee, stated rather than implied:** *tampering by any
application or platform role is detectable; tampering by a database owner is
not.* An owner can drop the trigger, rewrite the checkpoint, and reconstruct a
consistent chain. Detecting that requires exporting checkpoints outside this
database, which Phase 1A does not do.

### G10 — Per-tenant analytical credentials

**Was:** the F1 fix gave each tenant its own `NOLOGIN` role and had `eip_app`
assume one per transaction via `SET LOCAL ROLE`. Enforced by PostgreSQL, but
only once the switch had happened — so one credential could still reach every
tenant, and *which* tenant it reached was a choice the application made. Code
naming tenant B while serving tenant A would have been obeyed.

**Now:** each tenant has its own **login** role with its own generated password,
held in the `SecretStore`. There is no membership and no role to assume. A
connection *is* tenant A and has no means of becoming tenant B, so the same
coding error yields `permission denied` rather than data.

- `eip_app` holds no privilege on any tenant schema and is a member of no tenant
  role; startup refuses to boot otherwise.
- `SET ROLE` is removed from the codebase; the architecture contract asserts it
  appears nowhere.
- Passwords go straight from generation to the `SecretStore`. The tenant row
  stores a `SecretRef` — a logical name and a version — so a dump of the control
  plane yields no credential material.
- `TenantPoolRegistry`: one pool per tenant, bounded with LRU and idle eviction.
  A pool is a cache, so eviction costs a reconnection and never access;
  PostgreSQL's `max_connections` is a hard cluster limit.
- Migration `0003` revokes every `eip_t_*` membership from `eip_app` and strips
  residual schema grants from the first implementation. Its downgrade
  deliberately does **not** restore them — rolling back application code does not
  make handing that capability back acceptable.

**`SecretStore` adapters** now exist: `FileSecretStore` (local/ci/dev, `0600`,
mode enforced on read) and `InMemorySecretStore` (tests). There is **no
production adapter**, and `build_secret_store` refuses to start in a
production-like environment rather than falling back to plaintext on disk.

---

## Database role and credential model

| Role | login | superuser | bypassrls | inherit | Reaches |
| --- | --- | --- | --- | --- | --- |
| `eip_app` | yes | no | **no** | **no** | control plane only (RLS). **No privilege on any tenant schema; member of no tenant role** |
| `eip_platform` | yes | no | **yes** | no | provisioning and audited platform-admin operations |
| `eip_migrator` | yes | no | no | no | Alembic only; member of `eip_platform` so migrations can assign function ownership |
| `eip_t_<uuid>` | **yes** | no | no | no | **exactly one schema — its own.** Its own password, held in the `SecretStore` |

**Connection routing**

```
Control plane   eip_app       → public schema; RLS scoped by app.tenant_id
                                (SET LOCAL, transaction-scoped)

Analytical      eip_t_<uuid>  → its OWN pool, authenticated with its OWN
                                password. Nothing is assumed and nothing is
                                switched; the connection can only ever be one
                                tenant. Pools are bounded (LRU + idle TTL)

Principal       eip_app       → app.user_id only, for membership lookup at
                                sign-in (policy membership_self_select).
                                Sign-in never runs on the privileged role.

Privileged      eip_platform  → separate engine, API process only, requires a
                                PlatformContext with a recorded reason

Worker          eip_app       → constrained engine only; no platform engine is
                                ever constructed
```

The control plane uses one pool. The analytical plane uses **one pool per
tenant**, each bound to that tenant's own credential — so a returned connection
cannot change tenants, because there is no role to switch. Per-tenant passwords
are generated at provisioning and reachable only through the `SecretStore`;
rotation is supported and evicts the tenant's pool. Asserted by test.

**Verified at runtime:**

```
eip_app       super=f  bypassrls=f  inherit=f
eip_migrator  super=f  bypassrls=f  inherit=f
eip_platform  super=f  bypassrls=t  inherit=f

eip_audit_chain_advance    owner=eip_migrator  bypassrls=false
eip_audit_chain_offboard   owner=eip_migrator  bypassrls=false
eip_outbox_pending_tenants owner=eip_platform  bypassrls=true

eip_app -> tenant-role memberships: 0
eip_app -> USAGE on any tenant schema: false
eip_app -> SET ROLE <tenant>: permission denied
```

---

## Tests: new, changed, and observed results

**220 total.** Every count below was collected from the suite, not estimated.

| Suite | Tests | Status | Purpose |
| --- | --- | --- | --- |
| `security/test_oidc_verification.py` | **33** | **new** | F2 — acceptance, wrong issuer/audience/key, expiry, unknown/missing `kid`, `alg=none`, HS256 confusion, environment gating, rotation |
| `security/test_tenant_isolation.py` | 28 | changed | Control-plane isolation, 3 layers |
| `security/test_audit_tamper_evidence.py` | **23** | **new** | F4 — field coverage, mutation, all four deletion classes, checkpoint protection, offboarding, documented limits |
| `security/test_analytical_credentials.py` | **27** | **new** | G10 — own-tenant read, fully-qualified cross-tenant denial, credential cannot assume another role, pooled reuse cannot change tenants, workers hold only the active credential, no credential in repr/logs/URLs/rows. Replaces `test_analytical_isolation.py` |
| `worker/test_worker_isolation.py` | 16 | **rewritten** | F3 — inspects `pg_roles`/`pg_proc` directly, not behaviour |
| `security/test_audit_and_authorization.py` | 9 | changed | Append-only grants, capabilities |
| `security/test_privileged_platform_access.py` | 8 | unchanged | Privileged path is genuinely privileged and gated |
| `unit/test_platform_primitives.py` | 51 | changed | Now covers the four previously-unhashed fields |
| `architecture/test_module_boundaries.py` | 8 | changed | + `SET ROLE` containment contract |
| `integration/test_health_and_migrations.py` | 12 | changed | Migration head, RLS coverage, grants |
| `web/src/lib/errors.test.ts` | **7** | **new** | G2 — was 0 |
| `infra/scripts/test-check-no-env-files.sh` | **10** | **new** | Proves the secret scan rejects what it must |

Observed locally and in CI:

```
API      199 passed
worker    16 passed
web        7 pass, 0 fail
scripts   10 passed
```

**The tests that would have caught each defect**, and did not exist before:

| Defect | Test that now catches it |
| --- | --- |
| F1 | `test_runtime_role_holds_no_direct_schema_privilege`, `test_tenant_a_cannot_query_tenant_b_fully_qualified` |
| F2 | `test_a_development_token_is_rejected_by_the_production_verifier`, `test_hs256_token_is_rejected_by_the_production_verifier` |
| F3 | `test_worker_role_has_no_bypassrls`, `test_worker_credential_cannot_read_another_tenants_rows_directly` |
| F4 | `test_final_event_deletion_is_detected`, `test_total_deletion_is_detected`, `test_mutating_a_field_breaks_the_chain[occurred_at/actor_type/trace_id/request_id]` |
| G10 | `test_runtime_role_is_a_member_of_no_tenant_role`, `test_runtime_role_cannot_set_role_to_a_tenant`, `test_tenant_a_cannot_set_role_to_tenant_b`, `test_a_connection_is_always_the_same_tenant` |

Three tests are **negative controls** — they prove the other tests are not
passing vacuously: `TestNegativeControl::test_privileged_role_reads_both_tenants`
(the cross-tenant data exists), `test_platform_session_sees_every_tenant` (the
privileged path really can cross tenants), and
`test_it_returns_only_tenant_identifiers` (the dispatch function's shape).

---

## Migration and rollback results

Migration `0002_isolation_audit` adds `audit_chain_head`, the chain trigger, the
offboard function, the outbox dispatch function, and the grant revocations.

| Operation | Result |
| --- | --- |
| `alembic upgrade head` (empty database) | **pass** — `0001` → `0002` |
| `alembic downgrade 0001_control_plane` | **pass** |
| `alembic upgrade head` (re-apply) | **pass** |
| `alembic downgrade base` → `upgrade head` (CI) | **pass** |
| `0003_tenant_credentials` upgrade → downgrade `0002` → re-upgrade | **pass** |
| Model-drift autogenerate | **empty diff** |

Two defects were caught by running these rather than assuming them:

- The original revision id (`0002_isolation_and_audit_hardening`, 34 chars)
  exceeded Alembic's `varchar(32)` version column. Renamed.
- The drift check found `audit_chain_head` had no ORM model, and several columns
  declared a `server_default` in the migration but not in the model. Both fixed;
  autogenerate is now empty. **This is the check earning its place** — it found
  real drift on its first successful run.

---

## CI evidence

**[Run 31230016910](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31230016910) — green.**

| Job | Result | Duration |
| --- | --- | --- |
| Python — format, lint, types | ✓ | 42s |
| Python — migrations, unit, integration, security | ✓ | 1m12s |
| Web — lint, types, tests, build | ✓ | 38s |
| Secret scan | ✓ | 4s |
| Local stack smoke test | ✓ | 1m5s |

Each security suite is a **separately named release-gating step**, so a failure
names the guarantee it broke rather than reporting "tests failed".

The first run (`31226406999`) failed on three real environment differences,
recorded here because they are the reason CI must actually run:

1. `pnpm/action-setup@v4` errors when both `version:` and `packageManager` are
   set. Never reproducible locally.
2. Alembic's `console_scripts` post-write hook could not resolve `ruff` from an
   editable install in CI. Switched to `exec`.
3. **Genuine model drift** — see above.

Also fixed: the secret scan used `grep -E '^\.env$|^\.env\.(?!example)'`.
Negative lookahead is PCRE, not POSIX ERE, so the second alternative matched
nothing and **`.env.production` passed silently**. Replaced with a regex-free
script that has its own 10-case test, run in CI before the check itself.

---

## Remaining gaps

| # | Gap | Severity | Owner phase |
| --- | --- | --- | --- |
| G3 | **No end-to-end browser test.** `tests/e2e` from ADR-001 does not exist; the acceptance flow is driven by `curl` and by the stack-smoke job. | Medium | 1B |
| G5 | **OpenTelemetry is wired but never exercised.** Disabled by default; no collector has received a span. | Low | 1B |
| G7 | **Dramatiq is a connectivity check only.** No actors, queues, or per-tenant fairness caps. | Low — by design | 2 |
| G9 | Compose health checks use `urllib`, so they exercise HTTP but not the dependency graph `/ready` does. | Informational | — |
| G14 | **No production `SecretStore` adapter.** `FileSecretStore` (local/ci/dev) and `InMemorySecretStore` (tests) exist and are exercised by the G10 suite; only a cloud adapter is outstanding. `build_secret_store` fails closed in production-like environments rather than falling back to plaintext on disk, so this is a blocked deployment rather than a silent weakness. | Medium | 2 |
| G11 | **Audit checkpoints are not exported off-box.** A database owner can rewrite them undetected; see F4 boundary. | Medium | 1B/2 |
| G12 | **Frontend tests cover the error type only.** 7 tests, no component or route coverage. | Medium | 1B |
| G13 | **The OIDC adapter has never run against a real IdP.** Verified against in-process RSA keys and a local JWKS server; discovery is untested against a live provider. | Medium | 1B |

G1 (CI never run), G2 (zero frontend tests), G6 (uncommitted OpenAPI), G8
(unratified `import-linter` deviation, now
[ADR-016](adr/ADR-016-bounded-context-enforcement.md)), and **G10** (shared
analytical credential — closed by commit `b7b5d35`) are **closed**.

---

## Risks carried into Phase 1B

| Risk | Why it matters |
| --- | --- |
| **Every new tenant-scoped table is a chance to forget RLS.** | Enforced by a test and a startup assertion — but a developer can add a table *and* add it to `GLOBAL_TABLES` to make the test pass. Review of that list is load-bearing. `audit_chain_head` is the first justified entry; the justification is in the code. |
| **Every new analytical query path must go through `analytical_session`.** | A query issued on the control-plane session would be denied outright — a visible failure, not a leak, because `eip_app` holds nothing on tenant schemas. |
| **Per-tenant pools consume connections.** | Bounded with LRU and idle eviction, and asserted by test. `max_connections` remains a hard cluster limit worth monitoring as tenant count grows. |
| **Cache does not yet exist.** | ADR-007 §4 requires `auth_scope_hash` in every cache key. The highest-severity defect from the Phase 0 review is *not yet possible*, and must be prevented the moment caching appears. |
| **Credential rotation is implemented but not scheduled.** | `rotate_credential` exists and evicts the tenant's pool; nothing calls it on a cadence yet. |

---

## Product-owner decisions

**PO-001 … PO-005 are now recorded** in
[`20_PRODUCT_OWNER_DECISIONS.md`](20_PRODUCT_OWNER_DECISIONS.md), closing Q1–Q4
from the Phase 0 review and confirming the tenant data-plane model. Phase 1A was
built under ADR-003/009/010/014/015 as the governing authority; PO-005 confirms
that was the right assumption.

| Decision | Effect on Phase 1A |
| --- | --- |
| PO-001 — no bring-your-own warehouse in V1 | Confirms ADR-007 §7 and ADR-008; no change |
| PO-002 — outbound connections + IP allowlisting | Binds Phase 2; ADR-004's streaming contract is the agent extension point |
| PO-003 — multi-tenant SaaS from day one; TriVera is tenant #1 | **Raises the priority of provisioning automation**, which Phase 1A deliberately left manual |
| PO-004 — restatements never overwrite history | Binds Phase 6+; adds reason and approval to observation provenance, which ADR-012 did not mandate |
| PO-005 — confirm the ADR-003 hybrid model | **Confirms what Phase 1A built and verified.** No change required |

Two consequences are owed to later phases, recorded here so they are not
discovered late: provisioning automation moves into Phase 1B/2 scope under
PO-003, and an ADR-012 amendment is owed under PO-004.

Questions **Q5–Q12** remain open. None gates Phase 1B.

---

## Recommendation

**PASS.** (Recorded as conditional when written; the conditions are now met —
see *Phase 1A closure*.) Phase 1A is complete and its guarantees are now
evidenced rather than asserted. Every blocking finding is fixed with a test that
would catch its regression, CI is green end to end, and the boundaries of each
guarantee are documented — including the two places where the guarantee stops
(G10, G11).

The conditions on proceeding to Phase 1B:

1. **Answer Q1–Q4 and confirm PO-005.** Q2 (private-network connectivity) can
   change Phase 2's scope by weeks.
2. **Accept or reject the remaining documented boundary** (G11: a database
   owner can rewrite the audit checkpoint undetected). G10 is closed — analytical
   isolation no longer depends on any application choice.
3. **Treat G13 as a Phase 1B entry task.** The OIDC adapter is correct against
   synthetic keys; it has not met a real identity provider, and that is where
   discovery, clock skew, and claim-shape surprises live.

What this exercise should change going forward: the previous report failed not
because the code was unusually bad, but because **the tests confirmed the claims
where they were true instead of attacking them where they might not be.** The
suites added here are written the other way round — every one of them tries to
break the guarantee it documents, and several exist purely to prove the others
are not passing vacuously.

---

## Phase 1A closure

**Phase 1A is formally closed. Final verdict: PASS.**

### Commits

| Commit | What |
| --- | --- |
| `d766783` | Platform skeleton with enforced tenant isolation |
| `560dcbb` | First completion report (later corrected) |
| `450aab5` | Remediation of four blocking security findings |
| `c12ef30` | CI fixes — pnpm setup, Alembic hook, model drift |
| `7883f28` | Corrected report |
| `b7b5d35` | **G10 — per-tenant analytical credentials** |
| `1f3ed8e` | Report updated for G10 closure |

### Verification

[CI run 31233689164](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31233689164)
— **success, all 5 jobs.**

**232 tests pass**: 199 API, 16 worker, 7 web, 10 shell-script cases.
`ruff format` and `ruff check` clean; `mypy --strict` clean on 43 modules;
migrations upgrade, downgrade, and re-upgrade with an empty autogenerate diff.

### Accepted residual gaps

Carried into later phases with the product owner's acceptance, not as oversights:

| # | Gap | Why acceptable now |
| --- | --- | --- |
| G11 | Audit checkpoints are not exported off-box | Tampering by any *application or platform* role is detectable; only a database owner can evade it. The boundary is documented and has its own test |
| G14 | No production `SecretStore` adapter | Startup fails closed in production-like environments, so this blocks a deployment rather than weakening one |
| G13 | OIDC adapter unproven against a real IdP | Verified against in-process keys and a local JWKS server; discovery is the untested part |
| G3, G12 | No end-to-end browser test; frontend tests cover the error type only | The security-critical surface is the API, which is covered; the web tier holds no credentials and no database access |
| G5, G7, G9 | OpenTelemetry unexercised; Dramatiq is a connectivity check; compose health checks are shallow | Deliberately unbuilt — there is nothing yet to trace, queue, or deeply health-check |

Nothing on this list weakens tenant isolation, authentication, or audit
integrity. Each is either a deferred capability or a documented boundary.

### Phase 1B entry conditions

Phase 1B may begin. These are entry tasks, not blockers:

1. **Provisioning automation** — PO-003 makes TriVera an ordinary tenant, so
   manual platform-staff provisioning is no longer proportionate.
2. **G13** — exercise the OIDC adapter against a real identity provider.
   Discovery, clock skew, and claim-shape surprises live there.
3. **G3 / G12** — establish the end-to-end browser test and extend frontend
   coverage beyond the error type.
4. **Cache discipline** — ADR-007 §4 requires `auth_scope_hash` in every cache
   key. No cache exists yet, so the highest-severity defect found in the Phase 0
   review is currently *impossible*; it must be prevented the moment caching is
   introduced.
5. **ADR-012 amendment** — add reason and approval to observation provenance
   under PO-004, before the metric layer is built.

### What Phase 1A established

The platform foundation, and nothing else: no connectors, semantic model,
metrics, dashboards, lineage, insights, or AI. What it does establish is the set
of guarantees every later phase will rest on — tenant isolation on both planes,
enforced by PostgreSQL rather than by application discipline; delegated
authentication that cannot fall back; a worker with no privileged credential;
and an audit trail whose tamper-evidence has a tested boundary.

The correction history is part of the record deliberately. Four guarantees were
claimed before they were true, and a fifth (G10) was true but depended on an
application choice. Each was found by attacking the claim rather than confirming
it, which is the standard the remaining phases inherit.
