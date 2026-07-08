# Arquitetura da Solução na AWS

Este documento descreve como o micro-serviço de chat com LLM deste repositório
(FastAPI + PostgreSQL 16 + OpenRouter com fallback Gemini, retry e circuit breaker em
`app/services/resilience.py`) seria implantado e operado em produção na AWS: a
arquitetura de referência e sua estratégia de escalonamento para carga oscilante, a
observabilidade na nuvem, a justificativa da escolha do banco de dados e o desenho de
resiliência a falhas de dependências. Cada tema tem uma seção própria, autossuficiente:

## Seções

1. [Arquitetura cloud native e escalonamento](architecture/01_arquitetura_escalonamento.md)
   — arquitetura de referência (Route 53 → CloudFront/WAF → API Gateway → ECS Fargate →
   Aurora Serverless v2), auto scaling com métrica custom, cenário base → pico 10×,
   custos e alternativas (Lambda vs Fargate vs EKS). Diagramas em [docs/diagrams/](diagrams/).
2. [Observabilidade na nuvem](architecture/02_observabilidade.md)
   — logs, métricas (ADOT → CloudWatch/AMP), tracing X-Ray, SLOs, alarmes e dashboards.
3. [Escolha do banco de dados](architecture/03_banco_de_dados.md)
   — caracterização da carga, comparação PostgreSQL/Aurora vs DynamoDB vs DocumentDB e veredito.
4. [Resiliência a falhas de dependências](architecture/04_resiliencia.md)
   — modos de falha, timeouts/retry/circuit breaker/fallback, degradação controlada e DR.
5. [Evoluções pós-entrega — camada de inteligência de LLM](architecture/05_evolucoes.md)
   — seleção automática de modelos, cache TTL, Gemini primário com grounding,
   fail-fast 429, `response_mode` e política de frescor.

Complemento transversal:

- [Decisões AWS (ADRs)](architecture/DECISOES_AWS.md) — todas as decisões AWS da
  arquitetura final em formato ADR curto, cada uma com as alternativas igualmente
  viáveis que não foram usadas e por quê.
