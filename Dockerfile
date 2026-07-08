# syntax=docker/dockerfile:1

# ============================================================================
# Estágio 1 — builder: instala dependências em um venv isolado.
# Ferramentas de build ficam aqui e NÃO vão para a imagem final.
# ============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

# ============================================================================
# Estágio 2 — runtime: imagem enxuta, usuário non-root, somente o necessário.
# ============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Usuário sem privilégios (system user, sem home gravável desnecessária)
RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /srv/app

# Venv pronto do builder + apenas o código necessário em runtime
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

USER app

EXPOSE 8000

# Healthcheck de liveness via /health (stdlib, sem curl na imagem)
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
