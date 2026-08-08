"""Database engines and the tenant-scoping session hook (ADR-003).

This module is the *only* place in the codebase that opens a database session.
Everything about tenant isolation on the control plane converges here.

The model, restated from ADR-003 §1:

* **Application scoping is primary.** Repositories filter by ``tenant_id`` and
  take a ``TenantContext`` as a typed argument, so a missing scope is a type
  error with a good message.
* **PostgreSQL Row-Level Security is the backstop.** If application scoping is
  ever forgotten, ``FORCE ROW LEVEL SECURITY`` turns a cross-tenant read into
  *zero rows* rather than another customer's data.

Two engines exist, bound to two different database roles:

``app_engine``       role ``eip_app``      — NOBYPASSRLS, not the table owner.
                     Every runtime request and job uses this.
``platform_engine``  role ``eip_platform`` — BYPASSRLS. The explicit privileged
                     path, used only by audited platform-admin operations and
                     reached only through ``platform_session``.

``SET LOCAL`` semantics matter here: ``set_config(..., is_local => true)`` is
scoped to the *transaction*, so the setting is discarded on commit or rollback
and cannot leak to the next checkout of a pooled connection. There is a test
for exactly that (``tests/security/test_tenant_isolation.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from eip.platform.context import PlatformContext, TenantContext
from eip.platform.errors import ConfigurationError
from eip.platform.logging import get_logger
from eip.platform.settings import Settings

_log = get_logger("platform.db")

#: The PostgreSQL session variable carrying the active tenant. Referenced by
#: every RLS policy created in the migrations. Changing this string breaks
#: isolation, so it is defined once and imported.
TENANT_SETTING: Final = "app.tenant_id"

#: The session variable carrying the authenticated *user*, used by exactly one
#: RLS policy: ``membership_self_select``, which lets a principal read their own
#: membership rows before a tenant is known. Without it, authentication could
#: not discover which tenants a user belongs to without a privileged role —
#: and putting that lookup on the privileged path would mean every sign-in ran
#: with BYPASSRLS, which is precisely what must not happen.
PRINCIPAL_SETTING: Final = "app.user_id"

#: Prefix of the per-tenant analytical login roles. Defined here as well as in
#: the data plane so the startup assertion below does not have to import a
#: bounded context (ADR-001).
TENANT_ROLE_PREFIX: Final = "eip_t_"

#: Tables that are deliberately global and therefore exempt from RLS.
#: This list is asserted against ``pg_policies`` by a test, so adding a
#: tenant-scoped table without a policy fails the build (ADR-003 Risks).
GLOBAL_TABLES: Final[frozenset[str]] = frozenset(
    {
        "tenant",  # the tenant registry itself
        "app_user",  # a user may belong to several tenants; membership is scoped
        "role",  # platform-shipped role catalog
        "role_capability",
        "alembic_version",
        # The audit checkpoint. Carries a tenant_id but is deliberately NOT
        # RLS-protected, for two reasons:
        #
        #  1. It is written only by a SECURITY DEFINER trigger running as the
        #     table owner. Under FORCE RLS that trigger would be blocked during
        #     a platform session, where no app.tenant_id is set — so audit
        #     events for privileged operations could not be recorded at all.
        #  2. It holds no tenant business data: a tenant id, a sequence number,
        #     a hash, and timestamps. Tenant ids are already visible to eip_app
        #     through the (global) `tenant` table, so this grants no knowledge
        #     that role did not already have.
        #
        # Write access is what matters here, and it is denied to every runtime
        # and platform role (migration 0002). The API never exposes this table.
        "audit_chain_head",
    }
)


class Base(DeclarativeBase):
    """Declarative base for all control-plane ORM models."""


@dataclass(frozen=True, slots=True)
class Engines:
    """The engine pair. Constructed once per process."""

    app: AsyncEngine
    platform: AsyncEngine


def _engine_options(settings: Settings) -> dict[str, Any]:
    return {
        "echo": settings.db_echo,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        # Statement cache is disabled because pgbouncer in transaction mode
        # (the deployment target) does not support prepared statements.
        "connect_args": {"statement_cache_size": 0, "server_settings": {}},
    }


def create_app_engine(settings: Settings) -> AsyncEngine:
    """Build **only** the constrained runtime engine.

    Used by processes that must be structurally incapable of opening a
    privileged connection — the worker, above all. A process that never
    constructs a platform engine cannot accidentally acquire BYPASSRLS through
    a configuration mistake, which is a stronger guarantee than simply not
    setting the environment variable (ADR-009; Phase 1A finding 3).
    """
    return create_async_engine(settings.db_app_dsn, **_engine_options(settings))


def create_engines(settings: Settings) -> Engines:
    """Build both engines. Only the API process should call this."""
    common = _engine_options(settings)
    return Engines(
        app=create_async_engine(settings.db_app_dsn, **common),
        platform=create_async_engine(settings.db_platform_dsn, **common),
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )


# --- startup invariants ------------------------------------------------------


async def assert_runtime_role_is_constrained(engine: AsyncEngine) -> None:
    """Fail fast unless the runtime role is genuinely subject to RLS.

    Checks three properties of ``current_user`` (ADR-003 Risks):

    * not a superuser — superusers bypass RLS unconditionally;
    * not ``BYPASSRLS`` — the attribute exists precisely to skip policies;
    * not the owner of the tenant-scoped tables — although ``FORCE ROW LEVEL
      SECURITY`` also covers the owner, relying on that alone is one migration
      away from being wrong.

    A process that fails this check must not serve traffic. Booting with a
    silently over-privileged role would make every isolation test meaningless
    while continuing to pass.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        rolname, is_super, bypasses_rls, inherits = row

        if inherits:
            msg = (
                f"Runtime database role {rolname!r} has INHERIT. It must hold no privilege "
                "through membership: analytical access uses each tenant's own login "
                "credential, and this role is a member of nothing. NOINHERIT keeps a "
                "mistakenly granted membership from silently conferring privileges "
                "(ADR-003 §2)."
            )
            raise ConfigurationError(msg)

        if is_super:
            msg = (
                f"Runtime database role {rolname!r} is a SUPERUSER. Superusers bypass "
                "Row-Level Security, so tenant isolation would not be enforced. "
                "Use the eip_app role (ADR-003)."
            )
            raise ConfigurationError(msg)

        if bypasses_rls:
            msg = (
                f"Runtime database role {rolname!r} has BYPASSRLS. Tenant isolation "
                "would not be enforced. BYPASSRLS belongs only to eip_platform, which "
                "is reached exclusively through platform_session() (ADR-003)."
            )
            raise ConfigurationError(msg)

        # `<> ALL(:globals)` rather than `NOT IN :globals`: asyncpg binds a
        # single parameter per placeholder, so an IN-list needs either an
        # expanding bindparam or array semantics. Array form is clearer here.
        owned = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = 'public' AND tableowner = current_user "
                    "AND tablename <> ALL(:globals)"
                ),
                {"globals": sorted(GLOBAL_TABLES)},
            )
        ).scalar_one()
        if owned:
            msg = (
                f"Runtime database role {rolname!r} owns {owned} tenant-scoped table(s). "
                "Table owners are exempt from RLS unless FORCE is set; the runtime role "
                "must not be the owner (ADR-003)."
            )
            raise ConfigurationError(msg)

        # The assertion that closes G10. Analytical isolation now rests on each
        # tenant holding its own login credential; if the runtime role were a
        # member of any tenant role, it could assume that tenant regardless of
        # what the application intended — which is exactly the capability this
        # design removed.
        assumable = (
            await conn.execute(
                text(
                    "SELECT count(*) "
                    "FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid = m.roleid "
                    "JOIN pg_roles grantee ON grantee.oid = m.member "
                    "WHERE grantee.rolname = current_user "
                    "AND r.rolname LIKE :prefix"
                ),
                {"prefix": f"{TENANT_ROLE_PREFIX}%"},
            )
        ).scalar_one()
        if assumable:
            msg = (
                f"Runtime database role {rolname!r} is a member of {assumable} per-tenant "
                "analytical role(s). It could therefore assume any of those tenants. "
                "Analytical access must use the tenant's own credential, never an assumed "
                "role (ADR-003 §2; Phase 1A finding G10)."
            )
            raise ConfigurationError(msg)

    _log.info("db.runtime_role_verified", role=rolname)


async def assert_rls_covers_tenant_tables(engine: AsyncEngine) -> None:
    """Verify every tenant-scoped table has FORCE RLS and a policy.

    A tenant-scoped table without a policy is a silent isolation hole. This
    runs at startup as well as in CI so a migration that forgets the policy
    cannot reach production.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "       (SELECT count(*) FROM pg_policies p "
                    "        WHERE p.schemaname = 'public' AND p.tablename = c.relname) "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            )
        ).all()

    problems: list[str] = []
    for name, rls_enabled, rls_forced, policy_count in rows:
        if name in GLOBAL_TABLES:
            continue
        if not rls_enabled:
            problems.append(f"{name}: ROW LEVEL SECURITY not enabled")
        elif not rls_forced:
            problems.append(f"{name}: FORCE ROW LEVEL SECURITY not set")
        elif policy_count == 0:
            problems.append(f"{name}: no RLS policy defined")

    if problems:
        msg = "Tenant isolation is not enforced on: " + "; ".join(problems) + " (ADR-003)"
        raise ConfigurationError(msg)

    _log.info("db.rls_verified", tables_checked=len(rows))


# --- session factories -------------------------------------------------------


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to ``context.tenant_id``.

    This is the only sanctioned way to read or write tenant data. It:

    1. opens an explicit transaction;
    2. sets ``app.tenant_id`` **transaction-locally** so every RLS policy in
       the schema resolves to this tenant, and the value cannot survive the
       connection returning to the pool;
    3. yields the session;
    4. commits, or rolls back on any exception.

    The tenant id is bound as a parameter, never interpolated, so it cannot
    contribute to injection even if a caller were to construct a context from
    an unexpected source.
    """
    async with factory() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{TENANT_SETTING}', :tenant_id, true)"),
            {"tenant_id": str(context.tenant_id)},
        )
        yield session


@asynccontextmanager
async def platform_session(
    factory: async_sessionmaker[AsyncSession],
    context: PlatformContext,
) -> AsyncIterator[AsyncSession]:
    """Open a **privileged**, cross-tenant transaction (ADR-003 §3).

    Bound to the ``eip_platform`` role, which carries ``BYPASSRLS``. Reserved
    for operations that legitimately span tenants: provisioning, platform
    reporting, support break-glass.

    Every use is logged here and must additionally emit an audit event at the
    call site. Requiring a ``PlatformContext`` — which cannot be constructed
    without a non-empty ``reason`` — makes unjustified privileged access
    impossible to write by accident.
    """
    _log.warning(
        "db.privileged_session_opened",
        actor=str(context.principal.user_id),
        reason=context.reason,
    )
    async with factory() as session, session.begin():
        yield session


@asynccontextmanager
async def principal_session(
    factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to a *user*, before a tenant is known.

    Used by exactly one caller: ``eip.identity.auth.resolve_context``, to
    discover which tenants the authenticated principal belongs to.

    It sets ``app.user_id`` but deliberately **not** ``app.tenant_id``. The
    only policy keyed on ``app.user_id`` is ``membership_self_select``, which
    is ``FOR SELECT`` on ``membership`` and matches ``user_id`` alone. So this
    session can read the caller's own membership rows and nothing else — every
    other tenant-scoped table still evaluates against an unset tenant and
    returns zero rows.

    This exists so that sign-in does *not* need the privileged BYPASSRLS role.
    Routing authentication through the privileged path would mean every login
    ran with row-level security disabled, which would defeat the model at its
    most-exercised entry point.
    """
    async with factory() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{PRINCIPAL_SETTING}', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        yield session


@asynccontextmanager
async def unscoped_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open a session with **no** tenant setting, on the constrained app role.

    Used only for genuinely global reads (the health probe, the role catalog,
    resolving a user by OIDC subject before a tenant is known).

    This is safe by construction rather than by convention: because
    ``app.tenant_id`` is unset, every RLS policy evaluates against NULL and
    every tenant-scoped table returns **zero rows**. It is a fail-closed
    session, not an escape hatch.
    """
    async with factory() as session, session.begin():
        yield session


async def current_tenant_setting(session: AsyncSession) -> uuid.UUID | None:
    """Return the tenant currently bound to this transaction, if any.

    Used by tests and by the readiness probe to prove the setting behaves as
    documented.
    """
    raw = (
        await session.execute(text(f"SELECT NULLIF(current_setting('{TENANT_SETTING}', true), '')"))
    ).scalar_one_or_none()
    return uuid.UUID(raw) if raw else None
