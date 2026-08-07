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

from eip.platform.logging import get_logger
from eip.platform.settings import Settings

_log = get_logger("worker.broker")


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
