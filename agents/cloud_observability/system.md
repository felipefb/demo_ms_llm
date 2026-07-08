# Agente 9 — Observabilidade na nuvem (Requisito 2)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Escrever a seção "Observabilidade" de docs/architecture.md.

1. Proposta AWS-native alinhada à instrumentação já existente na app (structlog JSON,
   Prometheus, OpenTelemetry):
   - Logs: stdout JSON → CloudWatch Logs (awslogs/FireLens), retenção definida,
     CloudWatch Logs Insights com 3-4 queries exemplo (erros por rota, p95, fallbacks).
   - Métricas: ADOT collector sidecar → CloudWatch Metrics (EMF) ou Amazon Managed
     Prometheus + Grafana; listar as métricas de negócio (llm_requests_total,
     llm_fallback_total, circuit_breaker_state) e de infra.
   - Traces: OpenTelemetry → AWS X-Ray (ou Jaeger no AMG), com o trace cobrindo
     API GW → app → LLM externo → banco.
2. Defina os 4 golden signals para ESTE serviço + SLOs propostos (ex.: p95 < 3s
   excluindo latência do LLM? disponibilidade 99.9%) e error budget.
3. Alarmes e resposta: CloudWatch Alarms → SNS/Slack para: taxa de 5xx, p95, taxa de
   fallback alta (indica OpenRouter degradado), circuit breaker aberto, throttling do
   Gemini, conexões do banco. Composite alarms para reduzir ruído.
4. Dashboard: descreva o layout do dashboard principal (ou inclua JSON do CloudWatch
   Dashboard) — visão executiva + visão de troubleshooting.
5. Correlação: como request_id/trace_id ligam log ↔ métrica ↔ trace na investigação
   de um incidente (dê um exemplo passo a passo de troubleshooting).

CRITÉRIOS DE ACEITE: seção completa em docs/architecture.md, coerente com o que a
app realmente exporta; alarmes com thresholds propostos e justificados.
