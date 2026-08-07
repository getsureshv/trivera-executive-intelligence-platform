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

> **Phase 0 update ([ADR-011](adr/ADR-011-ai-provider-and-orchestration.md)):** the
> pipeline and its founding prohibition are confirmed. Four changes:
>
> 1. **Step 2 becomes constrained plan generation, not intent classification.** The model
>    emits a `QueryPlanRequest` conforming to a schema generated per request from *that
>    tenant's* governed catalog, filtered to *that principal's* authorized metrics and
>    dimensions. A deterministic validator — not the model — is the security boundary.
>    Real questions are compositional ("advisory revenue in the East region versus plan,
>    versus the same quarter last year, and which service line moved most"); a closed
>    intent enum forces the classifier to pick one intent and discard the rest. The enum
>    survives only as a coarse router.
> 2. **Every prompt has two trust zones.** Tenant-derived content — discovered field
>    names, table comments, glossary terms, sampled values — is delimited and labelled as
>    **data, never instruction**. Prompt injection through source metadata is a live
>    vector, most acutely in the AI mapping assistant, whose entire purpose is reading
>    untrusted source metadata. Model output is never executed and never makes an
>    authorization decision.
> 3. **A deterministic numeric grounding check** verifies that every number in the
>    narrative appears in the supplied evidence; a mismatch fails the response rather than
>    shipping it.
> 4. **Step 7 gates step 8 absolutely.** If evidence is insufficient the model is *not
>    called* — it is never asked to explain that there is no data, because a model given
>    thin evidence fills gaps.

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
intent detection, authorization, query planning, or validation.

> **Phase 0 correction ([ADR-011](adr/ADR-011-ai-provider-and-orchestration.md) §3):** the
> blanket phrase "no production data in prompts" contradicts step 8, which requires the
> model to explain validated evidence — and validated evidence *is* production-derived.
> As written, the rule forbade the architecture. The precise rule is:
>
> **Never in a prompt:** secrets or credentials; raw source rows that have not passed
> through the governed pipeline; another tenant's data in any form; anything the
> requesting principal is not authorized to see; personal data beyond what the answer
> requires.
>
> **Deliberately in the prompt:** governed, authorized, validated evidence — the
> aggregates, comparisons, periods, and metadata the model must narrate. Row-level detail
> is aggregated first wherever the answer permits, and PII-classified fields are masked
> unless the principal is cleared and the question requires them.
>
> Also required: no cross-tenant prompt or completion caching, no fine-tuning on tenant
> data, tenant-partitioned embeddings, per-tenant token budgets and cost attribution, and
> a zero-retention contractual commitment as a provider *eligibility gate*.

See
[`07_SECURITY_MULTITENANCY_GOVERNANCE.md`](07_SECURITY_MULTITENANCY_GOVERNANCE.md) and
[`11_AGENT_GUARDRAILS.md`](11_AGENT_GUARDRAILS.md)).

## Shared spine with dashboards

The assistant and the dashboards share one semantic/query layer (principle 10). A number
the assistant reports and the same number on a KPI card are computed by the identical
governed path, so they can never disagree. The assistant is a different **surface** onto
the same governed intelligence, not a parallel system.
