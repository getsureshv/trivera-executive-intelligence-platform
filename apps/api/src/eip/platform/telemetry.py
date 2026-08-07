"""OpenTelemetry wiring (ADR-014).

Vendor-neutral by construction: the application depends on the OTel API only
and exports OTLP to a collector, which is the swap point for any backend. No
vendor SDK appears in application code (guardrail 15).

Disabled by default. Local development requires no collector; setting
``EIP_OTEL_ENABLED=true`` turns it on.

The attribute allowlist is the load-bearing part. Telemetry in this system
naturally wants to carry business data — a span attribute holding a metric
value, a log field holding a customer name — and that data must not leave for a
third-party backend. ``safe_attributes`` drops anything not explicitly
permitted (ADR-014 §6).
"""

from __future__ import annotations

from typing import Any, Final

from eip.platform.logging import get_logger, is_loggable_key
from eip.platform.settings import Settings

_log = get_logger("platform.telemetry")

#: Attributes permitted on spans and metrics. Anything absent is dropped.
#: Identifiers, counts, durations, and statuses are allowed; *values* are not.
ALLOWED_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        # correlation
        "tenant_id",
        "trace_id",
        "request_id",
        "principal_id",
        "component",
        "operation",
        "environment",
        "service",
        "release",
        # governed query (populated from Phase 1B onward)
        "metric_code",
        "metric_version",
        "config_version",
        "plan_hash",
        "cache_hit",
        "rows_scanned",
        "rows_returned",
        "engine",
        "row_scope_applied",
        # http
        "http.method",
        "http.route",
        "http.status_code",
        # pipeline
        "pipeline_run_id",
        "pipeline_step",
        "attempt",
        "queue",
        "duration_ms",
        "outcome",
        "reason_code",
    }
)


def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Filter ``attributes`` down to the allowlist.

    Drop-by-default. A key must be both explicitly allowed *and* pass the
    logging denylist, so a future addition to ``ALLOWED_ATTRIBUTES`` that
    happens to name a value-bearing field is still refused.
    """
    return {
        key: value
        for key, value in attributes.items()
        if key in ALLOWED_ATTRIBUTES and is_loggable_key(key)
    }


def configure_telemetry(settings: Settings) -> None:
    """Initialise tracing if enabled; otherwise do nothing.

    Imports are local so that a deployment with telemetry disabled does not pay
    the SDK import cost, and so the module remains importable if the optional
    exporter is absent.
    """
    if not settings.otel_enabled:
        _log.info("telemetry.disabled")
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.env.value,
        }
    )
    provider = TracerProvider(
        resource=resource,
        # Governed queries and ingestion are always sampled from Phase 1B;
        # tail-based sampling belongs at the collector, not here (ADR-014).
        sampler=ParentBased(root=TraceIdRatioBased(1.0)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    _log.info("telemetry.enabled", endpoint=settings.otel_exporter_otlp_endpoint)


def instrument_app(app: object, settings: Settings) -> None:
    """Attach FastAPI/SQLAlchemy instrumentation when telemetry is enabled."""
    if not settings.otel_enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    _log.info("telemetry.instrumented", target="fastapi")
