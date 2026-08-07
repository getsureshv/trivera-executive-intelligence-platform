# 06 — AI Chat Architecture ("Ask Your Business")

"Ask Your Business" lets an executive ask questions in natural language and get trusted,
evidence-backed answers. The governing rule is simple and absolute:

> **Never give an LLM unrestricted access to production databases.**

The LLM never sees raw tables, never writes SQL that reaches production, and is never
the source of truth. It sits at the end of a governed pipeline and explains numbers that
the platform has already computed and validated (principle 11).

## The pipeline

Every question travels the same path:

```
User Question
  → Intent Detection
  → Authorization
  → Semantic Resolution
  → Governed Query Plan
  → Query Execution
  → Evidence Validation
  → LLM Explanation
```

1. **User Question** — free-form natural language from the executive.
2. **Intent Detection** — classify what the user wants (see supported intents below).
3. **Authorization** — enforce that this user may see these metrics, dimensions, and
   rows *before* any data is touched. Authorization is not a post-filter.
4. **Semantic Resolution** — map the question's terms to governed semantic entities,
   fields, dimensions, and metrics via the semantic layer and glossary.
5. **Governed Query Plan** — construct a plan expressed entirely in governed metrics,
   dimensions, and filters. No arbitrary SQL is produced.
6. **Query Execution** — run the plan through the Governed Query Service, the same path
   dashboards use.
7. **Evidence Validation** — confirm the results are sufficient, fresh, and
   quality-checked before they are allowed to reach the model.
8. **LLM Explanation** — the model explains the validated evidence in plain language,
   preserving the fact/correlation/hypothesis separation.

The model enters only at steps 2 (as a classifier/parser) and 8 (as an explainer). The
numbers themselves are produced by the governed spine, never by the model.

## Supported intents

The intent set may include:

- `get_metric` — return a metric value.
- `compare_metric` — compare a metric across periods or slices.
- `explain_variance` — explain a gap versus target or a prior period, using structured
  evidence.
- `breakdown_metric` — break a metric down by a dimension.
- `find_below_target` — find metrics under target.
- `identify_risk` — surface where risk is increasing.
- `summarize_business` — produce an executive summary.
- `explain_metric_definition` — explain how a metric is defined (from governed
  metadata, not invention).
- `show_lineage` — show a metric's lineage.
- `recommend_questions` — suggest questions worth asking next.

## Answer contract for quantitative questions

Any quantitative answer must carry the context that makes it trustworthy:

- **metric** — which governed metric answered the question.
- **value** — the computed value.
- **comparison** — versus target, prior period, or slice, as applicable.
- **period** — the time window.
- **filters** — the dimensions/filters applied.
- **freshness** — how current the underlying data is.
- **quality** — the data-quality status.
- **lineage availability** — that the number can be traced to its source.

An answer without this context is not shippable; the badges are how the executive knows
whether to act.

## When evidence is insufficient

If the evidence is insufficient — the data is stale, the quality is poor, the question
cannot be resolved to governed metrics, or the user is not authorized — the assistant
**says so**. It does not paper over gaps with a plausible-sounding answer.

## Hard prohibitions

The assistant must **never invent**:

- numbers
- causes
- targets
- source systems
- dates
- customers

If any of these is not present in validated evidence, the assistant states that it is
unavailable rather than fabricating it. This is the chat-surface expression of "evidence
first, narrative second."

## Provider neutrality

LLM integration is **provider-neutral**. The pipeline depends on an LLM gateway
interface, not on a specific vendor SDK, so the model can be swapped without touching
intent detection, authorization, query planning, or validation. No production data or
secrets are ever placed in prompts (see
[`07_SECURITY_MULTITENANCY_GOVERNANCE.md`](07_SECURITY_MULTITENANCY_GOVERNANCE.md) and
[`11_AGENT_GUARDRAILS.md`](11_AGENT_GUARDRAILS.md)).

## Shared spine with dashboards

The assistant and the dashboards share one semantic/query layer (principle 10). A number
the assistant reports and the same number on a KPI card are computed by the identical
governed path, so they can never disagree. The assistant is a different **surface** onto
the same governed intelligence, not a parallel system.
