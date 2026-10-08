# Estúdio de Canais Dark

Sistema web para gerenciar canais dark do YouTube e o pipeline de produção de vídeos,
operado pelo Claude via MCP. Ver `AGENTS.md` (convenções) e `docs/`.

## Setup

```bash
uv sync
cp .env.example .env
make migrate
uv run canaisdark create-user --name "Seu Nome" --email voce@example.com   # imprime o token
make dev     # http://localhost:8123/health
```

Testes: `make test` · Lint/tipos: `make lint` · Formatar: `make fmt`.

Requisitos: Python ≥ 3.12, uv. (ffmpeg será necessário a partir da Fase 5.)
