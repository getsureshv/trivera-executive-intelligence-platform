# 02 — Workbook Findings

## Purpose of this document

The source prototype is `TriVera Executive Dashboard.xlsx`. This document records what
the workbook contains and, critically, **how each part of it maps onto platform
metadata** rather than onto application code. It is the bridge between the prototype and
the production architecture.

> **The workbook is a prototype only.** It illustrates intent. The production platform
> must never recreate the workbook as the application, and must never hard-code its
> values, KPIs, or its `Total / People / Process / Technology / Enterprise` selector.

## Business areas in the workbook

The workbook is organized into business areas, each roughly corresponding to a leadership
concern:

- Dashboard
- Business Development
- Revenue
- Marketing
- Client Health
- Operations
- AI & Innovation
- Marketing Intelligence
- Workforce & Organizational Health

In the platform these are **not** sheets or hard-coded pages. They become configurable
**domains** used to group semantic entities, metrics, and dashboard sections. A tenant
in a different industry can rename, remove, or add domains without any code change.

## Executive KPIs in the workbook

The workbook surfaces executive KPIs including:

- Revenue YTD
- Revenue This Month
- Pipeline Value
- 90-Day Forecast
- Win Rate
- Proposals Won
- Active Clients
- Projects On Track
- Consultant Utilization
- Cash Collected
- New Leads
- Proposal Value

Each of these becomes a **governed `Metric`** with a code, a definition, an aggregation,
a format, dimensions, targets, and thresholds — see
[`05_KPI_INSIGHT_ENGINE.md`](05_KPI_INSIGHT_ENGINE.md). They are **seed content for a KPI
pack**, not literals embedded anywhere in code. For example `Revenue YTD` becomes a
metric `revenue_ytd` defined as `SUM(Revenue.Amount)` over a `fiscal_ytd` window — its
value is computed by the metric engine, never read from a cell.

## The recurring selector

The workbook uses a recurring selector with these values:

- Total
- People
- Process
- Technology
- Enterprise

This is the single most important thing to get right architecturally. In the workbook it
drives cell lookups. In the platform it must become a **configurable dimension**, not
code.

### Critical architectural rule

These values must **not** become hard-coded application logic. They must become
configurable `Dimension` / `DimensionValue` metadata.

**Bad (what the workbook effectively does):**

```python
if selectedView == "People":
    get Revenue!D3
```

**Good (what the platform does):**

```
Metric:          revenue_ytd
Dimension:       operating_model
Dimension Value: people
```

The selector becomes a dimension (here named `operating_model`) whose values
(`total`, `people`, `process`, `technology`, `enterprise`) are just rows of metadata a
tenant can edit. Selecting "People" becomes a filter on a governed metric query, not a
branch in code and not a cell reference.

## From cells to a governed pipeline

The platform replaces spreadsheet cell references with an explicit chain:

```
Source Field → Transformation → Semantic Field → Metric → Query → Dashboard / Chat / Alert / Insight
```

Read left to right: a raw field from a connected source is transformed into a normalized
semantic field; one or more semantic fields define a governed metric; a governed query
computes that metric with dimensions and filters; and the result feeds a dashboard
widget, a chat answer, an alert, or an insight. Every consumer travels the same path, so
there is exactly one definition of every number.

## Mapping summary

| Workbook artifact | Platform representation | Notes |
| --- | --- | --- |
| A sheet / business area | `Domain` (configuration) | Groups entities, metrics, dashboard sections. |
| A KPI cell (e.g. Revenue YTD) | `Metric` (governed) | Computed by the metric engine, never read from a cell. |
| A KPI value in a cell | Result of a governed metric query | Carries period, filters, freshness, quality, lineage. |
| `Total/People/Process/Technology/Enterprise` selector | `Dimension` + `DimensionValue` | Configurable; drives filters, not branches. |
| A hidden calc feeding a KPI | `Transformation` + `SemanticField` | Documented and versioned. |
| A source column behind a calc | `SourceField` on a `SourceObject` | Reached only through a connector. |

## What to carry forward vs. leave behind

**Carry forward** (as configuration/seed content): the KPI list as a starting KPI pack,
the business areas as candidate domains, the operating-model selector as a dimension, and
the overall executive framing of the Dashboard sheet as inspiration for the executive
command center.

**Leave behind** (never port): cell references, sheet-specific formulas, per-view
lookups, hard-coded value lists, and any assumption that these particular KPIs or these
particular dimension values are universal. Another tenant will have different KPIs and a
different selector, and the platform must accommodate that purely through configuration.
