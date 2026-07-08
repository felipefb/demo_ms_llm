"""Testes do parser de resposta estruturada (normalização p/ tabelas)."""

from app.services.formatting import parse_structured_answer


def test_json_puro_normalizado():
    out = parse_structured_answer(
        '{"resposta": "O dolar esta R$ 5,15.", "dados": [{"indicador": "USD/BRL",'
        ' "valor": "5,15", "fonte": "g1"}], "contexto": "Alta de 0,4%.",'
        ' "fontes": ["https://g1.globo.com"]}'
    )
    assert out.normalizada is True
    assert out.resposta == "O dolar esta R$ 5,15."
    assert out.dados == [{"indicador": "USD/BRL", "valor": "5,15", "fonte": "g1"}]
    assert out.contexto == "Alta de 0,4%."
    assert out.fontes == ["https://g1.globo.com"]


def test_json_com_cerca_de_codigo_e_prefixo():
    out = parse_structured_answer('Claro!\n```json\n{"resposta": "42."}\n```')
    assert out.normalizada is True
    assert out.resposta == "42."
    assert out.dados == []


def test_texto_cru_cai_no_fallback_seguro():
    out = parse_structured_answer("A cotacao do dolar hoje e R$ 5,15.")
    assert out.normalizada is False
    assert out.resposta == "A cotacao do dolar hoje e R$ 5,15."


def test_json_sem_resposta_cai_no_fallback():
    out = parse_structured_answer('{"dados": []}')
    assert out.normalizada is False


def test_itens_invalidos_em_dados_sao_ignorados():
    out = parse_structured_answer(
        '{"resposta": "ok", "dados": ["string-solta", {"indicador": "x", "valor": 1}, {}]}'
    )
    assert out.dados == [{"indicador": "x", "valor": "1"}]
