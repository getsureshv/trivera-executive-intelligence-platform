# ADR-003: Multi-Tenant Architecture and Tenant Isolation Strategy

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`07_SECURITY_MULTITENANCY_GOVERNANCE.md` mandates tenant isolation everywhere but
explicitly defers the isolation strategy — "schema-per-tenant, row-level, or hybrid" — to
a Phase 0 ADR. This is that ADR.

The decision must satisfy conflicting pressures:

- **Operational scale.** Schema-per-tenant means every Alembic migration fans out across
  every tenant schema. At 50 tenants this is annoying; at 500 it is a permanent
  reliability hazard and a deployment bottleneck.
- **Blast radius.** A single missing `WHERE tenant_id = ...` in a shared-schema design is
  a cross-tenant data breach. In an executive intelligence platform holding revenue,
  margin, pipeline, and compensation data, that is an existential failure, not a bug.
- **Enterprise procurement.** Large customers ask, in writing, whether their data shares a
  table with another customer's. "Yes, but we filter carefully" loses deals.
- **Deletion, export, residency.** GDPR erasure, tenant offboarding, and EU data residency
  are dramatically simpler when a tenant's analytical data is a droppable, relocatable
  container.

Critically, the *metadata* (control plane) and the *analytical data* (data plane) have
different profiles. Metadata is small, highly relational, migrated constantly, and
queried by the application. Analytical data is large, schema-varying per tenant, migrated
rarely, and queried by a generated compiler. Applying one strategy to both is the mistake
most platforms make.

## Decision

**A hybrid: pooled control plane, siloed data plane.**

### 1. Metadata / control plane — shared database, shared schema, `tenant_id` + PostgreSQL RLS

- Every tenant-owned table carries a non-null `tenant_id` with a foreign key to `tenant`.
- Every tenant-owned table has **Row-Level Security enabled and forced**
  (`ALTER TABLE ... FORCE ROW LEVEL SECURITY`) with a policy of the form
  `tenant_id = current_setting('app.tenant_id')::uuid`.
- The application connects as a **non-superuser, non-table-owner role** so RLS cannot be
  bypassed.
- `app.tenant_id` is set with `SET LOCAL` at the start of every transaction by a single
  session-checkout hook driven by the request/job tenant context. There is exactly one
  place in the codebase that sets it.
- ORM-level scoping remains the primary mechanism; **RLS is the backstop.** Defense in
  depth: the primary mechanism catches bugs early and gives good errors; the backstop
  makes a forgotten filter return zero rows instead of another tenant's rows.
- A small set of genuinely global tables (`tenant`, connector type registry, platform
  migrations) is explicitly enumerated and exempt.
- **Cross-tenant queries are impossible from application code.** Platform-level
  aggregate reporting uses a separate, audited role and a separate code path.

### 2. Analytical data plane — schema-per-tenant

- Each tenant's ingested and materialized analytical data lives in its own namespace
  (a dedicated PostgreSQL schema now; a dedicated database/catalog in a future engine).
- Naming: `tenant_<tenant_slug>` — resolved from tenant context, never concatenated from
  user input.
- The governed query compiler emits fully-qualified, schema-bound identifiers; the
  analytical connection role is granted `USAGE` on **only** the current tenant's schema.
- Tenant offboarding is `DROP SCHEMA ... CASCADE` plus object-storage prefix deletion.
- Per-tenant retention, sizing, and residency become configuration, not code.

### 3. Tenant context propagation

- Tenant context is resolved **once at the edge** (from the authenticated principal, not
  from a header, subdomain, or request body) into an immutable `TenantContext`.
- It propagates through an explicit context object — request state, job payload, and
  worker context. There is **no ambient global** that can be forgotten or stale.
- **No repository, query, cache lookup, object-storage access, or job may execute without
  a resolved `TenantContext`.** This is enforced by requiring the context as an argument
  in the data-access layer's type signatures, not by convention.
- Tenant id is attached to every log record, span, and metric as a first-class attribute.

### 4. Cache and object storage

- Cache keys are structured, not string-concatenated:
  `tenant_id | config_version | metric_version | normalized_params | auth_scope_hash`.
  The **`auth_scope_hash` is mandatory** — see Risks; caching a row-level-secured result
  under a key that omits the caller's effective row/field scope is a cross-*user*
  data leak inside a tenant.
- Object-storage paths are `s3://<bucket>/t/<tenant_id>/...` with IAM policies scoped by
  prefix where the provider supports it.

### 5. Tenant tiers (defined now, built later)

- **Tier 1 — Pooled (default):** as above.
- **Tier 2 — Dedicated analytical database:** same binaries, tenant's analytical schema
  moved to a dedicated instance. Configuration only.
- **Tier 3 — Siloed deployment:** an entire dedicated stack for a regulated or very large
  tenant. Same artifacts, separate infrastructure. **Not built in the near term**; the
  architecture must simply not preclude it, which it does not.

## Alternatives Considered

- **Shared schema, `tenant_id` only, no RLS.** Rejected. It makes correctness depend
  entirely on every developer and every generated query remembering a filter, forever.
  The cost of RLS (a session variable and a policy per table) is trivial against the
  consequence of a single miss.
- **Schema-per-tenant for everything, including metadata.** Rejected. Migration fan-out
  across hundreds of schemas becomes the dominant operational risk and blocks continuous
  deployment. Connection-pool efficiency also degrades (search_path churn or a pool per
  tenant). The isolation benefit for *metadata* is small — metadata is configuration, not
  the customer's business data.
- **Database-per-tenant for everything.** Rejected as a default for the same reasons,
  amplified: connection exhaustion, migration fan-out, and per-tenant infrastructure cost
  before there is per-tenant revenue to fund it. Retained as Tier 2/3.
- **Full silo per tenant from day one.** Rejected. It converts a software problem into an
  operations problem before there is an operations team, and it removes the pooled
  economics the multi-tenant product thesis depends on.
- **Application-level "tenant filter" middleware over a shared schema.** Rejected as a
  *sole* mechanism; adopted as the *primary* mechanism with RLS behind it.

## Rationale

Isolation guarantees should be strongest where the sensitive data actually is. The
customer's revenue rows are in the data plane; the control plane holds definitions and
configuration. Siloing the data plane buys the procurement answer, the deletion story,
the residency story, and the real blast-radius reduction — and it costs little, because
analytical schemas are created by our own provisioning code and migrated far less often
than metadata.

Pooling the control plane buys single-migration deployment, simple cross-cutting queries
for platform operations, and connection-pool efficiency — and its residual risk is
neutralized by database-enforced RLS.

This split is the point of the decision: it is not a compromise between two options, it
is recognizing that there are two different problems.

## Consequences

- Positive: one migration run for metadata; database-enforced isolation backstop; a
  credible answer to enterprise security review; trivially auditable deletion.
- Positive: per-tenant analytical retention/sizing/residency becomes configuration.
- Positive: Tier 2/3 upgrades require no code changes.
- Negative: analytical schema provisioning and per-tenant analytical DDL become a
  first-class subsystem with its own migration mechanism (separate from Alembic's
  metadata migrations). This must be built in Phase 1, not improvised later.
- Negative: RLS adds a small per-query planning cost and complicates connection pooling
  (`SET LOCAL` must be transaction-scoped; pooled connections must never leak
  `app.tenant_id` across checkouts).
- Negative: two isolation models means engineers must know which plane they are in.
  Mitigated by separate, clearly named session/engine factories.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| **Cache key omits authorization scope → intra-tenant leak** (highest-severity design risk found in Phase 0) | Key-construction is a single typed function; unit tests assert scope inclusion; fuzz test with two users of differing scope | Cache key type has no string constructor; `auth_scope_hash` is a required field |
| `app.tenant_id` leaks across pooled connections | Integration test: checkout, set, return, checkout, assert unset | `SET LOCAL` inside an explicit transaction; reset hook on connection return; assertion in checkout hook |
| A table is added without `tenant_id` or without an RLS policy | Migration lint: every new table must be declared global or tenant-scoped; a test enumerates `pg_policies` vs. the model registry | CI test fails on any tenant-scoped table lacking FORCE RLS |
| Application connects as table owner, silently bypassing RLS | Startup assertion that `current_user` is not the owner and not superuser | Fails fast on boot |
| Analytical schema name injection | Schema names derived from a UUID/slug in the tenant record only | Identifier quoting + allowlist regex; never from request input |
| Tenant context missing in a background job | Job payload schema requires `tenant_id`; worker refuses jobs without it | Typed job envelope (ADR-009) |
| Cross-tenant aggregation code path abused | Separate role, separate module, every use audited | Requires explicit platform-admin permission and emits an audit event |

## Future Considerations

- **Tenant hierarchies** (a holding company with subsidiaries) are *not* modeled as
  nested tenants. Provisional position: a tenant is one isolation and billing boundary;
  organizational structure inside it is a `Dimension`. This needs product-owner
  confirmation (see `17_PHASE_0_ARCHITECTURE_REVIEW.md`).
- Per-tenant encryption keys (BYOK/CMEK) fit the siloed data plane naturally; deferred
  until a customer requires it.
- Regional deployments for data residency: the siloed data plane makes per-region tenant
  placement feasible; the pooled control plane would need regional shards. Decide when the
  first EU customer is real.
- If tenant count grows past the point where a single metadata database is comfortable,
  shard the control plane by tenant — the `TenantContext` seam already makes routing
  possible.
