"""YouTube no site: conectar/desconectar canal (OAuth) e categoria padrão."""

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, col, select

from canaisdark.models import ClaudeTask, JobType, TaskType, Video
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.services import (
    channels,
    jobs,
    metadata,
    shorts,
    shorts_render,
    task_context,
    tasks,
    thumbnail,
    videos,
)
from canaisdark.services import youtube as yt_service
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, error_text, redirect, templates

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


# ----- Cartão ⑥ Metadados e YouTube (página do vídeo) -----

ACTIVE = ("queued", "running")


def _problem(session: Session, video_id: int, kind: str, index: int | None = None) -> str:
    try:
        yt_service.prepare(session, video_id, kind, index)
    except DomainError as e:
        return str(e)
    return ""


def youtube_ctx(session: Session, video: Video) -> dict[str, Any]:
    assert video.id is not None
    channel = channels.get_channel(session, video.channel_id)
    account = yt_service.get_account(session, channel.id or 0)
    job = jobs.latest_job(session, video.id, JobType.YOUTUBE_UPLOAD)
    proposal = metadata.latest(session, video.id)
    uploads = yt_service.list_uploads(session, video.id)
    long_upload = next((u for u in uploads if u.meta.get("target") == "long"), None)
    found = shorts.latest_plan(session, video.id)
    finals = {
        int(a.meta.get("index", -1)): a
        for a in reversed(shorts_render.list_shorts(session, video.id, "final"))
    }
    short_rows = []
    for i, sel in enumerate(found[1].shorts if found and not found[1].skipped else []):
        asset = finals.get(i)
        done = next(
            (
                u
                for u in uploads
                if u.meta.get("target") == "short" and u.meta.get("short_index") == i
            ),
            None,
        )
        short_rows.append(
            {
                "index": i,
                "title": sel.title,
                "has_final": asset is not None,
                "upload": done,
                "problem": "" if asset is None else _problem(session, video.id, "short", i),
            }
        )
    open_task = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video.id,
            ClaudeTask.type == TaskType.GENERATE_METADATA.value,
            col(ClaudeTask.status).in_(["pending", "in_progress"]),
        )
    ).first()
    return {
        "video": video,
        "channel": channel,
        "account": account,
        "connected": bool(account and not account.needs_reconnect),
        "setup": yt_service.setup_checklist(),
        "quota": yt_service.quota_info(session),
        "job": job,
        "active": bool(job and job.status in ACTIVE),
        "log": "\n".join((job.log if job else "").strip().splitlines()[-8:]),
        "proposal": proposal[1] if proposal else None,
        "proposal_asset": proposal[0] if proposal else None,
        "problems": metadata.problems(video),
        "thumb": thumbnail.latest(session, video.id),
        "long_problem": _problem(session, video.id, "long"),
        "long_upload": long_upload,
        "shorts": short_rows,
        "open_task": open_task,
        "claude_prompt": (
            task_context.build_user_prompt(
                video.id, video.topic, channel.name, TaskType.GENERATE_METADATA.value
            )
            if open_task
            else None
        ),
    }


def _home(video_id: int) -> str:
    return f"/videos/{video_id}#youtube-card"


@router.get("/videos/{video_id}/youtube/card", response_class=HTMLResponse)
def card(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    return templates.TemplateResponse(
        request, "_youtube_card.html", {"user": user, "y": youtube_ctx(session, video)}
    )


@router.post("/videos/{video_id}/metadata/save")
def save_metadata(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
) -> Response:
    """Salvar = a pessoa editou: grava como versão `user` e já vale no vídeo."""
    try:
        raw = [t for chunk in tags.splitlines() for t in chunk.split(",")]
        data = MetadataIn(title=title, description=description, tags=raw)
        asset = metadata.save_proposal(session, video_id, data, "user", f"user:{user.id}")
        metadata.apply(session, video_id, user, asset.id)
    except (ValidationError, DomainError) as e:
        return back(request, _home(video_id), error=DomainError(error_text(e)))
    return back(request, _home(video_id), ok="Metadados salvos no vídeo.")


@router.post("/videos/{video_id}/metadata/apply")
def apply_metadata(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    try:
        metadata.apply(session, video_id, user)
    except DomainError as e:
        return back(request, _home(video_id), error=e)
    return back(
        request, _home(video_id), ok="Proposta aplicada ao vídeo. Revise e ajuste se quiser."
    )


@router.post("/videos/{video_id}/metadata/request")
def request_metadata(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    try:
        video = videos.get_video(session, video_id)
        if video.approved_script_version_id is None:
            raise DomainError("Aprove o roteiro antes de pedir os metadados.")
        tasks.create_task(session, video_id, TaskType.GENERATE_METADATA, instructions.strip(), user)
    except DomainError as e:
        return back(request, _home(video_id), error=e)
    return back(request, _home(video_id), ok="Tarefa de metadados enfileirada para o Claude.")


@router.post("/videos/{video_id}/thumbnail")
async def upload_thumbnail(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    file: Annotated[UploadFile, File()],
) -> Response:
    try:
        asset = thumbnail.save_upload(session, video_id, await file.read())
    except DomainError as e:
        return back(request, _home(video_id), error=e)
    warn = " Atenção: " + " ".join(asset.meta["warnings"]) if asset.meta.get("warnings") else ""
    return back(request, _home(video_id), ok="Thumbnail salva." + warn)


@router.post("/videos/{video_id}/thumbnail/remove")
def remove_thumbnail(
    request: Request, user: WebUser, session: SessionDep, video_id: int
) -> Response:
    thumbnail.remove(session, video_id)
    return back(request, _home(video_id), ok="Thumbnail removida.")


@router.post("/videos/{video_id}/youtube/upload")
def upload(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    target: Annotated[str, Form()] = "long",
    index: Annotated[str, Form()] = "",
    force: Annotated[str, Form()] = "",
) -> Response:
    try:
        job = yt_service.start_upload(
            session,
            video_id,
            user,
            target,
            int(index) if index.strip() else None,
            bool(force),
        )
    except (DomainError, ValueError) as e:
        return back(request, _home(video_id), error=e)
    return back(
        request,
        _home(video_id),
        ok=f"Envio ao YouTube enfileirado (job #{job.id}), como PRIVADO. O worker precisa estar rodando.",
    )
