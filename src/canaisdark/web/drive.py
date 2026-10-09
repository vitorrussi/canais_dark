"""Google Drive no site: conectar/desconectar canal (OAuth) e, adiante, cartão de sincronização."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from canaisdark.models import JobType, Video
from canaisdark.services import channels, jobs, videos
from canaisdark.services import drive as drive_service
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, templates

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


# ----- Cartão ⑦ Google Drive (página do vídeo) -----

ACTIVE = ("queued", "running")


def drive_ctx(session: Session, video: Video) -> dict[str, Any]:
    assert video.id is not None
    channel = channels.get_channel(session, video.channel_id)
    account = drive_service.get_account(session, channel.id or 0)
    job = jobs.latest_job(session, video.id, JobType.DRIVE_SYNC)
    selected = drive_service.last_options(session, video.id)
    rows = drive_service.overview(session, video, drive_service.ALL_OPTIONS)
    return {
        "video": video,
        "channel": channel,
        "account": account,
        "connected": bool(account and not account.needs_reconnect),
        "setup": drive_service.setup_checklist(),
        "job": job,
        "active": bool(job and job.status in ACTIVE),
        "log": "\n".join((job.log if job else "").strip().splitlines()[-8:]),
        "options": [(k, v, k in selected) for k, v in drive_service.OPTION_LABELS.items()],
        "rows": rows,
        "pending": sum(1 for r in rows if r["state"] != "synced" and r["option"] in selected),
        "folder_url": video.drive_url,
        "open": bool(video.drive_folder_id) or bool(job and job.status in ACTIVE),
    }


@router.get("/videos/{video_id}/drive/card", response_class=HTMLResponse)
def card(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    return templates.TemplateResponse(
        request, "_drive_card.html", {"user": user, "d": drive_ctx(session, video)}
    )


@router.post("/videos/{video_id}/drive/sync")
async def sync(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    form = await request.form()
    options = [str(v) for v in form.getlist("options")]
    try:
        job = drive_service.start_sync(session, video_id, user, options or None)
    except DomainError as e:
        return back(request, f"/videos/{video_id}#drive-card", error=e)
    return back(
        request,
        f"/videos/{video_id}#drive-card",
        ok=f"Sincronização com o Drive enfileirada (job #{job.id}). O worker precisa estar rodando.",
    )
