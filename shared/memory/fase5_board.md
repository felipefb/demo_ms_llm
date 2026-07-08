# Quadro de Missão — Fase 5 (Desenho de Arquitetura AWS)

Time: `teams/team-fase5-arquitetura.yml`. 4 builders de documentação em paralelo,
seções disjuntas em docs/architecture/.

## Status

| Agente | Papel | Rodada | Veredito/Estado |
|--------|-------|--------|-----------------|
| cloud_architecture (seção 1 + índice + diagramas) | builder | 1 | ENTREGUE |
| cloud_observability (seção 2) | builder | 1 | ENTREGUE |
| database_rationale (seção 3) | builder | 1 | ENTREGUE |
| resilience_design (seção 4) | builder | 1 | ENTREGUE |
| revisão em anel (8→9→10→11→8) | validação | 2 | APROVADO (4/4; ajustes das seções 1, 2 e 4 aplicados) |
| final_reviewer | validator | 1 | APROVADO (aderência ao código confirmada por grep) |

## Registro de rodadas

### Rodada 1
- 4 builders despachados em paralelo.
- **database_rationale (Agente 10)**: ENTREGUE — criado `docs/architecture/03_banco_de_dados.md`
  (caracterização da carga citando `app/models/interaction.py` — JSONB metadata, índice
  composto user_id+created_at DESC, fluxo append-only pending→completed/failed; tabela
  comparativa PostgreSQL/Aurora vs DynamoDB vs MongoDB/DocumentDB com veredito honesto,
  reconhecendo quando DynamoDB venceria; justificativa Aurora Serverless v2 com pgvector,
  ACUs e wire-compatibility com o Postgres 16 da Parte 1; evolução: read replicas,
  particionamento por data, export S3/Athena). Sem commit.
- [resilience_design] Rodada 1 ENTREGUE: docs/architecture/04_resiliencia.md criado.
  Cobre: mapa de dependências/modos de falha (OpenRouter, Gemini, Aurora, Secrets
  Manager, AZ/região); camadas do LLM referenciando código real (timeout providers.py,
  retry tenacity + jitter, CircuitBreaker half-open, fallback, 503 llm_unavailable com
  prompt persistido via mark_failed); trade-off banco falhar vs degradar com SQS+DLQ
  (recomendado b, consistência eventual); padrões transversais (bulkhead, load shedding
  via rate limit existente, cache Redis TTL, Idempotency-Key); tabela cenário→status→
  experiência→recuperação; DR (RTO 1h/RPO 5min, PITR Aurora, IaC pilot light).
- [cloud_architecture] (Agente 8) ENTREGUE — `docs/architecture.md` criado como índice
  (visão geral em 1 parágrafo + links para as 4 seções, com os nomes reais dos arquivos
  entregues pelos outros builders). `docs/architecture/01_arquitetura_escalonamento.md`:
  arquitetura AWS de referência (Route 53 → CloudFront/WAF → API Gateway HTTP API →
  ECS Fargate → Aurora Serverless v2 + RDS Proxy; Secrets Manager; ElastiCache Redis
  promovendo o rate limit in-memory de app/core/ratelimit.py a distribuído + cache de
  respostas; SQS como válvula de escape com trade-offs e modo híbrido via flag);
  auto scaling target tracking com CPU/memória + métrica custom de requests-em-voo
  (justificada pelo perfil I/O-bound; origem em app/core/metrics.py via ADOT),
  cooldowns assimétricos (out 60s / in 300–600s), cenário numérico 10→100 rps por
  camada com tempos, custos qualitativos (Fargate domina; Spot no excedente; free tier
  LLM paga em rate limit) e Lambda vs Fargate vs EKS com veredito (Fargate).
  Diagramas: Mermaid embutido + `docs/diagrams/aws_architecture.py` (lib `diagrams`)
  com PNG gerado e commitável (`docs/diagrams/aws_architecture.png`). Nota de ambiente:
  Graphviz instalado na máquina via `winget install Graphviz.Graphviz` (15.1.0) e lib
  `diagrams` instalada no .venv — decidir se `diagrams` entra em requirements-dev.txt
  (sugestão: não; é ferramenta de docs, instruções de instalação estão no próprio
  script e na seção 1). Não editei as seções 02/03/04.
- cloud_observability (Agente 9): ENTREGUE — docs/architecture/02_observabilidade.md criado (logs CloudWatch+Insights com 4 queries sobre campos reais do access log; ADOT sidecar → AMP+AMG reutilizando dashboard local, alternativa EMF discutida; OTel → X-Ray com span llm.generate cobrindo API GW→app→LLM→banco; 4 golden signals + SLO 99,9% / p95 overhead <300ms excluindo LLM com justificativa + error budget 43,2 min/mês; 7 alarmes com thresholds justificados + 2 composite alarms; dashboard executivo+troubleshooting; exemplo passo a passo de correlação request_id/trace_id). Não editei docs/architecture.md.

### Rodada 2
- [resilience_design] Rodada 2 APLICADA em docs/architecture/04_resiliencia.md:
  (1) alinhado à seção 01 — removidas todas as menções a ALB/target group; agora
  API Gateway → ECS Service (4.2.2, 4.2.4, tabela 4.4, 4.5/IaC);
  (2) RPO ≤ 5min escopado como intra-região (PITR) com Aurora Global Database
  citado para RPO de segundos cross-region (4.5);
  (3) linha "Banco fora (hoje)" da tabela 4.4 agora cita code=internal_error.

### Rodada 2
- cloud_observability (Agente 9): AJUSTES APLICADOS em docs/architecture/02_observabilidade.md conforme parecer anel_8_sobre_9.md — (1) removidas as 3 menções a ALB: métricas de infra agora são as do API Gateway HTTP API (Count/4xx/5xx/Latency/IntegrationLatency), alternativa EMF cita percentis do API Gateway, diagrama de trace agora inicia em "API Gateway HTTP API" (seção 01 verificada: APIGW→SVC direto, sem ALB interno); (2) explicitado pipeline duplo do ADOT: prometheusremotewrite→AMP (dashboards/alertas) + awsemf→CloudWatch apenas para a métrica de scaling do target tracking (~20 req em voo/task), com a derivação via Little's Law nomeada e gauge http_requests_in_progress proposto como melhoria futura (item 3 do parecer). Nenhuma outra seção editada.
- [cloud_architecture] (Agente 8) AJUSTES APLICADOS em docs/architecture/01_arquitetura_escalonamento.md
  conforme anel_11_sobre_8.md (e alinhamento com anel_8_sobre_9.md):
  (1) Trade-off do timeout completado: citado o default real `llm_timeout_seconds=30.0`
  (`app/core/config.py:66`) > teto 29s do API GW; pior caso com `llm_max_retries=2` +
  fallback pode passar de 90s; documentado deadline total por request ~25s, override
  via env em produção (ex.: LLM_TIMEOUT_SECONDS=10) e SQS para casos longos; resumo 1.6
  item 1 atualizado.
  (2) Elo API GW→ECS explicitado: **VPC Link → ALB interno (health check /ready) →
  tasks**, no texto (1.2), no Mermaid e no script `docs/diagrams/aws_architecture.py`
  (PNG regenerado). Isso torna a seção 04 (que cita ALB/target group) consistente.
  NOTA para o anel: a seção 02 removeu menções a ALB na Rodada 2 assumindo "APIGW→SVC
  direto"; com o ALB interno agora explícito, as métricas de API GW da 02 continuam
  corretas como borda, mas a 02 pode opcionalmente reintroduzir métricas de target
  group do ALB interno como complemento (não bloqueante).
  (3) Métrica de scaling corrigida: explicitado que `http_requests_in_progress` NÃO
  existe em `app/core/metrics.py`; derivação atual via lei de Little sobre
  `http_request_duration_seconds`/`http_requests_total` no ADOT, com gauge
  `http_requests_in_progress` proposto como evolução; pipeline duplo ADOT (AMP para
  dashboards + EMF/CloudWatch só para a métrica de scaling) alinhado com a seção 02.
  (4) Cenário 1.3 agora referencia a tabela 4.4 de 04_resiliencia.md; corrigida também
  a referência interna do SQS (1.4→1.3).

## [Anel 8 → 9] Rodada 2 — Veredito: APROVADO (2026-07-07)

Re-verificação dos 3 itens do parecer `fase5_reviews/anel_8_sobre_9.md` contra as
versões atuais de 01_arquitetura_escalonamento.md e 02_observabilidade.md:
(1) menções a ALB removidas da seção 2 (métricas nativas do API GW HTTP API; trace
diagram corrigido); ALB interno via VPC Link agora explícito na seção 1 — métricas
de target group na seção 2 ficam como complemento opcional, não bloqueante.
(2) Pipeline duplo ADOT (remote_write→AMP + awsemf→CloudWatch só para a métrica de
scaling) presente e idêntico nas duas seções.
(3) Derivação de in-flight via lei de Little + gauge `http_requests_in_progress`
como evolução futura, alinhada entre as seções 1 e 2.
Detalhes na seção "Rodada 2" do parecer.

## [Anel 11 → 8] Rodada 2 — Veredito: APROVADO (2026-07-07)

Re-verificação dos 3 itens do parecer `fase5_reviews/anel_11_sobre_8.md` contra a
versão atual de 01_arquitetura_escalonamento.md:
(1) Trade-off do timeout completo: default 30s > teto 29s citado, override
`LLM_TIMEOUT_SECONDS` em produção, deadline total ~25s (retries+fallback dentro do
budget) e SQS para casos longos — também em 1.6.
(2) Elo API GW → VPC Link → ALB interno explícito no texto e no Mermaid (sintaxe
válida); compatível com a seção 04 atual ("ECS Service atrás do API Gateway").
Nota cosmética não bloqueante: 1.2 diz que "a seção 4 referencia" o ALB, mas a 04
não o nomeia mais.
(3) Referência cruzada 1.3 → tabela 4.4 presente.
Detalhes na seção "Rodada 2" do parecer.

## Encerramento — FASE 5 CONCLUÍDA

Placar final: 5/5 APROVADO (anel 8→9 e 11→8 na Rodada 2 após correções;
9→10 e 10→11 na Rodada 1; gate de aderência ao código do final_reviewer).
docs/architecture.md + 4 seções consistentes entre si e com o código real;
diagrama Mermaid + PNG gerado. Gate da Fase 6 liberado.
Notas não bloqueantes remanescentes (candidatas à Fase 6/7): 1.2 diz que a
seção 4 referencia o ALB (a 04 não o nomeia mais — cosmético); métricas do
ALB interno como complemento opcional na seção 2; "append-only" → "quase
append-only" na seção 3; decidir se a lib diagrams entra em requirements-dev
(sugestão dos agentes: não).
