"""Etapas do pipeline de um vídeo e seus estados.

Cada fase futura implementa o cálculo da sua etapa em `_STATE_CALCULATORS`;
etapas sem calculador aparecem como `not_started`.
"""

from collections.abc import Callable
from enum import StrEnum

from sqlmodel import Session, col, select

from canaisdark.models import ClaudeTask, ScriptVersion, TaskStatus, TaskType, Video
from canaisdark.schemas.video import StageRead


class StageState(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    STALE = "stale"
    FAILED = "failed"


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


_STATE_CALCULATORS: dict[str, Callable[[Session, Video], StageState]] = {
    "script": _script_state,
    "script_approval": _script_approval_state,
}


def get_stages(session: Session, video: Video) -> list[StageRead]:
    result = []
    for key, label in STAGES:
        calc = _STATE_CALCULATORS.get(key)
        state = calc(session, video) if calc else StageState.NOT_STARTED
        result.append(StageRead(key=key, label=label, state=state.value))
    return result
