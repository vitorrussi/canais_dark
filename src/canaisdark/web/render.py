"""Timeline e render no site: card do passo ④, editor de timeline, trilha sonora e aprovação."""

import json
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, col, select

from canaisdark.models import Asset, ClaudeTask, JobType, TaskType, User, Video
from canaisdark.schemas.timeline import (
    Motion,
    MotionKind,
    TextOverlay,
    Timeline,
    Transition,
    TransitionKind,
)
from canaisdark.services import channels, jobs, music, render, task_context, tasks, timeline, videos
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, templates
from canaisdark.web.common import render as render_page

router = APIRouter(include_in_schema=False)

ACTIVE = ("queued", "running")
MOTION_LABELS = {
    "zoom_in": "Zoom in",
    "zoom_out": "Zoom out",
    "pan_left": "Pan ← esquerda",
    "pan_right": "Pan → direita",
    "pan_up": "Pan ↑ cima",
    "pan_down": "Pan ↓ baixo",
    "static": "Parado",
}


def _label(video_id: int, user: User) -> str:
    return f"user:{user.id}"


def current_timeline(session: Session, video: Video) -> tuple[Asset | None, Timeline | None, str]:
    """(versão salva, timeline a mostrar, aviso). Sem versão salva, mostra a padrão (ainda não salva)."""
    assert video.id is not None
    found = timeline.latest(session, video.id)
    if found:
        return found[0], found[1], ""
    try:
        return None, timeline.build_default(session, video), ""
    except DomainError as e:
        return None, None, str(e)


def render_ctx(session: Session, video: Video) -> dict[str, Any]:
    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.RENDER)
    active = bool(job and job.status in ACTIVE)
    tl_asset, tl, tl_problem = current_timeline(session, video)
    problems = ""
    if not active and tl is not None:
        try:
            render.plan(session, video, tl, render.PREVIEW)
        except DomainError as e:
            problems = str(e)
    elif tl is None:
        problems = tl_problem
    preview = render.latest_render(session, video.id, "preview")
    final = render.latest_render(session, video.id, "final")
    stale_tl = bool(
        tl_asset
        and tl is not None
        and not tl_problem
        and tl_asset.meta.get("inputs_hash") != _safe_hash(session, video)
    )
    users = {u.id: u.name for u in session.exec(select(User))}
    open_task = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video.id,
            ClaudeTask.type == TaskType.GENERATE_TIMELINE.value,
            col(ClaudeTask.status).in_(["pending", "in_progress"]),
        )
    ).first()
    edit_task = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video.id,
            ClaudeTask.type == TaskType.EDIT_VIDEO.value,
            col(ClaudeTask.status).in_(["pending", "in_progress"]),
        )
    ).first()
    claude_prompt = (
        task_context.build_user_prompt(
            video.id,
            video.topic,
            channels.get_channel(session, video.channel_id).name,
            TaskType.GENERATE_TIMELINE.value,
        )
        if open_task
        else None
    )
    return {
        "open_task": open_task,
        "edit_task": edit_task,
        "claude_prompt": claude_prompt,
        "video": video,
        "ffmpeg": _ffmpeg(),
        "job": job,
        "active": active,
        "log": "\n".join((job.log if job else "").strip().splitlines()[-14:]),
        "tl_asset": tl_asset,
        "tl": tl,
        "tl_stale": stale_tl,
        "problems": problems,
        "preview": preview,
        "final": final,
        "music": music.list_music(session, video.id),
        "channel_music": music.list_channel_music(session, video.id),
        "users": users,
        "fresh": {
            "preview": _fresh(session, video, preview, tl_asset),
            "final": _fresh(session, video, final, tl_asset),
        },
    }


def _ffmpeg() -> bool:
    from canaisdark.services import audio

    return audio.ffmpeg_available()


def _safe_hash(session: Session, video: Video) -> str | None:
    try:
        return timeline.inputs_hash(session, video)
    except DomainError:
        return None


def _fresh(session: Session, video: Video, asset: Asset | None, tl_asset: Asset | None) -> bool:
    if asset is not None and asset.meta.get("external"):
        return asset.meta.get("inputs_hash") == _safe_hash(session, video)
    if asset is None or tl_asset is None:
        return False
    return asset.meta.get("timeline_asset_id") == tl_asset.id and asset.meta.get(
        "inputs_hash"
    ) == _safe_hash(session, video)


@router.get("/videos/{video_id}/render/card", response_class=HTMLResponse)
def card(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    return templates.TemplateResponse(
        request, "_render_card.html", {"user": user, "r": render_ctx(session, video)}
    )


@router.post("/videos/{video_id}/render/start")
def start(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    quality: Annotated[str, Form()] = "preview",
    subtitles: Annotated[str, Form()] = "default",
    allow_missing: Annotated[str, Form()] = "",
) -> Response:
    try:
        job = render.start(
            session,
            video_id,
            user,
            quality,
            {"on": True, "off": False}.get(subtitles),
            bool(allow_missing),
        )
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(
        request,
        f"/videos/{video_id}",
        ok=f"Render {'final' if quality == 'final' else 'de prévia'} enfileirado (job #{job.id}). O worker precisa estar rodando.",
    )


@router.post("/videos/{video_id}/render/approve")
def approve(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    try:
        render.approve(session, video_id, user)
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Vídeo aprovado na revisão.")


# ----- Timeline -----


def _save_user_timeline(session: Session, video_id: int, user: User, tl: Timeline) -> Asset:
    return timeline.save(session, video_id, tl, "user", _label(video_id, user))


def _update(
    session: Session, video_id: int, user: User, change: Callable[[Timeline], None]
) -> Asset:
    video = videos.get_video(session, video_id)
    _, tl, problem = current_timeline(session, video)
    if tl is None:
        raise DomainError(problem or "Não há timeline.")
    tl = tl.model_copy(deep=True)
    change(tl)
    return _save_user_timeline(session, video_id, user, Timeline.model_validate(tl.model_dump()))


@router.post("/videos/{video_id}/timeline/request")
def timeline_request(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    """Pede ao Claude (via MCP) para refinar a timeline; a automática continua valendo sem isso."""
    try:
        timeline.collect_timing(session, videos.get_video(session, video_id))
        tasks.create_task(session, video_id, TaskType.GENERATE_TIMELINE, instructions.strip(), user)
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Tarefa de timeline enfileirada para o Claude.")


@router.post("/videos/{video_id}/edit/request")
def edit_request(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    """Pede a edição livre ao Claude (claude.ai com conector); o render nativo continua disponível."""
    try:
        timeline.collect_timing(session, videos.get_video(session, video_id))
        tasks.create_task(session, video_id, TaskType.EDIT_VIDEO, instructions.strip(), user)
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(
        request, f"/videos/{video_id}", ok="Pedido de edição livre enfileirado para o Claude."
    )


@router.post("/videos/{video_id}/timeline/auto")
def timeline_auto(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    """Gera/atualiza a timeline automática (mantém as escolhas por cena já feitas)."""
    video = videos.get_video(session, video_id)
    try:
        found = timeline.latest(session, video_id)
        base = found[1] if found else None
        tl = timeline.build_default(session, video, base)
        timeline.save(session, video_id, tl, "auto", "sistema")
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Timeline atualizada.")


@router.get("/videos/{video_id}/timeline", response_class=HTMLResponse)
def timeline_page(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    from canaisdark.services import channels

    channel = channels.get_channel(session, video.channel_id)
    tl_asset, tl, problem = current_timeline(session, video)
    rows: list[dict[str, Any]] = []
    if tl is not None:
        timing = timeline.collect_timing(session, video)
        assets = timeline.main_assets(session, [sp.scene for sp in timing.spans])
        for sp in timing.spans:
            edit = tl.for_scene(sp.scene.index)
            a = assets.get(sp.scene.id or 0)
            rows.append(
                {
                    "span": sp,
                    "edit": edit,
                    "asset": a,
                    "text": edit.texts[0] if edit and edit.texts else None,
                }
            )
    return render_page(
        request,
        "timeline.html",
        user,
        video=video,
        channel=channel,
        tl=tl,
        tl_asset=tl_asset,
        problem=problem,
        rows=rows,
        versions=timeline.list_versions(session, video_id),
        music=music.list_music(session, video_id),
        motions=MOTION_LABELS,
        tl_json=tl.model_dump_json(indent=2) if tl else "",
        crumbs=[
            ("Canais", "/"),
            (channel.name, f"/channels/{channel.slug}"),
            (video.title or f"Vídeo #{video.id}", f"/videos/{video.id}"),
        ],
    )


@router.post("/videos/{video_id}/timeline")
async def timeline_save(
    request: Request, user: WebUser, session: SessionDep, video_id: int
) -> Response:
    form = await request.form()
    video = videos.get_video(session, video_id)
    _, tl, problem = current_timeline(session, video)
    if tl is None:
        return back(request, f"/videos/{video_id}/timeline", error=DomainError(problem))
    new = tl.model_copy(deep=True)
    try:
        for edit in new.scenes:
            i = edit.scene_index
            if f"motion_{i}" in form:
                edit.motion = Motion(
                    kind=MotionKind(str(form[f"motion_{i}"])),
                    intensity=float(str(form.get(f"intensity_{i}") or edit.motion.intensity)),
                )
                edit.transition_in = Transition(
                    kind=TransitionKind(str(form.get(f"trkind_{i}", "crossfade"))),
                    duration=float(str(form.get(f"trdur_{i}") or edit.transition_in.duration)),
                )
                text = str(form.get(f"text_{i}", "")).strip()
                edit.texts = (
                    [TextOverlay(text=text, position=str(form.get(f"textpos_{i}", "center")))]
                    if text
                    else []
                )
        new.fade_in_s = float(str(form.get("fade_in_s") or new.fade_in_s))
        new.fade_out_s = float(str(form.get("fade_out_s") or new.fade_out_s))
        new.effects.vignette = "vignette" in form
        new.effects.grain = "grain" in form
        new.subtitles.enabled = "subtitles" in form
        new = Timeline.model_validate(new.model_dump())
        _save_user_timeline(session, video_id, user, new)
    except (ValidationError, ValueError, DomainError) as e:
        return back(request, f"/videos/{video_id}/timeline", error=e)
    return back(request, f"/videos/{video_id}/timeline", ok="Nova versão da timeline salva.")


@router.post("/videos/{video_id}/timeline/json")
def timeline_json(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    payload: Annotated[str, Form()],
) -> Response:
    try:
        tl = Timeline.model_validate(json.loads(payload))
        _save_user_timeline(session, video_id, user, tl)
    except (json.JSONDecodeError, ValidationError) as e:
        msg = "JSON inválido" if isinstance(e, json.JSONDecodeError) else str(e.errors()[0]["msg"])
        return back(request, f"/videos/{video_id}/timeline", error=DomainError(msg))
    except DomainError as e:
        return back(request, f"/videos/{video_id}/timeline", error=e)
    return back(request, f"/videos/{video_id}/timeline", ok="Nova versão da timeline salva.")


# ----- Trilha sonora -----


@router.post("/videos/{video_id}/music")
async def music_upload(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    file: Annotated[UploadFile, File()],
) -> Response:
    try:
        asset = music.save_upload(session, video_id, await file.read(), file.filename or "trilha")
        _update(session, video_id, user, lambda t: setattr(t.music, "asset_id", asset.id))
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Trilha enviada e selecionada.")


@router.post("/videos/{video_id}/music/reuse")
def music_reuse(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    asset_id: Annotated[int, Form()],
) -> Response:
    try:
        asset = music.reuse(session, video_id, asset_id)
        _update(session, video_id, user, lambda t: setattr(t.music, "asset_id", asset.id))
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Trilha copiada e selecionada.")


@router.post("/videos/{video_id}/music/settings")
def music_settings(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    asset_id: Annotated[str, Form()] = "",
    volume_db: Annotated[float, Form()] = -18.0,
    ducking: Annotated[str, Form()] = "",
    subtitles: Annotated[str, Form()] = "",
) -> Response:
    def change(t: Timeline) -> None:
        t.music.asset_id = int(asset_id) if asset_id else None
        t.music.volume_db = volume_db
        t.music.ducking = bool(ducking)
        t.subtitles.enabled = bool(subtitles)

    try:
        _update(session, video_id, user, change)
    except (DomainError, ValidationError) as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Ajustes de áudio e legendas salvos.")


@router.post("/videos/{video_id}/music/{asset_id}/remove")
def music_remove(
    request: Request, user: WebUser, session: SessionDep, video_id: int, asset_id: int
) -> Response:
    try:
        found = timeline.latest(session, video_id)
        if found and found[1].music.asset_id == asset_id:
            _update(session, video_id, user, lambda t: setattr(t.music, "asset_id", None))
        music.remove(session, video_id, asset_id)
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(request, f"/videos/{video_id}", ok="Trilha removida.")
