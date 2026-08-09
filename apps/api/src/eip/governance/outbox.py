"""Publishing to the transactional outbox (ADR-009 §3).

One function, and the whole point is its signature: it takes the **caller's**
session and writes inside the caller's transaction. A message and the state
change that justifies it commit together or not at all.

The two failures this removes are both ordinary and both nasty:

* the transaction rolled back but the job already ran;
* the job was never enqueued because the broker blipped at the wrong moment.

In a governance product either one is a correctness defect, not an operational
annoyance — an audit trail recording a provisioning that never happened is
worse than no audit trail, because it will be believed.

Payloads pass through :func:`redact` for the same reason audit details do.
Nothing here should ever carry a credential, and "should" is not a control.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from eip.governance.models import OutboxMessage
from eip.platform.logging import get_logger, redact

_log = get_logger("governance.outbox")


class Topic:
    """Published topics.

    A closed set for the same reason ``AuditAction`` is one: subscribers are
    written against these strings, and a typo silently delivers to nobody.
    """

    TENANT_REGISTERED = "tenant.registered"
    TENANT_PROVISIONED = "tenant.provisioned"
    TENANT_PROVISIONING_FAILED = "tenant.provisioning_failed"
    CONNECTION_TEST_REQUESTED = "connection_test.requested"
    CONNECTION_TEST_COMPLETED = "connection_test.completed"
    CONNECTION_TEST_FAILED = "connection_test.failed"


async def publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    topic: str,
    payload: dict[str, Any],
    trace_id: str,
) -> uuid.UUID:
    """Write an outbox message in the caller's transaction.

    Returns the message id so a caller can assert on it; the worker's relay is
    what actually publishes it.
    """
    message = OutboxMessage(
        tenant_id=tenant_id,
        topic=topic,
        payload=redact(payload),
        trace_id=trace_id,
    )
    session.add(message)
    await session.flush()

    _log.info("outbox.queued", topic=topic, tenant_id=str(tenant_id))
    return message.id
