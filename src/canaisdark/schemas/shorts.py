"""Seleção de Shorts: trechos da narração (texto exato) + tempos localizados no alinhamento."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SHORTS_PLAN_VERSION = 1
MAX_SHORTS = 6
MIN_SECONDS = 15.0
MAX_SECONDS = 59.0


class ShortIn(BaseModel):
    """Como o Claude (ou a pessoa) descreve um Short: só o texto exato da narração + título."""

    exact_text: str = Field(min_length=20, max_length=2500)
    title: str = Field(min_length=1, max_length=100)
    hook_override: str | None = Field(default=None, max_length=80)

    @field_validator("exact_text", "title")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("hook_override")
    @classmethod
    def _hook(cls, v: str | None) -> str | None:
        v = " ".join(v.split()) if v else None
        return v or None


class ShortSelection(ShortIn):
    start_s: float = Field(ge=0.0)
    end_s: float = Field(gt=0.0)
    source: Literal["claude", "user"] = "user"
    located: Literal["exact", "fuzzy"] = "exact"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


class ShortsPlan(BaseModel):
    version: int = Field(default=SHORTS_PLAN_VERSION, ge=1, le=SHORTS_PLAN_VERSION)
    skipped: bool = False  # a etapa é opcional: "não vou fazer Shorts neste vídeo"
    timing: Literal["forced", "estimated"] = "forced"
    shorts: list[ShortSelection] = Field(default_factory=list, max_length=MAX_SHORTS)


class SaveShortsRequest(BaseModel):
    shorts: list[ShortIn] = Field(min_length=1, max_length=MAX_SHORTS)
