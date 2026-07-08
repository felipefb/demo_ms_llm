# Estrutura de Agentes — Desafio Itaú (micro-serviço LLM)

Estrutura de cérebro/autonomia replicada de `agent_team_app`: cada agente vive em
`agents/<id>/` com um contrato declarativo (`agent.yml`) e seu prompt de sistema
(`system.md`). Todos compartilham o Contexto Base em `shared/context/base_context.md`.
A orquestração do time está em `teams/team-itau-challenge.yml` e os defaults globais
em `conf/defaults.yml`. Fonte dos prompts: `AGENT_PROMPTS.md` (raiz do repo).

## Os 12 agentes

| # | Agente | Fase | Depende de |
|---|--------|------|------------|
| 1 | `api_scaffold` — Scaffold do serviço e API REST | 1 | — |
| 2 | `persistence` — Persistência PostgreSQL | 2 | 1 |
| 3 | `llm_integration` — OpenRouter + fallback Gemini | 2 | 1 |
| 4 | `security_hardening` — Segurança | 3 | 2, 3 |
| 5 | `app_observability` — Observabilidade da aplicação | 3 | 2, 3 |
| 6 | `quality_ci` — Qualidade, testes e CI | 4 | 4, 5 |
| 8 | `cloud_architecture` — Arquitetura AWS + escalonamento | 5 | 6 |
| 9 | `cloud_observability` — Observabilidade na nuvem | 5 | 6 |
| 10 | `database_rationale` — Justificativa do banco | 5 | 6 |
| 11 | `resilience_design` — Resiliência a falhas | 5 | 6 |
| 7 | `packaging_docs` — Docker, DX e README final | 6 | 6, 8–11 |
| 12 | `final_reviewer` — Revisor final | 7 | 7 |

Agentes da mesma fase podem rodar em paralelo. Uma fase só abre quando os
critérios de aceite da anterior estão verdes (gate definido em `conf/defaults.yml`).

## Como executar um agente

Cada agente é autocontido. Em uma sessão de um agente de IA de codificação (ou subagente), cole:

1. o conteúdo de `shared/context/base_context.md`;
2. o conteúdo de `agents/<id>/system.md`.

Ou, na raiz do repo: "Execute o agente `<id>` conforme `agents/<id>/system.md`,
aplicando o Contexto Base."
