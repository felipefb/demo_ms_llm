"""Schemas Pydantic de entrada/saida do endpoint de chat (validacao rigorosa)."""

import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_METADATA_KEYS = 20
MAX_METADATA_SERIALIZED_BYTES = 4096


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "user_id": "user-123",
                    "prompt": "Explique o que e um circuit breaker.",
                    "model": "meta-llama/llama-3.3-70b-instruct:free",
                    "metadata": {"channel": "web"},
                }
            ]
        },
    )

    user_id: str = Field(..., min_length=1, max_length=128, description="Identificador do usuario.")
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Prompt enviado ao LLM (1 a 4000 caracteres, nao pode ser so espacos).",
    )
    model: str | None = Field(
        default=None,
        max_length=128,
        description="Modelo desejado (opcional; validado contra allowlist se configurada).",
    )
    response_mode: Literal["direct", "detailed"] = Field(
        default="direct",
        description=(
            "direct (padrao): resposta curta e objetiva com modelo/limite de tokens "
            "otimizados para custo; detailed: inclui contexto adicional."
        ),
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description=(
            "Metadados para analytics (opcional). Chave/valor string, "
            f"max {MAX_METADATA_KEYS} chaves e {MAX_METADATA_SERIALIZED_BYTES} bytes serializados."
        ),
    )

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("prompt must not be empty or whitespace-only")
        return stripped

    @field_validator("metadata")
    @classmethod
    def metadata_limits(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return v
        if len(v) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata must have at most {MAX_METADATA_KEYS} keys")
        serialized = json.dumps(v, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > MAX_METADATA_SERIALIZED_BYTES:
            raise ValueError(
                f"metadata serialized size must be at most {MAX_METADATA_SERIALIZED_BYTES} bytes"
            )
        return v


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
                    "user_id": "user-123",
                    "prompt": "Explique o que e um circuit breaker.",
                    "response": "Um circuit breaker e um padrao de resiliencia...",
                    "model": "meta-llama/llama-3.3-70b-instruct:free",
                    "provider": "openrouter",
                    "status": "completed",
                    "usage": {"prompt_tokens": 12, "completion_tokens": 84, "total_tokens": 96},
                    "timestamp": "2026-07-06T12:00:00Z",
                    "latency_ms": 843.2,
                }
            ]
        }
    )

    id: uuid.UUID
    user_id: str
    prompt: str
    response: str | None
    structured: "StructuredResponse | None" = Field(
        default=None,
        description=(
            "Resposta normalizada em esquema fixo: resposta (frase direta), "
            "dados (linhas indicador/valor/fonte prontas para tabela), "
            "contexto e fontes (modo detailed). normalizada=false indica "
            "fallback de texto cru (modelo nao devolveu o esquema)."
        ),
    )
    model: str | None
    provider: str | None
    status: Literal["pending", "completed", "failed", "blocked"]
    usage: TokenUsage | None = None
    timestamp: datetime
    latency_ms: float


class StructuredResponse(BaseModel):
    """Esquema estável para normalização downstream (uma linha de `dados` por
    indicador — converte direto em tabela)."""

    resposta: str
    dados: list[dict[str, str]] = []
    contexto: str | None = None
    fontes: list[str] = []
    normalizada: bool = True


class ConversationItem(BaseModel):
    id: uuid.UUID
    user_id: str
    prompt: str
    response: str | None
    model: str | None
    provider: str | None
    status: Literal["pending", "completed", "failed", "blocked"]
    timestamp: datetime


class ConversationPage(BaseModel):
    items: list[ConversationItem]
    total: int
    limit: int
    offset: int
