# ADR-006: Metric Definition and KPI Engine

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`05_KPI_INSIGHT_ENGINE.md` defines a metric with an `expression` field, illustrated as
`SUM(Revenue.Amount)`, and lists sixteen supported "metric types" ranging from `sum` to
`forecast` to `calculated metric`.

Phase 0 review found three problems:

1. **`expression` is undefined.** Is it a string? Who parses it? If it is free text, then
   it cannot be diffed, cannot be validated at publish time, cannot produce lineage
   automatically, and cannot be compiled safely to more than one analytical dialect. A
   free-text expression is an escape hatch that would quietly reintroduce ad-hoc SQL —
   the exact thing guardrail 6 forbids.
2. **The "metric types" list conflates three different things.** `sum`/`count`/`average`
   are *aggregations*. `YTD`/`QTD`/`MTD`/`rolling average` are *time transformations*.
   `ratio`/`growth rate`/`target variance`/`calculated` are *compositions*. `forecast` is
   a *model*. Treating them as one flat enum guarantees a combinatorial mess (there is no
   enum value for "YTD of a ratio compared to prior year").
3. **`target` and `thresholds` are scalar fields on the metric** in `05`, but targets vary
   by period and by dimension slice (a regional target differs from the company target,
   and Q1's differs from Q4's). `09_DOMAIN_MODEL_API_CONTRACTS.md` correctly models them
   as entities. The two documents disagree.

## Decision

### 1. A metric is a typed **AST**, persisted as JSON. Not a string.

```
MetricDefinition
  aggregation      : { fn: sum|count|count_distinct|min|max|avg|median|percentile,
                       field: SemanticFieldRef, filter: PredicateAST? }
    | ratio        : { numerator: MetricRef|Aggregation,
                       denominator: MetricRef|Aggregation,
                       zero_denominator_policy: null|zero|error }
    | arithmetic   : { op: +|-|*|/, operands: [MetricRef|Literal|Metric] }
    | time_shifted : { base: MetricRef, shift: prior_period|prior_year|custom }
    | window       : { base: MetricRef, fn: rolling_avg|rolling_sum|cumulative,
                       periods: int, grain: day|week|month|quarter|year }
    | variance     : { actual: MetricRef, comparison: MetricRef|TargetRef,
                       mode: absolute|percent }
```

Orthogonal to the AST, a metric declares:

- `time_grain` and `time_anchor` (which semantic time anchor it uses — see ADR-005);
- `default_period` (`fiscal_ytd`, `mtd`, `last_90d`, …) resolved against the tenant's
  fiscal calendar;
- `allowed_dimensions` (validated against reachable join paths, ADR-005/007);
- `additivity` derived from the underlying semantic field, and **checked**: a metric that
  averages a semi-additive field over time is rejected at publish;
- `format`, `unit`/`currency`, `direction` (higher-is-better / lower-is-better —
  required for the insight engine to know whether a change is good);
- `owner`, `status`, `version`, `effective_from`/`effective_to`.

**YTD/QTD/MTD are periods, not metric types.** `revenue_ytd` is `revenue` (an
aggregation) evaluated over the `fiscal_ytd` period. We keep `revenue_ytd` as a *named,
governed metric* because executives refer to it by name and because it needs its own
target and owner — but it is defined by composition, not by a special type. This
collapses the sixteen-value enum into five node kinds plus period/window parameters,
which is what makes the compiler tractable.

**`forecast` is not a metric node.** Forecasting is a model producing a
`MetricObservation` series with a distinct provenance (`origin: forecast`, model id,
confidence interval). It is consumed like a metric but never mixed into an actuals
aggregation. Blurring them would let a projection appear as a fact — a direct violation of
principle 12.

### 2. Ratio semantics are declared, because ratio-of-sums ≠ sum-of-ratios

Every `ratio` node must declare its aggregation order. `win_rate` is
`SUM(won) / SUM(total)` at the query's grouping level — not the average of per-row rates.
The compiler computes ratios **after** aggregation at the requested grain, and a ratio
metric is marked `non_additive`, so the UI and the insight engine never sum or average it
across slices. Getting this wrong is the classic BI failure where subtotals do not add up
to the total; declaring it in metadata is what prevents it.

### 3. Targets and thresholds are entities, not fields

`MetricTarget(metric_version, period, dimension_scope, value, source, owner)` and
`MetricThreshold(metric_version, kind, comparator, value, severity)`. `dimension_scope` is
a (possibly empty) set of dimension filters, so company-wide and per-region targets
coexist, with most-specific-match resolution. `05_KPI_INSIGHT_ENGINE.md` is corrected to
match `09`.

Targets carry `source` (`imported from plan`, `manually set`, `derived`) and appear in
lineage. A target is evidence too; an executive comparing to a target nobody can attribute
is not being served.

### 4. Published metric versions are immutable

Publishing creates an immutable `MetricVersion` with a content hash of the AST plus
resolved references. Every query result and every stored `MetricObservation` records the
`metric_version_id` it was computed under. Editing a published metric creates a new
version; it never mutates the old one. Without this, lineage and "why did the number
change?" are unanswerable, and `07`'s rollback promise is empty.

### 5. Metric acceptance assertions (new capability)

A metric may carry **assertions** that run automatically on publish and on a schedule:

```
assert revenue_ytd (period=FY2025, filters={}) == 12_345_678 ± 0.5%   # per CFO close
assert revenue_ytd >= 0
assert sum(revenue_ytd by region) == revenue_ytd                       # additivity check
assert freshness(revenue_ytd) < 26h
```

This is the mechanism by which a tenant *earns* trust in a configured metric, and it is
cheap to build. Its absence from the current documentation is a significant gap: the
platform's entire value is trust, and there is currently no way to prove a metric is
right. Failed assertions block publish and raise a governance signal.

### 6. Metric computation is a compiled query, executed by the Governed Query Service

The metric engine **compiles**; it does not execute. It turns
`(MetricVersion, period, grouping, filters, auth scope)` into a `QueryPlan`, which ADR-007
executes. Pure, deterministic, no I/O — therefore exhaustively testable, which matters
more here than anywhere else in the system.

## Alternatives Considered

- **Free-text expression strings (as documented), parsed at execution.** Rejected: not
  diffable, not statically validatable, no automatic lineage, encourages SQL leakage.
- **Expressions as restricted SQL.** Rejected. It looks pragmatic and it is a trapdoor:
  once tenants write SQL, portability across analytical engines dies, lineage becomes
  best-effort parsing, and guardrail 6 is violated in substance while satisfied in
  letter.
- **Adopt MetricFlow / Cube / Malloy as the metric definition language.** Strong prior
  art and the direct inspiration for the AST above. Rejected as a runtime dependency for
  the reasons in ADR-005 (developer-artifact workflow vs. governed per-tenant UI
  configuration). Their semantics are adopted; their toolchain is not.
- **Materialize every metric as a pre-computed cube.** Rejected as the primary model —
  the dimension/filter combinatorics explode and it destroys ad-hoc drill-down. Retained
  as a *selective* optimization (ADR-008) driven by observed query patterns.
- **Keep the flat sixteen-type enum.** Rejected: it cannot express composition, which is
  the majority of real executive KPIs.

## Rationale

The metric definition is the product's unit of truth. Everything that makes it a durable
asset — versioning, diffing, rollback, lineage, portability, validation, explanation to an
executive — requires it to be **structured data**, not text. Choosing an AST is therefore
not an implementation preference; it is what makes the governance claims in `05` and `07`
achievable at all.

Decomposing the type enum into composable nodes is what keeps the compiler small enough to
be provably correct, which is the only defensible position for code that produces the
numbers a CEO acts on.

## Consequences

- Positive: metrics are diffable, reviewable, and rollback-able; lineage falls out of the
  AST for free (ADR-012).
- Positive: publish-time validation can prove a metric is well-formed, its dimensions are
  reachable, and its aggregation respects additivity.
- Positive: composition means a small library of primitives covers the workbook's KPI list
  and far more.
- Positive: assertions give tenants an objective trust gate.
- Negative: the metric editor UI must build an AST, not accept a formula box. This is more
  UI work and is the right trade.
- Negative: an AST is less immediately expressive than SQL; some legitimate metrics will
  be blocked until a node kind is added. Accepted; ship node kinds quickly.
- Negative: `05_KPI_INSIGHT_ENGINE.md` requires correction (targets/thresholds as
  entities; type list restructured; forecast separated).

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Compiler produces a subtly wrong aggregation | Golden-dataset tests per node kind; property-based tests (e.g. sum over partition equals sum over whole); metric assertions | Compiler is pure and typed; branch-coverage gate; every bug becomes a permanent golden case |
| Ratio/subtotal inconsistency ("parts don't add to the whole") | Additivity assertions run automatically | Non-additive metrics are marked and never summed by UI or insight engine |
| Pressure to add a raw-SQL escape hatch | Feature requests; review | Fast cadence on new node kinds; governed source-side views (ADR-005) as the sanctioned pressure valve |
| Metric version explosion clutters governance | Version count per metric | Draft edits do not create versions; only publish does |
| Forecast values mistaken for actuals | Provenance field on every observation | Forecasts are a separate origin and are visually distinct (principle 12) |
| Assertions become stale and are disabled | Assertion pass-rate dashboard | Assertion failures are governance events with an owner |

## Future Considerations

- Metric-level caching/materialization hints derived from observed usage.
- Cohort, funnel, and retention node kinds (needed for SaaS tenants).
- Allocation and driver-tree metrics (revenue = volume × price), which are what turn KPI
  reporting into genuine decision support and are a strong candidate for the first
  post-MVP differentiator.
- Statistical process control parameters attached to metrics, feeding the insight engine.
- Importing metric definitions from a tenant's existing dbt/LookML assets as a migration
  accelerator.
