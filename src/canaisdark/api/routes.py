from fastapi import APIRouter

from canaisdark.api import channels, scripts, tasks, videos
from canaisdark.api.deps import CurrentUser

router = APIRouter(prefix="/api")


@router.get("/me")
def me(user: CurrentUser) -> dict[str, object]:
    return {"id": user.id, "name": user.name, "email": user.email}


router.include_router(channels.router)
router.include_router(videos.router)
router.include_router(tasks.router)
router.include_router(scripts.router)
