"""Infra compartilhada das rotas web: templates, sessão, render, flash/redirect."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.db import get_engine, get_session
from canaisdark.models import User
from canaisdark.services import tasks, users
from canaisdark.services.errors import Forbidden

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


def _open_task_count() -> int:
    with Session(get_engine()) as session:
        return len(tasks.list_tasks(session, open_only=True))


templates.env.globals["open_task_count"] = _open_task_count
SessionDep = Annotated[Session, Depends(get_session)]

STATE_LABELS = {
    "not_started": "Não iniciada",
    "pending": "Pendente",
    "in_progress": "Em andamento",
    "done": "Concluída",
    "stale": "Desatualizada",
    "failed": "Falhou",
    "partial": "Só teste",
    "skipped": "Pulada",
}


class NotLoggedIn(Exception):
    pass


def web_user(request: Request, session: SessionDep) -> User:
    user_id = request.session.get("user_id")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        if request.method == "GET" and "hx-request" not in request.headers:
            query = f"?{request.url.query}" if request.url.query else ""
            request.session["next"] = f"{request.url.path}{query}"
        raise NotLoggedIn
    return user


def safe_next(value: object) -> str:
    """Destino pós-login: só caminhos internos (evita open redirect)."""
    text = str(value or "")
    ok = text.startswith("/") and not text.startswith(("//", "/login", "/\\"))
    return text if ok else "/"


WebUser = Annotated[User, Depends(web_user)]


def admin_user(user: WebUser) -> User:
    if not user.is_admin:
        raise Forbidden("Esta área é só para administradores.")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


def _author_label(author: str) -> str:
    with Session(get_engine()) as session:
        return users.author_label(session, author)


templates.env.globals["author_label"] = _author_label


def _static_version(name: str) -> str:
    """Query de cache-busting: muda quando o arquivo estático muda (evita CSS/JS velhos no celular)."""
    try:
        return str(int((BASE / "static" / name).stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["static_v"] = _static_version


def render(
    request: Request, name: str, user: User | None, status: int = 200, **ctx: object
) -> HTMLResponse:
    flash = request.session.pop("flash", None) if "session" in request.scope else None
    if ctx.get("error") and not flash:
        flash = {"kind": "error", "msg": str(ctx["error"])}
    return templates.TemplateResponse(
        request,
        name,
        {"user": user, "state_labels": STATE_LABELS, "flash": flash, "nav": "channels", **ctx},
        status_code=status,
    )


def back(
    request: Request, url: str, error: Exception | None = None, ok: str | None = None
) -> RedirectResponse:
    """Redireciona (PRG) deixando uma mensagem para o próximo render (toast)."""
    if error is not None:
        request.session["flash"] = {"kind": "error", "msg": str(error)}
    elif ok:
        request.session["flash"] = {"kind": "success", "msg": ok}
    return redirect(url)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)
