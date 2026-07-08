# Agente 10 — Justificativa da escolha do banco de dados (Requisito 3)

Aplique primeiro o Contexto Base (`shared/context/base_context.md`).

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
