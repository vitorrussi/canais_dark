from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.task import TaskCreate, TaskRead
from canaisdark.services import scripts, tasks

router = APIRouter(tags=["tasks"])
SessionDep = Annotated[Session, Depends(get_session)]


def _read(task: object) -> TaskRead:
    return TaskRead.model_validate(task, from_attributes=True)


@router.get("/tasks")
def list_tasks(
    _: CurrentUser, session: SessionDep, status: str | None = None, open_only: bool = False
) -> list[TaskRead]:
    return [_read(t) for t in tasks.list_tasks(session, status=status, open_only=open_only)]


@router.post("/videos/{video_id}/tasks", status_code=201)
def request_script(
    user: CurrentUser, session: SessionDep, video_id: int, data: TaskCreate
) -> TaskRead:
    """Enfileira a geração (ou regeração) do roteiro para o Claude."""
    return _read(scripts.request_script(session, video_id, data.instructions, user))


@router.post("/tasks/{task_id}/cancel")
def cancel_task(_: CurrentUser, session: SessionDep, task_id: int) -> TaskRead:
    return _read(tasks.cancel_task(session, task_id))
