import shutil

from sqlmodel import Session, col, select

from canaisdark.models import User, Video, VideoStatus
from canaisdark.models.channel import utcnow
from canaisdark.schemas.video import VideoCreate, VideoUpdate
from canaisdark.services import channels, storage
from canaisdark.services.errors import InvalidState, NotFound


def create_video(session: Session, channel_id: int, data: VideoCreate, actor: User) -> Video:
    channel = channels.get_channel(session, channel_id)
    if channel.is_archived:
        raise InvalidState("Canal arquivado não aceita novos vídeos")
    assert actor.id is not None
    video = Video(
        channel_id=channel_id,
        topic=data.topic,
        language=data.language or channel.language,
        created_by=actor.id,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    assert video.id is not None
    storage.video_dir(channel.slug, video.id, create=True)
    return video


def get_video(session: Session, video_id: int) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise NotFound(f"Vídeo {video_id} não encontrado")
    return video


def list_videos(
    session: Session, channel_id: int, status: VideoStatus | None = None
) -> list[Video]:
    stmt = select(Video).where(Video.channel_id == channel_id).order_by(col(Video.id).desc())
    if status is not None:
        stmt = stmt.where(Video.status == status)
    return list(session.exec(stmt))


def update_video(session: Session, video_id: int, data: VideoUpdate) -> Video:
    video = get_video(session, video_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(video, key, value)
    video.updated_at = utcnow()
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


def delete_video(session: Session, video_id: int) -> None:
    video = get_video(session, video_id)
    if video.status != VideoStatus.DRAFT:
        raise InvalidState("Só é possível apagar vídeos em rascunho")
    channel = channels.get_channel(session, video.channel_id)
    session.delete(video)
    session.commit()
    shutil.rmtree(storage.video_dir(channel.slug, video_id), ignore_errors=True)
