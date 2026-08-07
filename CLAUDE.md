# CLAUDE.md — Permanent Agent Rules

This file is the standing operating contract for any Claude-based agent working in
this repository. Read it before every non-trivial change. It is intentionally short.
The reasoning behind these rules lives in `/docs`; this file is the enforceable summary.

## What this repository is

**TriVera Executive Intelligence Platform** — a multi-tenant Executive Intelligence
Platform. It lets any organization connect its operational systems and obtain a
trusted, explainable, continuously updated view of business performance.

It is **not** a spreadsheet, a dashboard builder, a charting tool, a chatbot, a data
warehouse, or a generic BI tool. It is an Executive Intelligence Platform whose
operating model is:

```
DATA → BUSINESS MEANING → GOVERNED METRICS → INSIGHTS → DECISION SUPPORT → ACTION
```

## Non-negotiable rules

1. **Read relevant `/docs` before major changes.** Architecture lives in
   `03_PLATFORM_ARCHITECTURE.md`; the doc index is in `README.md`.
2. **Configuration over tenant-specific code.** No `if tenant == "X"` branches. New
   behavior is expressed as metadata, not code paths.
3. **The semantic model sits between sources and metrics.** Raw source fields never
   drive a dashboard, a metric, or a chat answer directly.
4. **Metrics are governed, reusable, first-class objects.** No ad-hoc metric math
   scattered across the UI or the query layer.
5. **Tenant isolation is mandatory from day one.** Every query, cache key, storage
   path, and log line is tenant-scoped.
6. **Connectors are provider-neutral.** Program to the connector abstraction, never
   to a specific database or SaaS vendor in business logic.
7. **LLM integration is provider-neutral.** Program to an LLM gateway/interface, not
   to a specific vendor SDK in business logic.
8. **No unrestricted LLM database access.** The LLM never sees raw production tables
   or writes SQL that reaches them. It explains governed, validated evidence only.
9. **No secrets in source control.** Not in code, logs, prompts, Git, or ordinary
   metadata. Use the external secret-manager abstraction.
10. **The workbook is a prototype only.** `TriVera Executive Dashboard.xlsx` documents
    intent. It is never recreated as the application.
11. **No hard-coded workbook dimensions.** `Total / People / Process / Technology /
    Enterprise` and all KPI names are configurable `Dimension` / `DimensionValue` and
    `Metric` metadata — never literals in code.
12. **Evidence first, narrative second.** Deterministic/statistical signals produce
    facts; the LLM narrates afterward. Facts, correlations, and hypotheses stay
    clearly separated.

## How to work

- Follow **EXPLORE → PLAN → IMPLEMENT → VERIFY** for every change.
- Prefer the **modular monolith** with strong bounded contexts. Do not introduce
  microservices without an accepted ADR.
- Use **database migrations** (Alembic) for every schema change. Never mutate schema
  by hand.
- Run **lint, typecheck, and tests** before considering work complete. Do not report
  a task done while any of these fail.
- Record significant technical decisions as **ADRs** in `docs/adr/`. Dated external
  research goes in `docs/research/` and never overrides an accepted ADR.
- When in doubt about scope, prefer the smallest correct change and write down the
  open question rather than guessing at product behavior.

## Current phase

The repository is in **documentation bootstrap**. Application code has not started.
The next assignment is **Phase 0 — Architecture validation** (see
`docs/10_IMPLEMENTATION_ROADMAP.md` and `docs/12_PROMPT_CLAUDE_CODE.md`). Do not
scaffold Next.js, FastAPI, Docker, the database, or connectors until Phase 0 is
explicitly approved.
