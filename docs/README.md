# Documentation Index

The documentation set for the **TriVera Executive Intelligence Platform**. This file is
the index referenced by `CLAUDE.md` and `AGENTS.md`; start here.

## Authority order

```
accepted ADRs  >  this documentation set  >  OpenAI Work artifacts  >  research
```

Where a Phase 0 ADR and a numbered document disagree, **the ADR governs**. Affected
documents carry a `> **Phase 0 update**` callout pointing at the ADR. Changing an accepted
decision means writing a new ADR, not editing the old one
(see [`16_TOOL_STRATEGY.md`](16_TOOL_STRATEGY.md)).

## Core documents

| # | Document | What it covers |
| --- | --- | --- |
| 01 | [Product Context](01_PRODUCT_CONTEXT.md) | What the product is, the operating model, the eight executive questions, the thirteen principles |
| 02 | [Workbook Findings](02_WORKBOOK_FINDINGS.md) | How the prototype workbook maps onto platform metadata |
| 03 | [Platform Architecture](03_PLATFORM_ARCHITECTURE.md) | Layers, bounded contexts, stack, request flow |
| 04 | [Data Connectors & Semantic Layer](04_DATA_CONNECTORS_SEMANTIC_LAYER.md) | Connector framework and semantic model |
| 05 | [KPI, Lineage & Insight Engine](05_KPI_INSIGHT_ENGINE.md) | Governed metrics, lineage, signal detection |
| 06 | [AI Chat Architecture](06_AI_CHAT_ARCHITECTURE.md) | "Ask Your Business" governed assistant pipeline |
| 07 | [Security, Multi-Tenancy & Governance](07_SECURITY_MULTITENANCY_GOVERNANCE.md) | Isolation, access control, secrets, versioning |
| 08 | [UX & Executive Experience](08_UX_EXECUTIVE_EXPERIENCE.md) | Attention surface, KPI cards, onboarding wizard |
| 09 | [Domain Model & API Contracts](09_DOMAIN_MODEL_API_CONTRACTS.md) | Entities and the conceptual API surface |
| 10 | [Implementation Roadmap](10_IMPLEMENTATION_ROADMAP.md) | Phases and sequencing |
| 11 | [Agent Guardrails](11_AGENT_GUARDRAILS.md) | Hard rules for every AI agent in this repository |
| 16 | [Tool Strategy](16_TOOL_STRATEGY.md) | Which tool does what, and the authority order |

## Phase 0 outputs

| # | Document | What it covers |
| --- | --- | --- |
| 17 | [Phase 0 Architecture Review](17_PHASE_0_ARCHITECTURE_REVIEW.md) | Validation report: strengths, weaknesses, missing capabilities, technology decisions, risks, open questions, final architecture |
| 18 | [First Vertical Slice](18_FIRST_VERTICAL_SLICE.md) | Design (not implementation) of the first end-to-end proof of the architecture |

## Phase 1A outputs

| # | Document | What it covers |
| --- | --- | --- |
| 19 | [Phase 1A Report](19_PHASE_1A_REPORT.md) | Completion report for the platform skeleton, **as remediated**: the four security findings and their fixes, the role and credential model, tests with observed results, migration/rollback evidence, the green CI run, and remaining gaps |
| — | [Architecture Decision Records](adr/README.md) | ADR-001 … ADR-015 — the binding decisions |

## Agent prompts

| # | Document |
| --- | --- |
| 12 | [Claude Code](12_PROMPT_CLAUDE_CODE.md) — primary implementation agent |
| 13 | [Cursor](13_PROMPT_CURSOR.md) — skeptical reviewer |
| 14 | [OpenAI Work](14_PROMPT_OPENAI_WORK.md) — product/architecture artifacts |
| 15 | [Perplexity](15_PROMPT_PERPLEXITY.md) — external research only |

## Reading order for a new contributor

1. [`01_PRODUCT_CONTEXT.md`](01_PRODUCT_CONTEXT.md) — what we are building and why
2. [`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md) — the current
   architectural position, including where the earlier documents were corrected
3. [`adr/`](adr/README.md) — the binding decisions
4. [`11_AGENT_GUARDRAILS.md`](11_AGENT_GUARDRAILS.md) — the rules
5. The numbered document relevant to your task

## Other folders

- [`adr/`](adr/README.md) — architecture decision records (authoritative)
- [`research/`](research/README.md) — dated external research (informs, never decides)

## Current phase

**Phase 1A — Platform skeleton is complete** (2026-08-07, commit `d766783`). See
[`19_PHASE_1A_REPORT.md`](19_PHASE_1A_REPORT.md). The repository now contains application
code: the platform foundation and enforced tenant isolation, and deliberately no
business-intelligence functionality.

Phase 1A was remediated on 2026-08-07 after a review found four security defects
(analytical isolation, production token verification, worker privileges, audit
tamper-evidence). All four are fixed and evidenced; CI runs green. Product-owner
questions Q1–Q4 from
[`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md) remain open and
gate the wider Phase 1 scope.
