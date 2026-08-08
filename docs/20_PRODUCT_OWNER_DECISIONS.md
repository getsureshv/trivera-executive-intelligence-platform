# 20 — Product-Owner Decisions (PO-001 … PO-005)

Date: 2026-08-07
Status: **Accepted.** These close the questions that gated Phase 1B.

These are **product-owner decisions**, not architecture decisions. An ADR records
a technical choice and its trade-offs; a PO decision records what the business
has committed to, and the ADRs then serve it. Where a PO decision and an ADR
appear to disagree, the PO decision defines the requirement and the ADR must be
amended or superseded — it is never resolved by quietly reinterpreting either.

**Authority order** (extending
[`16_TOOL_STRATEGY.md`](16_TOOL_STRATEGY.md)):

```
product-owner decisions  >  accepted ADRs  >  this documentation set
                         >  artifacts  >  research
```

These decisions answer questions **Q1–Q4** raised in
[`17_PHASE_0_ARCHITECTURE_REVIEW.md`](17_PHASE_0_ARCHITECTURE_REVIEW.md)
§ *Questions Requiring Product Owner Decision*, plus the tenant data-plane
confirmation the Phase 1A work proceeded under as an assumption.

---

## PO-001 — Bring-your-own warehouse is excluded from V1

**Decision.** V1 does **not** support running against a customer's own
Snowflake, Databricks, or BigQuery. V1 materializes data into the platform's
controlled analytical plane.

**Answers:** Q1.

**What this settles.** The Phase 0 review flagged that treating those vendors as
interchangeable back-ends conflated two different products: Mode A
(platform-managed store) and Mode B (bring-your-own warehouse, no data
movement). Mode B carries a different security posture, a different pricing
model, and a different role for the connector — the warehouse becomes both
source and compute target. That is a go-to-market decision, and it is now made:
**Mode A only for V1.**

**Consequences.**

- [ADR-008](adr/ADR-008-analytical-storage.md) stands as written: PostgreSQL is
  the sole analytical engine, with ClickHouse pre-selected as its successor and
  quantitative exit triggers already defined.
- [ADR-007](adr/ADR-007-governed-query-engine.md) §7 (materialize, do not
  federate) is confirmed rather than provisional.
- The `AnalyticalEngine` port and its capability matrix are **retained**. They
  are not speculative work for Mode B; they are what makes the ClickHouse
  migration a bounded project. Mode B remains architecturally possible without
  being built.
- Engineering should **not** invest in pushdown execution, no-copy ingestion, or
  warehouse-vendor adapters during V1.

**Revisit when** a customer's data-residency or data-movement policy makes Mode A
unacceptable, or a deal is lost specifically on this. Either is a new PO
decision, not a design drift.

---

## PO-002 — Private connectivity starts with outbound connections and IP allowlisting

**Decision.** V1 reaches customer systems by **outbound connection from the
platform**, with the customer allowlisting the platform's egress IP addresses.
An extension point must be preserved for a future **customer-network agent**,
**PrivateLink**, or **VPN**.

**Answers:** Q2.

**What this settles.** The Phase 0 review identified private-network
connectivity as the most likely reason an enterprise pilot stalls, and offered
three modes. V1 takes the simplest, and — importantly — commits to *not
foreclosing* the others.

**Consequences.**

- [ADR-004](adr/ADR-004-connector-framework.md)'s three connectivity modes
  remain declared. Mode 1 (direct, allowlisted) is built for V1; modes 2 and 3
  are not.
- Two properties of the connector contract now have a named product reason and
  must not be simplified away:
  - **Egress must be stable and documented.** Customers allowlist specific
    addresses, so the platform's egress identity is a published interface, not
    an implementation detail. It cannot change without customer coordination.
  - **Connector work must remain serializable and remotely executable.**
    ADR-004's `ExtractPlan` / `RecordBatch` streaming contract exists precisely
    so a connector runtime can later execute inside a customer's network. That
    is the agent extension point. Collapsing it into an in-process object model
    would close the door.
- The egress deny-list (RFC1918, loopback, link-local, cloud metadata endpoints)
  is unaffected and remains mandatory: outbound-by-default makes SSRF a live
  concern, not a theoretical one.

**Revisit when** a customer requires that no inbound connection reach their
network at all. The agent is then the answer, and the contract already supports
it.

---

## PO-003 — Multi-tenant SaaS is required from day one

**Decision.** The platform is **multi-tenant SaaS from day one**. TriVera is the
**first tenant and configuration pack** — not a special application, not a
privileged tenant, and not a code path.

**Answers:** Q3.

**What this settles.** The Phase 0 review recommended building irreversible
isolation immediately while deferring provisioning automation, on the reasoning
that there was only one customer. This decision goes further: TriVera is
explicitly *not* a special case, so no "first customer" shortcut is legitimate.

**Consequences.**

- Guardrail 4 (configuration over customization) and principle 1 apply to
  TriVera exactly as to any other tenant. A `TriVera` branch in business logic
  is a defect regardless of expedience.
- The workbook's KPIs, domains, and the `operating_model` dimension are
  **TriVera's configuration pack**, not product concepts — consistent with
  [`02_WORKBOOK_FINDINGS.md`](02_WORKBOOK_FINDINGS.md) and its Phase 0
  correction.
- Phase 1A's tenant isolation was built to this standard and is verified for it:
  two tenants, four attack vectors, database-enforced on both planes.
- **This raises the priority of tenant provisioning automation.** Phase 1A
  deliberately left provisioning a manual, audited, platform-staff operation on
  the assumption that a second tenant was distant. Under PO-003 that assumption
  no longer holds, and self-serve or operator-driven provisioning becomes Phase
  1B/2 scope rather than "later".
- Per-tenant cost attribution (ADR-014 §7) and per-tenant fairness caps
  (ADR-009 §5) are requirements, not refinements.

**Revisit** — not applicable. This is a foundational commitment; reversing it
would invalidate ADR-003 and most of Phase 1A.

---

## PO-004 — Restatements never silently overwrite executive history

**Decision.** When a source restates a period, the platform **preserves both the
original and the restated observation**. Every observation carries provenance:
the **calculation**, the **configuration**, the **source**, the **reason**, the
**approval**, and the **timestamp**.

**Answers:** Q4.

**What this settles.** The Phase 0 review named this as the finance-facing
correctness question and offered "freeze after close" or "visible restatement".
This decision selects visible restatement and, notably, requires **approval** —
a restatement is a governed act, not a data event.

**Consequences.**

- [ADR-012](adr/ADR-012-data-lineage.md) §3 (restatement handling) is confirmed
  and **extended**: it already required append-only observations, a recorded
  `Restatement` event, and suppression of insight-engine signals caused purely
  by restatement. PO-004 adds **reason and approval** to the required
  provenance, which ADR-012 did not mandate.
- The provenance envelope of [ADR-007](adr/ADR-007-governed-query-engine.md) §3
  supplies calculation (`metric_version`, `plan_hash`), configuration
  (`config_version`), and source (`data_snapshot_id`, `source_watermarks`).
  Reason and approval are **new fields** and must be added when the
  `MetricObservation` store is built.
- `MetricObservation` (ADR-008 §8) is confirmed as **append-only**. An
  `UPDATE` path to it would violate this decision directly.
- The insight engine must never report a restatement as a business change
  (ADR-012 §3.4). Under PO-004 that is a product requirement, not an
  implementation nicety.
- **An ADR amendment is owed** when the metric layer is built, to add
  reason/approval to the observation provenance. Recorded here so it is not
  discovered late.

**Note on scope.** Phase 1A has no metrics and no observations, so nothing here
is yet implementable. It binds Phase 6 (Metric Engine) onward.

---

## PO-005 — Confirm the ADR-003 hybrid isolation model

**Decision.** The [ADR-003](adr/ADR-003-multi-tenant-architecture.md) hybrid
model is **confirmed**:

- **Control plane** — pooled, shared schema, `tenant_id` with forced Row-Level
  Security.
- **Analytical plane** — schema-per-tenant, with **separate tenant
  credentials**.

**Confirms** the assumption Phase 1A proceeded under, and the G10 remediation
that followed.

**Consequences.**

- ADR-003 needs no amendment. The implementation matches it, including the
  requirement in §2 that the analytical connection role hold `USAGE` on *only*
  the current tenant's schema.
- "Separate tenant credentials" is explicit here because it is the distinction
  that Phase 1A got wrong twice before getting right. The current model —
  verified by test — is:
  - `eip_app` holds **no privilege** on any tenant schema and is a **member of
    no tenant role**; the process refuses to start otherwise;
  - each tenant has its **own login role and own password**, held in the
    `SecretStore`, with one connection pool per tenant;
  - `SET ROLE` appears **nowhere** in the codebase, asserted by an architecture
    test.
- ADR-003's Tier 2 (dedicated analytical instance) and Tier 3 (siloed
  deployment) remain available as configuration, unbuilt.

**Revisit when** a customer requires a dedicated instance or region. That is a
tier change, which ADR-003 already accommodates without redesign.

---

## Traceability

| Decision | Phase 0 question | Governing ADRs | Implemented in |
| --- | --- | --- | --- |
| PO-001 | Q1 | ADR-007, ADR-008 | not yet — binds Phase 2+ |
| PO-002 | Q2 | ADR-004 | not yet — binds Phase 2 |
| PO-003 | Q3 | ADR-003, ADR-009, ADR-014 | Phase 1A isolation; provisioning outstanding |
| PO-004 | Q4 | ADR-007, ADR-008, ADR-012 | not yet — binds Phase 6+ |
| PO-005 | — | ADR-003 | **Phase 1A, complete and verified** |

Questions **Q5–Q12** from the Phase 0 review remain open. None gates Phase 1B;
each is scoped to the phase that first depends on it.
