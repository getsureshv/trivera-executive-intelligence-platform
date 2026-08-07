# 10 — Implementation Roadmap

The platform is built in phases. Each phase produces working, verified capability and
respects the core principles and guardrails. **No implementation begins until Phase 0 is
approved.** The current state of the repository is documentation only.

## Phases

**Phase 0 — Architecture validation. ✅ COMPLETE (2026-08-07).** Validated the
architecture in this documentation set. Outputs: `docs/adr/ADR-001` … `ADR-015`,
[`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md), and
[`18_FIRST_VERTICAL_SLICE.md`](18_FIRST_VERTICAL_SLICE.md). No product code, no framework
scaffolding. Phase 1 is gated on the review's *Readiness Verdict*: product-owner questions
Q1–Q4 answered, and the eleven recommended changes reflected in the docs.

> **Phase 0 resequencing.** Three ordering errors were found in the phase list below and
> are corrected as follows:
>
> 1. **PostgreSQL connector before file connectors.** Phase 3 originally put Excel/CSV
>    first as "closest to the prototype workbook." File sources are in fact the *hardest*
>    semantic case — no reliable types, no keys, no incrementality, schema drift on every
>    upload, header ambiguity — and therefore the worst first proof of the architecture.
>    They also contradicted the first vertical slice in this same document, which uses
>    PostgreSQL. **Phases 2 and 3 merge**: the connector framework ships together with the
>    PostgreSQL connector, so connection testing has something real to test. File
>    connectors move to a later phase.
> 2. **Lineage folds into the metric-engine phase.** Lineage is a *byproduct* of the
>    metric AST and the binding graph ([ADR-012](adr/ADR-012-data-lineage.md)), not a
>    feature layered on afterwards. Deferring it to Phase 7 guarantees a retrofit.
> 3. **The semantic phase must deliver bindings, not field mappings**, with automated
>    binding validation as a publish gate
>    ([ADR-005](adr/ADR-005-semantic-model.md)) — otherwise Phase 6's metrics are computed
>    over an unvalidated model.
>
> Phase 1 additionally acquires three items the original list did not carry, all of which
> are expensive to retrofit: the **`ConfigurationBundle`** mechanism
> ([ADR-013](adr/ADR-013-configuration-versioning.md)), the **pipeline state machine and
> outbox** ([ADR-009](adr/ADR-009-background-job-architecture.md)), and the **analytical
> schema provisioning subsystem** ([ADR-003](adr/ADR-003-multi-tenant-architecture.md)).

**Phase 1 — Platform foundation.** Establish the modular monolith skeleton, tenant
context, identity, configuration versioning, migrations, and observability — the
scaffolding every later phase depends on.

**Phase 2 — Data Source Manager.** The connector framework abstraction, data-source
CRUD, and connection testing with meaningful diagnostics.

**Phase 3 — Excel / CSV connectors.** The first concrete connectors, proving the
abstraction against file-based sources (closest to the prototype workbook).

**Phase 4 — Semantic Model.** Semantic entities, fields, dimensions, dimension values,
glossary, mappings, and transformations, with draft/published/archived versioning.

**Phase 5 — AI Mapping Assistant.** AI-suggested field mappings with confidence and
origin, gated by human approval before publish.

**Phase 6 — Metric Engine.** Governed metric definitions and computation through the
Governed Query Service, with dimensions, filters, targets, and thresholds.

**Phase 7 — Lineage.** End-to-end lineage from dashboard widget to source, exposed as a
product feature.

**Phase 8 — Executive Command Center.** The "what deserves my attention?" home
experience with trust-badge KPI cards.

**Phase 9 — Insight Engine.** Deterministic/statistical signal detection producing
facts, correlations, hypotheses, and recommended questions.

**Phase 10 — Ask Your Business.** The governed natural-language assistant pipeline over
the shared semantic/query layer.

**Phase 11 — Executive Brief + Alerts.** The AI executive brief, alert rules, and alert
delivery.

**Phase 12 — Enterprise Hardening.** SSO/SAML depth, advanced RBAC/policy, encryption
and classification hardening, scale-out of the analytical store abstraction, and
operational readiness.

## First vertical slice

The first real implementation (later, after Phase 0) should prove the whole spine
end-to-end with the smallest possible feature set:

```
Create Tenant
  → Add PostgreSQL Source
  → Test Connection
  → Discover Table
  → Map Revenue.Amount
  → Define revenue_ytd
  → Query Metric
  → Display KPI
  → Show Lineage
```

This slice touches identity/tenant, connectivity, discovery, the semantic layer,
mapping, the metric engine, the governed query service, the experience, and lineage —
proving that the architecture holds together before breadth is added.

> **Phase 0 update.** The slice is fully specified in
> [`18_FIRST_VERTICAL_SLICE.md`](18_FIRST_VERTICAL_SLICE.md) — entities, API contracts,
> modules, screens, jobs, security controls, isolation tests, error handling, logging,
> tests, and acceptance criteria. It gains four steps (**profile fields**, **drill into
> the KPI by a tenant-configured dimension**, **validate the binding**, **publish a
> configuration bundle**) and is framed as an **experiment designed to falsify** the four
> riskiest Phase 0 assumptions, not as a demo. Its decisive test is the Company A /
> Company B fixture pair: two structurally different source schemas producing the same
> correct `Revenue.Amount` with **zero code differences**.

## Sequencing rationale

The order moves deliberately from foundation → data in → meaning → governed numbers →
trust → attention → intelligence → conversation → alerting → hardening. Each phase leans
on the invariants established before it, so the semantic/metric/query spine is solid
before insights and chat are layered on top of it.

## Do-not-do-yet list (until Phase 1 is approved)

- Do **not** scaffold Next.js.
- Do **not** scaffold FastAPI.
- Do **not** add Docker.
- Do **not** implement the database.
- Do **not** implement connectors.

Phase 0 is complete; **Phase 1 is not yet approved**. The gate is the *Readiness Verdict*
in [`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md): product-owner
questions **Q1–Q4** answered (bring-your-own-warehouse, private-network connectivity,
SaaS-now vs. TriVera-first, restatement policy), and the eleven recommended changes
reflected in the documentation.
