"""LLM client interface.

The real OpenRouter/Gemini implementation (retry, circuit breaker, fallback)
will be provided by the LLM integration step (Fase 3). This module defines the
contract plus a local echo implementation used as a safe default and in tests.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None


class LLMClient(Protocol):
    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult: ...


class EchoLLMClient:
    """Deterministic offline client: never makes network calls."""

    provider = "echo"

    def __init__(self, default_model: str = "echo-local"):
        self.default_model = default_model

    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult:
        used_model = model or self.default_model
        n = len(prompt.split())
        return LLMResult(
            text=f"[echo:{used_model}] {prompt}",
            model=used_model,
            provider=self.provider,
            prompt_tokens=n,
            completion_tokens=n + 2,
            total_tokens=2 * n + 2,
        )
