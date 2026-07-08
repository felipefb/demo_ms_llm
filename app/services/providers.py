"""LLM provider implementations (OpenRouter + Gemini).

Each provider implements the `LLMClient` protocol (async generate) and raises:
- `LLMTransientError` for retryable failures (timeout, 5xx, network);
- `LLMRateLimitError` (429): fails fast to the fallback provider (no retry);
- `LLMPermanentError` for non-retryable failures (validation 4xx, bad payload).

Providers share the single `httpx.AsyncClient` created in the app lifespan.
"""

import logging
import time
from datetime import UTC, datetime

import httpx

from app.services.llm import LLMResult
from app.services.model_selector import ModelSelector

logger = logging.getLogger("app.llm")

# Instruções de estilo por modo de resposta. "direct" força respostas curtas e
# objetivas (menos tokens de saída = menor custo/latência); "detailed" libera
# contexto adicional quando o cliente pedir.
_JSON_CONTRACT = (
    "Responda APENAS com JSON valido (sem cercas de codigo, sem texto fora do "
    'JSON) no esquema: {"resposta": string, "dados": [{"indicador": string, '
    '"valor": string, "fonte": string}], "contexto": string, "fontes": '
    '[string]}. "resposta" e uma unica frase direta com a informacao pedida. '
    'Use "dados" para valores/numeros/itens comparaveis (uma linha por '
    'indicador; omita se nao houver) e SEMPRE preencha "fonte" em cada item. '
    "Para taxa de cambio, use SEMPRE a PTAX do Banco Central do Brasil como "
    "fonte primaria (a parcial intradiaria mais recente de hoje ou, apos as "
    "13h11, o fechamento), indicando data e horario de referencia; somente "
    "se a PTAX nao aparecer nas fontes, informe o valor de mercado rotulando "
    "fonte e horario e diga que a PTAX nao estava disponivel. Para outros "
    "dados, prefira fontes oficiais e indique a data de referencia. "
    "Para valores de mercado: com o mercado ABERTO, informe o valor MAIS "
    "RECENTE divulgado HOJE (cotacao intradiaria/parcial, ex.: PTAX parcial "
    "do Banco Central), com o horario da ultima atualizacao — nao responda "
    "apenas com o fechamento de ontem se existir valor de hoje nas fontes. "
    "Somente se NAO houver nenhum valor de hoje, diga isso explicitamente, "
    "informe o ultimo valor disponivel com a data de referencia e o horario "
    "de abertura do mercado ou da proxima divulgacao oficial. Seja rapido: "
    "responda com a primeira informacao suficiente encontrada — se a busca "
    "nao trouxer valor de hoje, use IMEDIATAMENTE o fechamento do dia "
    "anterior, sem buscas adicionais. Nunca diga que o valor de hoje "
    "'fechou' se o mercado ainda estiver aberto."
)
MODE_STYLE = {
    "direct": (
        f"{_JSON_CONTRACT} Omita os campos contexto e fontes. Seja objetivo: "
        "sem ressalvas e sem sugestoes."
    ),
    "detailed": (
        f"{_JSON_CONTRACT} Preencha contexto com um paragrafo relevante e "
        "fontes com as URLs consultadas (se houver)."
    ),
}


class LLMProviderError(Exception):
    """Base error raised by a provider call."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class LLMTransientError(LLMProviderError):
    """Retryable: timeout, connection error, 5xx."""


class LLMRateLimitError(LLMTransientError):
    """429 do provider: transitório para o circuit breaker, mas SEM retry —
    com fallback disponível, insistir num provider saturado só soma latência."""


class LLMPermanentError(LLMProviderError):
    """Non-retryable: 4xx validation errors, malformed responses."""


async def _resolve_model(
    provider: str,
    model: str | None,
    mode: str,
    direct_model: str,
    selector: "ModelSelector | None",
    default_model: str,
) -> tuple[str, bool]:
    """Precedência: modelo do cliente (já validado pela allowlist) > override
    manual por modo (env *_MODEL_DIRECT) > seleção automática (catálogo) >
    default do env. Retorna (modelo, veio_da_seleção_automática) — o flag
    permite denylistar o modelo no selector em erro permanente (auto-cura)."""
    if model:
        return model, False
    if mode == "direct" and direct_model:
        return direct_model, False
    if selector is not None:
        selected = await selector.get_model(provider, mode)
        if selected:
            return selected, True
    return default_model, False


def _system_text(system_prompt: str, mode: str) -> str:
    """System prompt final: data atual (o modelo nao sabe que dia e hoje e
    tende a inferir das fontes da busca) + prompt fixo + contrato do modo."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    style = MODE_STYLE.get(mode, MODE_STYLE["direct"])
    return f"Hoje e {today} (UTC). {system_prompt} {style}".strip()


def _mark_bad(selector: "ModelSelector | None", auto: bool, provider: str, model: str) -> None:
    """Auto-cura: denylista o modelo escolhido pelo catálogo em erro permanente."""
    if auto and selector is not None:
        selector.mark_bad(provider, model)


def _classify_http_error(provider: str, response: httpx.Response) -> LLMProviderError:
    status = response.status_code
    # Never leak upstream bodies into exceptions surfaced to clients.
    message = f"HTTP {status} from upstream"
    if status == 429:
        return LLMRateLimitError(provider, message, status_code=status)
    if status >= 500:
        return LLMTransientError(provider, message, status_code=status)
    return LLMPermanentError(provider, message, status_code=status)


class OpenRouterProvider:
    """OpenRouter chat-completions provider (OpenAI-compatible API)."""

    provider = "openrouter"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "meta-llama/llama-3.3-70b-instruct:free",
        timeout_seconds: float = 30.0,
        system_prompt: str = "",
        web_search: bool = False,
        direct_model: str = "",
        direct_max_tokens: int = 256,
        detailed_max_tokens: int = 1024,
        model_selector: ModelSelector | None = None,
    ):
        self._client = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._timeout = timeout_seconds
        self._system_prompt = system_prompt
        self._web_search = web_search
        # Roteamento por custo: modo direct usa um modelo mais leve, se configurado.
        self._direct_model = direct_model
        self._max_tokens = {"direct": direct_max_tokens, "detailed": detailed_max_tokens}
        # Seleção automática de modelos via catálogo (LLM_AUTO_MODEL=true).
        self._selector = model_selector
        # Modelos que já truncaram no teto (thinking): próxima chamada vai
        # direto sem teto — economiza uma ida ao provider por request.
        self._uncapped_models: set[str] = set()

    def _mark_bad_if_auto(self, auto_selected: bool, used_model: str) -> None:
        _mark_bad(self._selector, auto_selected, self.provider, used_model)

    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult:
        used_model, auto_selected = await _resolve_model(
            self.provider, model, mode, self._direct_model, self._selector, self.default_model
        )
        # Prompt-injection mitigation: fixed server-side system prompt; the
        # user prompt is ALWAYS a separate user-role message, never
        # concatenated into instructions.
        messages: list[dict] = [
            {"role": "system", "content": _system_text(self._system_prompt, mode)},
            {"role": "user", "content": prompt},
        ]
        max_tokens = self._max_tokens.get(mode, 0)
        # Teto adaptativo: modelos com "thinking" gastam o teto em raciocínio
        # interno e devolvem 200 sem texto (finish_reason=length). Nesse caso
        # repetimos UMA vez sem teto — a culpa é do nosso limite, não do modelo.
        if max_tokens > 0 and used_model not in self._uncapped_models:
            caps = [max_tokens, 0]
        else:
            caps = [0]
        started = time.perf_counter()
        for i, cap in enumerate(caps):
            body: dict = {"model": used_model, "messages": messages}
            if cap > 0:
                body["max_tokens"] = cap
            if self._web_search:
                # OpenRouter web plugin: grounds the answer with live results.
                body["plugins"] = [{"id": "web"}]
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise LLMTransientError(self.provider, "request timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMTransientError(
                    self.provider, f"transport error: {type(exc).__name__}"
                ) from exc

            if response.status_code != 200:
                error = _classify_http_error(self.provider, response)
                if isinstance(error, LLMPermanentError):
                    # Auto-cura: modelo do catálogo com 4xx permanente sai da
                    # seleção e a re-escolha é imediata.
                    self._mark_bad_if_auto(auto_selected, used_model)
                raise error

            try:
                data = response.json()
                choice = data["choices"][0]
                text = choice["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self._mark_bad_if_auto(auto_selected, used_model)
                raise LLMPermanentError(self.provider, "malformed response body") from exc
            truncated = str(choice.get("finish_reason") or "").lower() == "length"
            if truncated and i + 1 < len(caps):
                self._uncapped_models.add(used_model)
                # Cortado pelo teto (modelos com reasoning estouram o cap no
                # meio do raciocínio): texto truncado é tão inútil quanto
                # vazio — repete UMA vez sem teto.
                logger.warning(
                    "llm provider=%s model=%s truncated by token cap; retrying uncapped",
                    self.provider,
                    used_model,
                )
                continue
            if isinstance(text, str) and text.strip():
                break
            if not truncated:
                # Vazio sem estouro de teto: o modelo é inutilizável p/ chat.
                self._mark_bad_if_auto(auto_selected, used_model)
            raise LLMPermanentError(self.provider, "empty completion content")

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=data.get("model", used_model),
            provider=self.provider,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
        )


class GeminiProvider:
    """Google Gemini generateContent provider."""

    provider = "gemini"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        default_model: str = "gemini-2.0-flash",
        timeout_seconds: float = 30.0,
        system_prompt: str = "",
        web_search: bool = False,
        direct_model: str = "",
        direct_max_tokens: int = 256,
        detailed_max_tokens: int = 1024,
        model_selector: ModelSelector | None = None,
    ):
        self._client = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._timeout = timeout_seconds
        self._system_prompt = system_prompt
        self._web_search = web_search
        self._direct_model = direct_model
        self._max_tokens = {"direct": direct_max_tokens, "detailed": detailed_max_tokens}
        self._selector = model_selector
        self._uncapped_models: set[str] = set()

    def _mark_bad_if_auto(self, auto_selected: bool, used_model: str) -> None:
        _mark_bad(self._selector, auto_selected, self.provider, used_model)

    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult:
        used_model, auto_selected = await _resolve_model(
            self.provider, model, mode, self._direct_model, self._selector, self.default_model
        )
        # Fixed server-side system instruction; user prompt stays a plain
        # user-role content part (prompt-injection mitigation).
        payload: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        payload["systemInstruction"] = {
            "parts": [{"text": _system_text(self._system_prompt, mode)}]
        }
        if self._web_search:
            # Grounding with Google Search: the model consults live results
            # before answering (e.g. current exchange rates).
            payload["tools"] = [{"google_search": {}}]
        max_tokens = self._max_tokens.get(mode, 0)
        # Teto adaptativo: modelos flash com "thinking" consomem o teto em
        # raciocínio interno e devolvem 200 sem parts (finishReason=MAX_TOKENS).
        # Nesse caso repetimos UMA vez sem teto — culpa do limite, não do modelo.
        if max_tokens > 0 and used_model not in self._uncapped_models:
            caps = [max_tokens, 0]
        else:
            caps = [0]
        started = time.perf_counter()
        for i, cap in enumerate(caps):
            if cap > 0:
                payload["generationConfig"] = {"maxOutputTokens": cap}
            else:
                payload.pop("generationConfig", None)
            try:
                response = await self._client.post(
                    f"{self._base_url}/models/{used_model}:generateContent",
                    # Header (not query param): key never appears in URLs/logs.
                    headers={"x-goog-api-key": self._api_key},
                    json=payload,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise LLMTransientError(self.provider, "request timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMTransientError(
                    self.provider, f"transport error: {type(exc).__name__}"
                ) from exc

            if response.status_code != 200:
                error = _classify_http_error(self.provider, response)
                if isinstance(error, LLMPermanentError):
                    # Auto-cura: modelo do catálogo com 4xx permanente sai da
                    # seleção e a re-escolha é imediata.
                    self._mark_bad_if_auto(auto_selected, used_model)
                raise error

            try:
                data = response.json()
                candidate = data["candidates"][0]
                parts = (candidate.get("content") or {}).get("parts") or []
                text = "".join(p["text"] for p in parts if isinstance(p.get("text"), str))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self._mark_bad_if_auto(auto_selected, used_model)
                raise LLMPermanentError(self.provider, "malformed response body") from exc
            truncated = str(candidate.get("finishReason") or "").upper() == "MAX_TOKENS"
            if truncated and i + 1 < len(caps):
                self._uncapped_models.add(used_model)
                # Cortado pelo teto: texto truncado é tão inútil quanto vazio.
                logger.warning(
                    "llm provider=%s model=%s truncated by token cap; retrying uncapped",
                    self.provider,
                    used_model,
                )
                continue
            if text.strip():
                break
            if not truncated:
                self._mark_bad_if_auto(auto_selected, used_model)
            raise LLMPermanentError(self.provider, "empty completion content")

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        usage = data.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            model=used_model,
            provider=self.provider,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            latency_ms=latency_ms,
        )
