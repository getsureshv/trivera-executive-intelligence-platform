# 10 — Implementation Roadmap

The platform is built in phases. Each phase produces working, verified capability and
respects the core principles and guardrails. **No implementation begins until Phase 0 is
approved.** The current state of the repository is documentation only.

## Phases

**Phase 0 — Architecture validation.** Validate the architecture in this documentation
set. Confirm the stack, the modular-monolith boundaries, the tenant-isolation strategy,
the semantic/metric/query spine, and the provider-neutral connector and LLM seams.
Output: ADRs for the load-bearing decisions and a short validation report. No product
code, no framework scaffolding.

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

## Sequencing rationale

The order moves deliberately from foundation → data in → meaning → governed numbers →
trust → attention → intelligence → conversation → alerting → hardening. Each phase leans
on the invariants established before it, so the semantic/metric/query spine is solid
before insights and chat are layered on top of it.

## Do-not-do-yet list (until Phase 0 is approved)

- Do **not** scaffold Next.js.
- Do **not** scaffold FastAPI.
- Do **not** add Docker.
- Do **not** implement the database.
- Do **not** implement connectors.

The next instruction after documentation bootstrap is **Phase 0 architecture
validation** (see [`12_PROMPT_CLAUDE_CODE.md`](12_PROMPT_CLAUDE_CODE.md)).
