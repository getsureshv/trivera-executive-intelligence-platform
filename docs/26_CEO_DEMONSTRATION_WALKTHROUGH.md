# CEO Demonstration Walkthrough — Governed Revenue Evidence

This walkthrough demonstrates a production-quality **seeded demonstration vertical slice**.
The PostgreSQL connection test is real. Revenue observations are deterministic seeded demo
data and are not extracted from the selected source.

## Deterministic setup and reset

1. Start the documented application profile and wait for PostgreSQL, Redis, API, worker, and
   web readiness:

   ```sh
   docker compose -f infra/docker-compose.yml --profile app up -d --build --wait
   ```

2. Apply migrations and run `python -m eip.scripts.seed_demo` in the API container to create
   the two demonstration organizations.
3. Provision a temporary least-privilege PostgreSQL login. Mask its unique password and make
   it available only as `EIP_E2E_SOURCE_PASSWORD`; never print or save the value.
4. Sign in as the Acme tenant administrator. Add that PostgreSQL source and select **Test
   connection**. Continue only after the current source version reports **Succeeded**.
5. Resolve the authenticated tenant, selected source, and author identifiers, then run:

   ```sh
   python -m eip.scripts.seed_executive_demo \
     --tenant-id TENANT_UUID \
     --source-id SUCCESSFULLY_TESTED_SOURCE_UUID \
     --author-id AUTHOR_UUID
   ```

   The guarded command is the reset as well as the initial seed. It rejects production
   environments, cross-tenant or disabled sources, and sources without a successful test for
   their current version. It removes only the selected tenant's deterministic demo bundle and
   reseeds the same immutable graph. It does not query or copy source business data.

The Playwright global setup performs steps 4–5 through the public tenant API plus the guarded
container command. A rehearsal therefore cannot accidentally use an untested or unrelated
source.

## Ten-minute CEO path

1. **Add PostgreSQL Source.** Show that the credential field clears after submission and no
   credential value is returned. Explain that tenant ownership and database row-level
   security are enforced beneath the API.
2. **Test Connection.** Run the test and show **Connection succeeded**. This is real selected-
   source connection-health evidence.
3. **Open Executive.** Open **Executive** and orient on Revenue YTD, its calendar-YTD period,
   as-of time, prior comparison, target variance, freshness, quality, and accountable-owner
   role.
4. **State the data boundary.** Point to **Demo dataset / seeded demonstration data** and the
   statement that observations were not live extracted. Do not describe the values as
   customer data or live analytics.
5. **Drill down.** Show the configured segment breakdown and the **Reconciled** result. The
   displayed segment values sum exactly to the headline using decimal arithmetic.
6. **Requires Attention.** Use the attention control. It moves to the API-selected segment;
   the selection follows the persisted largest-negative-target-variance rule.
7. **One-click trust.** Open the trust view. Show configuration version, snapshot, calculation
   time, source-health-only relationship, and request-derived lineage from the experience to
   governed metric, semantic binding, dataset, and selected source.

## Expected proof

- Tenant A sees only Tenant A evidence. A forged Tenant B selection returns to sign-in and
  never renders Tenant B identifiers.
- The headline and configured segments reconcile exactly; prior and target variance strings
  match the governed API.
- The selected source has a successful current-version test, while provenance explicitly says
  the revenue observations are seeded and not live extraction.
- Credential sentinels are absent from API JSON, browser responses, URL, HTML, storage,
  screenshots, logs, database metadata, audit/outbox data, and saved artifacts.

## Recovery and fallback

- If setup fails before the source test succeeds, repair PostgreSQL/worker connectivity and
  retry; do not bypass the prerequisite.
- If the demo graph is incomplete, rerun the guarded seed command with the same three
  identifiers. It is deterministic and tenant-scoped.
- If Redis is delayed, wait for outbox delivery and worker completion; do not manufacture a
  successful result.
- Traces, video, HAR, and persisted browser session state remain disabled because they can
  contain authorization or cookie material. The approved fallback is a credential-scanned
  PNG of the Executive page plus the text evidence report. Capture it only after the full
  sentinel scan passes, and save it as `docs/evidence/ceo-demo-executive.png`.

