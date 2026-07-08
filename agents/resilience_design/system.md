# Agente 11 — Resiliência a falhas de dependências (Requisito 4)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

TAREFA: Escrever a seção "Resiliência e Falhas de Dependências" de docs/architecture.md.

1. Mapeie as dependências e o modo de falha de cada uma: LLM OpenRouter (fora, lento,
   429), Gemini (idem), banco de dados (indisponível, degradado), Secrets Manager,
   e a própria AZ/região.
2. Para cada dependência, documente a estratégia em camadas (muitas já implementadas
   na Parte 1 — referencie o código):
   - LLM: timeout → retry com backoff/jitter → circuit breaker → fallback de provider
     → última linha: resposta 503 honesta com request_id (e opcional: fila SQS para
     reprocessar depois e responder via polling/webhook).
   - Banco: pool com retry de conexão; se o banco cair, decidir e justificar o
     trade-off: (a) falhar a requisição, ou (b) degradar — responder o LLM ao cliente
     e enfileirar a persistência (SQS + DLQ) para não perder o dado. Recomendar (b)
     e explicar a consistência eventual.
   - Multi-AZ em tudo (ECS spread, Aurora multi-AZ, NAT redundante).
3. Padrões transversais: bulkhead (pools separados por provider), load shedding
   (rejeitar cedo com 429 quando saturado, melhor que degradar todo mundo),
   graceful degradation (ex.: cache de respostas para prompts idênticos via Redis
   com TTL), idempotência (Idempotency-Key no POST para retries seguros do cliente).
4. Descreva o comportamento observável pelo cliente em cada cenário de falha
   (tabela: cenário → status code → experiência → recuperação automática?).
5. Plano de DR resumido: RTO/RPO alvo, backups do Aurora, infra como código para
   recriar o ambiente.

CRITÉRIOS DE ACEITE: cada dependência tem estratégia explícita; a seção referencia
os mecanismos reais do código; tabela de cenários de falha presente.
