from datetime import datetime

from pydantic import BaseModel


class TaskRead(BaseModel):
    id: int
    type: str
    video_id: int
    instructions: str
    status: str
    claimed_by: int | None
    claimed_at: datetime | None
    attempts: int
    result_summary: str
    error: str
    created_at: datetime
    finished_at: datetime | None


class TaskCreate(BaseModel):
    instructions: str = ""
