from sqlmodel import Session
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from canaisdark.db import get_engine
from canaisdark.mcp.context import current_user
from canaisdark.services import users


class BearerAuthMiddleware:
    """Exige `Authorization: Bearer <token>` no endpoint MCP e expõe o usuário via contextvar.

    Extensão futura: OAuth (conectores do Claude Desktop) entra aqui ou via `auth=` do MCPServer.
    """

    def __init__(self, app: ASGIApp, path: str = "/mcp") -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        header = dict(scope["headers"]).get(b"authorization", b"").decode()
        scheme, _, token = header.partition(" ")
        user = None
        if scheme.lower() == "bearer" and token:
            with Session(get_engine()) as session:
                user = users.authenticate_token(session, token.strip())
                if user is not None:
                    session.expunge(user)
        if user is None:
            response = JSONResponse(
                {"detail": "Token ausente ou inválido"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        reset = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(reset)
