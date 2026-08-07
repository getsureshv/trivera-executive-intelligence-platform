# ADR-015: Secrets Management

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`07_SECURITY_MULTITENANCY_GOVERNANCE.md` requires an external secret-store abstraction and
forbids secrets in source code, logs, prompts, Git, and ordinary metadata (guardrail 9).
Connector credentials must be least-privilege.

Phase 0 must make this concrete, because "use a secret manager" is not a design. Two
categories of secret exist and they have genuinely different requirements:

- **Platform secrets** — our database passwords, signing keys, LLM API keys, object-storage
  credentials. Few, rotated on our schedule, held by our infrastructure.
- **Tenant secrets** — a customer's database password, API token, OAuth refresh token, or
  private key for a data source. Many (per tenant, per source), supplied by the customer,
  rotated on *their* schedule, and among the most sensitive things we will ever hold. A
  breach here is a breach of the customer's production systems, not just of our platform.

The design must also survive a scenario the documentation does not consider: a customer
asking "if your platform is compromised, can the attacker read our production database?"
The honest answer depends entirely on decisions made here.

## Decision

### 1. A `SecretStore` port; secrets are referenced, never stored

```
SecretStore
  put(tenant_id, logical_name, value, metadata) -> SecretRef
  get(tenant_id, ref, purpose) -> SecretValue        # short-lived, in-memory only
  rotate(tenant_id, ref, new_value) -> SecretRef     # new version
  delete(tenant_id, ref)
  describe(tenant_id, ref) -> SecretMetadata         # never the value
```

- **No secret value is ever stored in the metadata database.** A `DataSource` row holds a
  `SecretRef` (a pointer plus version), never a ciphertext and never a value. This means a
  metadata database dump — the most likely accidental disclosure, via backups, logs, or a
  SQL injection — contains no credentials at all.
- The port has an obvious implementation per environment: cloud KMS-backed secret managers
  in production, a local development implementation for laptops. No vendor SDK outside the
  adapter (ADR-001 import contracts).
- Secret paths are tenant-namespaced (`/eip/<env>/tenants/<tenant_id>/<logical_name>`) with
  IAM policies scoped by path prefix where the provider supports it, so the blast radius of
  a compromised role is bounded.

### 2. Secrets are typed and never stringly-handled in the codebase

A `SecretValue` type whose `__str__`/`__repr__`/serialization emit `***`, which cannot be
JSON-serialized, and which is not accepted by logging, telemetry, or prompt-construction
functions (enforced by type signatures). Values are fetched at point of use, held for the
minimum duration, and never cached to disk.

This matters more than it sounds: the overwhelming majority of real credential leaks are
not exfiltration, they are a `logger.debug(config)` or an exception whose message includes
a connection string. Making that a type error is far more reliable than making it a review
comment.

### 3. Access is authorized, purposeful, and audited

- Only the connector runtime may retrieve tenant data-source secrets, and only during an
  operation that legitimately needs them (`test_connection`, `discover`, `extract`).
- Every retrieval passes a `purpose` and emits an audit event
  (`tenant, ref, purpose, actor, pipeline_run_id, timestamp`) — value never included.
- **No human, including platform staff, can read a tenant secret value through the
  platform.** The UI is write-only and update-only for secrets; there is no reveal.
  Retrieval by staff requires a break-glass path in the underlying secret manager, outside
  the application, with its own audit trail (ADR-010).
- Rotation creates a new version; the previous version remains briefly for in-flight jobs,
  then is destroyed.

### 4. Prefer credentials we cannot leak

Ordered preference for connecting to a customer system:

1. **Workload identity / IAM roles** (cloud-native sources) — no secret exists.
2. **Short-lived tokens** obtained per operation (OAuth with refresh, STS-style).
3. **Customer-side agent** (ADR-004 mode 3) — the credential never leaves the customer's
   network at all; we hold nothing. This is the strongest answer to the "if you are
   compromised" question and is a genuine competitive argument, not merely a security
   control.
4. **Long-lived static credentials** — supported, least preferred, and flagged in the UI
   with a recommendation to use a read-only, scoped account.

Least privilege is verified where possible: the connector's `authorization` diagnostic
(ADR-004) reports if the supplied principal has write privileges, and the UI warns.

### 5. Platform secrets

Injected at runtime from the secret manager (never baked into images, never in
environment files committed anywhere). Rotation is automated where the provider supports
it, with documented runbooks where it is not. Signing keys support overlapping validity so
rotation does not invalidate live sessions.

### 6. Configuration bundles never contain secrets

`ConfigurationBundle` export (ADR-013) contains `SecretRef`s only, so a bundle can be
diffed, reviewed, promoted between environments, and handed to a customer without
disclosure risk. This is by construction, not by filtering — bundle scope excludes
connection credentials entirely.

### 7. Detection in depth

- Pre-commit and CI secret scanning over the repository and its history.
- Automated scanning of log output in tests for credential-shaped patterns.
- Telemetry attribute allowlisting (ADR-014) blocks accidental emission.
- A documented, rehearsed leaked-credential response: revoke at the source, rotate,
  invalidate sessions, notify the tenant, and audit the exposure window.

## Alternatives Considered

- **Encrypted columns in PostgreSQL (application-level envelope encryption).** Seriously
  considered — it is simpler and removes an infrastructure dependency. Rejected because
  the data-encryption key must then live somewhere anyway (so a KMS is still required),
  and because it puts ciphertext into database backups, replicas, and dumps, widening
  exposure to every place a database copy exists. A dedicated secret manager keeps
  credentials out of the database's entire lifecycle.
- **Cloud provider secret manager referenced directly (no port).** Rejected — vendor
  coupling in a cross-cutting concern (guardrail 15), and it makes local development
  awkward.
- **HashiCorp Vault self-hosted from day one.** Rejected initially on operational cost;
  the port makes it a drop-in later, and it is the natural choice if we need dynamic
  database credentials or multi-cloud portability.
- **Per-tenant KMS keys (BYOK) now.** Deferred. The path-namespaced design accommodates it;
  building it before a customer requires it is speculative.
- **Storing secrets in the metadata DB "temporarily" to move faster in Phase 1.**
  Explicitly rejected and called out here, because it is the shortcut that will be
  proposed. Retrofitting secret extraction after connectors exist means touching every
  connector, every migration, and every backup — and living with historical backups that
  contain customer production credentials indefinitely.

## Rationale

The design goal is that **a full compromise of our metadata database yields zero customer
credentials**. That single property drives the reference-not-value decision, the typed
`SecretValue`, the write-only UI, and the bundle exclusion. It is also the property that
makes the enterprise security questionnaire answerable.

The preference ordering in §4 reflects a principle worth stating plainly: the best way to
protect a secret is not to hold it. Workload identity and the customer-side agent are
architecture decisions with security consequences, not security features bolted onto an
architecture.

## Consequences

- Positive: metadata dumps, backups, and replicas contain no credentials.
- Positive: leaking a secret into a log or prompt is a type error, not a review miss.
- Positive: every secret access is attributable.
- Positive: bundle export is safe by construction.
- Negative: an additional infrastructure dependency, on the critical path for ingestion —
  secret-manager unavailability stops extraction. Requires caching of short duration in
  memory and clear degradation behaviour.
- Negative: local development requires a working local implementation.
- Negative: per-fetch latency and secret-manager API cost at ingestion scale; mitigated by
  short-lived in-memory caching within a pipeline run.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Secret leaks into logs, errors, or prompts | Log scanning in CI; telemetry allowlist | Typed `SecretValue` that cannot be serialized or logged |
| Secret manager outage halts ingestion | Availability monitoring | In-run in-memory caching; retry with backoff; clear pipeline error state |
| Over-privileged customer credential | `authorization` diagnostic reports write access | UI warning; documented least-privilege setup per connector |
| Compromised worker exfiltrates tenant credentials | Anomalous retrieval-rate alerting | Purpose-scoped retrieval, audited; prefer short-lived tokens and the customer-side agent |
| Rotation breaks in-flight jobs | Job failure rate on rotation | Overlapping versions; jobs pin a secret version for the run |
| Someone stores a secret in the metadata DB "temporarily" | Schema review; a CI check for credential-shaped column names | This ADR; code review; the `SecretRef` type is the only accepted shape |
| Path-scoping misconfiguration allows cross-tenant secret access | IAM policy tests in CI | Tenant-namespaced paths with prefix-scoped policies |

## Future Considerations

- Dynamic, short-lived database credentials (Vault-style) — eliminating standing
  credentials entirely for capable sources.
- Per-tenant KMS keys (BYOK/CMEK) for regulated customers.
- The customer-side connector agent, which removes credential custody from us altogether.
- Automated rotation orchestration with customer notification.
- Hardware-backed key storage for signing keys.
