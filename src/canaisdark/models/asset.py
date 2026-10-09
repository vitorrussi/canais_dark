from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class AssetType(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO_CLIP = "video_clip"
    ALIGNMENT = "alignment"
    SUBTITLE = "subtitle"
    TIMELINE = "timeline"
    RENDER = "render"
    MUSIC = "music"
    SHORTS_PLAN = "shorts_plan"
    SHORT = "short"
    METADATA = "metadata"
    THUMBNAIL = "thumbnail"
    UPLOAD = "upload"
    DRIVE_FILE = "drive_file"


class Asset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    scene_id: int | None = Field(default=None, foreign_key="scene.id")
    type: str = Field(index=True)
    path: str  # relativo a DATA_DIR
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    based_on_script_version_id: int | None = Field(default=None, foreign_key="script_version.id")
    job_id: int | None = Field(default=None, foreign_key="job.id")
    drive_file_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
