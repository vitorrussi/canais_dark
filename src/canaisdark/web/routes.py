from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session, select

from canaisdark.db import get_engine, get_session
from canaisdark.models import Channel, User
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.schemas.video import VideoCreate
from canaisdark.services import (
    channels,
    scripts,
    task_context,
    tasks,
    users,
    video_status,
    videos,
)
from canaisdark.services.errors import DomainError
from canaisdark.web.presets import CHANNEL_PRESETS

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


def _open_task_count() -> int:
    with Session(get_engine()) as session:
        return len(tasks.list_tasks(session, open_only=True))


templates.env.globals["open_task_count"] = _open_task_count
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
    flash = request.session.pop("flash", None) if "session" in request.scope else None
    if ctx.get("error") and not flash:
        flash = {"kind": "error", "msg": str(ctx["error"])}
    return templates.TemplateResponse(
        request,
        name,
        {"user": user, "state_labels": STATE_LABELS, "flash": flash, "nav": "channels", **ctx},
        status_code=status,
    )


def _back(
    request: Request, url: str, error: Exception | None = None, ok: str | None = None
) -> RedirectResponse:
    """Redireciona (PRG) deixando uma mensagem para o próximo render (toast)."""
    if error is not None:
        request.session["flash"] = {"kind": "error", "msg": str(error)}
    elif ok:
        request.session["flash"] = {"kind": "success", "msg": ok}
    return _redirect(url)


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
    return _render(
        request,
        "channels.html",
        user,
        channels=channels.list_channels(session),
        stats=channels.stats_by_channel(session),
    )


@router.get("/channels/new", response_class=HTMLResponse)
def new_channel_page(request: Request, user: WebUser) -> Response:
    return _render(
        request, "channel_new.html", user, presets=CHANNEL_PRESETS, crumbs=[("Canais", "/")]
    )


@router.post("/channels")
async def create_channel(request: Request, user: WebUser, session: SessionDep) -> Response:
    data = await _form_data(request)
    try:
        channel = channels.create_channel(session, ChannelCreate(**data))
    except (DomainError, ValidationError) as e:
        return _render(
            request,
            "channel_new.html",
            user,
            status=422,
            presets=CHANNEL_PRESETS,
            channel=data,
            error=_error_text(e),
            crumbs=[("Canais", "/")],
        )
    return _back(request, f"/channels/{channel.slug}", ok=f"Canal “{channel.name}” criado")


def _channel_ctx(
    session: Session, channel: Channel, tab: str = "videos", **extra: Any
) -> dict[str, Any]:
    assert channel.id is not None
    chan_videos = videos.list_videos(session, channel.id)
    vinfo: dict[int | None, dict[str, object]] = {}
    for v in chan_videos:
        stages = video_status.get_stages(session, v)
        open_tasks = tasks.list_tasks(session, video_id=v.id, open_only=True)
        vinfo[v.id] = {
            "done": sum(1 for s in stages if s.state == "done"),
            "total": len(stages),
            "open_task": open_tasks[0].status if open_tasks else None,
        }
    return {
        "channel": channel,
        "videos": chan_videos,
        "vinfo": vinfo,
        "tab": tab,
        "crumbs": [("Canais", "/")],
        **extra,
    }


@router.get("/channels/{slug}", response_class=HTMLResponse)
def channel_page(
    request: Request, user: WebUser, session: SessionDep, slug: str, tab: str = "videos"
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    return _render(request, "channel.html", user, **_channel_ctx(session, channel, tab))


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
            **_channel_ctx(session, channel, "settings", error=_error_text(e)),
        )
    return _back(request, f"/channels/{slug}?tab=settings", ok="Configurações salvas")


@router.post("/channels/{slug}/archive")
def archive_channel(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    channels.archive_channel(session, channel.id)
    return _back(request, "/", ok=f"Canal “{channel.name}” arquivado")


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
            **_channel_ctx(session, channel, error=_error_text(e)),
        )
    return _back(request, f"/videos/{video.id}", ok="Vídeo criado — tarefa de roteiro enfileirada")


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def video_page(
    request: Request, user: WebUser, session: SessionDep, video_id: int, v: int | None = None
) -> Response:
    video = videos.get_video(session, video_id)
    versions = scripts.list_versions(session, video_id)
    selected = next((x for x in versions if x.number == v), None) if v else None
    if selected is None and versions:
        selected = next((x for x in versions if x.approved), versions[0])
    open_tasks = tasks.list_tasks(session, video_id=video_id, open_only=True)
    failed = [t for t in tasks.list_tasks(session, video_id=video_id, status="failed")][-1:]
    claimers = {u.id: u.name for u in session.exec(select(User))}
    channel = channels.get_channel(session, video.channel_id)
    claude_prompt = (
        task_context.build_user_prompt(video_id, video.topic, channel.name, open_tasks[0].type)
        if open_tasks
        else None
    )
    return _render(
        request,
        "video.html",
        user,
        video=video,
        channel=channel,
        crumbs=[("Canais", "/"), (channel.name, f"/channels/{channel.slug}")],
        claude_prompt=claude_prompt,
        stages=video_status.get_stages(session, video),
        versions=versions,
        selected=selected,
        scenes=scripts.scenes_of(session, selected) if selected else [],
        open_tasks=open_tasks,
        failed_tasks=failed,
        claimers=claimers,
    )


@router.post("/videos/{video_id}/script/generate")
def generate_script(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    try:
        scripts.request_script(session, video_id, instructions.strip(), user)
    except DomainError as e:
        return _back(request, f"/videos/{video_id}", e)
    return _back(request, f"/videos/{video_id}", ok="Tarefa enfileirada para o Claude")


@router.post("/videos/{video_id}/script/revision")
def request_revision(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    notes: Annotated[str, Form()] = "",
) -> Response:
    try:
        scripts.request_revision(session, video_id, notes, user)
    except DomainError as e:
        return _back(request, f"/videos/{video_id}", e)
    return _back(request, f"/videos/{video_id}", ok="Revisão enviada ao Claude")


@router.post("/scripts/{version_id}/approve")
def approve_script(
    request: Request, user: WebUser, session: SessionDep, version_id: int
) -> Response:
    version = scripts.approve_script(session, version_id, user)
    return _back(request, f"/videos/{version.video_id}", ok=f"Roteiro v{version.number} aprovado")


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, user: WebUser, session: SessionDep) -> Response:
    claimers = {u.id: u.name for u in session.exec(select(User))}
    return _render(
        request,
        "tasks.html",
        user,
        nav="tasks",
        open_tasks=tasks.list_tasks(session, open_only=True),
        recent=list(reversed(tasks.list_tasks(session)))[:20],
        claimers=claimers,
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(request: Request, user: WebUser, session: SessionDep, task_id: int) -> Response:
    task = tasks.get_task(session, task_id)
    try:
        tasks.cancel_task(session, task_id)
    except DomainError as e:
        return _back(request, f"/videos/{task.video_id}", e)
    return _back(request, request.headers.get("referer", f"/videos/{task.video_id}"))


@router.post("/videos/{video_id}/delete")
def delete_video(user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    videos.delete_video(session, video_id)
    return _redirect(f"/channels/{channel.slug}")
