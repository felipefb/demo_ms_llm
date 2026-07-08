# Parecer — validador persistence sobre a entrega do builder llm_integration (Fase 2, Rodada 1)

Revisor: persistence (validação cruzada, ótica de persistência/dados)
Arquivos revisados: app/services/providers.py, app/services/resilience.py,
app/services/llm.py, app/main.py, app/api/health.py, app/api/v1/chat.py (fluxo),
app/repositories/{conversations,postgres}.py (bordas).

## Veredito: APROVADO

## Verificações

1. **Prompt nunca se perde / mark_failed com error_detail** — OK.
   /v1/chat grava via `create_pending()` ANTES de `llm.generate()`. Qualquer exceção
   (incl. `LLMUnavailableError` da cadeia toda caindo) cai no `except` que chama
   `repo.mark_failed(record.id, error_detail=f"{type(exc).__name__}: {exc}", ...)`
   antes de re-lançar 503. Confirmado por teste end-to-end
   (503 llm_unavailable + registro failed no histórico) na suíte do builder.

2. **Sem vazamento de dados sensíveis em error_detail** — OK.
   `_classify_http_error` gera apenas "HTTP <status> from upstream"; corpos upstream
   e API keys nunca entram nas mensagens de exceção, logo nunca chegam à coluna
   `error_detail`. Gemini key vai em header (x-goog-api-key), não em URL.

3. **provider/model/tokens fluem ao repositório** — OK.
   `LLMResult` carrega provider/model/prompt_tokens/completion_tokens de ambos os
   providers (usage / usageMetadata) e o endpoint repassa a `mark_completed()`.
   No fallback, `result.provider`/`result.model` refletem o provider que realmente
   respondeu (Gemini usa seu default; model do cliente só vai ao primário) —
   semântica correta para análises futuras.

4. **latency_ms float vs coluna INTEGER** — OK/compatível.
   O endpoint mede latência própria (float, round a 2 casas) e ambas as
   implementações de repositório aplicam `round(latency_ms)` na borda de
   persistência (postgres.py:84/98, conversations.py:120/132). O
   `LLMResult.latency_ms` (float, aditivo) é usado apenas para logging em
   resilience.py e não entra no repositório — nenhum conflito de tipo.
   Observação (não bloqueante): a latência persistida é a do endpoint (inclui
   retries+fallback), não a do provider vencedor; é a métrica mais útil para o
   usuário, e a granularidade por provider fica nos logs. Decisão razoável.

5. **Convivência no lifespan (engine + llm client)** — OK.
   Ordem: http_client → engine/sessionmaker/repo (condicional a
   REPOSITORY_BACKEND) → build_llm_client reutilizando o mesmo http_client.
   Guards `getattr(app.state, ..., None) is None` preservam overrides de teste
   dos dois builders sem colisão. Teardown fecha http_client e faz
   `engine.dispose()` sem dependência cruzada. /ready combina os dois checks
   (database SELECT 1 + llm_egress com timeout 2s e "skipped" sem keys) sem
   interferência.

6. **pytest** — executado nesta revisão: **46 passed, 4 skipped** (skips =
   integração postgres/testcontainers sem Docker nesta máquina), zero rede.

## Ressalvas menores (não bloqueiam; podem ir a rodada futura)
- `total_tokens` não é persistido (só prompt/completion) — se análises futuras
  precisarem, derivar por soma ou adicionar coluna.
- Em falha total, o registro failed fica com `provider=None` — aceitável, mas
  registrar "chain" ou o último provider tentado enriqueceria diagnósticos.
