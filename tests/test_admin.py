from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.services import users


@pytest.fixture
def admin_client(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    admin = user_token[0]
    admin.is_admin = True
    session.add(admin)
    session.commit()
    client.post("/login", data={"token": user_token[1]})
    return client


def _token_from(html: str) -> str:
    return html.split('id="token-box" class="promptbox">')[1].split("<")[0]


def test_non_admin_is_forbidden(client: TestClient, user_token: tuple[User, str]) -> None:
    client.post("/login", data={"token": user_token[1]})
    r = client.get("/admin/users")
    assert r.status_code == 403 and "administradores" in r.text
    assert client.post("/admin/users", data={"name": "X", "email": "x@x.com"}).status_code == 403
    assert "Usuários" not in client.get("/").text.split("<main")[0]  # sem link no menu


def test_admin_creates_user_who_can_login(admin_client: TestClient, session: Session) -> None:
    assert "Usuários" in admin_client.get("/").text
    r = admin_client.post("/admin/users", data={"name": "Sócio", "email": "Socio@Exemplo.com"})
    token = _token_from(r.text)
    assert "não será mostrado de novo" in token or token.startswith("cd_")
    created = users.get_by_email(session, "socio@exemplo.com")
    assert created and not created.is_admin and users.authenticate_token(session, token) == created
    other = TestClient(admin_client.app)
    assert other.post("/login", data={"token": token}, follow_redirects=False).status_code == 303
    assert other.get("/admin/users").status_code == 403  # sócio não é admin


def test_validation_and_duplicates(admin_client: TestClient) -> None:
    assert (
        admin_client.post("/admin/users", data={"name": "Ana", "email": "sem-arroba"}).status_code
        == 422
    )
    admin_client.post("/admin/users", data={"name": "Ana", "email": "ana@x.com"})
    dup = admin_client.post("/admin/users", data={"name": "Ana 2", "email": "ANA@x.com"})
    assert dup.status_code == 422 and "Já existe" in dup.text


def test_rotate_deactivate_reactivate(admin_client: TestClient, session: Session) -> None:
    old = _token_from(
        admin_client.post("/admin/users", data={"name": "Bia", "email": "bia@x.com"}).text
    )
    bia = users.get_by_email(session, "bia@x.com")
    assert bia and bia.id
    new = _token_from(admin_client.post(f"/admin/users/{bia.id}/token").text)
    assert new != old and users.authenticate_token(session, old) is None
    assert (
        "desativado"
        in admin_client.post(f"/admin/users/{bia.id}/active", data={"active": "0"}).text
    )
    session.refresh(bia)
    assert not bia.is_active and users.authenticate_token(session, new) is None
    assert "Reative" in admin_client.post(f"/admin/users/{bia.id}/token").text
    assert (
        "reativado" in admin_client.post(f"/admin/users/{bia.id}/active", data={"active": "1"}).text
    )


def test_guards_last_admin_and_self(
    admin_client: TestClient, session: Session, user_token: tuple[User, str]
) -> None:
    me = user_token[0]
    assert (
        "a si mesmo" in admin_client.post(f"/admin/users/{me.id}/active", data={"active": "0"}).text
    )
    assert (
        "ao menos um administrador"
        in admin_client.post(f"/admin/users/{me.id}/admin", data={"make_admin": "0"}).text
    )
    admin_client.post("/admin/users", data={"name": "Co", "email": "co@x.com", "is_admin": "on"})
    co = users.get_by_email(session, "co@x.com")
    assert co and co.is_admin
    admin_client.post(f"/admin/users/{me.id}/admin", data={"make_admin": "0"})
    session.refresh(me)
    assert not me.is_admin  # com outro admin ativo, pode se rebaixar


def test_deactivated_session_is_kicked_out(admin_client: TestClient, session: Session) -> None:
    token = _token_from(
        admin_client.post("/admin/users", data={"name": "Dan", "email": "dan@x.com"}).text
    )
    other = TestClient(admin_client.app)
    other.post("/login", data={"token": token})
    assert other.get("/", follow_redirects=False).status_code == 200
    dan = users.get_by_email(session, "dan@x.com")
    assert dan and dan.id
    admin_client.post(f"/admin/users/{dan.id}/active", data={"active": "0"})
    assert other.get("/", follow_redirects=False).status_code == 303


def test_author_labels(session: Session, user_token: tuple[User, str]) -> None:
    uid = user_token[0].id
    assert users.author_label(session, f"claude:{uid}") == "Claude (via Teste)"
    assert users.author_label(session, f"user:{uid}") == "Teste"
    assert users.author_label(session, "import:999") == "Importado por usuário 999"
    assert users.author_label(session, "algo") == "algo"
