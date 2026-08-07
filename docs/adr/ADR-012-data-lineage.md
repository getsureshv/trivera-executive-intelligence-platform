# ADR-012: Data Lineage and Provenance

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`05_KPI_INSIGHT_ENGINE.md` defines lineage as a chain from dashboard widget down to data
source, and states — correctly and importantly — that lineage is a **product feature, not
only a developer feature**. It is the concrete answer to executive question 8 ("What
evidence supports that conclusion?").

Phase 0 review found two problems.

**Problem 1: `09_DOMAIN_MODEL_API_CONTRACTS.md` models `MetricLineage` as a stored
entity.** Stored lineage is maintained lineage, and maintained lineage drifts. The moment
it disagrees with reality, it is worse than no lineage — it is a trust artifact that
lies. Lineage must be *derived*.

**Problem 2: the documentation describes one thing but the product needs two.** The chain
in `05` answers "how is this number defined?" It cannot answer "why does this number
differ from the screenshot I took last Tuesday?" — which is the question that actually
destroys executive confidence, and which arises constantly during onboarding when
mappings, targets, and definitions are churning.

## Decision

**Lineage is derived, never stored as a parallel structure. It has two dimensions, both
required, and they are named separately so they are never conflated.**

### 1. Design-time lineage ("how is this defined?") — derived from the graph

Computed on demand by traversing objects that already exist:

```
Widget → Metric(version) → MetricAST → SemanticField(s)
   → EntityBinding(s) → Transformation(s) → SourceField(s)
   → SourceObject → DataSource → Connector
   ⤷ plus: SemanticRelationship join paths, RowPolicy predicates applied,
           MetricTarget source, FiscalCalendar used
```

Every node in this graph is an existing governed object with a version. There is **no
separate lineage table to maintain**; `MetricLineage` as an entity is removed from the
domain model. What *is* stored is a **materialized projection** for query performance —
a cache, explicitly labelled as such, rebuilt from the graph and invalidated by
`config_version`. A cache may be stale and be repaired; a system of record may be wrong
and be believed. The distinction is the whole point.

The AST-based metric definition (ADR-006) and the binding model (ADR-005) are what make
this derivation possible. If the metric expression were a free-text string, lineage would
be regex-parsing and guesswork — which is the deeper reason ADR-006 rejects strings.

### 2. Run-time provenance ("why is this number what it is, right now?")

Every `QueryResult` carries (ADR-007):

```
provenance {
  config_version, metric_version_id, plan_hash,
  data_snapshot_id, source_watermarks[], pipeline_run_ids[],
  computed_at, cache_hit, engine,
  target_version_id, fiscal_calendar_version
}
```

Every stored `MetricObservation` (ADR-008) carries the same. Together these answer:

- *"The number changed and nobody told me."* → diff `config_version` and
  `metric_version_id` between the two observations; the governance log names the author,
  approver, and reason (ADR-013).
- *"Is this stale?"* → `source_watermarks` versus the freshness policy.
- *"Did the source restate history?"* → the same period, same definition, different
  `data_snapshot_id`, different value. **This is restatement, and it must be visible
  rather than silent.**

### 3. Restatement policy (new capability)

When re-ingestion changes a value for a period already observed under the same
`config_version` and `metric_version`, the platform:

1. keeps both observations (append-only; observations are never updated in place);
2. records a `Restatement` event with magnitude and affected periods;
3. raises a governance signal if the magnitude exceeds a configured tolerance;
4. suppresses insight-engine anomaly signals caused purely by restatement, rather than
   reporting a restated prior period as a business event.

Point 4 matters more than it appears: without it, the insight engine will confidently tell
a CEO that revenue moved when in fact a mapping was corrected. Nothing erodes trust in an
intelligence product faster than confident wrongness about its own machinery.

### 4. Bidirectional traversal

- **Downward (drill-to-source):** the executive/auditor path — widget to source field.
- **Upward (impact analysis):** the steward path — "which metrics, dashboards, alerts, and
  insights depend on this source field / binding / semantic field?" This is what makes
  change safe: before publishing a mapping change, the steward sees the blast radius.
  Impact analysis is a **publish-time gate**, not just a report: a change affecting a
  `restricted` metric or one with acceptance assertions requires explicit acknowledgement.

### 5. Granularity and honesty

Lineage is at **field and binding granularity** — sufficient for the product claim and
derivable exactly. It is **not** row-level provenance ("which invoice rows produced this
sum"), which would require per-row tracking at prohibitive cost. Drill-through to
contributing rows is offered instead as a *governed query* at finer grain, subject to the
same authorization. The distinction is stated in the UI so the claim is not oversold.

### 6. Lineage respects authorization

A lineage view must not disclose source systems, table names, or field names the principal
cannot see. Nodes the principal is not authorized to view are rendered as redacted
placeholders that preserve the shape of the chain without leaking its content. Lineage is
a governed read like any other (ADR-010).

## Alternatives Considered

- **Store lineage as first-class edges written by the application (as in `09`).** Rejected
  — drift. Adopted only as an explicitly-labelled derived cache.
- **Parse generated SQL to extract lineage** (the approach most BI tools use, e.g. via
  sqlglot/Calcite lineage extraction). Rejected as primary: it recovers, imperfectly,
  information we already have exactly in the AST and binding graph. Retained as a
  *verification* technique — cross-checking derived lineage against the compiled plan is a
  good test, and a mismatch indicates a compiler bug.
- **Adopt OpenLineage / Marquez or a catalog (DataHub, OpenMetadata).** Rejected as the
  internal model: those are designed for pipeline-level lineage across an enterprise's
  many tools, at coarser granularity, and they cannot express our binding/metric-AST
  semantics. **Emitting OpenLineage events outward** is a good future integration for
  customers with an existing catalog, and is noted as such.
- **Row-level provenance.** Rejected on cost/benefit; drill-through covers the need.
- **Design-time lineage only (as documented).** Rejected — it leaves the most damaging
  executive question unanswerable.

## Rationale

Lineage is the product's trust mechanism, so it must be *incapable* of being wrong. The
only way to guarantee that is to derive it from the objects that actually determine the
computation. Everything else is a copy, and copies drift.

Separating design-time lineage from run-time provenance is the substantive addition of
this ADR. They are different questions, asked by different people, answered from different
data, and conflating them — as the current documentation does — means the second one never
gets built.

## Consequences

- Positive: lineage cannot disagree with computation, because it is computed from the same
  objects.
- Positive: "why did this change?" is answerable exactly, with author, approver, and
  reason attached.
- Positive: impact analysis makes semantic change safe, which is what allows fast
  onboarding iteration.
- Positive: restatement becomes a visible, explainable event.
- Negative: graph traversal can be expensive for deep chains; requires the derived cache
  and careful invalidation.
- Negative: append-only observations grow; needs partitioning and retention policy.
- Negative: `09_DOMAIN_MODEL_API_CONTRACTS.md` must be corrected (`MetricLineage` removed
  as a system-of-record entity).

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Derived-lineage cache goes stale and is trusted | Cache carries `config_version`; mismatch forces recompute | Cache is labelled derived; recompute is always available |
| Lineage discloses unauthorized source details | Authorization tests over lineage responses | Redacted nodes preserve shape, not content |
| Restatement floods the insight engine with false signals | Signal-source attribution telemetry | Restatement-aware suppression (§3.4) |
| Observation table growth | Size and partition telemetry | Time partitioning, per-tenant retention, rollups |
| Impact analysis is slow enough that stewards skip it | Publish-flow latency | Precomputed reverse index maintained with the derived cache |
| Users read field-level lineage as row-level proof | Support questions | Explicit UI wording; drill-through offered as the row-level answer |

## Future Considerations

- OpenLineage event emission for customers with an existing data catalog.
- Lineage diffing between two `config_version`s ("what changed in how we compute
  revenue?") — a genuinely compelling governance feature and cheap once versions are
  immutable.
- Time-travel queries: "show the dashboard exactly as it was on 2026-03-31," which the
  combination of immutable versions and append-only observations already makes possible.
- Certified-metric badges driven by assertion pass-rate plus lineage completeness.
- Cross-system lineage into the customer's upstream ETL, via connector-provided metadata.
