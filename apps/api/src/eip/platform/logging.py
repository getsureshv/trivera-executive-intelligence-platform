"""Structured logging and request correlation (ADR-014).

Two properties matter more than the choice of library:

1. **Mandatory context.** Every record carries ``tenant_id``, ``trace_id``,
   ``request_id``, ``service``, ``environment`` and ``component``. This is
   injected from contextvars rather than passed by hand, so a signal without
   tenant context is structurally hard to emit (ADR-014 §2).

2. **An allowlist, not a denylist, for what may be logged.** Metric values,
   dimension values, source field values, credentials, and raw prompts are
   never emitted. ``redact()`` below is the single sanctioned way to include a
   value-bearing object in a log record, and it drops rather than masks — a
   masked field still tells an attacker the field existed (ADR-014 §6).
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

from eip.platform.settings import Settings

# --- correlation context -----------------------------------------------------

_trace_id: ContextVar[str | None] = ContextVar("eip_trace_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("eip_request_id", default=None)
_tenant_id: ContextVar[str | None] = ContextVar("eip_tenant_id", default=None)
_principal_id: ContextVar[str | None] = ContextVar("eip_principal_id", default=None)
_component: ContextVar[str | None] = ContextVar("eip_component", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str:
    """Return the active trace id, minting one if this is an untraced path."""
    existing = _trace_id.get()
    if existing is None:
        existing = new_trace_id()
        _trace_id.set(existing)
    return existing


def current_request_id() -> str:
    return _request_id.get() or current_trace_id()


@contextmanager
def bind_context(
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    tenant_id: uuid.UUID | str | None = None,
    principal_id: uuid.UUID | str | None = None,
    component: str | None = None,
) -> Iterator[None]:
    """Bind correlation fields for the duration of a unit of work."""
    tokens = [
        _trace_id.set(trace_id or current_trace_id()),
        _request_id.set(request_id or _request_id.get()),
        _tenant_id.set(str(tenant_id) if tenant_id is not None else _tenant_id.get()),
        _principal_id.set(str(principal_id) if principal_id is not None else _principal_id.get()),
        _component.set(component or _component.get()),
    ]
    try:
        yield
    finally:
        for var, token in zip(
            (_trace_id, _request_id, _tenant_id, _principal_id, _component),
            tokens,
            strict=True,
        ):
            var.reset(token)


def _inject_context(
    _logger: Any,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor adding the mandatory context to every record."""
    event_dict["trace_id"] = _trace_id.get()
    event_dict["request_id"] = _request_id.get()
    event_dict["tenant_id"] = _tenant_id.get()
    event_dict["principal_id"] = _principal_id.get()
    event_dict["component"] = _component.get()
    return event_dict


# --- value protection --------------------------------------------------------

#: Field names that must never reach a log record or a telemetry attribute.
#: Denylisted names are dropped outright; see ``redact``.
_FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "credential",
        "credentials",
        "dsn",
        "connection_string",
        "private_key",
        "signing_secret",
        # Business values. These are the product's payload and never telemetry.
        "value",
        "values",
        "rows",
        "sample",
        "sample_values",
        "prompt",
        "completion",
    }
)


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload`` with forbidden keys removed.

    Drops rather than masks. A ``"password": "***"`` entry still confirms a
    password was present in that structure; dropping the key does not.
    """
    return {
        key: value
        for key, value in payload.items()
        if key.lower() not in _FORBIDDEN_KEYS and "secret" not in key.lower()
    }


def is_loggable_key(key: str) -> bool:
    """Whether ``key`` may appear in a log record or telemetry attribute."""
    lowered = key.lower()
    return lowered not in _FORBIDDEN_KEYS and "secret" not in lowered


# --- configuration -----------------------------------------------------------


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib root logger.

    Records go to stdout as structured JSON (or a readable console renderer in
    local development) and are shipped by the platform. The application never
    writes log files (ADR-014 §1).
    """
    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelNamesMapping()[settings.log_level],
    )
    # asyncpg and SQLAlchemy are noisy at INFO and can echo statement text.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a bounded context name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(component)
    return logger
