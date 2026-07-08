# Parecer — Qualidade e Testes (Fase 1, Rodada 1)

Revisor: Agente 6 (quality_ci) — 2026-07-06

## Veredito: APROVADO

## Evidências verificadas

- Suíte verde: `.venv\Scripts\python -m pytest` → **13 passed** em 0.18s (1 warning
  de deprecação do starlette.testclient, inofensivo).
- Contrato coberto em `tests/test_chat_contract.py`:
  - payload válido (200 + contrato completo: id UUID, user_id, prompt, response,
    model, usage, timestamp, latency_ms);
  - payload inválido/campos faltantes → 422 com envelope de erro e `details`;
  - prompt vazio → 422; prompt > 4000 chars → 422;
  - override de modelo, X-Request-ID (eco e geração), histórico paginado,
    usuário sem histórico, 404 com envelope.
- Nenhuma chamada externa em testes: `MockLLMClient` totalmente offline em
  `conftest.py`; `EchoLLMClient` padrão também não usa rede; nenhum uso de
  httpx/requests em `app/` hoje.
- Fixtures centralizadas em `tests/conftest.py` (`mock_llm`, `client`), com
  override via `app.dependency_overrides` — DI correta.
- Testabilidade: `LLMClient` e `ConversationRepository` são `Protocol`s;
  API depende só das abstrações (`app/api/deps.py`). Boa base para o Postgres
  e o cliente real entrarem sem tocar nos testes de contrato.
- Código limpo: docstrings nos módulos públicos, tipagem razoável, sem código
  morto. TODOs em `app/api/health.py` são intencionais (marcam trabalho das
  fases persistence/llm), não pendências esquecidas.
- `pyproject.toml` já traz pytest/ruff/mypy configurados (asyncio_mode=auto,
  ruff E/F/I/UP/B, mypy não-strict).

## Observações (não bloqueantes — tratar na Fase de qualidade/CI)

1. `deps.py` usa singletons de módulo (`_repository`, `_llm_client`); ao entrar
   o Postgres, migrar para lifespan/app.state para evitar estado global.
2. `prompt` com apenas espaços ("   ") passa na validação `min_length=1`;
   considerar `strip` + validação de whitespace-only (adicionar teste).
3. venv está em Python 3.11 e `target-version = "py311"`, mas o contexto base
   pede 3.12 — alinhar quando configurar CI.
4. Falta pytest-cov, Makefile e workflow de CI — escopo da minha fase, não do
   scaffold.
5. Warning de deprecação do TestClient (httpx/starlette) — monitorar em upgrade
   de dependências.
