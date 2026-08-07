# ADR-013: Configuration Versioning and Release Bundles

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`07_SECURITY_MULTITENANCY_GOVERNANCE.md` specifies a `draft → published → archived`
lifecycle **per object** (semantic entities, mappings, transformations, metrics, targets,
thresholds, dashboards), each version carrying change reason, author, approver, published
date, and rollback metadata.

Phase 0 review found this insufficient in a specific and consequential way.

**Per-object versioning cannot express a coherent system state.** A KPI on the executive
home page depends on a metric version, which depends on semantic field definitions, which
depend on entity bindings, which depend on transformations, and it is compared against a
target version and resolved through a fiscal calendar. If each of those versions
independently, then:

- "roll back the mapping" can leave a metric referencing a semantic field that the
  reverted binding no longer supplies — a broken or, worse, silently different number;
- there is no single identifier that answers "what configuration produced this number?",
  which ADR-007's provenance envelope requires and ADR-012's change-explanation depends
  on;
- a steward mid-edit across five related objects has no way to publish them atomically, so
  there is a window where the tenant's configuration is internally inconsistent;
- promotion between environments and tenant templating (industry KPI packs) have no unit
  to promote.

There is a second gap: configuration is the primary artifact of this product — it is what
replaces code — yet the documentation gives it none of the practices code gets. No diff,
no review, no test, no export, no promotion.

## Decision

**Keep per-object versioning. Add an atomic, immutable, tenant-scoped
`ConfigurationBundle` as the unit of publication, rollback, provenance, promotion, and
templating.**

### 1. The bundle

```
ConfigurationBundle
  tenant_id
  config_version      : monotonically increasing integer, per tenant
  content_hash        : hash of the full resolved manifest
  manifest            : the exact version id of every governed object in scope
  status              : draft | published | archived
  parent_version      : the bundle this was derived from
  change_summary      : structured diff vs. parent
  change_reason, author, approver(s), published_at
  validation_report   : binding validation, metric validation, assertion results
```

- Publishing is **atomic**: either the whole bundle becomes active or nothing does.
- Published bundles are **immutable**. Rollback is "activate bundle N−1", not "undo an
  edit" — an instant, side-effect-free pointer move with no reverse migration to get
  wrong.
- Every query result, every `MetricObservation`, every insight, and every assistant answer
  records the `config_version` it was computed under (ADR-007, ADR-008, ADR-012). This
  single field is what makes "why did the number change?" answerable.

Per-object versions continue to exist and remain the unit of *editing* and *ownership*.
The bundle is the unit of *release*. The relationship is exactly that of a commit to a
file revision.

### 2. Draft workspaces

A steward works in a **draft bundle** — a copy-on-write overlay over the published one.
Multiple related edits accumulate there and are validated together. The draft can be
previewed against real data ("what would revenue_ytd be under this bundle?") **without
affecting anyone else**, which is the capability that makes onboarding iteration safe.

Concurrency: drafts are per-workspace, not global. Two stewards editing different domains
do not block each other; conflicting edits to the same object are detected at merge via
object version (optimistic concurrency, `If-Match`/ETag at the API).

### 3. Validation gates publication

A bundle cannot be published unless:

- every entity binding validates against its semantic contract (ADR-005);
- every metric AST is well-formed, its dimensions are reachable, and its aggregation
  respects additivity (ADR-006);
- metric acceptance assertions pass (ADR-006);
- impact analysis has been produced, and acknowledged where it touches restricted or
  asserted objects (ADR-012);
- required approvals are present, honouring separation of duties (ADR-010).

This is the mechanism that makes "onboard by configuration" *safe*. Configuration without
a test gate is just untested code with a friendlier editor.

### 4. Bundles are exportable, diffable, signed artifacts

A bundle serializes to a canonical, human-readable document (YAML/JSON, stable ordering).
This yields, essentially for free:

- **diff and review** — a governance UI showing exactly what changed, and the ability to
  put configuration through a code-review-like flow;
- **environment promotion** — dev/staging/production tenants for large customers;
- **templating** — an **industry KPI pack** is simply a bundle fragment with unbound
  entities; onboarding a new tenant becomes "instantiate a pack, then bind it to sources."
  This is the concrete mechanism by which the product's central claim is delivered;
- **disaster recovery and portability** — a tenant's entire configuration is one artifact.

Bundles are signed and hash-verified so an imported pack cannot be tampered with in
transit.

**Secrets are never in a bundle** — only secret *references* (ADR-015). Export must be
safe to hand to a customer.

### 5. Effective-dating vs. versioning are kept distinct

Two different notions that are easy to conflate and expensive to confuse:

- **Version** — when the *definition* changed (system time).
- **Effective date** — the business period a definition applies to (valid time), e.g. "the
  new revenue recognition rule applies from FY2026 Q1 onward."

Both are modelled. A query resolves the definition effective for its period, under the
bundle active at computation time. This is what allows a correct year-over-year comparison
after a definition change, and it is a requirement, not a refinement, for a finance-facing
product.

### 6. Scope

The bundle covers: semantic entities/fields/relationships, bindings, transformations,
dimensions and hierarchies, metrics/targets/thresholds/assertions, glossary, dashboards
and widgets, alert rules, insight configuration, fiscal calendar, prompt versions
(ADR-011), and role/policy definitions (ADR-010).

It does **not** cover: data source connection details and secrets (operational, not
semantic), ingestion schedules, user accounts and memberships, or ingested data. These
have their own lifecycles and would make bundles non-portable.

## Alternatives Considered

- **Per-object versioning only (as documented).** Rejected — cannot express a coherent
  state, cannot support atomic rollback, cannot supply a provenance identifier.
- **Git as the configuration store.** Genuinely attractive: diff, review, branch, and
  history for free, and it is the model dbt/LookML use. Rejected as the *system of record*
  because tenant configuration is edited through a UI by non-engineers, must be
  transactionally consistent with application state, must be queried relationally at
  runtime (the compiler resolves definitions per request), and must be tenant-isolated. We
  take Git's *concepts* — immutable snapshots, atomic commits, diff, revert — and
  implement them over PostgreSQL. Git-backed export remains available for customers who
  want it.
- **Event sourcing the configuration.** Rejected as primary: powerful for audit but makes
  "read the current definition" — the hot path in every query compilation — awkward. The
  audit trail (ADR-014) already provides the event log.
- **Copy-on-write full snapshots per publish (denormalized).** Rejected in favour of a
  manifest of version ids: same guarantees, far less storage, and object identity is
  preserved for lineage.
- **No draft workspaces (edit live).** Rejected — publishing half-finished semantics to a
  live executive dashboard is unacceptable in a trust product.

## Rationale

Configuration is this platform's source code. The industry learned, expensively, that
source code needs atomic commits, immutable history, diffs, review, tests, and instant
revert. Per-object versioning provides none of those at the system level. A bundle
provides all of them, and it simultaneously supplies the `config_version` identifier that
three other ADRs (007, 008, 012) already depend on.

The templating consequence deserves emphasis: once configuration is a portable artifact,
"onboard a company primarily through configuration" acquires an actual delivery mechanism
— a library of industry packs — rather than remaining an aspiration about the data model.

## Consequences

- Positive: atomic publish and instant, safe rollback.
- Positive: one identifier explains any number's configuration context.
- Positive: safe iteration through draft workspaces with preview.
- Positive: industry KPI packs and environment promotion fall out of the design.
- Positive: validation at publish converts configuration into tested configuration.
- Negative: significant added complexity in the metadata layer; every governed read must
  resolve through a bundle, which affects nearly every query path.
- Negative: draft/merge semantics require careful concurrency handling.
- Negative: bundle resolution is on the hot path and must be aggressively cached, keyed by
  `(tenant, config_version)` — safe, since published bundles are immutable.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Bundle resolution becomes a latency bottleneck | Compile-path latency telemetry | Immutable bundles are trivially cacheable; warm on publish |
| Long-lived drafts diverge and merge painfully | Draft age telemetry | Encourage small bundles; conflict detection at object-version level |
| Rollback restores config but data was reprocessed under the new config | Observation provenance mismatch alarm | Observations are append-only and carry `config_version`; rollback triggers targeted recomputation |
| Validation gate is bypassed under delivery pressure | Publish audit shows validation status | Publishing without a passing validation report requires an explicit, audited override |
| Exported bundle leaks sensitive structure | Export authorization + review | Secrets excluded by construction; export is an audited, permissioned action |
| Effective-dating and versioning get conflated in implementation | Year-over-year comparison tests across a definition change | Separate fields, separate resolution steps, explicit tests |

## Future Considerations

- A configuration review workflow mirroring pull requests, with inline comments.
- An industry pack library and a pack authoring/publishing tool.
- Automated migration of tenant configuration when a platform semantic pack version
  advances.
- Scheduled publication ("this definition change takes effect at the start of FY2027").
- Bundle-level time travel: render the dashboard exactly as it was under bundle N.
