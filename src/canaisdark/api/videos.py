from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.models import VideoStatus
from canaisdark.schemas.video import StageRead, VideoCreate, VideoRead, VideoUpdate
from canaisdark.services import video_status, videos

router = APIRouter(tags=["videos"])
SessionDep = Annotated[Session, Depends(get_session)]


def _read(video: object) -> VideoRead:
    return VideoRead.model_validate(video, from_attributes=True)


@router.get("/channels/{channel_id}/videos")
def list_videos(
    _: CurrentUser, session: SessionDep, channel_id: int, status: VideoStatus | None = None
) -> list[VideoRead]:
    return [_read(v) for v in videos.list_videos(session, channel_id, status)]


@router.post("/channels/{channel_id}/videos", status_code=201)
def create_video(
    user: CurrentUser, session: SessionDep, channel_id: int, data: VideoCreate
) -> VideoRead:
    return _read(videos.create_video(session, channel_id, data, user))


@router.get("/videos/{video_id}")
def get_video(_: CurrentUser, session: SessionDep, video_id: int) -> VideoRead:
    return _read(videos.get_video(session, video_id))


@router.patch("/videos/{video_id}")
def update_video(
    _: CurrentUser, session: SessionDep, video_id: int, data: VideoUpdate
) -> VideoRead:
    return _read(videos.update_video(session, video_id, data))


@router.delete("/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(_: CurrentUser, session: SessionDep, video_id: int) -> Response:
    videos.delete_video(session, video_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/videos/{video_id}/stages")
def get_stages(_: CurrentUser, session: SessionDep, video_id: int) -> list[StageRead]:
    return video_status.get_stages(session, videos.get_video(session, video_id))
