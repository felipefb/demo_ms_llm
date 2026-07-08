# 3. Banco de Dados — Justificativa da Escolha

> Requisito 3 do desafio: justificar a escolha do banco de dados para persistir
> prompts e respostas visando análises futuras. Decisão: **PostgreSQL 16**
> localmente (docker-compose) e **Aurora PostgreSQL Serverless v2** na AWS.

## 3.1 Caracterização da carga

O modelo de dados real do serviço é uma única tabela de fatos, `interactions`
(`app/models/interaction.py`): PK `uuid`, `user_id` indexado, enum de `status`
(`pending → completed | failed`), `provider`, contadores de tokens
(`prompt_tokens`, `completion_tokens`), `latency_ms` (INTEGER), coluna
`metadata` JSONB e índice composto `ix_interactions_user_id_created_at`
(`user_id, created_at DESC`). O acesso é feito via SQLAlchemy 2.0 async, com
schema versionado por Alembic.

Padrões de acesso observados no código:

| Padrão | Descrição | Implicação |
|---|---|---|
| Escrita quase append-only | 1 INSERT (`pending`) + 1 UPDATE (`completed`/`failed`) por requisição; nunca há reescrita posterior | Carga de escrita simples, sem contenção; qualquer banco atende |
| Cache não reduz escrita | O cache TTL de respostas (seção 5.2/5.3) corta chamadas ao LLM, mas **cada request continua persistido individualmente** (`create_pending` roda antes do lookup no cache) | O dimensionamento de escrita segue proporcional ao tráfego HTTP, não ao tráfego de LLM — o banco é a fonte completa para analytics, incluindo hits de cache |
| Leitura por usuário | Listagem paginada por `user_id` ordenada por `created_at DESC` | Coberta pelo índice composto já existente |
| Análises futuras | Agregações (tokens/custo por usuário, latência por provider, taxa de falha), análise de conteúdo dos prompts e, possivelmente, **busca semântica** | Exige consultas ad hoc, janelas, GROUP BY flexível e caminho para vetores |

O que se grava em `response` também mudou: com a saída estruturada
(`app/services/formatting.py`), a coluna guarda a **frase direta normalizada**
(`resposta`), não mais o texto cru do modelo — texto curto e previsível, bom
para análise de conteúdo. Os campos estruturados (`dados` com uma linha por
indicador, `contexto`, `fontes`) hoje são devolvidos ao cliente mas não
persistidos; são candidatos naturais à coluna `metadata` JSONB já existente ou
a uma coluna dedicada futura (ver seção 3.4).

Volume estimado: um serviço de chat interno começa na casa de milhares a
dezenas de milhares de interações/dia (~1–5 KB por linha com prompt/response em
`TEXT`). Mesmo com crescimento de 10x ao ano, falamos de dezenas de GB em
horizonte de anos — confortável para uma única instância PostgreSQL bem
indexada. A decisão, portanto, não é dirigida por escala de escrita, e sim pela
**flexibilidade analítica futura**.

## 3.2 Comparação de alternativas

| Critério | PostgreSQL / Aurora (escolhido) | DynamoDB | MongoDB / DocumentDB |
|---|---|---|---|
| Modelo de dados | Relacional + JSONB (semiestruturado onde precisa — a coluna `metadata` já usa) | Key-value/wide-column; modelagem guiada pelos padrões de acesso conhecidos de antemão | Documentos; flexível, porém sem schema imposto |
| Consultas analíticas | SQL completo: agregações, window functions, CTEs, joins futuros | Muito limitado; exige export para Athena/Redshift ou GSIs por consulta | Aggregation pipeline capaz, mas menos expressivo que SQL; DocumentDB não suporta todos os operadores do MongoDB |
| Consistência | ACID forte, transações multi-linha | Forte por item (opt-in); transações limitadas e mais caras | Configurável; DocumentDB com nuances próprias |
| Escalabilidade | Vertical + read replicas; Aurora escala storage automaticamente | Praticamente ilimitada, horizontal nativa — o ponto mais forte | Sharding (MongoDB); DocumentDB escala leitura via réplicas |
| Custo em baixo volume | Aurora Serverless v2 escala de 0.5 ACU; local é grátis (docker) | On-demand muito barato em baixo volume — vence neste critério isolado | DocumentDB tem instância mínima cara; sem tier serverless real |
| Operação serverless | Aurora Serverless v2 (auto-scaling de ACUs) | Totalmente serverless, zero administração | Não (DocumentDB é provisionado) |
| Lock-in | Baixo: PostgreSQL roda em qualquer lugar; Aurora é wire-compatible | Alto: API proprietária AWS | Médio: DocumentDB é "compatível com" MongoDB, não MongoDB |
| Ecossistema Python | Excelente: SQLAlchemy 2.0 async + asyncpg + Alembic (já em uso na Parte 1) | boto3/aioboto3; sem ORM/migrations maduros equivalentes | motor/pymongo maduros; migrations menos padronizadas |
| Busca semântica | **pgvector** no mesmo banco | Requer serviço externo (OpenSearch, etc.) | Atlas Vector Search só no MongoDB Atlas (fora da AWS gerenciada); DocumentDB tem suporte vetorial mais recente e limitado |

**Veredito.** DynamoDB venceria se o requisito dominante fosse escala massiva
de escrita com padrões de acesso 100% previsíveis (lookup por chave, sem
consultas ad hoc) — é honesto reconhecer que, num cenário de centenas de
milhares de req/s só gravando e lendo por `(user_id, timestamp)`, ele seria
mais barato e mais simples de operar. MongoDB/DocumentDB não traz vantagem
decisiva: o único apelo (schema flexível) já é coberto pelo JSONB, e perde em
analytics, custo mínimo e fidelidade de compatibilidade. Como o requisito
explícito do desafio é **persistir para análises futuras** — consultas que
ainda não conhecemos —, o SQL do PostgreSQL é a aposta de menor risco.

## 3.3 Por que PostgreSQL / Aurora Serverless v2

1. **Flexibilidade analítica.** Agregações, window functions (p.ex. latência
   p95 por `provider` por dia, evolução de tokens por usuário) e joins com
   tabelas futuras (usuários, custos por modelo) sem mover dados. O rígido e o
   flexível convivem: colunas tipadas para o que é conhecido, `metadata` JSONB
   (já existente no modelo) para o que ainda não é — indexável com GIN quando
   necessário. A evolução da Parte 1 reforça o ponto: a resposta estruturada
   (`resposta`/`dados`/`contexto`/`fontes`) encaixa direto no JSONB, permitindo
   consultas como "todos os indicadores retornados para o usuário X" com
   `jsonb_array_elements` — sem migration de emergência nem segundo datastore.
2. **Caminho natural para busca semântica.** A extensão **pgvector** permite
   armazenar embeddings dos prompts na própria tabela (ou tabela satélite) e
   fazer busca por similaridade com índice HNSW — sem introduzir um segundo
   datastore, sem pipeline de sincronização. Aurora PostgreSQL suporta pgvector
   nativamente.
3. **Elasticidade de custo.** Aurora Serverless v2 escala em ACUs (0.5 até o
   teto configurado) acompanhando a oscilação de carga típica de um serviço de
   chat (picos em horário comercial, vale à noite). Custo inicial baixo, sem
   re-arquitetura quando o tráfego crescer; storage cresce automaticamente.
4. **Consistência dev↔prod e continuidade da Parte 1.** O serviço já roda
   PostgreSQL 16 via docker-compose com SQLAlchemy async + Alembic. Aurora é
   wire-compatible: as mesmas migrations e o mesmo código sobem para a nuvem
   sem mudança — zero divergência entre ambiente local e produção.
5. **Maturidade operacional.** Backups contínuos, PITR, failover multi-AZ,
   criptografia em repouso (KMS) e IAM auth já resolvidos pelo Aurora.

Trade-off assumido: aceitamos um teto de escalabilidade de escrita menor que o
do DynamoDB em troca de poder analítico e portabilidade. Dado o volume
projetado (seção 3.1), esse teto está a ordens de magnitude de distância.

## 3.4 Evolução

- **Read replicas para análises**: leitores Aurora dedicados a dashboards e
  consultas analíticas, isolando o OLTP (o writer só atende `POST /v1/chat` e a
  listagem por usuário).
- **Particionamento por data**: se `interactions` passar de centenas de milhões
  de linhas, particionar por range em `created_at` (mensal) mantém índices
  pequenos e permite arquivar/derrubar partições antigas com custo O(1).
- **Export para S3 + Athena**: para analytics pesado (varredura de todo o
  histórico, treino de modelos, análise de conteúdo em lote), export nativo do
  Aurora para S3 em Parquet e consulta via Athena/Glue — o OLTP nunca é tocado.
- **Persistir a resposta estruturada**: hoje só a frase normalizada vai para
  `response`; o passo natural é gravar o JSON estruturado (`dados`, `contexto`,
  `fontes`, flag `normalizada`) em `metadata` JSONB — ou, se as consultas sobre
  `dados` virarem rotina, promovê-lo a uma coluna própria `structured JSONB`
  com índice GIN via migration Alembic. Como `dados` já vem no formato "uma
  linha por indicador", agregações analíticas (indicadores mais consultados,
  fontes mais citadas, taxa de respostas não normalizadas por modelo) viram
  SQL direto sobre `jsonb_array_elements`, sem ETL.
- **pgvector**: adicionar coluna `embedding vector(n)` (ou tabela
  `interaction_embeddings`) via migration Alembic quando a busca semântica for
  priorizada; backfill assíncrono a partir dos prompts já persistidos.
