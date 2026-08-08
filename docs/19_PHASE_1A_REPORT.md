# 19 — Phase 1A Completion Report (Platform Skeleton)

Date: 2026-08-07
Status: **Remediated.** Supersedes the first version of this report, which
overstated four guarantees — see *Corrections* below.
Commits: `d766783` (initial), `450aab5` (remediation), `c12ef30` (CI fixes)
CI: [run 31230016910](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31230016910)
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

All four are fixed and proven. **220 tests pass** (188 API + 16 worker + 7 web +
10 shell-script cases), CI is green end to end, and the remaining gaps are
listed honestly rather than discovered later.

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

**Now:**

- `eip_app` is **`NOINHERIT`** and holds **no privilege** on any tenant schema.
- Each tenant gets a `NOLOGIN`, passwordless role `eip_t_<uuid>` with `USAGE` on
  exactly one schema.
- `eip_app` is a *member* of each, which grants only the ability to `SET ROLE` —
  not the privileges, because of `NOINHERIT`.
- `analytical_session()` issues `SET LOCAL ROLE`, transaction-scoped, and is the
  only place in the codebase that does (enforced by an architecture test).
- Startup refuses to boot if `eip_app` is `INHERIT`.

After the role switch, a statement naming another tenant's schema is refused by
PostgreSQL with `permission denied` — regardless of how the SQL was built.

**Residual risk, stated plainly:** `eip_app` is a member of every tenant role,
so code that *deliberately* assumed the wrong tenant's role would succeed. It is
bounded by a single `SET ROLE` call site, a handle/context match check, and an
architecture test. Eliminating it requires per-tenant login credentials and
pools — ADR-003 Tier 2 — which needs the `SecretStore` adapter that arrives in
Phase 2.

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

---

## Database role and credential model

| Role | superuser | bypassrls | inherit | createrole | Used by |
| --- | --- | --- | --- | --- | --- |
| `eip_app` | no | **no** | **no** | no | every request and every job |
| `eip_platform` | no | **yes** | no | yes | audited platform-admin operations; owns the dispatch function |
| `eip_migrator` | no | no | no | no | Alembic only; member of `eip_platform` so migrations can assign function ownership |
| `eip_t_<uuid>` | no | no | no | no | never logs in; assumed via `SET LOCAL ROLE` |

**Connection routing**

```
Control plane   eip_app       → public schema; RLS scoped by app.tenant_id
                                (SET LOCAL, transaction-scoped)

Analytical      eip_app       → SET LOCAL ROLE eip_t_<uuid>
                                current_user becomes the tenant role; PostgreSQL
                                denies any other tenant's schema outright

Principal       eip_app       → app.user_id only, for membership lookup at
                                sign-in (policy membership_self_select).
                                Sign-in never runs on the privileged role.

Privileged      eip_platform  → separate engine, API process only, requires a
                                PlatformContext with a recorded reason

Worker          eip_app       → constrained engine only; no platform engine is
                                ever constructed
```

One pool per login role. No per-tenant credential exists, so there is no
per-tenant secret to store or rotate. `SET LOCAL` is transaction-scoped, so no
pooled connection carries a tenant role or tenant setting into the next
checkout — asserted by test.

**Verified at runtime:**

```
eip_app       super=f  bypassrls=f  inherit=f
eip_migrator  super=f  bypassrls=f  inherit=f
eip_platform  super=f  bypassrls=t  inherit=f

eip_audit_chain_advance    owner=eip_migrator  bypassrls=false
eip_audit_chain_offboard   owner=eip_migrator  bypassrls=false
eip_outbox_pending_tenants owner=eip_platform  bypassrls=true
```

---

## Tests: new, changed, and observed results

**220 total.** Every count below was collected from the suite, not estimated.

| Suite | Tests | Status | Purpose |
| --- | --- | --- | --- |
| `security/test_oidc_verification.py` | **33** | **new** | F2 — acceptance, wrong issuer/audience/key, expiry, unknown/missing `kid`, `alg=none`, HS256 confusion, environment gating, rotation |
| `security/test_tenant_isolation.py` | 28 | changed | Control-plane isolation, 3 layers |
| `security/test_audit_tamper_evidence.py` | **23** | **new** | F4 — field coverage, mutation, all four deletion classes, checkpoint protection, offboarding, documented limits |
| `security/test_analytical_isolation.py` | **16** | **new** | F1 — role model, cross-schema denial, joins, `search_path`, pooling, guards, negative control |
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
API      188 passed
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
| G4 | **`SecretStore` is a port with no adapter.** Types and interface only — Phase 1A stores no secrets. | Low — by design | 2 |
| G5 | **OpenTelemetry is wired but never exercised.** Disabled by default; no collector has received a span. | Low | 1B |
| G7 | **Dramatiq is a connectivity check only.** No actors, queues, or per-tenant fairness caps. | Low — by design | 2 |
| G9 | Compose health checks use `urllib`, so they exercise HTTP but not the dependency graph `/ready` does. | Informational | — |
| G10 | **Per-tenant analytical credentials are not implemented.** `eip_app` can `SET ROLE` to any tenant role; see F1 residual risk. | Medium | 2 (ADR-003 Tier 2) |
| G11 | **Audit checkpoints are not exported off-box.** A database owner can rewrite them undetected; see F4 boundary. | Medium | 1B/2 |
| G12 | **Frontend tests cover the error type only.** 7 tests, no component or route coverage. | Medium | 1B |
| G13 | **The OIDC adapter has never run against a real IdP.** Verified against in-process RSA keys and a local JWKS server; discovery is untested against a live provider. | Medium | 1B |

G1 (CI never run), G2 (zero frontend tests), G6 (uncommitted OpenAPI), and G8
(unratified `import-linter` deviation, now [ADR-016](adr/ADR-016-bounded-context-enforcement.md))
are **closed**.

---

## Risks carried into Phase 1B

| Risk | Why it matters |
| --- | --- |
| **Every new tenant-scoped table is a chance to forget RLS.** | Enforced by a test and a startup assertion — but a developer can add a table *and* add it to `GLOBAL_TABLES` to make the test pass. Review of that list is load-bearing. `audit_chain_head` is the first justified entry; the justification is in the code. |
| **Every new analytical query path is a chance to skip `SET ROLE`.** | Isolation holds only inside `analytical_session`. The architecture test confines `SET ROLE` to one module, but a query issued on a plain session would simply be denied — a visible failure, not a leak. |
| **Cache does not yet exist.** | ADR-007 §4 requires `auth_scope_hash` in every cache key. The highest-severity defect from the Phase 0 review is *not yet possible*, and must be prevented the moment caching appears. |
| **The residual `SET ROLE` risk is real.** | Documented, bounded, and tested — but not eliminated until Tier 2. |

---

## Product-owner items still open

**PO-001 … PO-005 do not exist in this repository** (verified by `git ls-files`
and a repo-wide grep). Phase 1A and this remediation proceeded under
ADR-003/009/010/014/015 as the governing authority.

Still needed:

- **PO-005 / tenant data plane** — confirm schema-per-tenant. If a different
  mode was chosen, the `TenantDataPlane` port absorbs it but the implementation
  would be replaced.
- **Q1–Q4 from the Phase 0 review** remain the gate on wider Phase 1 work:
  bring-your-own warehouse, private-network connectivity, SaaS-now vs.
  TriVera-first, and restatement policy.

---

## Recommendation

**PASS, conditional.** Phase 1A is complete and its guarantees are now
evidenced rather than asserted. Every blocking finding is fixed with a test that
would catch its regression, CI is green end to end, and the boundaries of each
guarantee are documented — including the two places where the guarantee stops
(G10, G11).

The conditions on proceeding to Phase 1B:

1. **Answer Q1–Q4 and confirm PO-005.** Q2 (private-network connectivity) can
   change Phase 2's scope by weeks.
2. **Accept or reject the two documented residual risks** (G10, G11). Both are
   defensible for Phase 1A and both have a named remediation path; neither
   should be discovered later as a surprise.
3. **Treat G13 as a Phase 1B entry task.** The OIDC adapter is correct against
   synthetic keys; it has not met a real identity provider, and that is where
   discovery, clock skew, and claim-shape surprises live.

What this exercise should change going forward: the previous report failed not
because the code was unusually bad, but because **the tests confirmed the claims
where they were true instead of attacking them where they might not be.** The
suites added here are written the other way round — every one of them tries to
break the guarantee it documents, and several exist purely to prove the others
are not passing vacuously.
