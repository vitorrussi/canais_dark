from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from canaisdark.config import get_settings
from canaisdark.models import User


@pytest.fixture
def auth(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return {"Authorization": f"Bearer {user_token[1]}"}


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/channels").status_code == 401
    assert client.get("/api/videos/1").status_code == 401


def test_channel_crud(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/api/channels", json={"name": "Meu Canal"}, headers=auth)
    assert r.status_code == 201 and r.json()["slug"] == "meu-canal"
    assert client.post("/api/channels", json={"name": "Meu Canal"}, headers=auth).status_code == 409
    cid = r.json()["id"]
    r = client.patch(f"/api/channels/{cid}", json={"visual_style_prompt": "noir"}, headers=auth)
    assert r.json()["visual_style_prompt"] == "noir"
    assert client.get("/api/channels/99", headers=auth).status_code == 404
    client.post(f"/api/channels/{cid}/archive", headers=auth)
    assert client.get("/api/channels", headers=auth).json() == []


def test_video_flow(client: TestClient, auth: dict[str, str]) -> None:
    cid = client.post("/api/channels", json={"name": "C"}, headers=auth).json()["id"]
    r = client.post(f"/api/channels/{cid}/videos", json={"topic": "Tema"}, headers=auth)
    assert r.status_code == 201
    vid = r.json()["id"]
    assert r.json()["status"] == "draft"
    stages = client.get(f"/api/videos/{vid}/stages", headers=auth).json()
    assert len(stages) == 13 and stages[0]["key"] == "script"
    assert len(client.get(f"/api/channels/{cid}/videos?status=draft", headers=auth).json()) == 1
    r = client.patch(f"/api/videos/{vid}", json={"title": "T"}, headers=auth)
    assert r.json()["title"] == "T"
    assert client.delete(f"/api/videos/{vid}", headers=auth).status_code == 204
    assert client.get(f"/api/videos/{vid}", headers=auth).status_code == 404


def test_validation(client: TestClient, auth: dict[str, str]) -> None:
    assert client.post("/api/channels", json={"name": ""}, headers=auth).status_code == 422
