# Phase 2 Thursday Walkthrough — Data Source Manager

## Setup

1. Start the documented Docker application profile and wait for PostgreSQL, Redis, API,
   worker, and web readiness.
2. Run migrations and seed the two demo organizations.
3. Provision a temporary read-only PostgreSQL source login with a unique masked password.
   Never display, paste into chat, or save that password in an artifact.
4. Confirm the production `SecretStore` adapter is still an open deployment gate; this is
   a local/CI walkthrough, not production readiness.

## Walkthrough

1. Sign in as the Acme tenant administrator and open **Data sources**.
2. Add a PostgreSQL source using the temporary host, port, database, username, TLS choice,
   and password. Point out that the password clears immediately and never returns.
3. Select **Test connection**. Explain that the request commits a tenant-owned job, audit
   event, and identifier-only outbox message before Redis delivery.
4. Show the six ordered safe checks: network, TLS, authentication, authorization, metadata
   access, and latency. Expected result: **Connection succeeded**.
5. Show a wrong-password run in a disposable source if prepared. Expected result:
   authentication failure with a safe code and no driver text or credential.
6. Disable the source. Expected result: status becomes disabled immediately, new tests are
   refused, and the UI shows the 30-day destruction deadline without a secret reference.

## Security proof

- Sign in as the other seeded tenant and attempt the first tenant's saved identifiers.
  Expected: the same not-found response as a random identifier.
- Show the stale-attempt test: attempt A pauses before credential/network access, deletion
  increments the source version, then A resumes. Expected: A becomes stale, performs no
  secret/network access, and emits no completion event.
- Run the credential-sentinel scan over database text/JSON, API bodies, audit/outbox,
  broker data, logs, browser HTML/storage/responses, screenshots, and saved artifacts.
  Expected: zero matches.
- Advance the injected clock: before day 30 the credential remains; at day 30 maintenance
  destroys only the intended reference. Before day 90 test rows remain; after day 90
  terminal rows disappear while audit rows remain.

## Fallback and recovery notes

- If Redis is unavailable, the PostgreSQL outbox retains work; do not recreate the source.
- If a worker dies, its persisted lease expires and redelivery safely reclaims the attempt.
- If secret destruction succeeds but its database transaction fails, rerun maintenance;
  deletion is idempotent and the completion audit is written only with the database claim.
- If the browser test fails, preserve only the configured redacted diagnostics. Traces,
  videos, session storage state, and credential-bearing screenshots remain disabled.
