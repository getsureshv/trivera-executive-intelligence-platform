# Stage 01 — Provider-neutral connector contract

## Authority

Required by `docs/10_IMPLEMENTATION_ROADMAP.md`, ADR-004, PO-001, PO-002, and the
Phase 1B entry report. This stage stays inside the approved PostgreSQL connector slice.

## Assignment

Implement the smallest provider-neutral connectivity foundation under a new
`eip.connectivity` bounded context:

- a typed `Connector` protocol with static capabilities and configuration schema;
- serializable, remotely executable value objects for connection targets, extract plans,
  record batches, discovery pages, samples, profiles, and ordered connection diagnostics;
- exact canonical scalar/type representation, including exact decimals and explicit
  unknown values;
- contract tests proving JSON round trips and diagnostic ordering;
- architecture-boundary registration without widening unrelated dependencies.

Do not implement a concrete connector, networking, data-source persistence, discovery,
extraction, semantics, metrics, dashboards, insights, or AI.

## Verification gate

- Ruff formatting and lint
- strict mypy
- complete pytest suite in the documented Docker API container with live PostgreSQL
- no skipped tests
- independent diff review

