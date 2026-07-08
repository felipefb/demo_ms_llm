# Parecer — Observabilidade (Fase 1 / Rodada 1)

Revisor: Agente 5 (app_observability)
Escopo: request_id/contextvar, latency_ms, plugabilidade da instrumentação da Fase 5.

## Veredito: APROVADO

O scaffold atende aos pontos do meu domínio; os itens abaixo são observações
não bloqueantes que serão resolvidos na Fase 5 ou merecem atenção do time.

## Análise

1. **request_id (app/core/request_id.py + middleware.py)** — correto.
   ContextVar setado no `dispatch` antes de `call_next`; como contextvars são
   copiados para a task filha do `BaseHTTPMiddleware`, o valor chega a handlers,
   repositório e exception handlers (`errors.py` usa `get_request_id()` no
   envelope). O header `X-Request-ID` é lido do cliente ou gerado (uuid4) e
   ecoado na resposta. OK.

2. **latency_ms** — medido em dois pontos coerentes: latência HTTP total no
   middleware (log de acesso) e latência da chamada LLM no endpoint
   (`ChatResponse.latency_ms`). Atende ao requisito.

3. **Plugabilidade da Fase 5** — boa:
   - Logging centralizado em `setup_logging()` (app/core/logging.py) com
     `RequestIdFilter`; trocar por structlog JSON é substituição localizada,
     sem tocar em outros módulos.
   - `LLMClient` é um Protocol (app/services/llm.py); métricas
     `llm_requests_total`/`llm_latency_seconds` e spans OTel entram como
     decorator/wrapper do client real sem refatorar o endpoint.
   - Persistência isolada em repository — span de persistência plugável.
   - `create_app()` como factory facilita `Instrumentator().instrument(app)` e
     `FastAPIInstrumentor`.

## Observações (não bloqueantes — tratarei na Fase 5)

- **500 não tratados perdem header e access log**: exceções não capturadas
  sobem pelo `BaseHTTPMiddleware` (o handler genérico `Exception` roda no
  `ServerErrorMiddleware`, fora do RequestIdMiddleware), então as linhas
  `response.headers[...]` e o log de acesso não executam nesse caso. Na Fase 5
  migrarei para middleware ASGI puro com try/finally (também evita overhead e
  problemas de streaming do BaseHTTPMiddleware).
- Log de acesso ainda é texto plano e não inclui `user_id`, tamanho/hash do
  prompt nem `status_code` como campos estruturados — será substituído por
  structlog JSON na Fase 5 (previsto no escopo, não é defeito da Fase 1).
- Sugestão: expor `latency_ms` também como header de resposta
  (`X-Response-Time-Ms`) no middleware — barato e útil para debug.
