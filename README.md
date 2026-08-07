# TriVera Executive Intelligence Platform

A multi-tenant **Executive Intelligence Platform**. Organizations connect their
operational systems and obtain a trusted, explainable, continuously updated view of
business performance:

```
DATA → BUSINESS MEANING → GOVERNED METRICS → INSIGHTS → DECISION SUPPORT → ACTION
```

> **This file documents how to run the platform.** The architecture lives in
> [`docs/`](docs/README.md) and the binding decisions in [`docs/adr/`](docs/adr/README.md).
> Implementation documentation never replaces architecture documentation.

## Current status — Phase 1A (platform skeleton)

The repository contains the **platform foundation only**. There is deliberately no
business-intelligence functionality: no connectors, no schema discovery, no semantic
model, no metric engine, no dashboards, no lineage, no insights, and no AI assistant.
Those belong to Phase 1B onward — see
[`docs/10_IMPLEMENTATION_ROADMAP.md`](docs/10_IMPLEMENTATION_ROADMAP.md).

What Phase 1A does prove: the application runs, the frontend and backend communicate,
PostgreSQL persistence works, **tenant context is enforced and one tenant cannot reach
another's data**, authentication and authorization boundaries exist, migrations work,
audit events are recorded, services report health and readiness, and all of it is
verified automatically.

## Prerequisites

| Tool             | Version | Notes                                                    |
| ---------------- | ------- | -------------------------------------------------------- |
| Docker + Compose | 24+     | Runs PostgreSQL, Redis, the API, and the worker          |
| Node.js          | 20+     | Frontend                                                 |
| pnpm             | 10+     | `corepack enable && corepack prepare pnpm@10 --activate` |
| Python           | 3.12+   | Only needed to run lint/typecheck/tests on the host      |

## Getting started

```bash
cp .env.example .env
```

`.env` is git-ignored and contains **local-development placeholders only**. Never put a
real credential in it or in `.env.example` (ADR-015).

### 1. Start the platform

```bash
docker compose -f infra/docker-compose.yml --profile app up -d --build --wait
```

This starts PostgreSQL and Redis, applies migrations, then starts the API and the
worker — waiting until every health check passes.

| Service       | URL                          |
| ------------- | ---------------------------- |
| API           | http://localhost:8000        |
| API docs      | http://localhost:8000/docs   |
| Worker health | http://localhost:8001/health |
| PostgreSQL    | `localhost:5432`             |
| Redis         | `localhost:6380`             |

Infrastructure only, if you would rather run the Python services yourself:

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 2. Start the frontend

The frontend runs on the host — Next.js's dev loop is materially faster outside a
container and it needs no native dependencies.

```bash
pnpm install
pnpm dev            # http://localhost:3000
```

### 3. Create a tenant and sign in

Tenant provisioning is a **platform-staff operation**, deliberately not self-serve in
Phase 1A. Bootstrap the first platform administrator and two tenants with:

```bash
docker compose -f infra/docker-compose.yml --profile app exec api python -m eip.scripts.seed_demo
```

Then open http://localhost:3000, and sign in as one of the printed accounts.

## Everyday commands

### Starting and stopping

```bash
docker compose -f infra/docker-compose.yml --profile app up -d --build --wait
```

```bash
docker compose -f infra/docker-compose.yml --profile app down
```

Reset everything, including the database volume:

```bash
docker compose -f infra/docker-compose.yml --profile app down -v
```

### Running each service individually

API (in the container, which is where the PostgreSQL driver is installed):

```bash
docker compose -f infra/docker-compose.yml --profile app up -d api
```

Worker:

```bash
docker compose -f infra/docker-compose.yml --profile app up -d worker
```

Frontend:

```bash
pnpm --filter @eip/web dev
```

### Migrations

Schema changes ship as migrations and only ever run as the `eip_migrator` role. The
runtime role has no DDL rights at all, so an accidental `create_all()` cannot mutate the
schema (guardrail 17).

Apply:

```bash
docker compose -f infra/docker-compose.yml --profile app run --rm migrate
```

Create a new revision (after changing the ORM models):

```bash
docker compose -f infra/docker-compose.yml --profile app run --rm --entrypoint "" migrate alembic revision --autogenerate -m "describe the change"
```

Roll back one revision:

```bash
docker compose -f infra/docker-compose.yml --profile app run --rm --entrypoint "" migrate alembic downgrade -1
```

Every new migration **must** enable `FORCE ROW LEVEL SECURITY` and add a tenant policy
for any table carrying `tenant_id`. A test asserts this and will fail the build otherwise.

### Tests

Unit and architecture tests need no infrastructure:

```bash
python -m pytest apps/api/tests/unit apps/api/tests/architecture -q
```

The full suite, including the integration and release-gating security tests:

```bash
docker compose -f infra/docker-compose.yml --profile app run --rm -e EIP_ENV=ci api python -m pytest tests -q
```

The tenant-isolation suite on its own:

```bash
docker compose -f infra/docker-compose.yml --profile app run --rm -e EIP_ENV=ci api python -m pytest tests/security -v
```

> **`apps/api/tests/security/` and `apps/worker/tests/` are release-gating.** A failure
> there means one customer can read another's data. Do not skip, `xfail`, or weaken them
> to unblock a release; if the behaviour genuinely changed, the change is wrong until a
> superseding ADR says otherwise.

Frontend:

```bash
pnpm -r test
```

### Lint, format, and typecheck

Python — `mypy --strict` is mandatory (ADR-002 §1); loosening it requires an ADR
reference in the pull request:

```bash
python -m ruff format apps/api apps/worker
```

```bash
python -m ruff check apps/api apps/worker
```

```bash
cd apps/api && python -m mypy
```

TypeScript:

```bash
pnpm format:check && pnpm -r lint && pnpm -r typecheck
```

### Health and readiness

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

```bash
curl -s http://localhost:8000/ready | python -m json.tool
```

`/health` is **liveness** — is the process running? It touches no dependency, so a brief
database outage does not restart every replica. `/ready` is **readiness** — can this
process serve _correct_ traffic? It verifies the database, the migration state, and that
tenant isolation is actually enforced. A process that is live but not ready is removed
from the load balancer and left alone.

## Repository layout (ADR-001)

```
apps/
  api/          FastAPI modular monolith — the only holder of database credentials
    src/eip/
      platform/   cross-cutting: tenancy, config, errors, telemetry, ports
      identity/   Identity & Tenant context
      governance/ Audit trail and transactional outbox
      dataplane/  TenantDataPlane port + the approved implementation
      api/        HTTP routers; the only package that imports FastAPI
    migrations/ Alembic
    tests/      unit · architecture · integration · security
  worker/       Background worker (outbox relay) — imports the api package
  web/          Next.js application; NO database credentials, in any environment
packages/
  contracts/    Shared API contract types + the committed OpenAPI document
  config/       Shared TypeScript configuration
infra/
  docker-compose.yml
  docker/       Container definitions
  postgres/init Database role setup
docs/           Architecture, ADRs, research
```

Bounded-context boundaries are enforced in CI by
`apps/api/tests/architecture/test_module_boundaries.py`. A context may depend on
`eip.platform` and on another context's public surface — never its internals.

## How tenant isolation works

Phase 1A's most important property, implemented per
[ADR-003](docs/adr/ADR-003-multi-tenant-architecture.md).

**Tenant context originates from the authenticated principal's verified membership.** A
browser-supplied tenant identifier is at most a _request_: the server looks up the
membership row and refuses if it does not exist. `X-Tenant-Id` and similar headers are
actively ignored.

**Three database roles, deliberately distinct:**

| Role           | Attributes                                      | Used by                                |
| -------------- | ----------------------------------------------- | -------------------------------------- |
| `eip_app`      | not superuser, **NOBYPASSRLS**, not table owner | every request and job                  |
| `eip_platform` | **BYPASSRLS**                                   | audited platform-admin operations only |
| `eip_migrator` | owns the schema                                 | Alembic only                           |

**Two independent layers.** Application code filters by tenant _and_ every tenant-scoped
table has `FORCE ROW LEVEL SECURITY` with a policy resolving `app.tenant_id`, set
transaction-locally at the start of each transaction. If application filtering is ever
forgotten, the query returns **zero rows** rather than another customer's data.

The API and worker both **refuse to start** unless the runtime role is genuinely
constrained and every tenant-scoped table has an enforced policy. Booting with isolation
silently disabled would pass every functional test while being catastrophically wrong.

Privileged cross-tenant access exists, but it is a separate role, a separate dependency,
a separate module, and it requires an `X-Elevation-Reason` header. Every use is audited
into the affected tenant's own trail.

## Notes for specific platforms

### Windows on ARM64

`asyncpg` is a C extension with no prebuilt wheel for Windows/ARM64, and installing it
would require MSVC build tools. This is why the Python services run in containers: the
container is what makes the environment reproducible rather than "works if you have a
compiler".

Lint, typecheck, and the unit/architecture tests still run natively — the driver is an
optional extra (`apps/api[postgres]`) and nothing in `eip` imports it directly.

### Port conflicts

Redis publishes on **6380** by default, because 6379 collides with a stray local Redis
often enough that defaulting to it costs more time than it saves. Override with
`EIP_REDIS_HOST_PORT`, and keep `EIP_REDIS_URL` in sync.

## Security expectations for contributors

- **No secrets in source control** — not in code, logs, prompts, Git, or ordinary
  metadata. Credentials are referenced, never stored (ADR-015).
- **Never trust a client-supplied tenant identifier.**
- **Every new tenant-scoped table** needs `tenant_id`, `FORCE ROW LEVEL SECURITY`, and a
  policy. Tests enforce this.
- **Authorization precedes data access**, on every surface.
- **Never log** metric values, dimension values, source field values, credentials, or raw
  prompts (ADR-014 §6).
- **Run lint, typecheck, and tests before calling a task done.** A task with a failing
  check is not complete.
