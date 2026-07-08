# Parecer — Anel 9 (Observabilidade) sobre Seção 03 (Banco de Dados)

Revisor: Agente 9 (autor da seção 02_observabilidade.md)
Alvo: `docs/architecture/03_banco_de_dados.md`
Data: 2026-07-07

## Veredito: APROVADO

## Verificações realizadas

### 1. Consistência com as seções 1 e 2
- Seção 1 (`01_arquitetura_escalonamento.md`) assume **Aurora PostgreSQL
  Serverless v2 (0.5–16 ACUs) + RDS Proxy** — exatamente o banco justificado
  na seção 3 (incl. o argumento "wire-compatible / migrations Alembic sobem
  sem mudança", coerente com a linha 15 da seção 1). OK.
- Seção 2 (minha): os alarmes A6 usam `DatabaseConnections`/`max_connections`
  do RDS e o exemplo de troubleshooting (2.7) cita pool esgotado + RDS Proxy
  como mitigação — compatível com Aurora Sv2 (família RDS, mesmas métricas
  CloudWatch) e com a evolução "read replicas" da seção 3.4. OK.
- A seção 3 não propõe métricas/monitoramento próprios de banco, portanto não
  há conflito com os alarmes da seção 2. OK.

### 2. Fatos do código citados
Conferido contra `app/models/interaction.py` e
`migrations/versions/20260706_0001_create_interactions.py`:
- PK `uuid` (UUID as_uuid): confere.
- `user_id` indexado (`ix_interactions_user_id`) String(128): confere.
- Enum nativo `interaction_status` (pending/completed/failed): confere.
- `provider` String(64), `prompt_tokens`/`completion_tokens` Integer: confere.
- `latency_ms` INTEGER: confere (docstring do modelo confirma a decisão).
- Coluna `metadata` JSONB (atributo `extra_metadata`): confere.
- Índice composto `ix_interactions_user_id_created_at` em
  `(user_id, created_at DESC)`: confere no modelo e na migration.
- Fluxo "1 INSERT (pending) + 1 UPDATE (completed/failed)": confere com
  `app/repositories/postgres.py` (`create_pending`, `mark_completed`) e o
  contrato documentado em `app/repositories/conversations.py`.

### 3. Observações não bloqueantes (opcionais)
1. Terminologia: "escrita append-only" seguida de "1 INSERT + 1 UPDATE" é
   levemente contraditória — sugerir "escrita quase append-only (uma única
   atualização de status por linha)". Não afeta a conclusão.
2. A seção 3.1 cita "enum de status (pending → completed | failed)" mas não
   menciona `error_detail`; irrelevante para a justificativa do banco.
3. Sinergia positiva: a proposta de read replicas (3.4) reforça o SLO de
   latência da app da seção 2 (isola OLTP do analítico) — nenhuma ação
   necessária.
