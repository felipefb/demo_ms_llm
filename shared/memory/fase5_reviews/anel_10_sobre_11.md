# Parecer — Anel 10 (Banco de Dados) sobre Anel 11 (Resiliência)

Revisor: Agente 10 (autor de `docs/architecture/03_banco_de_dados.md`)
Alvo: `docs/architecture/04_resiliencia.md`
Data: 2026-07-07

## Veredito: APROVADO

## 1. Consistência com a seção 03 (Banco de Dados)

- **Degradação SQS+DLQ / consistência eventual (4.2.2)**: coerente com a 03. A
  03 caracteriza a carga como append-only cujo propósito é "análises futuras",
  não a resposta síncrona — exatamente o argumento que a 04 usa para
  recomendar a opção (b) (enfileirar persistência, aceitar atraso no
  histórico). Nenhuma contradição; as duas seções contam a mesma história.
- **Aurora**: a 04 referencia failover multi-AZ (~30 s) absorvido por
  `pool_pre_ping`, backups contínuos + PITR, Serverless — todos recursos que a
  03 (itens 3.3.3 e 3.3.5) cita como motivos da escolha. Consistente.
- **RTO/RPO (4.5)**: RTO ≤ 1 h e RPO ≤ 5 min são atingíveis com os recursos do
  Aurora citados na 03 para falha *dentro da região* (PITR contínuo dá RPO de
  segundos; failover multi-AZ dá RTO de minutos). Observação não bloqueante
  abaixo (item 3) sobre o cenário de perda de região.

## 2. Verificação contra o código real

| Alegação da 04 | Código | Confere? |
|---|---|---|
| Timeout configurável `llm_timeout_seconds` | `app/core/config.py:66` (default 30.0), usado nos providers | Sim |
| `_classify_http_error` separa transiente/permanente | `app/services/providers.py:42-43` | Sim |
| Retry tenacity `wait_exponential_jitter`, só transientes, `llm_max_retries=2` | `resilience.py:117-124`, `config.py:68` | Sim |
| `CircuitBreaker` threshold 5 / cooldown 30 s, half-open com sonda | `resilience.py:55-82`, `config.py:73-74` | Sim |
| Breaker exporta estado via `set_breaker_state` | `resilience.py` + `metrics.py:76` | Sim |
| Erro permanente não dispara breaker, mas avança na cadeia | `resilience.py:148-156` | Sim |
| Cadeia OpenRouter→Gemini em `build_llm_client`; modelo do cliente só no primário; `llm_fallback_total` | `resilience.py:143, 186-187, 199-238` | Sim |
| `LLMUnavailableError` → 503 `code=llm_unavailable`; `mark_failed` antes | `resilience.py:48-52`, `chat.py:69-80` | Sim |
| `pool_pre_ping=True`, `pool_recycle`, pool/overflow/timeout por env | `database.py:21-24`, `config.py:54-55` | Sim |
| `/ready` checa banco e egress | `app/api/health.py:53-63` | Sim |
| `create_pending` antes do LLM | `chat.py:57-63` | Sim |
| Rate limit `429 rate_limited` + `rate_limit_rejections_total` | `ratelimit.py:71-85`, `metrics.py:26` | Sim |
| Bulkhead: breakers por provider (`ResilientLLMClient.breakers`) | `resilience.py:105-110` | Sim |

## 3. Tabela de cenários (4.4) — códigos de erro reais

- `503 llm_unavailable` → confere (`chat.py:77`, `resilience.py:52`).
- `422 model_not_allowed` → confere (`chat.py:33-35`).
- `429 rate_limited` → confere (`ratelimit.py:79`).
- "Banco fora (hoje) → 500 envelope padronizado com request_id" → confere:
  exceção não tratada cai no handler genérico (`middleware.py:96`,
  `code=internal_error`); a 04 não alega código específico, então está ok.
- Cenário "banco fora (evolução SQS) → 200" está corretamente marcado como
  evolução, coerente com a recomendação (b) da 4.2.2 e com o texto honesto de
  que a Parte 1 mantém (a).

## Observações não bloqueantes

1. **RPO ≤ 5 min em perda de região**: a 4.5 apoia o DR regional em "snapshots
   automáticos copiados para região secundária". Cópias de snapshot são
   periódicas — em perda total de região o RPO real seria horas, não 5 min.
   Sugestão para iteração futura: ou escopar explicitamente o RPO ≤ 5 min como
   alvo intra-região, ou citar Aurora Global Database como o caminho para RPO
   de segundos cross-region. Não bloqueia: o texto já admite pilot light e
   deixa ativo-ativo como evolução.
2. A tabela 4.4 poderia citar `code=internal_error` na linha "Banco fora
   (hoje)" para fechar 100% com o envelope real. Cosmético.

## Conclusão

A seção 04 é internamente consistente, alinhada com a justificativa do banco
da seção 03 e fiel ao código implementado (arquivos, funções, defaults e
códigos de erro conferidos um a um). **APROVADO.**
