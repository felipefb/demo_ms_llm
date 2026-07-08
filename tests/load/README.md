# Teste de carga básico (k6)

Script: `chat_load.js` — 10 VUs contra `POST /v1/chat` por ~1m45s
(ramp-up 30s, sustentado 1m, ramp-down 15s).

## Pré-requisitos

- [k6](https://k6.io/docs/get-started/installation/) instalado
  (`winget install k6` / `brew install k6`).
- Serviço rodando localmente. Para não gastar quota do free tier, rode em modo
  dev sem chaves de provider (o `EchoLLMClient` responde localmente):

  ```bash
  docker compose up -d postgres
  make run   # ou: .venv\Scripts\python -m uvicorn app.main:app --port 8000
  ```

- Rate limit: o padrão de dev pode ser baixo; para carga, exporte
  `RATE_LIMIT_REQUESTS=10000` antes de subir o serviço, senão parte das
  respostas será 429 (comportamento correto, mas polui o resultado).

## Execução

```bash
k6 run tests/load/chat_load.js \
  -e BASE_URL=http://localhost:8000 \
  -e API_KEY=<sua-api-key>
```

## O que observar

| Métrica k6 | O que indica | Alvo (dev/echo) |
|---|---|---|
| `http_req_duration p(95)` | Latência fim-a-fim (API + persistência + LLM) | < 300ms com echo; segundos com provider real |
| `http_req_failed` | Erros 5xx / rede | < 5% |
| `checks` | Contrato da resposta (200 + campo `response`) | > 95% |
| `iterations` | Vazão efetiva | estável durante o platô |

Em paralelo, observe no serviço (`GET /metrics` ou Grafana):

- `http_request_duration_seconds` (p95 por rota) — deve acompanhar o k6;
- `llm_latency_seconds` — separa o custo do provider do custo da API;
- `rate_limit_rejections_total` — se crescer, o limite está baixo para a carga;
- `circuit_breaker_state` — deve permanecer 0 (closed) durante o teste;
- pool do Postgres (logs de timeout) — se aparecer `TimeoutError`, aumente
  `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`.

Thresholds falham o processo do k6 (exit code != 0), o que permite usar o
script em smoke de performance no CI no futuro (não habilitado por padrão
para não depender de LLM externo).
