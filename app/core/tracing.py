"""OpenTelemetry tracing — disabled by default, enabled via env.

- OTEL_ENABLED=false (default): no-op, zero overhead, no exporter.
- OTEL_ENABLED=true + OTEL_EXPORTER_OTLP_ENDPOINT set: OTLP/HTTP exporter
  (e.g. Jaeger's collector at http://jaeger:4318).
- OTEL_ENABLED=true without endpoint: ConsoleSpanExporter (local debugging).

Auto-instrumentation: FastAPI (server spans), httpx (LLM egress) and
SQLAlchemy (persistence). Custom named spans are created via `start_span`,
which degrades to a no-op when tracing is disabled.
"""

import contextlib
import logging
from collections.abc import Iterator

from app.core.config import Settings

logger = logging.getLogger("app.tracing")

_tracer = None  # set by setup_tracing when enabled


def setup_tracing(settings: Settings, app=None, engine=None, http_client=None) -> None:
    """Configure the tracer provider and auto-instrumentation. Idempotent-ish."""
    global _tracer
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:  # pragma: no cover - otel is an optional runtime dep
        logger.warning("OTEL_ENABLED=true but opentelemetry packages are not installed")
        return

    resource = Resource.create({"service.name": settings.otel_service_name or settings.app_name})
    provider = TracerProvider(resource=resource)
    from opentelemetry.sdk.trace.export import SpanExporter

    exporter: SpanExporter
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("itau-ms")

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health,/ready")
    with contextlib.suppress(Exception):
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    if engine is not None:
        with contextlib.suppress(Exception):
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    logger.info(
        "tracing enabled (exporter=%s)",
        settings.otel_exporter_otlp_endpoint or "console",
    )


@contextlib.contextmanager
def start_span(name: str, **attributes) -> Iterator[None]:
    """Named span helper; no-op when tracing is disabled."""
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield
