"""Dramatiq broker seam (ADR-009 §2).

Phase 1A wires the broker and proves connectivity; it registers **no real
actors**, because there is no real background work yet and inventing some would
be scope creep.

The reason this is a thin file rather than an important one is the substance of
ADR-009: pipeline state lives in *our* PostgreSQL tables, because run history is
product data — freshness badges, provenance, ingestion audit all read it. That
makes the broker responsible only for "run this step soon", and therefore a
small, replaceable component. Postgres-as-broker (``SKIP LOCKED``) is the
sanctioned fallback and the outbox relay already demonstrates the technique.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from eip.platform.logging import get_logger
from eip.platform.settings import Settings

_log = get_logger("worker.broker")


@lru_cache(maxsize=1)
def _connection_actor(redis_url: str) -> Any:
    """Create the interactive Dramatiq actor after configuring its Redis broker."""
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(url=redis_url)  # type: ignore[no-untyped-call]
    dramatiq.set_broker(broker)

    @dramatiq.actor(actor_name="connection_test", queue_name="interactive", max_retries=3)
    def connection_test(payload: dict[str, Any]) -> None:
        result = asyncio.run(_execute_default(payload))
        if result == "deferred":
            raise dramatiq.Retry(message="tenant concurrency or active lease", delay=1000)

    return connection_test


async def _execute_default(payload: dict[str, Any]) -> str:
    from eip.platform.db import create_app_engine, create_session_factory
    from eip.platform.secretstore import build_secret_store
    from eip_worker.connection_tests import execute_connection_test

    settings = Settings()
    engine = create_app_engine(settings)
    try:
        return await execute_connection_test(
            create_session_factory(engine), settings, build_secret_store(settings), payload
        )
    finally:
        await engine.dispose()


def enqueue_connection_test(settings: Settings, payload: dict[str, Any]) -> None:
    """Publish one validated identifier-only envelope to the interactive queue."""
    _connection_actor(settings.redis_url).send(payload)


def start_interactive_consumer(settings: Settings) -> Any:
    """Start a managed in-process consumer on the same registered actor/broker."""
    import dramatiq

    actor = _connection_actor(settings.redis_url)
    worker = dramatiq.Worker(actor.broker, queues={"interactive"}, worker_threads=2)
    worker.start()
    return worker


def stop_interactive_consumer(worker: Any) -> None:
    worker.stop(timeout=5000)
    worker.join()


async def check_broker(settings: Settings) -> bool:
    """Report whether the broker is reachable.

    Deliberately non-fatal. The outbox is durable, so if Redis is down work
    accumulates safely in PostgreSQL and is published when it returns. The
    worker stays *live* but reports *not ready*, which is exactly the
    distinction the two probes exist to express (ADR-014 §8).
    """
    try:
        import redis.asyncio as redis
    except ImportError:  # pragma: no cover - dramatiq[redis] provides it
        _log.warning("broker.client_unavailable")
        return False

    client = redis.from_url(settings.redis_url)
    try:
        await client.ping()
    except Exception as exc:
        _log.warning("broker.unreachable", error_type=type(exc).__name__)
        return False
    else:
        return True
    finally:
        await client.aclose()
