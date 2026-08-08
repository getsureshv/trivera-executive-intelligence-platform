# 21 — Phase 1B Entry Tasks Report

Date: 2026-08-08
Status: **In progress** — Task 1 complete, Tasks 2 and 3 outstanding.
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

Commit hash and CI result: recorded in the Task 2 update, per the convention
above.

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

Not started.

---

## Task 3 — First browser end-to-end security test

Not started.
