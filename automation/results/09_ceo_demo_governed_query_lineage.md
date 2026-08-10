# CEO demonstration Stage 2 result — governed query and derived lineage

Date: 2026-08-09
Result: **PASSED LOCALLY — awaiting commit and CI**

## Delivered

- Authenticated tenant-scoped executive dashboard, governed Revenue YTD query, and derived
  lineage routes over the immutable Stage 1 metadata.
- Authorization-before-read, forced-RLS tenant scoping, exact single-published-bundle
  resolution, closed AST revalidation, and fail-closed malformed/missing-state behavior.
- Lossless Decimal headline, prior comparison, target variance, ordered drill-down,
  reconciliation, persisted-rule attention selection, freshness, quality, and owner fields.
- Explicit provenance distinguishing seeded observations from a selected PostgreSQL
  DataSource's real successful current-version connection health.
- Request-time lineage traversal from widget through metric/semantic/binding/source metadata
  to DataSource; no stored projection or cache.
- Typed API/OpenAPI/TypeScript contracts and audit-safe, value-free event details.

## Independent review and repair

Codex reviewed the complete service, router, application composition, contracts, generated
OpenAPI, and security tests. The single focused repair made bundle selection deterministic,
completed drill-down/attention and trust provenance contracts, typed lineage edges, and
strengthened cross-tenant/malformed/leakage adversaries. Codex corrected two test-only setup
issues: isolated duplicate-fixture ordering and the exact transaction-local tenant context
required to mutate a malformed fixture under FORCE RLS. Production RLS and behavior were
unchanged.

## Observed verification

- Focused real PostgreSQL adversaries: 3 passed, 0 skipped.
- Complete real PostgreSQL API/integration/architecture/security suite: 394 passed, 0 skipped.
- Ruff format/lint: passed (85 files).
- Strict mypy: passed (56 source files).
- Prettier, TypeScript lint/typecheck, shared contracts, and diff integrity: passed.
- Web regression tests: 8 passed.
- Exact Decimal comparison/variance strings, drill-down reconciliation, attention selection,
  uniform cross-tenant absence, malformed persisted state, disabled source, provenance,
  lineage traversal, audit safety, and captured-log leakage scans executed against PostgreSQL.

No migration, persisted seed, model registry, UI, live source query, cache, discovery,
extraction, AI, or production deployment change was made. Commit/push/CI evidence is added
after those gates complete.
