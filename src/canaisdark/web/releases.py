"""Novidades do sistema (notas de versão) em linguagem simples."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from canaisdark.services import releases, version
from canaisdark.web.common import SessionDep, WebUser, render

router = APIRouter(include_in_schema=False)


@router.get("/novidades", response_class=HTMLResponse)
def novidades(request: Request, user: WebUser, session: SessionDep) -> Response:
    items = releases.load_all()
    newest = items[0] if items else None
    if newest is not None and user.last_seen_version != newest.version:
        user.last_seen_version = newest.version
        session.add(user)
        session.commit()
    return render(
        request,
        "releases.html",
        user,
        nav="releases",
        crumbs=[("Canais", "/")],
        latest=newest,
        previous=items[1:],
        icons=releases.SECTION_ICONS,
        current=version.current(),
    )
