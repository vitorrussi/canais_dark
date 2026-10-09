"""Narração no site: card com estimativas, início do job, progresso e arquivos."""

import json
import mimetypes
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, JobType, User, Video
from canaisdark.services import audio, channels, credits, jobs, narration, scripts, videos
from canaisdark.services.errors import DomainError, NotFound
from canaisdark.web.common import SessionDep, WebUser, back, render, templates

router = APIRouter(include_in_schema=False)

ACTIVE = ("queued", "running")


def _tail(log: str, lines: int = 14) -> str:
    return "\n".join(log.strip().splitlines()[-lines:])


def narration_ctx(session: Session, video: Video) -> dict[str, Any]:
    """Tudo o que o card de narração precisa."""
    assert video.id is not None
    settings = get_settings()
    channel = channels.get_channel(session, video.channel_id)
    balance = credits.get_balance()
    full = narration.plan(session, video.id, None, None, balance)
    test = narration.plan(session, video.id, 1, None, balance)
    cum: list[int] = []
    if full.script_version is not None:
        total = 0
        for i, s in enumerate(scripts.scenes_of(session, full.script_version)):
            total += len(s.text) + (1 if i else 0)
            cum.append(total)
    tts_job = jobs.latest_job(session, video.id, JobType.TTS)
    ali_job = jobs.latest_job(session, video.id, JobType.ALIGNMENT)
    active = [j for j in (tts_job, ali_job) if j and j.status in ACTIVE]
    shown = active[0] if active else (tts_job or ali_job)
    audio_asset = narration.latest_asset(session, video.id, AssetType.AUDIO, "narration")
    align_asset = narration.latest_asset(session, video.id, AssetType.ALIGNMENT)
    doc: dict[str, Any] | None = None
    if align_asset is not None and narration.asset_path(align_asset).exists():
        doc = json.loads(narration.asset_path(align_asset).read_text(encoding="utf-8"))
    scenes = []
    if video.approved_script_version_id:
        version = scripts.get_version(session, video.approved_script_version_id)
        scenes = scripts.scenes_of(session, version)
    users = {u.id: u.name for u in session.exec(select(User))}
    return {
        "video": video,
        "channel": channel,
        "full": full,
        "test": test,
        "cum_chars": cum,
        "multiplier": 0.5
        if ("flash" in channel.tts_model or "turbo" in channel.tts_model)
        else 1.0,
        "balance": balance,
        "api_key_set": settings.elevenlabs_ready,
        "demo": settings.elevenlabs_fake,
        "alignment_global": settings.forced_alignment_enabled,
        "ffmpeg": audio.ffmpeg_available(),
        "tts_job": tts_job,
        "ali_job": ali_job,
        "active": bool(active),
        "log": _tail(shown.log) if shown else "",
        "audio": audio_asset,
        "audio_url": f"/assets/{audio_asset.id}/file" if audio_asset else None,
        "downloads": [
            (label, a.id)
            for label, a in (
                ("Áudio (.mp3)", audio_asset),
                ("Legenda (.srt)", narration.latest_subtitle(session, video.id, "srt")),
                ("Legenda (.ass)", narration.latest_subtitle(session, video.id, "ass")),
                ("Alinhamento (.json)", align_asset),
            )
            if a is not None
        ],
        "doc": doc,
        "scenes": scenes,
        "users": users,
    }


@router.get("/videos/{video_id}/narration/card", response_class=HTMLResponse)
def card(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos.get_video(session, video_id)
    return templates.TemplateResponse(
        request, "_narration_card.html", {"user": user, "n": narration_ctx(session, video)}
    )


@router.post("/videos/{video_id}/narration/start")
def start(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    mode: Annotated[str, Form()] = "test",
    test_scenes: Annotated[int, Form()] = 1,
    align: Annotated[str, Form()] = "default",
) -> Response:
    try:
        job = narration.start(
            session,
            video_id,
            user,
            test_scenes=max(1, test_scenes) if mode == "test" else None,
            align={"on": True, "off": False}.get(align),
            balance=credits.get_balance(force=True),
        )
    except DomainError as e:
        return back(request, f"/videos/{video_id}", error=e)
    return back(
        request,
        f"/videos/{video_id}",
        ok=f"Narração enfileirada (job #{job.id}). O worker precisa estar rodando.",
    )


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, user: WebUser, session: SessionDep, job_id: int) -> Response:
    job = jobs.get_job(session, job_id)
    try:
        jobs.cancel(session, job_id)
    except DomainError as e:
        return back(request, f"/videos/{job.video_id}", error=e)
    return back(
        request, request.headers.get("referer", f"/videos/{job.video_id}"), ok="Job cancelado"
    )


@router.post("/jobs/{job_id}/retry")
def retry(request: Request, user: WebUser, session: SessionDep, job_id: int) -> Response:
    old = jobs.get_job(session, job_id)
    try:
        new = jobs.retry(session, job_id, user)
    except DomainError as e:
        return back(request, f"/videos/{old.video_id}", error=e)
    return back(
        request,
        f"/videos/{old.video_id}",
        ok=f"Job #{new.id} enfileirado (trechos já gerados são reaproveitados)",
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, user: WebUser, session: SessionDep) -> Response:
    rows = jobs.list_jobs(session, limit=60)
    video_titles = {v.id: (v.title or v.topic[:50]) for v in session.exec(select(Video))}
    names = {u.id: u.name for u in session.exec(select(User))}
    return render(
        request,
        "jobs.html",
        user,
        nav="jobs",
        jobs_list=rows,
        video_titles=video_titles,
        names=names,
        crumbs=[("Canais", "/")],
    )


@router.get("/assets/{asset_id}/file")
def asset_file(user: WebUser, session: SessionDep, asset_id: int, download: int = 0) -> Response:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise NotFound("Arquivo não encontrado")
    base = get_settings().data_dir.resolve()
    path = (base / asset.path).resolve()
    if base not in path.parents or not path.is_file():
        raise NotFound("Arquivo não encontrado no disco")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix == ".srt":
        mime = "application/x-subrip"
    elif path.suffix == ".ass":
        mime = "text/x-ssa"
    headers = {
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{path.name}"'
    }
    return FileResponse(path, media_type=mime, headers=headers)
