from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.job import JobRead, NarrationPlanRead, NarrationRequest
from canaisdark.services import credits, jobs, narration

router = APIRouter(tags=["jobs"])
SessionDep = Annotated[Session, Depends(get_session)]


def _read(job: object) -> JobRead:
    return JobRead.model_validate(job, from_attributes=True)


@router.get("/jobs")
def list_jobs(
    _: CurrentUser, session: SessionDep, video_id: int | None = None, status: str | None = None
) -> list[JobRead]:
    return [_read(j) for j in jobs.list_jobs(session, video_id, status)]


@router.get("/jobs/{job_id}")
def get_job(_: CurrentUser, session: SessionDep, job_id: int) -> JobRead:
    return _read(jobs.get_job(session, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(_: CurrentUser, session: SessionDep, job_id: int) -> JobRead:
    return _read(jobs.cancel(session, job_id))


@router.post("/jobs/{job_id}/retry", status_code=201)
def retry_job(user: CurrentUser, session: SessionDep, job_id: int) -> JobRead:
    return _read(jobs.retry(session, job_id, user))


@router.get("/videos/{video_id}/narration/plan")
def narration_plan(
    _: CurrentUser,
    session: SessionDep,
    video_id: int,
    test_scenes: int | None = None,
    align: bool | None = None,
) -> NarrationPlanRead:
    """Prévia (sem gastar nada): caracteres, créditos estimados, bloqueios."""
    p = narration.plan(session, video_id, test_scenes, align, credits.get_balance())
    return NarrationPlanRead(
        can_start=p.can_start,
        problems=p.problems,
        scenes_total=p.scenes_total,
        scenes_used=len(p.scene_texts),
        chars=p.chars,
        credits_estimated=p.credits_estimated,
        chunks=len(p.chunks),
        model_id=p.model_id,
        will_align=p.will_align,
        partial=p.partial,
        max_chars=p.max_chars,
        balance_remaining=p.balance.remaining if p.balance else None,
    )


@router.post("/videos/{video_id}/narration", status_code=201)
def start_narration(
    user: CurrentUser, session: SessionDep, video_id: int, data: NarrationRequest
) -> JobRead:
    """Enfileira a narração (GASTA créditos). Exige roteiro aprovado."""
    job = narration.start(
        session, video_id, user, data.test_scenes, data.align, credits.get_balance(force=True)
    )
    return _read(job)
