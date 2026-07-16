"""Corpos JSON em cp1252/latin-1 (PowerShell 5.1, curl Windows) viram UTF-8.

Cenário real: `Invoke-RestMethod` do Windows PowerShell 5.1 envia o body
string em cp1252; um prompt com acento ("eleições") chegava como UTF-8
inválido e o FastAPI respondia 400 genérico antes do guardrail rodar.
"""


def _post_raw(client, body: bytes, content_type: str = "application/json"):
    return client.post("/v1/chat", content=body, headers={"Content-Type": content_type})


class TestUtf8BodyMiddleware:
    def test_prompt_bloqueado_com_acento_em_cp1252_vira_blocked(self, client, mock_llm):
        body = '{"user_id":"12345","prompt":"Em quem votar nas próximas eleições?"}'
        r = _post_raw(client, body.encode("cp1252"))
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"
        assert mock_llm.calls == []

    def test_prompt_no_escopo_com_acento_em_cp1252_roundtrip(self, client, mock_llm):
        body = '{"user_id":"u1","prompt":"Como está a cotação do dólar hoje?"}'
        r = _post_raw(client, body.encode("cp1252"))
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        # O texto com acentos chega íntegro ao serviço (e ao LLM).
        assert data["prompt"] == "Como está a cotação do dólar hoje?"
        assert mock_llm.calls[0]["prompt"] == "Como está a cotação do dólar hoje?"

    def test_charset_declarado_e_respeitado(self, client):
        body = '{"user_id":"u1","prompt":"Qual religião é a verdadeira?"}'
        r = _post_raw(
            client, body.encode("cp1252"), content_type="application/json; charset=windows-1252"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

    def test_utf8_valido_passa_intocado(self, client):
        body = '{"user_id":"u1","prompt":"Como está a cotação do dólar hoje?"}'
        r = _post_raw(client, body.encode("utf-8"))
        assert r.status_code == 200
        assert r.json()["prompt"] == "Como está a cotação do dólar hoje?"

    def test_json_invalido_continua_422(self, client):
        r = _post_raw(client, b'{"user_id": "u1", "prompt": ')
        assert r.status_code == 422

    def test_body_vazio_continua_422(self, client):
        r = _post_raw(client, b"")
        assert r.status_code == 422
