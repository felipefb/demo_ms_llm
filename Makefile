# Alvos de qualidade e DX. Cada alvo é uma linha simples e funciona também
# no Windows sem make — basta rodar o comando `python -m ...` correspondente
# dentro do venv (ex.: .venv\Scripts\python -m pytest).

PY ?= python

.PHONY: install run test cov lint format typecheck security up down

install:
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PY) -m pytest -q

cov:
	$(PY) -m pytest -q --cov=app --cov-report=term-missing

lint:
	$(PY) -m ruff check app tests
	$(PY) -m ruff format --check app tests

format:
	$(PY) -m ruff check --fix app tests
	$(PY) -m ruff format app tests

typecheck:
	$(PY) -m mypy app

security:
	$(PY) -m bandit -q -r app
	$(PY) -m pip_audit -r requirements.txt -r requirements-dev.txt

up:
	docker compose up -d --build

down:
	docker compose down
