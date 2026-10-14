from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.services import offline_tasks

router = APIRouter(tags=["offline"])
SessionDep = Annotated[Session, Depends(get_session)]


class OfflineAnswer(BaseModel):
    answer: str


@router.get("/videos/{video_id}/claude/{task_type}")
def get_prompt(
    user: CurrentUser, session: SessionDep, video_id: int, task_type: str
) -> dict[str, Any]:
    """Prompt autossuficiente para colar em qualquer Claude (sem MCP)."""
    return {
        "task_type": task_type,
        "prompt": offline_tasks.build_prompt(session, video_id, task_type, user),
    }


@router.post("/videos/{video_id}/claude/{task_type}")
def apply_answer(
    user: CurrentUser, session: SessionDep, video_id: int, task_type: str, body: OfflineAnswer
) -> dict[str, Any]:
    """Valida a resposta (JSON) do Claude e salva como a tool MCP correspondente."""
    return offline_tasks.apply_result(session, video_id, task_type, user, body.answer)
