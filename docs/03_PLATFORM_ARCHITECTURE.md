# 03 — Platform Architecture

## Architectural stance

The platform starts as a **modular monolith** with strong bounded contexts. This gives
us clear internal boundaries and the option to extract services later, without paying
the operational and correctness cost of premature microservices. We do **not** start
with unnecessary microservices. A context is extracted into its own service only when a
concrete scaling, isolation, or team-ownership need justifies it, and only via an
accepted ADR.

Two more stances shape everything below:

- **API-first.** Every capability is exposed through a versioned API; the frontend is
  one client among potential others (partner integrations, automation, the assistant).
- **Configuration- and metadata-driven.** Tenant differences are data, not code. The
  same binaries serve every tenant.

## Logical layers

The platform logically separates the following concerns. In the modular monolith these
are modules/packages with enforced boundaries; some (the stores) are external
infrastructure.

- **Frontend** — the executive experience and configuration UIs.
- **API / BFF** — the versioned public API and a backend-for-frontend aggregation layer.
- **Identity & Tenant Services** — authentication, tenant resolution, org/user/role
  management.
- **Configuration** — versioned tenant configuration (draft/published/archived).
- **Connector Framework** — the provider-neutral abstraction over data sources.
- **Ingestion / ELT** — scheduled and on-demand extraction and loading.
- **Data Quality** — profiling, validation, freshness, and quality signals.
- **Semantic Layer** — semantic entities, fields, dimensions, glossary.
- **Mapping / Transformation** — source-field → semantic-field mappings and transforms.
- **Metric Engine** — governed metric definitions and computation.
- **Governed Query Service** — the single, safe path to query metrics with dimensions
  and filters. No arbitrary SQL from clients.
- **Insight Engine** — deterministic/statistical signal detection over metrics.
- **AI Orchestration** — intent detection, query planning, and LLM explanation.
- **Notification / Alerting** — alert rules and delivery.
- **Audit / Observability** — audit trail, tracing, metrics, structured logs.
- **Metadata Store** — the system of record for configuration and governance.
- **Analytical Store** — where analytical data is materialized and queried.
- **Object Storage** — files, extracts, and large artifacts.
- **Cache** — hot query results and session/derived state.

The **Semantic Layer**, **Metric Engine**, and **Governed Query Service** form the spine:
both dashboards and the assistant reach numbers only through them (principle 10).

## Recommended initial stack

The stack is a recommendation to be confirmed in Phase 0 ADRs. It favors mainstream,
well-supported choices and clean abstraction seams so heavier components can be swapped
in later.

**Frontend**

- Next.js
- React
- TypeScript

**Backend (preferred)**

- FastAPI
- Python
- Pydantic (validation / schemas)
- SQLAlchemy (ORM)
- Alembic (migrations)

**Metadata database**

- PostgreSQL

**Cache**

- Redis

**Background processing**

- Celery or Dramatiq initially
- Temporal later, only if durable, long-running workflows justify it

**Analytics**

- PostgreSQL initially where practical
- Abstracted so ClickHouse / Snowflake / BigQuery / Databricks can be introduced later
  without touching business logic

**Object storage**

- An S3-compatible abstraction (not a hard dependency on one vendor)

**Secrets**

- An external secret-manager abstraction (never secrets in code, logs, prompts, or Git)

**Identity**

- OIDC / OAuth
- SAML where enterprise customers require it

**Observability**

- OpenTelemetry
- Structured logs
- Distributed tracing
- Metrics

## Bounded contexts

The system is organized into the following bounded contexts. Each owns its data and
exposes its behavior through explicit interfaces; cross-context access goes through those
interfaces, never through another context's tables.

- **Identity & Tenant** — organizations, users, memberships, roles, permissions, tenant
  resolution.
- **Source Connectivity** — data sources, connection tests, connector implementations,
  discovery of source objects and fields.
- **Data Operations** — ingestion/ELT jobs, scheduling, data quality and profiling.
- **Semantic Model** — semantic entities, semantic fields, dimensions, dimension
  values, glossary terms, field mappings, transformations.
- **Metric Governance** — metric definitions, dimensions, filters, targets, thresholds,
  versions, ownership, lineage.
- **Query** — the governed query service that plans and executes metric queries safely.
- **Insight** — signals and insights derived from metrics.
- **AI** — intent detection, query planning for natural language, and LLM explanation.
- **Dashboard / Experience** — dashboards, widgets, saved views, the executive command
  center.
- **Audit / Governance** — audit events, change history, approvals, configuration
  versioning.

These map onto the domains in [`09_DOMAIN_MODEL_API_CONTRACTS.md`](09_DOMAIN_MODEL_API_CONTRACTS.md)
and the layers above.

## How a request flows (illustrative)

A dashboard KPI render, end to end, to show the spine in motion:

1. The **Frontend** requests a metric value via the **API / BFF**.
2. **Identity & Tenant** resolves the tenant and authorizes the caller.
3. The **Governed Query Service** looks up the governed **Metric** definition.
4. The **Metric Engine** compiles the definition against the **Semantic Layer**
   (semantic fields → mappings → transformations → source fields).
5. The query executes against the **Analytical Store**, with **Cache** in front.
6. **Data Quality** and freshness metadata are attached to the result.
7. The result returns with everything needed for **lineage** and trust badges.

The **assistant** follows the same spine (see
[`06_AI_CHAT_ARCHITECTURE.md`](06_AI_CHAT_ARCHITECTURE.md)); it does not get a private,
faster, or less-governed path.

## Multi-tenancy at the architecture level

Tenant isolation is a cross-cutting concern enforced at every layer: tenant-scoped
identities and authorization, tenant-scoped metadata and analytical data, tenant-scoped
cache keys and object-storage prefixes, and tenant context propagated through tracing
and logs. The details and the isolation strategy live in
[`07_SECURITY_MULTITENANCY_GOVERNANCE.md`](07_SECURITY_MULTITENANCY_GOVERNANCE.md).

## Why a modular monolith first

Starting as a modular monolith keeps transactional integrity simple, makes refactoring
cheap while the domain is still settling, and avoids distributed-systems failure modes
before we have the load to justify them. The bounded contexts give us the seams to
extract services later. The rule stands: **no microservices without an accepted ADR.**
