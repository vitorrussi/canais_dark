from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.models import User
from canaisdark.schemas.script import SceneIn
from canaisdark.services import scripts, tasks


@pytest.fixture
def auth(
    client: TestClient,
    user_token: tuple[User, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return {"Authorization": f"Bearer {user_token[1]}"}


def test_task_and_script_flow(
    client: TestClient, auth: dict[str, str], session: Session, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    cid = client.post("/api/channels", json={"name": "C"}, headers=auth).json()["id"]
    vid = client.post(f"/api/channels/{cid}/videos", json={"topic": "t"}, headers=auth).json()["id"]

    pending = client.get("/api/tasks?open_only=true", headers=auth).json()
    assert len(pending) == 1 and pending[0]["type"] == "generate_script"
    assert client.post(f"/api/videos/{vid}/tasks", json={}, headers=auth).status_code == 409

    task = tasks.claim_next(session, user)
    assert task and task.id
    version = scripts.save_script(session, vid, [SceneIn(text="a"), SceneIn(text="b")], user)
    tasks.complete_task(session, task.id, user, "ok")

    listed = client.get(f"/api/videos/{vid}/scripts", headers=auth).json()
    assert listed[0]["scene_count"] == 2 and listed[0]["scenes"] is None
    detail = client.get(f"/api/scripts/{version.id}", headers=auth).json()
    assert [s["text"] for s in detail["scenes"]] == ["a", "b"]

    approved = client.post(f"/api/scripts/{version.id}/approve", headers=auth).json()
    assert approved["approved"] is True
    stages = client.get(f"/api/videos/{vid}/stages", headers=auth).json()
    assert {s["key"]: s["state"] for s in stages}["script_approval"] == "done"

    assert (
        client.post(
            f"/api/videos/{vid}/script-revision", json={"notes": ""}, headers=auth
        ).status_code
        == 422
    )
    r = client.post(
        f"/api/videos/{vid}/script-revision", json={"notes": "mais curto"}, headers=auth
    )
    assert r.status_code == 201 and r.json()["instructions"] == "mais curto"

    cancelled = client.post(f"/api/tasks/{r.json()['id']}/cancel", headers=auth).json()
    assert cancelled["status"] == "cancelled"


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/scripts/1/approve").status_code == 401


def test_rest_save_user_script(client: TestClient, auth: dict[str, str]) -> None:
    cid = client.post("/api/channels", json={"name": "C"}, headers=auth).json()["id"]
    vid = client.post(f"/api/channels/{cid}/videos", json={"topic": "t"}, headers=auth).json()["id"]
    r = client.post(
        f"/api/videos/{vid}/scripts",
        json={"scenes": [{"text": "a"}, {"text": "b", "notes": "n"}], "approve": True},
        headers=auth,
    )
    assert r.status_code == 201
    body = r.json()
    assert (
        body["author"].startswith("user:")
        and body["approved"]
        and [s["text"] for s in body["scenes"]] == ["a", "b"]
    )
    assert (
        client.post(f"/api/videos/{vid}/scripts", json={"scenes": []}, headers=auth).status_code
        == 422
    )


def test_rest_transfer(client: TestClient, auth: dict[str, str]) -> None:
    cid = client.post("/api/channels", json={"name": "C"}, headers=auth).json()["id"]
    vid = client.post(f"/api/channels/{cid}/videos", json={"topic": "t"}, headers=auth).json()["id"]
    r = client.post(
        f"/api/videos/{vid}/scripts/import-text",
        json={"text": "A\n\nB", "rule": "blocks"},
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["scene_count"] == 2
    d = client.get(f"/api/scripts/{r.json()['id']}/download?format=md", headers=auth)
    assert d.status_code == 200 and "## Cena 1" in d.text
    exported = client.get(f"/api/videos/{vid}/export", headers=auth).json()
    assert exported["format"] == "canaisdark.video" and len(exported["scripts"]) == 1
    imp = client.post(f"/api/channels/{cid}/import-video?title=Copia", json=exported, headers=auth)
    assert imp.status_code == 201 and imp.json()["title"] == "Copia"
    chan = client.get(f"/api/channels/{cid}/export", headers=auth).json()
    assert len(chan["videos"]) == 2
    new = client.post("/api/import/channel?name=Novo", json=chan, headers=auth)
    assert new.status_code == 201 and new.json()["slug"] == "novo"
    assert client.post("/api/import/channel?name=Novo", json=chan, headers=auth).status_code == 409


def test_rest_jobs_and_narration(
    client: TestClient,
    auth: dict[str, str],
    session: Session,
    user_token: tuple[User, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from canaisdark.config import get_settings
    from canaisdark.integrations.elevenlabs import FakeElevenLabs
    from canaisdark.services import credits

    settings = get_settings()
    monkeypatch.setattr(settings, "elevenlabs_api_key", "KEY")
    credits.set_client_factory(lambda: FakeElevenLabs(credits=5000))
    try:
        cid = client.post(
            "/api/channels", json={"name": "C", "elevenlabs_voice_id": "v"}, headers=auth
        ).json()["id"]
        vid = client.post(f"/api/channels/{cid}/videos", json={"topic": "t"}, headers=auth).json()[
            "id"
        ]
        plan = client.get(f"/api/videos/{vid}/narration/plan?test_scenes=1", headers=auth).json()
        assert plan["can_start"] is False and "aprovado" in plan["problems"][0]
        assert (
            client.post(
                f"/api/videos/{vid}/narration", json={"test_scenes": 1}, headers=auth
            ).status_code
            == 422
        )
        client.post(
            f"/api/videos/{vid}/scripts",
            json={"scenes": [{"text": "Olá mundo."}, {"text": "Segunda."}], "approve": True},
            headers=auth,
        )
        plan = client.get(f"/api/videos/{vid}/narration/plan?test_scenes=1", headers=auth).json()
        assert (
            plan["can_start"]
            and plan["partial"]
            and plan["scenes_used"] == 1
            and plan["balance_remaining"] == 5000
        )
        r = client.post(
            f"/api/videos/{vid}/narration", json={"test_scenes": 1, "align": False}, headers=auth
        )
        assert (
            r.status_code == 201
            and r.json()["status"] == "queued"
            and r.json()["params"]["align"] is False
        )
        jid = r.json()["id"]
        assert client.get(f"/api/jobs?video_id={vid}", headers=auth).json()[0]["id"] == jid
        assert client.post(f"/api/jobs/{jid}/cancel", headers=auth).json()["status"] == "cancelled"
        assert client.post(f"/api/jobs/{jid}/retry", headers=auth).status_code == 201
        assert client.get("/api/jobs/999", headers=auth).status_code == 404
    finally:
        credits.set_client_factory(None)


def test_rest_scene_prompts_and_uploads(client: TestClient, auth: dict[str, str]) -> None:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), "teal").save(buf, "PNG")
    cid = client.post(
        "/api/channels", json={"name": "C", "visual_style_prompt": "noir"}, headers=auth
    ).json()["id"]
    vid = client.post(f"/api/channels/{cid}/videos", json={"topic": "t"}, headers=auth).json()["id"]
    assert (
        client.get(f"/api/videos/{vid}/scenes", headers=auth).status_code == 422
    )  # roteiro não aprovado
    client.post(
        f"/api/videos/{vid}/scripts",
        json={"scenes": [{"text": "A"}, {"text": "B"}], "approve": True},
        headers=auth,
    )
    r = client.put(
        f"/api/videos/{vid}/scene-prompts",
        json={"prompts": [{"scene_index": 0, "image_prompt": "a door"}]},
        headers=auth,
    )
    assert r.json() == {"saved": [0], "missing": [1]}
    assert (
        client.put(
            f"/api/videos/{vid}/scene-prompts",
            json={"prompts": [{"scene_index": 7, "image_prompt": "x"}]},
            headers=auth,
        ).status_code
        == 422
    )
    up = client.post(
        f"/api/videos/{vid}/scenes/0/upload",
        files={"file": ("a.png", buf.getvalue(), "image/png")},
        headers=auth,
    )
    assert (
        up.status_code == 201
        and up.json()["asset"]["width"] == 1920
        and up.json()["replaced"] is False
    )
    scenes = client.get(f"/api/videos/{vid}/scenes", headers=auth).json()
    assert (
        scenes[0]["image_prompt_full"] == "noir, a door, wide 16:9 landscape composition"
        and "main" in scenes[0]["assets"]
        and scenes[1]["assets"] == {}
    )
    assert (
        client.post(
            f"/api/videos/{vid}/scenes/1/upload",
            files={"file": ("x.png", b"nope", "image/png")},
            headers=auth,
        ).status_code
        == 422
    )
    assert (
        "[Cena 1] noir, a door"
        in client.get(f"/api/videos/{vid}/prompts?format=txt", headers=auth).text
    )
    assert (
        client.post(
            f"/api/videos/{vid}/prompts/request", json={"instructions": "x"}, headers=auth
        ).status_code
        == 201
    )
    assert (
        client.delete(
            f"/api/videos/{vid}/assets/{up.json()['asset']['id']}", headers=auth
        ).status_code
        == 204
    )
