"""Guardrail de escopo temático: unidade (TopicGuardrail) + endpoint /v1/chat."""

from app.core.config import get_settings
from app.services.guardrail import TopicGuardrail


class TestTopicGuardrail:
    def test_detecta_politica(self):
        match = TopicGuardrail().check("Em quem devo votar na eleicao para presidente?")
        assert match is not None
        assert match.category == "politica"

    def test_detecta_religiao_com_acentos_e_maiusculas(self):
        match = TopicGuardrail().check("Qual é a melhor RELIGIÃO do mundo?")
        assert match is not None
        assert match.category == "religiao"

    def test_politica_monetaria_esta_no_escopo(self):
        assert TopicGuardrail().check("Explique a política monetária do Banco Central") is None

    def test_politica_de_precos_esta_no_escopo(self):
        assert TopicGuardrail().check("Qual a política de preços da Petrobras?") is None

    def test_cotacao_do_dolar_passa(self):
        assert TopicGuardrail().check("Como está a cotação do dólar hoje?") is None

    def test_desabilitado_nao_bloqueia(self):
        assert TopicGuardrail(enabled=False).check("qual a melhor religião?") is None

    def test_categorias_customizadas_substituem_o_padrao(self):
        guardrail = TopicGuardrail(topics={"futebol": [r"\bfutebol\b"]})
        blocked = guardrail.check("Quem ganha o clássico de futebol?")
        assert blocked is not None
        assert blocked.category == "futebol"
        # O override substitui as categorias padrão por completo.
        assert guardrail.check("qual a melhor religião?") is None


class TestChatGuardrailEndpoint:
    BLOCKED_PROMPT = "Qual religião é a verdadeira?"

    def test_tema_fora_do_escopo_retorna_blocked_sem_chamar_llm(self, client, mock_llm, repo):
        r = client.post("/v1/chat", json={"user_id": "u1", "prompt": self.BLOCKED_PROMPT})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "blocked"
        assert body["provider"] == "guardrail"
        assert body["model"] is None
        assert body["usage"] is None
        assert "fora do escopo" in body["response"]
        assert "religiao" in body["structured"]["contexto"]
        # O LLM nunca foi invocado — zero tokens gastos.
        assert mock_llm.calls == []

    def test_tentativa_bloqueada_fica_auditavel_no_repositorio(self, client, repo):
        client.post("/v1/chat", json={"user_id": "u-audit", "prompt": self.BLOCKED_PROMPT})
        record = next(iter(repo._items.values()))
        assert record.status == "blocked"
        assert record.error_detail is not None and "guardrail" in record.error_detail
        assert record.provider == "guardrail"

    def test_bloqueio_incrementa_metrica_por_categoria(self, client):
        client.post("/v1/chat", json={"user_id": "u1", "prompt": "vamos falar de política?"})
        metrics = client.get("/metrics").text
        assert 'guardrail_blocked_total{category="politica"}' in metrics

    def test_bloqueio_aparece_no_historico_de_conversas(self, client):
        client.post("/v1/chat", json={"user_id": "u-hist", "prompt": self.BLOCKED_PROMPT})
        r = client.get("/v1/conversations/u-hist")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "blocked"

    def test_guardrail_desligado_por_env_passa_ao_llm(self, client, mock_llm, monkeypatch):
        monkeypatch.setenv("GUARDRAIL_ENABLED", "false")
        get_settings.cache_clear()
        r = client.post("/v1/chat", json={"user_id": "u1", "prompt": self.BLOCKED_PROMPT})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert len(mock_llm.calls) == 1

    def test_prompt_dentro_do_escopo_nao_e_afetado(self, client, mock_llm):
        r = client.post(
            "/v1/chat",
            json={"user_id": "u1", "prompt": "Como está a cotação do dólar hoje?"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        assert len(mock_llm.calls) == 1
