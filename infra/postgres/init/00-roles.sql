-- ---------------------------------------------------------------------------
-- TriVera EIP — PostgreSQL role setup (ADR-003).
--
-- Runs once, automatically, on an empty data directory.
--
-- Three roles exist deliberately. Collapsing them defeats the isolation model.
--
--   eip_migrator   Owns the schema. Runs Alembic. Never used at runtime.
--                  Subject to RLS anyway because tenant tables use
--                  FORCE ROW LEVEL SECURITY (the owner is normally exempt).
--
--   eip_app        The application runtime role.
--                  NOSUPERUSER, NOBYPASSRLS, and NOT the table owner — so
--                  Row-Level Security genuinely applies. The API asserts all
--                  three at startup and refuses to boot otherwise
--                  (eip.platform.db.assert_runtime_role_is_constrained).
--
--   eip_platform   The EXPLICIT privileged path. BYPASSRLS.
--                  Used only by audited platform-admin operations such as
--                  creating a tenant. It is a separate role, reached through a
--                  separate engine, exercised by a separate test module
--                  (tests/security/test_privileged_platform_access.py).
--
-- Passwords here are local-development placeholders. Real deployments obtain
-- credentials from the secret manager through the SecretStore port; nothing
-- reaches source control (ADR-015, guardrail 9).
-- ---------------------------------------------------------------------------

CREATE ROLE eip_migrator LOGIN PASSWORD 'local_dev_only' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE eip_app      LOGIN PASSWORD 'local_dev_only' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE eip_platform LOGIN PASSWORD 'local_dev_only' NOSUPERUSER BYPASSRLS  NOCREATEDB NOCREATEROLE;

-- The migrator owns the database and therefore every object it creates.
ALTER DATABASE eip OWNER TO eip_migrator;

\connect eip

-- public is owned by the migrator; the runtime roles only get to use it.
ALTER SCHEMA public OWNER TO eip_migrator;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  USAGE ON SCHEMA public TO eip_app, eip_platform;

-- Runtime roles may not create objects in public. Schema changes ship as
-- migrations, run by the migrator (guardrail 17).
REVOKE CREATE ON SCHEMA public FROM eip_app, eip_platform;

-- Tables created later by the migrator are granted to the runtime roles
-- automatically. DDL rights are never granted.
ALTER DEFAULT PRIVILEGES FOR ROLE eip_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eip_app, eip_platform;

ALTER DEFAULT PRIVILEGES FOR ROLE eip_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO eip_app, eip_platform;

-- The tenant data plane (ADR-003): each tenant's analytical data gets its own
-- schema, created by the provisioning subsystem. eip_app is granted USAGE on a
-- tenant schema only when that tenant is provisioned — never blanket-granted.
GRANT CREATE ON DATABASE eip TO eip_platform;

-- pg_stat_statements backs per-tenant query cost attribution (ADR-014 §7).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
