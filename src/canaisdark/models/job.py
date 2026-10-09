from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class JobType(StrEnum):
    TTS = "tts"
    ALIGNMENT = "alignment"
    RENDER = "render"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    status: str = Field(default=JobStatus.QUEUED.value, index=True)
    progress: int = 0
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    log: str = ""
    error: str = ""
    attempts: int = 0
    worker_id: str = ""
    heartbeat_at: datetime | None = None
    parent_job_id: int | None = Field(default=None, foreign_key="job.id")
    created_by: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    run_after: datetime | None = None  # janela de "desfazer": o worker só pega o job depois disso
    started_at: datetime | None = None
    finished_at: datetime | None = None
