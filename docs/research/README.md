# Research

This folder holds **dated external research** — findings gathered from Perplexity or
other external sources about the outside world: competitors, semantic-layer approaches,
AI analytics, enterprise security expectations, current technology choices, CEO/CFO pain
points, and industry KPI packs. See
[`../15_PROMPT_PERPLEXITY.md`](../15_PROMPT_PERPLEXITY.md) for the research agent's brief.

## Authority

Research **informs but does not decide.** It does **not** override an accepted ADR. Per
the authority order in [`../16_TOOL_STRATEGY.md`](../16_TOOL_STRATEGY.md):

```
accepted ADRs > core /docs > OpenAI Work artifacts > research
```

If a piece of research suggests that an accepted decision should change, that is a signal
to open a **new ADR** in [`../adr/`](../adr/README.md) — the research note itself never
supersedes the decision.

## Conventions

- **Date every file and finding.** External facts change; freshness must be visible.
  Prefer filenames like `YYYY-MM-DD-topic.md`.
- **Cite sources.** Every claim carries a citation; primary sources are preferred.
- **Attribute, don't assert.** Competitor and vendor marketing is labeled as such, not
  presented as fact.
- **One topic per file** where practical, so findings are easy to reference and age out.

## Status

No research has been filed yet. This folder is a placeholder describing where dated
external research belongs and what authority it carries.
