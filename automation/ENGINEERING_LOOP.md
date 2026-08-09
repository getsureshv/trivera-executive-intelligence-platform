# Continuous engineering loop

Last reconciled: 2026-08-09

## Durable operating contract

For each accepted capability: reconcile accepted ADR corrections, roadmap text, and actual
repository state; write a bounded entry report; split it into small sequential stages; then
use Claude implementation, Codex independent review, at most one focused repair, real
infrastructure verification with zero skips, a focused commit, push, green CI, and a clean
repository. Never overlap stages or capabilities.

Stop for a new product decision, ADR conflict, unresolved security issue, failed real-system
verification, failed CI, permission blocker, production credential/deployment action, or
work outside the accepted entry report. This ledger never authorizes production deployment,
purchases, customer-system access, security exceptions, or invented product policy.

## Completed capability

Phase 2 — Data Source Manager and first PostgreSQL connection-test slice is complete after
four sequential stages. It provides tenant-isolated source persistence, SecretStore-only
credentials, authorized management, real background PostgreSQL diagnostics, the browser Add
Source/Test Connection flow, immediate disabling, 30-day credential destruction, and 90-day
safe test-history retention. The production SecretStore adapter remains a deployment gate.

## Reconciled next sequence

1. **CEO demonstration vertical-slice entry review.** Reconcile the explicit CEO scope with
   accepted ADRs and the completed Phase 2 contracts. Define bounded integration, governed
   semantic/metric/query/lineage backend, executive web, and adversarial QA stages. Seeded
   observations must be visibly labelled demo data. No implementation begins during review.
2. **CEO demonstration vertical slice.** Only after an entry report introduces no unresolved
   choice or conflict: sequentially deliver the frozen shared boundary, backend evidence
   path, executive browser experience, and release/rehearsal pack. The target is a
   production-quality demo slice, not a claim that the full platform is complete.
3. **Post-demo roadmap reconciliation.** Re-evaluate the accepted roadmap and repository
   state after the demo. Produce the next bounded capability entry report before any work.
   Continue automatically only where accepted documents fully determine behavior.

The CEO slice remains limited to Connect PostgreSQL → Test Connection → Executive Command
Center → one configured drill-down → trust/provenance traversal. Discovery, profiling, live
extraction, ingestion, object-storage landing, additional connectors, customer-network
agents, caching, alerts, broad dashboards, general AI, and production deployment remain out
of scope unless a later accepted entry report explicitly authorizes them.

## Current handshake

- Current capability: Phase 2 records-only closeout.
- Owner: Codex, then GitHub Actions.
- Evidence: implementation `c981cda`, approved records repair `e4563cf`, CI 31320964652 green.
- Next action: records-only commit, push, green CI, clean repository; then CEO entry review.
- Product-owner action required: no.
