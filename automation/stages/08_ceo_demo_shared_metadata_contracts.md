# Stage 1 of 4 — CEO demo shared metadata and contracts

## Owner and purpose

Claude Code implements this assignment. Codex independently reviews and verifies it.
Build only the frozen, tenant-safe metadata foundation for the explicitly seeded CEO
demonstration described in `docs/25_CEO_DEMONSTRATION_VERTICAL_SLICE_ENTRY_REPORT.md`.
Do not implement query APIs, lineage traversal, the executive UI, or release materials.

## Binding product configuration

- Calendar YTD in `America/Chicago`, as of `2026-08-11 17:00:00-05:00`.
- Accountable owner role: `Chief Revenue Officer`.
- Headline `4210500`, prior `3980000`, target `4500000`.
- People `1850000` / target `1900000`; Process `1410500` / target `1450000`;
  Technology `950000` / target `1150000`.
- The configured largest-negative-target-variance rule selects Technology.
- Every persisted and returned origin label is `Demo dataset / seeded demonstration data`
  with machine origin `seeded_demo`.

## Required implementation

1. Add one reversible migration after `0007_source_retention` and matching registered ORM
   models for the minimum configuration, semantic, governed-metric, observation, quality,
   freshness, dashboard/widget, and attention-rule entities accepted in report section 4.
   Consolidation is allowed only when it retains the relationships and future versioning
   boundary. Every tenant row has forced PostgreSQL RLS, least runtime grants, and
   same-tenant composite foreign keys. Monetary columns are `numeric`, never float.
2. Enforce published configuration immutability in PostgreSQL, not merely application code.
   Observations are append-only. Validate closed status/origin/type values, version identity,
   timestamps, hashes, uniqueness, and tenant relationships. Platform administrators gain no
   standing tenant capabilities.
3. Implement the closed typed metric AST whose only accepted expression is a sum of an
   authorized additive semantic field. Reject unknown keys/nodes, SQL or formula strings,
   unknown/non-additive fields, unreachable dimensions, unpublished configuration, naive
   timestamps, and binary floating-point values.
4. Add deterministic, idempotent seed and reset commands. Values and business labels may
   exist only in checked-in seed input/data, never migrations, Python service logic, React
   components, or schema defaults. Reset only the targeted tenant's demo dataset and is safe
   against cross-tenant deletion. The three segment observations and targets must reconcile
   exactly to the headline and total target with Decimal arithmetic.
5. Add closed capabilities and audit action constants needed by later read/query/lineage
   stages, shared typed contracts for the mandatory envelope/lineage boundary, model
   registry entries, and bounded-context tests. Do not expose new HTTP routes in this stage.
6. Freeze the migration chain, central model registry, shared contracts, and composition
   boundary at completion so later lanes do not edit them concurrently.

## Required tests and evidence

- Ruff format/lint and strict mypy for all touched Python packages.
- TypeScript formatting, lint, typecheck, and contract tests for touched packages.
- Migration upgrade, downgrade, model-drift, SQL checks, policies, forced RLS, ownership,
  and grants against the documented real PostgreSQL container; zero skips.
- Real PostgreSQL adversarial tests for two tenants, forged tenant identifiers, same-tenant
  foreign keys, published mutation/deletion refusal, append-only observations, and a
  tenant-scoped reset that cannot affect another tenant.
- Seed twice and reset/reseed, proving stable identities, no duplicates, exact Decimal
  reconciliation, approved timezone/as-of data, immutable published rows, and visible
  seeded-demo origin.
- Validator tests for every accepted and rejected AST boundary above.
- A source scan/test proving KPI values, segment labels, targets, and lineage field names do
  not appear outside checked-in seed fixtures/data and acceptance tests that assert them.
- Secret, log, and audit-shape scans; no credential or business values in logs/audit.

## Stop conditions and exclusions

Stop and return the saved patch for a new product choice, ADR conflict, security ambiguity,
failed mandatory verification, or scope expansion. Exclude live discovery, profiling,
extraction, ingestion, object storage, customer data, arbitrary SQL/formulas, caching,
alerts, general dashboards, insights, AI, new connectors, production deployment, and UI or
backend query/lineage work reserved for later stages.

Return an uncommitted patch and exact verification evidence to Codex. Do not commit or push.
