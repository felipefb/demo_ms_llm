# 4. Resiliência e Falhas de Dependências

Esta seção mapeia cada dependência externa do serviço, seus modos de falha e a
estratégia em camadas adotada — grande parte já implementada no código da Parte 1
(referências de arquivo/função incluídas). O princípio norteador: **falhar rápido,
degradar de forma controlada e nunca perder o prompt do usuário**.

## 4.1 Mapa de dependências e modos de falha

| Dependência | Modos de falha típicos | Impacto se não tratado |
|---|---|---|
| OpenRouter (LLM primário) | Indisponibilidade total, latência alta, `429` (free tier), `5xx`, resposta malformada | Requisições penduradas, erro genérico ao cliente |
| Gemini (LLM fallback) | Idem OpenRouter (quota de free tier ainda mais restrita) | Fallback inútil se falhar junto |
| PostgreSQL / Aurora | Instância fora, failover em andamento, pool esgotado, conexões stale, latência degradada | Perda de prompts/respostas, 500 em cascata |
| Catálogos de modelos (OpenRouter `/models`, Gemini `ListModels`) | Endpoint fora, lento ou com payload inesperado durante a seleção automática de modelo | Seleção automática pararia de atualizar; sem tratamento, poderia bloquear requests |
| Secrets Manager (nuvem) | Indisponibilidade na inicialização ou na rotação de segredos | Serviço não sobe ou perde credenciais em runtime |
| AZ / Região AWS | Queda de uma AZ; raro, mas possível: região inteira | Perda total de capacidade se tudo estiver em uma AZ |

## 4.2 Estratégia em camadas por dependência

### 4.2.1 LLM (OpenRouter → Gemini)

Defesa em profundidade, na ordem em que a requisição a atravessa:

1. **Timeout** — cada chamada HTTP tem timeout explícito e configurável
   (`llm_timeout_seconds`, aplicado em `OpenRouterProvider.generate` e
   `GeminiProvider.generate`, `app/services/providers.py`). Nenhuma requisição
   fica pendurada esperando um provider lento.
2. **Classificação de erro** — `_classify_http_error` (`app/services/providers.py`)
   separa três classes: `LLMTransientError` (timeout, rede, `5xx` — vale a pena
   reexecutar), `LLMRateLimitError` (subclasse de transiente, exclusiva do
   `429`) e `LLMPermanentError` (`4xx` de validação, corpo malformado —
   reexecutar só desperdiça quota). Corpos de erro do upstream nunca vazam para
   o cliente.
3. **`429` = fail-fast direto ao fallback** — `LLMRateLimitError` **não** é
   retentado: o predicado de retry em `ResilientLLMClient._call_with_retry`
   (`app/services/resilience.py`) exclui explicitamente essa classe. Com
   fallback na cadeia, insistir num provider saturado de free tier só adiciona
   latência — a requisição pula imediatamente para o próximo provider.
4. **Retry com backoff exponencial + jitter — só para `5xx`/timeout** —
   `ResilientLLMClient._call_with_retry` usa tenacity com
   `wait_exponential_jitter`, máximo de 2 retries (`llm_max_retries`),
   **apenas** para erros transientes não-429. O jitter evita sincronização de
   retries (thundering herd) contra um provider já degradado.
5. **Circuit breaker por provider** — classe `CircuitBreaker`
   (`app/services/resilience.py`): abre após N falhas consecutivas
   (`llm_breaker_failure_threshold`, default 5), entra em half-open após o
   cooldown (`llm_breaker_cooldown_seconds`, default 30 s) permitindo uma
   chamada-sonda. Com o circuito aberto o provider é pulado sem gastar timeout,
   e o estado é exportado como métrica (`set_breaker_state`,
   `app/core/metrics.py`) — visível nos dashboards da seção de observabilidade.
   Erros permanentes **não** disparam o breaker (não são sinal de saúde do
   provider), mas avançam para o próximo da cadeia.
6. **Fallback de provider** — `ResilientLLMClient.generate` percorre a cadeia
   ordenada montada em `build_llm_client`: OpenRouter → Gemini por padrão;
   com `LLM_WEB_SEARCH=true` a cadeia é invertida (Gemini vira primário — o
   grounding nativo `google_search` resolve em uma chamada rápida; o OpenRouter
   vira fallback, e a mecânica de resiliência não muda). Fallbacks são
   contabilizados em `llm_fallback_total`. O modelo pedido pelo cliente só se
   aplica ao provider primário (catálogos de nomes diferem).
7. **Auto-cura por denylist de modelo** — quando um modelo escolhido
   automaticamente pelo catálogo devolve erro permanente (`4xx`, corpo
   malformado ou `content` nulo/vazio), o provider chama
   `_mark_bad_if_auto` → `ModelSelector.mark_bad`
   (`app/services/providers.py`, `app/services/model_selector.py`): o modelo
   entra numa denylist em runtime, sai da seleção e a re-escolha é imediata.
   Um modelo "ruim" no catálogo degrada no máximo uma requisição — o serviço
   se cura sozinho sem redeploy.
8. **Teto adaptativo de tokens (truncagem não derruba o request)** — modelos
   com "thinking" gastam o `max_tokens` em raciocínio interno e devolvem 200
   com texto vazio/cortado (`finish_reason=length` / `MAX_TOKENS`). Os
   providers (`OpenRouterProvider.generate` e `GeminiProvider.generate`)
   detectam a truncagem e repetem **uma** vez sem teto; o conjunto
   `_uncapped_models` memoriza o modelo para as próximas chamadas já irem sem
   teto (sem pagar a chamada dupla de novo). A culpa era do nosso limite, não
   do modelo — por isso não denylista.
9. **Última linha: 503 honesto** — se todos os providers falharem ou estiverem
   com circuito aberto, `LLMUnavailableError` produz `503` com envelope de erro
   padronizado `code=llm_unavailable` e `request_id` (correlacionável nos logs).
   Antes disso, o endpoint já persistiu a interação como `failed`
   (`mark_failed` em `app/api/v1/chat.py`) — **o prompt nunca se perde**.

**Evolução (não implementada, desenhada):** para picos prolongados de
indisponibilidade, publicar o prompt persistido em uma fila SQS e reprocessar
assincronamente quando os providers voltarem, com resposta via polling
(`GET /v1/conversations/{user_id}` já existe e suporta esse fluxo, pois a
interação `failed`/`pending` fica consultável) ou webhook.

### 4.2.2 Catálogos de modelos dos providers

Com `LLM_AUTO_MODEL=true`, o `ModelSelector` (`app/services/model_selector.py`)
consulta periodicamente os catálogos (`/models` do OpenRouter, `ListModels` do
Gemini) para escolher o melhor modelo por provider/modo. É uma dependência
externa nova — tratada para **nunca afetar a requisição**:

- **Cache com TTL** — a seleção acontece fora do caminho quente, com refresh a
  cada `LLM_MODEL_REFRESH_SECONDS` (default 3600 s) e timeout próprio
  (`catalog_timeout_seconds`); a primeira seleção roda no startup (lifespan).
- **Falha de catálogo = manter última seleção** — `ModelSelector._refresh`
  captura a falha do fetch, loga
  `catalog fetch failed (...); keeping last selection` e segue com a escolha
  anterior. Se nunca houve seleção, `get_model` devolve `None` e o provider usa
  o default do env (`OPENROUTER_MODEL`/`GEMINI_MODEL`) — cadeia de fallback:
  override manual → seleção do catálogo → default do env.
- **Denylist aplicada na seleção** — modelos marcados via `mark_bad` são
  filtrados do catálogo no refresh (`_refresh`), fechando o ciclo de auto-cura
  descrito em 4.2.1.

### 4.2.3 Banco de dados (PostgreSQL / Aurora)

- **Pool com verificação de saúde** — `create_engine`
  (`app/repositories/database.py`) configura `pool_pre_ping=True` (descarta
  conexões stale antes do uso — essencial durante failovers do Aurora),
  `pool_recycle` e limites de pool/overflow/timeout configuráveis por env var.
- **Readiness real** — `/ready` (`app/api/health.py`) executa checagem real de
  banco e de egress; se o banco cair, o health check do ECS Service (que fica
  atrás do API Gateway, conforme a seção 01) tira a task de rotação e ela para
  de receber tráfego novo.
- **Escrita antes do LLM** — `create_pending` roda **antes** da chamada ao LLM
  (`app/api/v1/chat.py`), então a janela de perda de dados é mínima: ou a
  requisição falha imediatamente (nada foi prometido ao cliente), ou o prompt já
  está durável.

**Trade-off: (a) falhar vs (b) degradar quando o banco cai.**

- *(a) Falhar a requisição* (comportamento atual): se `create_pending` falha, a
  requisição retorna `500` com envelope e `request_id`. Simples, consistente
  (tudo que foi respondido está persistido), mas o cliente perde disponibilidade
  mesmo com os LLMs saudáveis.
- *(b) Degradar*: chamar o LLM, responder ao cliente e **enfileirar a
  persistência** em SQS; um consumidor grava no banco quando ele voltar, com
  DLQ para mensagens que falharem repetidamente (inspecionáveis e
  reprocessáveis manualmente).

**Recomendação: (b)** na evolução para produção. A persistência aqui serve a
*análises futuras*, não à resposta síncrona — aceitar **consistência eventual**
(o histórico em `GET /v1/conversations` pode atrasar segundos/minutos durante
um incidente de banco) é um preço baixo por manter a funcionalidade principal
(obter resposta do LLM) disponível. A DLQ garante que nenhum dado é perdido,
apenas atrasado. O envelope de resposta pode sinalizar
`status=persist_pending` para transparência. Na Parte 1 mantivemos (a) pela
simplicidade e por não introduzir infraestrutura extra no docker-compose.

### 4.2.4 Secrets Manager

- Segredos são lidos **uma vez na inicialização** (injetados como env vars pelo
  ECS via integração nativa com Secrets Manager; localmente, pydantic-settings +
  `.env`). Falha do Secrets Manager em runtime não afeta tasks já rodando —
  apenas impede novos deploys/scale-out, o que é degradação aceitável.
- Rotação de segredos: rolling deploy das tasks (as novas leem o segredo novo);
  para chaves de LLM, manter duas chaves válidas durante a janela de rotação.

### 4.2.5 AZ / Região

- **Multi-AZ em todas as camadas**: API Gateway (serviço regional, multi-AZ por
  natureza) na borda; ECS com `spread` por AZ
  (perda de uma AZ remove só parte das tasks, e o auto scaling repõe); Aurora
  com réplica em AZ distinta e failover automático (~30 s, absorvido pelo
  `pool_pre_ping`); NAT Gateway redundante (um por AZ) para o egress aos LLMs.
- Queda de região: coberta pelo plano de DR (4.5) — não justificamos ativo-ativo
  multi-região para este escopo.

## 4.3 Padrões transversais

- **Bulkhead** — breakers **por provider** já isolam a saúde de OpenRouter e
  Gemini (`ResilientLLMClient.breakers`). Evolução: limites de concorrência /
  pools httpx separados por provider, para que a lentidão de um não consuma as
  conexões destinadas ao outro; e pool de DB dimensionado independentemente
  (`db_pool_size`/`db_max_overflow`).
- **Load shedding** — rejeitar cedo é melhor que degradar todo mundo. Já
  implementado: rate limit por chave/IP (`app/core/ratelimit.py`,
  `rate_limit_requests`/`rate_limit_window_seconds`) responde `429
  rate_limited` antes de tocar LLM ou banco, com métrica
  `rate_limit_rejections_total`. Na nuvem, soma-se o WAF na borda.
- **Graceful degradation** — **já implementado**: cache in-memory com TTL de
  respostas do LLM (`ResponseCache`, `app/services/cache.py`), consultado no
  endpoint antes de chamar o LLM (`app/api/v1/chat.py`; chave =
  `sha256(mode + model + prompt)`, TTL `LLM_CACHE_TTL_SECONDS`, default 60 s,
  `0` desliga, máx. 1024 entradas com descarte do mais antigo). Prompts
  idênticos dentro da janela respondem em milissegundos, sem gastar quota — e
  o cache serve como amortecedor quando os providers estão degradados. É
  single-réplica (mesma limitação documentada do rate limit); em produção o
  equivalente é Redis (ElastiCache) compartilhado entre tasks.
- **Idempotência** — evolução: header `Idempotency-Key` no `POST /v1/chat`,
  persistido com a interação; retries do cliente (após timeout de rede, por
  exemplo) retornam a resposta já computada em vez de gerar chamada e registro
  duplicados. Pré-requisito para o cliente aplicar retry com segurança sobre
  os `503`.

## 4.4 Comportamento observável pelo cliente por cenário

| Cenário | Status code | Experiência do cliente | Recuperação automática? |
|---|---|---|---|
| Primário lento/instável (timeout, `5xx`) | `200` | Resposta normal, latência um pouco maior (retry com backoff ou fallback transparente) | Sim — retry + fallback |
| `429` no primário, fallback saudável | `200` | Resposta via fallback **sem** o custo de retries (fail-fast: `LLMRateLimitError` não é retentado) | Sim — próxima requisição tenta o primário de novo |
| Primário fora por minutos (breaker aberto) | `200` | Resposta via fallback sem custo de timeout (provider pulado) | Sim — half-open sonda e fecha o circuito quando voltar |
| Modelo devolve `content` nulo/vazio ou corpo malformado | `200` | Resposta via próximo provider da cadeia; o modelo é denylistado (`mark_bad`) e sai da seleção automática | Sim — auto-cura: no máximo 1 requisição afetada por modelo ruim |
| Resposta truncada pelo teto de tokens (`finish_reason=length`) | `200` | Latência maior nessa requisição (repetição única sem teto); próximas chamadas ao mesmo modelo já vão sem teto (`_uncapped_models`) | Sim — memória de truncagem por modelo |
| Catálogo de modelos fora/lento | `200` | Nenhum impacto na requisição: seleção anterior é mantida (ou default do env); só a atualização de modelo atrasa | Sim — próximo refresh do TTL tenta de novo |
| Prompt idêntico repetido dentro do TTL do cache | `200` | Resposta em milissegundos, servida do `ResponseCache` sem tocar o LLM | N/A — comportamento desejado |
| Todos os providers fora / circuitos abertos | `503` | Envelope `code=llm_unavailable` + `request_id`; mensagem informa que o prompt foi armazenado (status `failed` consultável no histórico) | Parcial — serviço se recupera sozinho; o cliente reenvia (idempotência planejada) |
| Modelo solicitado fora do allowlist | `422` | Envelope `code=model_not_allowed`, orientação a omitir o campo | N/A — erro do cliente |
| Cliente excede rate limit | `429` | Envelope `code=rate_limited`; basta aguardar a janela | Sim — janela deslizante expira |
| Banco fora (hoje) | `500` | Envelope `code=internal_error` + `request_id`; task falha o `/ready` e sai de rotação no ECS Service (atrás do API Gateway) | Sim — failover Aurora + `pool_pre_ping`; tasks voltam à rotação |
| Banco fora (evolução SQS) | `200` | Resposta do LLM normal; histórico atualiza com atraso (consistência eventual) | Sim — consumidor drena a fila; DLQ para casos persistentes |
| Queda de uma AZ | `200` | Sem impacto perceptível além de possível latência breve | Sim — multi-AZ + auto scaling |

## 4.5 Plano de DR (resumo)

- **Alvos**: RTO ≤ 1 h e RPO ≤ 5 min **intra-região** (PITR contínuo do Aurora
  dá RPO de segundos; failover multi-AZ, RTO de minutos) — adequados a um
  serviço de conversas cujo dado crítico (prompt/resposta) tolera pequena
  janela de perda. Para perda de **região**, o RPO com cópia periódica de
  snapshots é de horas; se o SLO exigir RPO de segundos cross-region, o caminho
  é **Aurora Global Database** (réplica assíncrona em região secundária).
- **Backups**: Aurora com backups contínuos e PITR (retenção ≥ 7 dias) +
  snapshots automáticos copiados para uma região secundária.
- **Infra como código**: todo o ambiente (VPC, ECS, API Gateway, Aurora,
  alarmes) é
  reproduzível via IaC (Terraform/CDK), permitindo recriar a stack na região
  secundária dentro do RTO; segredos replicados via Secrets Manager
  multi-região.
- **Estratégia**: pilot light — dado restaurado do backup, stack recriada por
  IaC, DNS (Route 53) apontado para a nova região. Ativo-ativo multi-região
  fica como evolução se o SLO exigir.
