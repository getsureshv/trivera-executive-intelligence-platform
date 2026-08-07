"""Worker entrypoint: ``python -m eip_worker``.

Phase 1A scope is deliberately narrow (ADR-009): prove the worker starts, can
reach PostgreSQL and the broker, runs the transactional outbox relay, and
reports health. **No ingestion pipelines** — those belong to Phase 2.

Two loops run concurrently:

* the **outbox relay**, the durable Postgres-backed publication path;
* a **health server**, so the worker is observable through the same probe
  mechanism as the API rather than being a black box.

The worker performs the same startup isolation assertions as the API. A worker
able to see every tenant's rows would be a hole in the model no matter how
careful the API is — which is why background database access is called out
explicitly as a Phase 1A acceptance criterion.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# Registers every ORM mapper. The relay writes audit events, and AuditEvent's
# foreign key cannot resolve unless the identity models are loaded too.
import eip.models  # noqa: F401
from eip.platform.db import (
    assert_rls_covers_tenant_tables,
    assert_runtime_role_is_constrained,
    create_app_engine,
    create_session_factory,
)
from eip.platform.logging import configure_logging, get_logger
from eip.platform.settings import Settings, get_settings
from eip.platform.telemetry import configure_telemetry
from eip_worker.broker import check_broker
from eip_worker.outbox import relay_once

_log = get_logger("worker.main")


class Worker:
    """Owns the worker's lifecycle and shared resources."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # `create_app_engine`, not `create_engines`: the worker never builds a
        # platform engine, so it is structurally incapable of opening a
        # BYPASSRLS connection (ADR-009; Phase 1A finding 3). Tenant
        # enumeration goes through eip_outbox_pending_tenants(), a SECURITY
        # DEFINER function returning only identifiers.
        self._engine = create_app_engine(settings)
        self._app_factory = create_session_factory(self._engine)
        self._stopping = asyncio.Event()
        self._db_ready = False
        self._broker_ready = False

    @property
    def ready(self) -> bool:
        return self._db_ready and self._broker_ready

    async def verify_invariants(self) -> None:
        """Refuse to start unless tenant isolation is genuinely enforced.

        Identical assertions to the API's startup. Duplicating them here is
        deliberate: the worker connects with its own engine and could, through
        a configuration mistake, be handed a more privileged role than the API.
        """
        await assert_runtime_role_is_constrained(self._engine)
        await assert_rls_covers_tenant_tables(self._engine)
        self._db_ready = True

        self._broker_ready = await check_broker(self._settings)
        if not self._broker_ready:
            # Not fatal: the outbox is durable, so work accumulates safely in
            # PostgreSQL until the broker returns. Readiness reflects it.
            _log.warning("worker.broker_unavailable", url_scheme=self._settings.redis_url[:8])

        _log.info("worker.invariants_verified", broker_ready=self._broker_ready)

    async def run_relay(self) -> None:
        """Poll the outbox until asked to stop."""
        interval = self._settings.outbox_poll_interval_seconds
        batch = self._settings.outbox_batch_size

        while not self._stopping.is_set():
            try:
                published = await relay_once(self._app_factory, batch_size=batch)
            except Exception:
                _log.exception("worker.relay_failed")
                self._db_ready = False
            else:
                self._db_ready = True
                if published:
                    _log.info("worker.relay_pass", published=published)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)

    def stop(self) -> None:
        self._stopping.set()

    async def close(self) -> None:
        await self._engine.dispose()


def build_health_app(worker: Worker) -> Starlette:
    """Health and readiness endpoints mirroring the API's semantics."""

    async def health(_request: Request) -> JSONResponse:
        """Liveness. Touches no dependency."""
        return JSONResponse({"status": "ok", "service": "eip-worker"})

    async def ready(_request: Request) -> JSONResponse:
        """Readiness. Reflects database and broker reachability."""
        if worker.ready:
            return JSONResponse({"status": "ready", "service": "eip-worker"})
        return JSONResponse({"status": "not_ready", "service": "eip-worker"}, status_code=503)

    return Starlette(
        routes=[Route("/health", health), Route("/ready", ready)],
    )


async def run() -> None:
    settings = get_settings()
    configure_logging(settings)
    configure_telemetry(settings)

    worker = Worker(settings)
    await worker.verify_invariants()

    config = uvicorn.Config(
        build_health_app(worker),
        host="0.0.0.0",  # noqa: S104 - containers bind all interfaces
        port=settings.worker_health_port,
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    health_task = asyncio.create_task(server.serve())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # unavailable on Windows
            loop.add_signal_handler(sig, worker.stop)

    _log.info(
        "worker.started",
        health_port=settings.worker_health_port,
        poll_interval=settings.outbox_poll_interval_seconds,
    )

    try:
        await worker.run_relay()
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(health_task, timeout=5)
        await worker.close()
        _log.info("worker.stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        _log.info("worker.interrupted")


if __name__ == "__main__":
    main()
