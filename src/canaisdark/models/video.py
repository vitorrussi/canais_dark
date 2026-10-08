from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class VideoStatus(StrEnum):
    DRAFT = "draft"


class Video(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channel.id", index=True)
    topic: str
    status: VideoStatus = VideoStatus.DRAFT
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    language: str = "pt-BR"
    drive_url: str = ""
    youtube_url: str = ""
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
