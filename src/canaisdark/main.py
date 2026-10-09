from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, text
from starlette.middleware.sessions import SessionMiddleware

from canaisdark.api.routes import router
from canaisdark.config import get_settings
from canaisdark.db import get_session
from canaisdark.logging import configure_logging
from canaisdark.mcp.server import build_mcp_app, mcp
from canaisdark.services.errors import Conflict, DomainError, InvalidState, NotFound
from canaisdark.web.common import BASE as WEB_BASE
from canaisdark.web.common import NotLoggedIn, templates
from canaisdark.web.routes import router as web_router
from canaisdark.web.scripts import router as web_scripts_router

_STATUS: dict[type[DomainError], int] = {NotFound: 404, Conflict: 409, InvalidState: 422}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "dev")
    if settings.env != "dev" and settings.secret_key == "dev-insecure-change-me":
        raise RuntimeError("Defina SECRET_KEY fora do ambiente dev")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Estúdio de Canais Dark", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.env != "dev",
        max_age=60 * 60 * 24 * 30,
    )
    app.mount("/static", StaticFiles(directory=WEB_BASE / "static"), name="static")

    @app.get("/health")
    def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return {"status": "ok", "db": "ok"}

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> Response:
        code = _STATUS.get(type(exc), 400)
        if request.url.path.startswith(("/api", "/mcp")):
            return JSONResponse({"detail": str(exc)}, status_code=code)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"user": None, "message": str(exc), "code": code},
            status_code=code,
        )

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(_: Request, __: NotLoggedIn) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    app.include_router(router)
    app.include_router(web_router)
    app.include_router(web_scripts_router)
    # Último: o app MCP atende apenas /mcp; demais caminhos caem em 404.
    app.mount("/", build_mcp_app())
    return app
