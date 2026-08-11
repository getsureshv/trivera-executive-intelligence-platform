# CEO demonstration — read-only Configured for Acme summary

## Baseline and boundary

Start only after presentation-polish commit `b5e23660dfda88fbad11f5d43372cdeb5d90fe68`
and its seven-job CI are green. Implement the smallest honest, tenant-scoped, read-only
configuration summary supported by current committed APIs and TypeScript contracts. This is a
demonstration of configuration already present, not a self-service builder.

Do not change Python/backend code, routes, migrations, persistence, RLS, authorization,
tenant-security policy, shared contracts, OpenAPI, connector behavior, governed values,
calculations, seed data, or provenance semantics. Do not add mutation controls or imply live
source extraction.

## Required UI

1. Add an authenticated `/app/setup` page titled from the actual tenant returned by `/v1/me`,
   for example `Configured for Acme Industrial`; never hard-code the company name.
2. Compose only existing safe payloads: `/v1/me`, `/v1/data-sources`, the selected source's
   existing latest-connection-test endpoint, `/v1/dashboards/executive`, and the existing
   lineage endpoint. Join the source by the governed provenance `data_source_id`.
3. Present a professional read-only summary of:
   - company/tenant;
   - calendar and timezone from the governed metric period;
   - selected PostgreSQL source name and real latest current-version health;
   - metric name and version, target, prior comparison, and configured segment dimension;
   - accountable owner;
   - dashboard placement derived from the lineage widget node;
   - configuration version and `Published for executive use`, explicitly described as an
     inference from successful delivery by the published-only governed dashboard API rather
     than a raw status field;
   - an Executive Dashboard preview link.
4. Include one unobtrusive Demo Data disclosure and a clear note that this is a configuration
   summary available now; a full self-service configuration builder is a future phase.
5. Add navigation, accessible loading/error/empty behavior, laptop and 390-pixel containment,
   focused presentation tests, and real browser coverage proving API-derived values,
   cross-tenant denial, and credential absence.

## Verification and delivery

Claude returns an uncommitted UI-only patch and does not edit automation status/results,
commit, push, or tag. Codex independently reviews every changed line, permits at most one
focused repair, and runs Prettier, ESLint, strict TypeScript, web tests, production build,
OpenAPI drift, two complete real browser rehearsals with zero skips, secret/artifact scans,
and all seven CI jobs. Refresh the walkthrough and safe screenshot to include the setup view.
After implementation and records CI are green, create the unused immutable `ceo-demo-v2` tag
on the exact final clean commit and confirm local HEAD, remote main, and the remote tag match.

Stop for any backend/contract need, security ambiguity, dishonest status/data claim, failed or
skipped mandatory gate, permission blocker, or scope beyond this read-only summary.
