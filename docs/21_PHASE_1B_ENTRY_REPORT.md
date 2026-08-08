# 21 — Phase 1B Entry Tasks Report

Date: 2026-08-08
Status: **COMPLETE — PASS.** All three entry tasks done. See *Completion*.
Governed by: [ADR-001](adr/ADR-001-repository-architecture.md) …
[ADR-016](adr/ADR-016-bounded-context-enforcement.md), and the product-owner
decisions in [`20`](20_PRODUCT_OWNER_DECISIONS.md).
Follows: [`19_PHASE_1A_REPORT.md`](19_PHASE_1A_REPORT.md), which closed Phase 1A
and listed these tasks as entry conditions.

---

## Scope

Three entry tasks, each committed separately. **No connectors, ingestion,
semantic modelling, metrics, dashboards, or AI.** Phase 1B proper does not begin
here; this closes the conditions recorded at Phase 1A's closure.

| # | Task | Phase 1A gap it closes |
| --- | --- | --- |
| 1 | Real OIDC integration against a containerized identity provider | **G13** |
| 2 | Operator-driven tenant provisioning | PO-003 consequence |
| 3 | First browser end-to-end security test | **G3** (and part of G12) |

A convention worth stating: a task's own commit cannot contain its own commit
hash, so each task's hash and CI result are recorded here in the following
update. Every hash and link below has been observed, never predicted.

---

## Task 1 — Real OIDC integration

**Outcome: PASS.** The adapter has now met a real identity provider.

### What was actually wrong

Nothing in the adapter, as it turned out — but that could not have been asserted
beforehand, which was the point. Phase 1A generated RSA keys in-process and
served a JWKS from a local `http.server`. That proved the cryptography and left
the protocol untested: discovery, a realm-scoped issuer, the provider's own
`aud` behaviour, and live key rotation. The Phase 1A report recorded this
honestly as G13, "the OIDC adapter has never run against a real IdP".

**No production code changed.** The adapter passed every assertion below as
shipped. What changed is that the claim is now evidenced.

### The design decision that makes the negative cases meaningful

A forged token must differ from a valid one in **exactly the property under
test**. Signing a wrong-audience token with a key the provider never heard of
proves only that bad signatures are rejected — which was never in doubt, and
which is how a suite ends up looking thorough while testing one thing eight
times.

So the suite generates an RSA key at run time and registers it through
Keycloak's admin API. Keycloak then publishes it in its own JWKS. An expired
token is therefore a token that is perfect except for being expired; a
wrong-audience token is perfect except for its audience.

No key material is committed. The key exists only for the duration of the test
session and its Keycloak component is deleted on teardown.

Two findings from making this work, recorded because both fail silently:

- A key-provider component whose `parentId` is the **realm name** is accepted
  with `201 Created` and then never loaded. No error, no key, no log line. It
  must be the realm's internal UUID.
- A Keycloak user without `firstName`/`lastName` triggers the default
  `VERIFY_PROFILE` required action, and a direct grant fails with
  `invalid_grant: Account is not fully set up` — which reads like a credential
  problem and is not one.

### Proofs required, and where each is discharged

| Required | Test | Result |
| --- | --- | --- |
| Discovery works | `test_discovery_resolves_the_issuer_to_its_jwks_uri` | pass |
| … and fails loudly when wrong | `test_discovery_of_an_unknown_issuer_fails_loudly` | pass |
| JWKS retrieval works | `test_the_published_jwks_contains_a_usable_signing_key` | pass |
| A valid issued token is accepted | `test_a_token_issued_by_the_provider_is_accepted` | pass |
| … through the production selection path | `test_the_production_selection_path_accepts_it_too` | pass |
| Wrong issuer rejected | `test_wrong_issuer`, `test_a_genuine_token_from_a_different_provider` | pass |
| Wrong audience rejected | `test_wrong_audience` | pass |
| Wrong signing key rejected | `test_wrong_signing_key` | pass |
| Expired token rejected | `test_expired_token` | pass |
| Unsupported algorithm rejected | `test_unsupported_algorithm_hs256_confusion`, `test_unsupported_algorithm_none` | pass |
| Unknown `kid` rejected | `test_unknown_kid`, `test_missing_kid` | pass |
| Signing-key rotation works | `test_a_rotated_provider_key_is_picked_up_without_a_restart` | pass |
| Tenant context comes from membership | `TestTenantContextComesFromMembership` (4 tests) | pass |
| No development-secret fallback | `TestNoDevelopmentFallbackExists` (8 tests) | pass |

**28 tests, 28 passed.**

Three of them are doing most of the work:

- **`test_a_valid_token_naming_another_tenant_is_refused`** — the assertion the
  task exists for. The token is signed by a key the provider publishes, has the
  right issuer and audience, is unexpired, and names a real user. It claims
  `tid` = tenant B; Ada is a member of tenant A alone. The test first asserts
  the token *verifies*, so the refusal that follows is unambiguously an
  authorization decision rather than a verification failure. Cryptographic
  validity is not authorization.
- **`test_a_genuine_token_from_a_different_provider`** — `eip-other` is a second
  real realm with its own real keys at its own real JWKS endpoint. Its tokens
  are valid, for it. Controlling *an* identity provider must not confer control
  of this one. Comparing against a fabricated issuer string would only have
  proved that string comparison works.
- **`test_a_token_signed_by_the_imported_key_is_accepted`** — the negative
  control for the entire forgery section. Every rejection is minted the same
  way; if the imported key were not genuinely published, all of them would be
  false passes.

### Guard against the failure mode this project has already hit

The suite skips cleanly when the provider is absent, so a developer without the
container gets a clean run. That is also how a security suite silently stops
running.

`EIP_TEST_OIDC_REQUIRED=1` — set in CI and nowhere else — turns absence into a
**failure** instead of a skip. Verified both ways: 28 skipped without it,
28 errors with it.

### Changed files

| File | Change |
| --- | --- |
| `apps/api/tests/security/test_oidc_keycloak.py` | **new** — 28 release-gating tests |
| `infra/keycloak/realm-eip-test.json` | **new** — the provider under test |
| `infra/keycloak/realm-eip-other.json` | **new** — the second issuer |
| `infra/keycloak/README.md` | **new** — why each realm setting is what it is |
| `infra/docker-compose.yml` | Keycloak service under a new `oidc` profile; `EIP_TEST_OIDC_BASE_URL` for the API container |
| `apps/api/pyproject.toml` | registers the `oidc` pytest marker |
| `.github/workflows/ci.yml` | new release-gating job |

Realm JSON carries no comment keys: Keycloak's importer rejects any field it
does not recognise, `$comment` included. The reasoning lives in the README
beside them.

### Verification

```
tests/security/test_oidc_keycloak.py    28 passed
apps/api (full suite)                  227 passed      (was 199)
mypy --strict                          clean, 38 source files
ruff format --check / ruff check       clean
```

The provider is not started by default — it costs roughly fifteen seconds and
one suite needs it:

```bash
docker compose -f infra/docker-compose.yml --profile oidc up -d --wait
```

### CI

New job **`SECURITY: OIDC against a real identity provider`**, separate from
`python-tests` because it is the only suite needing an identity provider. It
starts PostgreSQL and Keycloak through compose rather than as service
containers — the realms are imported from a mounted directory, and service
containers cannot mount anything.

**Commit `9d21efc`.** CI
[run 31238388119](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31238388119)
— **success, all 6 jobs**, including the new one on its first execution.

### Gaps remaining after Task 1

| Gap | Status |
| --- | --- |
| **G13** | **Closed.** Discovery, verification, rotation, and the membership binding are all evidenced against a real provider. |
| Authorization-code flow | Not exercised. The suite uses the direct grant, so redirect handling, PKCE, and state/nonce are untested. They are the *client's* responsibility, and the client is Task 3's subject — but Task 3 signs in through the local test path, so this stays open beyond these entry tasks. |
| Real-world IdP variety | Keycloak is one provider. Entra ID and Okta differ in claim shapes and in whether `aud` is a string or an array. The adapter handles both, but only Keycloak has been observed. |
| G3, G12 | Task 3. |
| G11, G14 | Unchanged Phase 1A residuals; see [`19`](19_PHASE_1A_REPORT.md). |

---

## Task 2 — Operator-driven tenant provisioning

**Outcome: PASS.**

### What was actually wrong

Phase 1A provisioned a tenant inside one request handler: insert the row, then
run the DDL. It worked, and it had no answer for the second half failing. The
tenant existed, its analytical schema did not, **nothing recorded which**, and
the only way to find out was for somebody to try to use it.

That was tolerable while provisioning happened once, by hand. PO-003 removed
that assumption: TriVera is tenant #1 and not a special case, so provisioning is
something staff do repeatedly — and anything done repeatedly is interrupted
eventually.

### Design

*The workflow owns its transactions.* It takes a session **factory**, not a
session, because provisioning is three transactions with DDL between them:

1. **claim** — move `pending`/`failed` → `in_progress`, atomically;
2. *(no transaction)* — the data-plane DDL: schema, login role, password;
3. **settle** — record the credential reference and mark `ready`; or, on
   failure, mark `failed` with a redacted reason.

Step 3's failure branch is the reason the claim is separate. A workflow holding
one transaction throughout would roll its own failure record back along with
everything else, leaving the tenant in `pending` looking untouched — precisely
the half-created state the task exists to prevent.

*The claim is a conditional `UPDATE`, not lock-then-check.* Two concurrent
callers: the second blocks on the row lock and, under READ COMMITTED,
re-evaluates the `WHERE` after the first commits. It matches zero rows and is
told the truth. No advisory lock, no lease table, and no window between checking
and acting.

*Stale claims expire.* A process that dies mid-provision leaves `in_progress`
behind, and without an expiry that state is a tombstone — nothing will ever
clear it. A claim older than `provisioning_stale_after_seconds` (default 300)
may be taken over, and the takeover shows in the attempt count.

*Operator-driven, not self-serve.* Every entry point requires `platform_admin`,
an `X-Elevation-Reason` header, and writes an audit event into the target
tenant's own chain. No public signup path was added.

### The leak that nearly shipped

`summarise_failure` exists for one reason. SQLAlchemy appends the failing
statement **and its parameters** to its exception message, and the statement
that creates a tenant role contains that role's password. Recording a raw driver
error in `provisioning_error` would have written the credential into a column an
operator reads from a console — a fresh instance of exactly the class of defect
Phase 1A's remediation was about.

Two independent defences, tested separately so that removing either one fails a
test: everything from `[SQL:` onward is dropped, and every single-quoted literal
is masked. A third test asserts the result is still diagnostic — redaction that
reduces every failure to the same string would be its own defect.

### Requirements, and where each is discharged

| Required | Test | Result |
| --- | --- | --- |
| Creates the control-plane tenant | `test_a_tenant_is_registered_provisioned_and_marked_ready` | pass |
| Creates schema, login role, credential | `test_the_analytical_schema_and_login_role_really_exist` | pass |
| Stores only the `SecretRef` | same test + `test_the_api_response_carries_no_credential_material` | pass |
| Tracks state and failure detail | `test_a_failed_tenant_is_left_visible_not_half_created` | pass |
| Safe retry after partial failure | `test_retry_after_a_partial_failure_succeeds` | pass |
| Durable audit and outbox events | `test_registration_and_provisioning_both_emit_audit_and_outbox`, `test_a_failure_is_audited_into_the_tenants_own_chain` | pass |
| No credentials in responses, logs, audit, jobs | `test_the_generated_password_appears_in_no_observable_surface` | pass |
| Prevents duplicate tenants | `test_a_second_create_for_a_ready_tenant_is_refused`, `test_concurrent_creates_of_the_same_slug_produce_one_tenant` | pass |
| Prevents concurrent provisioning races | `test_a_second_provisioning_attempt_is_refused_while_one_holds_the_claim`, `test_a_stale_claim_can_be_taken_over` | pass |
| Failed tenants visibly recoverable | `test_incomplete_tenants_are_listed_first` | pass |
| Cross-tenant isolation | `test_a_provisioned_tenant_cannot_read_another_provisioned_tenant` | pass |
| Not self-serve | `TestProvisioningIsOperatorDriven` (4 tests) | pass |

**23 tests, 23 passed.**

The two that carry the most weight:

- **`test_the_generated_password_appears_in_no_observable_surface`** reads the
  tenant's *actual* password out of the `SecretStore` and searches for that
  exact string in the returned record, every column of the tenant row, every
  audit event, every outbox message, and every log record emitted while
  provisioning ran. Not "does the code look careful" — does the credential
  appear anywhere. It asserts the password is non-empty first, so it cannot pass
  by finding nothing because nothing was stored.
- **`test_a_provisioned_tenant_cannot_read_another_provisioned_tenant`**
  provisions two tenants through the workflow, gives each a table, and issues
  the fully-qualified cross-tenant query with tenant A's own credential.
  PostgreSQL refuses it. A workflow that created schemas without the per-tenant
  credential model would have passed every other test here while silently
  undoing the most expensive fix of Phase 1A.

`test_the_analytical_schema_and_login_role_really_exist` is the negative
control: without it, "A cannot read B" could pass because neither existed.

### What was removed

`TenantProvisioningService.create_tenant` and `provision_data_plane` are gone,
and the class is now `PlatformAdminService` (memberships only). Leaving the old
methods behind would have meant two provisioning paths, one of which silently
cannot record a partial failure — the same shape as the "misleading partial
path" Phase 1A was criticised for.

### Two things found by running the checks rather than assuming them

- **Model drift.** The migration created a partial index that the ORM model did
  not declare. The autogenerate check caught it. This is the second time that
  check has earned its place.
- **A test that was too rigid, not wrong code.** The audit-sequence assertion
  compared the action list for equality and failed when the *live worker
  container* relayed the outbox message mid-test and appended `outbox.relayed`.
  That is the outbox working end to end. The assertion now filters to
  provisioning actions, so the suite does not pass or fail on whether a
  container happened to be running.

### Changed files

| File | Change |
| --- | --- |
| `apps/api/src/eip/identity/provisioning.py` | **new** — the workflow |
| `apps/api/src/eip/governance/outbox.py` | **new** — transactional publish helper |
| `apps/api/migrations/versions/0004_tenant_provisioning.py` | **new** — lifecycle columns, constraints, partial index |
| `apps/api/tests/security/test_tenant_provisioning.py` | **new** — 23 release-gating tests |
| `apps/api/src/eip/identity/models.py` | provisioning state, attempts, timestamps, error; `status` gains `provisioning` |
| `apps/api/src/eip/api/routers/admin.py` | `POST /v1/admin/tenants` rewritten; adds `POST .../{id}/provision` and `GET /v1/admin/tenants` |
| `apps/api/src/eip/identity/service.py` | `TenantProvisioningService` → `PlatformAdminService`; provisioning methods removed |
| `apps/api/src/eip/governance/audit.py` | `outcome` on `record_platform_action`; two new actions |
| `apps/api/src/eip/platform/settings.py` | `provisioning_stale_after_seconds` |
| `apps/api/src/eip/scripts/seed_demo.py` | uses the workflow; tenant creation moved out of the enclosing transaction |
| `apps/api/tests/integration/test_health_and_migrations.py` | migration head → `0004` |
| `apps/api/tests/security/test_privileged_platform_access.py` | expects both audit events |
| `packages/contracts/openapi.json` | regenerated |
| `.github/workflows/ci.yml` | new release-gating step |

### Verification

```
tests/security/test_tenant_provisioning.py     23 passed
apps/api (full suite)                         250 passed      (was 227)
apps/worker                                    16 passed
apps/web                                        7 passed
mypy --strict                                  clean, 40 source files
ruff format --check / ruff check               clean, 63 files
alembic upgrade → downgrade → upgrade          pass
model-drift autogenerate                       empty after the fix above
```

One caveat, stated because it will bite somebody: running the worker suite
while the local `worker` **container** is up is flaky — the live relay competes
with the test for outbox rows. Observed once in ten runs; 5/5 stable with the
container stopped, which is also the CI arrangement. This is the environment,
not the code, and it does not affect CI.

**Commit `d7e2518`**, plus `cb5d75c` (see below). CI
[run 31239982186](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31239982186)
— **success**.

The first push of this task went red, and it is worth recording why: the static
job failed on **one file's formatting**. I ran the formatter, then made a
further edit to a test, then did not re-run it. The check happened before the
last change rather than after it. Fixed in `cb5d75c`; no code was wrong, and
the process was.

### Gaps remaining after Task 2

| Gap | Status |
| --- | --- |
| Provisioning automation (PO-003) | **Closed** for operator-driven provisioning. Self-serve signup remains deliberately unbuilt. |
| Deprovisioning is not exposed | `deprovision` exists on the data plane and is used by tests, but no operator route calls it. Offboarding is a governed action needing its own retention decision, and inventing one here would have been scope. |
| No background retry | A failed tenant waits for an operator. The outbox event exists, so a retry actor is a small addition when there is a reason for one. |
| G11, G14 | Unchanged Phase 1A residuals. |

---

## Task 3 — First browser end-to-end security test

**Outcome: PASS.**

### What was actually missing

ADR-001 called for `tests/e2e`. It did not exist, and the Phase 1A report said
so — gap G3. Every isolation assertion the project had was made by a test client
that could not send a stray header, follow a redirect it was not expecting, or
render a page containing the wrong organization's name. The guarantee was well
evidenced one layer below where a customer actually experiences it.

### The flow

Eleven tests, one browser, the real stack, two real seeded tenants. Tenant B's
identifiers are resolved from a **real session** through the API and then used
as the attack payload — never a placeholder, because a placeholder cannot prove
that a real identifier fails.

| Required | Test |
| --- | --- |
| Sign in through the local identity path | every test; `signInAs` drives the actual form |
| Load the application as tenant A | `the organization context shown is tenant A, resolved server-side` |
| Tenant A identity is displayed | same — name, slug, id, and signed-in email |
| Readiness information is displayed | `readiness information is displayed` |
| Tenant-header manipulation | `a forged X-Tenant-Id header changes nothing` |
| URL manipulation | `tenant identifiers in the URL change nothing` (four variants) |
| No tenant B identifier or data appears | `expectNoTraceOfTenantB`, called from every manipulation |
| Unauthenticated access returns to sign-in | `reaching the application without a session returns to sign-in`, `signing out ends the session…`, `the API refuses an unauthenticated request` |
| Diagnostics without tokens | `redaction removes credentials from captured diagnostics` |

Two more than the brief asked for, because they were cheap and they matter:

- **`the session token is unreachable from browser JavaScript`** — the premise
  everything else rests on. If a page script could read the token, isolation in
  the browser would be a rendering convention. It also checks the cookie *is*
  present and `HttpOnly`, so it cannot pass on an unauthenticated page.
- **`a token naming tenant B grants nothing`** — the most interesting result
  here. The dev issuer *mints* the token: `tid` is a request, and refusing it at
  minting time would move the decision to the wrong place. Membership answers no,
  so the browser ends up holding a cryptographically valid token that opens
  nothing, and the test proves it stays inert across a fresh navigation.

`tenant B's own session sees tenant B and not tenant A` is the negative control
for the whole file. Without it, "tenant B's data is absent" would be trivially
true and the suite would be testing nothing.

### What the first run taught

The suite failed six ways on its first execution, and two of those were the
tests being wrong rather than the application:

- **Next.js echoes the request URL into its RSC flight payload.** An identifier
  the *attack itself* put in the query string comes back in the response. That
  is the attacker's own input, not a disclosure — but a naive "tenant B's id
  appears nowhere in the DOM" assertion fails on a page that leaked nothing at
  all, and the tempting fix is to weaken the test until it passes.

  `expectNoTraceOfTenantB` draws the line explicitly instead. Values the attack
  supplied are checked against **rendered text** (they must never be displayed);
  everything else — notably tenant B's display name, which no attempt ever
  supplies — is checked against the **entire response, script payloads
  included**. `supplied` is passed per attempt, so widening it is a visible diff
  rather than a quiet relaxation.

- **A first draft of the helper iterated every field of the tenant object**,
  which swept in `status: "active"` — shared by every tenant, present on tenant
  A's own page. An assertion that fails on a page leaking nothing is worse than
  no assertion, because the only way to fix it is to make it weaker.

The other four were `loading.tsx` rendering sections with identical headings
(Playwright's strict mode correctly refused to guess between them), and the
sign-out control not existing.

### Diagnostics are built, not inherited

Playwright's trace and video are **off**. Both are genuinely useful and both
record complete request headers — `Authorization` and the session cookie. A CI
artefact containing a bearer token is a credential leak with a download link,
and it would be one nobody looked at until it mattered. `storageState` is never
written for the same reason: the usual sign-in-once pattern would leave a live
token in a file in the working tree.

`support/diagnostics.ts` captures the final URL, page title, console output, a
redacted DOM, and cookie **names only**. It has its own test, which feeds it a
JWT in four shapes — bearer header, cookie, JSON field, query parameter — and
also checks an ordinary error message survives unmangled. Redaction that
destroys every message is its own defect.

### One thing added beyond tests

The sign-out control. `signOut` was written in Phase 1A and nothing rendered it
— dead code on one side, a missing control on the other. It is a plain form
posting to the server action, so ending a session does not depend on a bundle
loading.

### Changed files

| File | Change |
| --- | --- |
| `tests/e2e/specs/tenant-isolation.spec.ts` | **new** — 11 release-gating browser tests |
| `tests/e2e/support/diagnostics.ts` | **new** — redaction and failure capture |
| `tests/e2e/support/fixtures.ts` | **new** — tenant resolution and sign-in helper |
| `tests/e2e/playwright.config.ts` | **new** — trace/video off, no retries, no `storageState` |
| `tests/e2e/package.json`, `tsconfig.json`, `eslint.config.mjs` | **new** — workspace member |
| `apps/web/src/app/app/layout.tsx` | **new** — the sign-out control |
| `apps/web/src/app/globals.css` | styles for it |
| `pnpm-workspace.yaml`, `pnpm-lock.yaml` | registers `tests/e2e` |
| `.gitignore` | Playwright output |
| `.github/workflows/ci.yml` | new release-gating job |

### CI

New job **`SECURITY: tenant isolation in the browser`**. It starts the full
stack, seeds **two** organizations (isolation cannot be observed with one),
builds and starts the web application, and runs the suite. Failure diagnostics
upload as an artefact — safe to publish, because of the redaction above.

**Commit `cc29dfa`.** CI
[run 31239982186](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31239982186)
— **success, all 7 jobs**, including this one on its first execution.

### Gaps remaining after Task 3

| Gap | Status |
| --- | --- |
| **G3** | **Closed.** The browser harness exists and is release-gating. |
| **G12** | **Partly closed.** 7 unit tests plus 11 browser tests now cover the authenticated flow end to end. Individual component and route unit tests remain absent; the browser suite covers the paths that matter for isolation. |
| One browser only | Chromium. Firefox and WebKit are one config line away, and nothing here is engine-specific, but only Chromium has been observed. |
| Authorization-code flow | Still unexercised. The browser signs in through the local identity path, so redirect handling, PKCE, and state/nonce are untested. This is the same gap noted under Task 1 and it survives both. |
| G11, G14 | Unchanged Phase 1A residuals. |

---

## Documentation correction

The Phase 1A recommendation said the guarantees stop in "two places (G10, G11)".
G10 was closed by `b7b5d35`, which removed the shared credential entirely —
analytical isolation no longer depends on any application choice. Corrected in
[`19_PHASE_1A_REPORT.md`](19_PHASE_1A_REPORT.md) to name **G11 alone**, with a
note on what changed.

A stale sentence in a *recommendation* is the worst place for one: a reader
deciding whether to proceed would have counted an open boundary that is not
open.

---

## Completion

### Task-by-task outcome

| # | Task | Outcome | Gap closed |
| --- | --- | --- | --- |
| 1 | Real OIDC integration | **PASS** — 28 tests against a containerized Keycloak. No production code changed; the adapter was correct, which could not have been asserted beforehand. | **G13** |
| 2 | Operator-driven tenant provisioning | **PASS** — 23 tests. Three-transaction workflow, atomic claim, redacted failures. Found and fixed a credential leak into `provisioning_error`. | PO-003 consequence |
| 3 | Browser end-to-end security test | **PASS** — 11 tests through a real browser. Found two of its own assertions to be wrong before finding the application to be right. | **G3**, part of **G12** |

### Commits

| Commit | Task |
| --- | --- |
| `9d21efc` | Task 1 — OIDC against a real identity provider |
| `d7e2518` | Task 2 — operator-driven tenant provisioning |
| `cc29dfa` | Task 3 — browser tenant-isolation suite |

Plus `cb5d75c`, a formatting-only fix for the one red CI run (see Task 2).

### Test results, as observed in CI

Every figure below is read from the log of
[run 31239982186](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31239982186),
not from a local run. That is the last run to execute a code change; the commits
after it are documentation only.

| Suite | Result |
| --- | --- |
| Unit and architecture | 59 passed |
| Integration | 12 passed |
| `SECURITY: control-plane tenant isolation` | 28 passed |
| `SECURITY: per-tenant analytical credentials (G10)` | 27 passed |
| `SECURITY: operator-driven tenant provisioning` | **23 passed** |
| `SECURITY: audit tamper evidence` | 23 passed |
| `SECURITY: production token verification` | 33 passed |
| `SECURITY: OIDC against a real identity provider` | **28 passed** |
| `SECURITY: privileged platform access` | 8 passed |
| `SECURITY: audit integrity and authorization` | 9 passed |
| `SECURITY: worker privileges and background isolation` | 16 passed |
| `SECURITY: tenant isolation in the browser` | **11 passed** |
| Web unit | 7 pass, 0 fail |
| Secret-scan self-test | 10 cases |

**278 automated checks, 0 failures.** Phase 1A closed at 232.

`mypy --strict`: clean, 40 source files (API) and 5 (worker).
`ruff format --check` and `ruff check`: clean, 72 files.

### Migration and rollback

Migration `0004_tenant_provisioning` adds the provisioning lifecycle columns,
widens `ck_tenant_status`, and creates a partial index on incomplete tenants.

| Operation | Result |
| --- | --- |
| `alembic upgrade head` (`0001` → `0004`) | **pass** |
| `alembic downgrade base` (`0004` → `0003` → `0002` → `0001` → base) | **pass** |
| `alembic upgrade head` (re-apply) | **pass** |
| Model-drift autogenerate | **empty** |

The drift check earned its place again: the partial index was in the migration
and not on the ORM model. That is the second time it has caught real drift, and
both times the drift was invisible to every other check.

The downgrade deliberately settles `status = 'provisioning'` rows to `'active'`
rather than failing on the narrower constraint. A migration that cannot run
backwards cannot ship.

### Final CI

[Run 31240283082](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31240283082)
on commit `8875441` — **success**. The job breakdown below is from
[run 31239982186](https://github.com/getsureshv/trivera-executive-intelligence-platform/actions/runs/31239982186),
the last run over a code change — **all 7 jobs**:

| Job | Result |
| --- | --- |
| Python — format, lint, types | ✓ |
| Python — migrations, unit, integration, security | ✓ |
| SECURITY: OIDC against a real identity provider | ✓ |
| SECURITY: tenant isolation in the browser | ✓ |
| Web — lint, types, tests, build | ✓ |
| Local stack smoke test | ✓ |
| Secret scan | ✓ |

Two of these jobs are new, and both passed on their first CI execution.

### Unresolved risks

Ordered by what would hurt most if ignored.

| # | Risk | Why it is acceptable now | What would change that |
| --- | --- | --- | --- |
| 1 | **G11 — audit checkpoints are not exported off-box.** A database owner can rewrite them undetected. | The only remaining boundary on tamper-evidence, documented and tested. Tampering by any application or platform role is detectable. | A compliance commitment that names the database administrator in the threat model. |
| 2 | **G14 — no production `SecretStore` adapter.** | `build_secret_store` fails closed in production-like environments, so this blocks a deployment rather than weakening one. | The first non-local deployment. |
| 3 | **Cache discipline.** ADR-007 §4 requires `auth_scope_hash` in every cache key; no cache exists yet. | The highest-severity defect from the Phase 0 review is currently *impossible*. | The moment caching is introduced — which the first connector slice may well do. |
| 4 | **The authorization-code flow is unexercised.** Redirect handling, PKCE, state, and nonce are untested. Both Task 1 and Task 3 sign in through the local path. | Token *verification* is now proven against a real provider, and that is the security-critical half. The client half is standard and delegated. | Configuring a real IdP for a real environment. |
| 5 | **One identity provider, one browser.** Keycloak and Chromium. Entra ID and Okta differ in claim shapes and in whether `aud` is a string or an array. | The adapter handles both shapes; only one has been observed. | A customer naming their provider. |
| 6 | **Deprovisioning has no operator route.** `deprovision` exists and is used by tests; nothing calls it in anger. | Offboarding is a governed action needing its own retention decision. Inventing one here would have been scope. | The first tenant that leaves, or a data-retention commitment. |
| 7 | **No background retry for failed provisioning.** A failed tenant waits for an operator. | Failures are visible and retryable, and the outbox event exists. | Provisioning volume that makes manual retry disproportionate. |
| 8 | **ADR-012 amendment owed** — PO-004 requires reason and approval in observation provenance. | Nothing is implementable until the metric layer exists. | Starting the metric layer (Phase 6). |

Risks 1, 2, 3 and 8 are carried from Phase 1A unchanged. Nothing in these three
tasks weakened any existing guarantee.

### Recommendation

**PASS. The first connector slice may begin.**

The three conditions that gated it are discharged, and each was discharged by
attacking the claim rather than confirming it:

1. **Authentication is proven, not assumed.** The adapter has met a real
   provider, survived six classes of forged token signed by keys that provider
   genuinely publishes, and healed a live key rotation without a restart. A
   cryptographically perfect token naming another tenant is refused by the
   membership lookup.
2. **Tenants can be created reliably and observably.** Provisioning is
   idempotent, races are refused by the database rather than by a check-then-act
   window, partial failures are visible and retryable, and the credential
   appears in no response, row, audit event, outbox message, or log line — as
   verified by searching for the actual password.
3. **Isolation holds where the customer meets it.** A real browser, real
   sessions, real identifiers, and the manipulations an attacker reaches for
   first.

**Three conditions on the connector slice**, stated now because they are cheaper
to honour than to retrofit:

- **`auth_scope_hash` in every cache key, from the first cache.** Risk 3 becomes
  live the moment a connector caches anything.
- **Connector work stays serializable and remotely executable.** PO-002 preserved
  the customer-network agent as an extension point, and ADR-004's
  `ExtractPlan`/`RecordBatch` streaming contract is that extension point.
  Collapsing it into an in-process object model would close the door quietly.
- **Every connector credential goes through the `SecretStore`.** G14 means there
  is no production adapter yet; a connector that reads a credential from the
  environment instead would work locally and be wrong everywhere else.

What these tasks should change about how the next phase is verified: **twice
here, the test was wrong and the code was right** — the RSC echo, and the tenant
object with a shared `status` field. Both times the tempting fix was to weaken
the assertion until it passed. Both were resolved by making the assertion more
precise instead, and writing down why. That is the harder direction, and it is
the one that keeps a suite worth having.
