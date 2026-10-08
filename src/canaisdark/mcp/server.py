from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from canaisdark.config import get_settings
from canaisdark.mcp.auth import BearerAuthMiddleware
from canaisdark.mcp.context import current_user
from canaisdark.models import User

mcp = MCPServer(
    "canais-dark",
    instructions="Estúdio de Canais Dark: fila de tarefas e dados dos canais/vídeos.",
)


def actor() -> User:
    user = current_user.get()
    if user is None:
        raise ToolError("Não autenticado")
    return user


@mcp.tool()
def whoami() -> str:
    """Retorna o nome do usuário autenticado (teste de conexão)."""
    return actor().name


def build_mcp_app() -> BearerAuthMiddleware:
    extra = [h.strip() for h in get_settings().mcp_allowed_hosts.split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *extra],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        + [f"https://{h}" for h in extra],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, transport_security=security
    )
    return BearerAuthMiddleware(app)
