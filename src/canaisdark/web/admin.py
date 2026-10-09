"""Administração de usuários (somente administradores)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from canaisdark.models import User
from canaisdark.services import users
from canaisdark.services.users import UserError
from canaisdark.web.common import AdminUser, SessionDep, back, render

router = APIRouter(include_in_schema=False)


def _page(
    request: Request,
    admin: User,
    session: SessionDep,
    status: int = 200,
    new_token: tuple[User, str] | None = None,
    error: str | None = None,
) -> Response:
    return render(
        request,
        "admin_users.html",
        admin,
        status=status,
        nav="admin",
        users_list=users.list_users(session),
        new_token=new_token,
        error=error,
        crumbs=[("Canais", "/")],
    )


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, admin: AdminUser, session: SessionDep) -> Response:
    return _page(request, admin, session)


@router.post("/admin/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    admin: AdminUser,
    session: SessionDep,
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    is_admin: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        user = users.create_user(session, name, email, bool(is_admin))
    except UserError as e:
        return _page(request, admin, session, status=422, error=str(e))
    return _page(request, admin, session, new_token=(user, users.issue_token(session, user)))


@router.post("/admin/users/{user_id}/token", response_class=HTMLResponse)
def rotate_token(request: Request, admin: AdminUser, session: SessionDep, user_id: int) -> Response:
    try:
        user = users.get_user(session, user_id)
        if not user.is_active:
            raise UserError("Reative o usuário antes de gerar um token")
    except UserError as e:
        return _page(request, admin, session, status=422, error=str(e))
    return _page(request, admin, session, new_token=(user, users.issue_token(session, user)))


@router.post("/admin/users/{user_id}/active")
def toggle_active(
    request: Request,
    admin: AdminUser,
    session: SessionDep,
    user_id: int,
    active: Annotated[str, Form()],
) -> Response:
    try:
        user = users.get_user(session, user_id)
        users.set_active(session, user, active == "1", admin)
    except UserError as e:
        return back(request, "/admin/users", error=e)
    return back(
        request, "/admin/users", ok=f"{user.name} {'reativado' if user.is_active else 'desativado'}"
    )


@router.post("/admin/users/{user_id}/admin")
def toggle_admin(
    request: Request,
    admin: AdminUser,
    session: SessionDep,
    user_id: int,
    make_admin: Annotated[str, Form()],
) -> Response:
    try:
        user = users.get_user(session, user_id)
        users.set_admin(session, user, make_admin == "1", admin)
    except UserError as e:
        return back(request, "/admin/users", error=e)
    return back(
        request,
        "/admin/users",
        ok=f"{user.name} agora {'é administrador' if user.is_admin else 'não é mais administrador'}",
    )
