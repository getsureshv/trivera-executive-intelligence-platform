# ADR-016: Bounded-Context Enforcement Mechanism

Status: Accepted
Date: 2026-08-07
Phase: 1A (remediation)
Amends: [ADR-001](ADR-001-repository-architecture.md) — the enforcement
mechanism only. Everything else in ADR-001 stands.

## Context

ADR-001 specified `import-linter` contracts, run in CI, as the mechanism that
turns "strong bounded contexts" from a review-time opinion into a build-time
invariant. That intent is right and is not in question here.

The tool is not usable. `import-linter` depends on `grimp`, a compiled Rust
extension with no prebuilt wheel for Windows on ARM64 — one of the platforms
this repository is actively developed on — and installing it requires a full
MSVC toolchain. During Phase 1A implementation, `pip install` of the dev extras
failed outright at metadata generation.

Two options existed: make the check CI-only, or replace it. CI-only was rejected
because a boundary check a developer cannot run locally is a check they discover
only after pushing, and one they will eventually route around. The value of an
architecture contract is that it fails *while you are writing the violating
import*, not twenty minutes later in a pipeline.

Phase 1A shipped an AST-based replacement without an ADR. The Phase 1A review
correctly flagged that as an unratified deviation. This ADR ratifies it.

## Decision

**Bounded-context import contracts are enforced by
`apps/api/tests/architecture/test_module_boundaries.py`, a dependency-free AST
check that runs as part of the ordinary test suite.** `import-linter` is not a
dependency of this repository.

The check parses every module under `src/eip`, extracts its imports, and asserts
the declared dependency graph:

| Contract | Assertion |
| --- | --- |
| Context graph | Each context may import only the contexts listed in `ALLOWED` |
| Foundation purity | `eip.platform` imports no bounded context |
| Framework containment | Only `eip.api` imports FastAPI or Starlette |
| Driver containment | No module imports `asyncpg`/`psycopg` directly |
| Direction | Domain contexts never import `eip.api` |

Two properties are deliberately preserved from ADR-001's intent:

* **The allowed graph is an explicit literal.** Widening it is a visible diff in
  a file whose only purpose is the architecture contract, which is exactly the
  reviewability `import-linter`'s `.ini` contracts provided.
* **It runs everywhere.** No compiler, no platform-specific wheel, no optional
  dependency. It runs in the fast unit lane, so it costs nothing.

The check also gained a contract `import-linter` could not have expressed
conveniently: `SET ROLE` may appear in exactly one module
(`eip.dataplane.session`), because the analytical isolation guarantee rests
entirely on which role a transaction assumes (ADR-003 §2).

## Alternatives Considered

- **Keep `import-linter`, CI-only.** Rejected. Developers would not run it, and
  a contract violation would surface after push rather than at the moment of
  writing. It also leaves the repository un-installable on a supported
  development platform, which has costs well beyond this check.
- **Keep `import-linter`, require a compiler toolchain.** Rejected. Mandating
  MSVC build tools to run a lint is disproportionate, and it would have to be
  documented, supported, and kept working.
- **Run `import-linter` inside the container.** Viable — the container already
  exists for the same class of reason (the PostgreSQL driver). Rejected because
  the boundary check is a *fast, local* feedback tool; routing it through a
  container image build inverts that. The database tests have no such
  alternative; this check does.
- **Drop mechanical enforcement and rely on review.** Rejected outright. ADR-001
  is explicit that a modular monolith degrades into a ball of mud without a
  checkable contract, and that judgement has not changed.
- **Adopt a different off-the-shelf tool** (`pytestarch`, `tach`). Considered.
  Both are credible; neither is meaningfully better than ~160 lines of `ast`
  walking for the five contracts we actually need, and each is another
  dependency in the critical path of the architecture check. Revisit if the
  contract set grows substantially.

## Rationale

The decision ADR-001 made was *that boundaries must be mechanically enforced*.
That decision is unchanged and, if anything, strengthened: the check now runs on
every developer machine rather than only where a Rust toolchain happens to
build.

What changed is an implementation detail that ADR-001 named specifically —
reasonably, since naming a tool is more useful than gesturing at a category. The
correct response to a named tool becoming unworkable is a superseding decision
record, not a silent substitution, which is what this ADR provides.

## Consequences

- Positive: the architecture contract runs on every supported platform, in the
  fast test lane, with no extra dependency.
- Positive: contracts can express repository-specific rules (`SET ROLE`
  containment) that a generic tool would not.
- Positive: one fewer compiled dependency in the dev toolchain.
- Negative: we maintain ~160 lines that a third party would otherwise maintain.
  Small, stable, and fully covered by its own assertions.
- Negative: the AST check sees only static `import` statements. A dynamic
  `importlib` call would evade it. Accepted: dynamic imports are absent from
  this codebase and would themselves be a review finding.
- Negative: it checks `apps/api/src/eip` only. `apps/worker` is a thin
  entrypoint that imports the api package by design (ADR-001), so it has no
  internal graph to enforce yet.

## Risks

| Risk | Detection | Mitigation |
| --- | --- | --- |
| `ALLOWED` is widened to unblock a pull request | The literal is in a file that does nothing else; diffs are conspicuous | Widening it requires an ADR reference in the pull-request description, the same rule ADR-001 set for `import-linter` contracts |
| A violation is introduced via a dynamic import | Not detectable by this check | Dynamic imports are a review finding in their own right; none exist |
| The check is deleted or marked skip | Test count and CI job | It runs in the same suite as the release-gating security tests |
| Worker-internal boundaries drift once it grows | Manual review | Extend the check to `apps/worker/src` when it acquires internal structure |

## Future Considerations

- Extend the contract set to `apps/worker` when it grows beyond an entrypoint.
- Add a contract asserting that only `eip.adapters.*` imports a vendor SDK, once
  adapters exist (Phase 2, with the first connector and the `SecretStore`).
- Reconsider an off-the-shelf tool if the contract set grows past roughly a dozen
  rules, at which point the maintenance argument flips.
- If `grimp` ships ARM64 wheels and the contract set has grown, migrating back is
  a bounded change: the contracts are already declarative data.
