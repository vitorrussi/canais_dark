from datetime import datetime

from pydantic import BaseModel, Field


class VideoCreate(BaseModel):
    topic: str = Field(min_length=1)
    language: str | None = None  # None → herda do canal


class VideoUpdate(BaseModel):
    topic: str | None = Field(default=None, min_length=1)
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    language: str | None = None


class VideoRead(BaseModel):
    id: int
    channel_id: int
    topic: str
    status: str
    title: str
    description: str
    tags: list[str]
    language: str
    drive_url: str
    youtube_url: str
    created_by: int
    created_at: datetime
    updated_at: datetime


class StageRead(BaseModel):
    key: str
    label: str
    state: str
