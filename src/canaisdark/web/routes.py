from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.db import get_session
from canaisdark.models import User
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import channels, users, video_status, videos
from canaisdark.services.errors import DomainError

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
router = APIRouter(include_in_schema=False)
SessionDep = Annotated[Session, Depends(get_session)]

STATE_LABELS = {
    "not_started": "Não iniciada",
    "pending": "Pendente",
    "in_progress": "Em andamento",
    "done": "Concluída",
    "stale": "Desatualizada",
    "failed": "Falhou",
}


class NotLoggedIn(Exception):
    pass


def web_user(request: Request, session: SessionDep) -> User:
    user_id = request.session.get("user_id")
    user = session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        raise NotLoggedIn
    return user


WebUser = Annotated[User, Depends(web_user)]


def _render(
    request: Request, name: str, user: User | None, status: int = 200, **ctx: object
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, {"user": user, "state_labels": STATE_LABELS, **ctx}, status_code=status
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)


def _channel_form(
    name: str,
    description: str,
    youtube_url: str,
    language: str,
    extra_languages: str,
    elevenlabs_voice_id: str,
    visual_style_prompt: str,
    script_template: str,
    target_duration_minutes: str,
    drive_folder_id: str,
) -> dict[str, object]:
    return {
        "name": name.strip(),
        "description": description,
        "youtube_url": youtube_url.strip(),
        "language": language.strip() or "pt-BR",
        "extra_languages": [x.strip() for x in extra_languages.split(",") if x.strip()],
        "elevenlabs_voice_id": elevenlabs_voice_id.strip(),
        "visual_style_prompt": visual_style_prompt,
        "script_template": script_template,
        "target_duration_minutes": target_duration_minutes or 8,
        "drive_folder_id": drive_folder_id.strip(),
    }


async def _form_data(request: Request) -> dict[str, object]:
    f = await request.form()
    keys = [
        "name",
        "description",
        "youtube_url",
        "language",
        "extra_languages",
        "elevenlabs_voice_id",
        "visual_style_prompt",
        "script_template",
        "target_duration_minutes",
        "drive_folder_id",
    ]
    return _channel_form(**{k: str(f.get(k, "")) for k in keys})


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return _render(request, "login.html", None)


@router.post("/login")
def login(request: Request, session: SessionDep, token: Annotated[str, Form()]) -> Response:
    user = users.authenticate_token(session, token.strip())
    if user is None:
        return _render(request, "login.html", None, status=401, error="Token inválido")
    request.session.clear()
    request.session["user_id"] = user.id
    return _redirect("/")


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return _redirect("/login")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, user: WebUser, session: SessionDep) -> Response:
    return _render(request, "channels.html", user, channels=channels.list_channels(session))


@router.post("/channels")
async def create_channel(request: Request, user: WebUser, session: SessionDep) -> Response:
    data = await _form_data(request)
    try:
        channel = channels.create_channel(session, ChannelCreate(**data))
    except (DomainError, ValidationError) as e:
        return _render(
            request,
            "channels.html",
            user,
            status=422,
            channels=channels.list_channels(session),
            channel=data,
            error=_error_text(e),
        )
    return _redirect(f"/channels/{channel.slug}")


@router.get("/channels/{slug}", response_class=HTMLResponse)
def channel_page(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    return _render(
        request,
        "channel.html",
        user,
        channel=channel,
        videos=videos.list_videos(session, channel.id),
    )


@router.post("/channels/{slug}")
async def update_channel(
    request: Request, user: WebUser, session: SessionDep, slug: str
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    data = await _form_data(request)
    try:
        channels.update_channel(session, channel.id, ChannelUpdate(**data))
    except (DomainError, ValidationError) as e:
        session.rollback()
        return _render(
            request,
            "channel.html",
            user,
            status=422,
            channel=channel,
            videos=videos.list_videos(session, channel.id),
            error=_error_text(e),
        )
    return _redirect(f"/channels/{slug}")


@router.post("/channels/{slug}/archive")
def archive_channel(user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    channels.archive_channel(session, channel.id)
    return _redirect("/")


@router.post("/channels/{slug}/videos")
def create_video(
    request: Request, user: WebUser, session: SessionDep, slug: str, topic: Annotated[str, Form()]
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    try:
        video = videos.create_video(session, channel.id, VideoCreate(topic=topic.strip()), user)
    except (DomainError, ValidationError) as e:
        return _render(
            request,
            "channel.html",
            user,
            status=422,
            channel=channel,
            videos=videos.list_videos(session, channel.id),
            video_error=_error_text(e),
        )
    return _redirect(f"/videos/{video.id}")


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def video_page(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    return _render(
        request,
        "video.html",
        user,
        video=video,
        channel=channels.get_channel(session, video.channel_id),
        stages=video_status.get_stages(session, video),
    )


@router.post("/videos/{video_id}/delete")
def delete_video(user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    videos.delete_video(session, video_id)
    return _redirect(f"/channels/{channel.slug}")
