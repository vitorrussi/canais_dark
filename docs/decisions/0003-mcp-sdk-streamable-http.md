# 0003 — MCP: SDK oficial `mcp` 2.x (`MCPServer`), Streamable HTTP stateless em `/mcp`
**Decisão:** usar `mcp>=2.3,<3` (`from mcp.server.mcpserver import MCPServer`; no v1 era `FastMCP`), com
`streamable_http_app(stateless_http=True)` montado no FastAPI (último mount, serve só `/mcp`).
**Detalhes:** o `session_manager` roda no `lifespan` do FastAPI (`main.py`); auth Bearer por middleware ASGI
próprio + contextvar; proteção DNS-rebinding do SDK ativa — hosts extras via `MCP_ALLOWED_HOSTS`.
**Fallback (não necessário até aqui):** `mcp<2` com FastMCP v1. Tools nunca bloqueiam: operações longas criam Job e retornam `job_id`.
