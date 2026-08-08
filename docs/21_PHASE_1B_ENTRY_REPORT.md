# 21 — Phase 1B Entry Tasks Report

Date: 2026-08-08
Status: **In progress** — Tasks 1 and 2 complete, Task 3 outstanding.
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

Commit hash and CI result: recorded in the Task 3 update, per the convention
above.

### Gaps remaining after Task 2

| Gap | Status |
| --- | --- |
| Provisioning automation (PO-003) | **Closed** for operator-driven provisioning. Self-serve signup remains deliberately unbuilt. |
| Deprovisioning is not exposed | `deprovision` exists on the data plane and is used by tests, but no operator route calls it. Offboarding is a governed action needing its own retention decision, and inventing one here would have been scope. |
| No background retry | A failed tenant waits for an operator. The outbox event exists, so a retry actor is a small addition when there is a reason for one. |
| G11, G14 | Unchanged Phase 1A residuals. |

---

## Task 3 — First browser end-to-end security test

Not started.
