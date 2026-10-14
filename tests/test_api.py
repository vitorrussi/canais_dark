from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.models import User
from canaisdark.services import users


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["version"]


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer x"}).status_code == 401


def test_me_ok(client: TestClient, user_token: tuple[User, str]) -> None:
    _, token = user_token
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "teste@example.com"


def test_me_inactive(client: TestClient, session: Session, user_token: tuple[User, str]) -> None:
    user, token = user_token
    users.deactivate_user(session, user)
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
