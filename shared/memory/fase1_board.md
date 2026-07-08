# Quadro de Missão — Fase 1 (Scaffold do serviço e API REST)

Time: `teams/team-fase1-scaffold.yml`. Cada agente registra suas entregas e
vereditos aqui. A fase encerra quando todos os validadores marcarem APROVADO.

## Status

| Agente | Papel | Rodada | Veredito |
|--------|-------|--------|----------|
| api_scaffold | builder | 1 | ENTREGUE (13 testes verdes) |
| persistence | validator | 2 | APROVADO |
| llm_integration | validator | 2 | APROVADO |
| security_hardening | validator | 2 | APROVADO |
| app_observability | validator | 1 | APROVADO |
| quality_ci | validator | 1 | APROVADO |

## Registro de rodadas

### Rodada 1
- [api_scaffold] ENTREGUE: scaffold completo (app/, tests/, docs/, migrations/), POST /v1/chat,
  GET /health, /ready, /v1/conversations/{user_id}, envelope de erro único, middleware
  X-Request-ID, OpenAPI em /docs, 13 testes de contrato verdes, LLM mockado (EchoLLMClient),
  repositório in-memory atrás de protocolo. Python local 3.11.9 (requires-python >=3.11).
- Validadores despachados em paralelo; pareceres em shared/memory/fase1_reviews/<agente>.md.
- Placar Rodada 1: APROVADO ×2 (app_observability, quality_ci); AJUSTES ×3
  (persistence: 5 itens; llm_integration: 4 itens; security_hardening: 7 itens, 3 bloqueantes).
- Temas convergentes: fluxo pending→completed/failed no repositório (gravar prompt ANTES do LLM),
  campos status/provider/latency/tokens no modelo e schemas, lifespan + app.state para
  httpx.AsyncClient (fim dos singletons em deps.py), extra="forbid" + limites em metadata,
  fail-fast de config, provider no LLMResult.

### Rodada 2
- [api_scaffold] Despachado para corrigir os 3 pareceres de AJUSTES; aprovados
  (app_observability, quality_ci) não exigem mudanças, mas suas observações não
  bloqueantes de baixo custo podem ser incorporadas (strip no prompt, X-Response-Time-Ms).
- [api_scaffold] ENTREGUE Rodada 2 — todos os itens dos 3 pareceres corrigidos (29 testes verdes):
  1. Repositório: `save()` substituído por `create_pending()/mark_completed()/mark_failed()`;
     endpoint grava o prompt ANTES do LLM e atualiza depois; falha do LLM => status=failed
     persistido + 503 `llm_unavailable` (prompt nunca se perde). `RecordNotFoundError` adicionado.
  2. `ConversationRecord`: status/provider/error_detail/latency_ms/prompt_tokens/
     completion_tokens/created_at/updated_at; `response: str | None`.
  3. Schemas: `ChatResponse` e `ConversationItem` expõem status/provider; response opcional;
     `extra="forbid"` em ChatRequest/TokenUsage; metadata limitado (dict[str,str], ≤20 chaves,
     ≤4096 bytes serializados); prompt com strip + rejeição de whitespace-only.
  4. `LLMResult.provider` adicionado (echo/mock/openrouter/gemini); latency_ms medido flui
     ao repositório via mark_completed/mark_failed.
  5. `create_app()`: lifespan cria `httpx.AsyncClient` único em `app.state.http_client`
     (fechado no shutdown); repository/llm_client resolvidos via `request.app.state`
     (deps.py sem singletons de import).
  6. Config: fail-fast (app_env != dev/test ou REQUIRE_LLM_KEYS=true exige keys reais,
     "changeme" conta como ausente); `ALLOWED_MODELS` (allowlist; vazio = cliente não
     sobrescreve o model => 422 `model_not_allowed`); docs/redoc/openapi desabilitados
     em prod (`ENABLE_DOCS` + APP_ENV).
  7. Handler de HTTPException padroniza mensagem por status (não vaza `detail` interno);
     header `X-Response-Time-Ms` no middleware.
  Testes novos: pending→completed, pending→failed (LLM mockado falhando), extra field,
  metadata gigante/aninhado, whitespace prompt, allowlist de model, fail-fast de config,
  docs em prod, histórico com failed. `.env.example` atualizado (REQUIRE_LLM_KEYS,
  ENABLE_DOCS, ALLOWED_MODELS, gemini-2.0-flash). pytest: 29 passed.

## Encerramento — FASE 1 CONCLUÍDA

Rodada 2: persistence APROVADO, llm_integration APROVADO, security_hardening APROVADO
(re-verificação item a item; suite 29 passed sem rede em todas as revisões).
Placar final: 5/5 APROVADO + pytest verde. Gate da Fase 2 liberado.
Pendências delegadas às fases seguintes registradas nos pareceres
(shared/memory/fase1_reviews/): latency float vs int (Fase 2), IDOR no histórico,
body limit 413 e API_KEY_HASH no fail-fast (Fase 4), middleware ASGI puro e
structlog JSON (Fase 5).
