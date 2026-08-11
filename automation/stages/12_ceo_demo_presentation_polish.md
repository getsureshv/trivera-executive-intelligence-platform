# CEO demonstration presentation polish — bounded assignment

## Baseline and owner

Start from immutable tag `ceo-demo-v1` (`4f4b1164b48eff8033242893463b5aa0dd1e9195`).
Claude Code implements one bounded, uncommitted presentation pass. Codex independently reviews
the entire patch and owns all verification, commit, push, CI, evidence, and tagging.

## Frozen boundaries

Do not change Python/backend behavior, migrations, persistence, RLS, authorization, tenant
security, connector/source logic, API routes or response semantics, generated OpenAPI, shared
data contracts, governed metric values, calculations, lineage derivation, seed truth, or the
distinction between real connection health and seeded demonstration observations. Never imply
live extraction. Do not enable trace, video, HAR, or persisted browser session state.

## Required presentation and reliability work

1. Establish one canonical local demo URL and start path, documented and mechanically easy to
   invoke. Avoid stale-port ambiguity by using an explicit dedicated demo port and a readiness
   check that verifies the TriVera page rather than accepting any HTTP server. Do not terminate
   unknown processes or weaken network/security configuration.
2. Polish development sign-in for a CEO demo while retaining the development-only boundary,
   short-lived token path, membership verification, no-password design, and production OIDC
   behavior. Prefer a clearly marked demo identity path; do not add credentials or bypass auth.
3. Make `/app/executive` CEO-readable using only existing governed values: human-formatted
   currency, signed percentages and variances, and readable dates/times; a clear segment-section
   title; a compact target-progress or comparison treatment; and plain-language Technology
   attention reasoning derived from the existing attention/target values.
4. Retain one unmistakable `Demo dataset / seeded demonstration data` disclosure while reducing
   repetitive technical/demo wording elsewhere. Simplify visible trust copy while preserving
   the complete provenance values and full lineage path in the DOM and API-driven rendering.
5. Preserve accessible keyboard behavior and ensure laptop and 390-pixel layouts have no
   clipping, overflow, or hidden evidence.
6. Update focused presentation tests, real browser assertions, the CEO walkthrough, and the
   credential-safe fallback screenshot to match the polished presentation. Tests must still
   prove values originate from governed APIs, exact reconciliation, tenant isolation, and
   secret/artifact safety.

## Required verification and delivery

- Claude returns an uncommitted patch and exact focused results; it must not edit
  `automation/status.md` or `automation/results/`, commit, push, or tag.
- Codex reviews every changed line and permits at most one focused repair.
- Run Prettier, ESLint, strict TypeScript, focused web tests, production build, contract drift,
  two real reset/reseed browser rehearsals with zero skips, secret/log/artifact scans, and all
  repository-required regressions needed to prove frozen security boundaries.
- Commit and push the implementation separately; require all seven GitHub Actions jobs green.
- Record final evidence, require records CI green, then create the unused immutable tag
  `ceo-demo-v2` on the exact final verified commit. Confirm local HEAD, remote `main`, and the
  remote tag match and the repository is clean.

Stop for any backend/contract/security need, new product choice, dishonest data claim, failed
or skipped mandatory test, CI failure, permission blocker, or scope outside this assignment.
