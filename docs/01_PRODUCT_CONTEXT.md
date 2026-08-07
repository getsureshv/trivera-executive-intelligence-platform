# 01 — Product Context

## What we are building

**TriVera Executive Intelligence Platform** is a multi-tenant **Executive Intelligence
Platform**. It lets any organization connect its operational systems and obtain a
trusted, explainable, continuously updated view of business performance.

The product is deliberately **not** primarily any of the following:

- a spreadsheet
- a dashboard builder
- a charting tool
- a chatbot
- a data warehouse
- a generic BI tool

Each of those is either a component we may use internally or a category we are
frequently compared to. None of them is the product. The product is the governed
**intelligence layer** that sits above data and turns it into leadership-grade
decisions.

## The operating model

Everything in the platform serves one directional flow:

```
DATA → BUSINESS MEANING → GOVERNED METRICS → INSIGHTS → DECISION SUPPORT → ACTION
```

- **Data** — operational systems: databases, SaaS APIs, files.
- **Business meaning** — the semantic layer that translates raw source fields into
  business concepts an executive would recognize.
- **Governed metrics** — versioned, owned, reusable definitions of the numbers the
  business runs on.
- **Insights** — deterministic and statistical signals that flag what changed, what is
  off plan, and what is anomalous.
- **Decision support** — insights framed as facts, correlations, hypotheses, and
  questions worth asking, with evidence attached.
- **Action** — the decisions and follow-ups leadership takes as a result.

Each arrow is a governed transformation, not a leap of faith. The value of the platform
is that every step is explicit, inspectable, and trustworthy.

## The core executive questions

The platform exists to answer eight questions that recur in every leadership team,
in every industry:

1. **How is the business performing?**
2. **What changed?**
3. **What is off plan?**
4. **Why might it have changed?**
5. **Where is risk increasing?**
6. **Where is opportunity emerging?**
7. **What deserves leadership attention?**
8. **What evidence supports that conclusion?**

Feature ideas are evaluated against these questions. If a feature does not help answer
one of them better, faster, or more trustworthily, it is probably out of scope.

## Who it is for

The primary user is a **time-poor executive** (CEO, CFO, COO, and their direct
leadership team) who needs to know where to look and why — not to build charts. The
secondary users are the **analysts and data stewards** who configure connectors, curate
the semantic model, and govern metrics on the executive's behalf. The platform must
serve both: a governance surface for the stewards and a distilled attention surface for
the executive.

## Multi-tenancy and configurability

The platform is multi-tenant from day one and must be configurable for **any** company.
No customer is special-cased in code. A new organization is brought live by
**configuration** — connectors, a semantic model, governed metrics, targets, and
dashboards — not by branching business logic. This is what lets a single codebase serve
a professional-services firm, a manufacturer, and a SaaS company without forks.

## Core product principles

These thirteen principles are binding and are referenced throughout the rest of the
documentation. They are the tie-breakers when a design decision is unclear.

1. **Configuration over customization.** Behavior differences between tenants are
   expressed as configuration, not code.
2. **Metadata over tenant-specific code.** The system is driven by metadata
   (entities, fields, dimensions, metrics), not per-tenant conditionals.
3. **Governed metrics over arbitrary SQL.** The unit of truth is a governed metric
   definition, not a query someone typed once.
4. **Source-system independence.** The platform does not care whether revenue comes
   from SQL Server, a REST API, or a spreadsheet; connectors normalize that away.
5. **Explainability and lineage for important numbers.** Any number that matters can
   be traced from the widget back to the source field.
6. **Human approval for governed semantic changes.** AI may suggest mappings and
   definitions; a human approves before anything is published.
7. **Multi-tenant from day one.** Isolation is designed in, never retrofitted.
8. **API-first design.** Every capability is available through a versioned API; the UI
   is a client of that API.
9. **Enterprise security by design.** Security is a first-class requirement, not a
   later hardening pass.
10. **Dashboards and AI share one semantic/query layer.** There is exactly one path to
    a number, whether a chart or the assistant asks for it.
11. **AI explains governed evidence; AI is not the source of truth.** The LLM narrates
    validated numbers; it never originates them.
12. **Facts, correlations, and hypotheses must be clearly separated.** The platform
    never lets a guess masquerade as a fact.
13. **Executives should see what deserves attention, not just charts.** The home
    experience is an attention surface, not a wall of visualizations.

## Relationship to the prototype workbook

The product was prototyped as an Excel workbook, `TriVera Executive Dashboard.xlsx`. It
is a **functional prototype only**: it demonstrates the intended KPIs, business areas,
and executive framing. The production platform does **not** recreate the workbook. Its
cell references become source fields, its KPIs become governed metrics, and its
`Total / People / Process / Technology / Enterprise` selector becomes a configurable
dimension. Details in [`02_WORKBOOK_FINDINGS.md`](02_WORKBOOK_FINDINGS.md).
