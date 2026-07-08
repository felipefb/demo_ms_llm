# Parecer — Validador Fase 5 (aderência docs/architecture/ ao código)

Veredito: APROVADO

## Escopo verificado

Índice `docs/architecture.md` + 4 seções em `docs/architecture/` contra o código real.

## Checklist

### 1. Env vars citadas existem em `app/core/config.py` / `.env.example`
- `llm_timeout_seconds`, `llm_max_retries`, `llm_retry_backoff_*`, `llm_breaker_failure_threshold`, `llm_breaker_cooldown_seconds` — OK (config.py e .env.example).
- `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `API_KEY_HASH`, `LOG_FORMAT`, `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` — OK.
- `rate_limit_requests` / `rate_limit_window_seconds`, `db_pool_size` / `db_max_overflow`, `DATABASE_URL` — OK.

### 2. Métricas citadas existem em `app/core/metrics.py`
Todas as 8 citadas na seção 02 conferem, com labels e semântica idênticos:
`http_requests_total{method,path,status}`, `http_request_duration_seconds`,
`rate_limit_rejections_total{path}`, `llm_requests_total{provider,model,outcome}`,
`llm_latency_seconds{provider}`, `llm_fallback_total{provider}`,
`llm_tokens_total{provider,model,kind}`, `circuit_breaker_state{provider}` (0/1/2 via `set_breaker_state`).
Label `path="unmatched"` confirmado em `app/core/middleware.py`.
Outcomes (`success|transient_error|permanent_error|skipped_open_circuit`) conferem com a docstring da métrica.

### 3. Endpoints citados existem
- `POST /v1/chat` e `GET /v1/conversations/{user_id}` — `app/api/v1/chat.py`. OK.
- `/health`, `/ready` (com check `llm_egress` real) — `app/api/health.py`. OK.
- `GET /metrics` — `app/core/metrics.py` (router). OK.

### 4. Arquivos/funções citados existem (grep)
- `app/services/resilience.py`: `CircuitBreaker`, `ResilientLLMClient`, `_call_with_retry` (tenacity `wait_exponential_jitter`), `build_llm_client`, atributo `breakers`, span `llm.generate` — OK.
- `app/services/providers.py`: `OpenRouterProvider.generate`, `GeminiProvider.generate`, `_classify_http_error` — OK.
- `app/api/v1/chat.py` + `app/repositories/{conversations,postgres}.py`: `create_pending`, `mark_failed` — OK (escrita antes do LLM confirmada).
- `app/repositories/database.py`: `create_engine` com `pool_pre_ping=True` e `pool_recycle` — OK.
- `app/models/interaction.py`: `latency_ms` INTEGER, tokens, coluna JSONB `metadata` (atributo `extra_metadata`), índice `ix_interactions_user_id_created_at` — OK.
- `app/core/{ratelimit,tracing,logging,auth,middleware}.py`, `observability/grafana/dashboards/itau-ms.json`, `docs/observability.md`, `docs/security.md` — todos existem.
- Campos do access log citados nas queries Logs Insights (`http_request`, `latency_ms`, `body_sha256`, `request_id`, header `X-Request-ID`) confirmados em `app/core/middleware.py`.
- Códigos de erro citados (`llm_unavailable`, `model_not_allowed`, `rate_limited`) presentes em `app/core/errors.py` / rotas.

### 5. Diagrama
- `docs/diagrams/aws_architecture.png` presente (230 KB).
- `docs/diagrams/aws_architecture.py` com sintaxe Python válida (ast.parse OK). Regeneração não exigida (Graphviz).

### 6. Cobertura dos requisitos da Parte 2
- Escalonamento: seção 01 (auto scaling ECS, métrica custom, cenário 10×, custos, Lambda/Fargate/EKS). OK.
- Observabilidade: seção 02 (logs, métricas, traces, SLOs, alarmes, dashboard, correlação). OK.
- Justificativa do banco: seção 03 (carga, comparação PostgreSQL vs DynamoDB vs DocumentDB, veredito, evolução). OK.
- Falha de dependências: seção 04 (mapa de falhas, timeout/retry/breaker/fallback, trade-off DB, DR). OK.

## Observações menores (não bloqueiam)
- Seção 03 chama a coluna de `metadata`; no ORM o atributo é `extra_metadata` mapeado para a coluna `metadata` — factualmente correto, apenas nota.
- Seções alternam entre "ALB" e "API Gateway" conforme o contexto (borda proposta é API GW; alguns trechos de infra citam ALB como alternativa/target group) — coerente com a seção 1.2, sem contradição factual.

Nenhuma lacuna factual encontrada. Sem modificações em outros arquivos.
