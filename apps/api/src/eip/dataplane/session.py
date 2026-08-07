"""Analytical session — the only path to a tenant's analytical data.

Lives in ``eip.dataplane`` rather than ``eip.platform`` because it depends on a
``DataPlaneHandle``, and ``eip.platform`` may not depend on a bounded context
(ADR-001; enforced by ``tests/architecture/test_module_boundaries.py``).

**This module contains the only ``SET ROLE`` in the codebase.** An architecture
test asserts that, because the isolation guarantee of the analytical plane rests
entirely on the role a transaction assumes.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.dataplane.interfaces import DataPlaneHandle
from eip.platform.context import TenantContext
from eip.platform.db import TENANT_SETTING
from eip.platform.errors import ConfigurationError
from eip.platform.logging import get_logger

_log = get_logger("dataplane.session")

_SAFE_ROLE: Final = re.compile(r"^[a-z_][a-z0-9_]*$")


@asynccontextmanager
async def analytical_session(
    factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
    handle: DataPlaneHandle,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction that assumes the tenant's **analytical role**.

    Isolation here is enforced by PostgreSQL privileges, not by our SQL. After
    the role switch, ``current_user`` is a role holding ``USAGE`` on exactly one
    schema, so a statement naming any other tenant's schema is refused with
    ``permission denied`` regardless of how that statement was constructed
    (ADR-003 §2). ``SET LOCAL`` is transaction-scoped, so the assumed role
    cannot survive the connection returning to the pool.

    ``app.tenant_id`` is set as well, so any control-plane table touched inside
    the same transaction stays RLS-scoped — defence in depth across both planes.

    The handle's tenant must match the context's. Both are constructed from the
    same authenticated ``TenantRef``; a mismatch means two different tenants
    were combined somewhere upstream, which is a programming error serious
    enough to refuse rather than reconcile.
    """
    if handle.tenant_id != context.tenant_id:
        msg = (
            "Refusing to open an analytical session: the data-plane handle belongs to "
            f"tenant {handle.tenant_id} but the request context is tenant "
            f"{context.tenant_id}."
        )
        raise ConfigurationError(msg)

    if not handle.role:
        msg = (
            f"Data-plane handle for tenant {handle.tenant_id} carries no analytical role. "
            "Isolation would rest on schema qualification alone, which is not enforcement "
            "(ADR-003 §2)."
        )
        raise ConfigurationError(msg)

    if not _SAFE_ROLE.match(handle.role):  # pragma: no cover - defence in depth
        msg = f"Unsafe analytical role name: {handle.role!r}"
        raise ConfigurationError(msg)

    async with factory() as session, session.begin():
        await session.execute(
            text(f"SELECT set_config('{TENANT_SETTING}', :tenant_id, true)"),
            {"tenant_id": str(context.tenant_id)},
        )
        # PostgreSQL accepts no bind parameter for a role name. The value is
        # regex-validated above and derived from a UUID in an authenticated
        # tenant record — never from request input.
        await session.execute(text(f'SET LOCAL ROLE "{handle.role}"'))
        yield session
