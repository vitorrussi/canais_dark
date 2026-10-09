"""Fila de jobs no banco (o worker é um processo separado). Mesmo padrão de reserva das ClaudeTasks."""

from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, select

from canaisdark.models import Job, JobStatus, JobType, User, Video
from canaisdark.models.channel import utcnow
from canaisdark.services.errors import Conflict, InvalidState, NotFound

STALE_AFTER = timedelta(minutes=10)
OPEN = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)


def create_job(
    session: Session,
    video_id: int,
    job_type: JobType,
    params: dict[str, Any] | None = None,
    actor: User | None = None,
    parent_job_id: int | None = None,
) -> Job:
    if session.get(Video, video_id) is None:
        raise NotFound(f"Vídeo {video_id} não encontrado")
    busy = session.exec(
        select(Job).where(
            Job.video_id == video_id, Job.type == job_type.value, col(Job.status).in_(OPEN)
        )
    ).first()
    if busy:
        raise Conflict(
            f"Já existe um job '{job_type.value}' em andamento para este vídeo (#{busy.id})"
        )
    job = Job(
        type=job_type.value,
        video_id=video_id,
        params=params or {},
        created_by=actor.id if actor else None,
        parent_job_id=parent_job_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound(f"Job {job_id} não encontrado")
    return job


def list_jobs(
    session: Session, video_id: int | None = None, status: str | None = None, limit: int = 50
) -> list[Job]:
    stmt = select(Job).order_by(col(Job.id).desc()).limit(limit)
    if video_id is not None:
        stmt = stmt.where(Job.video_id == video_id)
    if status:
        stmt = stmt.where(Job.status == status)
    return list(session.exec(stmt))


def latest_job(session: Session, video_id: int, job_type: JobType) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.video_id == video_id, Job.type == job_type.value)
        .order_by(col(Job.id).desc())
    )
    return session.exec(stmt).first()


def claim_next(session: Session, worker_id: str) -> Job | None:
    """Reserva atomicamente o job `queued` mais antigo (UPDATE condicional é o lock)."""
    for _ in range(5):
        job_id = session.exec(
            select(Job.id)
            .where(Job.status == JobStatus.QUEUED.value)
            .order_by(col(Job.id))
            .limit(1)
        ).first()
        if job_id is None:
            return None
        now = utcnow()
        result = session.execute(
            update(Job)
            .where(col(Job.id) == job_id, col(Job.status) == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.RUNNING.value,
                worker_id=worker_id,
                heartbeat_at=now,
                started_at=now,
                attempts=col(Job.attempts) + 1,
            )
        )
        session.commit()
        if result.rowcount == 1:  # type: ignore[attr-defined]
            job = session.get(Job, job_id)
            assert job is not None
            session.refresh(job)
            return job
    return None


def recover_stale(session: Session, max_age: timedelta = STALE_AFTER) -> list[Job]:
    """Jobs 'running' sem heartbeat recente (worker caiu) viram 'failed' para poderem ser repetidos."""
    cutoff = utcnow() - max_age
    stale = session.exec(
        select(Job).where(Job.status == JobStatus.RUNNING.value, col(Job.heartbeat_at) < cutoff)
    ).all()
    for job in stale:
        job.status = JobStatus.FAILED.value
        job.error = "O worker parou de responder durante este job. Use “Tentar de novo” (trechos já gerados são reaproveitados)."
        job.finished_at = utcnow()
        session.add(job)
    session.commit()
    return list(stale)


def heartbeat(session: Session, job_id: int, progress: int | None = None) -> None:
    job = get_job(session, job_id)
    job.heartbeat_at = utcnow()
    if progress is not None:
        job.progress = max(0, min(100, progress))
    session.add(job)
    session.commit()


def append_log(session: Session, job_id: int, line: str) -> None:
    job = get_job(session, job_id)
    stamp = utcnow().strftime("%H:%M:%S")
    job.log = f"{job.log}[{stamp}] {line}\n"
    job.heartbeat_at = utcnow()
    session.add(job)
    session.commit()


def is_cancelled(session: Session, job_id: int) -> bool:
    session.expire_all()
    return get_job(session, job_id).status == JobStatus.CANCELLED.value


def complete(session: Session, job_id: int, result: dict[str, Any] | None = None) -> Job:
    job = get_job(session, job_id)
    if job.status == JobStatus.CANCELLED.value:
        return job
    job.status = JobStatus.DONE.value
    job.progress = 100
    job.result = result or {}
    job.finished_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def fail(session: Session, job_id: int, error: str) -> Job:
    job = get_job(session, job_id)
    if job.status == JobStatus.CANCELLED.value:
        return job
    job.status = JobStatus.FAILED.value
    job.error = error
    job.finished_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def cancel(session: Session, job_id: int) -> Job:
    job = get_job(session, job_id)
    if job.status not in OPEN:
        raise InvalidState(f"O job {job_id} já está '{job.status}'")
    job.status = JobStatus.CANCELLED.value
    job.finished_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def retry(session: Session, job_id: int, actor: User | None = None) -> Job:
    """Novo job com os mesmos parâmetros; `resume_from` permite reaproveitar trechos já pagos."""
    old = get_job(session, job_id)
    if old.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise InvalidState("Só é possível repetir jobs que falharam ou foram cancelados")
    params = {**old.params, "resume_from": old.id}
    return create_job(session, old.video_id, JobType(old.type), params, actor, parent_job_id=old.id)
