"""YouTube no site: conectar/desconectar canal (OAuth) e categoria padrão."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from canaisdark.services import channels
from canaisdark.services import youtube as yt_service
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, redirect

router = APIRouter(include_in_schema=False)


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/channels/{slug}/youtube/connect")
def connect(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    try:
        url = yt_service.connect_url(session, channel, user, _base(request))
    except DomainError as e:
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return RedirectResponse(url, status_code=303)


@router.get("/oauth/youtube/callback")
def callback(
    request: Request,
    user: WebUser,
    session: SessionDep,
    code: str = "",
    state: str = "",
    error: str = "",
) -> Response:
    try:
        if error or not code:
            reason = "Você cancelou a autorização." if error == "access_denied" else error
            raise DomainError(f"O Google não autorizou a conexão. {reason}".strip())
        channel, account = yt_service.complete_connection(
            session, code, state, user, _base(request)
        )
    except DomainError as e:
        try:
            slug = channels.get_channel(session, yt_service.read_state(state)[0]).slug
        except DomainError:
            return back(request, "/", error=e)
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return back(
        request,
        f"/channels/{channel.slug}?tab=settings",
        ok=f"YouTube conectado: {account.youtube_channel_title}.",
    )


@router.post("/channels/{slug}/youtube/disconnect")
def disconnect(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    try:
        yt_service.disconnect(session, channel.id or 0)
    except DomainError as e:
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return back(request, f"/channels/{slug}?tab=settings", ok="YouTube desconectado.")


@router.post("/channels/{slug}/youtube/category")
def category(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    category_id: Annotated[str, Form()],
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    try:
        yt_service.set_category(session, channel.id or 0, category_id)
    except DomainError as e:
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return redirect(f"/channels/{slug}?tab=settings")
