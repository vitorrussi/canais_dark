"""Dados do painel inicial: o que precisa de atenção e atividade recente."""

from dataclasses import dataclass

from sqlmodel import Session, col, not_, select

from canaisdark.models import Channel, ClaudeTask, ScriptVersion, TaskStatus, TaskType, Video


@dataclass
class AttentionItem:
    video: Video
    channel: Channel
    reason: str  # approve_script | task_failed
    label: str


def _channel_map(session: Session) -> dict[int, Channel]:
    active = select(Channel).where(not_(col(Channel.is_archived)))
    return {c.id: c for c in session.exec(active) if c.id}


def attention(session: Session) -> list[AttentionItem]:
    chans = _channel_map(session)
    items: list[AttentionItem] = []
    for video in session.exec(select(Video).order_by(col(Video.id).desc())):
        channel = chans.get(video.channel_id)
        if channel is None or video.id is None:
            continue
        tasks = list(
            session.exec(
                select(ClaudeTask)
                .where(
                    ClaudeTask.video_id == video.id,
                    ClaudeTask.type == TaskType.GENERATE_SCRIPT.value,
                )
                .order_by(col(ClaudeTask.id).desc())
            )
        )
        has_open = any(
            t.status in (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value) for t in tasks
        )
        if has_open:
            continue
        if tasks and tasks[0].status == TaskStatus.FAILED.value:
            items.append(
                AttentionItem(
                    video,
                    channel,
                    "task_failed",
                    f"Tarefa de roteiro falhou: {tasks[0].error[:80]}",
                )
            )
            continue
        has_version = session.exec(
            select(ScriptVersion.id).where(ScriptVersion.video_id == video.id)
        ).first()
        if has_version and video.approved_script_version_id is None:
            items.append(
                AttentionItem(
                    video, channel, "approve_script", "Roteiro pronto para revisar e aprovar"
                )
            )
    return items


def recent_videos(session: Session, limit: int = 6) -> list[tuple[Video, Channel]]:
    chans = _channel_map(session)
    rows = session.exec(select(Video).order_by(col(Video.updated_at).desc()).limit(limit * 3))
    return [(v, chans[v.channel_id]) for v in rows if v.channel_id in chans][:limit]
