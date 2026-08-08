"""Per-tenant analytical connection pools (ADR-003 §2).

One pool per tenant, each authenticated as that tenant's own database role. A
connection in tenant A's pool *is* tenant A — there is no role to switch, so
returning it to the pool and handing it to the next caller cannot change which
tenant it can reach. That is the property the previous shared-pool design could
not have: a pooled connection there was a general-purpose credential that
happened to have assumed a role.

Two operational concerns come with per-tenant pools, and both are handled here
rather than left to discover under load:

**Bounded.** A pool per tenant would otherwise mean unbounded connections as
tenants are added. The registry keeps at most ``max_tenants`` pools and evicts
the least-recently-used one when full. PostgreSQL's ``max_connections`` is a
hard cluster limit, and exhausting it takes down every tenant at once — a
noisy-neighbour failure of the worst kind.

**Evicted.** Idle pools are disposed after a TTL, so a tenant that was active
once does not hold connections forever.

Eviction is safe because a pool is a cache, not state: disposing it closes idle
connections and the next request rebuilds it. Checked-out connections are
unaffected; ``dispose()`` does not interrupt work in flight.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from eip.dataplane.credentials import AnalyticalCredential, AnalyticalCredentialProvider
from eip.platform.logging import get_logger

_log = get_logger("dataplane.pool")


@dataclass(slots=True)
class _Entry:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    last_used: float


class TenantPoolRegistry:
    """Bounded, LRU-evicting registry of per-tenant analytical pools.

    Not a general connection cache: the key is the tenant, and the value is a
    pool that can only ever reach that tenant. Losing an entry costs a
    reconnection; it can never cost isolation.
    """

    def __init__(
        self,
        *,
        credentials: AnalyticalCredentialProvider,
        max_tenants: int,
        pool_size: int,
        max_overflow: int,
        idle_ttl_seconds: float,
    ) -> None:
        self._credentials = credentials
        self._max_tenants = max_tenants
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._idle_ttl = idle_ttl_seconds
        self._entries: OrderedDict[uuid.UUID, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._entries)

    def tracked_tenants(self) -> list[uuid.UUID]:
        """Tenants that currently hold a pool. For tests and diagnostics."""
        return list(self._entries.keys())

    async def sessions_for(
        self, credential: AnalyticalCredential
    ) -> async_sessionmaker[AsyncSession]:
        """Return the session factory bound to this tenant's own credential.

        Callers never receive an engine or a URL — only a factory. There is no
        supported way to obtain a connection for a tenant other than by holding
        that tenant's ``AnalyticalCredential``.
        """
        async with self._lock:
            await self._evict_idle_locked()

            entry = self._entries.get(credential.tenant_id)
            if entry is not None:
                entry.last_used = time.monotonic()
                self._entries.move_to_end(credential.tenant_id)
                return entry.sessions

            await self._make_room_locked()

            # The URL is built here and immediately handed to the engine. It is
            # never returned, logged, or stored; SQLAlchemy masks the password
            # in the engine's repr.
            url = await self._credentials.url_for(credential)
            engine = create_async_engine(
                url,
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_pre_ping=True,
                # Never echo: statements would carry tenant data, and the
                # connection banner would carry the role.
                echo=False,
                connect_args={"statement_cache_size": 0, "server_settings": {}},
            )
            entry = _Entry(
                engine=engine,
                sessions=async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False),
                last_used=time.monotonic(),
            )
            self._entries[credential.tenant_id] = entry
            _log.info(
                "pool.opened",
                tenant_id=str(credential.tenant_id),
                pools_open=len(self._entries),
            )
            return entry.sessions

    async def _make_room_locked(self) -> None:
        """Evict the least-recently-used pool if the registry is full."""
        while len(self._entries) >= self._max_tenants:
            tenant_id, entry = self._entries.popitem(last=False)
            await entry.engine.dispose()
            _log.info("pool.evicted", tenant_id=str(tenant_id), reason="capacity")

    async def _evict_idle_locked(self) -> None:
        now = time.monotonic()
        stale = [
            tenant_id
            for tenant_id, entry in self._entries.items()
            if (now - entry.last_used) > self._idle_ttl
        ]
        for tenant_id in stale:
            entry = self._entries.pop(tenant_id)
            await entry.engine.dispose()
            _log.info("pool.evicted", tenant_id=str(tenant_id), reason="idle")

    async def evict(self, tenant_id: uuid.UUID) -> None:
        """Drop a tenant's pool immediately.

        Called on deprovisioning and on credential rotation, so a revoked or
        superseded password cannot keep working from a warm pool.
        """
        async with self._lock:
            entry = self._entries.pop(tenant_id, None)
        if entry is not None:
            await entry.engine.dispose()
            _log.info("pool.evicted", tenant_id=str(tenant_id), reason="explicit")

    async def close(self) -> None:
        """Dispose every pool. Called at process shutdown."""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await entry.engine.dispose()
        _log.info("pool.closed_all", disposed=len(entries))
