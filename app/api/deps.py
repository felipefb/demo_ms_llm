"""Dependency providers.

Resources are created in the app lifespan (app/main.py) and stored on
`app.state` — no import-time singletons. Providers resolve from
`request.app.state`, and remain overridable in tests via
`app.dependency_overrides`.
"""

from fastapi import Request

from app.repositories.conversations import ConversationRepository
from app.services.cache import ResponseCache
from app.services.guardrail import TopicGuardrail
from app.services.llm import LLMClient


def get_repository(request: Request) -> ConversationRepository:
    return request.app.state.repository


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def get_response_cache(request: Request) -> "ResponseCache":
    cache = getattr(request.app.state, "response_cache", None)
    if cache is None:
        # TestClient sem lifespan (ou provisioning parcial): cria lazy.
        from app.core.config import get_settings

        cache = ResponseCache(get_settings().llm_cache_ttl_seconds)
        request.app.state.response_cache = cache
    return cache


def get_guardrail(request: Request) -> TopicGuardrail:
    guardrail = getattr(request.app.state, "guardrail", None)
    if guardrail is None:
        # TestClient sem lifespan (ou provisioning parcial): cria lazy.
        from app.core.config import get_settings

        guardrail = TopicGuardrail.from_settings(get_settings())
        request.app.state.guardrail = guardrail
    return guardrail
