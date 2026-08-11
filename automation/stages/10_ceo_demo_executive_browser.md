# Stage 3 of 4 — Executive Command Center browser experience

## Owner and purpose

Claude Code implements this bounded web lane; Codex independently reviews and verifies the
complete uncommitted change. Build the responsive `/app/executive` experience using only the
frozen, typed Stage 2 read-only APIs. Do not change migrations, Python calculations,
authorization, RLS, seeded metadata, API composition, or generated contracts.

## Required behavior

1. Add authenticated `/app/executive` and navigation using the existing web architecture and
   server-side API helpers. Render only typed API data; do not hard-code KPI names, values,
   labels, targets, dimensions, attention selection, timestamps, provenance, or lineage.
2. Present a polished Revenue YTD hero, comparison and target progress, freshness, quality,
   accountable owner, period/as-of, and optional selected-source connection-health evidence.
3. Show the configured People/Process/Technology drill-down with exact reconciliation and a
   Requires Attention link driven solely by the API attention item.
4. Open the trust view in one user action. Show the derived widget → metric version →
   semantic binding → source/table/fields path, configuration version, snapshot and
   calculation time.
5. Visibly and repeatedly distinguish `Demo dataset / seeded demonstration data` and seeded
   observations that are not live extraction from real selected-source connection health.
6. Provide accessible, keyboard-usable loading/error/empty states and responsive desktop and
   narrow-mobile layouts. Never place credentials or secret values in client state, URLs,
   logs, browser storage, screenshots, traces, or error text.
7. Add deterministic component/presentation tests and a real Playwright happy path covering
   dashboard → attention drill-down → one-click trust view. Add narrow-viewport coverage,
   tenant/session manipulation denial, exact visible reconciliation, seeded disclosure, and
   browser artifact leakage scanning.

## Required verification

- Prettier, ESLint, strict TypeScript, shared contract checks, unit tests, and production build.
- Real application stack with PostgreSQL, Redis/worker, seeded source binding, and zero-skip
  Playwright execution for the full browser path and responsive layout.
- Assert all displayed business content originates in intercepted/observed API responses and
  that drill-down totals exactly reconcile to the headline.
- Manipulate tenant/session inputs and prove no cross-tenant data renders.
- Scan URL, HTML, browser storage, captured responses, screenshots, traces, application logs,
  and artifacts for credentials and forbidden raw payload leakage.
- Run the existing real PostgreSQL security regressions required by the repository.

## Stop conditions and exclusions

Stop for a new product choice, API/contract deficiency, ADR conflict, security ambiguity,
failed or skipped mandatory verification, or any need to change frozen shared files. Exclude
discovery, profiling, extraction, ingestion, object storage, live source queries, caching,
alerts, general dashboard builders, insights, AI, new connectors, production deployment, and
customer-system access.

Return an uncommitted patch and exact evidence. Do not edit automation status/results,
commit, push, or begin Stage 4.
