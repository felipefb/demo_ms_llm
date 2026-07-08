"""Testes do cache de respostas com TTL (performance com custo controlado)."""

import time

from app.services.cache import ResponseCache
from app.services.llm import LLMResult


def _result(text: str = "ok") -> LLMResult:
    return LLMResult(text=text, model="m", provider="p")


def test_hit_dentro_do_ttl():
    cache = ResponseCache(ttl_seconds=60)
    cache.put("prompt", "direct", None, _result())
    assert cache.get("prompt", "direct", None) is not None
    # modo diferente = chave diferente
    assert cache.get("prompt", "detailed", None) is None


def test_expira_apos_o_ttl(monkeypatch):
    cache = ResponseCache(ttl_seconds=0.01)
    cache.put("prompt", "direct", None, _result())
    time.sleep(0.02)
    assert cache.get("prompt", "direct", None) is None


def test_desligado_com_ttl_zero():
    cache = ResponseCache(ttl_seconds=0)
    cache.put("prompt", "direct", None, _result())
    assert cache.get("prompt", "direct", None) is None


def test_endpoint_usa_cache_e_nao_chama_o_llm(client, mock_llm):
    body = {"user_id": "u-cache", "prompt": "pergunta repetida"}
    r1 = client.post("/v1/chat", json=body)
    r2 = client.post("/v1/chat", json=body)
    assert r1.status_code == r2.status_code == 200
    assert len(mock_llm.calls) == 1  # segunda resposta veio do cache
    assert r1.json()["response"] == r2.json()["response"]
    # ambas persistidas para analytics (2 interações no histórico)
    hist = client.get("/v1/conversations/u-cache").json()
    assert hist["total"] == 2
