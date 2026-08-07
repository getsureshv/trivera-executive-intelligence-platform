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
--  eip_app        The application runtime role. ONE login credential shared by
--                 the API and the worker.
--                 NOSUPERUSER, NOBYPASSRLS, not a table owner — so Row-Level
--                 Security genuinely applies on the control plane.
--
--                 **NOINHERIT is load-bearing, not stylistic.**
--                 eip_app is made a member of every per-tenant analytical role
--                 so it can `SET LOCAL ROLE` into exactly one of them. With
--                 INHERIT (the PostgreSQL default) it would silently acquire
--                 the *union* of every tenant's privileges, which is precisely
--                 the hole this design exists to close. The API and worker
--                 assert NOINHERIT at startup and refuse to boot otherwise.
--
--  eip_platform   The EXPLICIT privileged path. BYPASSRLS.
--                 Used only by audited platform-admin operations such as
--                 creating a tenant. Reached through a separate engine, from a
--                 separate module, requiring a PlatformContext that cannot be
--                 constructed without a recorded reason.
--                 CREATEROLE so it can provision per-tenant analytical roles.
--                 **The worker does NOT hold this credential** (see below).
--
--  eip_t_<uuid>   One NOLOGIN role per provisioned tenant, created at
--                 provisioning time. Granted USAGE on exactly one schema —
--                 its own. Has no password and cannot log in; it is reachable
--                 only via `SET LOCAL ROLE` from eip_app.
--
-- ===========================================================================
--  CONNECTION ROUTING
-- ===========================================================================
--
--  Control plane   eip_app  →  public schema, RLS scoped by app.tenant_id
--                             (SET LOCAL, transaction-scoped)
--
--  Analytical      eip_app  →  SET LOCAL ROLE eip_t_<uuid>
--                             After this, current_user is the tenant role and
--                             PostgreSQL denies any reference to another
--                             tenant's schema — regardless of what SQL is
--                             issued, because the privilege simply is not held.
--
--  Privileged      eip_platform  →  separate engine, API process only
--
--  Both roles use one connection pool each. There is no per-tenant connection
--  pool and no per-tenant password, so there is no per-tenant secret to store
--  or rotate. `SET LOCAL ROLE` is transaction-scoped, so a pooled connection
--  cannot carry a tenant role into the next checkout.
--
-- Passwords here are local-development placeholders. Real deployments obtain
-- credentials from the secret manager through the SecretStore port; nothing
-- reaches source control (ADR-015, guardrail 9).
-- ---------------------------------------------------------------------------

CREATE ROLE eip_migrator LOGIN PASSWORD 'local_dev_only'
    NOSUPERUSER NOBYPASSRLS NOINHERIT;

-- NOINHERIT: see the note above. Removing it silently defeats analytical
-- isolation, so tests/security/test_analytical_isolation.py asserts it.
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

-- eip_platform creates the per-tenant analytical schemas and roles.
GRANT CREATE ON DATABASE eip TO eip_platform;

-- pg_stat_statements backs per-tenant query cost attribution (ADR-014 §7).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
