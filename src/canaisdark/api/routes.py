from fastapi import APIRouter

from canaisdark.api import (
    channels,
    drive,
    jobs,
    metadata,
    offline,
    render,
    scenes,
    scripts,
    shorts,
    tasks,
    transfer,
    videos,
    youtube,
)
from canaisdark.api.deps import CurrentUser

router = APIRouter(prefix="/api")


@router.get("/me")
def me(user: CurrentUser) -> dict[str, object]:
    return {"id": user.id, "name": user.name, "email": user.email}


router.include_router(channels.router)
router.include_router(videos.router)
router.include_router(tasks.router)
router.include_router(scripts.router)
router.include_router(transfer.router)
router.include_router(jobs.router)
router.include_router(scenes.router)
router.include_router(render.router)
router.include_router(metadata.router)
router.include_router(shorts.router)
router.include_router(youtube.router)
router.include_router(drive.router)
router.include_router(offline.router)
