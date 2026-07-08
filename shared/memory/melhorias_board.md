# Quadro de Missão — Melhorias pós-entrega

Melhorias incrementais sobre a entrega das Fases 1–7, uma por rodada, sempre
com a suíte offline verde (ruff + mypy + pytest sem rede).

## Status

| Melhoria | Rodada | Veredito/Estado |
|----------|--------|-----------------|
| Seleção automática de modelos (LLM_AUTO_MODEL) | 1 | ENTREGUE (91 passed, 4 skipped; ruff/mypy limpos) |

## Registro de rodadas

### Rodada 1 — Seleção automática de modelos
- [llm_integration] **ENTREGUE** — 2026-07-07:
  - Novo `app/services/model_selector.py`: `ModelSelector` consulta os catálogos
    (OpenRouter `GET /models`; Gemini `GET /models` com key no header
    `x-goog-api-key`) e escolhe o melhor modelo por provider/modo. Heurística
    documentada no docstring do módulo: elegibilidade (chat/generateContent,
    outputTokenLimit >= teto do modo, contexto >= 8k — margem p/ prompt de
    4000 chars), free primeiro / menor preço, direct = menor modelo elegível
    (3b, flash-lite), detailed = mais capaz do tier free (72b, flash) com
    desempate por versão mais recente; exp/preview/beta só se não houver
    estável.
  - Cache com TTL (`LLM_MODEL_REFRESH_SECONDS`, default 3600): seleção no
    startup (lifespan em `app/main.py`, best-effort) e re-seleção lazy no
    primeiro request após expirar, com `asyncio.Lock` (sem estouro). Falha no
    catálogo mantém a última seleção boa; fallback final =
    `OPENROUTER_MODEL`/`GEMINI_MODEL` do env. Nunca derruba o request.
  - Flag `LLM_AUTO_MODEL` (default true) em `app/core/config.py`; false =
    comportamento antigo. Precedência em `app/services/providers.py`
    (`_resolve_model`): modelo do cliente (allowlist) > override manual
    (`*_MODEL_DIRECT`) > seleção automática > default env.
  - Wiring em `app/services/resilience.py` (`build_llm_client` cria o selector
    compartilhado e o injeta nos dois providers; exposto como
    `client.model_selector` p/ o lifespan).
  - Observabilidade: log estruturado a cada (re)seleção
    (`model_selector provider=... mode=... model=... reason=...`) e gauge
    `llm_selected_model{provider,mode,model}=1` em `app/core/metrics.py`
    (`set_selected_model` zera a série do modelo anterior ao trocar).
  - Config/env: `.env.example` com bloco comentado explicando a heurística;
    `docker-compose.yml` repassa `LLM_AUTO_MODEL` e
    `LLM_MODEL_REFRESH_SECONDS`; parágrafo na seção de configuração do README.
  - Testes offline (respx) em `tests/test_model_selector.py` (9 testes):
    ranking OpenRouter (direct menor free / detailed maior free; pago e
    exp excluídos), ranking Gemini (flash-lite vs flash mais novo; preview e
    embedding excluídos), TTL expirado re-consulta / TTL válido usa cache,
    catálogo fora mantém última seleção ou cai no default do env sem erro,
    `LLM_AUTO_MODEL=false` preserva o comportamento antigo, e override manual
    vencendo a seleção.
  - Gates: `ruff check` + `ruff format --check` OK, `mypy app` OK
    (strict-ish em services), `pytest -q` = 91 passed, 4 skipped, sem rede.

- [orquestrador] VALIDAÇÃO (inline; validadores indisponíveis por limite de créditos):
  APROVADO — gates verdes (91 passed/4 skipped offline; ruff check+format, mypy,
  bandit limpos); keys de catálogo só em headers; corpo do catálogo não logado;
  refresh com asyncio.Lock; precedência cliente>override>auto>default conferida
  no código e nos testes; gauge llm_selected_model remove série anterior.
