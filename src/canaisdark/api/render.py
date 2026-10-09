from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.job import JobRead
from canaisdark.schemas.timeline import SaveTimelineRequest
from canaisdark.services import render, timeline, videos

router = APIRouter(tags=["render"])
SessionDep = Annotated[Session, Depends(get_session)]


class RenderRequest(BaseModel):
    quality: str = "preview"
    subtitles: bool | None = None
    allow_missing: bool = False


@router.get("/videos/{video_id}/timeline")
def get_timeline(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    """Última versão salva ou, se não houver, a timeline padrão (ainda não salva)."""
    video = videos.get_video(session, video_id)
    found = timeline.latest(session, video_id)
    tl = found[1] if found else timeline.build_default(session, video)
    return {
        "saved_version": found[0].meta.get("number") if found else None,
        "timeline": tl.model_dump(mode="json"),
    }


@router.put("/videos/{video_id}/timeline", status_code=status.HTTP_201_CREATED)
def put_timeline(
    user: CurrentUser, session: SessionDep, video_id: int, body: SaveTimelineRequest
) -> dict[str, Any]:
    asset = timeline.save(session, video_id, body.timeline, "user", f"user:{user.id}")
    return {"version": asset.meta["number"], "timeline_asset_id": asset.id}


@router.post("/videos/{video_id}/render", status_code=status.HTTP_202_ACCEPTED)
def start_render(
    user: CurrentUser, session: SessionDep, video_id: int, body: RenderRequest
) -> JobRead:
    job = render.start(session, video_id, user, body.quality, body.subtitles, body.allow_missing)
    return JobRead.model_validate(job, from_attributes=True)


@router.get("/videos/{video_id}/render")
def get_render(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("preview", "final"):
        a = render.latest_render(session, video_id, kind)
        out[kind] = {"asset_id": a.id, "file_url": f"/assets/{a.id}/file", **a.meta} if a else None
    return out
