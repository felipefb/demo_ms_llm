# Observabilidade

O serviço expõe três sinais: **logs estruturados (JSON)**, **métricas Prometheus**
e **traces OpenTelemetry**. Tudo é configurável por variáveis de ambiente
(ver `.env.example`).

## 1. Logs estruturados (structlog)

- Formato: `LOG_FORMAT=json` (padrão) — uma linha JSON por evento;
  `LOG_FORMAT=console` para saída legível em dev.
- Nível: `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, ...).
- **Correlação**: todo log (structlog ou stdlib) carrega `request_id`
  (lido do header `X-Request-ID` ou gerado; devolvido no response).
- **Access log** (`app.access` / evento `http_request`), emitido por middleware
  ASGI puro — garantido mesmo em exceção não tratada:

```json
{"event": "http_request", "method": "POST", "path": "/v1/chat", "route": "/v1/chat",
 "status_code": 200, "latency_ms": 42.1, "request_id": "…", "client": "…",
 "body_bytes": 57, "body_sha256": "…", "logger": "app.access", "level": "info",
 "timestamp": "…"}
```

Privacidade: o corpo da requisição (prompt) **nunca** é logado — apenas o tamanho
(`body_bytes`) e o hash (`body_sha256`), suficientes para auditoria/deduplicação.

## 2. Métricas Prometheus — `GET /metrics`

HTTP (via middleware, rótulo `path` usa o template da rota — baixa cardinalidade):

| Métrica | Tipo | Labels |
|---|---|---|
| `http_requests_total` | counter | method, path, status |
| `http_request_duration_seconds` | histogram | method, path, status |

Negócio (LLM, instrumentado no `ResilientLLMClient`):

| Métrica | Tipo | Labels | Significado |
|---|---|---|---|
| `llm_requests_total` | counter | provider, model, outcome | outcome: `success`, `transient_error`, `permanent_error`, `skipped_open_circuit` |
| `llm_latency_seconds` | histogram | provider | latência das chamadas bem-sucedidas |
| `llm_fallback_total` | counter | provider | respostas servidas por provider não-primário |
| `llm_tokens_total` | counter | provider, model, kind | tokens `prompt`/`completion` consumidos |
| `circuit_breaker_state` | gauge | provider | 0=closed, 1=half-open, 2=open |

### Queries de exemplo (PromQL)

```promql
# p95 de latência HTTP (todas as rotas)
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))

# p95 de latência do LLM por provider
histogram_quantile(0.95, sum by (provider, le) (rate(llm_latency_seconds_bucket[5m])))

# Taxa de erro HTTP (5xx)
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# Taxa de fallback (fração das respostas servidas pelo provider secundário)
sum(rate(llm_fallback_total[5m]))
  / sum(rate(llm_requests_total{outcome="success"}[5m]))

# Circuit breaker aberto agora?
max by (provider) (circuit_breaker_state) == 2

# Consumo de tokens por modelo (por minuto)
sum by (provider, model, kind) (rate(llm_tokens_total[1m])) * 60
```

## 3. Tracing (OpenTelemetry)

Desligado por padrão (`OTEL_ENABLED=false` — custo zero). Quando habilitado:

- `OTEL_EXPORTER_OTLP_ENDPOINT` definido → exporter OTLP/HTTP
  (ex.: `http://localhost:4318`, o Jaeger do perfil observability);
- endpoint vazio → `ConsoleSpanExporter` (debug local).

Instrumentação automática: FastAPI (spans de servidor, exclui
`/metrics,/health,/ready`), httpx (egress para OpenRouter/Gemini) e SQLAlchemy
(persistência — spans das queries no PostgreSQL). Span custom nomeado
`llm.generate` (atributos `provider`, `model`) envolve cada tentativa de provider,
incluindo os retries.

> **Nota (dados nos traces):** a instrumentação httpx registra as URLs de egress
> (OpenRouter/Gemini) como atributos dos spans. As API keys nunca aparecem nos
> traces: são enviadas exclusivamente via header, nunca na URL.

## 4. Stack local — perfil `observability`

```bash
docker compose --profile observability up --build
```

| Ferramenta | URL | Notas |
|---|---|---|
| Prometheus | http://localhost:9090 | scrape de `app:8000/metrics` a cada 15s |
| Grafana | http://localhost:3000 | admin/admin; datasource + dashboard "Itau MS - LLM Chat Service" provisionados automaticamente |
| Jaeger | http://localhost:16686 | recebe OTLP/HTTP na 4318; exige `OTEL_ENABLED=true` no app |

> **Somente desenvolvimento local:** o perfil `observability` usa credenciais
> padrão no Grafana (`admin/admin`) e publica as portas 9090/3000/16686/4318 no
> host sem autenticação. Não use este perfil em ambiente exposto — em produção,
> prefira serviços gerenciados ou provisione credenciais/políticas de rede.

O dashboard provisionado (`observability/grafana/dashboards/itau-ms.json`) traz:
requests/s por rota/status, latência p50/p95/p99, taxa de 5xx, chamadas LLM por
provider/outcome, p95 do LLM por provider, taxa de fallback e estado dos circuit
breakers.

Para ver traces: `OTEL_ENABLED=true docker compose --profile observability up`
(o compose já aponta `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`).

## 5. Testes

`tests/test_observability.py` cobre: `/metrics` expõe as métricas custom;
contadores de LLM e gauge do breaker; access log JSON com `request_id`
correlacionado e sem o corpo do prompt; `X-Request-ID`/`X-Response-Time-Ms`
presentes mesmo em exceção não tratada (middleware ASGI puro).
