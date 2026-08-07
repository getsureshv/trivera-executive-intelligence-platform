# ADR-005: Semantic Model — Contracts, Bindings, Grain and Joins

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

**This is the load-bearing ADR of Phase 0.** If it is wrong, the platform's central
promise — onboard a company by configuration, not code — is false.

## Context

The stated requirement is that Company A's `Invoice.TotalAmount` and Company B's
`billing.transactions.net_value` must both map to `Revenue.Amount`, after which the entire
platform operates against `Revenue.Amount`.

`04_DATA_CONNECTORS_SEMANTIC_LAYER.md` models this as a **field-level `FieldMapping`**:
`SourceField → Transformation → SemanticField`. Phase 0 review concludes that **this is
necessary but nowhere near sufficient**, and that shipping it as designed would produce a
platform that renames fields successfully and computes revenue wrongly.

Four gaps, each of which independently breaks the promise:

**1. Grain is undeclared.** Company A's `Invoice` is one row per invoice header. Company
B's `billing.transactions` is one row per transaction *line*, and it probably includes
refunds, credit memos, intercompany transfers, and test records. `SUM` over both is
"revenue" in neither case without a declared row-qualifying predicate. Field-to-field
mapping cannot express "only rows where `type IN ('SALE','ADJ')` and
`is_intercompany = false` count." Without this, both tenants map cleanly and one of them
gets a wrong number that looks entirely plausible.

**2. There is no join model.** `05_KPI_INSIGHT_ENGINE.md` shows `revenue_ytd` sliceable by
`BusinessUnit, Region, ServiceLine`. Those attributes almost never live on the revenue
table. Reaching them requires a declared relationship and join path. The documentation has
`SemanticEntity` and `SemanticField` but **no `SemanticRelationship`**. As written, a
metric can only be sliced by columns that happen to sit on the same physical table — which
is close to useless for real schemas, and silently so.

**3. Semantic units are undeclared.** `Revenue.Amount` is a money quantity. Company A
stores gross including tax, in cents, in the invoice's local currency. Company B stores
net of discounts, in dollars, already converted to USD at an unspecified rate. Both map to
`Revenue.Amount`. The resulting number is not comparable, not auditable, and not correct
for at least one of them. A semantic field without a declared unit, currency, and
sign/scale contract is not a semantic field.

**4. Time semantics are undeclared.** `revenue_ytd` is `SUM(Revenue.Amount)` over
`fiscal_ytd`. Over *which date*? Invoice date, service date, recognition date, payment
date, or posting date? Different departments in the *same* company answer differently.
There is no `time anchor` concept in the model.

There is a fifth, subtler defect in `02_WORKBOOK_FINDINGS.md`: `total` is listed as a
peer `DimensionValue` alongside `people`, `process`, `technology`, `enterprise`. `Total`
is not a sibling — it is the aggregate over the siblings. Modeling it as a value means any
unfiltered `SUM` double-counts. This is workbook thinking surviving into the metadata
model.

## Decision

**Adopt a contract-and-binding semantic model. The unit of mapping is an entity binding,
not a field mapping. Field mappings become components of a binding.**

### 1. `SemanticEntity` becomes a **Semantic Contract**

A semantic entity declares, as governed metadata:

| Property | Meaning |
| --- | --- |
| `code`, `name`, `description` | `Revenue`, business definition |
| `kind` | `fact` \| `dimension` \| `bridge` |
| `grain` | Declared meaning of one row, e.g. "one recognized revenue line" |
| `natural_key` | The field(s) uniquely identifying a row at that grain |
| `required_fields` | Fields a valid binding **must** supply (`Amount`, `RecognizedAt`, `CustomerRef`) |
| `optional_fields` | Fields a binding may supply |
| `time_anchors` | Named date/timestamp fields, with one marked default (`recognized_at`) |
| `default_filter_intent` | Prose statement of what rows qualify (e.g. "excludes intercompany, excludes voided") |

A `SemanticField` declares:

| Property | Meaning |
| --- | --- |
| `code` | `Revenue.Amount` |
| `data_type` | canonical type |
| `semantic_type` | `money` \| `count` \| `ratio` \| `duration` \| `identifier` \| `category` \| `timestamp` |
| `unit` | for money: currency handling policy; for duration: unit; for ratio: numerator/denominator meaning |
| `additivity` | `additive` \| `semi_additive(over: [dims])` \| `non_additive` |
| `sign_convention` | e.g. credits negative |
| `nullability_policy`, `precision` | correctness constraints |
| `classification` | `public` \| `internal` \| `confidential` \| `restricted` (drives ADR-010) |

**Additivity is not optional metadata.** A headcount or a balance is semi-additive over
time; averaging it across periods is wrong. The metric compiler (ADR-006) must refuse to
generate an invalid aggregation, and it can only do that if additivity is declared.

### 2. `EntityBinding` — the real unit of mapping

A binding attaches **one semantic entity to one source object** (or to a governed
source-side view) for one tenant:

```
EntityBinding
  semantic_entity : Revenue
  source_object   : pg.public.billing_transactions
  grain_assertion : natural key columns that make one row = one revenue line
  row_filter      : a governed predicate over source fields (structured, not SQL text)
  field_bindings  : [ SourceField -> SemanticField, with Transformation ]
  time_bindings   : [ semantic time anchor -> source field ]
  currency_policy : source currency field / fixed currency / conversion rule
  status/version/approver/change_reason
```

Multiple bindings may target the same entity (revenue from two source systems), in which
case they **union** at the same declared grain, with a `source_priority` for
deduplication. This is how a company that invoices from NetSuite and Stripe gets one
`Revenue`.

Binding **validation is mandatory and automated**, and runs before publish:

- every `required_field` of the contract is bound;
- declared grain holds — the natural key is unique in a sampled/profiled check;
- types are compatible after transformation;
- money fields resolve to a currency;
- every declared time anchor is bound to a date/timestamp;
- the row filter references only fields on the bound object;
- null rates and ranges from profiling are within declared policy.

A binding that fails validation cannot be published. This turns "onboard by configuration"
into something verifiable rather than hopeful.

### 3. `SemanticRelationship` — the missing join model

```
SemanticRelationship
  from_entity, from_fields
  to_entity,   to_fields
  cardinality : many_to_one | one_to_one | one_to_many | many_to_many(via bridge)
  role        : optional named role (e.g. "billing_customer" vs "parent_customer")
  join_type   : inner | left
```

Relationships are declared at the **semantic** level and resolved to physical joins
through each side's bindings. The governed query compiler (ADR-007) finds join paths over
this graph.

Two hard rules, both enforced in the compiler:

- **Ambiguous paths are an error, not a guess.** If two distinct paths connect `Revenue`
  to `Region`, the compiler refuses and requires the metric or query to name the role.
  Silently picking a path is how BI tools produce numbers nobody can defend.
- **Fan-out is detected and refused or corrected.** Joining a fact to a one-to-many
  relationship before aggregating multiplies rows. The compiler must detect fan-out from
  declared cardinality and either aggregate before joining or reject the query with a
  specific error. This is the single most common source of wrong numbers in semantic
  layers.

### 4. Transformations are a closed, typed expression set — not code, not SQL

`Transformation` is a structured expression (persisted as JSON AST), drawn from a closed
library: cast, scale, round, currency-convert, trim/case, coalesce, string→date parse with
explicit format, categorical remap via a lookup table, sign flip, conditional
(`case/when` over source fields), and safe arithmetic over fields of the same binding.

**No arbitrary SQL, no Python, no user-supplied code.** Reasons: it must be safe to
execute against a tenant's data, statically analyzable for lineage, diffable for
versioning, and portable across analytical engines (ADR-008). An escape hatch of "just
write SQL here" would destroy all four properties and would violate guardrail 6.

When the closed set is genuinely insufficient, the answer is a **new transformation
primitive shipped in the platform** — reviewable, tested, available to all tenants — not
a per-tenant script. This is exactly what "configuration over customization" means when
it is taken seriously.

### 5. Dimensions, hierarchies, and the `Total` correction

- A `Dimension` is a semantic attribute (or a conformed dimension entity) usable as a
  slicing axis. Its allowed values may be enumerated (`DimensionValue`) or open.
- **`total` is not a `DimensionValue`.** "Total" is the *absence* of a filter on that
  dimension, represented in the query contract as `grouping: none` / `filter: none`, and
  rendered in the UI as an "All" affordance. `02_WORKBOOK_FINDINGS.md` must be corrected.
- `DimensionHierarchy` (level ordering, e.g. `region → country → territory`) is added, so
  drill-down is metadata-driven rather than screen-specific.
- The workbook's selector remains exactly what `02` says it is — a configurable dimension
  named e.g. `operating_model` with values `people`, `process`, `technology`,
  `enterprise` — with `total` removed as a value.

### 6. Conformance across tenants

`Revenue`, `Revenue.Amount`, and the core contracts ship as a **platform-owned semantic
pack**, versioned by us. A tenant may extend it (add fields, add entities) but may not
silently redefine a platform contract's meaning; redefinition creates a tenant-local
entity with a distinct code. Without this, "the rest of the platform operates against
`Revenue.Amount`" is untrue the moment two tenants disagree about what it means, and
cross-tenant benchmarking (a plausible future product) becomes impossible.

## Alternatives Considered

- **Keep field-level mapping only (as documented).** Rejected — the four gaps above.
  It is the fastest path to a demo and the fastest path to an indefensible number.
- **Adopt an existing semantic layer spec (dbt Semantic Layer / MetricFlow, Cube,
  Malloy, LookML).** Seriously considered; these solve exactly the join/grain/fan-out
  problems above and are battle-tested. Rejected as the *runtime* because (a) all of them
  are code/YAML-artifact-centric with a developer edit-and-deploy loop, which is
  incompatible with per-tenant, UI-driven, human-approved, versioned configuration; (b)
  none of them models source→semantic *binding provenance* well enough for our lineage
  product feature; (c) embedding one makes it a hard dependency in the most load-bearing
  part of the system. **However, their models are the reference design** — the decisions
  above (declared grain, cardinality-aware joins, fan-out detection, additivity) are
  deliberately adopted from that prior art rather than invented. If we later need
  compilation breadth, MetricFlow's or Cube's compiler is a candidate backend behind our
  AST.
- **Let AI infer grain, joins, and filters at query time.** Rejected outright. It violates
  principles 6, 11, and 12 and makes numbers non-reproducible.
- **Skip the semantic layer and map source fields directly to metrics.** Rejected — it is
  the thing the product exists to prevent (guardrail 5).
- **Allow per-tenant SQL views as the binding target.** Partially adopted: a binding may
  target a *governed, versioned, reviewed* source-side view where the source supports it,
  because that is sometimes the only sane way to express a complex qualification. But the
  view is a first-class governed object with lineage, not an escape hatch, and the row
  filter/grain declarations still apply.

## Rationale

The requirement is not "rename fields." It is "two companies with structurally different
data produce the same trustworthy number." Renaming is the easy 20%. The hard 80% is
grain, qualification, joins, units, and time — and every one of those is currently absent
from the model.

Making the **binding** (not the field mapping) the governed unit is what makes validation
possible: you can automatically check that a binding satisfies a contract, and you cannot
meaningfully check that an isolated field mapping is "correct." That validation is what
lets a non-engineer onboard a company safely, which is the actual product requirement.

## Consequences

- Positive: the Company A / Company B test is genuinely satisfiable, including the parts
  that would otherwise fail silently.
- Positive: metrics become sliceable by real dimensions via declared relationships.
- Positive: fan-out and invalid aggregations become compiler errors instead of wrong
  numbers.
- Positive: lineage (ADR-012) is derivable from the binding graph, not hand-maintained.
- Positive: binding validation gives onboarding an objective "is this configured
  correctly?" gate.
- Negative: substantially more metadata to author per tenant. This is the real cost and it
  must be attacked by the AI mapping assistant and by industry semantic packs — the
  assistant's job is now *proposing bindings*, which is a bigger and more valuable job
  than proposing field pairs.
- Negative: the semantic model is now complex enough that the steward UI is a serious
  design problem, not a CRUD form.
- Negative: `04_DATA_CONNECTORS_SEMANTIC_LAYER.md`, `02_WORKBOOK_FINDINGS.md`, and
  `09_DOMAIN_MODEL_API_CONTRACTS.md` all require updating.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Model complexity makes onboarding slower than the sales promise | Time-to-first-trusted-KPI measured per tenant | Industry semantic packs; AI-proposed bindings; validation that tells the steward exactly what is missing |
| Declared grain is asserted but false | Automated uniqueness check on the natural key during validation and on every ingest | Grain violation is a blocking data-quality signal, not a warning |
| Ambiguous join paths frustrate users | Compiler error rate telemetry | Named roles; a path-picker in the steward UI that persists the choice as metadata |
| Closed transformation library proves too restrictive | Track "cannot express" escalations | Ship new primitives on a fast cadence; governed source-side views as the pressure valve |
| Currency conversion policy disputes | Reconciliation against the tenant's ledger | Conversion policy is explicit metadata (rate source, rate date basis) and appears in lineage |
| Semi-additive measures aggregated incorrectly | Property tests over additivity in the compiler | Compiler refuses invalid aggregation; no silent fallback |
| Tenants redefine platform contracts, breaking conformance | Publish-time check on platform-owned codes | Redefinition forces a tenant-local code |

## Future Considerations

- Conformed dimensions shared across entities (a single `Customer` dimension serving
  `Revenue`, `Pipeline`, `Support`).
- Slowly-changing dimensions (type 2) — needed the first time a customer is re-segmented
  and history must be preserved.
- Multi-currency reporting with as-of and average-rate policies.
- Semantic pack marketplace by industry; this is the commercial expression of
  "configuration over customization."
- Reconciliation bindings: declaring a source-of-truth total (e.g. the GL) that a bound
  entity must tie out to, run as an automated test.
