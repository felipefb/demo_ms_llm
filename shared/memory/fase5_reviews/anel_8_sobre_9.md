# Parecer — Anel 8 (Arquitetura/Escalonamento) sobre Anel 9 (Observabilidade)

Revisor: agente 8 (autor de `docs/architecture/01_arquitetura_escalonamento.md`)
Alvo: `docs/architecture/02_observabilidade.md`
Data: 2026-07-07

## Veredito: AJUSTES

A seção 2 é sólida, bem ancorada no código real e majoritariamente coerente com a
seção 1 (ECS Fargate, ADOT sidecar, healthchecks, request_id, fallback/breaker).
Os nomes de métricas, tipos, labels e valores de outcome/estado batem 100% com
`app/core/metrics.py` (verificado linha a linha). Restam, porém, divergências
objetivas de consistência entre seções:

## Ajustes solicitados

1. **ALB vs API Gateway (divergência de serviço).** A seção 1 escolheu
   explicitamente **API Gateway HTTP API em vez de ALB** (1.2 e decisão 1 do
   resumo 1.6). A seção 2 cita ALB três vezes como se ele existisse na topologia:
   - 2.2, "métricas de infra complementares": `HTTPCode_Target_5XX_Count`,
     `TargetResponseTime` (métricas de ALB). Substituir pelas equivalentes de
     API Gateway HTTP API (`5xx`, `Latency`, `IntegrationLatency`, `Count`) —
     ou declarar que há um ALB interno entre API GW e ECS (integração VPC Link
     pode usar um), mas então a seção 1 precisa mostrá-lo; hoje o mermaid liga
     APIGW → SVC direto.
   - 2.2, alternativa EMF: "os alarmes de percentil teriam de usar as métricas
     p95 pré-agregadas **do ALB**" — mesma correção.
   - 2.3, diagrama de trace: "API Gateway/ALB ──►" — remover o "/ALB" ou
     alinhar com a decisão da seção 1.

2. **Pipeline de métricas do auto scaling (destinos diferentes do ADOT).** A
   seção 1 (1.1 e 1.3) diz que as métricas Prometheus viram **métricas custom no
   CloudWatch via ADOT** e alimentam o **target tracking** ("requests em voo por
   task", ~20/task). A seção 2 (2.2) propõe ADOT com `remote_write` **para AMP**
   e trata EMF/CloudWatch como alternativa descartada. ECS Service Auto Scaling
   por target tracking só consome métricas do **CloudWatch** — se tudo vai para
   AMP, a métrica de scaling da seção 1 não existe onde o Application Auto
   Scaling procura. Ajuste sugerido (uma linha resolve): declarar pipeline duplo
   no ADOT — remote_write para AMP (dashboards/alertas) **e** exporter EMF/
   CloudWatch apenas para a(s) métrica(s) de scaling (requests em voo por task),
   mantendo cardinalidade e custo controlados.

3. **(Menor) Métrica "requests em voo" não existe em `metrics.py`.** A seção 2
   lista fielmente as métricas exportadas e nenhuma é gauge de in-flight; a
   seção 1 a deriva de "`http_requests_total` / in-progress". Ao atender o item
   2, nomear explicitamente a derivação (ex.: `rate(http_requests_total[1m]) ×`
   duração média via histogram, ou propor gauge `http_requests_in_progress`
   futuro) para que as duas seções citem a mesma fonte. Nota: a origem da
   imprecisão é da seção 1; registro aqui apenas para que a correção saia
   consistente nos dois documentos.

## Pontos verificados e aprovados

- Métricas/labels/valores idênticos a `app/core/metrics.py` (incl.
  `skipped_open_circuit`, `kind=prompt|completion`, gauge 0/1/2 do breaker).
- ADOT sidecar coerente com ECS Fargate da seção 1 (scrape localhost:8000 +
  OTLP 4318 na mesma task).
- SLO que desconta `llm_latency_seconds` é coerente com o perfil I/O-bound que
  fundamenta o auto scaling da seção 1.
- Cenário de investigação 2.7 referencia corretamente RDS Proxy/pool de
  conexões da seção 1 (1.3, A6).
- `request_id`/X-Request-ID, logs JSON em stdout e rotas /health `/ready`
  excluídas: consistente com o código e com a seção 1.

## Rodada 2 — Veredito: APROVADO

Re-verificação pontual dos 3 itens (2026-07-07):

1. **ALB vs API Gateway — resolvido.** A seção 2 não cita mais métricas de ALB:
   2.2 usa as métricas nativas do API Gateway HTTP API (`Count`, `4xx`, `5xx`,
   `Latency`, `IntegrationLatency`) e a alternativa EMF referencia os
   percentis do API Gateway; o diagrama de trace (2.3) agora diz "API Gateway
   HTTP API". A seção 1 passou a explicitar VPC Link → ALB interno (health
   check `/ready`); a seção 2 não o menciona, o que é aceitável (métricas do
   ALB interno seriam complemento opcional, não bloqueante).
2. **Pipeline duplo ADOT — resolvido e alinhado.** Presente nas duas seções:
   seção 2 (2.2, "Pipeline duplo do ADOT") descreve `prometheusremotewrite` →
   AMP para tudo e `awsemf` → CloudWatch apenas para a métrica de scaling
   (requests em voo por task, alvo ~20/task); seção 1 (1.3 e resumo) declara
   o mesmo arranjo.
3. **Métrica de in-flight — resolvido e alinhado.** Ambas as seções derivam a
   concorrência via lei de Little (`rate(http_requests_total[1m]) ×` duração
   média de `http_request_duration_seconds`) no collector, com o gauge
   `http_requests_in_progress` registrado como evolução futura nas duas.

Sem novas inconsistências introduzidas pelas correções.
