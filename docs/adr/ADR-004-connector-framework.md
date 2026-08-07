# ADR-004: Connector Framework

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

`04_DATA_CONNECTORS_SEMANTIC_LAYER.md` proposes a `Connector` protocol with eight
synchronous methods: `test_connection`, `list_namespaces`, `list_objects`,
`describe_object`, `sample`, `profile`, `extract`, `health`. The shape is right; the
contract as written will not survive first contact with real sources. Phase 0 review
found five concrete defects:

1. **No capability declaration.** A PostgreSQL connector supports incremental extraction,
   server-side predicate pushdown, exact distinct counts, and transactional snapshots. A
   CSV upload supports none of those. Callers cannot branch on connector *type* (that
   would be vendor coupling, violating principle 4), so capabilities must be *data*.
2. **`extract()` returns a result.** Real extractions are gigabytes and hours. The method
   must stream batches and be resumable from a watermark, or the ingestion tier cannot be
   built.
3. **Everything is synchronous.** Discovery against a large SQL Server catalog and
   extraction over a rate-limited REST API are both long-running and cancellable.
4. **No network egress model.** A tenant admin configuring a REST connector can point it
   at `http://169.254.169.254/`, at `http://localhost:5432`, or at another customer's
   internal service. This is server-side request forgery with the platform as the
   confused deputy, and the current documentation does not mention it at all.
5. **No connectivity model for private customer networks.** "Add a PostgreSQL source"
   presumes we can reach the customer's database. For most enterprises we cannot. This is
   an onboarding blocker, not a detail.

## Decision

**Keep the connector abstraction and its provider-neutral intent. Revise the contract as
follows, and add an explicit connectivity and egress model.**

### Revised contract (shape, not final code)

```python
class Connector(Protocol):
    # --- static, cheap, no I/O ---
    def capabilities(self) -> ConnectorCapabilities: ...
    def config_schema(self) -> JsonSchema: ...          # drives the UI form, generically

    # --- diagnostics ---
    async def test_connection(self) -> ConnectionTestResult: ...
    async def health(self) -> HealthStatus: ...

    # --- discovery (async, paginated, cancellable) ---
    async def list_namespaces(self) -> AsyncIterator[Namespace]: ...
    async def list_objects(self, ns: NamespaceRef) -> AsyncIterator[SourceObject]: ...
    async def describe_object(self, ref: ObjectRef) -> ObjectSchema: ...

    # --- inspection ---
    async def sample(self, ref: ObjectRef, limit: int) -> SampleRows: ...
    async def profile(self, ref: ObjectRef, spec: ProfileSpec) -> ProfileStats: ...

    # --- extraction (streaming, resumable) ---
    async def extract(
        self, ref: ObjectRef, plan: ExtractPlan
    ) -> AsyncIterator[RecordBatch]: ...
```

`ExtractPlan` carries mode (`full` | `incremental` | `cdc`), the cursor/watermark, a
column projection, an optional pushdown predicate, and a batch size. `RecordBatch`
carries rows plus a **resumable cursor**, so a failed extraction restarts from the last
committed batch rather than from zero.

`ConnectorCapabilities` is declarative data, e.g.:

```
supports_incremental, supports_cdc, supports_predicate_pushdown,
supports_column_projection, supports_server_side_aggregation,
supports_exact_distinct_count, supports_transactional_snapshot,
supports_schema_discovery, supports_statistics,
type_system, max_parallel_streams, rate_limit_profile
```

Callers (ingestion planner, profiler, future pushdown query executor) branch on
**capabilities**, never on connector identity. This is what makes principle 4 mechanically
true rather than aspirational.

### Type normalization

Every connector maps its native types into a **platform canonical type system**
(`string`, `integer`, `decimal(p,s)`, `float`, `boolean`, `date`, `timestamp`,
`timestamptz`, `json`, `binary`, `unknown`) and reports the original native type
alongside. Decimals are never silently converted to floats — money precision is
non-negotiable in this product. Unmappable types surface as `unknown` and are visible to
the steward rather than being coerced.

### `test_connection()` returns structured diagnostics

Per `04`, but formalized: an ordered list of checks — `network`, `tls`, `authentication`,
`authorization`, `metadata_access`, `latency` — each with `pass | fail | skipped`, a
machine-readable code, a human remediation hint, and a duration. Skipped checks after a
failure are reported as skipped, not as failures. Secrets never appear in diagnostics.

### Raw extract landing (new requirement)

Every extraction lands its **raw batches in tenant-prefixed object storage** (compressed
columnar) before transformation. Rationale: when a mapping or transformation changes, we
must be able to reprocess history **without re-reading the source** — the source may be
rate-limited, may have purged history, or may be a one-time file upload that no longer
exists. This is also the audit artifact for "what did the source actually say on that
date." Retention is configurable per tenant/source.

### Egress control (new requirement)

- All connector network traffic goes through a **controlled egress path** (an egress proxy
  or an explicit allowlist enforced in the connector runtime).
- **Denied by default:** RFC1918 ranges, loopback, link-local (`169.254.0.0/16`,
  including cloud metadata endpoints), IPv6 unique-local, and any address resolving into
  those ranges. DNS rebinding is countered by resolving once and connecting to the
  resolved address, or by proxy-side enforcement.
- Redirects are not followed across hosts by the REST connector without re-validation.
- Per-tenant egress allowlists are configuration, recorded and audited.

### Customer connectivity model (new requirement)

Three supported modes, decided per data source, expressed as configuration:

1. **Direct** — the source is internet-reachable; the customer allowlists our static
   egress addresses.
2. **Private link / VPC peering** — for cloud-hosted customer sources.
3. **Tenant-deployed agent** — a lightweight, outbound-only connector runtime the
   customer runs inside their network, which polls the platform for work and streams
   results out. This is the mode most enterprises will actually accept.

Mode 3 is **designed for now, built later**. The requirement it places on Phase 0 is that
the connector runtime must be able to execute somewhere other than our worker process —
i.e. the connector interface must be serializable-work-driven, not in-process-object
driven. The `ExtractPlan`/`RecordBatch` streaming contract above satisfies that.

### Credentials

Credentials never live on the `DataSource` record. The record holds a **secret reference**
(ADR-015). Least privilege, read-only, is a documented requirement in the connector's
setup instructions and is verified where the source exposes it (the `authorization` check
should report if the principal has write privileges, as a warning).

### Connector ordering (roadmap change)

`10_IMPLEMENTATION_ROADMAP.md` sequences Excel/CSV connectors first (Phase 3) on the
grounds that they are "closest to the prototype workbook." Phase 0 recommends
**reversing this: PostgreSQL first, files second.** File sources are the *hardest*
semantic case — no reliable types, no keys, no incrementality, schema drift on every
upload, and header ambiguity — and therefore the worst first proof of the architecture.
A typed, introspectable relational source exercises discovery, profiling, mapping,
incrementality, and lineage cleanly. This also aligns the roadmap with the first vertical
slice defined in the same document.

## Alternatives Considered

- **Adopt Singer / Airbyte / Meltano connector ecosystems.** Seriously considered — it
  would give immediate breadth. Rejected as the *core* abstraction because those protocols
  are extraction-centric: they do not model discovery-for-mapping, profiling for
  data-quality signals, capability negotiation, or the per-field metadata the semantic
  layer needs, and their operational model (subprocess per tap, JSON-lines) is a poor fit
  for our lineage and tenancy requirements. **Not rejected as a source of connectors** —
  a future `SingerBridgeConnector` implementing our protocol on top of a Singer tap is a
  cheap way to buy long-tail breadth, and the capability model makes it a first-class
  citizen with honestly-declared (limited) capabilities.
- **Embed a federation engine (Trino/Presto) and treat every source as a catalog.**
  Rejected for now; see ADR-008. It would solve extraction elegantly but forces the
  federated-query product mode and drags in heavy infrastructure before there is load.
- **Per-source bespoke services.** Rejected — precisely the vendor coupling principle 4
  forbids.
- **Keep the synchronous contract and add streaming later.** Rejected. The contract is the
  cheapest thing to fix now and the most expensive to fix after five connectors exist.

## Rationale

The two changes that matter most are **capabilities-as-data** and **streaming resumable
extraction**. The first is what allows one ingestion planner and one query planner to
serve wildly heterogeneous sources without a single `if isinstance(connector, ...)`. The
second is what allows the platform to ingest anything larger than a demo.

The egress and connectivity additions are not gold-plating: SSRF via a user-configured
HTTP connector is a well-known, frequently exploited class of vulnerability in exactly
this kind of product, and inability to reach a customer's private database is the most
common reason enterprise data-platform pilots stall.

## Consequences

- Positive: adding a connector genuinely requires no downstream change (principle 4).
- Positive: the ingestion planner can make correct decisions (incremental vs. full,
  pushdown vs. local filter) from declared capabilities.
- Positive: raw landing enables reprocessing after semantic changes — which will happen
  constantly during onboarding.
- Positive: the agent mode remains open without redesign.
- Negative: the connector contract is now substantially more work to implement per
  connector. Mitigated by base classes (`SqlConnectorBase` covering the JDBC-like family)
  and a **connector conformance test suite** every connector must pass.
- Negative: object storage becomes a required dependency from Phase 2/3, not "later."
- Negative: an egress proxy is additional infrastructure.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| SSRF through a tenant-configured REST/JDBC URL | Security test suite with metadata-endpoint and RFC1918 targets in CI | Deny-by-default egress; resolve-then-connect; no cross-host redirects |
| Credential leakage into diagnostics, logs, or error messages | Automated secret-pattern scan over log output in tests | Diagnostics are structured with an explicit allowlist of fields; connector config redaction at the type level |
| Decimal→float coercion corrupting money | Golden tests on a decimal-heavy fixture | Canonical type system forbids implicit narrowing; `unknown` over guessing |
| Incremental cursor loses or duplicates rows | Reconciliation job compares source and landed row counts/checksums | Watermarks committed atomically with batches; idempotent load keyed on natural key + batch |
| Schema drift breaks published mappings silently | Discovery diffing on every run; drift raises a data-quality signal | Mappings pin the source field identity; drift blocks publish and alerts the steward |
| Long-tail connector demand outruns capacity | Backlog | Singer bridge; connector conformance suite makes third-party authoring feasible |

## Future Considerations

- CDC (logical replication, change tables) for PostgreSQL and SQL Server, declared as a
  capability.
- Predicate/aggregation pushdown for capable sources, enabling a partially federated query
  mode (ADR-008) without changing the connector contract.
- The tenant-deployed agent as a shippable artifact.
- A Singer/Airbyte bridge connector for long-tail SaaS sources.
- Third-party connector authorship, which is when ADR-001's "extract the connector SDK"
  option should be revisited.
