from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobRead(BaseModel):
    id: int
    type: str
    video_id: int
    status: str
    progress: int
    params: dict[str, Any]
    result: dict[str, Any]
    log: str
    error: str
    attempts: int
    parent_job_id: int | None
    created_by: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class NarrationRequest(BaseModel):
    test_scenes: int | None = None  # None = narração completa
    align: bool | None = None  # None = configuração do canal


class NarrationPlanRead(BaseModel):
    can_start: bool
    problems: list[str]
    scenes_total: int
    scenes_used: int
    chars: int
    credits_estimated: int
    chunks: int
    model_id: str
    will_align: bool
    partial: bool
    max_chars: int
    balance_remaining: int | None
