"""Guardrail de escopo temático: unidade (TopicGuardrail) + endpoint /v1/chat."""

from app.core.config import get_settings
from app.services.guardrail import (
    SCOPE_SENTINEL,
    TopicGuardrail,
    build_system_prompt,
    is_out_of_scope_response,
)
from tests.conftest import MockLLMClient, make_client


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


class SentinelLLM(MockLLMClient):
    """Simula o modelo sinalizando tema fora do escopo (camada 2)."""

    async def generate(self, prompt, model=None, mode="direct"):
        result = await super().generate(prompt, model=model, mode=mode)
        result.text = f'{{"resposta": "{SCOPE_SENTINEL}"}}'
        return result


class TestEscopoPositivo:
    OFF_TOPIC = "Como está o tempo agora em SP?"

    def test_system_prompt_declara_escopo_e_sentinela(self, test_settings):
        prompt = build_system_prompt(get_settings())
        assert "ESCOPO DO SERVICO" in prompt
        assert SCOPE_SENTINEL in prompt
        assert "econômico-financeiros" in prompt

    def test_system_prompt_sem_escopo_quando_desabilitado(self, monkeypatch):
        monkeypatch.setenv("GUARDRAIL_SCOPE_ENABLED", "false")
        get_settings.cache_clear()
        assert SCOPE_SENTINEL not in build_system_prompt(get_settings())

    def test_deteccao_da_sentinela_em_json_e_texto_cru(self):
        assert is_out_of_scope_response(f'{{"resposta": "{SCOPE_SENTINEL}"}}')
        assert is_out_of_scope_response(f"  {SCOPE_SENTINEL}. ")
        assert not is_out_of_scope_response("A PTAX fechou em R$ 5,07.")

    def test_sentinela_do_llm_vira_blocked_auditavel(self, repo):
        client = make_client(SentinelLLM(), repo)
        r = client.post("/v1/chat", json={"user_id": "u-fora", "prompt": self.OFF_TOPIC})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "blocked"
        assert body["provider"] == "guardrail"
        assert "fora do escopo" in body["response"]
        assert "fora_do_escopo" in body["structured"]["contexto"]
        # Tokens gastos na sinalização ficam expostos (transparência de custo).
        assert body["usage"]["total_tokens"] == 8
        record = next(iter(repo._items.values()))
        assert record.status == "blocked"
        assert "sinalizado pelo LLM" in record.error_detail
        metrics = client.get("/metrics").text
        assert 'guardrail_blocked_total{category="fora_do_escopo"}' in metrics

    def test_escopo_desabilitado_devolve_resposta_do_llm(self, repo, monkeypatch):
        monkeypatch.setenv("GUARDRAIL_SCOPE_ENABLED", "false")
        get_settings.cache_clear()
        client = make_client(SentinelLLM(), repo)
        r = client.post("/v1/chat", json={"user_id": "u1", "prompt": self.OFF_TOPIC})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
