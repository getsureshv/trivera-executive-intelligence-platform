# ADR-001: Repository Architecture

Status: Accepted
Date: 2026-08-07
Phase: 0 — Architecture validation

## Context

The platform is a **modular monolith** (`03_PLATFORM_ARCHITECTURE.md`) with a TypeScript
frontend and a Python backend, plus background workers, an eventual connector SDK, and a
versioned public API that the UI consumes as a client (principle 8).

Two forces pull in opposite directions:

1. **Cohesion.** The API contract, the generated client, the semantic/metric model, and
   the UI change together. Splitting them across repositories creates a permanent
   version-skew tax on a product whose whole value proposition is "there is exactly one
   definition of every number."
2. **Boundary enforcement.** A monolith in one repository degrades into a ball of mud
   unless boundaries are *mechanically* enforced. "Strong bounded contexts" is a claim
   that must be checkable in CI, not an aspiration in a document.

There is also a specific hazard created by choosing Next.js (ADR-002): a Node runtime
sitting next to the database is a standing invitation to open a second, ungoverned data
path straight from a route handler to Postgres, bypassing the Governed Query Service.
Repository and deployment structure is one of the few places that hazard can be designed
out rather than policed.

## Decision

**One Git repository (a polyglot monorepo), no monorepo build framework, with bounded
contexts enforced by static import contracts in CI.**

Layout:

```
/apps
  /api                 FastAPI modular monolith (the only holder of DB credentials)
    /src/eip
      /platform        cross-cutting: tenancy, config, errors, telemetry, ports
      /identity        Identity & Tenant context
      /connectivity    Source Connectivity context
      /dataops         Data Operations context (ingestion, quality, profiling)
      /semantic        Semantic Model context
      /metrics         Metric Governance context
      /query           Governed Query context
      /insight         Insight context
      /ai              AI context
      /experience      Dashboard / Experience context
      /governance      Audit / Governance context
      /api             HTTP routers; the ONLY place FastAPI is imported
    /migrations        Alembic
    /tests
  /worker              Background worker entrypoint; imports apps/api packages
  /web                 Next.js app (no database credentials, ever)
/packages
  /api-client          TypeScript client GENERATED from the OpenAPI contract
  /ui                  shared React components / design system
/contracts             OpenAPI + JSON Schemas, generated and committed
/docs                  this documentation set, ADRs, research
/infra                 IaC, container definitions, environment config
/tests/e2e             cross-tier acceptance tests
```

Enforcement rules, all in CI:

- **`import-linter` contracts** define the allowed dependency graph between
  `eip.*` context packages. A context may depend on `eip.platform` and on another
  context's public `interfaces` module only — never on its `models`, `repositories`, or
  `services` internals. Violations fail the build.
- **`eip.api` (HTTP) may not import a context's internals** either; routers call context
  interfaces.
- **`apps/web` has no database driver dependency and no database credentials in any
  environment.** This is enforced by dependency policy and by secret scoping in `/infra`,
  not by convention.
- **`/contracts` is generated from the backend and committed.** `packages/api-client` is
  generated from `/contracts`. A pull request that changes an endpoint without
  regenerating both fails CI. This is how "API-first" stops being a slogan.
- One version, one release train: the repository is versioned and released as a unit.

## Alternatives Considered

- **Polyrepo (separate `web`, `api`, `connectors`, `contracts` repositories).** Rejected.
  Every cross-cutting change — and in a metadata-driven platform nearly all of them are —
  becomes a multi-repository, multi-PR choreography. Contract skew becomes chronic. The
  cost is paid daily; the benefit (independent release cadence) is not needed until teams
  are independent, which is years away.
- **Monorepo with Nx / Turborepo / Bazel.** Rejected for now. These pay off with many
  packages and long build graphs. We have two toolchains and a handful of packages;
  `uv`/`pip-tools` + `pnpm` + a Makefile is sufficient and far cheaper to reason about.
  Revisit when the TypeScript package count exceeds roughly six.
- **Separate repository for connectors ("connector SDK").** Rejected now, revisit later.
  Connectors are the most likely thing a third party will eventually author, which is the
  real argument for extraction. Until there is an external author, extraction only buys
  version skew. The connector abstraction (ADR-004) is designed so extraction stays cheap.
- **Package-by-layer (`models/`, `services/`, `api/` at the top level) instead of
  package-by-context.** Rejected. Package-by-layer makes every feature a change across
  every top-level directory and makes boundary enforcement impossible — the exact failure
  mode the modular monolith exists to avoid.

## Rationale

A monorepo makes the *contract* the unit of coordination rather than the *repository*,
which is the right coupling for a system with one governed spine. `import-linter`
contracts convert "strong bounded contexts" from a review-time opinion into a build-time
invariant, which is what makes later service extraction (if ever justified) a mechanical
operation rather than an archaeology project.

Denying the Node tier any database access is the single highest-leverage structural
decision available at this stage: it makes the governed query path the *only* path by
construction, satisfying principles 3 and 10 without depending on anyone's discipline.

## Consequences

- Positive: atomic cross-tier changes; one CI pipeline; generated client eliminates a
  whole class of drift; boundary violations are caught mechanically; deployment topology
  changes (extracting the worker, later extracting a context) do not require repository
  surgery.
- Positive: a new engineer can read the whole system without cloning five repositories.
- Negative: CI runtime grows with the repository; needs path-filtered jobs early.
- Negative: two toolchains in one repository means two dependency ecosystems, two
  lockfiles, two linters. Accepted cost.
- Negative: coarse-grained access control — everyone with repository access sees
  everything. Acceptable for a small team; revisit if third-party connector authors
  arrive.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| Import contracts are added but weakened over time to unblock a PR | Contract file diff review; contract count/strictness tracked | Changing an `import-linter` contract requires an ADR reference in the PR description |
| Generated client drifts from `/contracts` | CI regenerates and diffs; non-empty diff fails | Generation is a build step, not a manual chore |
| Someone adds a DB driver to `apps/web` | Dependency allowlist check in CI | Also enforced by not issuing DB credentials to the web deployment |
| CI wall-clock becomes a bottleneck | Track p50/p95 pipeline duration | Path-filtered jobs, test sharding, cached layers |

## Future Considerations

- Extract `apps/worker` into its own deployable unit while remaining in the repository —
  already the intended shape.
- If a context (most plausibly Query or Ingestion) needs independent scaling or isolation,
  extract it as a service; the import contracts already define its surface. Requires a new
  ADR per `11_AGENT_GUARDRAILS.md` guardrail 7.
- Publish `packages/api-client` to a private registry when partner integrations appear
  (principle 8).
- Reconsider a build framework and a separate connector repository together, when
  third-party connector authorship becomes real.
