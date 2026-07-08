# Quadro de Missão — Fase 2 (Persistência PostgreSQL + Integração LLM)

Time: `teams/team-fase2-core.yml`. Builders em paralelo com propriedade de
arquivos particionada; validação cruzada na sequência.

## Status

| Agente | Papel | Rodada | Veredito/Estado |
|--------|-------|--------|-----------------|
| persistence | builder | 1 | ENTREGUE (46 passed, 4 skipped) |
| llm_integration | builder | 1 | ENTREGUE (46 passed, 4 skipped) |
| persistence → llm | validação cruzada | 1 | APROVADO |
| llm → persistence | validação cruzada | 1 | APROVADO |
| security_hardening | validator | 1 | APROVADO (3 notas não bloqueantes) |
| quality_ci | validator | 2 | APROVADO (higiene aplicada na Rodada 2) |

## Pendências herdadas da Fase 1
- latency_ms float no record vs coluna int (dono: persistence — decidir e alinhar).
- Timeout curto (~2s) no check llm_egress do /ready (dono: llm_integration).

## Registro de rodadas

### Rodada 1
- [persistence] Em execução — SQLAlchemy async + Alembic + docker-compose.
- [llm_integration] Em execução — OpenRouterProvider + GeminiProvider + resiliência.
- [persistence] ENTREGUE Rodada 1 — camada PostgreSQL completa (46 passed, 4 skipped):
  1. Modelo `interactions` (app/models/interaction.py): uuid pk, user_id indexado,
     prompt/response text, model/provider, enum nativo interaction_status
     (pending|completed|failed), error_detail, latency_ms INT, tokens, metadata JSONB,
     created_at/updated_at timestamptz + índice composto (user_id, created_at DESC).
  2. PENDÊNCIA RESOLVIDA latency float vs int: DECISÃO = coluna INTEGER (ms inteiros);
     record `latency_ms: int | None`; round() aplicado na borda de persistência nas
     DUAS implementações (in-memory e postgres) — comportamento idêntico.
  3. Engine/pool: app/repositories/database.py (create_async_engine com pool_size/
     max_overflow/timeout/recycle via env: DB_POOL_*; pool_pre_ping). Repositório real:
     app/repositories/postgres.py (PostgresConversationRepository, mesmo Protocol,
     sessão curta por operação, RecordNotFoundError preservado).
  4. Alembic: alembic.ini + migrations/env.py (async, DATABASE_URL sobrepõe ini) +
     migration inicial 0001 (versions/20260706_0001_create_interactions.py).
  5. Wiring: lifespan (app/main.py) cria engine+sessionmaker+repo quando
     REPOSITORY_BACKEND=postgres (padrão; "memory" p/ dev/testes) e dispose no shutdown;
     deps.py inalterado (resolve via app.state). /ready agora faz SELECT 1 real
     (check "database": ok|error|skipped).
  6. docker-compose.yml: postgres:16 (volume nomeado pgdata, healthcheck pg_isready),
     app aguarda service_healthy e roda `alembic upgrade head` antes do uvicorn.
     Dockerfile baseline criado (packaging/Agente 7 fará o multi-stage).
  7. Testes: tests/test_repository_inmemory.py (fluxos pending→completed/failed,
     rounding, paginação, not-found) e tests/test_repository_postgres.py
     (testcontainers + migration real; skip automático sem Docker — documentado).
     testcontainers[postgres] adicionado a requirements-dev.txt como opcional.
  Config compartilhada tocada cirurgicamente: config.py (REPOSITORY_BACKEND + DB_POOL_*),
  conftest.py (REPOSITORY_BACKEND=memory nos testes), .env.example (novas vars).
  pytest: 46 passed, 4 skipped (integração pg sem Docker nesta máquina). Sem commit.
- [llm_integration] ENTREGUE Rodada 1 — cliente LLM resiliente (46 passed, 4 skipped, sem rede):
  1. Providers (app/services/providers.py): OpenRouterProvider (POST
     {base_url}/chat/completions, Bearer via OPENROUTER_API_KEY) e GeminiProvider
     (generateContent v1beta, key via header x-goog-api-key — nunca na URL).
     Erros classificados: LLMTransientError (timeout/transporte/429/5xx) vs
     LLMPermanentError (4xx de validação, corpo malformado); corpos upstream
     nunca vazam nas exceções. LLMResult ganhou latency_ms opcional (aditivo);
     tokens (usage/usageMetadata) e modelo registrados em cada resultado.
  2. Resiliência (app/services/resilience.py): ResilientLLMClient com retry
     tenacity (backoff exponencial + jitter, máx. 2 retries, SÓ transientes),
     CircuitBreaker próprio por provider (abre após N falhas consecutivas,
     half-open pós-cooldown; permanentes não abrem o breaker), fallback
     OpenRouter→Gemini (model do cliente só vai ao primário; fallback usa
     default próprio) e LLMUnavailableError(AppError 503/llm_unavailable) quando
     a cadeia toda falha — endpoint já persiste status=failed antes do 503.
  3. Wiring: build_llm_client(settings, http_client) chamado no lifespan
     (app/main.py) — cadeia real com keys configuradas; EchoLLMClient mantido
     como default offline (dev/test sem keys). deps.py inalterado. Reuso do
     httpx.AsyncClient único do lifespan em tudo.
  4. PENDÊNCIA RESOLVIDA llm_egress: /ready (app/api/health.py) faz GET
     {openrouter_base_url}/models com timeout READY_CHECK_TIMEOUT_SECONDS (2s);
     "skipped" sem keys reais (testes nunca tocam rede), ok|error com keys.
  5. Config (app/core/config.py, cirúrgico): GEMINI_BASE_URL, LLM_MAX_RETRIES,
     LLM_RETRY_BACKOFF_{INITIAL,MAX}_SECONDS, LLM_BREAKER_FAILURE_THRESHOLD,
     LLM_BREAKER_COOLDOWN_SECONDS, READY_CHECK_TIMEOUT_SECONDS; .env.example
     atualizado. Deps: tenacity (runtime) + respx (dev) em requirements*/pyproject.
  6. Testes (tests/test_llm_client.py, respx, 13 novos): sucesso OpenRouter;
     429→retry→sucesso; 500→fallback Gemini; ambos caindo→LLMUnavailableError e
     end-to-end /v1/chat→503 llm_unavailable + registro failed no histórico;
     timeout→retries→fallback; 4xx sem retry; breaker abre/skipa e half-open;
     factory (sem keys→Echo, com keys→cadeia). ruff limpo. Sem commit.
  Suíte completa: pytest → 46 passed, 4 skipped (pg/testcontainers sem Docker),
  zero chamadas de rede.

### Rodada 2
- [quality_ci] Parecer Rodada 1: AJUSTES (higiene ruff nos arquivos novos;
  cobertura de cenários, skips e suíte offline todos OK — 46 passed, 4 skipped).
- [quality_ci] Rodada 2: ajustes aplicados pelo próprio validador (mandato do
  orquestrador, sem mudança de comportamento): import os removido de
  tests/conftest.py; import os inline movido ao topo em
  tests/test_repository_postgres.py; ruff format em 8 arquivos; UP042
  corrigido (InteractionStatus -> enum.StrEnum). Verificação: pytest 46 passed
  4 skipped; ruff format --check limpo; ruff check limpo exceto 4 B008
  pré-existentes em app/api/v1/chat.py (deferidos para a fase de CI).
  Parecer atualizado: shared/memory/fase2_reviews/quality.md — Rodada 2:
  Veredito APROVADO. Sem commit.

## Encerramento — FASE 2 CONCLUÍDA

Placar final: 4/4 APROVADO (validação cruzada persistence→llm e llm→persistence,
security_hardening, quality_ci) + pytest 46 passed, 4 skipped (integração pg sem
Docker), zero rede; ruff check/format limpos. Gate da Fase 3 (segurança +
observabilidade) liberado.
Pendências delegadas: bind 127.0.0.1 ou parametrização da porta/senha do postgres
no compose e Dockerfile non-root multi-stage (Agente 7); B008 em chat.py e decisão
de lint (Agente 6/CI); total_tokens não persistido e provider=None em failed
(avaliar na Fase 5/observabilidade); IDOR/413/API_KEY_HASH (Fase 4).
