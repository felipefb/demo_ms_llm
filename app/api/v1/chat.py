"""Rotas /v1: POST /v1/chat e GET /v1/conversations/{user_id}."""

import logging
import time

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import get_guardrail, get_llm_client, get_repository, get_response_cache
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.metrics import guardrail_blocked_total, llm_cache_hits_total
from app.repositories.conversations import ConversationRepository
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationItem,
    ConversationPage,
    StructuredResponse,
    TokenUsage,
)
from app.services.cache import ResponseCache
from app.services.formatting import parse_structured_answer
from app.services.guardrail import TopicGuardrail
from app.services.llm import LLMClient

logger = logging.getLogger("app.guardrail")

router = APIRouter(prefix="/v1", tags=["chat"])


def _validate_model(model: str | None, settings: Settings) -> None:
    """Client-requested model must belong to the configured allowlist.

    Safe default: with no allowlist configured, clients cannot override
    the model (the service default is used by omitting the field).
    """
    if model is None:
        return
    if model not in settings.allowed_models:
        raise AppError(
            code="model_not_allowed",
            message="Requested model is not allowed. Omit 'model' to use the default.",
            status_code=422,
        )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Envia um prompt ao LLM e persiste a conversa",
    description=(
        "Persiste o prompt (status=pending) ANTES de invocar o LLM "
        "(OpenRouter com fallback para Gemini); em caso de sucesso a interacao "
        "vira completed, em caso de falha vira failed — o prompt nunca se perde. "
        "Temas fora do escopo (ex.: politica, religiao) sao bloqueados pelo "
        "guardrail antes do LLM e respondidos com status=blocked."
    ),
)
async def chat(
    payload: ChatRequest,
    llm: LLMClient = Depends(get_llm_client),
    repo: ConversationRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
    cache: ResponseCache = Depends(get_response_cache),
    guardrail: TopicGuardrail = Depends(get_guardrail),
) -> ChatResponse:
    _validate_model(payload.model, settings)

    # 1. Persist the prompt BEFORE calling the LLM (never lose a prompt).
    record = await repo.create_pending(
        user_id=payload.user_id,
        prompt=payload.prompt,
        model=payload.model,
        metadata=payload.metadata,
    )

    # 1.5. Guardrail de escopo temático: temas divergentes (ex.: política,
    # religião) recebem resposta controlada SEM tocar cache/LLM. A tentativa
    # fica auditável (status=blocked) e gera aviso: WARNING no log estruturado
    # + métrica guardrail_blocked_total{category}.
    blocked = guardrail.check(payload.prompt)
    if blocked is not None:
        guardrail_blocked_total.labels(category=blocked.category).inc()
        logger.warning(
            "guardrail: prompt bloqueado category=%s term=%s user_id=%s interaction_id=%s",
            blocked.category,
            blocked.term,
            payload.user_id,
            record.id,
        )
        record = await repo.mark_blocked(
            record.id,
            response=settings.guardrail_message,
            reason=f"guardrail: tema '{blocked.category}' fora do escopo",
            latency_ms=0.0,
        )
        return ChatResponse(
            id=record.id,
            user_id=record.user_id,
            prompt=record.prompt,
            response=record.response,
            structured=StructuredResponse(
                resposta=settings.guardrail_message,
                contexto=(
                    "Aviso: tentativa de tema fora do escopo detectada "
                    f"(categoria: {blocked.category}). A tentativa foi registrada."
                ),
            ),
            model=None,
            provider=record.provider,
            status=record.status,
            usage=None,
            timestamp=record.updated_at,
            latency_ms=0.0,
        )

    # 2. Cache com TTL: prompts idênticos na janela reutilizam a resposta —
    # latência de ms e zero tokens (performance com custo controlado).
    start = time.perf_counter()
    cached = cache.get(payload.prompt, payload.response_mode, payload.model)
    if cached is not None:
        llm_cache_hits_total.inc()
        result = cached
    else:
        try:
            result = await llm.generate(
                payload.prompt, model=payload.model, mode=payload.response_mode
            )
            cache.put(payload.prompt, payload.response_mode, payload.model, result)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            await repo.mark_failed(
                record.id, error_detail=f"{type(exc).__name__}: {exc}", latency_ms=latency_ms
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                code="llm_unavailable",
                message="LLM providers are currently unavailable. The prompt was stored.",
                status_code=503,
            ) from exc
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    # 3. Normaliza a resposta no esquema estruturado (fallback seguro para
    # texto cru) e persiste a frase direta como response para analytics.
    structured = parse_structured_answer(result.text)

    record = await repo.mark_completed(
        record.id,
        response=structured.resposta,
        model=result.model,
        provider=result.provider,
        latency_ms=latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )

    return ChatResponse(
        id=record.id,
        user_id=record.user_id,
        prompt=record.prompt,
        response=record.response,
        structured=StructuredResponse(
            resposta=structured.resposta,
            dados=structured.dados,
            contexto=structured.contexto,
            fontes=structured.fontes,
            normalizada=structured.normalizada,
        ),
        model=record.model,
        provider=record.provider,
        status=record.status,
        usage=TokenUsage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        ),
        timestamp=record.updated_at,
        latency_ms=latency_ms,
    )


@router.get(
    "/conversations/{user_id}",
    response_model=ConversationPage,
    summary="Historico paginado de conversas de um usuario",
)
async def list_conversations(
    user_id: str = Path(..., min_length=1, max_length=128),
    limit: int = Query(default=20, ge=1, le=100, description="Tamanho da pagina."),
    offset: int = Query(default=0, ge=0, description="Deslocamento da pagina."),
    repo: ConversationRepository = Depends(get_repository),
) -> ConversationPage:
    items, total = await repo.list_by_user(user_id, limit=limit, offset=offset)
    return ConversationPage(
        items=[
            ConversationItem(
                id=r.id,
                user_id=r.user_id,
                prompt=r.prompt,
                response=r.response,
                model=r.model,
                provider=r.provider,
                status=r.status,
                timestamp=r.updated_at,
            )
            for r in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
