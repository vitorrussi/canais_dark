"""Shorts no site: cartão ⑤ (OPCIONAL), seleção manual, pedido ao Claude, render e dispensar."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, col, select

from canaisdark.models import ClaudeTask, JobType, TaskType, Video
from canaisdark.schemas.shorts import ShortIn, ShortsPlan
from canaisdark.services import channels, jobs, shorts, shorts_render, task_context, tasks
from canaisdark.services import timeline as tl_service
from canaisdark.services import videos as videos_service
from canaisdark.services.errors import DomainError
from canaisdark.web.common import SessionDep, WebUser, back, templates

router = APIRouter(include_in_schema=False)
ACTIVE = ("queued", "running")


def shorts_ctx(session: Session, video: Video) -> dict[str, Any]:
    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.SHORTS)
    active = bool(job and job.status in ACTIVE)
    found = shorts.latest_plan(session, video.id)
    plan = found[1] if found else ShortsPlan()
    problem = ""
    scenes: list[dict[str, Any]] = []
    try:
        timing = tl_service.collect_timing(session, video)
        scenes = [
            {"index": sp.scene.index, "text": sp.scene.text, "start": sp.start, "end": sp.end}
            for sp in timing.spans
        ]
    except DomainError as e:
        problem = str(e)
    open_task = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video.id,
            ClaudeTask.type == TaskType.SELECT_SHORTS.value,
            col(ClaudeTask.status).in_(["pending", "in_progress"]),
        )
    ).first()
    prompt = (
        task_context.build_user_prompt(
            video.id,
            video.topic,
            channels.get_channel(session, video.channel_id).name,
            TaskType.SELECT_SHORTS.value,
        )
        if open_task
        else None
    )
    stale = bool(found and found[0].based_on_script_version_id != video.approved_script_version_id)
    return {
        "video": video,
        "ffmpeg": _ffmpeg(),
        "job": job,
        "active": active,
        "log": "\n".join((job.log if job else "").strip().splitlines()[-10:]),
        "plan": plan,
        "plan_asset": found[0] if found else None,
        "rendered": shorts_render.best_for(session, video.id),
        "problem": problem,
        "scenes": scenes,
        "open_task": open_task,
        "claude_prompt": prompt,
        "stale": stale,
        "open": active or bool(plan.shorts) or open_task is not None,
    }


def _ffmpeg() -> bool:
    from canaisdark.services import audio

    return audio.ffmpeg_available()


def _items(plan: ShortsPlan) -> list[ShortIn]:
    return [
        ShortIn(exact_text=s.exact_text, title=s.title, hook_override=s.hook_override)
        for s in plan.shorts
    ]


def _current(session: Session, video_id: int) -> ShortsPlan:
    found = shorts.latest_plan(session, video_id)
    return found[1] if found else ShortsPlan()


def _home(video_id: int) -> str:
    return f"/videos/{video_id}#shorts-card"


@router.get("/videos/{video_id}/shorts/card", response_class=HTMLResponse)
def card(request: Request, user: WebUser, session: SessionDep, video_id: int) -> Response:
    video = videos_service.get_video(session, video_id)
    return templates.TemplateResponse(
        request, "_shorts_card.html", {"user": user, "s": shorts_ctx(session, video)}
    )


@router.post("/videos/{video_id}/shorts/add")
def add(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    exact_text: Annotated[str, Form()],
    title: Annotated[str, Form()],
    hook_override: Annotated[str, Form()] = "",
) -> Response:
    """Adiciona um trecho manualmente: o texto é localizado na narração (ou o erro explica o motivo)."""
    try:
        new = ShortIn(exact_text=exact_text, title=title, hook_override=hook_override or None)
        items = [*_items(_current(session, video_id)), new]
        _, plan = shorts.save_selections(session, video_id, items, "user", f"user:{user.id}")
    except (ValidationError, DomainError) as e:
        return back(request, _home(video_id), error=e)
    added = plan.shorts[-1] if plan.shorts else None
    dur = f" ({added.duration:.0f} s)" if added else ""
    return back(request, _home(video_id), ok=f"Trecho adicionado{dur}.")


@router.post("/videos/{video_id}/shorts/{index}/remove")
def remove(
    request: Request, user: WebUser, session: SessionDep, video_id: int, index: int
) -> Response:
    plan = _current(session, video_id)
    items = _items(plan)
    if not 0 <= index < len(items):
        return back(request, _home(video_id), error=DomainError("Short não encontrado."))
    del items[index]
    try:
        if items:
            shorts.save_selections(session, video_id, items, "user", f"user:{user.id}")
        else:
            shorts.save_plan(
                session,
                video_id,
                ShortsPlan(skipped=plan.skipped, timing=plan.timing),
                "user",
                f"user:{user.id}",
            )
    except DomainError as e:
        return back(request, _home(video_id), error=e)
    return back(request, _home(video_id), ok="Trecho removido.")


@router.post("/videos/{video_id}/shorts/request")
def request_claude(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    instructions: Annotated[str, Form()] = "",
) -> Response:
    """Pede ao Claude (via MCP) para escolher os trechos. Só acontece quando a pessoa pede."""
    try:
        tl_service.collect_timing(session, videos_service.get_video(session, video_id))
        tasks.create_task(session, video_id, TaskType.SELECT_SHORTS, instructions.strip(), user)
    except DomainError as e:
        return back(request, _home(video_id), error=e)
    return back(request, _home(video_id), ok="Tarefa de Shorts enfileirada para o Claude.")


@router.post("/videos/{video_id}/shorts/render")
def start_render(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    quality: Annotated[str, Form()] = "preview",
    index: Annotated[str, Form()] = "",
    allow_missing: Annotated[str, Form()] = "",
) -> Response:
    try:
        job = shorts_render.start(
            session,
            video_id,
            user,
            quality,
            int(index) if index.strip() else None,
            bool(allow_missing),
        )
    except (DomainError, ValueError) as e:
        return back(request, _home(video_id), error=e)
    return back(
        request,
        _home(video_id),
        ok=f"Render dos Shorts enfileirado (job #{job.id}). O worker precisa estar rodando.",
    )


@router.post("/videos/{video_id}/shorts/skip")
def skip(
    request: Request,
    user: WebUser,
    session: SessionDep,
    video_id: int,
    undo: Annotated[str, Form()] = "",
) -> Response:
    shorts.set_skipped(session, video_id, not undo, f"user:{user.id}")
    return back(
        request,
        _home(video_id),
        ok="Shorts reativados."
        if undo
        else "Shorts dispensados neste vídeo. Você pode reativar quando quiser.",
    )
