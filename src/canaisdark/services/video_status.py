"""Etapas do pipeline de um vídeo e seus estados.

Cada fase futura implementa o cálculo da sua etapa em `_STATE_CALCULATORS`;
etapas sem calculador aparecem como `not_started`.
"""

from collections.abc import Callable
from enum import StrEnum

from sqlmodel import Session

from canaisdark.models import Video
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

_STATE_CALCULATORS: dict[str, Callable[[Session, Video], StageState]] = {}


def get_stages(session: Session, video: Video) -> list[StageRead]:
    result = []
    for key, label in STAGES:
        calc = _STATE_CALCULATORS.get(key)
        state = calc(session, video) if calc else StageState.NOT_STARTED
        result.append(StageRead(key=key, label=label, state=state.value))
    return result
