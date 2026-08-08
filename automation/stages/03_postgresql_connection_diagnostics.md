# Stage 03 — Secret-backed PostgreSQL connection diagnostics

## Authority

Required by the Phase 2 connector slice in the accepted roadmap, ADR-004, ADR-015,
PO-001, and PO-002. PostgreSQL is the approved first connector.

## Assignment

Implement the smallest concrete PostgreSQL connector connection-test path:

- declare PostgreSQL capabilities and a non-secret configuration schema;
- accept a `ConnectionTarget` containing only an opaque SecretRef;
- retrieve the credential at point of use through the existing `SecretStore` with purpose
  `test_connection`; never persist, log, serialize, or return the value;
- apply the Stage 2 endpoint policy and selected-peer revalidation;
- execute ordered diagnostics: network, TLS, authentication, authorization,
  metadata access, latency; after one failure, every later check is skipped;
- use stable non-secret error codes and remediation text;
- check that metadata is readable and report whether the login has write-capable database
  privileges without mutating the source;
- verify success and representative failures against a real PostgreSQL container.

Keep PostgreSQL-specific code inside the connectivity adapter. Use SQLAlchemy's async
surface so application code does not import a database driver directly. Do not persist a
DataSource or ConnectionTest, add API endpoints/jobs, perform discovery/extraction, or
start semantics, metrics, dashboards, insights, or AI.

## Verification gate

- Ruff format/lint and strict mypy
- connector unit/security tests with secret-redaction assertions
- real PostgreSQL success, authentication-failure, authorization, metadata, and latency
  diagnostic execution; zero skips
- complete architecture and live PostgreSQL security suites
- independent diff review

