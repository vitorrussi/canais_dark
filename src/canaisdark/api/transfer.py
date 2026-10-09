from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.script import ScriptVersionRead
from canaisdark.schemas.transfer import ChannelBundle, VideoBundle
from canaisdark.schemas.video import VideoRead
from canaisdark.services import channels, script_formats, scripts, transfer, videos

router = APIRouter(tags=["transfer"])
SessionDep = Annotated[Session, Depends(get_session)]


class TextImport(BaseModel):
    text: str = Field(min_length=1)
    rule: str = "auto"
    max_chars: int = 400
    approve: bool = False


@router.get("/scripts/{version_id}/download")
def download_script(
    _: CurrentUser, session: SessionDep, version_id: int, format: str = "txt"
) -> Response:
    version = scripts.get_version(session, version_id)
    video = videos.get_video(session, version.video_id)
    channel = channels.get_channel(session, video.channel_id)
    d = script_formats.render_script(
        format,
        script_formats.default_title(video),
        version,
        scripts.scenes_of(session, version),
        channel,
        video,
    )
    return Response(
        d.content,
        media_type=d.mime,
        headers={"Content-Disposition": f'attachment; filename="{d.filename}"'},
    )


@router.post("/videos/{video_id}/scripts/import-text", status_code=201)
def import_text(
    user: CurrentUser, session: SessionDep, video_id: int, data: TextImport
) -> ScriptVersionRead:
    """Divide um texto em cenas (regra `auto|blocks|lines|headings|sentences|chars`) e salva como nova versão."""
    scenes = script_formats.text_to_scenes(data.text, data.rule, data.max_chars)
    version = scripts.save_user_script(session, video_id, scenes, user, data.approve)
    return ScriptVersionRead(
        id=version.id or 0,
        video_id=video_id,
        number=version.number,
        approved=version.approved,
        approved_at=version.approved_at,
        author=version.author,
        created_at=version.created_at,
        scene_count=len(scenes),
    )


@router.get("/videos/{video_id}/export")
def export_video(_: CurrentUser, session: SessionDep, video_id: int) -> VideoBundle:
    return transfer.export_video(session, video_id)


@router.get("/channels/{channel_id}/export")
def export_channel(
    _: CurrentUser, session: SessionDep, channel_id: int, include_videos: bool = True
) -> ChannelBundle:
    return transfer.export_channel(session, channel_id, include_videos)


@router.post("/channels/{channel_id}/import-video", status_code=201)
def import_video(
    user: CurrentUser,
    session: SessionDep,
    channel_id: int,
    bundle: VideoBundle,
    title: str | None = None,
) -> VideoRead:
    video = transfer.import_video(session, channel_id, bundle, user, title)
    return VideoRead.model_validate(video, from_attributes=True)


@router.post("/import/channel", status_code=201)
def import_channel(
    user: CurrentUser,
    session: SessionDep,
    bundle: ChannelBundle,
    name: str | None = None,
    include_videos: bool = True,
) -> dict[str, object]:
    channel = transfer.import_channel(session, bundle, user, name, include_videos)
    return {"id": channel.id, "name": channel.name, "slug": channel.slug}
