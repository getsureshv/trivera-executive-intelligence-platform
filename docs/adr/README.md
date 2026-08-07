# Architecture Decision Records (ADRs)

This folder holds the **Architecture Decision Records** for the TriVera Executive
Intelligence Platform. An ADR captures a single significant technical decision — the
context that forced it, the decision itself, the alternatives considered, and the
consequences.

ADRs are the **durable, authoritative** record of decisions. Per
[`../16_TOOL_STRATEGY.md`](../16_TOOL_STRATEGY.md), accepted ADRs outrank artifacts and
research. Changing an accepted decision means writing a **new** ADR (superseding the
old one), never quietly editing the old decision or overriding it in a research note.

## Accepted ADRs (Phase 0, 2026-08-07)

| ADR | Decision | Headline |
| --- | --- | --- |
| [001](ADR-001-repository-architecture.md) | Repository Architecture | One polyglot monorepo; bounded contexts enforced by `import-linter` in CI; the Node tier never holds database credentials |
| [002](ADR-002-backend-framework.md) | Backend Framework | FastAPI + Python confirmed, with `mypy --strict` mandatory |
| [003](ADR-003-multi-tenant-architecture.md) | Multi-Tenant Architecture | Hybrid: pooled control plane (`tenant_id` + forced RLS), siloed data plane (schema-per-tenant) |
| [004](ADR-004-connector-framework.md) | Connector Framework | Capability-declaring, async, streaming, resumable; egress control; customer connectivity model |
| [005](ADR-005-semantic-model.md) | Semantic Model | **Entity bindings, not field mappings**; declared grain, row filter, units, time anchors; cardinality-aware joins |
| [006](ADR-006-metric-definition-and-kpi-engine.md) | Metric & KPI Engine | Metrics are typed ASTs, not strings; immutable published versions; acceptance assertions |
| [007](ADR-007-governed-query-engine.md) | Governed Query Engine | Fixed pipeline; authorize before compile; mandatory result envelope; scope-aware cache keys |
| [008](ADR-008-analytical-storage.md) | Analytical Storage | PostgreSQL only, ClickHouse pre-selected, named exit triggers; observation store; raw landing zone |
| [009](ADR-009-background-job-architecture.md) | Background Jobs | Pipeline state in PostgreSQL (it is product data) + Dramatiq + outbox; Temporal deferred |
| [010](ADR-010-authentication-and-authorization.md) | AuthN / AuthZ | Delegated OIDC; four composed layers; metrics inherit field classification |
| [011](ADR-011-ai-provider-and-orchestration.md) | AI Provider & Orchestration | Constrained plan generation replaces intent enums; prompt trust zones; numeric grounding checks |
| [012](ADR-012-data-lineage.md) | Data Lineage | Lineage is **derived**, never stored; run-time provenance and restatement are distinct |
| [013](ADR-013-configuration-versioning.md) | Configuration Versioning | `ConfigurationBundle` as the atomic, immutable unit of publish, rollback, provenance, and templating |
| [014](ADR-014-observability.md) | Observability | OpenTelemetry; data health is product health; audit is a durable store, not a log stream |
| [015](ADR-015-secrets-management.md) | Secrets Management | References only — a metadata dump contains zero customer credentials |

Context and the reasoning behind these sit in
[`../17_PHASE_0_ARCHITECTURE_REVIEW.md`](../17_PHASE_0_ARCHITECTURE_REVIEW.md).

## Naming

ADRs are numbered sequentially and named descriptively:

```
docs/adr/ADR-001-short-title.md
docs/adr/ADR-002-short-title.md
```

## Status values

- **Proposed** — under discussion.
- **Accepted** — the current decision; governs the build.
- **Superseded** — replaced by a later ADR (reference it).
- **Deprecated** — no longer applies.

## Template

```markdown
# ADR-NNN: Title

Status: Proposed | Accepted | Superseded | Deprecated
Date: YYYY-MM-DD
Phase: N

## Context
What is the situation, constraint, or problem forcing a decision?

## Decision
What did we decide to do?

## Alternatives Considered
What other options were considered, and why was each not chosen?

## Rationale
Why this decision follows from the context and beats the alternatives.

## Consequences
What follows from this decision — positive and negative?

## Risks
What could go wrong, how will we detect it, and how will we mitigate it?
(A table of Risk / Detection / Mitigation works well.)

## Future Considerations
What this decision leaves open, and what would trigger revisiting it.
```

## Writing a good ADR

Keep each ADR to one decision. Write the context so a future reader who was not in the
room understands why the decision was necessary. Be honest in Alternatives and Risks —
an ADR that lists no real alternatives and no real risks is not doing its job. Link
related ADRs and the relevant `/docs` sections.
