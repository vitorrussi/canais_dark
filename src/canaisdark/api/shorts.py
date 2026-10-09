from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import Session

from canaisdark.api.deps import CurrentUser
from canaisdark.db import get_session
from canaisdark.schemas.job import JobRead
from canaisdark.schemas.shorts import SaveShortsRequest
from canaisdark.services import shorts, shorts_render, videos

router = APIRouter(tags=["shorts"])
SessionDep = Annotated[Session, Depends(get_session)]


class ShortsRenderRequest(BaseModel):
    quality: str = "preview"
    index: int | None = None
    allow_missing: bool = False


class SkipRequest(BaseModel):
    skipped: bool = True


@router.get("/videos/{video_id}/shorts")
def get_shorts(_: CurrentUser, session: SessionDep, video_id: int) -> dict[str, Any]:
    videos.get_video(session, video_id)
    found = shorts.latest_plan(session, video_id)
    rendered = shorts_render.best_for(session, video_id)
    plan = found[1] if found else None
    return {
        "saved_version": found[0].meta.get("number") if found else None,
        "skipped": bool(plan and plan.skipped),
        "timing": plan.timing if plan else None,
        "shorts": [
            {
                **sel.model_dump(),
                "index": i,
                "duration": round(sel.duration, 2),
                "render": (
                    {"asset_id": r.id, "file_url": f"/assets/{r.id}/file", **r.meta}
                    if (r := rendered.get(i))
                    else None
                ),
            }
            for i, sel in enumerate(plan.shorts if plan else [])
        ],
    }


@router.put("/videos/{video_id}/shorts", status_code=status.HTTP_201_CREATED)
def put_shorts(
    user: CurrentUser, session: SessionDep, video_id: int, body: SaveShortsRequest
) -> dict[str, Any]:
    asset, plan = shorts.save_selections(session, video_id, body.shorts, "user", f"user:{user.id}")
    return {"version": asset.meta["number"], "plan_asset_id": asset.id, "count": len(plan.shorts)}


@router.post("/videos/{video_id}/shorts/render", status_code=status.HTTP_202_ACCEPTED)
def render_shorts(
    user: CurrentUser, session: SessionDep, video_id: int, body: ShortsRenderRequest
) -> JobRead:
    job = shorts_render.start(session, video_id, user, body.quality, body.index, body.allow_missing)
    return JobRead.model_validate(job, from_attributes=True)


@router.post("/videos/{video_id}/shorts/skip")
def skip_shorts(
    user: CurrentUser, session: SessionDep, video_id: int, body: SkipRequest
) -> dict[str, Any]:
    plan = shorts.set_skipped(session, video_id, body.skipped, f"user:{user.id}")
    return {"skipped": plan.skipped}
