# Phase 2 Stage 3 — Add Source / Test Connection experience

## Authority and boundary

Implement only Stage 3 of `docs/22_PHASE_2_DATA_SOURCE_MANAGER_ENTRY_REPORT.md` on the
green Stage 2 baseline `01aee08`. Do not change migrations, backend authorization,
connector behavior, worker behavior, retention/deletion, or excluded roadmap features.

## Assignment

- Add an authenticated `/app/data-sources` experience using the committed OpenAPI
  contract and existing web session/API helpers.
- Show the tenant's source list and an honest empty state.
- Provide the smallest accessible PostgreSQL Add Source form: name, host/port, username,
  database, TLS mode, and password. Password must remain a write-only control: never put
  it in a URL, browser persistence, returned model, rendered confirmation, diagnostic,
  log, or test artifact; clear it after submission.
- Do not send a tenant identifier. Do not expose or construct a SecretStore reference.
- Let an authorized user request a connection test, poll the returned `poll_url` with a
  bounded interval/timeout, and render status plus the six safe ordered diagnostic checks.
- Clearly distinguish authentication failure from network failure using only safe API
  codes/messages, with accessible loading, retry, denial/not-found, and generic error
  states that do not reveal whether another tenant's identifier exists.
- Keep styling consistent with the existing authenticated shell and responsive at mobile
  and desktop widths. Add no dashboard, metric, discovery, profiling, extraction, or
  additional connector UI.
- Add focused web unit tests and Playwright coverage. The real browser test must perform
  Add PostgreSQL Source → Test Connection against the Docker API, Redis worker, and real
  PostgreSQL, verify the completed result, and scan visible/page state and diagnostics
  artifacts for a unique credential sentinel.
- Update shared generated contracts only when deterministically required by the already
  committed OpenAPI document. Update this stage's result and workflow status only after
  Codex verification.

## Acceptance gates

- Complete Codex diff review, no secret-bearing browser state or unsafe error rendering.
- Prettier, ESLint, strict TypeScript, web unit tests, production build.
- Real Docker PostgreSQL + Redis/worker walkthrough with Playwright and zero skips.
- Cross-tenant/absent behavior remains indistinguishable and credential sentinel is absent
  from HTML, URLs, local/session storage, returned API bodies, screenshots, and saved test
  artifacts.
- Existing Ruff, strict mypy, architecture/connectivity/security suites remain green.
- Focused commit, push, all-green CI, and clean repository before Stage 4.

## Explicit exclusions

No deletion/retention, discovery, profiling, extraction, ingestion, object storage,
semantic mapping, metrics, dashboards, insights, lineage, alerts, AI, caching, additional
connectors, customer-network agent, production secret adapter, or deployment work.
