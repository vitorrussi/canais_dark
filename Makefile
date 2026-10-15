.PHONY: dev serve test test-fast test-ffmpeg coverage check lint fmt migrate worker release-notes

dev:
	uv run uvicorn canaisdark.main:create_app --reload --factory --port 8123 --proxy-headers --forwarded-allow-ips=127.0.0.1

# Sem reload, atrás do Cloudflare Tunnel (confia nos cabeçalhos X-Forwarded-* só de 127.0.0.1)
serve:
	uv run uvicorn canaisdark.main:create_app --factory --host 127.0.0.1 --port 8123 --proxy-headers --forwarded-allow-ips=127.0.0.1

# Padrão: sem os testes que rodam o ffmpeg de verdade (ver docs/testing.md)
test:
	uv run pytest

test-fast:
	uv run pytest -m "not e2e and not ffmpeg"

test-ffmpeg:
	uv run pytest -m ffmpeg

coverage:
	uv run pytest --cov=canaisdark --cov-report=term-missing:skip-covered

# Tudo que o CI confere: lint, tipos, testes e regressão obrigatória
check: lint test
	deploy/check_regressions.sh

lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy src

fmt:
	uv run ruff check --fix . && uv run ruff format .

migrate:
	uv run alembic upgrade head

worker:
	uv run canaisdark worker

# Rascunho bruto das notas: commits desde a última tag, agrupados por tipo (o Claude traduz para linguagem simples)
release-notes:
	@last=$$(git tag --list 'v*' --sort=-v:refname | head -1); \
	range=$${last:+$$last..}HEAD; echo "# Commits em $$range"; \
	for t in feat fix perf refactor docs chore test; do \
	  out=$$(git log --no-merges --pretty='- %s' $$range --grep="^$$t[(:!]" -E); \
	  [ -z "$$out" ] || printf '\n## %s\n%s\n' "$$t" "$$out"; \
	done
