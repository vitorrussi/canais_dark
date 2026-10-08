from contextvars import ContextVar

from canaisdark.models import User

# Usuário autenticado da requisição MCP corrente (definido por BearerAuthMiddleware).
current_user: ContextVar[User | None] = ContextVar("mcp_current_user", default=None)
