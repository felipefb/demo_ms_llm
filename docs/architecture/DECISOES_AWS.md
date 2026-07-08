# Decisões AWS — registro de decisões de arquitetura (ADRs)

> Consolidação de **todas** as decisões AWS da arquitetura final, em formato ADR
> curto: decisão, justificativa e — obrigatoriamente — as alternativas igualmente
> viáveis que não foram usadas e por quê. Cada ADR referencia a seção que a
> detalha ([01](01_arquitetura_escalonamento.md), [02](02_observabilidade.md),
> [03](03_banco_de_dados.md), [04](04_resiliencia.md), [05](05_evolucoes.md)).

Contexto comum a todos: API FastAPI stateless e **I/O-bound** (a maior parte do
tempo de request é esperar o LLM, 5–15s), carga oscilante, PostgreSQL como
persistência, free tiers de LLM como recurso mais escasso, time pequeno
(baixa complexidade operacional é requisito implícito).

---

## ADR-01 — Computação: ECS Fargate

**Decisão.** Rodar o container existente em ECS Fargate, mínimo 2 tasks
multi-AZ, auto scaling por métrica custom de requests em voo (seção 1.3);
Fargate Spot para o excedente acima do piso.

**Justificativa.** Roda o Dockerfile do repo sem adaptação; no modelo async,
uma task barata (0.25–0.5 vCPU) sustenta dezenas de chamadas LLM concorrentes
em espera — o melhor custo por request para workload I/O-bound; operação
simples (sem nós, sem control plane).

**Alternativas viáveis não usadas.**
- **Lambda** — viável (Mangum adapta FastAPI; timeout de 15min comporta o LLM).
  Rejeitada porque o modelo de cobrança escala errado para I/O-bound: 1 request
  = 1 execução cobrada em GB-s durante os 5–15s de **espera** do LLM, enquanto
  uma task Fargate atende dezenas dessas esperas simultaneamente. Cold start
  agrava sob carga oscilante. Continuaria plausível para o worker SQS ou
  tráfego quase nulo.
- **EKS** — viável e mais poderoso (HPA/KEDA, ecossistema). Rejeitada por custo
  fixo (control plane ~US$ 73/mês) e complexidade operacional (upgrades,
  add-ons, IRSA) injustificáveis para **um** serviço; venceria num ecossistema
  multi-serviço com plataforma Kubernetes já existente.
- **App Runner** — o caminho mais simples de todos (container → URL). Rejeitada
  porque o controle de scaling é limitado (só concorrência por instância, sem
  métrica custom, sem step scaling assimétrico), não há Spot e a integração
  fina com VPC/ALB/target tracking exigida pela seção 1.3 não existe. Para um
  MVP sem requisito explícito de escalonamento, seria a escolha.
- **EC2 (ASG)** — mais barato por vCPU em carga alta e sustentada. Rejeitada
  porque carga oscilante desperdiça o ganho (paga-se o vale), e volta a gestão
  de AMI/patching/bin-packing que Fargate elimina.

## ADR-02 — Borda: API Gateway HTTP API (+ CloudFront/WAF)

**Decisão.** API Gateway HTTP API como borda pública com throttling por
estágio e por API key, atrás de CloudFront + WAF (rate rules).

**Justificativa.** O requisito central é amortecer picos: throttling/quotas
nativos devolvem `429` na borda sem custar Fargate; auth por API key casa com
`app/core/auth.py`; paga-se por request (zero em repouso).

**Alternativas viáveis não usadas.**
- **ALB público puro** — viável e mais barato em volume alto sustentado (por
  hora+LCU, não por request). Rejeitada porque não faz throttling nem quota:
  toda a proteção de pico viveria na aplicação, consumindo exatamente a
  computação que se quer proteger. (Um ALB **interno** permanece no desenho,
  como alvo do VPC Link — ver ADR-08.)
- **API Gateway REST API** — mais recursos (usage plans por chave com burst
  fino, cache de resposta, validação de request). Rejeitada porque custa ~3×
  mais por milhão de requests e adiciona latência; o HTTP API cobre o
  necessário (throttling, JWT/lambda authorizer, VPC Link). Se quotas
  comerciais por cliente virarem produto, migrar é evolução natural.

## ADR-03 — Banco: Aurora PostgreSQL Serverless v2 (+ RDS Proxy)

**Decisão.** Aurora PostgreSQL Serverless v2 (ACUs 0.5–16) com RDS Proxy;
detalhes e caracterização da carga na seção 3.

**Justificativa.** Drop-in para o código real (PostgreSQL 16 + SQLAlchemy
async + Alembic, zero mudança); ACUs escalam em segundos acompanhando a carga
oscilante; PITR/failover multi-AZ atendem o RPO/RTO da seção 4.5; RDS Proxy
multiplexa as conexões quando o ECS multiplica tasks.

**Alternativas viáveis não usadas.**
- **RDS PostgreSQL provisionado** — viável e mais barato em carga **estável**.
  Rejeitada porque carga oscilante obriga a provisionar pelo pico (paga-se o
  vale) e o resize tem janela; Serverless v2 cobra proporcional ao uso.
- **DynamoDB** — viável para o padrão de acesso atual (insert + get por id) e
  venceria se o requisito dominante fosse escala massiva serverless (seção 3).
  Rejeitada porque as "análises futuras" do requisito pedem consultas ad-hoc
  (SQL, joins, agregações) que no DynamoDB exigem desenho prévio de índices ou
  export para outro motor — além de jogar fora o código de persistência pronto.
- **DocumentDB** — viável se o payload fosse documento sem esquema. Rejeitada
  porque o dado é relacional e estável (conversas com campos fixos), o código é
  SQLAlchemy, e DocumentDB não tem tier serverless equivalente nem vantagem
  analítica sobre SQL aqui.

## ADR-04 — Cache/estado compartilhado: ElastiCache Redis

**Decisão.** ElastiCache Redis como **promoção** dos dois stores in-memory que
já existem no código: rate limit (`app/core/ratelimit.py`) e cache de respostas
TTL (`app/services/cache.py`, chave = hash(modo+model+prompt),
`LLM_CACHE_TTL_SECONDS`). Mesmas chaves, mesmos TTLs — troca de store, não de
desenho (seções 1.2 e 5.3).

**Alternativas viáveis não usadas.**
- **Manter in-memory por task** — viável (o serviço funciona assim hoje) e é o
  estado aceito no MVP. Rejeitada como estado final porque com N tasks o rate
  limit efetivo vira N× o configurado e o hit rate do cache se dilui em 1/N —
  exatamente a limitação documentada no código.
- **DAX** — rejeitada por incompatibilidade: DAX é cache exclusivo de DynamoDB,
  que não é o banco escolhido (ADR-03).
- **Memcached** — viável para cache puro e ligeiramente mais simples. Rejeitada
  porque o rate limit distribuído precisa de `INCR` atômico com TTL e, no
  futuro, estruturas (sorted sets p/ sliding window) que Memcached não tem;
  usar dois motores para cache e contadores seria complexidade sem ganho.

## ADR-05 — Segredos: Secrets Manager

**Decisão.** Secrets Manager para `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, DSN
do banco e API keys do serviço, injetados como env vars no task definition
(config já é 100% env — zero mudança de código).

**Alternativas viáveis não usadas.**
- **SSM Parameter Store (SecureString)** — perfeitamente viável e **grátis** no
  tier standard; a injeção no ECS é idêntica. Rejeitada (por margem pequena)
  porque Secrets Manager dá rotação automática nativa — decisiva para a
  credencial do banco (integração RDS pronta) — e replicação cross-region que o
  plano de DR (4.5) aproveita. Decisão pragmática: **não-segredos** de
  configuração podem ir ao Parameter Store; segredos rotacionáveis ficam no
  Secrets Manager.

## ADR-06 — Mensageria: SQS

**Decisão.** SQS (fila padrão + DLQ) como válvula de escape para picos
extremos: modo `202 Accepted` + worker, atrás de flag (seção 1.3).

**Justificativa.** Semântica exata do problema: buffer ponto-a-ponto com
backpressure mensurável (profundidade da fila escala o worker), retry/DLQ
nativos, custo por request, zero administração.

**Alternativas viáveis não usadas.**
- **SNS** — viável para fan-out pub/sub. Rejeitada porque não há múltiplos
  consumidores nem broadcast: é um produtor e um pool de workers; SNS sem SQS
  atrás nem oferece retenção/retry adequados — acabaria em SNS→SQS, que é SQS
  com um salto a mais.
- **EventBridge** — viável para roteamento por regras entre muitos
  produtores/consumidores. Rejeitada porque não há taxonomia de eventos nem
  múltiplos alvos; menor throughput por padrão e sem semântica de fila
  (ordenação de retrabalho, DLQ por mensagem) que o worker precisa.
- **Kinesis Data Streams** — viável para streaming ordenado de alto volume com
  replay. Rejeitada porque paga-se por shard-hora (custo fixo em repouso, ruim
  para carga oscilante), ordenação por partição é irrelevante aqui e o modelo
  de consumo (checkpointing) é mais complexo que `ReceiveMessage`/`Delete`.

## ADR-07 — Observabilidade: CloudWatch + AMP/AMG + X-Ray (via ADOT)

**Decisão.** Stack AWS-native com OpenTelemetry como camada neutra: ADOT
collector com destino duplo — `remote_write` → AMP (dashboards/alertas em
Managed Grafana) e EMF → CloudWatch **só** para a métrica de auto scaling;
traces OTLP → X-Ray; logs → CloudWatch Logs (seção 2).

**Justificativa.** O código já expõe Prometheus + OTel: AMP consome sem
tradução; CloudWatch é obrigatório no elo de scaling (Application Auto Scaling
só lê CloudWatch); X-Ray integra IAM/VPC sem agente extra; custo proporcional
ao uso, sem contrato.

**Alternativas viáveis não usadas.**
- **Datadog** — viável e superior em UX/correlação pronta (APM, logs, métricas
  num produto só). Rejeitada pelo custo por host/GB que domina rapidamente um
  serviço pequeno, pelo lock-in de agente/formato, e porque ainda precisaríamos
  do CloudWatch para o auto scaling — pagar-se-ia duas vezes. Faria sentido com
  um time já padronizado em Datadog.
- **Grafana Cloud (ou LGTM auto-hospedado)** — viável; mesmo modelo Prometheus.
  Rejeitada porque AMP/AMG entregam o mesmo Grafana sem sair do IAM/billing da
  AWS nem operar Loki/Tempo/Mimir; a portabilidade fica garantida pelo OTel/
  PromQL de qualquer forma (migrar depois é trocar exporter, não instrumentação).

## ADR-08 — Integração borda→serviço: VPC Link + ALB interno

**Decisão.** API Gateway → **VPC Link** → **ALB interno** → tasks ECS em
subnets privadas; health check `/ready` por target group (seções 1.2 e 4).

**Justificativa.** Mantém as tasks sem exposição pública; o ALB dá health
check HTTP real (retira task sem DB do rodízio — assumido pela seção 4),
deregistration delay para drenar conexões em deploy/scale-in e distribuição
uniforme entre tasks.

**Alternativas viáveis não usadas.**
- **Cloud Map (service discovery) direto no VPC Link** — viável e mais barata
  (sem horas/LCU de ALB): o HTTP API integra com Cloud Map. Rejeitada porque o
  health check do Cloud Map é o do ECS (container-level), mais grosso que o
  `/ready` por target group; perde-se também o deregistration delay e métricas
  de LB (latência/5xx por target) que a seção 2 usa. O custo do ALB interno é
  pequeno diante do ganho operacional.

## ADR-09 — Topologia: multi-AZ em região única

**Decisão.** Todas as camadas multi-AZ (ECS ≥2 tasks em AZs distintas, Aurora
com réplica em outra AZ, ALB/API GW regionais) em **uma** região; DR por
backups/snapshots copiados para região secundária, com IaC para recriação
(RTO ≤ 1h, RPO ≤ 5 min intra-região — seção 4.5).

**Justificativa.** Queda de AZ é o modo de falha comum e fica invisível ao
cliente; queda de região inteira é rara e o custo de mitigá-la em ativo-ativo
não se paga neste escopo.

**Alternativas viáveis não usadas.**
- **Multi-region ativo-ativo** — viável (Route 53 latency/failover routing,
  Aurora Global Database, stack duplicada). Rejeitada porque dobra o custo fixo
  e a complexidade operacional (replicação, consistência do rate limit/cache
  entre regiões, deploys coordenados) para proteger contra um evento raro que o
  negócio deste serviço tolera com RTO de horas. Caminho de evolução já mapeado
  na seção 4.5: Aurora Global Database se o SLO exigir RPO de segundos
  cross-region — sem redesenho.

---

## Visão de conjunto

| Camada | Escolhido | Rejeitados (viáveis) |
|---|---|---|
| Computação | ECS Fargate | Lambda, EKS, App Runner, EC2/ASG |
| Borda | API GW HTTP API + CloudFront/WAF | ALB público puro, REST API GW |
| Banco | Aurora PostgreSQL Serverless v2 | RDS provisionado, DynamoDB, DocumentDB |
| Cache/estado | ElastiCache Redis | in-memory por task (MVP), DAX, Memcached |
| Segredos | Secrets Manager | SSM Parameter Store (parcial: fica p/ config) |
| Mensageria | SQS | SNS, EventBridge, Kinesis |
| Observabilidade | CloudWatch + AMP/AMG + X-Ray | Datadog, Grafana Cloud |
| Borda→serviço | VPC Link + ALB interno | Cloud Map direto |
| Topologia | Multi-AZ single-region | Multi-region ativo-ativo |

O fio condutor das nove decisões é o mesmo: **serviço I/O-bound com carga
oscilante, operado por time pequeno** — favorece cobrança proporcional ao uso
(Fargate/Serverless v2/API GW/SQS), reuso do código existente sem adaptação e
o mínimo de superfícies operacionais novas.
