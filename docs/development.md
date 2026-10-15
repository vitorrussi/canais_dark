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
Veja `docs/testing.md` (comandos, guardas e a regra de regressão obrigatória).

`tests/conftest.py` fornece `session` (SQLite temporário), `client` (TestClient) e `user_token`.
Integrações externas são sempre mockadas; nenhum teste usa rede.

## Branches e versões
Trabalhe em `dev`; `main` só recebe releases (`deploy/release.sh`). Ver `docs/releases.md`.

## Commits
Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`), pequenos e com testes verdes.
Segredos só em `.env` (ignorado pelo git).

## Adicionar uma etapa ao status do vídeo
Em `services/video_status.py`, escreva `def _x_state(session, video) -> StageState` e registre em
`_STATE_CALCULATORS["<key da etapa>"]`. A UI (`video.html`) e `GET /api/videos/{id}/stages` passam a refletir.

## UI
Templates em `web/templates/`, estáticos vendorizados em `web/static/` (htmx, pico). Rotas em `web/routes.py`
usam `WebUser` (sessão); REST usa `CurrentUser` (Bearer). Padrão: POST → redirect 303; erro de validação → re-render 422.

## MCP
Adicionar tool: função em `mcp/server.py` com `@mcp.tool()` + `@safe` (nessa ordem), usando `with _db() as (s, user):`
e chamando `services/`. Documentar em `docs/mcp-tools.md`. Testes: `tests/test_mcp_tools.py` (chamada direta via
`server.mcp.call_tool` com `current_user.set(...)`) e `tests/test_mcp_auth.py` (HTTP + auth). Testes async usam `pytest.mark.anyio`.

## Convenções de UI
- Campos de formulário: use a macro `field(name, label, value, help, example, tip=...)` — sempre com ajuda e exemplo.
- Ícones: `ic("nome")` (adicione o path SVG em `_macros.html`). Estilos só em `app.css`, com variáveis de tema (`--accent`, `--surface`…).
- Mensagens de sucesso/erro: `_back(request, url, ok="…")` / `error=exc` (viram toast). Erros de validação em re-render: passe `error=`.
- JS global por delegação (`data-copy`, `data-confirm`, `data-fill`), compatível com `hx-boost`.

## Editor de roteiro (JS)
`static/script_editor.js` não tem build nem testes automatizados; teste no navegador chamando `window.__editor`
(ex.: `__editor.act(0,"sentences")`, `__editor.transformAll(__editor.TOOLS.punct,"x")`). O script é carregado dentro do
bloco `content` (não no `<head>`) porque o `hx-boost` só executa scripts do corpo; ele limpa seus listeners via `window.__editorCleanup`.
Regras de negócio (limites 1–200 cenas, 5000 caracteres) ficam em `schemas/script.py`/`services/scripts.py` e são repetidas no JS apenas para feedback imediato.
