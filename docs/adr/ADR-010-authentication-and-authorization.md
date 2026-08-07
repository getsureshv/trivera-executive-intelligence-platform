# ADR-010: Authentication and Authorization

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`07_SECURITY_MULTITENANCY_GOVERNANCE.md` requires OIDC/OAuth, SAML where enterprises need
it, RBAC, policy-based access, row-level security, semantic-field restrictions, metric
permissions, and dashboard permissions — with authorization enforced **before** data
access on both the dashboard and assistant surfaces.

That is the right list. What is missing is *how* those layers compose without becoming an
unreviewable tangle, and where each is enforced. Two specific hazards:

- **Row-level security is listed but has no mechanism.** "Restrict which rows a principal
  can see" is meaningless unless it is expressed against something. Against physical
  tables it breaks the moment a mapping changes; it must be expressed **semantically**
  (e.g. "region ∈ user.regions") and compiled into the query plan.
- **Field-level restriction interacts with aggregation.** Denying a user the
  `Compensation.Amount` *field* is pointless if they can query a metric that aggregates
  it. Field classification must propagate into metric authorization.

## Decision

### 1. Authentication: OIDC, delegated, with per-tenant identity providers

- **We are not an identity provider.** No password storage, no password reset, no MFA
  implementation. Authentication is delegated to an OIDC provider.
- Per-tenant IdP configuration: enterprise tenants bring their own (Entra ID, Okta, Google
  Workspace, Ping). SAML is supported via the IdP-broker layer rather than implemented in
  our application code — SAML is a protocol best consumed through a hardened
  implementation, not hand-rolled.
- Tenant resolution comes from the **authenticated principal's** tenant membership, never
  from a header, subdomain, or request body (ADR-003). A subdomain may *hint* which IdP to
  use; it never *decides* which tenant's data is served.
- Sessions: short-lived access tokens, refresh handled server-side, `HttpOnly`/`Secure`/
  `SameSite` cookies for the browser. The browser never holds a long-lived credential.
- **Machine access** (partner integrations, principle 8) uses OAuth client credentials
  with scoped service principals — the same authorization pipeline, different principal
  kind. Never a shared static API key.
- Just-in-time provisioning on first sign-in, with SCIM deferred to enterprise hardening.

### 2. Authorization: four composed layers, evaluated in one place

```
Principal (user or service, in exactly one TenantContext)
   ↓ 1. ROLE          → coarse capability grants
   ↓ 2. RESOURCE ACL  → per-object grants (metric, dashboard, data source, domain)
   ↓ 3. FIELD POLICY  → semantic-field classification vs. principal clearance
   ↓ 4. ROW POLICY    → semantic predicates injected into the query plan
   = EffectiveAuthorizationScope   (hashed into every cache key — ADR-007)
```

**Layer 1 — Roles.** Baseline roles shipped by the platform: `platform_admin` (our staff,
outside tenants, heavily audited), `tenant_admin`, `data_steward`, `metric_owner`,
`executive`, `analyst`, `viewer`. Roles grant *capabilities* (`metric.publish`,
`source.create`, `mapping.approve`), not object access. Tenants may define custom roles as
configuration — a composition of platform capabilities, never new code.

**Layer 2 — Resource ACLs.** Grants of `view`/`edit`/`manage` on specific metrics,
dashboards, data sources, and domains, to a role, group, or principal. Default deny.
Grouping by `Domain` keeps this manageable ("Finance can see Finance metrics").

**Layer 3 — Field policy.** Every `SemanticField` carries a `classification`
(ADR-005). A principal has a clearance set. Access to a field requires clearance ≥
classification. Critically:

> **A metric inherits the maximum classification of every semantic field its AST
> references, transitively.** Authorizing a metric therefore authorizes its inputs.

This closes the aggregate leak: a user without `restricted` clearance cannot query
`gross_margin` if it derives from `Cost.Amount`. Where a metric is *deliberately*
publishable at a lower classification than its inputs (an aggregate that is not
disclosive), that is an explicit, audited **declassification** on the metric version,
approved by a human — not an accident.

**Layer 4 — Row policy.** Policies are declared against **dimensions**, not tables:

```
RowPolicy
  applies_to : role | group | principal
  dimension  : region
  operator   : in
  values     : static list | principal attribute (from IdP claims) | lookup table
```

The governed query compiler resolves each policy's dimension to a join path (ADR-005) and
injects the predicate into the plan **before** execution (ADR-007). Policies compose with
AND across dimensions and OR within a dimension. If a policy's dimension is not reachable
from the queried metric, the query is **denied**, not silently allowed — fail closed.

Where the analytical engine supports RLS, the same predicates are additionally applied at
the database level as defense in depth.

### 3. Enforcement is centralized, and it is checked

There is exactly **one** authorization service. It produces an `EffectiveAuthorizationScope`
that is required as a typed argument by the governed query compiler — so a plan
*cannot be constructed* without it. This makes "authorize before data access" a type-level
property rather than a review-time hope. An architecture test asserts the compiler has no
constructor path that omits it.

The assistant uses the identical path (`06_AI_CHAT_ARCHITECTURE.md` step 3) — it is not a
privileged caller. The LLM never participates in an authorization decision; it receives
only what the principal was already entitled to see.

### 4. Error behaviour

Unauthorized access to an object returns the **same** response as a non-existent object.
Distinguishing them leaks the existence of metrics, dashboards, and data sources — a real
information disclosure in a product where metric names describe business strategy.

### 5. Delegated administration and separation of duties

- Publishing a governed object requires `*.publish`; **the author cannot be the sole
  approver** where the tenant enables separation of duties (principle 6). This is
  configuration, defaulted on for `restricted`-classified objects.
- `platform_admin` access to tenant data requires an explicit, time-bounded, reason-logged
  elevation ("break-glass"), which emits a high-severity audit event and notifies the
  tenant admin. Support staff must not have standing access to customer revenue data.

## Alternatives Considered

- **Build authentication in-house.** Rejected without hesitation. Password handling, MFA,
  session security, and SAML are solved problems with expensive failure modes.
- **RBAC only (no row or field policy).** Rejected — insufficient for compensation,
  margin, and regional restrictions, all of which are certain requirements for the target
  buyer.
- **ABAC/policy engine only (OPA, Cedar, Casbin) with no roles.** Rejected as the sole
  model: policy-as-code is powerful but opaque to tenant admins, who must be able to
  answer "who can see margin?" from a UI. Roles + ACLs are legible; policies handle the
  cases roles cannot express. **An embedded policy engine remains a candidate for the
  layer-4 evaluator** if policy complexity grows — the interface is designed to allow it.
- **Row security via database RLS on the analytical store only.** Rejected as primary:
  our policies are semantic and require joins; physical RLS cannot express them and would
  break on every binding change. Retained as a secondary backstop.
- **Post-filtering results by permission.** Rejected — aggregates leak (ADR-007).
- **Per-tenant permission models.** Rejected — that is tenant-specific code by another
  name (principle 1). Custom roles are compositions of platform capabilities.

## Rationale

The composition order matters more than any individual layer: roles gate *what you can
do*, ACLs gate *which objects*, field policy gates *which measures*, row policy gates
*which slice*. Each is enforced in one place, and the output is a single hashable scope
object that flows into the query plan and the cache key. That single object is what makes
it possible to reason about — and test — the whole model.

Classification inheritance through the metric AST is the non-obvious requirement. Without
it, every other layer is theatre: an aggregate is a perfectly good exfiltration channel.

## Consequences

- Positive: no credential storage; enterprise SSO is a configuration exercise.
- Positive: authorization is impossible to skip by construction (typed argument).
- Positive: row and field policies are expressed in business terms, so they survive
  source-system changes.
- Positive: cache correctness follows from the scope hash (ADR-003/007).
- Negative: four layers is genuinely complex; the admin UI must make effective permissions
  inspectable ("what can this user see?") or it will be misconfigured.
- Negative: classification inheritance will surprise users ("why can't I see this
  metric?"); the denial message must name the reason without naming the restricted field.
- Negative: IdP-per-tenant configuration is real onboarding work.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Row policy silently not applied to a query path | `row_scope_applied` flag in every envelope; test asserting deny-when-unreachable | Fail closed; scope required by the compiler's type signature |
| Aggregate leaks restricted fields | Classification-inheritance tests over metric ASTs | Inheritance is computed, not declared; declassification is explicit and audited |
| Cache serves one user's scope to another | Differential two-user test in CI | `auth_scope_hash` mandatory in the key |
| Effective-permission misconfiguration | "Explain access" tooling; periodic access reviews | Admin UI shows resolved effective scope per user |
| IdP claim spoofing / mis-mapped groups | Claim-mapping is per-tenant configuration and audited | Validate issuer, audience, signature; pin per-tenant issuer |
| Platform-admin standing access to tenant data | Elevation audit review | Break-glass only, time-bounded, tenant-notified |
| Token replay / session fixation | Standard session telemetry | Short-lived tokens, rotation, `HttpOnly`+`SameSite`, server-side refresh |
| Denial messages leak object existence | Error-taxonomy security test | Uniform not-found-or-not-permitted response |

## Future Considerations

- SCIM provisioning and group sync for enterprise tenants.
- An embedded policy engine (Cedar/OPA) for layer 4 if predicate complexity outgrows the
  declarative model.
- Purpose-based access (access granted for a stated purpose, logged) for regulated
  tenants.
- Customer-managed break-glass approval, where tenant admins approve support access.
- Attribute-driven policies sourced from the tenant's own HR/org data via a connector —
  natural once the semantic layer holds an org dimension.
