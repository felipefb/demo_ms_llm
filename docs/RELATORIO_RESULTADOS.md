# Relatório de resultados — micro-serviço LLM (Desafio Itaú)

Consolida o que foi entregue, os resultados medidos em demonstração real
(docker compose + keys reais dos providers) e o que foi acrescentado além do
enunciado, sempre com o propósito de qualidade, performance, eficiência,
utilidade e custo controlado.

## 1. Aderência ao enunciado

| Requisito do desafio | Status | Evidência |
|---|---|---|
| POST /v1/chat recebe prompt e devolve resposta | ✅ | `app/api/v1/chat.py`; demo real com cotação do dia |
| Persistência em banco para análises futuras | ✅ | PostgreSQL + Alembic; fluxo pending→completed/failed (prompt nunca se perde) |
| LLM em tempo real via OpenRouter/Gemini (free tier) | ✅ | `app/services/providers.py`; fallback demonstrado ao vivo |
| Qualidade | ✅ | 114 testes, cobertura ≥95%, ruff/mypy/bandit/pip-audit limpos, CI |
| Segurança | ✅ | API key com hash, rate limit, headers, anti prompt-injection, `docs/security.md` |
| Resiliência | ✅ | retry seletivo, circuit breaker, fallback, auto-cura; 503 honesto com prompt salvo |
| Performance | ✅ | cache TTL, fail-fast em 429, memória de truncagem, grounding nativo (seção 5.4 da arquitetura) |
| Parte 2 (escalonamento, observabilidade, banco, falhas) | ✅ | `docs/architecture/01..05` + diagrama como código (PNG) |
| Documentação + instruções locais + desenho no repo | ✅ | README, docs/, playbook de demonstração |

## 2. Resultados medidos (evolução durante a validação real)

| Momento | Cenário | Latência | Tokens |
|---|---|---|---|
| Baseline sem busca web | resposta sem dado real ("não tenho acesso...") | ~6,6 s | ~500 |
| Grounding inicial (OpenRouter primário) | dado real, mas plugin web + reasoning | 45–84 s | 3,5–4,2k |
| Após otimizações (Gemini nativo primário) | dado real do dia | **~8 s** | **~2,2k** |
| Cache hit (repetição ≤60s) | mesma resposta | **0,07 ms** | **0** |

Bugs reais encontrados e corrigidos durante a validação (cada um com teste de
regressão): modelo de imagem escolhido para texto (400), modelo minúsculo
alucinando valores, `content: null` de modelos reasoning (500), truncagem do
teto vazando raciocínio como resposta, encoding Latin-1 em clientes legados,
datas inferidas de artigos antigos.

## 3. Além do proposto (e por quê)

| Acréscimo | Propósito |
|---|---|
| Estrutura multi-agente (12 agentes, 7 fases, validação cruzada registrada em `shared/memory/`) | Qualidade do processo: cada fase só fechou com aprovação unânime e suíte verde |
| Seleção automática e contínua de modelos por catálogo | Eficiência/custo: sempre o melhor modelo free para a necessidade, sem manutenção manual |
| Auto-cura por denylist + teto adaptativo + memória de truncagem | Utilidade: o serviço se corrige sozinho diante de modelos problemáticos |
| `response_mode` direct/detailed | Utilidade: o consumidor controla profundidade e custo por request |
| Saída estruturada normalizada (`structured.dados` → tabela) | Utilidade: normalização downstream sem parsing frágil |
| Busca web opcional com honestidade temporal (data injetada, intradiário primeiro, recuo rápido) | Qualidade do dado: responde como o exemplo do enunciado, com fonte e data de referência |
| Cache de respostas com TTL + métrica de hits | Performance com custo controlado: repetições em ms e 0 tokens |
| Fail-fast em 429 + Gemini primário no grounding | Performance: caminho comum no alvo de ~8s |
| Observabilidade das decisões (llm_selected_model, cache_hits, fallback, breaker) | Operabilidade: toda decisão automática é auditável no /metrics e nos logs |
| Playbook de demonstração com evidências | Utilidade para avaliação: roteiro requisito→prova em 15 min |

## 4. Limitações conhecidas (documentadas)

- Cache, rate limit e denylist são in-memory (single-réplica); o equivalente
  distribuído (Redis/ElastiCache) está desenhado em `docs/architecture/01`.
- Piso de ~8s para dado real do dia no free tier (busca + síntese) — mitigado
  pelo cache e pelo modo sem busca (~5s) para perguntas atemporais.
- Free tier dos providers impõe rate limits; a cadeia com fallback + breaker
  absorve, e a resposta 503 nunca perde o prompt.
