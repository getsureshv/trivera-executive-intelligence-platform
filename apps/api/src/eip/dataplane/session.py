"""Analytical session — the only path to a tenant's analytical data.

**There is no ``SET ROLE`` here, and none anywhere in the codebase.** That
mechanism is gone, and an architecture test asserts it stays gone.

The previous design gave every process one shared credential that was a member
of every per-tenant role and switched into one of them per transaction. It was
enforced by PostgreSQL *once the switch had happened*, but the switch was a
choice the application made. Code that named the wrong tenant would have been
obeyed. That was finding G10.

Now a tenant's analytical session comes from a pool authenticated as **that
tenant's own database role, with that tenant's own password**. The connection
has no privilege on any other schema and no role it could assume. A statement
naming tenant B while processing tenant A is refused by PostgreSQL because the
connection is not tenant B and has no way to become tenant B — not because a
guard noticed.

The handle/context match check below is retained, but its role has changed:
before, it was the thing standing between a coding error and a cross-tenant
read. Now it is an early, legible failure for a mistake the database would
refuse anyway.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from eip.dataplane.credentials import AnalyticalCredential
from eip.dataplane.interfaces import DataPlaneHandle
from eip.dataplane.pool import TenantPoolRegistry
from eip.platform.context import TenantContext
from eip.platform.errors import ConfigurationError
from eip.platform.logging import get_logger

_log = get_logger("dataplane.session")


@asynccontextmanager
async def analytical_session(
    registry: TenantPoolRegistry,
    context: TenantContext,
    handle: DataPlaneHandle,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction on the tenant's **own** analytical connection.

    Isolation is a property of the credential, not of the statement. There is
    nothing to set, nothing to reset, and nothing that could leak across a
    pooled checkout: the connection can only ever authenticate as one tenant.

    The handle must match the request context. Both derive from the same
    authenticated ``TenantRef``; a mismatch means two tenants were combined
    upstream, which is a programming error worth refusing rather than
    reconciling — even though the database would refuse it too.
    """
    if handle.tenant_id != context.tenant_id:
        msg = (
            "Refusing to open an analytical session: the data-plane handle belongs to "
            f"tenant {handle.tenant_id} but the request context is tenant "
            f"{context.tenant_id}."
        )
        raise ConfigurationError(msg)

    if not handle.role or handle.secret_ref is None:
        msg = (
            f"Data-plane handle for tenant {handle.tenant_id} carries no analytical "
            "credential. Isolation would rest on schema qualification alone, which is "
            "not enforcement (ADR-003 §2)."
        )
        raise ConfigurationError(msg)

    credential = AnalyticalCredential(
        tenant_id=handle.tenant_id,
        role=handle.role,
        secret_ref=handle.secret_ref,
    )
    sessions = await registry.sessions_for(credential)

    async with sessions() as session, session.begin():
        yield session
