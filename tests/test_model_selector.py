"""Seleção automática de modelos — totalmente offline via respx.

Cobre: ranking OpenRouter (direct = menor free elegível; detailed = maior
free), ranking Gemini (flash-lite vs flash), TTL expirado dispara re-consulta,
catálogo fora do ar cai no default do env sem erro, e LLM_AUTO_MODEL=false
preserva o comportamento antigo (modelos manuais do env).
"""

import json

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.services.model_selector import (
    ModelSelector,
    parse_gemini_catalog,
    parse_openrouter_catalog,
    select_gemini_model,
    select_openrouter_model,
)
from app.services.providers import OpenRouterProvider
from app.services.resilience import ResilientLLMClient, build_llm_client

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

OPENROUTER_CATALOG = {
    "data": [
        {
            "id": "meta-llama/llama-3.2-3b-instruct:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 131072,
            "top_provider": {"max_completion_tokens": 4096},
        },
        {
            "id": "qwen/qwen-2.5-72b-instruct:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 32768,
            "top_provider": {"max_completion_tokens": 8192},
        },
        {
            # Pago: nunca deve vencer enquanto houver free elegível.
            "id": "openai/gpt-4o",
            "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            "context_length": 128000,
            "top_provider": {"max_completion_tokens": 16384},
        },
        {
            # Free porém experimental: excluído enquanto houver estável.
            "id": "big-lab/mega-999b-exp:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 131072,
            "top_provider": {"max_completion_tokens": 8192},
        },
        {
            # Free mas contexto pequeno demais (< 8k): inelegível.
            "id": "tiny/nano-1b:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 4096,
            "top_provider": {"max_completion_tokens": 4096},
        },
    ]
}

GEMINI_CATALOG = {
    "models": [
        {
            "name": "models/gemini-2.0-flash",
            "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 8192,
        },
        {
            "name": "models/gemini-2.0-flash-lite",
            "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 8192,
        },
        {
            "name": "models/gemini-2.5-flash",
            "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
        },
        {
            # Preview: evitado enquanto houver estável.
            "name": "models/gemini-3.0-flash-preview",
            "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
        },
        {
            # Não gera conteúdo: inelegível.
            "name": "models/gemini-embedding-001",
            "supportedGenerationMethods": ["embedContent"],
            "inputTokenLimit": 2048,
            "outputTokenLimit": 1,
        },
    ]
}


def make_selector(http: httpx.AsyncClient, **kwargs) -> ModelSelector:
    kwargs.setdefault("openrouter_api_key", "or-key")
    kwargs.setdefault("gemini_api_key", "gm-key")
    return ModelSelector(http, **kwargs)


@respx.mock
async def test_openrouter_direct_smallest_free_and_detailed_largest_free():
    respx.get(OPENROUTER_MODELS_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_CATALOG))
    async with httpx.AsyncClient() as http:
        selector = make_selector(http)
        assert (
            await selector.get_model("openrouter", "direct")
            == "meta-llama/llama-3.2-3b-instruct:free"
        )
        assert (
            await selector.get_model("openrouter", "detailed") == "qwen/qwen-2.5-72b-instruct:free"
        )


@respx.mock
async def test_gemini_direct_flash_lite_and_detailed_newest_flash():
    route = respx.get(GEMINI_MODELS_URL).mock(return_value=httpx.Response(200, json=GEMINI_CATALOG))
    async with httpx.AsyncClient() as http:
        selector = make_selector(http)
        assert await selector.get_model("gemini", "direct") == "gemini-2.0-flash-lite"
        # detailed: flash não-lite mais novo, ignorando o preview.
        assert await selector.get_model("gemini", "detailed") == "gemini-2.5-flash"
    # Key vai no header, nunca na URL; um único fetch cobre os dois modos.
    assert route.call_count == 1
    assert route.calls[0].request.headers["x-goog-api-key"] == "gm-key"
    assert "key=" not in str(route.calls[0].request.url)


@respx.mock
async def test_ttl_expirado_dispara_reconsulta():
    route = respx.get(OPENROUTER_MODELS_URL).mock(
        return_value=httpx.Response(200, json=OPENROUTER_CATALOG)
    )
    async with httpx.AsyncClient() as http:
        selector = make_selector(http, refresh_seconds=0.0)  # sempre expirado
        await selector.get_model("openrouter", "direct")
        await selector.get_model("openrouter", "direct")
    assert route.call_count == 2


@respx.mock
async def test_ttl_valido_usa_cache():
    route = respx.get(OPENROUTER_MODELS_URL).mock(
        return_value=httpx.Response(200, json=OPENROUTER_CATALOG)
    )
    async with httpx.AsyncClient() as http:
        selector = make_selector(http, refresh_seconds=3600.0)
        await selector.get_model("openrouter", "direct")
        await selector.get_model("openrouter", "detailed")
    assert route.call_count == 1


@respx.mock
async def test_catalogo_fora_mantem_ultima_selecao_boa():
    route = respx.get(OPENROUTER_MODELS_URL).mock(
        side_effect=[
            httpx.Response(200, json=OPENROUTER_CATALOG),
            httpx.Response(503),
        ]
    )
    async with httpx.AsyncClient() as http:
        selector = make_selector(http, refresh_seconds=0.0)
        first = await selector.get_model("openrouter", "direct")
        second = await selector.get_model("openrouter", "direct")  # fetch falha
    assert route.call_count == 2
    assert first == second == "meta-llama/llama-3.2-3b-instruct:free"


@respx.mock
async def test_catalogo_fora_sem_selecao_usa_default_do_env():
    """Sem seleção alguma, o provider cai no default_model — request não quebra."""
    respx.get(OPENROUTER_MODELS_URL).mock(side_effect=httpx.ConnectTimeout("down"))
    chat = respx.post(OPENROUTER_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "model": "x"},
        )
    )
    async with httpx.AsyncClient() as http:
        selector = make_selector(http)
        provider = OpenRouterProvider(
            http,
            api_key="or-key",
            default_model="env-default-model",
            model_selector=selector,
        )
        result = await provider.generate("oi")
    assert result.text == "ok"
    body = json.loads(chat.calls[0].request.content)
    assert body["model"] == "env-default-model"


@respx.mock
async def test_selecao_automatica_roteia_o_modelo_do_provider():
    respx.get(OPENROUTER_MODELS_URL).mock(return_value=httpx.Response(200, json=OPENROUTER_CATALOG))
    chat = respx.post(OPENROUTER_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "model": "x"},
        )
    )
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="or-key", model_selector=make_selector(http))
        await provider.generate("oi")  # direct
        await provider.generate("oi", mode="detailed")
        # Override manual explícito continua vencendo a seleção automática.
        provider_manual = OpenRouterProvider(
            http,
            api_key="or-key",
            direct_model="manual-direct-model",
            model_selector=make_selector(http),
        )
        await provider_manual.generate("oi")
    bodies = [json.loads(c.request.content) for c in chat.calls]
    assert bodies[0]["model"] == "meta-llama/llama-3.2-3b-instruct:free"
    assert bodies[1]["model"] == "qwen/qwen-2.5-72b-instruct:free"
    assert bodies[2]["model"] == "manual-direct-model"


@pytest.mark.parametrize("auto", ["true", "false"])
def test_build_llm_client_flag_llm_auto_model(monkeypatch, auto):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    monkeypatch.setenv("LLM_AUTO_MODEL", auto)
    get_settings.cache_clear()
    settings = get_settings()
    client = build_llm_client(settings, httpx.AsyncClient())
    assert isinstance(client, ResilientLLMClient)
    if auto == "true":
        assert client.model_selector is not None
        assert all(p._selector is client.model_selector for p in client.providers)
    else:
        # Comportamento antigo preservado: nenhum selector, modelos do env.
        assert client.model_selector is None
        assert all(p._selector is None for p in client.providers)
    get_settings.cache_clear()


@respx.mock
async def test_flag_false_usa_modelo_do_env(monkeypatch):
    """LLM_AUTO_MODEL=false: nenhuma chamada de catálogo, modelo do env é usado."""
    catalog = respx.get(OPENROUTER_MODELS_URL).mock(
        return_value=httpx.Response(200, json=OPENROUTER_CATALOG)
    )
    chat = respx.post(OPENROUTER_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "model": "x"},
        )
    )
    async with httpx.AsyncClient() as http:
        provider = OpenRouterProvider(http, api_key="or-key", default_model="env-model")
        await provider.generate("oi")
    assert catalog.call_count == 0
    assert json.loads(chat.calls[0].request.content)["model"] == "env-model"


def test_heuristica_exclui_modelos_nao_texto_e_minisculos():
    """Regressão dos casos reais da demo: variante -image do Gemini (400 para
    texto) e modelo 1.2b "thinking" no direct do OpenRouter (502/qualidade)."""
    gem = parse_gemini_catalog(
        {
            "models": [
                {
                    "name": "models/gemini-3.1-flash-lite-image",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 32768,
                    "outputTokenLimit": 8192,
                },
                {
                    "name": "models/gemini-3.1-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 32768,
                    "outputTokenLimit": 8192,
                },
            ]
        }
    )
    assert select_gemini_model(gem, "direct", 256) == "gemini-3.1-flash-lite"

    opr = parse_openrouter_catalog(
        {
            "data": [
                {
                    "id": "liquid/lfm-2.5-1.2b-thinking:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 32768,
                    "top_provider": {"max_completion_tokens": 4096},
                },
                {
                    "id": "meta-llama/llama-3.2-3b-instruct:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 131072,
                    "top_provider": {"max_completion_tokens": 4096},
                },
                {
                    "id": "some/image-gen:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 32768,
                    "architecture": {"modality": "text->image"},
                    "top_provider": {},
                },
            ]
        }
    )
    assert select_openrouter_model(opr, "direct", 256) == "meta-llama/llama-3.2-3b-instruct:free"


async def test_mark_bad_denylista_e_forca_reselecao(monkeypatch):
    """Auto-cura: modelo com erro permanente sai da seleção e a re-escolha
    acontece no request seguinte."""
    import httpx as _httpx
    import respx as _respx

    catalog_v1 = {
        "models": [
            {
                "name": "models/gemini-3.1-flash-lite",
                "supportedGenerationMethods": ["generateContent"],
                "inputTokenLimit": 32768,
                "outputTokenLimit": 8192,
            },
            {
                "name": "models/gemini-3.5-flash",
                "supportedGenerationMethods": ["generateContent"],
                "inputTokenLimit": 1048576,
                "outputTokenLimit": 65536,
            },
        ]
    }
    with _respx.mock:
        route = _respx.get(GEMINI_MODELS_URL).mock(
            return_value=_httpx.Response(200, json=catalog_v1)
        )
        async with _httpx.AsyncClient() as http:
            selector = ModelSelector(http, gemini_api_key="k", refresh_seconds=3600)
            assert await selector.get_model("gemini", "direct") == "gemini-3.1-flash-lite"
            selector.mark_bad("gemini", "gemini-3.1-flash-lite")
            # denylist + cache invalidado: re-consulta e escolhe outro modelo
            assert await selector.get_model("gemini", "direct") == "gemini-3.5-flash"
            assert route.call_count == 2


def test_grounding_eleva_o_piso_de_qualidade_do_direct():
    """Com busca web ligada, o direct exige síntese: Gemini sai do -lite e o
    OpenRouter escolhe o modelo mais capaz do tier free."""
    gem = parse_gemini_catalog(
        {
            "models": [
                {
                    "name": "models/gemini-3.1-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 32768,
                    "outputTokenLimit": 8192,
                },
                {
                    "name": "models/gemini-3.5-flash",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536,
                },
            ]
        }
    )
    assert select_gemini_model(gem, "direct", 256) == "gemini-3.1-flash-lite"
    assert select_gemini_model(gem, "direct", 256, grounded=True) == "gemini-3.5-flash"

    opr = parse_openrouter_catalog(
        {
            "data": [
                {
                    "id": "meta-llama/llama-3.2-3b-instruct:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 131072,
                    "top_provider": {"max_completion_tokens": 4096},
                },
                {
                    "id": "meta-llama/llama-3.3-70b-instruct:free",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 131072,
                    "top_provider": {"max_completion_tokens": 4096},
                },
            ]
        }
    )
    assert select_openrouter_model(opr, "direct", 256) == "meta-llama/llama-3.2-3b-instruct:free"
    assert (
        select_openrouter_model(opr, "direct", 256, grounded=True)
        == "meta-llama/llama-3.3-70b-instruct:free"
    )
