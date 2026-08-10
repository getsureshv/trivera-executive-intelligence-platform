# Stage 2 of 4 — Governed query and derived lineage backend

## Owner and purpose

Claude Code implements this assignment as the single integration owner for API composition
and any strictly required contract completion. Codex independently reviews and verifies it.
Build the authenticated, tenant-scoped, read-only backend over the frozen Stage 1 metadata.
Do not add migrations, change persisted seed values, implement UI, or query a source system.

## Required behavior

1. Add the three accepted routes:
   - `GET /v1/dashboards/executive`
   - `POST /v1/metrics/revenue_ytd/query`
   - `GET /v1/metrics/revenue_ytd/lineage?config_version={v}`
2. Every route authorizes the closed capability before reading definitions, observations,
   source metadata, or lineage. Use the server-derived tenant context and constrained tenant
   session. Forged tenant/resource/config identifiers must be indistinguishable from absent
   identifiers. Platform administrators have no standing tenant read path.
3. Resolve only the immutable published bundle and validate the persisted metric AST against
   published semantic metadata, field classification/additivity, and the configured allowed
   dimension before reading governed facts. The only query inputs are the exact calendar-YTD
   period and an optional configured `group_by`; reject SQL, formulas, unknown keys,
   arbitrary metrics, dimensions, periods, and naive timestamps.
4. Assemble the mandatory lossless envelope from persisted metadata and facts using Python
   `Decimal`: headline, prior value, absolute and percentage comparison, target, absolute and
   percentage target variance, unit/format, exact period/as-of, freshness, quality checks,
   accountable owner, allowed drill-down, row-scope/redaction evidence, and lineage handle.
   Extend the shared contract only where the already-accepted mandatory comparison/variance
   and quality-check fields are absent; no other shared-file changes are allowed.
5. Drill-down returns configured ordered dimension values and exact Decimal strings. Assert
   displayed slices sum exactly to the headline and select Technology through the persisted
   largest-negative-target-variance rule—never through hard-coded labels or thresholds.
6. Provenance must explicitly state `Demo dataset / seeded demonstration data` and
   `seeded_demo_observations_not_live_extraction`. It may show the selected DataSource's real
   current successful connection-test evidence, but must label its relationship as
   `selected_source_connection_health_only`; connection health is not metric freshness.
7. Derive lineage on every request by traversing widget → metric version/AST → semantic
   field → field binding → source field/object → selected DataSource. Do not store, cache, or
   accept a client-supplied lineage graph. Deleting any optional/projection-shaped data must
   not alter traversal output.
8. Record allowlisted audit events for dashboard view, metric query, drill-down query, and
   lineage view. Audit/log/telemetry may contain identifiers, versions, outcomes, durations,
   row-scope/redaction flags, and counts only—never metric, target, prior, segment, source
   value, endpoint/user detail, credentials, raw requests, or lineage field names.
9. Add the router through the existing application-composition pattern and regenerate the
   committed OpenAPI contract from the authoritative app schema. Do not change migration
   0008 or the central model registry.

## Required verification

- Ruff format/lint and strict mypy.
- OpenAPI generation, shared TypeScript contract formatting/lint/typecheck, and architecture
  boundary tests.
- Real PostgreSQL tests with zero skips for exact headline/prior/target comparisons, target
  variance, ordered drill-down reconciliation, attention selection, freshness/quality,
  seeded-vs-real-health provenance, and full lineage traversal.
- Two-tenant and forged-ID/config/version adversaries proving authorization before all reads
  and uniform absent behavior.
- Fail-closed tests for unpublished configuration, nonadditive/unknown semantic fields,
  unreachable dimensions, malformed AST/query input, missing observation/target/quality/
  freshness links, and stale/disabled selected sources without claiming live analytics.
- Audit/log capture and database scans proving business values, segment names, source field
  names, credentials, and raw payloads do not leak.
- Full existing real PostgreSQL security regressions; zero skipped mandatory tests.

## Stop conditions and exclusions

Stop for any new product choice, ADR conflict, security ambiguity, failed mandatory test, or
need to change migration 0008/frozen persistence. Exclude discovery, profiling, extraction,
ingestion, object storage, live source queries, caching, alerts, general dashboards, UI,
insights, AI, new connectors, production deployment, and customer-system access.

Return an uncommitted patch and exact local evidence. Do not edit automation status/results,
commit, or push.
