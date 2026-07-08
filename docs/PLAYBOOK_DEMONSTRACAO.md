# Playbook de Demonstração — Micro-serviço LLM (Desafio Itaú)

Roteiro para demonstrar, ao vivo, que cada requisito do desafio está implementado
e validado. Tempo estimado: 10–15 minutos. Pré-requisito mínimo: Python 3.11+;
com Docker a demo fica completa (banco real).

> **Evidências visuais**: cada passo tem um slot de imagem em `docs/images/`.
> Durante a sua execução de validação, capture a tela no momento indicado
> (Win+Shift+S) e salve com o nome sugerido — o markdown já referencia os
> arquivos. Os blocos "Saída esperada" mostram o que a captura deve conter.

---

## 0. Obtenção das chaves (uma vez, antes da demo)

O serviço usa **três credenciais distintas** — não as confunda:

| Credencial | O que é | Onde obter | Onde vai |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Key do provider primário de LLM | openrouter.ai (passo 0.1) | Só no `.env` |
| `GEMINI_API_KEY` | Key do provider de fallback | aistudio.google.com (passo 0.2) | Só no `.env` |
| API key do serviço | Senha que **você inventa** para os clientes da SUA API | Você cria (passo 0.3) | Texto puro no header `X-API-Key`; **hash** no `.env` (`API_KEY_HASH`) |

### 0.1 OpenRouter (primário)
1. Acesse https://openrouter.ai e clique em **Sign in** (aceita conta Google/GitHub).
2. Menu do avatar (canto superior direito) → **Keys** — ou direto: https://openrouter.ai/settings/keys
3. **Create Key** → dê um nome (ex.: `itau-ms-demo`) → **Create** → copie a key
   `sk-or-v1-...` (ela só aparece uma vez).
4. Cole no `.env`: `OPENROUTER_API_KEY=sk-or-v1-...`

![Passo 0.1 — página de Keys do OpenRouter](images/passo-01-openrouter-keys.png)

### 0.2 Gemini (fallback)
1. Acesse https://aistudio.google.com/apikey (login Google).
2. **Create API key** → selecione/crie um projeto → copie a key `AIza...`.
3. Cole no `.env`: `GEMINI_API_KEY=AIza...`
4. (Opcional) Confirme os modelos disponíveis para a sua key:
   ```powershell
   (Invoke-RestMethod -Uri "https://generativelanguage.googleapis.com/v1beta/models" `
     -Headers @{"x-goog-api-key"="SUA_GEMINI_KEY"}).models | Select-Object name
   ```
   e ajuste `GEMINI_MODEL=` no `.env` para um dos nomes listados (sem o prefixo `models/`).

![Passo 0.2 — criação da key no Google AI Studio](images/passo-02-gemini-key.png)

### 0.3 API key do serviço (autenticação da SUA API)
1. Invente uma senha para a demo (ex.: `demo-itau-2026`). **Nunca** reutilize a
   key de um provider aqui.
2. Gere o hash SHA-256 dela:
   ```powershell
   python -c "import hashlib; print(hashlib.sha256('demo-itau-2026'.encode()).hexdigest())"
   ```
3. Cole **o hash** no `.env`: `API_KEY_HASH=<64 caracteres hex, sem aspas>`.
4. Nos requests, envie **a senha em texto** no header: `X-API-Key: demo-itau-2026`.
   O servidor nunca armazena a senha — só o hash (argumento de segurança da demo).

---

## 1. Subir o serviço

```powershell
git clone https://github.com/felipefb/demo_ms_llm.git && cd demo_ms_llm
copy .env.example .env
# Edite o .env com as 3 credenciais do passo 0
docker compose up --build
```

**Saída esperada:** `Container demo_ms_llm-postgres-1 Healthy`, migrations Alembic
aplicadas e `Uvicorn running on http://0.0.0.0:8000`. Sem o aviso
"no LLM API keys configured" (se aparecer, as keys não chegaram ao container).

![Passo 1 — compose up com postgres healthy e migration](images/passo-10-compose-up.png)

*Sem Docker:* `python -m venv .venv; .venv\Scripts\pip install -r requirements.txt;`
depois `$env:REPOSITORY_BACKEND="memory"; .venv\Scripts\python -m uvicorn app.main:app`.

> Dica para o terminal Windows: rode uma vez
> `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`
> (o serviço já envia `charset=utf-8`; isso garante a exibição correta em
> consoles antigos).

## 2. Fluxo principal: prompt → LLM → persistência

```powershell
Invoke-RestMethod http://localhost:8000/v1/chat -Method Post `
  -ContentType "application/json" `
  -Headers @{"X-API-Key"="demo-itau-2026"} `
  -Body '{"user_id":"12345","prompt":"Como esta a cotacao do dolar hoje?"}'
```

**Saída esperada:** campos do enunciado (`id`, `user_id`, `prompt`, `response`,
`model`, `timestamp`) + os que adicionamos deliberadamente (`status=completed`,
`provider`, `latency_ms`, `usage`) — mudança de payload permitida pelo ponto 4
do enunciado. Por padrão a resposta vem **direta e objetiva** (`response_mode`
= `direct`: modelo otimizado para custo + teto de tokens).

Para uma resposta **com contexto adicional**, inclua `"response_mode":"detailed"`
no body — o serviço automaticamente libera um teto maior de tokens e usa o
modelo principal (roteamento custo × qualidade).

Com `LLM_WEB_SEARCH=true` no `.env`, a resposta traz **o dado atual do dia**
(grounding com busca web), como no exemplo do enunciado.

![Passo 2 — resposta 200 com provider e cotação do dia](images/passo-20-chat-ok.png)

**Persistência (análises futuras):**
```powershell
Invoke-RestMethod http://localhost:8000/v1/conversations/12345 -Headers @{"X-API-Key"="demo-itau-2026"}
docker compose exec postgres psql -U postgres -d itau_ms -c "select user_id, status, provider, latency_ms from interactions;"
```

![Passo 2b — linha persistida no PostgreSQL](images/passo-21-postgres.png)

## 3. Resiliência (OpenRouter → Gemini)

Com o free tier do OpenRouter estourado (429) — situação fácil de reproduzir —
o log conta a história completa em um único `request_id`: 3 tentativas com
backoff → falha do primário → fallback → resposta do Gemini
(`provider: "gemini"` no retorno). Se ambos caírem: **503 honesto e o prompt
fica salvo com `status=failed`** (nada se perde).

```powershell
docker compose logs app --tail 20
.venv\Scripts\python -m pytest tests/test_llm_client.py -q   # 429→retry, fallback, breaker, timeout
```

![Passo 3 — log do retry + fallback correlacionado por request_id](images/passo-30-fallback-log.png)

## 4. Segurança

```powershell
# Sem key -> 401 com envelope e request_id
Invoke-RestMethod http://localhost:8000/v1/chat -Method Post -ContentType "application/json" -Body '{"user_id":"x","prompt":"oi"}'
# 11º request no mesmo minuto -> 429 com Retry-After
1..11 | ForEach-Object { try { Invoke-RestMethod http://localhost:8000/v1/chat -Method Post -ContentType "application/json" -Headers @{"X-API-Key"="demo-itau-2026"} -Body '{"user_id":"x","prompt":"oi"}' | Out-Null; "200" } catch { $_.Exception.Response.StatusCode.value__ } }
```

Mostre `docs/security.md` e rode `bandit`/`pip-audit` (alvo `security` do Makefile).

![Passo 4 — 401 e sequência terminando em 429](images/passo-40-seguranca.png)

## 5. Observabilidade

```powershell
curl.exe -s http://localhost:8000/metrics | findstr /R "llm_requests_total llm_fallback_total circuit_breaker rate_limit"
docker compose --profile observability up -d   # Prometheus :9090, Grafana :3000 (admin/admin), Jaeger :16686
```

Logs JSON com `request_id` correlacionado aparecem no console do `docker compose logs app`.

![Passo 5 — dashboard Grafana provisionado](images/passo-50-grafana.png)

## 6. Qualidade

```powershell
.venv\Scripts\python -m pytest --cov=app -q   # 81 passed, cobertura ~95% (gate 80%)
.venv\Scripts\python -m ruff check .; .venv\Scripts\python -m mypy app
```

CI no GitHub Actions roda lint/typecheck/security/test em todo push/PR.
Teste de carga: `tests/load/chat_load.js` (k6).

![Passo 6 — suíte e cobertura verdes](images/passo-60-testes.png)

## 7. Parte 2 — Arquitetura (docs/architecture.md)

Abra o índice e mostre uma seção por requisito: **01** escalonamento (Mermaid +
PNG `docs/diagrams/aws_architecture.png`, cenário 10→100 rps), **02**
observabilidade (SLOs, alarmes), **03** banco (comparativo com veredito),
**04** resiliência (tabela cenário→experiência, DR).

![Passo 7 — diagrama AWS gerado como código](../docs/diagrams/aws_architecture.png)

## 8. Diferencial: como o projeto foi construído

`agents/README.md` + `shared/memory/fase*_board.md`: 12 agentes autônomos em 7
fases com validação cruzada registrada em pareceres; relatório final de
prontidão em `shared/memory/fase7_relatorio_final.md`.

---

## Checklist rápido enunciado → evidência

| Requisito do enunciado | Onde está | Passo |
|---|---|---|
| POST /v1/chat com userId/prompt | `app/api/v1/chat.py` (snake_case; mudança permitida pelo ponto 4) | 2 |
| Retorno id/userId/prompt/response/model/timestamp | `app/schemas/chat.py` | 2 |
| Persistir em banco para análises | `app/models/interaction.py` + Alembic | 2 |
| LLM em tempo real (OpenRouter/Gemini) | `app/services/providers.py` | 2 |
| Resposta direta/objetiva (como o exemplo) com dado do dia | `response_mode=direct` + `LLM_WEB_SEARCH` | 2 |
| Resiliência | `app/services/resilience.py` | 3 |
| Segurança | `app/core/auth.py` + `docs/security.md` | 4 |
| Observabilidade | `app/core/metrics.py` + Grafana | 5 |
| Qualidade/performance | 81 testes, cov 95%, CI, k6 | 6 |
| Arquitetura (4 requisitos) | `docs/architecture/01..04` | 7 |
| Docs + instruções + diagrama no repo | `README.md`, `docs/` | 1 e 7 |
| Repositório público no GitHub pessoal | Settings → General → Visibility | — |
