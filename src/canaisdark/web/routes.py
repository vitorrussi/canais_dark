from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, select

from canaisdark.models import Channel, User, VideoStatus
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.schemas.video import VideoCreate, VideoUpdate
from canaisdark.services import (
    channels,
    dashboard,
    scripts,
    task_context,
    tasks,
    users,
    video_status,
    videos,
)
from canaisdark.services.errors import DomainError
from canaisdark.services.ratelimit import login_limiter
from canaisdark.web.common import (
    BASE,  # noqa: F401  (reexport)
    SessionDep,
    WebUser,
    back,
    error_text,
    redirect,
    render,
)
from canaisdark.web.presets import CHANNEL_PRESETS
from canaisdark.web.settings import PROCESS_ALL_PROMPT

router = APIRouter(include_in_schema=False)


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
    words_per_scene: str,
    script_rules: str,
    default_script_instructions: str,
    tts_model: str,
    tts_stability: str,
    tts_similarity: str,
    tts_speed: str,
    forced_alignment: bool,
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
        "words_per_scene": words_per_scene or 40,
        "script_rules": script_rules,
        "default_script_instructions": default_script_instructions,
        "tts_model": tts_model.strip() or "eleven_flash_v2_5",
        "tts_stability": tts_stability or 0.5,
        "tts_similarity": tts_similarity or 0.75,
        "tts_speed": tts_speed or 1.0,
        "forced_alignment": forced_alignment,
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
        "words_per_scene",
        "script_rules",
        "default_script_instructions",
        "tts_model",
        "tts_stability",
        "tts_similarity",
        "tts_speed",
    ]
    return _channel_form(
        **{k: str(f.get(k, "")) for k in keys},
        forced_alignment=f.get("forced_alignment") is not None,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return render(request, "login.html", None)


def client_ip(request: Request) -> str:
    """IP real do cliente (Cloudflare envia CF-Connecting-IP; fallback para o socket)."""
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "?"
    )


@router.post("/login")
def login(request: Request, session: SessionDep, token: Annotated[str, Form()]) -> Response:
    ip = client_ip(request)
    if login_limiter.blocked(ip):
        return render(
            request,
            "login.html",
            None,
            status=429,
            error="Muitas tentativas. Aguarde alguns minutos e tente de novo.",
        )
    user = users.authenticate_token(session, token.strip())
    if user is None:
        login_limiter.record_failure(ip)
        return render(request, "login.html", None, status=401, error="Token inválido")
    login_limiter.reset(ip)
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/")


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return redirect("/login")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, user: WebUser, session: SessionDep, archived: int = 0) -> Response:
    return render(
        request,
        "channels.html",
        user,
        channels=channels.list_channels(session, include_archived=bool(archived)),
        stats=channels.stats_by_channel(session),
        show_archived=bool(archived),
        attention=dashboard.attention(session),
        recent=dashboard.recent_videos(session),
    )


@router.get("/channels/new", response_class=HTMLResponse)
def new_channel_page(request: Request, user: WebUser) -> Response:
    return render(
        request, "channel_new.html", user, presets=CHANNEL_PRESETS, crumbs=[("Canais", "/")]
    )


@router.post("/channels")
async def create_channel(request: Request, user: WebUser, session: SessionDep) -> Response:
    data = await _form_data(request)
    try:
        channel = channels.create_channel(session, ChannelCreate(**data))
    except (DomainError, ValidationError) as e:
        return render(
            request,
            "channel_new.html",
            user,
            status=422,
            presets=CHANNEL_PRESETS,
            channel=data,
            error=error_text(e),
            crumbs=[("Canais", "/")],
        )
    return back(request, f"/channels/{channel.slug}", ok=f"Canal “{channel.name}” criado")


def _parse_status(value: str | None) -> VideoStatus | None:
    try:
        return VideoStatus(value) if value else None
    except ValueError:
        return None


def _channel_ctx(
    session: Session,
    channel: Channel,
    tab: str = "videos",
    q: str = "",
    status: str = "",
    **extra: Any,
) -> dict[str, Any]:
    assert channel.id is not None
    chan_videos = videos.list_videos(session, channel.id, _parse_status(status), q)
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
        "q": q,
        "status_filter": status,
        "total_videos": len(videos.list_videos(session, channel.id)),
        "statuses": [s.value for s in VideoStatus],
        "crumbs": [("Canais", "/")],
        **extra,
    }


@router.get("/channels/{slug}", response_class=HTMLResponse)
def channel_page(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    tab: str = "videos",
    q: str = "",
    status: str = "",
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    return render(request, "channel.html", user, **_channel_ctx(session, channel, tab, q, status))


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
        return render(
            request,
            "channel.html",
            user,
            status=422,
            **_channel_ctx(session, channel, "settings", error=error_text(e)),
        )
    return back(request, f"/channels/{slug}?tab=settings", ok="Configurações salvas")


@router.post("/channels/{slug}/archive")
def archive_channel(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    channels.archive_channel(session, channel.id)
    return back(request, "/", ok=f"Canal “{channel.name}” arquivado")


@router.post("/channels/{slug}/videos")
def create_video(
    request: Request,
    user: WebUser,
    session: SessionDep,
    slug: str,
    topic: Annotated[str, Form()],
    instructions: Annotated[str, Form()] = "",
) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    try:
        video = videos.create_video(
            session, channel.id, VideoCreate(topic=topic.strip(), instructions=instructions), user
        )
    except (DomainError, ValidationError) as e:
        return render(
            request,
            "channel.html",
            user,
            status=422,
            **_channel_ctx(session, channel, error=error_text(e)),
        )
    return back(request, f"/videos/{video.id}", ok="Vídeo criado — tarefa de roteiro enfileirada")


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
    return render(
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
        return back(request, f"/videos/{video_id}", e)
    return back(request, f"/videos/{video_id}", ok="Tarefa enfileirada para o Claude")


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
        return back(request, f"/videos/{video_id}", e)
    return back(request, f"/videos/{video_id}", ok="Revisão enviada ao Claude")


@router.post("/scripts/{version_id}/approve")
def approve_script(
    request: Request, user: WebUser, session: SessionDep, version_id: int
) -> Response:
    version = scripts.approve_script(session, version_id, user)
    return back(request, f"/videos/{version.video_id}", ok=f"Roteiro v{version.number} aprovado")


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, user: WebUser, session: SessionDep) -> Response:
    claimers = {u.id: u.name for u in session.exec(select(User))}
    return render(
        request,
        "tasks.html",
        user,
        nav="tasks",
        process_all_prompt=PROCESS_ALL_PROMPT,
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
        return back(request, f"/videos/{task.video_id}", e)
    return back(request, request.headers.get("referer", f"/videos/{task.video_id}"))


@router.post("/videos/{video_id}/edit")
def edit_video(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    title: Annotated[str, Form()] = "",
    topic: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    try:
        videos.update_video(
            session,
            video_id,
            VideoUpdate(
                title=title.strip(),
                topic=topic.strip() or None,
                language=language.strip() or None,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                description=description,
            ),
        )
    except (DomainError, ValidationError) as e:
        return back(request, f"/videos/{video_id}", error=ValueError(error_text(e)))
    return back(request, f"/videos/{video_id}", ok="Detalhes do vídeo salvos")


@router.post("/videos/{video_id}/duplicate")
def duplicate_video(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    with_script: Annotated[str | None, Form()] = "on",
) -> Response:
    try:
        new = videos.duplicate_video(session, video_id, user, bool(with_script))
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{new.id}", ok="Vídeo duplicado")


@router.post("/channels/{slug}/unarchive")
def unarchive_channel(request: Request, user: WebUser, session: SessionDep, slug: str) -> Response:
    channel = channels.get_channel_by_slug(session, slug)
    assert channel.id is not None
    channels.unarchive_channel(session, channel.id)
    return back(request, f"/channels/{slug}", ok="Canal reativado")


@router.post("/videos/{video_id}/delete")
def delete_video(user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    videos.delete_video(session, video_id)
    return redirect(f"/channels/{channel.slug}")
