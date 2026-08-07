# Architecture Decision Records (ADRs)

This folder holds the **Architecture Decision Records** for the TriVera Executive
Intelligence Platform. An ADR captures a single significant technical decision — the
context that forced it, the decision itself, the alternatives considered, and the
consequences.

ADRs are the **durable, authoritative** record of decisions. Per
[`../16_TOOL_STRATEGY.md`](../16_TOOL_STRATEGY.md), accepted ADRs outrank artifacts and
research. Changing an accepted decision means writing a **new** ADR (superseding the
old one), never quietly editing the old decision or overriding it in a research note.

> **No architecture decisions have been created yet.** Do not add ADRs unless requested.
> The first ADRs are expected as an output of **Phase 0 — Architecture validation**
> (see [`../10_IMPLEMENTATION_ROADMAP.md`](../10_IMPLEMENTATION_ROADMAP.md)).

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

## Context
What is the situation, constraint, or problem forcing a decision?

## Decision
What did we decide to do?

## Alternatives
What other options were considered, and why were they not chosen?

## Consequences
What follows from this decision — positive and negative?

## Risks
What could go wrong, and how will we detect or mitigate it?

## Follow-Up
What actions, ADRs, or reviews does this decision trigger?
```

## Writing a good ADR

Keep each ADR to one decision. Write the context so a future reader who was not in the
room understands why the decision was necessary. Be honest in Alternatives and Risks —
an ADR that lists no real alternatives and no real risks is not doing its job. Link
related ADRs and the relevant `/docs` sections.
