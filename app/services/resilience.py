"""Resilient LLM client: retry + circuit breaker + provider fallback.

Chain: OpenRouter (primary) -> Gemini (fallback).
- Retry with exponential backoff + jitter ONLY for transient errors
  (timeout, 5xx) — never for 4xx validation errors. Max 2 retries.
  429 (rate limit) is NOT retried: it fails fast to the next provider.
- Simple per-provider circuit breaker: opens after N consecutive failures,
  half-open after a cooldown (one probe call allowed).
- If every provider fails or has an open circuit, raises
  `LLMUnavailableError` (AppError 503 / code=llm_unavailable). The /v1/chat
  endpoint persists the interaction as status=failed before re-raising, so
  the prompt is never lost.
"""

import logging
import time

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings
from app.core.errors import AppError
from app.core.metrics import (
    llm_fallback_total,
    llm_latency_seconds,
    llm_requests_total,
    llm_tokens_total,
    set_breaker_state,
)
from app.core.tracing import start_span
from app.services.llm import EchoLLMClient, LLMClient, LLMResult
from app.services.model_selector import ModelSelector
from app.services.providers import (
    GeminiProvider,
    LLMPermanentError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTransientError,
    OpenRouterProvider,
)

logger = logging.getLogger("app.llm")


class LLMUnavailableError(AppError):
    DEFAULT_MESSAGE = "LLM providers are currently unavailable. The prompt was stored."

    def __init__(self, message: str = DEFAULT_MESSAGE):
        super().__init__(code="llm_unavailable", message=message, status_code=503)


class CircuitBreaker:
    """Minimal circuit breaker: closed -> open (threshold) -> half-open (cooldown)."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            return "half-open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()


class ResilientLLMClient:
    """Fans out over an ordered provider chain with retry + circuit breaking."""

    provider = "resilient"
    # Preenchido por build_llm_client quando LLM_AUTO_MODEL=true; o lifespan
    # usa para disparar a seleção inicial de modelos no startup.
    model_selector: ModelSelector | None = None

    def __init__(
        self,
        providers: list[LLMClient],
        max_retries: int = 2,
        backoff_initial_seconds: float = 0.5,
        backoff_max_seconds: float = 4.0,
        breaker_failure_threshold: int = 5,
        breaker_cooldown_seconds: float = 30.0,
    ):
        if not providers:
            raise ValueError("ResilientLLMClient requires at least one provider")
        self.providers = providers
        self._max_attempts = max_retries + 1
        self._backoff_initial = backoff_initial_seconds
        self._backoff_max = backoff_max_seconds
        self.breakers: dict[str, CircuitBreaker] = {
            getattr(p, "provider", f"provider{i}"): CircuitBreaker(
                breaker_failure_threshold, breaker_cooldown_seconds
            )
            for i, p in enumerate(providers)
        }
        for provider_name in self.breakers:
            set_breaker_state(provider_name, "closed")

    async def _call_with_retry(
        self, provider: LLMClient, prompt: str, model: str | None, mode: str
    ) -> LLMResult:
        async for attempt in AsyncRetrying(
            # 429 (LLMRateLimitError) NÃO é retentado: com fallback na cadeia,
            # insistir num provider saturado só adiciona latência.
            retry=retry_if_exception(
                lambda e: isinstance(e, LLMTransientError) and not isinstance(e, LLMRateLimitError)
            ),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=self._backoff_initial, max=self._backoff_max),
            reraise=True,
        ):
            with attempt:
                return await provider.generate(prompt, model=model, mode=mode)
        # Unreachable with reraise=True; kept as a guard for tenacity API changes.
        raise RetryError(attempt)  # type: ignore[arg-type]  # pragma: no cover

    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult:
        last_error: Exception | None = None
        for index, provider in enumerate(self.providers):
            name = getattr(provider, "provider", f"provider{index}")
            breaker = self.breakers[name]
            if not breaker.allow():
                logger.warning("llm provider=%s circuit=open, skipping", name)
                set_breaker_state(name, breaker.state)
                llm_requests_total.labels(
                    provider=name, model="-", outcome="skipped_open_circuit"
                ).inc()
                continue
            # Client-requested model only applies to the primary provider;
            # fallback providers use their own default model (names differ
            # across catalogs).
            requested_model = model if index == 0 else None
            started = time.perf_counter()
            try:
                with start_span("llm.generate", provider=name, model=requested_model):
                    result = await self._call_with_retry(provider, prompt, requested_model, mode)
            except LLMPermanentError as exc:
                # Not a provider-health signal: do not trip the breaker,
                # but try the next provider in the chain.
                logger.warning("llm provider=%s permanent error: %s", name, exc)
                llm_requests_total.labels(
                    provider=name, model=requested_model or "-", outcome="permanent_error"
                ).inc()
                last_error = exc
                continue
            except LLMProviderError as exc:
                breaker.record_failure()
                set_breaker_state(name, breaker.state)
                logger.warning(
                    "llm provider=%s failed after retries (circuit=%s): %s",
                    name,
                    breaker.state,
                    exc,
                )
                llm_requests_total.labels(
                    provider=name, model=requested_model or "-", outcome="transient_error"
                ).inc()
                last_error = exc
                continue
            breaker.record_success()
            set_breaker_state(name, breaker.state)
            latency_ms = result.latency_ms or round((time.perf_counter() - started) * 1000, 2)
            llm_requests_total.labels(
                provider=result.provider, model=result.model, outcome="success"
            ).inc()
            llm_latency_seconds.labels(provider=result.provider).observe(latency_ms / 1000)
            if result.prompt_tokens:
                llm_tokens_total.labels(
                    provider=result.provider, model=result.model, kind="prompt"
                ).inc(result.prompt_tokens)
            if result.completion_tokens:
                llm_tokens_total.labels(
                    provider=result.provider, model=result.model, kind="completion"
                ).inc(result.completion_tokens)
            if index > 0:
                llm_fallback_total.labels(provider=result.provider).inc()
            logger.info(
                "llm provider=%s model=%s latency_ms=%s tokens=%s",
                result.provider,
                result.model,
                latency_ms,
                result.total_tokens,
            )
            return result
        raise LLMUnavailableError() from last_error


def build_llm_client(settings: Settings, http_client: httpx.AsyncClient) -> LLMClient:
    """Factory used by the app lifespan.

    Builds the OpenRouter->Gemini chain from configured keys; falls back to
    the offline EchoLLMClient when no real key is configured (dev/test).

    With LLM_AUTO_MODEL=true (default) a shared ModelSelector picks the best
    model per provider/mode from the live catalogs; manual overrides
    (OPENROUTER_MODEL_DIRECT/GEMINI_MODEL_DIRECT) still win when set, and the
    env defaults (OPENROUTER_MODEL/GEMINI_MODEL) remain the final fallback.
    """
    selector: ModelSelector | None = None
    if settings.llm_auto_model:
        selector = ModelSelector(
            http_client,
            openrouter_base_url=settings.openrouter_base_url,
            openrouter_api_key=settings.openrouter_api_key,
            gemini_base_url=settings.gemini_base_url,
            gemini_api_key=settings.gemini_api_key,
            direct_max_tokens=settings.llm_direct_max_tokens,
            detailed_max_tokens=settings.llm_detailed_max_tokens,
            refresh_seconds=settings.llm_model_refresh_seconds,
            catalog_timeout_seconds=settings.ready_check_timeout_seconds,
            web_search=settings.llm_web_search,
        )
    providers: list[LLMClient] = []
    if settings.openrouter_api_key and settings.openrouter_api_key != "changeme":
        providers.append(
            OpenRouterProvider(
                http_client,
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                default_model=settings.openrouter_model,
                timeout_seconds=settings.llm_timeout_seconds,
                system_prompt=settings.llm_system_prompt,
                web_search=settings.llm_web_search,
                direct_model=settings.openrouter_model_direct,
                direct_max_tokens=settings.llm_direct_max_tokens,
                detailed_max_tokens=settings.llm_detailed_max_tokens,
                model_selector=selector,
            )
        )
    if settings.gemini_api_key and settings.gemini_api_key != "changeme":
        providers.append(
            GeminiProvider(
                http_client,
                api_key=settings.gemini_api_key,
                base_url=settings.gemini_base_url,
                default_model=settings.gemini_model,
                timeout_seconds=settings.llm_timeout_seconds,
                system_prompt=settings.llm_system_prompt,
                web_search=settings.llm_web_search,
                direct_model=settings.gemini_model_direct,
                direct_max_tokens=settings.llm_direct_max_tokens,
                detailed_max_tokens=settings.llm_detailed_max_tokens,
                model_selector=selector,
            )
        )
    if settings.llm_web_search and len(providers) == 2:
        # Com busca web, o Gemini vira o primário: o grounding nativo
        # (google_search) resolve em UMA chamada rápida e barata (~14s/1k
        # tokens observados), enquanto o plugin web do OpenRouter + modelos
        # grandes de reasoning custam 30-80s/4k tokens. OpenRouter vira
        # fallback — a resiliência da cadeia não muda.
        providers.reverse()
    if not providers:
        logger.warning("no LLM API keys configured; using offline EchoLLMClient")
        return EchoLLMClient()
    client = ResilientLLMClient(
        providers,
        max_retries=settings.llm_max_retries,
        backoff_initial_seconds=settings.llm_retry_backoff_initial_seconds,
        backoff_max_seconds=settings.llm_retry_backoff_max_seconds,
        breaker_failure_threshold=settings.llm_breaker_failure_threshold,
        breaker_cooldown_seconds=settings.llm_breaker_cooldown_seconds,
    )
    # Exposto para o lifespan disparar a seleção inicial no startup.
    client.model_selector = selector
    return client
