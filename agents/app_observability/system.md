# Agente 5 — Observabilidade da aplicação

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Instrumentar logging, métricas e tracing no serviço.

1. Logging estruturado em JSON (structlog): request_id, user_id, rota, status_code,
   latency_ms, provider/modelo usado; níveis configuráveis por env; log de acesso via
   middleware (sem body do prompt em produção — apenas tamanho e hash).
2. Métricas Prometheus em GET /metrics (prometheus-fastapi-instrumentator ou
   prometheus-client): histograma de latência HTTP por rota/status, contador de
   requests, e métricas de negócio: llm_requests_total{provider,model,outcome},
   llm_latency_seconds{provider}, llm_fallback_total, circuit_breaker_state.
3. Tracing com OpenTelemetry: instrumentação automática FastAPI + httpx + SQLAlchemy,
   exporter OTLP configurável por env (default: console/desligado). Spans nomeados para
   a chamada LLM e para a persistência.
4. docker-compose com perfil opcional `observability`: Prometheus + Grafana provisionados
   (datasource + um dashboard JSON básico com latência, taxa de erro, uso por provider)
   e, se simples, Jaeger para traces.
5. Documente em docs/observability.md: quais sinais existem, onde ver, e exemplos de
   queries (ex.: p95 de latência, taxa de fallback).

CRITÉRIOS DE ACEITE: /metrics expõe as métricas custom; logs saem em JSON com
request_id correlacionado; `docker compose --profile observability up` sobe
Prometheus+Grafana com o dashboard carregado.
