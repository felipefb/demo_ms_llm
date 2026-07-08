"""LLM integration tests — fully offline via respx (mocked httpx).

Covers: OpenRouter success, 429 -> fallback imediato, OpenRouter down ->
fallback Gemini, both providers down -> 503 (prompt persisted as failed),
timeout handling, permanent 4xx (no retry), circuit breaker, and the
build_llm_client factory.
"""

import json
from datetime import UTC

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.repositories.conversations import InMemoryConversationRepository
from app.services.llm import EchoLLMClient
from app.services.providers import (
    GeminiProvider,
    LLMPermanentError,
    OpenRouterProvider,
)
from app.services.resilience import (
    CircuitBreaker,
    LLMUnavailableError,
    ResilientLLMClient,
    build_llm_client,
)
from tests.conftest import make_client

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
)

OPENROUTER_OK = {
    "model": "meta-llama/llama-3.3-70b-instruct:free",
    "choices": [{"message": {"role": "assistant", "content": "or-answer"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
}
GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": "gm-answer"}]}}],
    "usageMetadata": {
        "promptTokenCount": 5,
        "candidatesTokenCount": 9,
        "totalTokenCount": 14,
    },
}


def make_chain(http_client: httpx.AsyncClient, providers=None, **kwargs) -> ResilientLLMClient:
    if providers is None:
        providers = [
            OpenRouterProvider(http_client, api_key="test-or-key"),
            GeminiProvider(http_client, api_key="test-gm-key"),
        ]
    kwargs.setdefault("backoff_initial_seconds", 0.001)
    kwargs.setdefault("backoff_max_seconds", 0.002)
    return ResilientLLMClient(providers, **kwargs)


@respx.mock
async def test_openrouter_success():
    route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    async with httpx.AsyncClient() as http:
        result = await make_chain(http).generate("hello")
    assert route.call_count == 1
    assert result.provider == "openrouter"
    assert result.text == "or-answer"
    assert result.model == "meta-llama/llama-3.3-70b-instruct:free"
    assert result.total_tokens == 18
    assert result.latency_ms is not None
    # Auth header sent, key never in URL
    assert route.calls[0].request.headers["authorization"] == "Bearer test-or-key"


@respx.mock
async def test_openrouter_429_faz_fallback_imediato_sem_retry():
    """429 não é retentado (fail fast): com fallback na cadeia, insistir num
    provider saturado só soma latência — cai direto no Gemini."""
    or_route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(429, json={"error": "rate limited"})
    )
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        result = await make_chain(http).generate("hello")
    assert or_route.call_count == 1  # UMA tentativa, sem retries
    assert result.provider == "gemini"


@respx.mock
async def test_502_continua_sendo_retentado():
    """5xx segue com retry (transitório de verdade, sem custo de rate limit)."""
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[
            httpx.Response(502, json={"error": "bad gateway"}),
            httpx.Response(200, json=OPENROUTER_OK),
        ]
    )
    async with httpx.AsyncClient() as http:
        result = await make_chain(http).generate("hello")
    assert route.call_count == 2
    assert result.provider == "openrouter"


@respx.mock
async def test_openrouter_down_falls_back_to_gemini():
    or_route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    gm_route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        result = await make_chain(http, max_retries=1).generate("hello")
    assert or_route.call_count == 2  # 1 attempt + 1 retry
    assert gm_route.call_count == 1
    assert result.provider == "gemini"
    assert result.text == "gm-answer"
    assert result.total_tokens == 14
    # Gemini key travels in header, never in the URL
    assert gm_route.calls[0].request.headers["x-goog-api-key"] == "test-gm-key"
    assert "key=" not in str(gm_route.calls[0].request.url)


@respx.mock
async def test_both_providers_down_raises_llm_unavailable():
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(503))
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailableError) as exc_info:
            await make_chain(http, max_retries=0).generate("hello")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "llm_unavailable"


@respx.mock
async def test_timeout_is_retried_then_falls_back():
    or_route = respx.post(OPENROUTER_URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    gm_route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        result = await make_chain(http, max_retries=2).generate("hello")
    assert or_route.call_count == 3  # 1 attempt + 2 retries
    assert result.provider == "gemini"
    assert gm_route.call_count == 1


@respx.mock
async def test_permanent_4xx_is_not_retried():
    or_route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    gm_route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        result = await make_chain(http, max_retries=2).generate("hello")
    assert or_route.call_count == 1  # no retry on 4xx
    assert result.provider == "gemini"
    assert gm_route.call_count == 1


@respx.mock
async def test_permanent_error_direct_provider():
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="bad")
        with pytest.raises(LLMPermanentError):
            await provider.generate("hello")


@respx.mock
async def test_circuit_breaker_opens_and_skips_provider():
    or_route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(500))
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        chain = make_chain(
            http,
            max_retries=0,
            breaker_failure_threshold=1,
            breaker_cooldown_seconds=60.0,
        )
        r1 = await chain.generate("hello")  # OpenRouter fails once -> breaker opens
        assert r1.provider == "gemini"
        assert chain.breakers["openrouter"].state == "open"
        calls_after_first = or_route.call_count
        r2 = await chain.generate("hello again")  # circuit open -> skipped
        assert r2.provider == "gemini"
        assert or_route.call_count == calls_after_first  # not called again


def test_circuit_breaker_half_open_after_cooldown():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.0)
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert cb.state == "half-open"  # cooldown 0 => immediately probe-able
    assert cb.allow()
    cb.record_success()
    assert cb.state == "closed"


def test_build_llm_client_without_keys_returns_echo(test_settings):
    settings = get_settings()
    client = build_llm_client(settings, http_client=None)  # type: ignore[arg-type]
    assert isinstance(client, EchoLLMClient)


def test_build_llm_client_with_keys_builds_chain(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    get_settings.cache_clear()
    settings = get_settings()
    async_client = httpx.AsyncClient()
    try:
        client = build_llm_client(settings, async_client)
        assert isinstance(client, ResilientLLMClient)
        assert [p.provider for p in client.providers] == ["openrouter", "gemini"]
    finally:
        get_settings.cache_clear()


@respx.mock
def test_chat_endpoint_503_and_failed_record_when_all_providers_down():
    """End-to-end: /v1/chat returns the standardized 503 envelope and the
    interaction is persisted with status=failed (prompt never lost)."""
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(503))
    respx.post(GEMINI_URL).mock(side_effect=httpx.ConnectTimeout("down"))
    repo = InMemoryConversationRepository()
    async_client = httpx.AsyncClient()
    chain = make_chain(async_client, max_retries=0)
    client = make_client(chain, repo)
    with client:
        resp = client.post("/v1/chat", json={"user_id": "u1", "prompt": "hello"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "llm_unavailable"
    with client:
        history = client.get("/v1/conversations/u1").json()
    assert history["total"] == 1
    assert history["items"][0]["status"] == "failed"
    assert history["items"][0]["prompt"] == "hello"


@respx.mock
def test_chat_endpoint_success_via_openrouter():
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    repo = InMemoryConversationRepository()
    chain = make_chain(httpx.AsyncClient())
    client = make_client(chain, repo)
    with client:
        resp = client.post("/v1/chat", json={"user_id": "u1", "prompt": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openrouter"
    assert body["response"] == "or-answer"
    assert body["status"] == "completed"
    assert body["usage"]["total_tokens"] == 18


@respx.mock
async def test_web_search_adds_grounding_to_payloads():
    """Com web_search=True, OpenRouter recebe o plugin web e o Gemini a tool
    google_search; respostas grounded multi-part são concatenadas."""
    or_route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="test-or-key", web_search=True)
        await provider.generate("cotacao do dolar hoje")
    body = json.loads(or_route.calls[0].request.content)
    assert body["plugins"] == [{"id": "web"}]

    grounded = {
        "candidates": [{"content": {"parts": [{"text": "A cotacao "}, {"text": "e R$ 5,10."}]}}],
        "usageMetadata": {"totalTokenCount": 20},
    }
    gm_route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=grounded))
    async with httpx.AsyncClient() as http:
        provider = GeminiProvider(http, api_key="test-gm-key", web_search=True)
        result = await provider.generate("cotacao do dolar hoje")
    body = json.loads(gm_route.calls[0].request.content)
    assert body["tools"] == [{"google_search": {}}]
    assert result.text == "A cotacao e R$ 5,10."


async def test_web_search_disabled_by_default():
    """Sem a flag, os payloads não ganham plugins/tools (comportamento antigo)."""
    with respx.mock:
        or_route = respx.post(OPENROUTER_URL).mock(
            return_value=httpx.Response(200, json=OPENROUTER_OK)
        )
        async with httpx.AsyncClient() as http:
            await OpenRouterProvider(http, api_key="k").generate("oi")
        body = json.loads(or_route.calls[0].request.content)
        assert "plugins" not in body


@respx.mock
async def test_response_mode_controls_tokens_style_and_model_routing():
    """direct (padrão): modelo leve roteado, teto de tokens baixo e instrução
    de objetividade; detailed: modelo principal, teto maior e estilo com contexto."""
    route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(
            http,
            api_key="k",
            direct_model="meta-llama/llama-3.2-3b-instruct:free",
            direct_max_tokens=128,
            detailed_max_tokens=2048,
            system_prompt="Base.",
        )
        await provider.generate("qual a capital do Brasil?")  # mode default = direct
        await provider.generate("qual a capital do Brasil?", mode="detailed")

    direct_body = json.loads(route.calls[0].request.content)
    assert direct_body["model"] == "meta-llama/llama-3.2-3b-instruct:free"
    assert direct_body["max_tokens"] == 128
    assert "Omita os campos contexto" in direct_body["messages"][0]["content"]

    detailed_body = json.loads(route.calls[1].request.content)
    assert detailed_body["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert detailed_body["max_tokens"] == 2048
    assert "Preencha contexto" in detailed_body["messages"][0]["content"]


@respx.mock
async def test_gemini_mode_sets_generation_config():
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=GEMINI_OK))
    async with httpx.AsyncClient() as http:
        provider = GeminiProvider(http, api_key="k", direct_max_tokens=100)
        await provider.generate("oi")
    body = json.loads(route.calls[0].request.content)
    assert body["generationConfig"] == {"maxOutputTokens": 100}
    assert "Omita os campos contexto" in body["systemInstruction"]["parts"][0]["text"]


@respx.mock
async def test_system_prompt_inclui_data_atual_e_fonte_obrigatoria():
    """O modelo recebe a data de hoje (não infere de artigos velhos) e o
    contrato exige fonte em cada item de dados, com preferência oficial."""
    from datetime import datetime

    route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    async with httpx.AsyncClient() as http:
        await OpenRouterProvider(http, api_key="k", system_prompt="Base.").generate("oi")
    system = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert f"Hoje e {today}" in system
    assert 'SEMPRE preencha "fonte"' in system
    assert "Banco" in system


@respx.mock
async def test_content_nulo_vira_erro_permanente_com_denylist():
    """Regressão do 500 da demo: 200 com content null (modelos de reasoning)
    vira LLMPermanentError e denylista o modelo auto-selecionado."""

    class FakeSelector:
        def __init__(self):
            self.bad: list[tuple[str, str]] = []

        async def get_model(self, provider, mode):
            return "openai/gpt-oss-20b:free"

        def mark_bad(self, provider, model_id):
            self.bad.append((provider, model_id))

    null_content = {
        "model": "openai/gpt-oss-20b:free",
        "choices": [{"message": {"role": "assistant", "content": None}}],
    }
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=null_content))
    selector = FakeSelector()
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="k", model_selector=selector)  # type: ignore[arg-type]
        with pytest.raises(LLMPermanentError):
            await provider.generate("oi")
    assert selector.bad == [("openrouter", "openai/gpt-oss-20b:free")]


def test_parser_aceita_none_sem_quebrar():
    from app.services.formatting import parse_structured_answer

    out = parse_structured_answer(None)
    assert out.normalizada is False
    assert out.resposta == ""


@respx.mock
async def test_teto_estourado_por_thinking_repete_sem_teto_e_nao_denylista():
    """Regressão da demo: gemini flash com thinking gastou o teto (256) em
    raciocínio e voltou 200 sem parts (MAX_TOKENS). Deve repetir sem teto e
    NÃO denylistar (a culpa é do nosso limite, não do modelo)."""

    class FakeSelector:
        def __init__(self):
            self.bad: list[tuple[str, str]] = []

        async def get_model(self, provider, mode):
            return "gemini-2.0-flash"

        def mark_bad(self, provider, model_id):
            self.bad.append((provider, model_id))

    truncated = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}
    ok = {
        "candidates": [{"content": {"parts": [{"text": '{"resposta": "R$ 5,15."}'}]}}],
        "usageMetadata": {"totalTokenCount": 300},
    }
    route = respx.post(GEMINI_URL).mock(
        side_effect=[httpx.Response(200, json=truncated), httpx.Response(200, json=ok)]
    )
    selector = FakeSelector()
    async with httpx.AsyncClient() as http:
        provider = GeminiProvider(http, api_key="k", direct_max_tokens=256, model_selector=selector)  # type: ignore[arg-type]
        result = await provider.generate("cotacao?")
    assert route.call_count == 2
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["generationConfig"] == {"maxOutputTokens": 256}
    assert "generationConfig" not in second
    assert result.text == '{"resposta": "R$ 5,15."}'
    assert selector.bad == []  # modelo NÃO foi denylistado


@respx.mock
async def test_openrouter_length_repete_sem_teto():
    truncated = {
        "model": "m",
        "choices": [{"finish_reason": "length", "message": {"content": ""}}],
    }
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[httpx.Response(200, json=truncated), httpx.Response(200, json=OPENROUTER_OK)]
    )
    async with httpx.AsyncClient() as http:
        result = await OpenRouterProvider(http, api_key="k", direct_max_tokens=128).generate("oi")
    assert route.call_count == 2
    assert "max_tokens" not in json.loads(route.calls[1].request.content)
    assert result.text == "or-answer"


@respx.mock
async def test_contrato_exige_honestidade_temporal():
    """Mercado fechado/sem cotação do dia: o contrato exige dizer isso, dar o
    último valor com data de referência e o horário de abertura."""
    route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_OK))
    async with httpx.AsyncClient() as http:
        await OpenRouterProvider(http, api_key="k").generate("cotacao?")
    system = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert "MAIS RECENTE divulgado HOJE" in system
    assert "horario de abertura" in system
    assert "Nunca diga que o valor de hoje 'fechou'" in system


@respx.mock
async def test_texto_truncado_pelo_teto_repete_sem_teto():
    """Regressão da demo: modelo com reasoning foi cortado no teto no MEIO do
    raciocínio (texto parcial, finish_reason=length) — deve descartar o texto
    truncado e repetir sem teto."""
    truncated = {
        "model": "m",
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "The user is asking... I need to output JSON with fields:"},
            }
        ],
    }
    ok = {
        "model": "m",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"resposta": "R$ 5,15 (fechamento 07/07)."}'},
            }
        ],
        "usage": {"total_tokens": 900},
    }
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[httpx.Response(200, json=truncated), httpx.Response(200, json=ok)]
    )
    async with httpx.AsyncClient() as http:
        result = await OpenRouterProvider(http, api_key="k", direct_max_tokens=256).generate("oi")
    assert route.call_count == 2
    assert result.text == '{"resposta": "R$ 5,15 (fechamento 07/07)."}'


@respx.mock
async def test_memoria_de_truncagem_pula_a_chamada_capada():
    """Depois da 1ª truncagem, o provider aprende e vai direto sem teto —
    economiza uma ida ao provider por request (latência)."""
    truncated = {
        "model": "m",
        "choices": [{"finish_reason": "length", "message": {"content": "raciocinio..."}}],
    }
    ok = {
        "model": "m",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"resposta": "ok"}'}}],
    }
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[
            httpx.Response(200, json=truncated),
            httpx.Response(200, json=ok),
            httpx.Response(200, json=ok),
        ]
    )
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="k", direct_max_tokens=256)
        await provider.generate("oi")  # 1º request: capada + sem teto (2 chamadas)
        await provider.generate("oi")  # 2º request: direto sem teto (1 chamada)
    assert route.call_count == 3
    assert "max_tokens" not in json.loads(route.calls[2].request.content)


async def test_web_search_torna_gemini_o_provider_primario(monkeypatch):
    """Com grounding, o google_search nativo do Gemini é mais rápido e barato
    (1 chamada) que o plugin web do OpenRouter — a cadeia inverte a ordem."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    monkeypatch.setenv("LLM_AUTO_MODEL", "false")
    async with httpx.AsyncClient() as http:
        monkeypatch.setenv("LLM_WEB_SEARCH", "true")
        get_settings.cache_clear()
        grounded = build_llm_client(get_settings(), http)
        monkeypatch.setenv("LLM_WEB_SEARCH", "false")
        get_settings.cache_clear()
        plain = build_llm_client(get_settings(), http)
    get_settings.cache_clear()
    assert [p.provider for p in grounded.providers] == ["gemini", "openrouter"]
    assert [p.provider for p in plain.providers] == ["openrouter", "gemini"]
