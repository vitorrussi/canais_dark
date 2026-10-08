from fastapi import APIRouter

from canaisdark.api.deps import CurrentUser

router = APIRouter(prefix="/api")


@router.get("/me")
def me(user: CurrentUser) -> dict[str, object]:
    return {"id": user.id, "name": user.name, "email": user.email}
