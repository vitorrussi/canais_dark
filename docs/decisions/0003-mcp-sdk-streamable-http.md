# 0003 — MCP: SDK oficial `mcp` (FastMCP), Streamable HTTP stateless em `/mcp`
**Decisão:** usar o SDK oficial montado no FastAPI com `stateless_http=True`.
**Atenção (Fase 2):** o session manager do MCP deve rodar no `lifespan` do app. Fallback: pacote `fastmcp`.
Tools nunca bloqueiam: operações longas criam Job e retornam `job_id`.
