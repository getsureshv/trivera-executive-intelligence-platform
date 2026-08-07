"""HTTP middleware: correlation, structured access logs, error rendering.

Three responsibilities, deliberately in one ordered place.

**Correlation.** Every request gets a ``trace_id`` and ``request_id`` bound into
contextvars, so every log record and audit event emitted while handling it
carries them without any handler passing them along (ADR-014 §2). An inbound
``traceparent`` is honoured so a trace begun in the browser continues here.

**Error rendering.** All ``EipError`` subclasses become RFC 9457
``application/problem+json``. Unexpected exceptions become an opaque 500 with a
correlation id — the detail stays server-side, because stack traces and driver
messages leak schema, host, and sometimes credential fragments.

**Header hygiene.** ``X-Tenant-Id`` is *actively ignored*. It is stripped and
logged if present, because a client sending one is either confused or probing,
and both are worth seeing (ADR-003 §3).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from eip.platform.errors import EipError
from eip.platform.logging import bind_context, get_logger, new_trace_id

_log = get_logger("api.http")

PROBLEM_CONTENT_TYPE = "application/problem+json"

#: Headers a client might send hoping to influence tenant resolution. They are
#: never read. Listed so the intent is explicit and testable.
IGNORED_TENANT_HEADERS = ("x-tenant-id", "x-tenant", "x-org-id", "x-organization-id")


def _extract_trace_id(request: Request) -> str:
    """Continue an inbound W3C trace if one is present, else start a new one."""
    traceparent = request.headers.get("traceparent")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 2 and len(parts[1]) == 32:
            return parts[1]
    return new_trace_id()


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Bind correlation ids and emit one structured access log per request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = _extract_trace_id(request)
        request_id = uuid.uuid4().hex

        supplied_tenant_headers = [
            name for name in IGNORED_TENANT_HEADERS if name in request.headers
        ]

        request.state.trace_id = trace_id
        request.state.request_id = request_id

        started = perf_counter()
        with bind_context(trace_id=trace_id, request_id=request_id, component="api"):
            if supplied_tenant_headers:
                # Not an error — just ignored. Logged because a client trying to
                # select a tenant by header is worth noticing.
                _log.warning(
                    "http.tenant_header_ignored",
                    headers=supplied_tenant_headers,
                    path=request.url.path,
                )

            response = await call_next(request)

            duration_ms = round((perf_counter() - started) * 1000, 2)
            _log.info(
                "http.request",
                method=request.method,
                # The route template, not the raw path: a path may contain
                # identifiers, and high-cardinality log fields are a cost and a
                # privacy problem.
                route=_route_template(request),
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

        response.headers["x-request-id"] = request_id
        response.headers["x-trace-id"] = trace_id
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    return str(path_format) if path_format else request.url.path


async def handle_eip_error(request: Request, exc: Exception) -> JSONResponse:
    """Render a deliberate application error as problem+json."""
    assert isinstance(exc, EipError)
    correlation_id = getattr(request.state, "trace_id", "unknown")

    log_method = _log.warning if exc.status_code < 500 else _log.error
    log_method(
        "http.error",
        code=exc.code,
        status_code=exc.status_code,
        route=_route_template(request),
    )

    return JSONResponse(
        status_code=exc.status_code,
        media_type=PROBLEM_CONTENT_TYPE,
        content=exc.to_problem(correlation_id=correlation_id, instance=request.url.path),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Render an unanticipated exception without leaking its detail.

    The exception is logged in full server-side and the client receives only a
    correlation id. Driver errors and tracebacks routinely contain host names,
    schema details, and occasionally credential fragments.
    """
    correlation_id = getattr(request.state, "trace_id", "unknown")
    _log.exception(
        "http.unhandled_exception",
        error_type=type(exc).__name__,
        route=_route_template(request),
    )
    return JSONResponse(
        status_code=500,
        media_type=PROBLEM_CONTENT_TYPE,
        content={
            "type": "https://docs.trivera.invalid/problem/internal-error",
            "title": "Internal server error",
            "status": 500,
            "detail": "An unexpected error occurred. Quote the correlation id when reporting it.",
            "code": "INTERNAL_ERROR",
            "instance": request.url.path,
            "correlation_id": correlation_id,
        },
    )
