"""Gera o diagrama de arquitetura AWS de referencia (PNG) usando a lib `diagrams`.

Pre-requisitos:
    pip install diagrams          # ja incluso em requirements-dev.txt (ou instale avulso)
    Graphviz instalado e `dot` no PATH (Windows: `winget install Graphviz.Graphviz`).

Execucao (a partir da raiz do repo):
    python docs/diagrams/aws_architecture.py

Saida: docs/diagrams/aws_architecture.png
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Em instalacoes recentes do Windows o Graphviz pode nao estar no PATH da sessao.
_GRAPHVIZ_BIN = r"C:\Program Files\Graphviz\bin"
if shutil.which("dot") is None and Path(_GRAPHVIZ_BIN).exists():
    os.environ["PATH"] = _GRAPHVIZ_BIN + os.pathsep + os.environ.get("PATH", "")

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ElasticContainerServiceService, Fargate
from diagrams.aws.database import Aurora, ElasticacheForRedis
from diagrams.aws.integration import SimpleQueueServiceSqs
from diagrams.aws.management import Cloudwatch
from diagrams.aws.network import APIGateway, CloudFront, ElbApplicationLoadBalancer, Route53
from diagrams.aws.security import SecretsManager, WAF
from diagrams.generic.device import Mobile
from diagrams.saas.chat import Messenger

OUT = Path(__file__).parent / "aws_architecture"

with Diagram(
    "Microservico de Chat LLM - Arquitetura AWS de referencia",
    filename=str(OUT),
    outformat="png",
    show=False,
    direction="LR",
):
    user = Mobile("Cliente")
    dns = Route53("Route 53")
    cdn = CloudFront("CloudFront")
    waf = WAF("AWS WAF\n(rate rules)")
    apigw = APIGateway("API Gateway\n(throttling + auth)")

    with Cluster("VPC"):
        alb = ElbApplicationLoadBalancer("ALB interno\nhealth check /ready")
        with Cluster("ECS Fargate (subnets privadas)"):
            svc = ElasticContainerServiceService("ECS Service\nAuto Scaling")
            tasks = Fargate("Tasks FastAPI\n(2..N replicas)")
            svc - tasks

        redis = ElasticacheForRedis("ElastiCache Redis\nrate limit + cache TTL\n(promocao do in-memory)")
        db = Aurora("Aurora PostgreSQL\nServerless v2")

    secrets = SecretsManager("Secrets Manager\n(API keys LLM)")
    queue = SimpleQueueServiceSqs("SQS\n(modo assincrono)")
    cw = Cloudwatch("CloudWatch\nmetricas + alarmes")

    with Cluster("Provedores LLM (internet)"):
        primary = Messenger("Primario\nGemini se LLM_WEB_SEARCH\nsenao OpenRouter")
        secondary = Messenger("Fallback\n(outro provider)")
        catalogs = Messenger("Catalogos /models\n(selecao automatica)")

    user >> dns >> cdn >> waf >> apigw
    apigw >> Edge(label="VPC Link") >> alb >> svc
    tasks >> Edge(label="SQLAlchemy async") >> db
    tasks >> redis
    tasks >> Edge(style="dashed", label="picos: 202 + fila") >> queue
    queue >> Edge(style="dashed", label="worker Fargate") >> tasks
    tasks >> Edge(label="httpx + retry/CB\n429 = fail-fast") >> primary
    tasks >> Edge(style="dashed", label="fallback") >> secondary
    tasks >> Edge(style="dotted", label="startup + TTL 1h") >> catalogs
    secrets >> Edge(style="dotted") >> tasks
    tasks >> Edge(style="dotted", label="metricas custom") >> cw
    cw >> Edge(style="dotted", label="target tracking") >> svc

print(f"Diagrama gerado em {OUT}.png")
