"""Google Drive no site: conectar/desconectar canal (OAuth) e, adiante, cartão de sincronização."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from canaisdark.services import channels
from canaisdark.services import drive as drive_service
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back

router = APIRouter(include_in_schema=False)


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/channels/{slug}/drive/connect")
def connect(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    try:
        url = drive_service.connect_url(session, channel, user, _base(request))
    except DomainError as e:
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return RedirectResponse(url, status_code=303)


@router.get("/oauth/drive/callback")
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
        channel, account = drive_service.complete_connection(
            session, code, state, user, _base(request)
        )
    except DomainError as e:
        try:
            slug = channels.get_channel(session, drive_service.read_state(state)[0]).slug
        except DomainError:
            return back(request, "/", error=e)
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    who = f" ({account.account_email})" if account.account_email else ""
    return back(
        request, f"/channels/{channel.slug}?tab=settings", ok=f"Google Drive conectado{who}."
    )


@router.post("/channels/{slug}/drive/disconnect")
def disconnect(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    try:
        drive_service.disconnect(session, channel.id or 0)
    except DomainError as e:
        return back(request, f"/channels/{slug}?tab=settings", error=e)
    return back(
        request,
        f"/channels/{slug}?tab=settings",
        ok="Google Drive desconectado. Os arquivos já enviados continuam no seu Drive.",
    )
