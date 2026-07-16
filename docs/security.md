# Segurança — modelo de ameaças e mitigações

Escopo: micro-serviço `itau-ms` (FastAPI) que recebe prompts via `POST /v1/chat`,
invoca OpenRouter/Gemini e persiste conversas em PostgreSQL.

## Modelo de ameaças (resumo)

| Ameaça | Vetor | Mitigação |
|---|---|---|
| Acesso não autorizado à API | Chamadas diretas sem credencial | API key obrigatória (`X-API-Key`) validada contra hash SHA-256 (`API_KEY_HASH`), comparação em tempo constante |
| Abuso / negação de serviço | Flood de requisições | Rate limit por (API key, IP) — janela deslizante, 429 + `Retry-After` |
| Payloads maliciosos/gigantes | Bodies enormes, content-type inesperado, campos extras | Limite global de body (413), `Content-Type: application/json` obrigatório (415), schemas `extra="forbid"`, prompt ≤ 4000 chars, metadata limitada (20 chaves / 4 KiB) |
| Vazamento de informação | Stack traces, detalhes internos em erros | Envelope de erro padronizado (código + mensagem genérica + request_id); corpos de erro upstream nunca repassados |
| Vazamento de segredos | Keys em logs/URLs/repo | Keys só em headers HTTP; logs mascaram a key (`abcd***`); `.env` no `.gitignore`; fail-fast na inicialização exige `OPENROUTER_API_KEY`, `GEMINI_API_KEY` e `API_KEY_HASH` fora de dev/test |
| Prompt injection | Instruções maliciosas no prompt do usuário | System prompt fixo definido no servidor (`LLM_SYSTEM_PROMPT`); prompt do usuário sempre como mensagem `user` separada (nunca concatenado a instruções); limite de tamanho |
| SQL injection | Input do usuário em queries | Exclusivamente ORM SQLAlchemy com bound params; zero interpolação de SQL |
| Model abuse | Cliente forçando modelos caros/não homologados | Allowlist `ALLOWED_MODELS`; sem allowlist, cliente não pode sobrescrever o modelo |
| Uso fora do escopo | Prompts sobre temas divergentes (política, religião) | Guardrail de escopo temático: pré-filtro determinístico antes do LLM + reforço no system prompt; tentativa auditável (`status=blocked`), aviso em log WARNING + métrica |
| Ataques via browser | Clickjacking, sniffing, CORS aberto | Headers de segurança em toda resposta (nosniff, DENY, CSP `default-src 'none'`, no-referrer, no-store); CORS fechado por padrão (`CORS_ALLOWED_ORIGINS=[]`) |

## Autenticação

- Header `X-API-Key` em todos os endpoints, exceto públicos:
  `/health`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/metrics`
  (o `/metrics` é para o scraper Prometheus na rede interna — não exponha publicamente).
- O servidor guarda apenas o hash: `API_KEY_HASH` = SHA-256 hex da key.
  Geração: `python -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <key>`
- Comparação com `hmac.compare_digest` (resistente a timing attack).
- `API_KEY_HASH` vazio desabilita auth **somente** em dev/test (warning no log);
  em qualquer outro `APP_ENV` a aplicação recusa iniciar (fail-fast).
- `/docs`, `/redoc` e `/openapi.json` são desabilitados automaticamente com `APP_ENV=prod`.

## Rate limiting

- Janela deslizante em memória por hash de (API key, IP): `RATE_LIMIT_REQUESTS`
  (padrão 10) por `RATE_LIMIT_WINDOW_SECONDS` (padrão 60s); excedente recebe
  `429` com header `Retry-After`.
- Limitação conhecida: estado por processo. Com múltiplas réplicas, o limite
  efetivo é N× o configurado — na arquitetura alvo o throttling fica no API
  Gateway/Redis (ver `docs/architecture.md`).

## Limites de entrada

- Body > `MAX_BODY_BYTES` (padrão 64 KiB) → `413 payload_too_large`
  (checa `Content-Length` e também bodies chunked durante o streaming).
- `POST/PUT/PATCH` com content-type ≠ `application/json` → `415`.
- Schemas Pydantic com `extra="forbid"`; prompt 1–4000 chars não-branco;
  `user_id` ≤ 128; metadata ≤ 20 chaves / 4096 bytes serializados.

## Mitigações de prompt injection e limitações

- System prompt **fixo no servidor** (`LLM_SYSTEM_PROMPT`), enviado como
  mensagem `system` (OpenRouter) / `systemInstruction` (Gemini).
- O prompt do usuário vai **sempre** como mensagem de papel `user`, nunca
  concatenado a instruções do sistema.
- Limitações conhecidas (documentadas, não elimináveis por construção):
  - LLMs podem ainda assim seguir instruções injetadas; o system prompt reduz,
    não elimina, o risco.
  - A resposta do modelo é retornada ao cliente sem filtro de conteúdo;
    um output-filter/moderation seria etapa futura.
  - Não há detecção semântica de injection (ex.: classificador dedicado).

## Guardrail de escopo temático (política, religião, etc.)

O serviço responde a temas do escopo (ex.: indicadores econômico-financeiros,
como a cotação do dólar). Temas divergentes são bloqueados em **duas camadas**:

1. **Pré-filtro determinístico** (`app/services/guardrail.py`): categorias de
   temas bloqueados — `politica` e `religiao` por padrão, customizáveis via
   `GUARDRAIL_BLOCKED_TOPICS` (JSON categoria → regexes) — detectadas por regex
   sobre o texto normalizado (minúsculas, sem acentos), **antes** do cache e do
   LLM: zero tokens gastos. Expressões do domínio econômico que contêm termos
   bloqueados ("política monetária/fiscal/cambial/de preços") são exceções e
   passam normalmente.
2. **System prompt** (`LLM_SYSTEM_PROMPT`): instrui o modelo a recusar temas
   fora do escopo que o pré-filtro não capturar.

**Comportamento quando bloqueado:**

- O cliente recebe `200` com `status=blocked`, `provider=guardrail` e a
  mensagem controlada (`GUARDRAIL_MESSAGE`); `structured.contexto` avisa que a
  tentativa foi detectada e registrada.
- **Aviso operacional**: log `WARNING` estruturado
  (`guardrail: prompt bloqueado category=... user_id=... interaction_id=...`)
  e métrica `guardrail_blocked_total{category}` em `/metrics` — alarmável por
  categoria/volume.
- **Auditoria**: a tentativa é persistida (`status=blocked`,
  `error_detail="guardrail: tema '...' fora do escopo"`) e aparece no
  histórico `GET /v1/conversations/{user_id}` — o controle do que foi
  questionado fica consultável para análise.

**Limitações conhecidas**: filtro por palavras-chave tem cobertura parcial por
construção (falsos negativos possíveis — mitigados pela camada do system
prompt — e falsos positivos raros, mitigados pela lista de exceções). Evolução
natural: classificador semântico dedicado (ex.: modelo leve de moderação) como
terceira camada. `GUARDRAIL_ENABLED=false` desliga o pré-filtro.

## IDOR conhecido — `GET /v1/conversations/{user_id}`

- **Risco:** qualquer portador da API key pode ler o histórico de qualquer
  `user_id` (não há sessão por usuário final; a key autentica a *aplicação
  cliente*, não o usuário).
- **Modelo atual (aceito e documentado):** a API é service-to-service com uma
  única key confiável (backend-for-frontend); o chamador é responsável por
  autorizar seus usuários finais.
- **Mitigação futura:** JWT por usuário final (sub == user_id) ou múltiplas
  keys com escopo de usuário; alternativa mínima: keys distintas por cliente e
  auditoria de acesso (logs já registram rota + request_id, sem prompt).

## Segredos

- `.env` e `.env.*` no `.gitignore` (só `.env.example` versionado, apenas
  placeholders).
- Keys de LLM apenas em headers (`Authorization: Bearer`, `x-goog-api-key`) —
  nunca em query string, logs ou mensagens de exceção.
- Fail-fast na inicialização fora de dev/test: exige `OPENROUTER_API_KEY`,
  `GEMINI_API_KEY` e `API_KEY_HASH` (e valida o formato do hash).

## Checagens automatizadas (SAST + dependências)

Executar localmente / no CI (Makefile do Agente 6):

```bash
# SAST — falha se houver qualquer finding
python -m bandit -r app

# Vulnerabilidades de dependências instaladas
python -m pip_audit
```

Estado em 2026-07-06: bandit 0 findings (todas as severidades);
pip-audit sem vulnerabilidades conhecidas (após upgrade de pip/setuptools do venv).

## Headers de segurança nas respostas

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Cache-Control: no-store`,
`Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`
e `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`
(CSP relaxada apenas nas rotas de docs, quando habilitadas).

## Decisões da Fase 4 (quality gate)

- **POST sem Content-Type → 422 (mantido).** Um `Content-Type` explicitamente
  não-JSON em métodos de escrita retorna **415** (BodyLimitMiddleware). Quando o
  header está **ausente/vazio**, o corpo é tratado como tentativa de JSON e a
  validação do FastAPI/Pydantic responde **422** se for inválido. Racional:
  ausência de header não é uma declaração de mídia errada — rejeitar com 415
  quebraria clientes simples (curl com `-d` já envia form-urlencoded, que cai
  no 415; sem header nenhum, o corpo JSON válido funciona). Coberto por
  `tests/test_quality_gaps.py::test_post_without_content_type_returns_422`.
- **Contador dedicado de rate-limit implementado**:
  `rate_limit_rejections_total{path}` em `app/core/metrics.py`, incrementado
  pelo `RateLimitMiddleware` a cada 429.
