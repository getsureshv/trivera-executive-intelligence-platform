# Stage 02 — Direct-mode egress guard

## Authority

Required by ADR-004 and PO-002 for the approved PostgreSQL connector slice. V1 uses
direct outbound connectivity with static egress allowlisting; private-link and agent
modes remain represented but are not implemented here.

## Assignment

Add the smallest provider-neutral egress policy under `eip.connectivity`:

- parse and validate a connection endpoint without opening a connection;
- resolve every DNS answer and reject loopback, link-local, multicast, unspecified,
  reserved, RFC1918/private, and cloud-instance metadata destinations by default;
- validate the selected peer address again immediately before connection so DNS rebinding
  cannot bypass the policy;
- support an explicit immutable allowlist of IP networks for controlled exceptions;
- fail closed on malformed endpoints, DNS errors, empty answers, mixed safe/unsafe answer
  sets, and IPv4-mapped IPv6 addresses;
- return non-secret, stable machine-readable denial codes.

Use dependency injection for resolution/peer inspection so tests are deterministic. Do
not open a database connection, implement PostgreSQL diagnostics, persist data sources,
or begin discovery/extraction.

## Verification gate

- Ruff format/lint and strict mypy
- deterministic unit/security tests for the policy and rebinding boundary
- complete architecture tests
- complete live-PostgreSQL security suites as regression gate, with zero skips
- independent diff review

