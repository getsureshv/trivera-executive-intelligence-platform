# Documentation Index

The documentation set for the **TriVera Executive Intelligence Platform**. This file is
the index referenced by `CLAUDE.md` and `AGENTS.md`; start here.

## Authority order

```
product-owner decisions  >  accepted ADRs  >  this documentation set
                         >  OpenAI Work artifacts  >  research
```

Product-owner decisions ([`20`](20_PRODUCT_OWNER_DECISIONS.md)) define what the
business has committed to; the ADRs serve them. Where the two appear to
disagree, the ADR is amended or superseded — never reinterpreted.

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
| 20 | [Product-Owner Decisions](20_PRODUCT_OWNER_DECISIONS.md) | PO-001 … PO-005 — the business commitments the ADRs serve. **Outrank accepted ADRs**; closes Phase 0 questions Q1–Q4 |
| 19 | [Phase 1A Report](19_PHASE_1A_REPORT.md) | Completion report for the platform skeleton, **as remediated**: the four security findings and their fixes, the role and credential model, tests with observed results, migration/rollback evidence, the green CI run, and remaining gaps |
| — | [Architecture Decision Records](adr/README.md) | ADR-001 … ADR-016 — the binding decisions |

## Phase 1B entry tasks

| # | Document | What it covers |
| --- | --- | --- |
| 21 | [Phase 1B Entry Tasks Report](21_PHASE_1B_ENTRY_REPORT.md) | The three conditions recorded at Phase 1A's closure: OIDC against a real identity provider, operator-driven tenant provisioning, and the first browser end-to-end security test. Commits, observed test results, migration evidence, unresolved risks, and the recommendation on beginning the first connector slice |

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

**Phase 1A is closed** (2026-08-07) and its **three entry tasks for Phase 1B are
complete** (2026-08-08). See [`19`](19_PHASE_1A_REPORT.md) and
[`21`](21_PHASE_1B_ENTRY_REPORT.md).

The repository contains the platform foundation and enforced tenant isolation,
now evidenced at three layers — database, API, and browser — plus delegated
authentication proven against a real identity provider and an idempotent,
observable tenant-provisioning workflow. There is still deliberately **no
business-intelligence functionality**: no connectors, semantic model, metrics,
dashboards, lineage, insights, or AI.

Gaps **G3** and **G13** are closed; **G12** is partly closed. **G11** (audit
checkpoints are not exported off-box) and **G14** (no production `SecretStore`
adapter) remain documented boundaries. Product-owner questions Q1–Q4 are answered
in [`20`](20_PRODUCT_OWNER_DECISIONS.md); Q5–Q12 remain open and none gates the
first connector slice, which is **recommended to begin** under the three
conditions in [`21`](21_PHASE_1B_ENTRY_REPORT.md).
