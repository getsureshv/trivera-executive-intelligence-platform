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
construction, and the isolation strategy chosen in Phase 0 (schema-per-tenant,
row-level, or hybrid) is recorded as an ADR.

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

## Audit

- **Audit logs** capture security- and governance-relevant events: sign-in, permission
  changes, configuration changes, metric approvals/publishes, data-source changes, and
  data access to sensitive fields.
- Audit records are tenant-scoped, tamper-evident, and retained per policy. They feed
  the Audit / Governance context (see
  [`03_PLATFORM_ARCHITECTURE.md`](03_PLATFORM_ARCHITECTURE.md)).

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

## Governance and human approval

AI may suggest semantic mappings and metric definitions, but a **human approves** before
anything is published. Publishing is a governed action: it records author, approver,
reason, and version, and emits an audit event. This is how the platform keeps a fast,
AI-assisted configuration loop without sacrificing trust.
