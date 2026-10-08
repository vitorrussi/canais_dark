# Guia de desenvolvimento

## Comandos
| Comando | O que faz |
|---|---|
| `uv sync` | instala dependências |
| `make dev` | servidor com reload |
| `make test` | pytest |
| `make lint` / `make fmt` | ruff + mypy / formatar |
| `make migrate` | `alembic upgrade head` |
| `uv run canaisdark --help` | CLI admin |

## Adicionar uma funcionalidade
1. Modelo em `src/canaisdark/models/` (exportar em `models/__init__.py`) e migração:
   `uv run alembic revision --autogenerate -m "descrição"` — **revisar o arquivo gerado**.
2. Lógica em `services/<area>.py` (funções puras com `Session` explícita) + testes em `tests/`.
3. Casca fina: rota em `api/`, tool MCP em `mcp/` (documentar em `docs/mcp-tools.md`), página em `web/`.
4. Atualizar `AGENTS.md`, `docs/roadmap.md` e docs afetados.

## Testes
`tests/conftest.py` fornece `session` (SQLite temporário), `client` (TestClient) e `user_token`.
Integrações externas são sempre mockadas; nenhum teste usa rede.

## Commits
Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`), pequenos e com testes verdes.
Segredos só em `.env` (ignorado pelo git).
