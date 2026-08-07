"""Schema-per-tenant data plane — the mode approved by ADR-003 §2.

ADR-003 §2 requires that "the analytical connection role is granted ``USAGE`` on
**only** the current tenant's schema". The first Phase 1A implementation granted
the single shared ``eip_app`` role cumulative ``USAGE`` on *every* provisioned
schema, which satisfied the letter of "granted USAGE" while destroying its
point: one role holding every tenant's privileges is not isolation, and no
amount of compiler discipline or schema qualification can recover it.

**The mechanism that actually enforces isolation**

Each tenant gets a ``NOLOGIN`` role — ``eip_t_<uuid>`` — holding ``USAGE`` on
exactly one schema: its own. ``eip_app`` is made a *member* of that role, which
grants only the ability to ``SET ROLE`` into it, **not** the privileges
themselves, because ``eip_app`` is created ``NOINHERIT``
(``infra/postgres/init/00-roles.sql``).

An analytical transaction therefore looks like::

    BEGIN;
    SET LOCAL ROLE eip_t_<uuid>;      -- current_user is now the tenant role
    SELECT ... FROM "tenant_<uuid>"."sem_revenue";
    COMMIT;                            -- role reverts with the transaction

After the ``SET LOCAL ROLE``, a query naming another tenant's schema is refused
by PostgreSQL itself with ``permission denied for schema`` — not by our code,
not by a ``WHERE`` clause, and not by whichever identifier the compiler happened
to emit. That is the property ``tests/security/test_analytical_isolation.py``
proves by deliberately issuing fully-qualified cross-tenant SQL.

``SET LOCAL`` is transaction-scoped, so a pooled connection cannot carry a
tenant role into the next checkout. That is tested too.

**Residual risk, stated plainly.** ``eip_app`` is a member of every tenant role,
so code that deliberately issued ``SET LOCAL ROLE`` for the wrong tenant would
reach that tenant's data. This is bounded three ways: the role name is derived
solely from a ``DataPlaneHandle`` built from an authenticated ``TenantContext``;
there is exactly one function in the codebase that issues ``SET LOCAL ROLE``
(asserted by an architecture test); and the failure requires an explicit,
greppable action rather than an omission. Eliminating it entirely means
per-tenant login credentials and per-tenant pools — ADR-003's Tier 2 — which
needs the ``SecretStore`` adapter that does not exist until Phase 2, and is
recorded there as the hardening path.
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from eip.dataplane.interfaces import (
    DataPlaneHandle,
    DataPlaneHealth,
    DataPlaneInfo,
    DataPlaneStatus,
    TenantRef,
)
from eip.platform.logging import get_logger
from eip.platform.settings import IsolationMode

_log = get_logger("dataplane.schema_per_tenant")

#: PostgreSQL identifiers are limited to 63 bytes.
_MAX_IDENTIFIER_LENGTH: Final = 63
_SAFE_IDENTIFIER: Final = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Prefix for the per-tenant analytical role. Distinct from the schema prefix so
#: a role can never be mistaken for a schema in a log line or an error message.
TENANT_ROLE_PREFIX: Final = "eip_t_"


class SchemaPerTenantDataPlane:
    """``TenantDataPlane`` over PostgreSQL schemas with per-tenant roles.

    Uses the ``eip_platform`` engine: provisioning is DDL plus role management,
    neither of which the constrained runtime role can perform (guardrail 17).
    """

    def __init__(
        self,
        *,
        platform_engine: AsyncEngine,
        schema_prefix: str,
        runtime_role: str = "eip_app",
    ) -> None:
        self._engine = platform_engine
        self._prefix = schema_prefix
        self._runtime_role = runtime_role
        if not _SAFE_IDENTIFIER.match(runtime_role):
            msg = f"Unsafe runtime role name: {runtime_role!r}"
            raise ValueError(msg)

    @property
    def mode(self) -> IsolationMode:
        return IsolationMode.SCHEMA_PER_TENANT

    # --- naming ----------------------------------------------------------

    def namespace_for(self, tenant: TenantRef) -> str:
        """Derive the tenant's schema name.

        Built from the tenant's UUID rather than its slug: a slug is
        user-supplied and mutable, and a renamed tenant must not orphan its
        data.
        """
        return self._identifier(self._prefix, tenant)

    def role_for(self, tenant: TenantRef) -> str:
        """Derive the tenant's analytical role name."""
        return self._identifier(TENANT_ROLE_PREFIX, tenant)

    def _identifier(self, prefix: str, tenant: TenantRef) -> str:
        """Build and validate an identifier.

        PostgreSQL has no bind parameters for identifiers, so DDL must
        interpolate. The defence is that the input is a UUID from an
        authenticated tenant record — never request input — plus an allowlist
        regex applied here, in one place, rather than at each call site.
        """
        suffix = str(tenant.tenant_id).replace("-", "_")
        name = f"{prefix}{suffix}"[:_MAX_IDENTIFIER_LENGTH]
        if not _SAFE_IDENTIFIER.match(name):
            msg = f"Derived identifier is not safe: {name!r}"
            raise ValueError(msg)
        return name

    # --- lifecycle -------------------------------------------------------

    async def provision(self, tenant: TenantRef) -> DataPlaneHandle:
        """Create the tenant's schema and its dedicated analytical role.

        Idempotent, so a retried provisioning job is safe (ADR-009).

        Note what is deliberately **absent**: no privilege of any kind is
        granted to ``eip_app`` on this schema. ``eip_app`` reaches the data only
        by assuming the tenant role, and only for the tenant whose handle it
        holds.
        """
        namespace = self.namespace_for(tenant)
        role = self.role_for(tenant)

        async with self._engine.begin() as conn:
            # The per-tenant role. NOLOGIN and passwordless: it has no
            # credential to steal and cannot open a connection of its own.
            #
            # S608 is suppressed on the statement below because PostgreSQL
            # accepts no bind parameter for a role name in DDL. `role` comes
            # from `_identifier`, which derives it from a tenant UUID and
            # validates it against `_SAFE_IDENTIFIER`; request input cannot
            # reach it.
            create_role = (
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f'CREATE ROLE "{role}" NOLOGIN NOINHERIT; '
                "END IF; END $$;"
            )
            await conn.execute(text(create_role))

            # The schema stays owned by eip_platform, which creates it. Handing
            # ownership to the tenant role would require eip_platform to be able
            # to SET ROLE into it, and ownership buys nothing here: isolation
            # comes from the grants below, not from who owns the namespace.
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{namespace}"'))

            # Exactly one schema, for exactly this role.
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{role}"'))
            await conn.execute(
                text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" TO "{role}"')
            )
            await conn.execute(
                text(
                    f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{namespace}" '
                    f'GRANT SELECT ON TABLES TO "{role}"'
                )
            )

            # Nothing else may reach in — including the runtime role directly.
            await conn.execute(text(f'REVOKE ALL ON SCHEMA "{namespace}" FROM PUBLIC'))
            await conn.execute(
                text(f'REVOKE ALL ON SCHEMA "{namespace}" FROM {self._runtime_role}')
            )

            # Membership grants the ability to SET ROLE and — because eip_app is
            # NOINHERIT — nothing more.
            await conn.execute(text(f'GRANT "{role}" TO {self._runtime_role}'))

        _log.info(
            "dataplane.provisioned",
            tenant_id=str(tenant.tenant_id),
            mode=self.mode.value,
        )
        return DataPlaneHandle(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=namespace,
            role=role,
        )

    async def deprovision(self, tenant: TenantRef) -> None:
        """Drop the tenant's schema and its role.

        One operation erases a tenant's analytical data and revokes the only
        means of reaching it — the offboarding and GDPR-erasure property
        ADR-003 siloed the data plane to obtain.
        """
        namespace = self.namespace_for(tenant)
        role = self.role_for(tenant)

        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
            # Dropping the schema first removes the role's grants and the
            # default-privilege entries that depend on it, so the role has no
            # remaining dependencies by the time it is dropped. `DROP OWNED BY`
            # is deliberately not used: it requires membership in the target
            # role, which eip_platform does not (and should not) hold.
            drop_role = (
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f'REVOKE "{role}" FROM {self._runtime_role}; '
                f'DROP ROLE "{role}"; '
                "END IF; END $$;"
            )
            await conn.execute(text(drop_role))
        _log.warning("dataplane.deprovisioned", tenant_id=str(tenant.tenant_id))

    async def handle(self, tenant: TenantRef) -> DataPlaneHandle:
        return DataPlaneHandle(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=self.namespace_for(tenant),
            role=self.role_for(tenant),
        )

    # --- introspection ---------------------------------------------------

    async def describe(self, tenant: TenantRef) -> DataPlaneInfo:
        namespace = self.namespace_for(tenant)
        async with self._engine.connect() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
                    {"name": namespace},
                )
            ).scalar_one_or_none()

        return DataPlaneInfo(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=namespace,
            status=DataPlaneStatus.READY if exists else DataPlaneStatus.UNPROVISIONED,
        )

    async def health(self, tenant: TenantRef) -> DataPlaneHealth:
        try:
            info = await self.describe(tenant)
        except Exception as exc:  # health must never raise
            return DataPlaneHealth(
                tenant_id=tenant.tenant_id, reachable=False, detail=type(exc).__name__
            )
        return DataPlaneHealth(
            tenant_id=tenant.tenant_id,
            reachable=info.status is DataPlaneStatus.READY,
            detail=info.status.value,
        )
