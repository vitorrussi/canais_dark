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
    ("shorts", "Shorts"),
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


_STATE_CALCULATORS: dict[str, Callable[[Session, Video], StageState]] = {
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
