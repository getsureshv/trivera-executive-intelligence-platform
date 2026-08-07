# AGENTS.md — Portable Coding-Agent Instructions

Portable instructions for any coding agent working in this repository (Cursor, Codex,
OpenAI-based agents, or others). This is the tool-neutral mirror of `CLAUDE.md`. If a
rule here and a rule in `CLAUDE.md` ever disagree, treat it as a bug and open an issue —
they are meant to stay in sync.

## Project in one paragraph

**TriVera Executive Intelligence Platform** is a multi-tenant Executive Intelligence
Platform. Any organization connects its operational systems (databases, SaaS APIs,
files) and gets a trusted, explainable, continuously updated view of business
performance. The pipeline is `DATA → BUSINESS MEANING → GOVERNED METRICS → INSIGHTS →
DECISION SUPPORT → ACTION`. It is not a spreadsheet, dashboard builder, charting tool,
chatbot, warehouse, or generic BI tool.

## Rules every agent must follow

1. Read the relevant files in `/docs` before making architectural or cross-cutting
   changes. Start from the index in `docs/README.md`. **Accepted ADRs in `docs/adr/`
   outrank the numbered documents**; see `docs/17_PHASE_0_ARCHITECTURE_REVIEW.md` for the
   Phase 0 corrections.
2. Prefer **configuration over customization** and **metadata over tenant-specific
   code**. Never write per-tenant `if` branches in business logic.
3. Keep the **semantic layer** between raw sources and metrics. Source fields do not
   feed dashboards, metrics, or the assistant directly.
4. Treat **metrics as governed, versioned, reusable objects**. No arbitrary SQL from
   clients; no scattered metric math.
5. Enforce **tenant isolation** everywhere: queries, caches, storage, logs, telemetry.
6. Keep **connectors provider-neutral** and **LLM access provider-neutral**. Depend on
   abstractions, not vendor SDKs, in business logic.
7. The **LLM never has unrestricted database access**. It explains governed, validated
   evidence; it is not the source of truth.
8. **No secrets** in code, logs, prompts, Git, or ordinary metadata. Use the secret
   manager abstraction.
9. The Excel **workbook is a prototype only**. Do not recreate it as the app, and do
   not hard-code its dimensions (`Total / People / Process / Technology / Enterprise`)
   or KPI names.
10. Keep **facts, correlations, and hypotheses** separated in any generated output.
    Evidence first, narrative second.

## Working method

- Loop: **EXPLORE → PLAN → IMPLEMENT → VERIFY**. Explore the code and docs, write a
  short plan, implement the smallest correct change, then verify.
- Architecture default: **modular monolith** with strong bounded contexts (see
  `docs/03_PLATFORM_ARCHITECTURE.md`). No microservices without an accepted ADR.
- Every schema change ships as a **migration**. Never hand-edit the database.
- Before you call a task done, run **lint, typecheck, and tests** and make them pass.
- Significant decisions become **ADRs** (`docs/adr/`). Dated research lives in
  `docs/research/` and does not override accepted ADRs.

## Agent-specific notes

- **Cursor** acts as a skeptical senior reviewer of autonomous code — see
  `docs/13_PROMPT_CURSOR.md`.
- **Claude Code** is the primary implementation agent — see
  `docs/12_PROMPT_CLAUDE_CODE.md`.
- **OpenAI Work** handles product/architecture artifacts — see
  `docs/14_PROMPT_OPENAI_WORK.md`.
- **Perplexity** handles current external research only — see
  `docs/15_PROMPT_PERPLEXITY.md`.
- Overall lifecycle and division of labor: `docs/16_TOOL_STRATEGY.md`.

## Status

**Phase 0 — Architecture validation is complete** (2026-08-07): ADR-001 … ADR-015,
`docs/17_PHASE_0_ARCHITECTURE_REVIEW.md`, `docs/18_FIRST_VERTICAL_SLICE.md`. No
application code yet. Do not scaffold frameworks, Docker, the database, or connectors
until Phase 1 is explicitly approved per the review's *Readiness Verdict*.
