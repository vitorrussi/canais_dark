.PHONY: dev test lint fmt migrate worker

dev:
	uv run uvicorn canaisdark.main:create_app --reload --factory

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
