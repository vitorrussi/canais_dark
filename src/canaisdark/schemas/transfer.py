"""Formato de arquivo de exportação/importação (versionado)."""

from typing import Literal

from pydantic import BaseModel, Field

from canaisdark.schemas.channel import ChannelCreate
from canaisdark.schemas.script import SceneIn

FORMAT_VIDEO = "canaisdark.video"
FORMAT_CHANNEL = "canaisdark.channel"
CURRENT_VERSION = 1


class ScriptBundle(BaseModel):
    number: int
    author: str = ""
    approved: bool = False
    created_at: str | None = None
    scenes: list[SceneIn] = Field(min_length=1)


class VideoData(BaseModel):
    topic: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    tags: list[str] = []
    language: str = "pt-BR"


class VideoBundle(BaseModel):
    format: Literal["canaisdark.video"] = "canaisdark.video"
    version: int = CURRENT_VERSION
    exported_at: str
    video: VideoData
    scripts: list[ScriptBundle] = []


class ChannelBundle(BaseModel):
    format: Literal["canaisdark.channel"] = "canaisdark.channel"
    version: int = CURRENT_VERSION
    exported_at: str
    channel: ChannelCreate
    videos: list[VideoBundle] = []
