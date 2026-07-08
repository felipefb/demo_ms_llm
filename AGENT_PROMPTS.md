# Prompts de Agentes — Desafio Técnico Itaú (micro-serviço LLM)

Cada agente abaixo é autônomo e autocontido: pode ser executado de forma independente
(em sessões separadas de um agente de IA, subagentes ou em sequência). Todos compartilham
o mesmo **Contexto Base** — cole-o no início de cada prompt.

---

## Contexto Base (colar no início de TODOS os prompts)

```
CONTEXTO DO PROJETO:
Estou construindo um micro-serviço para um desafio técnico com os seguintes requisitos:
- Receber prompts de usuários via API REST (POST /v1/chat), com payload de entrada
  { "userId": "...", "prompt": "..." } e resposta contendo
  { id, userId, prompt, response, model, timestamp } (payloads podem ser melhorados).
- Persistir prompt e resposta em banco de dados para análises futuras.
- Invocar um LLM em tempo real via OpenRouter (free tier) com fallback para Gemini Free Tier.
- A solução deve ser resiliente, segura, observável e performática.

DECISÕES DE STACK (usar sempre, para manter consistência entre agentes):
- Python 3.12 + FastAPI + Uvicorn, gerenciado com uv (ou pip + requirements.txt)
- PostgreSQL 16 como banco principal, acesso via SQLAlchemy 2.0 async + Alembic (migrations)
- httpx (async) para chamadas ao LLM
- Docker + docker-compose para rodar localmente
- pytest + pytest-asyncio para testes; ruff para lint/format; mypy para tipos
- Estrutura do projeto: app/ (api/, core/, services/, repositories/, models/, schemas/),
  tests/, docs/, migrations/
- Configuração 100% via variáveis de ambiente (pydantic-settings), com .env.example no repo
  e NENHUM segredo commitado.

O diretório do projeto é a raiz deste repositório. Trabalhe de forma autônoma: não pare
para pedir confirmação; tome decisões razoáveis e documente-as. Ao final, liste o que
foi criado/alterado e como validar.
```

---

# PARTE 1 — Implementação e Código

## Agente 1 — Scaffold do serviço e API REST

```
TAREFA: Criar o esqueleto do micro-serviço e o endpoint principal.

1. Crie a estrutura de projeto conforme o Contexto Base (app/, tests/, docs/, migrations/),
   com pyproject.toml, .gitignore para Python, .env.example e configuração via
   pydantic-settings (app/core/config.py).
2. Implemente POST /v1/chat:
   - Request (Pydantic): user_id (str, obrigatório), prompt (str, obrigatório,
     1..4000 chars), opcionais: model (str), metadata (dict).
   - Response: id (uuid), user_id, prompt, response, model, tokens de uso se disponíveis,
     timestamp (UTC ISO-8601), latency_ms.
   - Validação com mensagens de erro claras (422 padronizado).
3. Implemente GET /health (liveness) e GET /ready (readiness — checa DB e conectividade
   de saída) e GET /v1/conversations/{user_id} (histórico paginado, será ligado ao banco
   pelo Agente 2 — deixe a interface do repositório pronta com implementação em memória).
4. Padronize TODAS as respostas de erro em um envelope único
   { "error": { "code", "message", "request_id" } } via exception handlers globais.
5. Adicione middleware de request_id (header X-Request-ID, gera uuid se ausente) e
   propague-o em logs e respostas.
6. Exponha OpenAPI/Swagger em /docs com descrições e exemplos nos schemas.
7. Crie testes de contrato do endpoint (payload válido, inválido, prompt vazio,
   prompt acima do limite) usando TestClient com o LLM mockado.

CRITÉRIOS DE ACEITE: `uvicorn app.main:app` sobe sem erros; /docs renderiza;
pytest verde; nenhuma chamada externa real nos testes.
```

## Agente 2 — Persistência (PostgreSQL)

```
TAREFA: Implementar a camada de persistência dos prompts e respostas.

1. Modele a tabela `interactions`: id (uuid pk), user_id (indexado), prompt (text),
   response (text, nullable), model (varchar), provider (varchar), status
   (enum: pending|completed|failed), error_detail (text nullable), latency_ms (int),
   prompt_tokens/completion_tokens (int nullable), created_at/updated_at (timestamptz).
   Índice composto (user_id, created_at DESC) para o histórico.
2. Configure SQLAlchemy 2.0 async (asyncpg) com pool configurável por env var e
   Alembic com a migration inicial.
3. Implemente o padrão Repository (app/repositories/interaction_repository.py) com:
   create_pending(), mark_completed(), mark_failed(), list_by_user() paginado
   (limit/offset ou cursor). O serviço grava o prompt ANTES de chamar o LLM
   (status=pending) e atualiza depois — assim nenhum prompt se perde se o LLM falhar.
4. Ligue o repositório real ao endpoint /v1/chat e ao GET /v1/conversations/{user_id}.
5. Atualize o docker-compose com serviço postgres:16 (volume nomeado, healthcheck)
   e faça a app aguardar o banco ficar saudável.
6. Testes: repositório com banco real via testcontainers (ou fixture com
   docker-compose) + testes do fluxo pending→completed e pending→failed.

CRITÉRIOS DE ACEITE: `docker compose up` sobe app+banco; migration roda
automaticamente (ou via comando documentado); um POST /v1/chat gera linha no banco
mesmo quando o LLM falha (status=failed); pytest verde.
```

## Agente 3 — Integração com LLM (OpenRouter + fallback Gemini)

```
TAREFA: Implementar o cliente LLM resiliente.

1. Crie uma abstração LLMProvider (interface com generate(prompt, model) -> LLMResult
   contendo text, model, provider, usage) e duas implementações:
   - OpenRouterProvider: POST https://openrouter.ai/api/v1/chat/completions,
     auth via env OPENROUTER_API_KEY, modelo default configurável por env
     (ex.: um modelo :free do catálogo).
   - GeminiProvider: API generateContent do Gemini (env GEMINI_API_KEY),
     modelo default gemini-1.5-flash ou mais novo.
2. Resiliência (biblioteca tenacity + implementação própria onde fizer sentido):
   - Timeout total por chamada configurável (default 30s) via httpx.
   - Retry com backoff exponencial + jitter APENAS para erros transitórios
     (timeout, 429, 5xx) — nunca para 4xx de validação. Máx. 2 retries.
   - Circuit breaker simples por provider (abre após N falhas consecutivas,
     half-open após cooldown) — pode usar a lib `purgatory` ou implementar.
   - Fallback: se OpenRouter falhar/circuito aberto, tenta Gemini; se ambos falharem,
     retorna 503 com envelope de erro padronizado e a interação fica status=failed
     no banco (o prompt nunca se perde).
3. Reuse um único httpx.AsyncClient (connection pool) criado no lifespan da app.
4. Registre em cada resultado: provider usado, modelo, latência e tokens.
5. Testes com respx (mock do httpx): sucesso OpenRouter, 429→retry→sucesso,
   OpenRouter caindo→fallback Gemini, ambos caindo→503, timeout.

CRITÉRIOS DE ACEITE: pytest verde sem tocar rede real; com as duas API keys no .env,
um curl em /v1/chat retorna resposta real do LLM; derrubando a key do OpenRouter,
o fallback para Gemini funciona.
```

## Agente 4 — Segurança

```
TAREFA: Endurecer a segurança do micro-serviço.

1. Autenticação por API key: header X-API-Key validado contra hash (não plaintext)
   configurado por env var; aplicar em todos os endpoints exceto /health, /ready e /docs
   (docs podem ser desabilitados por env em produção).
2. Rate limiting por API key + IP (slowapi ou implementação com Redis se já existir;
   caso contrário, in-memory com limites configuráveis, ex.: 10 req/min) retornando 429
   com Retry-After.
3. Sanitização e limites de entrada: tamanho máximo do body, rejeição de content-type
   inesperado, validação estrita dos schemas (extra="forbid").
4. Proteções de resposta: headers de segurança (via middleware), CORS restritivo
   configurável, nunca vazar stack trace ou detalhes internos nos erros (conferir os
   exception handlers), logs sem dados sensíveis (mascarar API keys, truncar prompts
   nos logs).
5. Segredos: garantir .env no .gitignore, .env.example completo, README com aviso;
   validar na inicialização que as keys obrigatórias existem (fail fast com mensagem clara).
6. Mitigações de prompt injection no uso do LLM: system prompt fixo definido no servidor,
   prompt do usuário sempre enviado como mensagem de usuário (nunca concatenado a
   instruções), limite de tamanho, e documentar as limitações em docs/security.md.
7. Adicione checagens automatizadas: bandit (SAST) e pip-audit (vulnerabilidades de
   dependências) como comandos no CI/Makefile.
8. Testes: request sem API key→401, key errada→401, rate limit→429, body gigante→413/422.

CRITÉRIOS DE ACEITE: pytest verde; bandit e pip-audit sem findings de severidade alta;
docs/security.md descrevendo o modelo de ameaças resumido e as mitigações.
```

## Agente 5 — Observabilidade da aplicação

```
TAREFA: Instrumentar logging, métricas e tracing no serviço.

1. Logging estruturado em JSON (structlog): request_id, user_id, rota, status_code,
   latency_ms, provider/modelo usado; níveis configuráveis por env; log de acesso via
   middleware (sem body do prompt em produção — apenas tamanho e hash).
2. Métricas Prometheus em GET /metrics (prometheus-fastapi-instrumentator ou
   prometheus-client): histograma de latência HTTP por rota/status, contador de
   requests, e métricas de negócio: llm_requests_total{provider,model,outcome},
   llm_latency_seconds{provider}, llm_fallback_total, circuit_breaker_state.
3. Tracing com OpenTelemetry: instrumentação automática FastAPI + httpx + SQLAlchemy,
   exporter OTLP configurável por env (default: console/desligado). Spans nomeados para
   a chamada LLM e para a persistência.
4. docker-compose com perfil opcional `observability`: Prometheus + Grafana provisionados
   (datasource + um dashboard JSON básico com latência, taxa de erro, uso por provider)
   e, se simples, Jaeger para traces.
5. Documente em docs/observability.md: quais sinais existem, onde ver, e exemplos de
   queries (ex.: p95 de latência, taxa de fallback).

CRITÉRIOS DE ACEITE: /metrics expõe as métricas custom; logs saem em JSON com
request_id correlacionado; `docker compose --profile observability up` sobe
Prometheus+Grafana com o dashboard carregado.
```

## Agente 6 — Qualidade, testes e CI

```
TAREFA: Garantir a régua de qualidade do repositório.

1. Configure ruff (lint+format), mypy (strict onde viável) e cobertura de testes
   (pytest-cov) com meta mínima de 80% nas camadas de serviço/repositório.
2. Complete a suíte de testes: unitários (services, providers com mock), integração
   (API+banco), e um teste e2e do fluxo feliz com LLM mockado. Organize fixtures em
   conftest.py.
3. Crie um Makefile (ou justfile) com alvos: install, run, test, lint, format,
   typecheck, security (bandit+pip-audit), up, down.
4. GitHub Actions (.github/workflows/ci.yml): jobs de lint, typecheck, security e
   test (com serviço postgres), rodando em push/PR para main. Cache de dependências.
5. Teste de carga básico documentado: script k6 (ou locust) simples em tests/load/
   com instruções de execução e o que observar.
6. Revise o código existente: remova código morto, TODOs resolvidos, docstrings nos
   módulos públicos.

CRITÉRIOS DE ACEITE: `make lint typecheck test` verde local; workflow do Actions
válido (actionlint ou revisão manual); cobertura reportada >= 80% nas camadas core.
```

## Agente 7 — Docker, DX e documentação final do repositório

```
TAREFA: Empacotar e documentar o projeto para entrega.

1. Dockerfile multi-stage (builder + runtime slim, non-root user, healthcheck),
   imagem final enxuta. docker-compose final: app + postgres (+ perfil observability),
   com envs documentadas.
2. README.md completo em PT-BR (o repositório será avaliado por ele):
   - Visão geral do problema e da solução (1 parágrafo + diagrama simples da app)
   - Stack e decisões técnicas (tabela: escolha → justificativa)
   - Como rodar localmente: pré-requisitos, passo a passo com docker compose E sem
     Docker, como obter as API keys gratuitas (OpenRouter/Gemini), exemplo de curl
     do /v1/chat com resposta real
   - Como rodar testes, lint e o resto do Makefile
   - Estrutura de pastas comentada
   - Seções de segurança, resiliência e observabilidade (resumo + link para docs/)
   - Link para docs/architecture.md (Parte 2)
3. Confira que TUDO que o README promete funciona de ponta a ponta num ambiente limpo
   (simule: clone → .env → docker compose up → curl).
4. Prepare o repositório para publicação: git init se necessário, commits organizados
   por tema, LICENSE (MIT), e verifique que nenhum segredo está no histórico.

CRITÉRIOS DE ACEITE: uma pessoa que nunca viu o projeto consegue rodá-lo só com o
README; `docker compose up` + curl funciona; repositório pronto para `git push` público.
```

---

# PARTE 2 — Desenho de Arquitetura (AWS)

## Agente 8 — Arquitetura cloud native + escalonamento (Requisito 1)

```
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
```

## Agente 9 — Observabilidade na nuvem (Requisito 2)

```
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
```

## Agente 10 — Justificativa da escolha do banco de dados (Requisito 3)

```
TAREFA: Escrever a seção "Banco de Dados" de docs/architecture.md justificando a escolha.

1. Caracterize a carga: gravações append-only de interações (prompt/response),
   leituras por user_id ordenadas por data, futuras análises (agregações, análise de
   prompts, possivelmente busca semântica). Volume estimado e crescimento.
2. Compare honestamente pelo menos: PostgreSQL/Aurora (escolhido), DynamoDB e MongoDB/
   DocumentDB — critérios: modelo de dados, consultas analíticas, consistência,
   escalabilidade, custo em baixo volume, operação (serverless?), lock-in, maturidade
   do ecossistema Python. Tabela comparativa + parágrafo de veredito.
3. Justifique PostgreSQL/Aurora Serverless v2: flexível para análises futuras (SQL,
   janelas, JSONB para metadata), pgvector como caminho natural para busca semântica
   dos prompts, Aurora Serverless acompanha oscilação de carga, custo inicial baixo.
   Reconheça quando DynamoDB venceria (escala massiva de key-value, acesso 100%
   previsível) para mostrar maturidade da análise.
4. Evolução: read replicas para análises, particionamento por data se o volume crescer,
   export para S3/Athena para analytics pesado sem impactar o OLTP.

CRITÉRIOS DE ACEITE: a justificativa conecta requisitos → critérios → decisão;
admite trade-offs; consistente com o banco usado na Parte 1.
```

## Agente 11 — Resiliência a falhas de dependências (Requisito 4)

```
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
```

---

# Agente 12 — Revisor final (executar por último)

```
TAREFA: Revisão final de entrega do desafio. NÃO adicione features novas.

1. Releia o enunciado do desafio (Parte 1: micro-serviço com qualidade, segurança,
   resiliência, performance; Parte 2: escalonamento, observabilidade, justificativa
   do banco, estratégia de falha de dependências; pontos importantes: repo público,
   documentação + instruções locais + desenho de arquitetura no repositório).
2. Faça um checklist requisito → onde está atendido (arquivo/seção) e aponte lacunas.
3. Ambiente limpo: siga o README literalmente (clone simulado, .env a partir do
   .env.example com keys reais, docker compose up, curl do exemplo). Corrija qualquer
   passo que falhe ou esteja impreciso.
4. Rode make lint typecheck test security e corrija o que quebrar.
5. Revise consistência entre código e docs (nomes de env vars, portas, endpoints,
   métricas citadas em architecture.md existem de fato).
6. Confirme: nenhum segredo no repo/histórico, LICENSE presente, diagrama renderizando,
   commits limpos. Deixe pronto para publicar no GitHub público.

ENTREGÁVEL: relatório final com o checklist preenchido + lista de correções feitas.
```

---

## Ordem de execução sugerida

| Fase | Agentes | Dependência |
|------|---------|-------------|
| 1 | Agente 1 (scaffold) | — |
| 2 | Agentes 2 e 3 em paralelo | Agente 1 |
| 3 | Agentes 4 e 5 em paralelo | Agentes 2 e 3 |
| 4 | Agente 6 | Agentes 4 e 5 |
| 5 | Agentes 8, 9, 10 e 11 em paralelo (só escrevem docs) | Conceitos da Parte 1 fechados |
| 6 | Agente 7 (empacotamento/README) | Tudo da Parte 1 |
| 7 | Agente 12 (revisor final) | Todos |
