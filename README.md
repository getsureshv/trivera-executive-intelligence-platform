# TriVera Executive Intelligence Platform

A multi-tenant **Executive Intelligence Platform**. Any organization connects its
operational systems and obtains a trusted, explainable, continuously updated view of
business performance — not another dashboard, but a system that tells leadership *what
deserves attention* and *why*.

> This repository currently contains **architecture and context documentation only**.
> No application code has been written yet. The next step is Phase 0 — Architecture
> validation. See [`docs/10_IMPLEMENTATION_ROADMAP.md`](docs/10_IMPLEMENTATION_ROADMAP.md).

## Product vision

The platform turns raw operational data into governed executive intelligence through a
single, explicit pipeline:

```
DATA → BUSINESS MEANING → GOVERNED METRICS → INSIGHTS → DECISION SUPPORT → ACTION
```

It is deliberately **not**: a spreadsheet, a dashboard builder, a charting tool, a
chatbot, a data warehouse, or a generic BI tool. Those are components or comparisons;
the product is the governed intelligence layer that sits above them and answers the
executive questions that matter:

1. How is the business performing?
2. What changed?
3. What is off plan?
4. Why might it have changed?
5. Where is risk increasing?
6. Where is opportunity emerging?
7. What deserves leadership attention?
8. What evidence supports that conclusion?

## Architecture philosophy

The platform is **configuration-first** and **metadata-driven**. A new customer is
onboarded by configuring connectors, a semantic model, and governed metrics — never by
writing tenant-specific code. Thirteen principles govern the design (full text in
[`docs/01_PRODUCT_CONTEXT.md`](docs/01_PRODUCT_CONTEXT.md)); the load-bearing ones:

- Configuration over customization; metadata over tenant-specific code.
- A **semantic layer** sits between raw sources and metrics, so cell references and
  vendor field names never leak into business logic.
- **Governed, reusable metrics** — not arbitrary SQL — are the unit of truth.
- **Dashboards and AI share one semantic/query layer.** The assistant explains
  governed evidence; it is never the source of truth.
- **Explainability and lineage** are product features: any important number can be
  traced back to its source field.
- **Facts, correlations, and hypotheses** are always kept distinct.
- Multi-tenant, API-first, and enterprise-secure from day one.

The source prototype, `TriVera Executive Dashboard.xlsx`, is a **functional prototype
only**. Its KPIs and its `Total / People / Process / Technology / Enterprise` selector
become configurable `Metric` and `Dimension` / `DimensionValue` metadata — never
hard-coded logic. See [`docs/02_WORKBOOK_FINDINGS.md`](docs/02_WORKBOOK_FINDINGS.md).

## File index

Root:

| File | Purpose |
| --- | --- |
| [`README.md`](README.md) | This file — vision, philosophy, index, AI workflow. |
| [`CLAUDE.md`](CLAUDE.md) | Permanent rules for Claude-based agents. |
| [`AGENTS.md`](AGENTS.md) | Portable rules for any coding agent (Cursor/Codex/other). |

Documentation (`/docs`):

| File | Purpose |
| --- | --- |
| [`01_PRODUCT_CONTEXT.md`](docs/01_PRODUCT_CONTEXT.md) | Vision, operating model, executive questions, core principles. |
| [`02_WORKBOOK_FINDINGS.md`](docs/02_WORKBOOK_FINDINGS.md) | What the prototype workbook contains and how it maps to metadata. |
| [`03_PLATFORM_ARCHITECTURE.md`](docs/03_PLATFORM_ARCHITECTURE.md) | Logical layers, stack, bounded contexts, modular monolith. |
| [`04_DATA_CONNECTORS_SEMANTIC_LAYER.md`](docs/04_DATA_CONNECTORS_SEMANTIC_LAYER.md) | Connector framework and semantic layer design. |
| [`05_KPI_INSIGHT_ENGINE.md`](docs/05_KPI_INSIGHT_ENGINE.md) | Governed metric engine, lineage, and the insight engine. |
| [`06_AI_CHAT_ARCHITECTURE.md`](docs/06_AI_CHAT_ARCHITECTURE.md) | "Ask Your Business" governed chat pipeline. |
| [`07_SECURITY_MULTITENANCY_GOVERNANCE.md`](docs/07_SECURITY_MULTITENANCY_GOVERNANCE.md) | Security, tenant isolation, configuration versioning. |
| [`08_UX_EXECUTIVE_EXPERIENCE.md`](docs/08_UX_EXECUTIVE_EXPERIENCE.md) | Executive command center and onboarding wizard. |
| [`09_DOMAIN_MODEL_API_CONTRACTS.md`](docs/09_DOMAIN_MODEL_API_CONTRACTS.md) | Domain entities and conceptual API surface. |
| [`10_IMPLEMENTATION_ROADMAP.md`](docs/10_IMPLEMENTATION_ROADMAP.md) | Phases 0–12 and the first vertical slice. |
| [`11_AGENT_GUARDRAILS.md`](docs/11_AGENT_GUARDRAILS.md) | Hard guardrails for all AI agents working on the codebase. |
| [`12_PROMPT_CLAUDE_CODE.md`](docs/12_PROMPT_CLAUDE_CODE.md) | Prompt/role for Claude Code (primary implementation agent). |
| [`13_PROMPT_CURSOR.md`](docs/13_PROMPT_CURSOR.md) | Prompt/role for Cursor (skeptical reviewer/refactor). |
| [`14_PROMPT_OPENAI_WORK.md`](docs/14_PROMPT_OPENAI_WORK.md) | Prompt/role for OpenAI Work (product/architecture artifacts). |
| [`15_PROMPT_PERPLEXITY.md`](docs/15_PROMPT_PERPLEXITY.md) | Prompt/role for Perplexity (external research only). |
| [`16_TOOL_STRATEGY.md`](docs/16_TOOL_STRATEGY.md) | How the AI tools fit together and the recommended lifecycle. |
| [`adr/README.md`](docs/adr/README.md) | Architecture Decision Record template and process. |
| [`research/README.md`](docs/research/README.md) | Where dated external research lives and its authority. |

## Recommended AI workflow

This project is built with a small fleet of AI tools, each with a distinct job. The
recommended lifecycle:

```
Perplexity  →  OpenAI Work  →  Architecture / ADR  →  Claude Code  →  Cursor Review  →  CI / Tests
(research)     (artifacts)      (decisions)           (build)         (skeptical review)  (verify)
```

- **Perplexity** — current external research only (competitors, semantic layers, AI
  analytics, security expectations, technology choices, CEO/CFO pain points, industry
  KPI packs). Requires citations. See [`docs/15_PROMPT_PERPLEXITY.md`](docs/15_PROMPT_PERPLEXITY.md).
- **OpenAI Work** — workbook analysis, architecture write-ups, PRDs, product artifacts,
  CEO demos, mapping matrices. See [`docs/14_PROMPT_OPENAI_WORK.md`](docs/14_PROMPT_OPENAI_WORK.md).
- **Claude Code** — the primary implementation agent. Works EXPLORE → PLAN → IMPLEMENT
  → VERIFY. See [`docs/12_PROMPT_CLAUDE_CODE.md`](docs/12_PROMPT_CLAUDE_CODE.md).
- **Cursor** — interactive development and a skeptical senior review of autonomous
  code. See [`docs/13_PROMPT_CURSOR.md`](docs/13_PROMPT_CURSOR.md).

Full division of labor: [`docs/16_TOOL_STRATEGY.md`](docs/16_TOOL_STRATEGY.md).

### How to start with Claude Code

1. Read `CLAUDE.md`, then `docs/01`–`docs/03`, then `docs/10` and `docs/11`.
2. Take **only** the Phase 0 assignment from `docs/12_PROMPT_CLAUDE_CODE.md`
   (architecture validation). Do not scaffold Next.js, FastAPI, Docker, the database,
   or connectors yet.
3. Produce Phase 0 outputs as ADRs in `docs/adr/` and a short validation report.

### How to use Cursor

Open the repo, read `AGENTS.md` and `docs/13_PROMPT_CURSOR.md`, and use Cursor to
review diffs produced by Claude Code as a skeptical senior engineer — hunting for
tenant-isolation gaps, hard-coded workbook values, secret leakage, and ungoverned SQL.

### How to use OpenAI Work

Use it for non-code artifacts: PRD sections, architecture narratives, the workbook →
metadata mapping matrix, and CEO-facing demo material, following
`docs/14_PROMPT_OPENAI_WORK.md`.

### How to use Perplexity

Use it for time-sensitive external research only, always with citations and primary
sources, following `docs/15_PROMPT_PERPLEXITY.md`. File dated results in
`docs/research/`; they inform but never override accepted ADRs.

## Status

Documentation bootstrap complete once this repository is committed. Application
implementation begins at **Phase 0 — Architecture validation**.
