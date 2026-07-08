
## Agente 10 — Banco de Dados (revisão pós-melhorias Parte 1)
- Arquivo: docs/architecture/03_banco_de_dados.md (apenas esta seção editada; sem commit).
- 3.1: nova linha na tabela de padrões — cache TTL reduz chamadas ao LLM mas NÃO reduz escrita (create_pending roda antes do lookup; cada request, inclusive cache hit, é persistido). Parágrafo novo: `response` agora guarda a frase direta normalizada (formatting.py); dados/contexto/fontes não são persistidos hoje e são candidatos ao JSONB metadata.
- 3.3: argumento JSONB reforçado com o caso concreto da resposta estruturada (consultas via jsonb_array_elements).
- 3.4: novo item de evolução — persistir o structured em metadata JSONB ou coluna dedicada `structured JSONB` + GIN via Alembic, habilitando analytics sobre `dados` sem ETL.
- Veredito PostgreSQL/Aurora mantido e fortalecido; comparação com DynamoDB/DocumentDB inalterada.

## Agente 9 — Observabilidade (revisão pós-melhorias Parte 1)
- Arquivo: docs/architecture/02_observabilidade.md (apenas esta seção editada; sem commit).
- 2.1: 3 queries novas de Logs Insights — (e) trocas de modelo pelo seletor (`model_selector`), (f) denylist (`denylisted after permanent error`), (g) truncagem pelo teto (`truncated by token cap; retrying uncapped`) — textos conferidos contra os logs reais em app/services/providers.py e model_selector.py.
- 2.2: tabela de métricas ganhou `llm_cache_hits_total` (indicador de custo evitado) e `llm_selected_model{provider,mode,model}` (gauge 1=ativo; série antiga removida na troca — cardinalidade controlada). `rate_limit_rejections_total` já constava.
- 2.4: parágrafo sobre efeito do cache nos SLOs — p95 excluindo LLM continua válido (hit não tem llm_latency a descontar); queda de hit rate desloca p95 total ao piso físico de 8–15s (coerente com 5.4). Error budget: cache protege o budget durante indisponibilidade dupla dos providers (repetições respondem 200 do cache).
- 2.5: alarmes novos A8 (queda de cache hit rate vs. baseline 24h — custo, nunca page) e A9 (trocas de modelo em rajada >3/h — catálogo instável ou denylist em cascata).
- 2.6: painel executivo novo de cache hit rate; painel de troubleshooting com tabela de modelo ativo + annotations de troca de modelo nos gráficos (evento operacional); painel de logs agora linka as queries (f) e (g).

## Agente 11 — Resiliência (revisão pós-melhorias) — 2026-07-08

Atualizei `docs/architecture/04_resiliencia.md` (somente a seção 4):

- 4.2.1 (LLM) reescrita em 9 degraus refletindo o código atual: timeout →
  classificação em 3 classes (`LLMRateLimitError` separada) → 429 fail-fast
  direto ao fallback (sem retry) → retry backoff+jitter só p/ 5xx/timeout →
  breaker → fallback (com inversão da cadeia p/ Gemini primário quando
  `LLM_WEB_SEARCH=true`) → auto-cura por denylist (`_mark_bad_if_auto` /
  `ModelSelector.mark_bad`) → teto adaptativo com memória de truncagem
  (`_uncapped_models`) → 503 honesto com prompt salvo.
- Nova dependência mapeada: catálogos de modelos (nova subseção 4.2.2;
  Banco/Secrets/AZ renumerados p/ 4.2.3–4.2.5). Falha de catálogo mantém a
  última seleção ou cai no default do env — nunca afeta o request.
- Cache TTL (`app/services/cache.py` + `app/api/v1/chat.py`) agora documentado
  como graceful degradation JÁ implementada (era "evolução").
- Tabela 4.4 ganhou 5 linhas novas: 429 no primário c/ fallback ok, content
  nulo/malformado (denylist), truncagem por teto, catálogo fora, cache hit.

Sem commit, conforme instrução.

## Agente 8 — Arquitetura/Escalonamento + Decisões AWS (revisão pós-melhorias) — 2026-07-08

T1 — `docs/architecture/01_arquitetura_escalonamento.md` atualizado p/ Parte 1 atual:
- Tabela 1.1: cache TTL (`app/services/cache.py`) citado ao lado do rate limit
  como par a ser **promovido** ao Redis (mesma chave/TTL); linha nova p/ a
  seleção automática de modelos (egress via NAT, startup + TTL 1h, falha
  tolerada — não é dependência dura); linha de resiliência com ordem da cadeia
  por config (`LLM_WEB_SEARCH=true` → Gemini primário) e 429 fail-fast.
- Mermaid 1.2: providers viram primário/secundário condicionais, aresta nova
  para catálogos /models, Redis rotulado como promoção do in-memory.
- Bullet ElastiCache reescrito (deixa de ser hipotético: troca de store, não de
  desenho); bullet novo sobre egress p/ catálogos; parágrafo do budget de 29s
  registra que fail-fast 429 e cache já encurtam o pior caso.
- Cenário numérico recalibrado com as medições da seção 5.4 (~6s média em vez
  de 2s hipotéticos): base 10 rps → ~4 tasks; pico 100 rps → ~25–30 tasks em
  ~4–6 min; teto do auto scaling coerente (30–40); linhas ElastiCache (cache
  amortece o próprio pico) e LLM (fail-fast) atualizadas; resumo 1.6 idem.
- PNG regenerado: `docs/diagrams/aws_architecture.py` atualizado (Redis,
  primário condicional, nó de catálogos, aresta 429 fail-fast) e executado →
  `docs/diagrams/aws_architecture.png`.

T2 — `docs/architecture/DECISOES_AWS.md` criado (PT-BR, ADRs curtos):
- 9 ADRs, cada um com decisão, justificativa e alternativas viáveis rejeitadas
  com motivo: ECS Fargate (vs Lambda/EKS/App Runner/EC2), API GW HTTP API
  (vs ALB puro/REST API GW), Aurora Sv2 (vs RDS provisionado/DynamoDB/
  DocumentDB), ElastiCache Redis (vs in-memory/DAX/Memcached), Secrets Manager
  (vs SSM Parameter Store — config não-segredo pode ir ao SSM), SQS (vs SNS/
  EventBridge/Kinesis), CloudWatch+AMP+X-Ray (vs Datadog/Grafana Cloud),
  VPC Link+ALB interno (vs Cloud Map direto), multi-AZ single-region
  (vs multi-region ativo-ativo, c/ caminho Aurora Global da 4.5). Tabela
  síntese ao final. Consistência conferida com as seções 01–05.
- Link adicionado em `docs/architecture.md` ("Complemento transversal"); de
  passagem, corrigido o índice: a descrição da seção 4 estava pendurada no
  item 5 — cada item agora descreve a própria seção.

Sem commit, conforme instrução.
