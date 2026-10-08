from typing import Annotated

from fastapi import Depends, FastAPI
from sqlmodel import Session, text

from canaisdark.api.routes import router
from canaisdark.config import get_settings
from canaisdark.db import get_session
from canaisdark.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "dev")
    app = FastAPI(title="Estúdio de Canais Dark")

    @app.get("/health")
    def health(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return {"status": "ok", "db": "ok"}

    app.include_router(router)
    return app
