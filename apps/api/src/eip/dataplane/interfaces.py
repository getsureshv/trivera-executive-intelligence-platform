"""The ``TenantDataPlane`` port.

ADR-003 chose schema-per-tenant for the analytical plane. This port exists so
that the *choice* stays a deployment concern rather than an assumption baked
into business logic.

The rule for every caller: **ask the plane for a handle; never construct a
schema name, never interpolate a tenant into SQL, never assume the tenant's
data is reachable from the current connection.** A module that does any of
those has hard-wired one isolation mode and will have to be rewritten when a
customer requires another — which ADR-003 anticipates as Tier 2 and Tier 3.

Four modes are *declared* so the abstraction is shaped by all of them:

``SHARED_RLS``            one schema, rows separated by ``tenant_id`` + RLS.
``SCHEMA_PER_TENANT``     one schema per tenant. **Implemented (ADR-003 §2).**
``DATABASE_PER_TENANT``   a dedicated database or catalog per tenant (Tier 2).
``DEDICATED_DEPLOYMENT``  an entire stack per tenant (Tier 3).

Only ``SCHEMA_PER_TENANT`` is implemented. Requesting another raises
``NotImplementedModeError`` — loudly. Silently degrading to a weaker isolation
mode would be the single worst failure this subsystem could have.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, final, runtime_checkable

from eip.platform.secrets import SecretRef
from eip.platform.settings import IsolationMode


class DataPlaneStatus(StrEnum):
    UNPROVISIONED = "unprovisioned"
    PROVISIONING = "provisioning"
    READY = "ready"
    DEGRADED = "degraded"
    OFFBOARDING = "offboarding"


@final
@dataclass(frozen=True, slots=True)
class TenantRef:
    """The minimum identity a data plane needs.

    Deliberately not the ORM ``Tenant``: the data plane must not depend on the
    control plane's model, and ``slug`` is carried because some backends want a
    human-readable namespace while others want only the id.
    """

    tenant_id: uuid.UUID
    slug: str


@final
@dataclass(frozen=True, slots=True)
class DataPlaneHandle:
    """An opaque handle to a tenant's analytical storage.

    ``qualify()`` is the *only* sanctioned way to name a physical object.
    Callers pass a logical object name and receive a backend-correct,
    safely-quoted identifier. Under a future database-per-tenant mode the same
    call would return an unqualified name against a different connection, and
    no caller would change.

    A handle is also the **only** carrier of the database role an analytical
    transaction may assume. It is constructed from a ``TenantRef`` that came
    from an authenticated membership, so there is no path from request input to
    a role name (ADR-003).
    """

    tenant_id: uuid.UUID
    mode: IsolationMode
    #: Backend-specific location: a schema name today; a database or catalog
    #: under other modes. Callers must treat this as opaque and use qualify().
    namespace: str
    #: The tenant's own database **login** role. It holds privileges on
    #: ``namespace`` and on nothing else, and no other role is a member of it.
    #: Connections authenticate *as* this role; nothing assumes it.
    role: str = ""
    #: Pointer to that role's password in the SecretStore. A reference, never a
    #: value — so a handle can be logged, cached, or passed through a job
    #: payload without disclosing a credential (ADR-015).
    secret_ref: SecretRef | None = None

    def qualify(self, object_name: str) -> str:
        """Return a fully-qualified, quoted identifier for ``object_name``."""
        if not object_name.isidentifier():
            msg = f"Unsafe analytical object name: {object_name!r}"
            raise ValueError(msg)
        if self.mode is IsolationMode.SCHEMA_PER_TENANT:
            return f'"{self.namespace}"."{object_name}"'
        if self.mode is IsolationMode.SHARED_RLS:
            return f'"{object_name}"'
        # Database/catalog-per-tenant reach their namespace via the connection.
        return f'"{object_name}"'


@final
@dataclass(frozen=True, slots=True)
class DataPlaneInfo:
    tenant_id: uuid.UUID
    mode: IsolationMode
    namespace: str
    status: DataPlaneStatus


@final
@dataclass(frozen=True, slots=True)
class DataPlaneHealth:
    tenant_id: uuid.UUID
    reachable: bool
    detail: str = ""


@runtime_checkable
class TenantDataPlane(Protocol):
    """Provisioning and access to a tenant's analytical storage.

    Kept intentionally small. Phase 1A needs lifecycle and health only; the
    query surface arrives with the governed query engine (ADR-007), which will
    take a ``DataPlaneHandle`` rather than a connection string.
    """

    @property
    def mode(self) -> IsolationMode:
        """The isolation mode this implementation provides."""
        ...

    async def provision(self, tenant: TenantRef) -> DataPlaneHandle:
        """Create the tenant's storage. Must be idempotent."""
        ...

    async def deprovision(self, tenant: TenantRef) -> None:
        """Destroy the tenant's storage.

        Under schema-per-tenant this is a single ``DROP SCHEMA ... CASCADE`` —
        which is precisely why ADR-003 silos the data plane: tenant offboarding
        and GDPR erasure become one verifiable operation instead of a hunt
        across shared tables.
        """
        ...

    async def handle(self, tenant: TenantRef) -> DataPlaneHandle:
        """Return a handle for an already-provisioned tenant."""
        ...

    async def describe(self, tenant: TenantRef) -> DataPlaneInfo: ...

    async def health(self, tenant: TenantRef) -> DataPlaneHealth: ...
