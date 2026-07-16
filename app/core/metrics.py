"""Prometheus metrics: HTTP (middleware) + LLM business metrics.

Exposed at GET /metrics (text format). A dedicated registry is used so tests
can create multiple app instances without duplicated-timeseries errors.
"""

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

# --- HTTP -----------------------------------------------------------------
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
    registry=registry,
)
rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Requests rejected with 429 by the rate limiter.",
    ["path"],
    registry=registry,
)
guardrail_blocked_total = Counter(
    "guardrail_blocked_total",
    "Prompts bloqueados pelo guardrail de escopo temático, por categoria.",
    ["category"],
    registry=registry,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

# --- LLM business metrics ---------------------------------------------------
llm_requests_total = Counter(
    "llm_requests_total",
    "LLM provider calls by outcome (success|transient_error|permanent_error|skipped_open_circuit).",
    ["provider", "model", "outcome"],
    registry=registry,
)
llm_latency_seconds = Histogram(
    "llm_latency_seconds",
    "Latency of successful LLM provider calls in seconds.",
    ["provider"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0),
    registry=registry,
)
llm_fallback_total = Counter(
    "llm_fallback_total",
    "Requests served by a non-primary provider after the primary failed.",
    ["provider"],
    registry=registry,
)
llm_cache_hits_total = Counter(
    "llm_cache_hits_total",
    "Respostas servidas do cache de TTL (sem chamada ao LLM)",
    registry=registry,
)
llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total tokens consumed, by provider/model and kind (prompt|completion).",
    ["provider", "model", "kind"],
    registry=registry,
)
llm_selected_model = Gauge(
    "llm_selected_model",
    "Modelo selecionado automaticamente por provider e modo (valor 1 = ativo).",
    ["provider", "mode", "model"],
    registry=registry,
)
circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state per provider: 0=closed, 1=half-open, 2=open.",
    ["provider"],
    registry=registry,
)

_BREAKER_STATE_VALUE = {"closed": 0, "half-open": 1, "open": 2}


def set_breaker_state(provider: str, state: str) -> None:
    circuit_breaker_state.labels(provider=provider).set(_BREAKER_STATE_VALUE.get(state, 0))


# Última série ativa por (provider, mode) para zerar a anterior ao trocar de modelo.
_last_selected_model: dict[tuple[str, str], str] = {}


def set_selected_model(provider: str, mode: str, model: str) -> None:
    """Registra o modelo ativo; remove a série do modelo anterior (se mudou)."""
    previous = _last_selected_model.get((provider, mode))
    if previous is not None and previous != model:
        try:
            llm_selected_model.remove(provider, mode, previous)
        except KeyError:  # série já removida (ex.: registry recriado em testes)
            pass
    _last_selected_model[(provider, mode)] = model
    llm_selected_model.labels(provider=provider, mode=mode, model=model).set(1)


router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
