"""Fast worker-envelope and dispatch tests."""

from __future__ import annotations

import uuid
from threading import Event

import pytest

from eip.platform.settings import Settings
from eip_worker import broker as broker_module
from eip_worker.broker import (
    enqueue_connection_test,
    start_interactive_consumer,
    stop_interactive_consumer,
)
from eip_worker.connection_tests import ConnectionTestEnvelope


def _payload() -> dict[str, object]:
    return {
        "job_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "source_id": str(uuid.uuid4()),
        "source_version": 1,
        "actor_id": str(uuid.uuid4()),
        "trace_id": "trace-safe",
        "idempotency_key": "request-safe",
        "attempt": 1,
    }


def test_envelope_requires_exact_closed_fields() -> None:
    payload = _payload()
    envelope = ConnectionTestEnvelope.parse(payload)
    assert envelope.source_version == envelope.attempt == 1
    payload["endpoint"] = "must-not-be-carried"
    with pytest.raises(ValueError, match="Malformed"):
        ConnectionTestEnvelope.parse(payload)


@pytest.mark.parametrize("field", ["tenant_id", "job_id", "source_id", "actor_id"])
def test_envelope_rejects_missing_or_invalid_identifiers(field: str) -> None:
    payload = _payload()
    payload[field] = ""
    with pytest.raises(ValueError, match="Malformed"):
        ConnectionTestEnvelope.parse(payload)


def test_real_redis_consumer_executes_registered_interactive_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release gate: Redis delivery reaches the worker, not merely its queue."""
    consumed = Event()
    received: list[dict[str, object]] = []

    async def fake_execute(payload: dict[str, object]) -> str:
        received.append(payload)
        consumed.set()
        return "succeeded"

    broker_module._connection_actor.cache_clear()
    monkeypatch.setattr(broker_module, "_execute_default", fake_execute)
    settings = Settings()
    worker = start_interactive_consumer(settings)
    try:
        payload = _payload()
        enqueue_connection_test(settings, payload)
        assert consumed.wait(timeout=10), "Redis message was not consumed"
        assert received == [payload]
    finally:
        stop_interactive_consumer(worker)
        broker_module._connection_actor.cache_clear()
