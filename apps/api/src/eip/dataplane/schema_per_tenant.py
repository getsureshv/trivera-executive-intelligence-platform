"""Schema-per-tenant data plane — the mode approved by ADR-003 §2.

History matters for reading this file, because it has been wrong twice in
different ways.

**First implementation.** ``provision()`` granted the single shared ``eip_app``
role ``USAGE`` on every tenant schema. One credential reached every tenant's
data; isolation existed only in application constructs the database does not
enforce.

**Second implementation (finding G10).** Per-tenant ``NOLOGIN`` roles, with
``eip_app`` made a *member* of each so it could ``SET ROLE`` into one per
transaction. PostgreSQL did enforce the boundary once the switch had happened —
but the switch was a choice. Code that assumed the wrong tenant's role would
have been obeyed, and ``eip_app`` remained a credential that could reach every
tenant.

**This implementation.** Each tenant has its own **login** role with its own
password, held in the ``SecretStore``. ``eip_app`` has no privilege on any
tenant schema and is a member of no tenant role. There is nothing to switch.

    A connection authenticated as tenant A's role holds ``USAGE`` on exactly
    one schema and belongs to no other role. Naming tenant B is refused by
    PostgreSQL because the connection *is not* tenant B and has no means of
    becoming tenant B.

That is the difference between isolation the application chooses and isolation
the database imposes. A coding error that names tenant B while processing
tenant A now produces ``permission denied``, not data.

Passwords are generated at provisioning, handed straight to the ``SecretStore``,
and never returned. The tenant row stores a ``SecretRef`` — a pointer and a
version — so a dump of the metadata database contains no credential material
(ADR-015).
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from eip.dataplane.credentials import (
    AnalyticalCredential,
    AnalyticalCredentialProvider,
    generate_password,
)
from eip.dataplane.interfaces import (
    DataPlaneHandle,
    DataPlaneHealth,
    DataPlaneInfo,
    DataPlaneStatus,
    TenantRef,
)
from eip.platform.logging import get_logger
from eip.platform.secrets import SecretRef
from eip.platform.settings import IsolationMode

_log = get_logger("dataplane.schema_per_tenant")

#: PostgreSQL identifiers are limited to 63 bytes.
_MAX_IDENTIFIER_LENGTH: Final = 63
_SAFE_IDENTIFIER: Final = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Prefix for the per-tenant analytical login role. Distinct from the schema
#: prefix so a role can never be mistaken for a schema in a log line.
TENANT_ROLE_PREFIX: Final = "eip_t_"


class SchemaPerTenantDataPlane:
    """``TenantDataPlane`` over PostgreSQL schemas with per-tenant login roles.

    Uses the ``eip_platform`` engine: provisioning is DDL plus role management,
    neither of which the constrained runtime role can perform (guardrail 17).
    """

    def __init__(
        self,
        *,
        platform_engine: AsyncEngine,
        schema_prefix: str,
        credentials: AnalyticalCredentialProvider,
        runtime_role: str = "eip_app",
    ) -> None:
        self._engine = platform_engine
        self._prefix = schema_prefix
        self._credentials = credentials
        # Retained solely so provisioning can assert that this role holds
        # nothing on the schema it just created. It is never granted anything.
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
        """Derive the tenant's analytical login role name."""
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
        """Create the tenant's schema and its dedicated login role.

        Idempotent, so a retried provisioning job is safe (ADR-009). Re-running
        rotates the password, which is harmless: the pool registry is keyed on
        the tenant and rebuilds on the next request.

        Note what is deliberately **absent**: no grant to ``eip_app``, and no
        ``GRANT <tenant role> TO <anyone>``. Nothing but the tenant's own
        credential can reach the schema.
        """
        namespace = self.namespace_for(tenant)
        role = self.role_for(tenant)

        password = generate_password()
        secret_ref = await self._credentials.store_new_password(tenant.tenant_id, password)

        async with self._engine.begin() as conn:
            # A LOGIN role with its own password. NOINHERIT because it should
            # never acquire anything through membership — it has none, and if
            # one were ever granted, this limits the damage.
            create_role = (
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f'CREATE ROLE "{role}" LOGIN NOINHERIT NOCREATEDB NOCREATEROLE '
                f"NOSUPERUSER NOBYPASSRLS PASSWORD '{_escape_literal(password.reveal())}'; "
                "ELSE "
                f"ALTER ROLE \"{role}\" PASSWORD '{_escape_literal(password.reveal())}'; "
                "END IF; END $$;"
            )
            await conn.execute(text(create_role))

            # The schema stays owned by eip_platform, which creates it. Handing
            # ownership to the tenant role would require eip_platform to be able
            # to SET ROLE into it — precisely the membership this design
            # removes — and ownership buys nothing: isolation comes from the
            # grants below.
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

            # Nothing else may reach in — including the runtime role. The
            # REVOKE is belt-and-braces: no grant was issued, and none will be.
            await conn.execute(text(f'REVOKE ALL ON SCHEMA "{namespace}" FROM PUBLIC'))
            await conn.execute(
                text(f'REVOKE ALL ON SCHEMA "{namespace}" FROM {self._runtime_role}')
            )

        _log.info(
            "dataplane.provisioned",
            tenant_id=str(tenant.tenant_id),
            mode=self.mode.value,
            # The role name is an identifier, not a credential. The password is
            # never referenced here in any form.
            role=role,
        )
        return DataPlaneHandle(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=namespace,
            role=role,
            secret_ref=secret_ref,
        )

    async def deprovision(self, tenant: TenantRef, secret_ref: SecretRef | None = None) -> None:
        """Drop the tenant's schema, its role, and its stored credential.

        Three things must go together. Dropping the schema without the role
        leaves a login that can still authenticate; dropping the role without
        the secret leaves a credential for an identity that no longer exists.
        """
        namespace = self.namespace_for(tenant)
        role = self.role_for(tenant)

        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
            # Dropping the schema first removes the role's grants and the
            # default-privilege entries that depend on it, so the role has no
            # remaining dependencies. `DROP OWNED BY` is deliberately not used:
            # it requires membership in the target role, which eip_platform does
            # not — and must not — hold.
            drop_role = (
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f'DROP ROLE "{role}"; '
                "END IF; END $$;"
            )
            await conn.execute(text(drop_role))

        if secret_ref is not None:
            await self._credentials.forget(
                AnalyticalCredential(tenant_id=tenant.tenant_id, role=role, secret_ref=secret_ref)
            )

        _log.warning("dataplane.deprovisioned", tenant_id=str(tenant.tenant_id))

    async def rotate_credential(self, tenant: TenantRef, secret_ref: SecretRef) -> DataPlaneHandle:
        """Issue a new password for the tenant's role.

        The caller must evict the tenant's pool afterwards, or a warm pool would
        keep using the superseded password until it recycled.
        """
        role = self.role_for(tenant)
        password = generate_password()

        async with self._engine.begin() as conn:
            await conn.execute(
                text(f"ALTER ROLE \"{role}\" PASSWORD '{_escape_literal(password.reveal())}'")
            )

        rotated = await self._credentials.rotate_password(
            AnalyticalCredential(tenant_id=tenant.tenant_id, role=role, secret_ref=secret_ref),
            password,
        )
        return DataPlaneHandle(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=self.namespace_for(tenant),
            role=role,
            secret_ref=rotated,
        )

    async def handle(
        self, tenant: TenantRef, secret_ref: SecretRef | None = None
    ) -> DataPlaneHandle:
        return DataPlaneHandle(
            tenant_id=tenant.tenant_id,
            mode=self.mode,
            namespace=self.namespace_for(tenant),
            role=self.role_for(tenant),
            secret_ref=secret_ref,
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


def _escape_literal(value: str) -> str:
    """Escape a value for a single-quoted SQL literal.

    ``CREATE ROLE ... PASSWORD`` takes no bind parameter, so the password must be
    interpolated. It is generated by ``secrets.token_urlsafe`` — a fixed
    alphabet with no quotes — but doubling any quote is cheap and removes the
    need for the reader to verify that claim about a value they cannot see.
    """
    return value.replace("'", "''")
