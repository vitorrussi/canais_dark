from datetime import timedelta

from sqlalchemy import update
from sqlmodel import Session, col, or_, select

from canaisdark.models import ClaudeTask, TaskStatus, TaskType, User, Video
from canaisdark.models.channel import utcnow
from canaisdark.services.errors import Conflict, InvalidState, NotFound

CLAIM_TTL = timedelta(minutes=30)
OPEN_STATUSES = (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value)


def create_task(
    session: Session,
    video_id: int,
    task_type: TaskType,
    instructions: str = "",
    actor: User | None = None,
) -> ClaudeTask:
    if session.get(Video, video_id) is None:
        raise NotFound(f"Vídeo {video_id} não encontrado")
    existing = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video_id,
            ClaudeTask.type == task_type.value,
            col(ClaudeTask.status).in_(OPEN_STATUSES),
        )
    ).first()
    if existing:
        raise Conflict(f"Já existe uma tarefa '{task_type.value}' aberta para o vídeo {video_id}")
    task = ClaudeTask(
        type=task_type.value,
        video_id=video_id,
        instructions=instructions,
        created_by=actor.id if actor else None,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: int) -> ClaudeTask:
    task = session.get(ClaudeTask, task_id)
    if task is None:
        raise NotFound(f"Tarefa {task_id} não encontrada")
    return task


def list_tasks(
    session: Session,
    status: str | None = None,
    video_id: int | None = None,
    open_only: bool = False,
) -> list[ClaudeTask]:
    stmt = select(ClaudeTask).order_by(col(ClaudeTask.id))
    if status:
        stmt = stmt.where(ClaudeTask.status == status)
    if open_only:
        stmt = stmt.where(col(ClaudeTask.status).in_(OPEN_STATUSES))
    if video_id is not None:
        stmt = stmt.where(ClaudeTask.video_id == video_id)
    return list(session.exec(stmt))


def is_claim_expired(task: ClaudeTask) -> bool:
    if task.claimed_at is None:
        return True
    claimed = task.claimed_at.replace(tzinfo=None)
    return claimed < utcnow().replace(tzinfo=None) - CLAIM_TTL


def claim_next(session: Session, actor: User, task_type: str | None = None) -> ClaudeTask | None:
    """Reserva atomicamente a próxima tarefa (pendente ou com reserva expirada)."""
    assert actor.id is not None
    for _ in range(5):
        now = utcnow()
        cutoff = now - CLAIM_TTL
        available = or_(
            ClaudeTask.status == TaskStatus.PENDING.value,
            (ClaudeTask.status == TaskStatus.IN_PROGRESS.value)
            & (col(ClaudeTask.claimed_at) < cutoff),
        )
        stmt = select(ClaudeTask.id).where(available).order_by(col(ClaudeTask.id)).limit(1)
        if task_type:
            stmt = stmt.where(ClaudeTask.type == task_type)
        task_id = session.exec(stmt).first()
        if task_id is None:
            return None
        # O UPDATE condicional é o lock: se outro reservou antes, rowcount = 0 e repetimos.
        result = session.execute(
            update(ClaudeTask)
            .where(col(ClaudeTask.id) == task_id, available)
            .values(
                status=TaskStatus.IN_PROGRESS.value,
                claimed_by=actor.id,
                claimed_at=now,
                attempts=col(ClaudeTask.attempts) + 1,
            )
        )
        session.commit()
        if result.rowcount == 1:  # type: ignore[attr-defined]
            task = session.get(ClaudeTask, task_id)
            assert task is not None
            session.refresh(task)
            return task
    return None


def _owned_open_task(session: Session, task_id: int, actor: User) -> ClaudeTask:
    task = get_task(session, task_id)
    if task.status != TaskStatus.IN_PROGRESS.value:
        raise InvalidState(f"A tarefa {task_id} está '{task.status}', não 'in_progress'")
    if task.claimed_by != actor.id and not is_claim_expired(task):
        raise Conflict(f"A tarefa {task_id} está reservada por outro usuário")
    return task


def _finish(session: Session, task: ClaudeTask, status: TaskStatus) -> ClaudeTask:
    task.status = status.value
    task.finished_at = utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def complete_task(session: Session, task_id: int, actor: User, result_summary: str) -> ClaudeTask:
    task = _owned_open_task(session, task_id, actor)
    task.result_summary = result_summary
    return _finish(session, task, TaskStatus.DONE)


def fail_task(session: Session, task_id: int, actor: User, reason: str) -> ClaudeTask:
    task = _owned_open_task(session, task_id, actor)
    task.error = reason
    return _finish(session, task, TaskStatus.FAILED)


def release_task(session: Session, task_id: int, actor: User) -> ClaudeTask:
    """Devolve a tarefa à fila (ex.: o Claude não conseguiu continuar)."""
    task = _owned_open_task(session, task_id, actor)
    task.status = TaskStatus.PENDING.value
    task.claimed_by = None
    task.claimed_at = None
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def cancel_task(session: Session, task_id: int) -> ClaudeTask:
    task = get_task(session, task_id)
    if task.status not in OPEN_STATUSES:
        raise InvalidState(f"A tarefa {task_id} já está '{task.status}'")
    return _finish(session, task, TaskStatus.CANCELLED)
