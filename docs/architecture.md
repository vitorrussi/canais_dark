# Arquitetura

```
        REST (/api)     MCP (/mcp)     Web (Jinja2+HTMX)     Worker     CLI
              \            |                |                  |         /
               +-----------+----------------+------------------+--------+
                                     services/   (toda a lógica)
                                         |
                              models/ + db.py (SQLModel/SQLite)   integrations/ (ElevenLabs, YouTube, Drive, ffmpeg)
```

## Regra de dependência
`api/`, `mcp/`, `web/`, `worker/`, `cli.py` → `services/` → `models/`, `integrations/`.
Nunca o contrário. Services recebem `Session` (e o usuário/ator) explicitamente e não importam
FastAPI nem MCP; assim são testáveis sem HTTP e sem o Claude.

## Processos
- **web**: uvicorn → FastAPI (`/api`, `/mcp`, UI, `/health`).
- **worker** (Fase 3): processo separado que reserva jobs da tabela `Job` e os executa.
- Ambos usam o mesmo SQLite (WAL, `busy_timeout`) e o mesmo `DATA_DIR`.

## Autenticação
Token Bearer por usuário (`cd_...`), guardado como SHA-256. `api/deps.py:get_current_user` é a
única porta de entrada; o MCP reutilizará via middleware (Fase 2). OAuth no MCP: extensão futura.

## Camadas externas (produção)
Cloudflare Tunnel + Access (site: login por e-mail; `/mcp`: Service Token) + Bearer do app.
