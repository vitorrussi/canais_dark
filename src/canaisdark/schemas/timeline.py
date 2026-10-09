"""Timeline de edição (JSON versionado, validado por Pydantic).

Os tempos de cada cena NÃO ficam aqui: vêm do alinhamento (`Scene.start_s/end_s`). A timeline guarda só as
decisões de edição (movimento, transição, textos, legendas, trilha), então continua válida se os tempos mudarem
levemente. `scene_index` é 0-based, igual ao `index` devolvido por `get_script`.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TIMELINE_VERSION = 1


class MotionKind(StrEnum):
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    PAN_UP = "pan_up"
    PAN_DOWN = "pan_down"
    STATIC = "static"


class TransitionKind(StrEnum):
    CUT = "cut"
    CROSSFADE = "crossfade"


class Motion(BaseModel):
    """Movimento da câmera sobre a imagem. `intensity` = quanto da imagem é percorrido (0,10 = 10%)."""

    kind: MotionKind = MotionKind.ZOOM_IN
    intensity: float = Field(default=0.10, ge=0.0, le=0.30)


class Transition(BaseModel):
    """Transição ENTRANDO nesta cena (vinda da cena anterior). Ignorada na primeira cena."""

    kind: TransitionKind = TransitionKind.CROSSFADE
    duration: float = Field(default=0.6, ge=0.0, le=2.0)

    @property
    def seconds(self) -> float:
        return 0.0 if self.kind == TransitionKind.CUT else self.duration


class TextOverlay(BaseModel):
    """Texto na tela. `start_s`/`end_s` são relativos ao início da cena."""

    text: str = Field(min_length=1, max_length=140)
    start_s: float = Field(default=0.0, ge=0.0)
    end_s: float | None = Field(default=None, gt=0.0)  # None = até o fim da cena
    position: Literal["top", "center", "bottom"] = "center"

    @model_validator(mode="after")
    def _order(self) -> "TextOverlay":
        if self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("`end_s` do texto deve ser maior que `start_s`")
        return self


class SceneEdit(BaseModel):
    scene_index: int = Field(ge=0)
    asset_id: int | None = None  # None = usa a imagem/clipe `main` da cena
    motion: Motion = Field(default_factory=Motion)
    transition_in: Transition = Field(default_factory=Transition)
    texts: list[TextOverlay] = Field(default_factory=list, max_length=4)


class SubtitleSettings(BaseModel):
    enabled: bool = False


class MusicSettings(BaseModel):
    asset_id: int | None = None  # None = sem trilha
    volume_db: float = Field(default=-18.0, ge=-40.0, le=0.0)
    ducking: bool = True  # abaixa a trilha enquanto a narração fala


class EffectSettings(BaseModel):
    vignette: bool = True
    grain: bool = False


class Timeline(BaseModel):
    version: Annotated[int, Field(ge=1, le=TIMELINE_VERSION)] = TIMELINE_VERSION
    fade_in_s: float = Field(default=0.6, ge=0.0, le=3.0)  # a partir do preto, no começo do vídeo
    fade_out_s: float = Field(default=0.8, ge=0.0, le=3.0)  # para o preto, no fim
    scenes: list[SceneEdit] = Field(min_length=1)
    subtitles: SubtitleSettings = Field(default_factory=SubtitleSettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    effects: EffectSettings = Field(default_factory=EffectSettings)

    @field_validator("scenes")
    @classmethod
    def _unique_sorted(cls, scenes: list[SceneEdit]) -> list[SceneEdit]:
        indexes = [s.scene_index for s in scenes]
        if len(set(indexes)) != len(indexes):
            dup = sorted({i for i in indexes if indexes.count(i) > 1})
            raise ValueError(f"scene_index repetido: {dup}. Cada cena aparece uma única vez.")
        return sorted(scenes, key=lambda s: s.scene_index)

    def for_scene(self, index: int) -> SceneEdit | None:
        return next((s for s in self.scenes if s.scene_index == index), None)


class SaveTimelineRequest(BaseModel):
    timeline: Timeline
