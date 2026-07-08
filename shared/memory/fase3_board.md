# Quadro de Missão — Fase 3 (Segurança + Observabilidade da aplicação)

Time: `teams/team-fase3-hardening-obs.yml`. Builders em paralelo com propriedade
particionada; validação cruzada na sequência.

## Status

| Agente | Papel | Rodada | Veredito/Estado |
|--------|-------|--------|-----------------|
| security_hardening | builder | 1 | ENTREGUE (65 passed, 4 skipped; bandit/pip-audit limpos) |
| app_observability | builder | 1 | ENTREGUE (65 passed, 4 skipped) |
| sec → obs | validação cruzada | 2 | APROVADO |
| obs → sec | validação cruzada | 1 | APROVADO |
| quality_ci | validator | 2 | APROVADO (higiene aplicada; B008 ignorado por decisão) |

## Pendências herdadas (das Fases 1–2)
- IDOR no GET /v1/conversations/{user_id} — documentar/mitigar (dono: security).
- Limite global de body → 413 (dono: security).
- API_KEY_HASH incluído no fail-fast de config (dono: security).
- Middleware BaseHTTP pula X-Request-ID/access log em exceções não tratadas —
  migrar para middleware ASGI puro (dono: observability).
- Access log texto plano → structlog JSON sem body do prompt (dono: observability).
- provider=None em registros failed e total_tokens não persistido — avaliar
  exposição via métricas (dono: observability; mudanças de schema fora de escopo).

## Registro de rodadas

### Rodada 1
- [security_hardening] Em execução — API key auth, rate limit, sanitização, bandit/pip-audit.
- [security_hardening] **ENTREGUE (Rodada 1)** — 2026-07-06:
  - Auth por `X-API-Key` vs hash SHA-256 (`API_KEY_HASH`, compare_digest) em
    `app/core/auth.py`; públicos: /health, /ready, /docs, /redoc, /openapi.json,
    /metrics (scraper interno). API_KEY_HASH no fail-fast + validação de formato
    (config.py) — pendência herdada resolvida.
  - Rate limit por (key, IP) em `app/core/ratelimit.py` (sliding window in-memory,
    RATE_LIMIT_REQUESTS=10/60s, 429 + Retry-After; limitação multi-réplica documentada).
  - `app/core/security_headers.py`: SecurityHeadersMiddleware (nosniff, DENY, CSP,
    no-referrer, no-store) + BodyLimitMiddleware (413 p/ body > MAX_BODY_BYTES=64KiB,
    inclusive chunked; 415 p/ content-type ≠ JSON) — pendência 413 resolvida.
  - CORS fechado por padrão (CORS_ALLOWED_ORIGINS=[]); 413/415 no mapa de errors.py.
  - Prompt injection: system prompt fixo do servidor (LLM_SYSTEM_PROMPT) via
    providers.py/resilience.py; prompt do usuário sempre role=user.
  - IDOR do GET /v1/conversations/{user_id}: documentado como modelo
    service-to-service aceito + mitigação futura em `docs/security.md`.
  - conftest: TEST_API_KEY + header default no make_client (nenhum teste antigo quebrou).
  - `tests/test_security.py`: 15 testes (401 sem/errada key, 429+Retry-After,
    413/415/422, headers, fail-fast). Suíte: 65 passed, 4 skipped.
  - bandit -r app: 0 findings; pip-audit: sem vulnerabilidades (pip/setuptools do
    venv atualizados). Comandos documentados em docs/security.md p/ Makefile/CI (Agente 6).
  - Nota p/ observability: /metrics foi adicionado a PUBLIC_PATHS (auth+rate limit isentos).
- [app_observability] Em execução — structlog JSON, /metrics Prometheus, OTel, perfil observability.
- [app_observability] ENTREGUE (Rodada 1):
  - Pendências herdadas resolvidas: RequestIdMiddleware migrado de BaseHTTPMiddleware
    para middleware ASGI puro (try/except/finally) — X-Request-ID, X-Response-Time-Ms
    e access log garantidos mesmo em exceção não tratada (500 JSON emitido pelo
    próprio middleware quando a resposta ainda não começou).
  - Access log estruturado JSON (structlog, evento `http_request`): request_id, rota
    (template — baixa cardinalidade), status, latency_ms, client; SEM body do prompt —
    apenas body_bytes + body_sha256. LOG_FORMAT=json|console, LOG_LEVEL por env;
    todo logger stdlib também sai JSON com request_id.
  - /metrics (prometheus-client, registry dedicado): http_requests_total,
    http_request_duration_seconds{method,path,status}, llm_requests_total{provider,
    model,outcome}, llm_latency_seconds{provider}, llm_fallback_total,
    llm_tokens_total{provider,model,kind} (tokens expostos via métrica — schema DB
    intocado), circuit_breaker_state gauge (0/1/2) atualizado no ResilientLLMClient.
  - OTel: app/core/tracing.py — OTEL_ENABLED=false por padrão; OTLP/HTTP por env,
    console exporter sem endpoint; auto-instr FastAPI+httpx+SQLAlchemy; span nomeado
    llm.generate{provider,model}; persistência coberta pelos spans do SQLAlchemy.
  - docker-compose perfil `observability`: Prometheus 9090 + Grafana 3000 (datasource
    e dashboard itau-ms.json provisionados) + Jaeger 16686/4318. docs/observability.md
    com sinais, URLs e queries PromQL (p95, taxa de fallback, breaker).
  - Testes novos em tests/test_observability.py (4). Suíte: 65 passed, 4 skipped;
    ruff/mypy OK nos arquivos tocados. Compartilhados editados cirurgicamente:
    config.py (log_format+otel_*), main.py (metrics router, setup_tracing,
    setup_logging fmt), resilience.py (métricas+span), docker-compose, .env.example,
    requirements.txt. Sem commit.

### Rodada 2
- [app_observability] CORREÇÃO (item bloqueante da validação cruzada de segurança):
  - app/core/middleware.py: _route_template() agora retorna o label fixo
    "unmatched" quando não há rota casada (404 e 401 pré-roteamento) — elimina
    cardinalidade ilimitada em http_requests_total e reflexão de paths
    arbitrários no /metrics público.
  - Teste novo: test_unmatched_paths_do_not_create_raw_path_series (404 e 401
    com key errada não criam série com path bruto; série path="unmatched" existe).
  - Recomendações menores aplicadas: nota "somente dev" para Grafana admin/admin
    e portas do perfil observability (docs/observability.md + comentário no
    docker-compose.yml); documentado que a instrumentação httpx do OTel registra
    URLs de egress e que as keys vão sempre por header (docs/observability.md).
  - Suíte: 66 passed, 4 skipped; ruff OK. Sem commit.
- [security → obs] Validação cruzada Rodada 2 (2026-07-07): **APROVADO** —
  re-verificado pontualmente: label fixo "unmatched" em middleware.py, teste novo
  cobrindo 404/401, avisos dev-only e nota de egress em docs/observability.md.
  pytest: 66 passed, 4 skipped. Parecer atualizado em
  shared/memory/fase3_reviews/security_sobre_obs.md.

### Validação — quality_ci
- [quality_ci] Parecer em `shared/memory/fase3_reviews/quality.md`.
  - Rodada 1: suíte real = **66 passed, 4 skipped** (board dizia 65; test_observability.py
    tem 5 casos). Todos os cenários exigidos cobertos. Achados só de higiene:
    3 arquivos fora do ruff format (auth.py, config.py, test_security.py) e 4× B008
    (Depends em chat.py — idiomático FastAPI). Correções aplicadas pelo próprio
    validador (format + `ignore = ["B008"]` no pyproject.toml).
  - Rodada 2: pytest 66 passed/4 skipped; ruff check + format --check limpos.
    Sem duplicação entre middlewares; conftest coeso; sem dead code.
  - **Veredito: APROVADO**

## Encerramento — FASE 3 CONCLUÍDA

Placar final: 3/3 APROVADO (sec→obs Rodada 2 após correção do label unmatched;
obs→sec Rodada 1; quality_ci Rodada 2 com higiene aplicada e B008 ignorado por
decisão documentada). Suíte: 66 passed, 4 skipped, sem rede; ruff check/format
limpos; bandit 0 findings; pip-audit sem vulnerabilidades. Gate da Fase 4 liberado.
Pendências delegadas: rate limit multi-réplica via Redis e mitigação IDOR
(documentadas em docs/security.md — arquitetura/Fase 5 e futuro); POST sem
Content-Type → 422 vs 415 e contador dedicado de rate-limit (nice-to-have, Fase 4
decide); Dockerfile multi-stage non-root e compose hardening (Agente 7).
