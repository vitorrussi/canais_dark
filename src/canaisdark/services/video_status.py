"""Etapas do pipeline de um vídeo e seus estados.

Cada fase futura implementa o cálculo da sua etapa em `_STATE_CALCULATORS`;
etapas sem calculador aparecem como `not_started`.
"""

from collections.abc import Callable
from enum import StrEnum

from sqlmodel import Session, col, select

from canaisdark.models import (
    AssetType,
    ClaudeTask,
    JobStatus,
    JobType,
    ScriptVersion,
    TaskStatus,
    TaskType,
    Video,
)
from canaisdark.schemas.video import StageRead


class StageState(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"
    PARTIAL = "partial"  # só teste curto
    SKIPPED = "skipped"  # etapa desligada de propósito (ex.: sem Forced Alignment)


STAGES: list[tuple[str, str]] = [
    ("script", "Roteiro"),
    ("script_approval", "Aprovação do roteiro"),
    ("narration", "Narração"),
    ("alignment", "Alinhamento"),
    ("prompts", "Prompts de imagem"),
    ("assets", "Imagens/clipes"),
    ("timeline", "Timeline de edição"),
    ("render", "Render"),
    ("review", "Revisão humana"),
    ("metadata", "Metadados"),
    ("shorts", "Shorts (opcional)"),
    ("youtube", "Upload YouTube"),
    ("drive", "Google Drive"),
]


def _latest_task(session: Session, video: Video, task_type: TaskType) -> ClaudeTask | None:
    stmt = (
        select(ClaudeTask)
        .where(ClaudeTask.video_id == video.id, ClaudeTask.type == task_type.value)
        .order_by(col(ClaudeTask.id).desc())
    )
    return session.exec(stmt).first()


def _task_state(task: ClaudeTask | None) -> StageState | None:
    if task is None:
        return None
    return {
        TaskStatus.PENDING.value: StageState.PENDING,
        TaskStatus.IN_PROGRESS.value: StageState.IN_PROGRESS,
        TaskStatus.FAILED.value: StageState.FAILED,
    }.get(task.status)


def _script_state(session: Session, video: Video) -> StageState:
    task_state = _task_state(_latest_task(session, video, TaskType.GENERATE_SCRIPT))
    has_version = session.exec(
        select(ScriptVersion.id).where(ScriptVersion.video_id == video.id)
    ).first()
    if task_state in (StageState.PENDING, StageState.IN_PROGRESS):
        return task_state
    if has_version:
        return StageState.DONE
    return task_state or StageState.NOT_STARTED


def _script_approval_state(session: Session, video: Video) -> StageState:
    if video.approved_script_version_id is not None:
        return StageState.DONE
    has_version = session.exec(
        select(ScriptVersion.id).where(ScriptVersion.video_id == video.id)
    ).first()
    return StageState.PENDING if has_version else StageState.NOT_STARTED


def _job_state(job_status: str | None) -> StageState | None:
    return {
        JobStatus.QUEUED.value: StageState.PENDING,
        JobStatus.RUNNING.value: StageState.IN_PROGRESS,
    }.get(job_status or "")


def _narration_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import jobs, narration

    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.TTS)
    if (running := _job_state(job.status if job else None)) is not None:
        return running
    audio = narration.latest_asset(session, video.id, AssetType.AUDIO, "narration")
    if audio is not None:
        if audio.meta.get("partial"):
            return StageState.PARTIAL
        if audio.based_on_script_version_id != video.approved_script_version_id:
            return StageState.STALE
        return StageState.DONE
    if job and job.status == JobStatus.FAILED.value:
        return StageState.FAILED
    return StageState.NOT_STARTED


def _alignment_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import jobs, narration

    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.ALIGNMENT)
    if (running := _job_state(job.status if job else None)) is not None:
        return running
    audio = narration.latest_asset(session, video.id, AssetType.AUDIO, "narration")
    if audio is None:
        return StageState.NOT_STARTED
    timing = narration.latest_asset(session, video.id, AssetType.ALIGNMENT)
    has_forced = (
        timing is not None
        and timing.meta.get("timing") == "forced"
        and timing.meta.get("audio_asset_id") == audio.id
    )
    if not has_forced:
        if audio.meta.get("align") is False:
            return StageState.SKIPPED
        return (
            StageState.FAILED
            if job and job.status == JobStatus.FAILED.value
            else StageState.NOT_STARTED
        )
    if audio.meta.get("partial"):
        return StageState.PARTIAL
    if audio.based_on_script_version_id != video.approved_script_version_id:
        return StageState.STALE
    return StageState.DONE


def _approved_scenes(session: Session, video: Video) -> list:  # type: ignore[type-arg]
    from canaisdark.services import scripts

    if video.approved_script_version_id is None:
        return []
    return scripts.scenes_of(
        session, scripts.get_version(session, video.approved_script_version_id)
    )


def _prompts_state(session: Session, video: Video) -> StageState:
    scenes = _approved_scenes(session, video)
    if not scenes:
        return StageState.NOT_STARTED
    have = sum(1 for s in scenes if s.image_prompt.strip())
    task = _latest_task(session, video, TaskType.GENERATE_PROMPTS)
    if have == len(scenes):
        return StageState.DONE
    if (running := _task_state(task)) in (StageState.PENDING, StageState.IN_PROGRESS):
        return running
    if have:
        return StageState.PARTIAL
    return StageState.FAILED if running == StageState.FAILED else StageState.NOT_STARTED


def _assets_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import scene_assets

    scenes = _approved_scenes(session, video)
    if not scenes:
        return StageState.NOT_STARTED
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    needed = have = 0
    for s in scenes:
        present = amap.get(s.id or 0, {})
        needed += 1
        have += "main" in present
        if s.wants_vertical:
            needed += 1
            have += "vertical" in present
    if have == 0:
        return StageState.NOT_STARTED
    return StageState.DONE if have == needed else StageState.PARTIAL


def _current_hash(session: Session, video: Video) -> str | None:
    from canaisdark.services import timeline
    from canaisdark.services.errors import InvalidState

    try:
        return timeline.inputs_hash(session, video)
    except InvalidState:
        return None


def _timeline_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import timeline

    assert video.id is not None
    task_state = _task_state(_latest_task(session, video, TaskType.GENERATE_TIMELINE))
    if task_state in (StageState.PENDING, StageState.IN_PROGRESS):
        return task_state
    found = timeline.list_versions(session, video.id)
    if not found:
        return task_state or StageState.NOT_STARTED
    current = _current_hash(session, video)
    if current is None or found[-1].meta.get("inputs_hash") != current:
        return StageState.STALE
    return StageState.DONE


def _render_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import jobs, render, timeline

    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.RENDER)
    if (running := _job_state(job.status if job else None)) is not None:
        return running
    final = render.latest_render(session, video.id, "final")
    preview = render.latest_render(session, video.id, "preview")
    best = final or preview
    if best is None:
        return (
            StageState.FAILED
            if job and job.status == JobStatus.FAILED.value
            else StageState.NOT_STARTED
        )
    versions = timeline.list_versions(session, video.id)
    current = _current_hash(session, video)
    if (
        current is None
        or best.meta.get("inputs_hash") != current
        or (versions and best.meta.get("timeline_asset_id") != versions[-1].id)
    ):
        return StageState.STALE
    if final is None or final.meta.get("partial"):
        return StageState.PARTIAL  # só prévia (ou teste curto)
    return StageState.DONE


def _review_state(session: Session, video: Video) -> StageState:
    from canaisdark.services import render

    assert video.id is not None
    final = render.latest_render(session, video.id, "final")
    if final is None or _render_state(session, video) in (StageState.STALE, StageState.PARTIAL):
        return StageState.NOT_STARTED
    return StageState.DONE if final.meta.get("approved_by") else StageState.PENDING


def _shorts_state(session: Session, video: Video) -> StageState:
    """Etapa OPCIONAL: nada depende dela; SKIPPED = dispensada de propósito."""
    from canaisdark.services import jobs, shorts, shorts_render

    assert video.id is not None
    job = jobs.latest_job(session, video.id, JobType.SHORTS)
    if (running := _job_state(job.status if job else None)) is not None:
        return running
    found = shorts.latest_plan(session, video.id)
    if found is None:
        return (
            _task_state(_latest_task(session, video, TaskType.SELECT_SHORTS))
            or StageState.NOT_STARTED
        )
    asset, plan = found
    if plan.skipped:
        return StageState.SKIPPED
    if not plan.shorts:
        return StageState.NOT_STARTED
    if asset.based_on_script_version_id != video.approved_script_version_id:
        return StageState.STALE
    rendered = shorts_render.best_for(session, video.id)
    done = sum(1 for i in range(len(plan.shorts)) if rendered.get(i) is not None)
    if done == len(plan.shorts):
        return StageState.DONE
    return StageState.PARTIAL if done else StageState.PENDING


_STATE_CALCULATORS: dict[str, Callable[[Session, Video], StageState]] = {
    "timeline": _timeline_state,
    "render": _render_state,
    "review": _review_state,
    "shorts": _shorts_state,
    "prompts": _prompts_state,
    "assets": _assets_state,
    "script": _script_state,
    "script_approval": _script_approval_state,
    "narration": _narration_state,
    "alignment": _alignment_state,
}


def get_stages(session: Session, video: Video) -> list[StageRead]:
    result = []
    for key, label in STAGES:
        calc = _STATE_CALCULATORS.get(key)
        state = calc(session, video) if calc else StageState.NOT_STARTED
        result.append(StageRead(key=key, label=label, state=state.value))
    return result
