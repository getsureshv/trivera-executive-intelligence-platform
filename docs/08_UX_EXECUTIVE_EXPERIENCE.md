# 08 — UX & Executive Experience

The experience is built around one question, not a gallery of charts:

> **"What deserves my attention?"**

Executives are time-poor. The home experience is an **attention surface**, not a
dashboard-builder canvas. It leads with what changed and what is off plan, and it makes
evidence one click away (principle 13). It deliberately **avoids chart overload**.

## Executive command center (home)

The home page is structured top to bottom to move from state → change → attention →
questions → narrative → conversation:

**BUSINESS HEALTH** — a compact row of core KPI cards:

- Revenue
- Pipeline
- Customer Health
- Operations
- People
- Cash

Then, in order:

- **WHAT CHANGED** — the notable movements since last look, sourced from the insight
  engine's signals.
- **REQUIRES ATTENTION** — items breaching targets/thresholds or trending badly, ranked
  by importance.
- **CEO QUESTIONS WORTH ASKING** — recommended questions the insight engine surfaces,
  each tied to evidence.
- **AI EXECUTIVE BRIEF** — a short narrative summary that explains the above using
  validated evidence, preserving fact/correlation/hypothesis separation.
- **ASK YOUR BUSINESS** — the entry point to the governed assistant
  ([`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)).

This ordering is intentional: the executive sees the state of the business, then what
moved, then what to worry about, then what to ask, then a synthesized brief, and finally
a way to interrogate it directly.

## KPI card requirements

Each KPI card is a trust object, not just a number. Every card supports:

- **target** — the plan the KPI is measured against.
- **comparison** — versus target and/or prior period.
- **freshness** — how current the underlying data is.
- **data quality** — the quality status of the inputs.
- **owner** — the accountable steward.
- **drill-down** — the path into detail and slices.
- **lineage** — the trace back to source
  ([`05_KPI_INSIGHT_ENGINE.md`](05_KPI_INSIGHT_ENGINE.md)).

The freshness, quality, owner, and lineage affordances are what let an executive trust a
number enough to act. A KPI without them is not done.

## Design principles for the experience

- **Attention over exploration.** The default view answers "what deserves attention?"
  before it offers exploration.
- **Avoid chart overload.** Prefer a small number of high-signal cards and one clear
  narrative to a wall of visualizations.
- **Evidence is always one step away.** Every claim links to its metric, and every
  metric to its lineage.
- **Facts, correlations, hypotheses stay visually distinct.** The brief and insight
  cards never blur the categories.

## Onboarding wizard

A new organization is brought live through a guided wizard — configuration, not code.
The steps:

1. **Create Organization** — establish the tenant.
2. **Select Industry** — seed sensible defaults (candidate KPI packs, domains).
3. **Define Fiscal Calendar** — so YTD/QTD/MTD windows resolve correctly.
4. **Select Executive Priorities** — what leadership most wants to watch.
5. **Add Data Source** — configure a connector.
6. **Test Connection** — run the connector's diagnostics
   ([`04_DATA_CONNECTORS_SEMANTIC_LAYER.md`](04_DATA_CONNECTORS_SEMANTIC_LAYER.md)).
7. **Discover Data** — enumerate namespaces, objects, and fields.
8. **Review AI Mapping Suggestions** — approve or edit AI-proposed field mappings
   (human approval required).
9. **Select KPI Pack** — choose a starter set of governed metrics.
10. **Configure Targets** — set targets and thresholds.
11. **Generate Dashboard** — assemble the executive command center from the configured
    metrics.
12. **Review and Publish** — review and publish the configuration (versioned, governed).

The wizard is the human-facing embodiment of the operating model: it walks a tenant from
DATA (steps 5–7) through BUSINESS MEANING (step 8) to GOVERNED METRICS (steps 9–10) and
on to the experience (steps 11–12). Every step it produces is versioned configuration,
so the whole onboarding is auditable and reversible.

> **Phase 0 update.** Three changes:
>
> 1. **KPI-pack selection moves before mapping.** As ordered, step 8 asks a steward to map
>    fields before step 9 establishes which KPIs the tenant actually needs — so there is no
>    way to know which fields matter. The KPI pack defines the **semantic contracts** that
>    bindings must satisfy ([ADR-005](adr/ADR-005-semantic-model.md)), so it must come
>    first and *drive* discovery and mapping.
> 2. **Step 8 produces entity bindings, not field mappings**, and cannot complete until
>    **binding validation passes** — grain uniqueness, required fields bound, types and
>    units compatible, time anchors bound. This validation gate is what makes "onboard by
>    configuration" verifiable rather than hopeful.
> 3. **Fiscal calendar (step 3) is a hard prerequisite** for anything computing
>    `fiscal_ytd`/QTD/MTD, and **currency policy** joins it where the tenant is
>    multi-currency. The wizard should block rather than defaulting silently.
>
> The wizard's output is a **draft `ConfigurationBundle`**; "Review and Publish" (step 12)
> is an atomic bundle publish, gated on validation and acceptance assertions
> ([ADR-013](adr/ADR-013-configuration-versioning.md)).
