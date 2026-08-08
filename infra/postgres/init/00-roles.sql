-- ---------------------------------------------------------------------------
-- TriVera EIP — PostgreSQL role setup (ADR-003).
--
-- Runs once, automatically, on an empty data directory.
--
-- ===========================================================================
--  ROLE AND CREDENTIAL MODEL
-- ===========================================================================
--
--  eip_migrator   Owns the schema. Runs Alembic. Never used at runtime.
--                 Owns the SECURITY DEFINER functions, so those functions can
--                 write objects that no runtime role may write directly.
--
--  eip_app        The CONTROL-PLANE runtime role, shared by the API and the
--                 worker. NOSUPERUSER, NOBYPASSRLS, not a table owner — so
--                 Row-Level Security genuinely applies.
--
--                 **It holds no privilege on any tenant analytical schema and
--                 is a member of no tenant role.** Analytical data is reached
--                 with each tenant's own credential, never by this one. The API
--                 and worker assert both properties at startup and refuse to
--                 boot otherwise.
--
--                 NOINHERIT is retained as hygiene rather than as the
--                 mechanism: with no memberships there is nothing to inherit,
--                 but a mistakenly granted membership would then still confer
--                 no privileges.
--
--  eip_platform   The EXPLICIT privileged path. BYPASSRLS.
--                 Used only by audited platform-admin operations such as
--                 creating a tenant. Reached through a separate engine, from a
--                 separate module, requiring a PlatformContext that cannot be
--                 constructed without a recorded reason.
--                 CREATEROLE so it can provision per-tenant analytical roles.
--                 **The worker does NOT hold this credential** (see below).
--
--  eip_t_<uuid>   One LOGIN role per provisioned tenant, created at
--                 provisioning time with its own generated password held in
--                 the SecretStore. Granted USAGE on exactly one schema — its
--                 own — and a member of nothing.
--
--                 Connections authenticate *as* this role. Nothing assumes it,
--                 and it can assume nothing, so a statement naming another
--                 tenant's schema is refused because the connection is not
--                 that tenant and has no means of becoming it.
--
-- ===========================================================================
--  CONNECTION ROUTING
-- ===========================================================================
--
--  Control plane   eip_app       →  public schema, RLS scoped by app.tenant_id
--                                  (SET LOCAL, transaction-scoped). One pool.
--
--  Analytical      eip_t_<uuid>  →  its OWN pool, authenticated with its OWN
--                                  password. Nothing is assumed and nothing is
--                                  switched: the connection can only ever be
--                                  one tenant. Pools are bounded per tenant
--                                  with LRU and idle eviction, because
--                                  max_connections is a hard cluster limit.
--
--  Privileged      eip_platform  →  separate engine, API process only. The
--                                  worker never holds this credential.
--
--  `SET ROLE` appears nowhere in the codebase; an architecture test asserts it.
--  An earlier design had eip_app assume a per-tenant role, which PostgreSQL
--  enforced only *after* the switch — leaving which tenant was reached an
--  application decision. That was Phase 1A finding G10, and it is closed
--  (see docs/19_PHASE_1A_REPORT.md).
--
-- Passwords here are local-development placeholders. Real deployments obtain
-- credentials from the secret manager through the SecretStore port; nothing
-- reaches source control (ADR-015, guardrail 9).
-- ---------------------------------------------------------------------------

CREATE ROLE eip_migrator LOGIN PASSWORD 'local_dev_only'
    NOSUPERUSER NOBYPASSRLS NOINHERIT;

-- NOINHERIT: see the note above. Asserted at startup and by
-- tests/security/test_analytical_credentials.py.
CREATE ROLE eip_app LOGIN PASSWORD 'local_dev_only'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;

-- CREATEROLE is required to provision per-tenant analytical roles. In
-- PostgreSQL 16+ a CREATEROLE role cannot grant itself superuser or alter
-- roles it did not create, which bounds the blast radius.
CREATE ROLE eip_platform LOGIN PASSWORD 'local_dev_only'
    NOSUPERUSER BYPASSRLS NOCREATEDB CREATEROLE NOINHERIT;

-- Migration 0002 assigns ownership of eip_outbox_pending_tenants() to
-- eip_platform, and PostgreSQL requires the assigning role to be a member of
-- the target role. This membership grants that and nothing else: eip_migrator
-- is NOINHERIT, so it can SET ROLE to eip_platform during a migration but does
-- not silently acquire BYPASSRLS for ordinary statements. The migrator never
-- serves traffic — it exists only to run Alembic.
GRANT eip_platform TO eip_migrator;

-- The migrator owns the database and therefore every object it creates.
ALTER DATABASE eip OWNER TO eip_migrator;

\connect eip

-- public is owned by the migrator; the runtime roles only get to use it.
ALTER SCHEMA public OWNER TO eip_migrator;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  USAGE ON SCHEMA public TO eip_app, eip_platform;

-- eip_app may not create objects in public. Schema changes ship as migrations,
-- run by the migrator (guardrail 17), and the API asserts at startup that the
-- runtime role owns no tenant-scoped table.
REVOKE CREATE ON SCHEMA public FROM eip_app;

-- eip_platform retains CREATE on public because PostgreSQL requires a
-- function's owner to hold CREATE on the function's schema, and migration 0002
-- assigns it ownership of eip_outbox_pending_tenants(). This is a privilege the
-- provisioning role already effectively has — it holds CREATEROLE and CREATE on
-- the database, and creates every tenant schema. The distinction that matters
-- is between eip_platform (privileged, reached only through an audited path)
-- and eip_app (the credential every request runs under), and that distinction
-- is preserved.
GRANT CREATE ON SCHEMA public TO eip_platform;

-- Tables created later by the migrator are granted to the runtime roles
-- automatically. DDL rights are never granted.
ALTER DEFAULT PRIVILEGES FOR ROLE eip_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eip_app, eip_platform;

ALTER DEFAULT PRIVILEGES FOR ROLE eip_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO eip_app, eip_platform;

-- eip_platform creates the per-tenant analytical schemas and login roles,
-- and generates their passwords into the SecretStore.
GRANT CREATE ON DATABASE eip TO eip_platform;

-- pg_stat_statements backs per-tenant query cost attribution (ADR-014 §7).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
