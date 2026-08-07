# 11 — Agent Guardrails

Hard guardrails for **every** AI agent that works on this codebase — implementation
agents, review agents, and research agents. These are non-negotiable. If a task appears
to require breaking one, stop and raise it rather than proceeding. `CLAUDE.md` and
`AGENTS.md` are the short summaries; this is the detailed rationale.

## Product-shape guardrails

1. **The product is an Executive Intelligence Platform.** Do not turn it into a
   spreadsheet, dashboard builder, charting tool, chatbot, warehouse, or generic BI
   tool. Those are components or comparisons, not the product.
2. **The workbook is a prototype only.** Never recreate `TriVera Executive
   Dashboard.xlsx` as the application.
3. **No hard-coded workbook values.** KPI names and the `Total / People / Process /
   Technology / Enterprise` selector are configurable `Metric` and `Dimension` /
   `DimensionValue` metadata. Never literals in code, never `if selectedView == "People"`.

## Configuration and architecture guardrails

4. **Configuration over customization; metadata over tenant-specific code.** No
   per-tenant branches in business logic. New behavior is metadata.
5. **The semantic layer is mandatory.** Raw source fields never drive dashboards,
   metrics, or chat directly.
6. **Metrics are governed and reusable.** No ad-hoc metric math and no arbitrary
   client SQL. All numbers flow through the Governed Query Service.
7. **Modular monolith with strong bounded contexts.** No microservices without an
   accepted ADR. Do not reach across a context's internal data.

## Security and tenancy guardrails

8. **Tenant isolation everywhere.** Every query, cache key, storage path, and log line
   is tenant-scoped. Never write a code path that could serve one tenant's data to
   another.
9. **No secrets in source control, logs, prompts, Git, or ordinary metadata.** Use the
   secret-manager abstraction. Connector credentials are least-privilege.
10. **Authorize before data access.** On both dashboard and assistant surfaces,
    authorization precedes any data touch.

## AI/LLM guardrails

11. **No unrestricted LLM database access.** The LLM never sees raw production tables or
    emits SQL that reaches them. It explains governed, validated evidence only.
12. **AI is not the source of truth.** Numbers come from the governed metric engine;
    the model narrates them.
13. **Human approval for governed semantic changes.** AI may suggest mappings and metric
    definitions; a human approves before publish.
14. **Evidence first, narrative second.** Keep FACT, CORRELATION, and HYPOTHESIS clearly
    separated. Never let the model invent numbers, causes, targets, source systems,
    dates, or customers. If evidence is insufficient, say so.
15. **Provider-neutral integrations.** Depend on connector and LLM-gateway abstractions,
    never on a specific vendor SDK in business logic.

## Engineering-hygiene guardrails

16. **Explore → Plan → Implement → Verify.** Understand before changing; verify after.
17. **Use migrations for every schema change.** Never hand-edit the database.
18. **Run lint, typecheck, and tests before declaring done.** A task with a failing
    check is not complete.
19. **Read the relevant `/docs` before major changes**, and record significant
    decisions as ADRs (`docs/adr/`). Dated research (`docs/research/`) informs but never
    overrides an accepted ADR.
20. **Prefer the smallest correct change.** When product behavior is ambiguous, write
    down the open question instead of guessing.

## When a guardrail seems to block the task

Guardrails win. If the requested change genuinely cannot be done within them, the correct
move is to surface the conflict — in the PR, an ADR, or a direct question — not to quietly
relax a guardrail. These rules exist because trust is the product; a shortcut that
compromises isolation, governance, or explainability compromises the product itself.
