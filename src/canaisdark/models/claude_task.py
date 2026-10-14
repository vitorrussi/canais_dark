from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class TaskType(StrEnum):
    GENERATE_SCRIPT = "generate_script"
    GENERATE_PROMPTS = "generate_prompts"
    GENERATE_TIMELINE = "generate_timeline"
    GENERATE_METADATA = "generate_metadata"
    SELECT_SHORTS = "select_shorts"
    EDIT_VIDEO = "edit_video"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClaudeTask(SQLModel, table=True):
    __tablename__ = "claude_task"

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    instructions: str = ""
    status: str = Field(default=TaskStatus.PENDING.value, index=True)
    claimed_by: int | None = Field(default=None, foreign_key="user.id")
    claimed_at: datetime | None = None
    attempts: int = 0
    result_summary: str = ""
    error: str = ""
    created_by: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
