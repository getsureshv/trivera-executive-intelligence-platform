# CEO demonstration Stage 4 of 4 — adversarial release and demonstration pack

## Owner and purpose

Claude Code prepares the bounded closeout artifacts; Codex independently reviews the complete
uncommitted patch and runs every acceptance check. Freeze production behavior unless a test
exposes a genuine defect. This stage closes the production-quality **seeded demonstration
vertical slice**; it must never describe the observations as live extracted analytics or
claim the complete platform is finished.

## Required deliverables

1. Provide a deterministic, documented seed/reset procedure that requires a successfully
   tested tenant-owned PostgreSQL DataSource and preserves the visible distinction between
   real connection-health evidence and seeded demonstration observations.
2. Write a concise CEO walkthrough covering setup, Add PostgreSQL Source, Test Connection,
   Revenue YTD, configured drill-down, Requires Attention, one-click trust/provenance, expected
   results, security proof, and fallback/recovery notes.
3. Produce a compact completion/evidence report with exact commits, CI links, test counts,
   seeded-demo honesty statement, and remaining production gaps.
4. Prepare safe screenshot fallback evidence without credentials, session material, raw
   source payloads, or misleading live-data claims. Do not enable Playwright trace/video or
   introduce an unsafe artifact path. If a safe video cannot be produced under existing
   controls, document the safe screenshot fallback explicitly.
5. Define the immutable demo tag to be applied only after the final reviewed Stage 4 commit
   and green CI. Do not create, move, or push the tag in the implementation handoff.
6. Add or refine only closeout-oriented tests/scripts when needed to prove deterministic
   reset, exact values, two-tenant isolation, credential/log/artifact safety, and two complete
   browser rehearsals. No feature expansion.

## Required verification

- Run the complete real PostgreSQL suite with zero skips, including FORCE RLS, cross-tenant,
  exact Decimal reconciliation, immutable configuration, source binding, and leakage tests.
- Run the complete Redis/worker security suite with zero skips.
- Run the complete real browser suite twice against deterministic reset/reseed, with zero
  failures and zero skips. Each rehearsal must cover Add Source → Test Connection → Executive
  Command Center → drill-down → trust/provenance.
- Run Ruff, strict mypy, Prettier, ESLint, strict TypeScript, unit tests, production build,
  migration replay/model parity, OpenAPI/contracts drift, secret scan, log scan, and repository
  artifact scan.
- Codex independently inspects every changed file and all produced evidence. At most one
  focused repair is permitted.
- Commit and push Stage 4 separately, require all seven GitHub Actions jobs green, apply the
  immutable demo tag only to the verified commit, push the tag, and confirm local HEAD,
  remote `main`, tag, and a clean repository agree.

## Explicit exclusions and stop conditions

No discovery, profiling, extraction, ingestion, object-storage landing, semantic authoring,
arbitrary SQL/formulas, caching, alerts, general dashboards, insights, AI, new connectors,
customer-network agents, production SecretStore, production deployment, purchases, or
customer-system access. Stop for a product decision, ADR conflict, security defect, failed or
skipped mandatory check, CI failure, permission blocker, dishonest demo claim, or required
scope outside this assignment.

Return an uncommitted patch with exact evidence. Do not edit `automation/status.md` or
`automation/results/`, commit, push, tag, or start later roadmap work.
