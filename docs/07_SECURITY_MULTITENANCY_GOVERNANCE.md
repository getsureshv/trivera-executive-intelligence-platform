# 07 — Security, Multi-Tenancy & Governance

Security is a first-class requirement, designed in from day one, not a later hardening
pass (principle 9). This document covers tenant isolation, access control, secrets,
data protection, and configuration versioning/governance.

## Tenant isolation

The platform is multi-tenant from day one. Every piece of state and every operation is
tenant-scoped:

- Identities and sessions resolve to exactly one tenant context per request.
- Metadata (configuration, semantic model, metrics) is partitioned by tenant.
- Analytical data is isolated by tenant.
- Cache keys are prefixed by tenant; no cross-tenant cache hits are possible.
- Object-storage paths are prefixed by tenant.
- Logs, traces, and metrics carry tenant context.

Tenant context is established at the edge and propagated through every layer. No query,
job, or cache lookup runs without a resolved tenant. Cross-tenant access is impossible by
construction.

> **Phase 0 decision ([ADR-003](adr/ADR-003-multi-tenant-architecture.md)) — hybrid:
> pooled control plane, siloed data plane.**
>
> - **Metadata:** shared database, shared schema, `tenant_id` on every table, with
>   PostgreSQL **Row-Level Security enabled and FORCED**, the application connecting as a
>   non-owner, non-superuser role, and `app.tenant_id` set via `SET LOCAL` by a single
>   session-checkout hook. ORM scoping is primary; RLS is the backstop, so a forgotten
>   filter returns zero rows rather than another tenant's. Rationale: schema-per-tenant
>   would make every migration a fan-out across hundreds of schemas — the dominant
>   operational risk — for little isolation benefit on what is, after all, configuration.
> - **Analytical data:** **schema-per-tenant**. This is where the customer's actual
>   business data lives, so it is where blast radius, deletion (`DROP SCHEMA`), per-tenant
>   retention/sizing/residency, and the enterprise-procurement answer all matter.
> - **Tiers:** dedicated analytical instance (Tier 2) and fully siloed deployment
>   (Tier 3) are *configuration*, not forks. Not built until a customer requires them.
> - **Tenant context is resolved from the authenticated principal only** — never from a
>   header, subdomain, or request body — and is a required typed argument in the
>   data-access layer, not an ambient global.
>
> **Cache keys are not merely tenant-prefixed.** The structured key is
> `tenant_id | config_version | metric_version | plan_hash | normalized_params |
> auth_scope_hash | data_snapshot_id`. **`auth_scope_hash` is mandatory**: with row-level
> security and field restrictions also specified below, a tenant-only key means two users
> in the same tenant with different scopes collide — an intra-tenant data leak. This was
> the most severe latent defect found in Phase 0 review. See
> [ADR-007](adr/ADR-007-governed-query-engine.md) §4.

## Identity and access control

Document and implement:

- **OIDC / OAuth** for authentication.
- **SAML support** where enterprise customers require it.
- **RBAC** — roles and permissions as the baseline model.
- **Policy-based access** — attribute/condition-based policies layered on top of roles
  for finer control.
- **Row-level security** — restrict which rows a principal can see within a tenant.
- **Semantic-field restrictions** — restrict access to sensitive semantic fields (e.g.
  compensation, margin).
- **Metric permissions** — govern who can view or manage specific metrics.
- **Dashboard permissions** — govern who can view or edit specific dashboards.

Authorization is enforced **before** data is accessed, on both the dashboard and the
assistant surfaces (see [`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)).

> **Phase 0 update ([ADR-010](adr/ADR-010-authentication-and-authorization.md)):** the
> list above is right; the mechanics were missing.
>
> - Authentication is **delegated** — we are not an identity provider. OIDC per tenant;
>   SAML via a broker, never hand-rolled.
> - The four layers compose in one place into a single **`EffectiveAuthorizationScope`**,
>   which is a *required typed argument* of the governed query compiler — so authorization
>   cannot be skipped by construction, and its hash flows into the cache key.
> - **Row-level security is expressed semantically** (`region ∈ user.regions`), against
>   dimensions rather than physical tables, and the predicate is **injected into the query
>   plan before execution**. Post-filtering leaks through aggregates. If a policy's
>   dimension is unreachable from the queried metric, the query is **denied** — fail
>   closed.
> - **A metric inherits the maximum classification of every semantic field its AST
>   references.** Without this, denying a field is theatre: an aggregate is a perfectly
>   good exfiltration channel. Deliberate declassification is explicit and audited.
> - Unauthorized and non-existent objects return the **same** response; distinguishing
>   them leaks the existence of metrics whose names describe business strategy.
> - Platform staff have **no standing access** to tenant data — time-bounded, reason-logged
>   break-glass only, with tenant notification.

## Audit

- **Audit logs** capture security- and governance-relevant events: sign-in, permission
  changes, configuration changes, metric approvals/publishes, data-source changes, and
  data access to sensitive fields.
- Audit records are tenant-scoped, tamper-evident, and retained per policy. They feed
  the Audit / Governance context (see
  [`03_PLATFORM_ARCHITECTURE.md`](03_PLATFORM_ARCHITECTURE.md)).

> **Phase 0 update ([ADR-014](adr/ADR-014-observability.md) §5):** the audit trail is a
> **separate, durable, append-only store in PostgreSQL — not a log stream.** It must be
> transactionally consistent with the change it records (same transaction, or via the
> outbox), queryable by tenant admins as a product feature, and retained on a compliance
> schedule that differs from telemetry. Log pipelines drop records under load; an audit
> trail that drops records is not an audit trail.
>
> "Tamper-evident" is made concrete: no `UPDATE`/`DELETE` grant to the application role,
> each row hash-chained to its predecessor within the tenant, and periodic checkpoint
> hashes exported to write-once storage.
>
> Separately, telemetry uses an **attribute allowlist** — metric values, dimension values,
> source field values, credentials, and raw prompts/completions are never emitted.

## Secrets management

Use an **external secret store** abstraction for all credentials and keys. Never place
credentials or secrets in:

- source code
- logs
- prompts
- Git
- normal metadata

Connector credentials use **least privilege** — read-only and scoped to what discovery
and extraction require (see
[`04_DATA_CONNECTORS_SEMANTIC_LAYER.md`](04_DATA_CONNECTORS_SEMANTIC_LAYER.md)).

> **Phase 0 update ([ADR-015](adr/ADR-015-secrets-management.md)):** the design goal is
> that **a full compromise of the metadata database yields zero customer credentials.** A
> `DataSource` row holds a `SecretRef` — a pointer plus version — never a value and never a
> ciphertext, so database dumps, backups, and replicas contain no credential material at
> all.
>
> `SecretValue` is a **type** whose repr/serialization emit `***`, which cannot be
> JSON-serialized, and which logging, telemetry, and prompt-construction functions do not
> accept — because the overwhelming majority of real credential leaks are a
> `logger.debug(config)`, not exfiltration. Retrieval is purpose-scoped and audited; **no
> human, including platform staff, can read a tenant secret value through the platform**
> (the UI is write-only, with no reveal).
>
> Credential preference order, best first: workload identity (no secret exists) →
> short-lived tokens → **customer-deployed agent (the credential never leaves their
> network)** → long-lived static credentials. The best way to protect a secret is not to
> hold it.

## Data protection

- **TLS** for data in transit.
- **Encryption at rest** for metadata, analytical data, and object storage.
- **PII / financial classification** — sensitive semantic fields are classified so that
  masking, restriction, and audit can be applied consistently and so the assistant and
  dashboards honor those restrictions.

## Configuration versioning

Governed configuration is versioned through a clear lifecycle. Every versionable object
moves through:

- **Draft** — being edited; not visible to consumers.
- **Published** — the active, governed version.
- **Archived** — retired but retained for history and rollback.

The following are versioned:

- semantic entities
- mappings
- transformations
- metrics
- targets
- thresholds
- dashboards

For every version, store:

- **change reason** — why the change was made.
- **author** — who made it.
- **approver** — who approved it (human approval for governed semantic changes,
  principle 6).
- **published date** — when it went live.
- **rollback metadata** — what is needed to revert safely.

This makes every governed change explainable and reversible, and ties directly to the
audit trail. It is the governance backbone that lets executives trust that a number's
definition did not silently change underneath them.

> **Phase 0 update ([ADR-013](adr/ADR-013-configuration-versioning.md)):** per-object
> versioning is retained as the unit of *editing*, but it **cannot express a coherent
> system state**. Rolling back a mapping without the metric that depends on it produces an
> inconsistent configuration, there is no identifier answering "what configuration
> produced this number?", and a steward mid-edit across five related objects cannot publish
> them atomically.
>
> An **immutable, atomic, per-tenant `ConfigurationBundle`** is therefore added as the unit
> of *release*: a monotonic `config_version` pinning the exact version of every governed
> object. Publishing is all-or-nothing; **rollback is activating bundle N−1**, an instant
> pointer move with no reverse migration to get wrong. Every query result, observation,
> insight, and assistant answer records the `config_version` it was computed under.
>
> This also brings configuration the practices code already has: **draft workspaces** with
> preview against real data, **diff and review**, **validation as a publish gate** (binding
> validation, metric validation, acceptance assertions, impact acknowledgement), and
> **export as a signed, portable artifact** — which is what makes industry KPI packs and
> environment promotion possible. Secrets are never in a bundle, only `SecretRef`s
> ([ADR-015](adr/ADR-015-secrets-management.md)), so export is safe by construction.
>
> **Version** (when the definition changed) and **effective date** (the business period a
> definition applies to) are modelled as distinct concepts — required for a correct
> year-over-year comparison across a definition change.

## Governance and human approval

AI may suggest semantic mappings and metric definitions, but a **human approves** before
anything is published. Publishing is a governed action: it records author, approver,
reason, and version, and emits an audit event. This is how the platform keeps a fast,
AI-assisted configuration loop without sacrificing trust.
