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

> **Phase 0 update ([ADR-006](adr/ADR-006-metric-definition-and-kpi-engine.md)):**
>
> - **`expression` is a typed AST persisted as JSON, never a string.** A free-text
>   expression cannot be diffed, validated at publish, lineage-traced automatically, or
>   compiled portably — and it is the trapdoor through which ad-hoc SQL re-enters.
> - **`target` and `thresholds` are entities, not scalar fields.** Targets vary by period
>   and by dimension slice, with most-specific-match resolution.
>   [`09_DOMAIN_MODEL_API_CONTRACTS.md`](09_DOMAIN_MODEL_API_CONTRACTS.md) is correct;
>   this list was not.
> - A metric additionally declares `time_anchor`, `default_period`, `direction`
>   (higher/lower-is-better — the insight engine cannot judge a change without it), and
>   `additivity`, which the compiler **checks**: averaging a semi-additive measure over
>   time is rejected at publish.
> - **Published metric versions are immutable**, identified by a content hash, and
>   referenced by id in every result and observation. Editing creates a new version.
> - New: **metric acceptance assertions** (e.g. `revenue_ytd FY2025 == the CFO's close
>   ± 0.5%`, and `sum(by region) == ungrouped total`), run on publish and on a schedule.
>   This is the mechanism by which a tenant *earns* trust in a configured metric.

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

> **Phase 0 update ([ADR-006](adr/ADR-006-metric-definition-and-kpi-engine.md)):** this
> flat list conflates four different things — aggregations (`sum`, `count`), time
> *periods* (`YTD`, `QTD`, `MTD`), compositions (`ratio`, `growth rate`, `variance`,
> `calculated`), and a *model* (`forecast`). As an enum it cannot express an ordinary
> executive question such as "YTD of a ratio versus prior year." It is replaced by **five
> composable AST node kinds** — aggregation, ratio, arithmetic, time-shift, window — plus
> orthogonal period and grain parameters.
>
> `revenue_ytd` survives as a named, owned, governed metric (executives refer to it by
> name and it needs its own target), but it is *defined by composition*: the `revenue`
> aggregation evaluated over the `fiscal_ytd` period resolved from the tenant's fiscal
> calendar.
>
> **`forecast` is not a metric node.** It is a model producing observations with
> `origin: forecast`, a model id, and a confidence interval — consumed like a metric but
> never mixed into an actuals aggregation. Blurring the two would let a projection appear
> as a fact, violating principle 12.
>
> **Ratios declare their aggregation order.** `win_rate` is `SUM(won)/SUM(total)` at the
> query's grouping level, not the average of per-row rates, and ratio metrics are marked
> non-additive so nothing sums them across slices.

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

> **Phase 0 update ([ADR-012](adr/ADR-012-data-lineage.md)):** lineage is **derived** from
> the metric AST and the binding graph on demand — never stored as a parallel structure.
> Stored lineage is maintained lineage, and maintained lineage drifts; a trust artifact
> that lies is worse than none. (A materialized projection exists purely as a *cache*,
> labelled as such and keyed by `config_version`. `MetricLineage` is removed as a
> system-of-record entity from
> [`09_DOMAIN_MODEL_API_CONTRACTS.md`](09_DOMAIN_MODEL_API_CONTRACTS.md).)
>
> The chain above is **design-time lineage** — "how is this number defined?" It cannot
> answer the question that actually destroys executive confidence: *"why is this different
> from the screenshot I took last Tuesday?"* ADR-012 therefore adds **run-time
> provenance** — `config_version`, `metric_version`, `plan_hash`, `data_snapshot_id`,
> source watermarks, and `computed_at` on every result and every stored observation — plus
> explicit **restatement** handling: observations are append-only, a restatement is a
> recorded event, and insight signals caused purely by restatement are suppressed rather
> than reported as business changes.
>
> Lineage is also **bidirectional**: downward for drill-to-source, upward for **impact
> analysis** ("what depends on this field?"), which becomes a publish-time gate. And it is
> **authorization-aware** — nodes the principal cannot see are redacted placeholders that
> preserve the shape of the chain without leaking its content.

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

> **Phase 0 update ([ADR-008](adr/ADR-008-analytical-storage.md) §8,
> [ADR-012](adr/ADR-012-data-lineage.md) §3):** these signals require a **stable
> history**, which the documentation assumed rather than provided. A per-tenant,
> append-only **`MetricObservation`** store records
> `(metric_version, period, dimension_key, value, computed_at, config_version,
> data_snapshot_id, origin)`. Recomputing history on each run would be both expensive and
> *wrong* — it would erase the record of what the business believed at the time.
>
> Two further requirements, both missing:
>
> - **Signal state and suppression.** Without it, the same signal re-fires every run and
>   executives switch the attention surface off. Signals carry state, dedupe, and
>   restatement-aware suppression.
> - **A scoped-scan policy.** Signals × metrics × dimensions × dimension values explodes —
>   50 metrics × 5 dimensions × 20 values × 13 signal types is ~65,000 evaluations per
>   tenant per run. Detection descends hierarchically (slice only where the parent metric
>   already signals), runs set-based inside the analytical store rather than per-series in
>   Python, and operates under a per-run evaluation budget. Anything dropped by that budget
>   is logged, never silently truncated.

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
