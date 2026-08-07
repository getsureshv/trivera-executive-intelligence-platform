# 16 — Tool Strategy

The project is built by a small fleet of AI tools, each with a distinct, deliberate job.
Keeping the roles separate is what makes the whole loop trustworthy: research is cited,
artifacts are legible, code is guardrailed, and review is skeptical.

## Roles

| Tool | Role |
| --- | --- |
| **Claude Code** | Primary coding agent. Builds the platform EXPLORE → PLAN → IMPLEMENT → VERIFY. See [`12_PROMPT_CLAUDE_CODE.md`](12_PROMPT_CLAUDE_CODE.md). |
| **Cursor** | Interactive development and review. Acts as a skeptical senior reviewer of autonomous code, plus hands-on refactor. See [`13_PROMPT_CURSOR.md`](13_PROMPT_CURSOR.md). |
| **OpenAI Work** | Product / architecture / artifacts. Workbook analysis, PRDs, architecture write-ups, CEO demos, mapping matrices. See [`14_PROMPT_OPENAI_WORK.md`](14_PROMPT_OPENAI_WORK.md). |
| **Perplexity** | External research only, with citations and primary sources. See [`15_PROMPT_PERPLEXITY.md`](15_PROMPT_PERPLEXITY.md). |

## Recommended lifecycle

```
Perplexity
  → OpenAI Work
  → Architecture / ADR
  → Claude Code
  → Cursor Review
  → CI / Tests
```

Read as a pipeline:

1. **Perplexity** gathers current external evidence (competitors, semantic layers, AI
   analytics, security expectations, technology choices, CEO/CFO pain points, KPI packs),
   dated and cited, filed in `docs/research/`.
2. **OpenAI Work** turns that evidence into product and architecture artifacts — PRDs,
   architecture narratives, mapping matrices, demo material.
3. **Architecture / ADR** — load-bearing decisions are captured as ADRs in `docs/adr/`.
   Accepted ADRs govern the build; research informs but never overrides them.
4. **Claude Code** implements against the accepted architecture and the guardrails,
   EXPLORE → PLAN → IMPLEMENT → VERIFY.
5. **Cursor Review** reviews the autonomous code skeptically — tenant isolation,
   hard-coded workbook values, ungoverned SQL, secret leakage, vendor coupling — and
   refactors interactively.
6. **CI / Tests** provide the final automated verification gate.

## Why the separation matters

- **Research is isolated from decisions.** Perplexity gathers; it does not decide. This
  keeps time-sensitive external claims from silently becoming architecture.
- **Artifacts are isolated from code.** OpenAI Work makes the product legible to
  executives without touching the build.
- **Building is isolated from reviewing.** Claude Code builds and Cursor reviews as
  independent perspectives, so a fast implementation pass is always checked by a
  skeptical one before CI.
- **Decisions are durable.** ADRs are the one place where a choice becomes binding; every
  other tool feeds into or executes against them.

## Authority order

When sources conflict, authority runs: **accepted ADRs** > **this documentation set** >
**OpenAI Work artifacts** > **Perplexity research**. Research and artifacts propose;
ADRs and the core docs dispose. Changing an accepted decision means writing a new ADR,
not overriding it in a research note or a diff.
