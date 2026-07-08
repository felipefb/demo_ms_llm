# 2. Observabilidade na nuvem (AWS)

> Esta seção descreve como a instrumentação **já existente na aplicação**
> (structlog JSON, métricas Prometheus, OpenTelemetry opt-in — ver
> [`docs/observability.md`](../observability.md)) é aproveitada numa operação
> AWS-native, sem alterar código. O princípio de projeto foi: a app emite
> sinais em formatos abertos (JSON em stdout, `/metrics` Prometheus, OTLP);
> a plataforma decide o destino.

## 2.1 Logs — CloudWatch Logs + Logs Insights

**Coleta.** A app já escreve **uma linha JSON por evento em stdout**
(`app/core/logging.py`, `LOG_FORMAT=json`), com `request_id` injetado por
middleware ASGI em todo registro (inclusive stdlib). Em ECS Fargate basta o
log driver `awslogs` apontando para um log group
(`/ecs/itau-ms-chat/app`) — o CloudWatch indexa os campos JSON
automaticamente para o Logs Insights. Se no futuro for necessário roteamento
duplo (ex.: S3 para retenção fria + OpenSearch), troca-se por **FireLens
(Fluent Bit)** sem mudança na aplicação.

**Retenção proposta:**

| Log group | Retenção | Justificativa |
|---|---|---|
| `/ecs/itau-ms-chat/app` | 30 dias | investigação de incidentes; o conteúdo de prompts nunca é logado (só `body_bytes`/`body_sha256`), então não há pressão de compliance para expurgo rápido |
| Export S3 (opcional, via subscription) | 365 dias (Glacier IR) | auditoria/análises futuras a custo baixo |

**Queries CloudWatch Logs Insights** (usam os campos reais do access log
`http_request` emitido pelo middleware — `method`, `path`, `route`,
`status_code`, `latency_ms`, `request_id`, `level`, `event`):

```sql
-- (a) Erros 5xx por rota, última hora
fields @timestamp, route, status_code, request_id
| filter event = "http_request" and status_code >= 500
| stats count(*) as erros by route, status_code
| sort erros desc
```

```sql
-- (b) p95 de latência por rota (em ms), janelas de 5 min
fields @timestamp, route, latency_ms
| filter event = "http_request"
| stats pct(latency_ms, 95) as p95_ms, count(*) as reqs by route, bin(5m)
| sort bin(5m) desc
```

```sql
-- (c) Fallbacks para o provider secundário (OpenRouter degradado)
fields @timestamp, request_id, @message
| filter level = "warning" and @message like /fallback/
| sort @timestamp desc
| limit 50
```

```sql
-- (d) Linha do tempo completa de UMA requisição (correlação por request_id)
fields @timestamp, level, event, logger, @message
| filter request_id = "<request_id do header X-Request-ID>"
| sort @timestamp asc
```

Queries adicionais para as **decisões automáticas da camada de LLM**
(seção 5 — seleção de modelo, denylist e retry sem teto emitem logs
estruturados próprios):

```sql
-- (e) Trocas de modelo pelo seletor automático (evento operacional relevante)
fields @timestamp, provider, mode, model, @message
| filter @message like /model_selector/
| sort @timestamp desc
| limit 50
```

```sql
-- (f) Modelos colocados em denylist (auto-cura: erro permanente do modelo)
fields @timestamp, provider, model, request_id, @message
| filter @message like /denylisted after permanent error/
| stats count(*) as ocorrencias by provider, model
| sort ocorrencias desc
```

```sql
-- (g) Respostas truncadas pelo teto de tokens e o retry sem teto correspondente
fields @timestamp, provider, model, request_id, @message
| filter @message like /truncated by token cap; retrying uncapped/
| sort @timestamp desc
| limit 50
```

## 2.2 Métricas — ADOT + Amazon Managed Prometheus + Grafana

A app expõe `GET /metrics` em formato Prometheus (`app/core/metrics.py`).
Proposta: **sidecar ADOT Collector** na task ECS fazendo scrape de
`localhost:8000/metrics` e `remote_write` para **Amazon Managed Prometheus
(AMP)**, visualização em **Amazon Managed Grafana (AMG)**. Vantagens sobre
converter para EMF/CloudWatch Metrics: preserva histogramas e labels sem
explosão de custo por dimensão, e **reutiliza sem alteração o dashboard e as
queries PromQL já provisionados para o ambiente local**
(`observability/grafana/dashboards/itau-ms.json`). Alternativa mais simples
(menos peças): ADOT com exporter EMF → CloudWatch Metrics; aceitável, mas
perde `histogram_quantile` nativo — os alarmes de percentil teriam de usar as
métricas de latência pré-agregadas do API Gateway (`Latency`/
`IntegrationLatency` com estatística p95).

**Pipeline duplo do ADOT (alinhado à seção 1).** O ECS Service Auto Scaling
por *target tracking* (seção 1.3) só consome métricas do **CloudWatch**, não
do AMP. Por isso o mesmo collector ADOT tem **dois exporters**:
`prometheusremotewrite` → AMP (todas as métricas — dashboards e alertas no
Grafana) **e** `awsemf` → CloudWatch **apenas** para a métrica de scaling
("requests em voo por task", alvo ~20/task), mantendo custo e cardinalidade
controlados. Como `app/core/metrics.py` ainda não expõe um gauge de
in-flight, essa métrica é **derivada** no collector via Little's Law —
`rate(http_requests_total[1m]) ×` duração média (soma/contagem de
`http_request_duration_seconds`) — até que um gauge
`http_requests_in_progress` seja adicionado à app (melhoria futura,
registrada na seção 1).

Métricas exportadas pela app (as mesmas do ambiente local):

| Métrica | Tipo | Labels | Uso na nuvem |
|---|---|---|---|
| `http_requests_total` | counter | `method,path,status` (path = template da rota; `unmatched` p/ não roteados — cardinalidade controlada) | tráfego, taxa de erro |
| `http_request_duration_seconds` | histogram | `method,path,status` | p50/p95/p99, SLO de latência |
| `rate_limit_rejections_total` | counter | `path` | abuso / clientes mal configurados |
| `llm_requests_total` | counter | `provider,model,outcome` (`success`/`transient_error`/`permanent_error`/`skipped_open_circuit`) | saúde dos providers |
| `llm_latency_seconds` | histogram | `provider` | separar latência do LLM da latência da app |
| `llm_fallback_total` | counter | `provider` | detector precoce de degradação do OpenRouter |
| `llm_cache_hits_total` | counter | — | respostas servidas do cache TTL (sem chamada ao LLM) — **indicador direto de custo evitado** (tokens/free tier) e de latência ~0 ms |
| `llm_tokens_total` | counter | `provider,model,kind` | custo/consumo de free tier |
| `llm_selected_model` | gauge (1 = ativo) | `provider,mode,model` (a série antiga é removida ao trocar de modelo — cardinalidade não acumula) | qual modelo o seletor automático está usando por provider/modo; **mudança de valor = evento operacional** (vira *annotation* no Grafana) |
| `circuit_breaker_state` | gauge | `provider` (0=closed, 1=half-open, 2=open) | alarme direto de breaker aberto |

Métricas de infra complementares (nativas, sem instrumentação): API Gateway
HTTP API (`Count`, `4xx`, `5xx`, `Latency`, `IntegrationLatency` — a
diferença entre as duas últimas isola o overhead do próprio gateway),
ECS/Container Insights
(CPU, memória, task count), RDS (`DatabaseConnections`, `CPUUtilization`,
`FreeableMemory`), NAT Gateway (egress para os LLMs).

## 2.3 Traces — OpenTelemetry → AWS X-Ray

A app já tem tracing OTel opt-in (`app/core/tracing.py`, `OTEL_ENABLED=true`)
com exporter **OTLP/HTTP** e auto-instrumentação de FastAPI, httpx e
SQLAlchemy, mais o span custom `llm.generate` (atributos `provider`,
`model`). O **mesmo sidecar ADOT** recebe OTLP em `localhost:4318` e exporta
para **X-Ray** — nenhuma mudança de código, só apontar
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`. (Alternativa: Jaeger/
Tempo via AMG, se o time preferir ficar 100% OSS.)

Trace ponta a ponta de um `POST /v1/chat`:

```
API Gateway HTTP API ──► span FastAPI (server, rota /v1/chat)
                      ├─► llm.generate {provider=openrouter, model=...}   ← span custom
                      │     └─► httpx POST openrouter.ai (auto)           ← inclui retries
                      │   (falha) llm.generate {provider=gemini}          ← fallback visível
                      │     └─► httpx POST generativelanguage.googleapis.com
                      └─► SQLAlchemy INSERT chat (auto)                   ← persistência no RDS
```

Cada tentativa de provider vira um span próprio, então **retry, fallback e
breaker aberto ficam visíveis na cascata** do X-Ray. `/metrics`, `/health` e
`/ready` são excluídos da instrumentação (sem ruído de health check).
Amostragem: 100% em 5xx e latência > 5s; 5% do tráfego saudável (regra de
sampling do X-Ray) — volume baixo esperado torna o custo irrelevante.

## 2.4 Golden signals, SLOs e error budget

| Sinal | Medição (fonte real) | SLO proposto |
|---|---|---|
| **Latência** | p95 de `http_request_duration_seconds{path="/v1/chat"}` **descontando** o p95 de `llm_latency_seconds` na mesma janela | **p95 do overhead da app < 300 ms**; adicionalmente p95 total < 30 s (timeout duro) |
| **Tráfego** | `sum(rate(http_requests_total[5m]))` | sem SLO — sinal de contexto e capacity planning |
| **Erros** | `rate(http_requests_total{status=~"5.."}) / rate(http_requests_total)` | **disponibilidade 99,9%** mensal (taxa de sucesso ≥ 99,9%) |
| **Saturação** | CPU/memória ECS, `DatabaseConnections` RDS, `rate_limit_rejections_total`, profundidade de retries | CPU < 75% sustentado; conexões DB < 80% do `max_connections` |

**Por que excluir a latência do LLM do SLO de p95?** A latência do
OpenRouter/Gemini free tier é dominante (segundos), altamente variável e
**fora do nosso controle** — um SLO que a inclua mede o fornecedor, não o
serviço, e geraria alarmes não acionáveis. A instrumentação separa os dois
propositalmente: `llm_latency_seconds{provider}` mede o fornecedor (vira
sinal de *fallback/breaker*, seção 2.5), enquanto o SLO da app mede o que a
engenharia pode consertar (fila, pool de conexões, serialização, banco). O
guarda-corpo do usuário final é o timeout total + p95 total < 30 s.

**Efeito do cache TTL (seção 5.3/5.4) nos SLOs.** O SLO de p95 *excluindo o
LLM* continua válido e fica ainda mais representativo: um cache hit responde
em ~ms sem chamar provider algum, então ele é medido integralmente pelo
overhead da app — nenhuma correção é necessária na fórmula (não há
`llm_latency_seconds` a descontar nesses requests). Para o p95 **total**,
vale acompanhar a taxa de hit
(`rate(llm_cache_hits_total) / rate(http_requests_total{path="/v1/chat"})`):
uma queda brusca de hit rate desloca o p95 total para o piso físico de
~8–15 s da busca web (5.4) sem que nada esteja "quebrado" — o painel de
cache (2.6) dá esse contexto antes de alguém abrir incidente.

**Error budget** (disponibilidade 99,9%): **43,2 min/mês** de
indisponibilidade equivalente. Política: budget < 50% restante → congela
mudanças não essenciais e prioriza confiabilidade; consumo rápido (> 5% em
1 h) → incidente. Erros do LLM **não** consomem budget quando o fallback
responde com sucesso (o usuário recebeu 200); consomem quando ambos os
providers falham e a API devolve 5xx/503. O **cache TTL protege o budget**:
durante uma indisponibilidade simultânea dos dois providers, prompts
repetidos dentro da janela de TTL continuam respondendo 200 do cache — a
queima efetiva do budget é menor que a taxa de falha dos providers, e o
painel de hit rate quantifica essa proteção.

## 2.5 Alarmes (CloudWatch Alarms → SNS → Slack/PagerDuty)

Todos avaliados sobre métricas do AMP (via alerting do Grafana → SNS) ou
CloudWatch nativo, com **thresholds justificados**:

| # | Alarme | Condição | Justificativa | Ação |
|---|---|---|---|---|
| A1 | Taxa de 5xx alta | > 1% por 5 min (janela curta: > 5% por 1 min) | 1% = 10× o budget mensal de 0,1%; queima o budget em ~3 dias | page |
| A2 | p95 da app degradado | p95 de `http_request_duration_seconds − llm_latency_seconds` > 300 ms por 10 min | SLO da seção 2.4; 10 min evita flap em picos curtos | ticket; page se > 1 s |
| A3 | Taxa de fallback alta | `rate(llm_fallback_total) / rate(llm_requests_total{outcome="success"})` > 20% por 10 min | usuários ainda são atendidos (Gemini), mas OpenRouter está degradado — leading indicator antes de A4 | ticket/Slack |
| A4 | Circuit breaker aberto | `max(circuit_breaker_state) == 2` por 5 min (qualquer provider) | 5 min > janela de half-open; breaker aberto sustentado = provider realmente fora | Slack; page se **ambos** providers abertos (serviço sem saída) |
| A5 | Throttling do Gemini | `rate(llm_requests_total{provider="gemini",outcome="transient_error"})` crescendo enquanto A3 ativo; proxy adicional: `llm_tokens_total{provider="gemini"}` se aproximando da cota diária do free tier | Gemini é a rede de segurança — se ele throttla durante um fallback, a próxima falha vira 5xx | page |
| A6 | Conexões do banco | RDS `DatabaseConnections` > 80% do `max_connections` por 5 min | acima disso, novas tasks/conexões começam a falhar em cascata | ticket; page a 95% |
| A7 | Rate limiting anômalo | `rate(rate_limit_rejections_total[5m])` > 5/s por 10 min | ou abuso ou cliente legítimo mal configurado | Slack |
| A8 | Cache hit rate em queda | hit rate (`rate(llm_cache_hits_total) / rate(http_requests_total{path="/v1/chat"})`) caindo > 50% vs. baseline de 24 h por 30 min, **com tráfego estável** | indicador de **custo**: menos hits = mais chamadas reais ao LLM (tokens do free tier) e p95 total pior; queda com tráfego estável sugere cache desabilitado por engano (`LLM_CACHE_TTL_SECONDS=0`) ou mudança no padrão de prompts | Slack/ticket — nunca page |
| A9 | Troca de modelo frequente | mais de 3 mudanças de série em `llm_selected_model` (por provider/modo) em 1 h | o seletor re-escolher ocasionalmente é normal (catálogo TTL 1 h, denylist); trocas em rajada indicam catálogo instável ou modelos entrando em denylist em cascata (ver query (f)) | Slack |

**Composite alarms** (redução de ruído):

- **`LLM-Degradation`** = A3 **OU** A4 → um único aviso "provider primário
  degradado" em vez de três alarmes correlacionados; suprime A5 enquanto não
  disparar.
- **`Service-Down`** = A1 **E** (A4 em ambos providers **OU** A6) → page
  imediato: erro visível ao usuário com causa raiz já sugerida (LLMs fora
  ou banco saturado).
- Alarmes filhos ficam com `actions_suppressor` apontando para o composite —
  o on-call recebe 1 página, não 5.

## 2.6 Dashboard principal (AMG)

Duas faixas no mesmo dashboard — evolução direta do
`observability/grafana/dashboards/itau-ms.json` já provisionado localmente:

**Faixa 1 — Visão executiva (responde "está saudável?"):**

1. Stat: disponibilidade 30 d (sucesso/total) vs. SLO 99,9% + error budget
   restante (%).
2. Stat: p95 do overhead da app (SLO 300 ms) e p95 total.
3. Timeseries: requests/s por status (2xx/4xx/5xx empilhado).
4. Stat: taxa de fallback atual + estado dos breakers (verde/amarelo/
   vermelho por `circuit_breaker_state`).
5. Timeseries: tokens/min por provider/modelo (`llm_tokens_total`) — consumo
   do free tier.
6. Stat + timeseries: **cache hit rate** (`llm_cache_hits_total` /
   requests de `/v1/chat`) — cada ponto percentual de hit é chamada de LLM
   (custo + segundos de latência) evitada.

**Faixa 2 — Troubleshooting (responde "onde está quebrando?"):**

7. Latência p50/p95/p99 por rota (`http_request_duration_seconds`).
8. `llm_requests_total` por provider/outcome (aqui aparece
   `skipped_open_circuit` e `transient_error`).
9. p95 de `llm_latency_seconds` por provider (OpenRouter vs. Gemini lado a
   lado).
10. Tabela: **modelo ativo por provider/modo** (`llm_selected_model == 1`).
    Além do painel, cada mudança de série vira uma **annotation vertical**
    nos gráficos de latência e tokens (Grafana annotation query sobre
    `changes(llm_selected_model[5m]) > 0` ou sobre o log `model_selector`
    via datasource CloudWatch) — troca de modelo é evento operacional e
    frequentemente explica um degrau de latência/qualidade sem deploy.
11. `rate_limit_rejections_total` por path.
12. Infra: CPU/mem das tasks ECS, `DatabaseConnections` e latência de query
    (spans SQLAlchemy no X-Ray como link).
13. Painel de logs (datasource CloudWatch): erros recentes filtrados por
    `level=error` **mais eventos de denylist e retry sem teto** (queries (f)
    e (g) da seção 2.1 como links pré-preenchidos), além do link para a
    query (d) de correlação por `request_id`.

## 2.7 Correlação log ↔ métrica ↔ trace: exemplo de investigação

Cenário: alarme **A2** dispara às 14:32 — p95 da app subiu para 900 ms.

1. **Métrica → escopo.** No dashboard (painel 6), o p95 subiu apenas na rota
   `/v1/chat`; painel 8 mostra `llm_latency_seconds` estável — então o
   problema é nosso, não do provider. Painel 10 mostra
   `DatabaseConnections` em 95%.
2. **Métrica → trace.** No X-Ray, filtra-se
   `service("itau-ms") AND responsetime > 0.9` na janela 14:25–14:35. As
   cascatas mostram o span FastAPI com `llm.generate` normal (~2 s, como
   sempre) mas o span SQLAlchemy do `INSERT` levando 700 ms aguardando
   conexão do pool.
3. **Trace → log.** Cada resposta carrega o header `X-Request-ID` (mesmo
   valor do campo `request_id` dos logs). Pegando um trace lento, roda-se a
   query (d) do Logs Insights com esse `request_id` e obtém-se a linha do
   tempo completa daquela requisição: o access log `http_request` confirma
   `latency_ms=912`, e um `warning` do pool de conexões aparece 700 ms antes.
4. **Log → causa raiz.** A query (b) confirma que a degradação começou às
   14:20, exatamente quando o auto scaling dobrou o número de tasks — o pool
   por task × nº de tasks estourou o `max_connections` do RDS
   (ver seção de escalonamento, RDS Proxy como mitigação).
5. **Fechamento.** Ação: habilitar RDS Proxy / reduzir pool por task; o
   alarme A6 (que também estava amarelo) é promovido a composite com A2 para
   diagnósticos futuros em 1 clique.

O elo de tudo é o **`request_id`**: gerado (ou propagado de `X-Request-ID`)
pelo middleware, presente em **todos** os logs da requisição, devolvido ao
cliente no response e visível junto ao trace — com OTel habilitado, o
`trace_id` do X-Ray cobre a mesma requisição e os dois identificadores se
cruzam pelo timestamp + rota, permitindo navegar em qualquer direção
(métrica → trace → log → cliente).
