# Parecer — llm_integration sobre a entrega do builder persistence (Fase 2, Rodada 1)

Revisor: agente llm_integration (validação cruzada, ótica da integração LLM).
Escopo revisado: app/models/interaction.py, app/repositories/postgres.py,
app/repositories/database.py, migrations/, docker-compose.yml, app/main.py,
app/api/health.py. Suíte executada: `python -m pytest -q` → **46 passed, 4 skipped**
(skips = integração pg/testcontainers sem Docker nesta máquina; zero rede).

## Veredito: APROVADO

## Verificações (ponto a ponto)

1. **Fidelidade ao fluxo resiliente** — OK.
   `PostgresConversationRepository.mark_completed` grava `response`, `model`,
   `provider`, `latency_ms` (round na borda, idêntico ao in-memory),
   `prompt_tokens`, `completion_tokens`; `mark_failed` grava `error_detail` +
   `latency_ms` e status=failed. O endpoint (app/api/v1/chat.py) passa
   `result.provider`/`result.model` vindos do `LLMResult` — logo, quando o
   fallback Gemini responde, `provider="gemini"` e o modelo Gemini real ficam
   registrados corretamente; falha total vira status=failed antes do 503
   (prompt nunca se perde). Assinaturas batem com o Protocol
   `ConversationRepository` sem alteração de contrato.

2. **Wiring do lifespan** — OK.
   Ordem em app/main.py: http_client → engine/sessionmaker/repo → llm_client
   (que reusa o http_client único). Shutdown: `aclose()` do http_client e
   `dispose()` do engine, ambos no `finally`. Testes continuam podendo pré-setar
   `app.state.repository`/`llm_client` (guards `is None` preservados). Nenhum
   conflito com o wiring do cliente LLM.

3. **/ready: database vs llm_egress** — OK, sem interferência.
   `_check_database` usa exclusivamente o `db_engine` (SELECT 1, conexão do
   pool próprio); `_check_llm_egress` usa o http_client compartilhado com
   timeout curto por-request (READY_CHECK_TIMEOUT_SECONDS=2s, sobrepõe o
   default de 30s do client). São checks independentes; backend memory →
   database="skipped"; sem keys reais → llm_egress="skipped" (testes offline).

4. **Cobertura da migration vs LLMResult** — OK.
   Migration 0001 cria todas as colunas que o fluxo produz: response, model,
   provider, status (enum nativo com server_default), error_detail,
   latency_ms INTEGER, prompt_tokens, completion_tokens, metadata JSONB,
   timestamps tz-aware + índices (user_id e composto user_id/created_at DESC).
   Colunas/tipos idênticos ao ORM (app/models/interaction.py). O campo
   `latency_ms` do LLMResult (latência da chamada ao provider) não tem coluna
   própria — a coluna persiste a latência fim-a-fim medida no endpoint, que é
   a semântica combinada; a latência por provider fica para observabilidade
   (Agente 5), não para o banco. Sem lacuna.

5. **Pendência herdada resolvida** — OK. latency float→INTEGER decidido e
   aplicado simetricamente nas duas implementações (round na borda).

## Observações não bloqueantes (podem ficar para rodadas futuras)

- `updated_at` na migration tem apenas `server_default=now()`; o refresh via
  `onupdate` é do ORM (Python-side). Updates fora do ORM não atualizariam a
  coluna. Aceitável no escopo atual; um trigger seria o purismo.
- docker-compose usa `sh -c "alembic upgrade head && uvicorn ..."` — depende
  do Dockerfile baseline conter alembic no PATH; já sinalizado como escopo do
  Agente 7 (multi-stage).
- `_check_database` faz import local de `sqlalchemy.text`; funcional, apenas
  estilístico.
