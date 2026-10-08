from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session, text

from canaisdark.api.routes import router
from canaisdark.config import get_settings
from canaisdark.db import get_session
from canaisdark.logging import configure_logging
from canaisdark.services.errors import Conflict, DomainError, InvalidState, NotFound

_STATUS: dict[type[DomainError], int] = {NotFound: 404, Conflict: 409, InvalidState: 422}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "dev")
    app = FastAPI(title="Estúdio de Canais Dark")

    @app.get("/health")
    def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return {"status": "ok", "db": "ok"}

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=_STATUS.get(type(exc), 400))

    app.include_router(router)
    return app
