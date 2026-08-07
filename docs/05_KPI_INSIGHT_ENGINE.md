# 05 — KPI Metric Engine, Lineage & Insight Engine

This document covers three tightly related things: the **governed metric engine** (the
unit of truth), **lineage** (why a number can be trusted), and the **insight engine**
(what deserves attention). All three sit above the semantic layer described in
[`04_DATA_CONNECTORS_SEMANTIC_LAYER.md`](04_DATA_CONNECTORS_SEMANTIC_LAYER.md).

## Governed metric engine

Metrics are **first-class governed objects**, not formulas scattered across the UI or
ad-hoc SQL. There is exactly one definition of `revenue_ytd`, it is owned, versioned,
and reused everywhere — dashboards, alerts, insights, and the assistant.

### Metric model

A metric definition includes:

- **id** — internal identifier.
- **tenant_id** — owning tenant (isolation).
- **code** — stable machine name (e.g. `revenue_ytd`).
- **name** — human name (e.g. "Revenue YTD").
- **description** — what it means and how to read it.
- **domain** — grouping (e.g. Revenue, Business Development).
- **expression** — how it is computed from semantic fields.
- **aggregation** — the aggregation type (see below).
- **format** — display format (currency, percent, count, …).
- **dimensions** — the dimensions it can be sliced by.
- **filters** — default/allowed filters.
- **target** — the plan/target it is measured against.
- **thresholds** — the levels that drive signals and status.
- **owner** — the accountable steward.
- **status** — draft / published / archived.
- **version** — for change tracking and rollback.
- **effective dates** — when the definition applies.
- **lineage** — the traceable derivation (see below).

### Supported metric types

- sum
- count
- distinct count
- average
- ratio
- percentage
- variance
- growth rate
- conversion rate
- rolling average
- YTD
- QTD
- MTD
- target variance
- forecast
- calculated metric (composed from other metrics)

### Example

```
code:        revenue_ytd
expression:  SUM(Revenue.Amount)
date window: fiscal_ytd
dimensions:  BusinessUnit, Region, ServiceLine
```

The expression references the **semantic field** `Revenue.Amount`, never a source
column and never a spreadsheet cell. The date window `fiscal_ytd` is resolved from the
tenant's configured fiscal calendar. Slicing by `BusinessUnit`, `Region`, or
`ServiceLine` is a filter on a governed query — never a branch in code.

### Governed query, not arbitrary SQL

Metrics are always computed through the **Governed Query Service**. Clients — including
the browser and the assistant — request a metric by code with dimensions and filters;
they never submit raw SQL. This is what makes every number reproducible, cacheable,
authorizable, and explainable.

## Lineage

Every important metric supports full lineage, traceable in both directions:

```
Dashboard Widget
  → Metric
  → Metric Expression
  → Semantic Field
  → Field Mapping
  → Transformation
  → Source Field
  → Source Object
  → Data Source
```

Given a KPI card on the executive home page, a user can walk down this chain to the
exact source field and source system behind the number — and a steward can walk up it to
find every place a source field is used.

**Lineage is a product feature, not only a developer feature.** Executives and auditors
see it. It is the concrete answer to executive question 8 ("What evidence supports that
conclusion?") and it is what lets leadership trust a number enough to act on it.

## Insight engine

The insight engine decides **what deserves attention**. It does **not** use an LLM as
the sole anomaly detector. Detection is **deterministic and statistical**; the LLM's job
comes afterward, and only to explain (see
[`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)).

### Signal detection

Deterministic/statistical signal detection runs over governed metrics for:

- target breach
- threshold breach
- week-over-week change
- month-over-month change
- year-over-year change
- trend reversal
- sustained decline
- sustained improvement
- anomaly
- volatility
- forecast deviation
- stale data
- data quality degradation

Because detection is deterministic, signals are reproducible, testable, and auditable —
the same inputs always produce the same signals, which is a property no LLM-only
detector can offer.

### Insight structure: facts, correlations, hypotheses, questions

Every insight must clearly distinguish four kinds of statement, and never blur them:

- **FACT** — something the governed data directly shows (e.g. "Revenue YTD is 8% below
  target as of the latest close").
- **CORRELATION** — an observed co-movement, stated as such, without implying cause
  (e.g. "Win rate declined in the same period that pipeline value fell").
- **HYPOTHESIS** — a possible explanation, explicitly labeled as unproven (e.g. "The
  revenue shortfall may be driven by slower deal cycles in one region").
- **RECOMMENDED QUESTION** — the question leadership should ask next (e.g. "Which
  service line accounts for most of the win-rate decline?").

This separation is a hard rule (principle 12). The LLM may generate the narrative around
these, but it must preserve the categories and may only build them from validated
structured evidence. Facts come from the governed metric engine; the model never invents
numbers, causes, or targets.

## How these fit together

The metric engine produces trustworthy numbers; lineage makes them explainable; the
insight engine turns them into a ranked, categorized view of what changed and what
matters. That ranked, evidence-backed view is what powers the executive command center
([`08_UX_EXECUTIVE_EXPERIENCE.md`](08_UX_EXECUTIVE_EXPERIENCE.md)) and the assistant
([`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)) — always evidence first,
narrative second.
