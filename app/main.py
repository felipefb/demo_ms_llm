"""App factory (create_app) e lifespan: engine, http client, LLM e middlewares."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.chat import router as chat_router
from app.core.auth import ApiKeyAuthMiddleware
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.core.metrics import router as metrics_router
from app.core.middleware import RequestIdMiddleware
from app.core.ratelimit import RateLimitMiddleware
from app.core.responses import UTF8JSONResponse
from app.core.security_headers import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.core.tracing import setup_tracing
from app.repositories.conversations import InMemoryConversationRepository
from app.repositories.database import create_engine, create_session_factory
from app.repositories.postgres import PostgresConversationRepository
from app.services.cache import ResponseCache
from app.services.guardrail import TopicGuardrail
from app.services.resilience import ResilientLLMClient, build_llm_client

logger = logging.getLogger("app.llm")


async def _warmup_llm(llm: ResilientLLMClient) -> None:
    """Aquecimento best-effort: exercita seleção/denylist antes do 1º usuário."""
    try:
        result = await llm.generate("ping", mode="direct")
        logger.info("llm warmup ok provider=%s model=%s", result.provider, result.model)
    except Exception as exc:  # noqa: BLE001 — warmup nunca derruba a app
        logger.warning("llm warmup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Single shared HTTP client (connection pool) for all outbound calls.
    # Fase 3 providers and the /ready llm_egress check must use this client.
    app.state.http_client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
    # Repository: PostgreSQL by default (REPOSITORY_BACKEND=postgres);
    # in-memory for dev/tests without a database (REPOSITORY_BACKEND=memory).
    # Tests can still pre-set app.state.repository before startup.
    app.state.db_engine = None
    if getattr(app.state, "repository", None) is None:
        if settings.repository_backend == "postgres":
            app.state.db_engine = create_engine(settings)
            app.state.db_session_factory = create_session_factory(app.state.db_engine)
            app.state.repository = PostgresConversationRepository(app.state.db_session_factory)
        else:
            app.state.repository = InMemoryConversationRepository()
    app.state.response_cache = ResponseCache(settings.llm_cache_ttl_seconds)
    # Guardrail de escopo temático: pré-filtro de temas divergentes
    # (ex.: política, religião) avaliado antes do cache/LLM.
    app.state.guardrail = TopicGuardrail.from_settings(settings)
    if getattr(app.state, "llm_client", None) is None:
        # OpenRouter -> Gemini chain when keys are configured; offline
        # EchoLLMClient otherwise (dev/test without keys).
        app.state.llm_client = build_llm_client(settings, app.state.http_client)
    # Seleção automática de modelos (LLM_AUTO_MODEL): consulta os catálogos no
    # startup; re-seleção lazy por TTL nos requests. Best-effort — nunca falha.
    selector = getattr(app.state.llm_client, "model_selector", None)
    if selector is not None:
        await selector.refresh_all()
    # Warm-up em background (só na cadeia real; nunca em mocks/echo de teste).
    if settings.llm_warmup and isinstance(app.state.llm_client, ResilientLLMClient):
        app.state.warmup_task = asyncio.create_task(_warmup_llm(app.state.llm_client))
    # Tracing (OTel) — no-op unless OTEL_ENABLED=true.
    setup_tracing(settings, app=app, engine=app.state.db_engine)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        if app.state.db_engine is not None:
            await app.state.db_engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Itau MS - LLM Chat Service",
        description=(
            "Micro-servico que recebe prompts via API REST, invoca um LLM "
            "(OpenRouter free tier com fallback Gemini) e persiste prompt e "
            "resposta em PostgreSQL para analises futuras."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.state.repository = None
    app.state.llm_client = None

    # Middleware order (outermost -> innermost at runtime; add_middleware is LIFO):
    # RequestId -> CORS -> SecurityHeaders -> BodyLimit -> ApiKeyAuth -> RateLimit.
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(ApiKeyAuthMiddleware, settings=settings)
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(metrics_router)
    return app


app = create_app()
