import re
import unicodedata

from sqlalchemy import func
from sqlmodel import Session, col, select

from canaisdark.models import Channel, ClaudeTask, Video
from canaisdark.models.channel import utcnow
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.services.errors import Conflict, NotFound


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def create_channel(session: Session, data: ChannelCreate) -> Channel:
    slug = slugify(data.name)
    if not slug:
        raise Conflict("O nome precisa ter ao menos uma letra ou número")
    if session.exec(select(Channel).where(Channel.slug == slug)).first():
        raise Conflict(f"Já existe um canal com o slug '{slug}'")
    channel = Channel(slug=slug, **data.model_dump())
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def get_channel(session: Session, channel_id: int) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFound(f"Canal {channel_id} não encontrado")
    return channel


def get_channel_by_slug(session: Session, slug: str) -> Channel:
    channel = session.exec(select(Channel).where(Channel.slug == slug)).first()
    if channel is None:
        raise NotFound(f"Canal '{slug}' não encontrado")
    return channel


def list_channels(session: Session, include_archived: bool = False) -> list[Channel]:
    stmt = select(Channel).order_by(Channel.name)
    if not include_archived:
        stmt = stmt.where(Channel.is_archived == False)  # noqa: E712
    return list(session.exec(stmt))


def update_channel(session: Session, channel_id: int, data: ChannelUpdate) -> Channel:
    channel = get_channel(session, channel_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(channel, key, value)
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def archive_channel(session: Session, channel_id: int) -> Channel:
    channel = get_channel(session, channel_id)
    channel.is_archived = True
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def stats_by_channel(session: Session) -> dict[int, dict[str, int]]:
    """Nº de vídeos e de tarefas abertas do Claude por canal (para os cards da home)."""
    stats: dict[int, dict[str, int]] = {}
    rows = session.exec(select(Video.channel_id, func.count()).group_by(col(Video.channel_id)))
    for channel_id, count in rows:
        stats.setdefault(channel_id, {"videos": 0, "open_tasks": 0})["videos"] = count
    open_rows = session.exec(
        select(Video.channel_id, func.count())
        .select_from(ClaudeTask)
        .join(Video, col(Video.id) == col(ClaudeTask.video_id))
        .where(col(ClaudeTask.status).in_(("pending", "in_progress")))
        .group_by(col(Video.channel_id))
    )
    for channel_id, count in open_rows:
        stats.setdefault(channel_id, {"videos": 0, "open_tasks": 0})["open_tasks"] = count
    return stats


def unarchive_channel(session: Session, channel_id: int) -> Channel:
    channel = get_channel(session, channel_id)
    channel.is_archived = False
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel
