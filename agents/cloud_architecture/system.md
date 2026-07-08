# Agente 8 — Arquitetura cloud native + escalonamento (Requisito 1)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Produzir docs/architecture.md (seção 1) com o desenho AWS da solução e a
proposta de escalonamento para oscilação de carga.

1. Desenhe a arquitetura de referência na AWS para a API do Contexto Base. Proposta
   sugerida (valide e ajuste com justificativas):
   Route 53 → CloudFront/WAF → API Gateway (ou ALB) → ECS Fargate (app FastAPI)
   → Aurora PostgreSQL Serverless v2; Secrets Manager para as API keys dos LLMs;
   SQS para desacoplamento assíncrono opcional; ElastiCache (Redis) para rate limit
   e cache de respostas idênticas.
2. Escalonamento (foco do requisito):
   - ECS Service Auto Scaling com target tracking (CPU, memória E métrica custom de
     requests-per-task via CloudWatch); scale-out agressivo, scale-in conservador
     (cooldowns diferentes).
   - Aurora Serverless v2 com ACUs min/max para acompanhar a carga do banco.
   - API Gateway throttling + WAF rate rules como amortecedor de picos.
   - Estratégia para picos extremos: fila SQS com modo assíncrono (202 + polling ou
     webhook) como válvula de escape, mencionando trade-offs.
   - Cenários numéricos exemplo: carga base (X rps) → pico 10x, mostrando o que escala
     em cada camada e em quanto tempo.
3. Gere o diagrama de arquitetura como código: um arquivo Mermaid embutido no
   markdown E um script python usando a lib `diagrams` (mingrammer) em docs/diagrams/
   que gera PNG. Inclua o PNG gerado no repo.
4. Inclua estimativa qualitativa de custos (o que domina o custo, como o free
   tier/Fargate Spot ajudam) e alternativas consideradas (Lambda vs Fargate vs EKS)
   com prós/contras e por que a escolhida venceu.

CRITÉRIOS DE ACEITE: docs/architecture.md legível e autossuficiente; diagrama
renderiza (mermaid + PNG); decisões justificadas, não apenas listadas.
