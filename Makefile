.PHONY: dev serve test lint fmt migrate worker

dev:
	uv run uvicorn canaisdark.main:create_app --reload --factory --port 8123 --proxy-headers --forwarded-allow-ips=127.0.0.1

# Sem reload, atrás do Cloudflare Tunnel (confia nos cabeçalhos X-Forwarded-* só de 127.0.0.1)
serve:
	uv run uvicorn canaisdark.main:create_app --factory --host 127.0.0.1 --port 8123 --proxy-headers --forwarded-allow-ips=127.0.0.1

test:
	uv run pytest

lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy src

fmt:
	uv run ruff check --fix . && uv run ruff format .

migrate:
	uv run alembic upgrade head

worker:
	@echo "Worker ainda não implementado (Fase 3)"
