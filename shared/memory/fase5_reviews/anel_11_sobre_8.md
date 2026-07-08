# Parecer — Anel 11 (Resiliência) sobre Anel 8 (Arquitetura/Escalonamento)

Revisor: Agente 11 (autor de `docs/architecture/04_resiliencia.md`)
Alvos: `docs/architecture/01_arquitetura_escalonamento.md` e `docs/architecture.md` (índice)
Data: 2026-07-07

## Veredito: AJUSTES

## O que está consistente (verificado)

1. **Índice (`docs/architecture.md`)** — linka corretamente as 4 seções; todos os
   arquivos existem em `docs/architecture/` (01–04). OK.
2. **Mermaid (1.2)** — sintaxe válida de `flowchart LR`: subgraphs fechados, labels
   com `<br/>`, arestas pontilhadas `-.texto.->` bem formadas, nós `[[...]]`/`[(...)]`
   corretos. Renderiza no GitHub/mermaid-live. OK.
3. **Coerência com a seção 04** — camadas de defesa (WAF → API GW throttle → rate
   limit da app), SQS como válvula de escape/modo degradado, ElastiCache para rate
   limit distribuído + cache de respostas, RDS Proxy para tempestade de conexões,
   multi-AZ com piso de 2 tasks: tudo alinhado com 4.2/4.3 e com o código real
   (`resilience.py`, `ratelimit.py`, `database.py`, `health.py`). OK.
4. **Cenário numérico 10→100 rps** — premissa de 2s de latência LLM e ~20 req em voo
   por task não contradiz retries/breaker do código; o cenário reconhece o provider
   LLM como gargalo real e referencia o breaker corretamente. OK.

## Ajustes solicitados (objetivos)

1. **Trade-off dos 29s do API Gateway está incompleto (1.2 / 1.6).** O texto diz que
   "o timeout do cliente httpx deve ficar abaixo de 29s", mas:
   - o **default real** é `llm_timeout_seconds = 30.0` (`app/core/config.py:66`) —
     acima dos 29s. Registrar que em produção o valor deve ser reduzido (ex.: 20s) ou
     citar o override explícito no task definition;
   - o pior caso síncrono não é 1 timeout: com até 2 retries com backoff
     (`llm_max_retries`) e fallback para o segundo provider, o orçamento acumulado
     pode ultrapassar largamente 29s mesmo com timeout unitário < 29s. Documentar um
     **deadline total por request** (budget compartilhado entre retries/fallback) ou
     ao menos citar essa limitação junto ao trade-off.
2. **ALB vs API Gateway — inconsistência entre seções.** A seção 01 escolhe API
   Gateway *em vez de* ALB (1.2), mas a minha seção 04 menciona "target group do ALB"
   e "ALB em ≥2 AZs" (4.2.2, 4.2.4, 4.5). Alinhar terminologia: ou a seção 01
   explicita que API GW → VPC Link → **ALB interno** → ECS (o que tornaria as duas
   coerentes e é o padrão usual para HTTP API + Fargate), ou a 04 é corrigida. A
   correção na 04 é minha; peço que a 01 declare explicitamente o elo API GW→ECS
   (hoje o diagrama liga APIGW → SVC direto, sem dizer se é VPC Link/ALB/Cloud Map).
3. **Menor (não bloqueante):** em 1.3 o cenário diz que excesso na janela de scale-out
   recebe "429/latência alta"; sugerir referência cruzada à tabela 4.4 para manter a
   experiência do cliente documentada em um único lugar.

## Conclusão

Arquitetura sólida e ancorada no código; os ajustes 1 e 2 são de consistência
documental (timeout default 30s vs teto 29s, e o elo API GW→ECS/ALB) e não exigem
mudança de código.

## Rodada 2 — Veredito: APROVADO

Re-verificação pontual dos 3 itens contra a versão atual da seção 1:

1. **Trade-off do timeout — RESOLVIDO.** 1.2 explicita que o default
   `llm_timeout_seconds=30.0` excede o teto de 29s, exige override via env em
   produção (ex.: `LLM_TIMEOUT_SECONDS=10`), trata retries+fallback como deadline
   total por request de ~25s e encaminha casos longos ao modo assíncrono SQS.
   Consolidado em 1.6 (decisão 1).
2. **Elo API GW → ECS — RESOLVIDO.** Texto (1.2) explicita VPC Link → ALB interno
   com health check `/ready`; o Mermaid inclui o nó `ALB` e as arestas
   `APIGW -->|VPC Link| ALB --> SVC` (sintaxe válida). Minha seção 04
   (generalizada na Rodada 2 para "ECS Service atrás do API Gateway") permanece
   compatível — o ALB interno é detalhe de implementação do elo, sem contradição.
   Nota cosmética (não bloqueante): 1.2 diz "é esse ALB interno que a seção 4
   referencia", mas a 04 atual não nomeia mais o ALB; frase pode ser suavizada
   em edição futura.
3. **Referência cruzada 1.3 → 4.4 — RESOLVIDA.** Presente na linha "Janela de
   3–5 min do scale-out" do cenário numérico, com link para `04_resiliencia.md`.
