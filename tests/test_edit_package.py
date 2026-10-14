import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.mcp import server
from canaisdark.mcp.context import current_user
from canaisdark.models import TaskType, User
from canaisdark.services import edit_package, render, users, videos
from canaisdark.services.errors import Forbidden, InvalidState
from tests.media import have_ffmpeg, setup_video

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg não instalado")


@pytest.fixture
def ids(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return setup_video(session, user_token[0], [2.0, 3.0], real_audio=have_ffmpeg())


def _make_mp4(path: Path, seconds: float, audio: bool = True, size: str = "1280x720") -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s={size}:r=12"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
    cmd += ["-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


def _token_of(url: str) -> str:
    return url.split("/x/")[1].split("/")[0]


@needs_ffmpeg
def test_manifest_lists_files_scenes_and_signed_links(
    session: Session, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    m = edit_package.build_manifest(
        session, ids["video_id"], user_token[0], "720p", "https://x.test"
    )
    assert m["target"]["width"] == 1280 and m["target"]["duration_s"] > 4.9
    assert len(m["scenes"]) == 2 and m["scenes"][0]["image"] == "scenes/001_main.jpg"
    names = {f["name"] for f in m["files"]}
    assert "narration/narration.mp3" in names and "scenes/002_main.jpg" in names
    assert all(
        f["url"].startswith("https://x.test/x/") and len(f["sha256"]) == 64 for f in m["files"]
    )
    assert m["upload"]["url"].endswith("/upload") and "how_to" in m
    with pytest.raises(InvalidState):
        edit_package.build_manifest(session, ids["video_id"], user_token[0], "4k")


@needs_ffmpeg
def test_signed_link_scopes_expiry_and_token_rotation(
    session: Session, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    user = user_token[0]
    video = videos.get_video(session, ids["video_id"])
    signed = edit_package.sign(video, user, "zip")
    assert edit_package.verify(session, signed).key == "zip"
    with pytest.raises(Forbidden):
        edit_package.verify(session, signed[:-2] + "xx")
    old = URLSafeTimedSerializer(get_settings().secret_key, salt="outro").dumps({"v": 1})
    with pytest.raises(Forbidden):
        edit_package.verify(session, old)
    users.issue_token(session, user)  # rotacionar invalida
    with pytest.raises(Forbidden, match="não vale mais"):
        edit_package.verify(session, signed)


@needs_ffmpeg
def test_find_file_rejects_unknown_and_traversal(session: Session, ids: dict[str, int]) -> None:
    video = videos.get_video(session, ids["video_id"])
    assert edit_package.find_file(session, video, "scenes/001_main.jpg").path.is_file()
    for bad in ("../../etc/passwd", "/etc/passwd", "scenes/999_main.jpg"):
        with pytest.raises(Exception, match="não encontrado"):
            edit_package.find_file(session, video, bad)


@needs_ffmpeg
def test_download_zip_and_files_over_http_without_session(
    client: TestClient, session: Session, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    m = edit_package.build_manifest(session, ids["video_id"], user_token[0], base_url="")
    zres = client.get(m["zip_url"])
    assert zres.status_code == 200 and zres.headers["content-type"] == "application/zip"
    zpath = Path(__file__).parent / "_tmp.zip"
    try:
        zpath.write_bytes(zres.content)
        with zipfile.ZipFile(zpath) as z:
            assert "manifest.json" in z.namelist() and "scenes/001_main.jpg" in z.namelist()
            assert json.loads(z.read("manifest.json"))["video"]["id"] == ids["video_id"]
    finally:
        zpath.unlink(missing_ok=True)
    f = next(x for x in m["files"] if x["name"] == "scenes/001_main.jpg")
    img = client.get(f["url"])
    assert img.status_code == 200 and img.content[:2] == b"\xff\xd8"
    ranged = client.get(f["url"], headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206 and len(ranged.content) == 10
    # link de upload não baixa; link inválido é 403 em JSON
    up = client.get(m["upload"]["url"].removesuffix("/upload"))
    assert up.status_code == 403 and up.json()["status"] == "error"
    assert client.get("/x/lixo").status_code == 403


@needs_ffmpeg
def test_chunked_upload_registers_final_render(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    ids: dict[str, int],
    tmp_path: Path,
) -> None:
    m = edit_package.build_manifest(session, ids["video_id"], user_token[0], base_url="")
    url = m["upload"]["url"]
    data = _make_mp4(tmp_path / "ed.mp4", 5.0).read_bytes()
    third = len(data) // 3
    parts = [data[:third], data[third : 2 * third], data[2 * third :]]
    r1 = client.post(f"{url}?part=2&total=3", content=parts[1])
    assert r1.json() == {"status": "partial", "received": [2], "missing": [1, 3]}
    assert client.post(f"{url}?part=1&total=3", content=parts[0]).json()["status"] == "partial"
    done = client.post(f"{url}?part=3&total=3", content=parts[2])
    assert done.status_code == 200, done.json()
    assert done.json()["status"] == "ok"
    session.expire_all()
    final = render.latest_render(session, ids["video_id"], "final")
    assert final is not None and final.meta["external"] is True
    assert (
        final.meta["source"] == "claude_external" and final.meta["uploaded_by"] == user_token[0].id
    )
    assert (get_settings().data_dir / final.path).is_file()
    assert not list((get_settings().data_dir).rglob("part-*"))  # staging limpo


@needs_ffmpeg
@pytest.mark.parametrize(
    ("seconds", "audio", "size", "fragment"),
    [
        (20.0, True, "1280x720", "Duração"),
        (5.0, False, "1280x720", "não tem áudio"),
        (5.0, True, "1280x1280", "16:9"),
    ],
)
def test_invalid_video_is_rejected_and_nothing_saved(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    ids: dict[str, int],
    tmp_path: Path,
    seconds: float,
    audio: bool,
    size: str,
    fragment: str,
) -> None:
    m = edit_package.build_manifest(session, ids["video_id"], user_token[0], base_url="")
    body = _make_mp4(tmp_path / "bad.mp4", seconds, audio, size).read_bytes()
    res = client.post(f"{m['upload']['url']}?part=1&total=1", content=body)
    assert res.status_code == 422 and fragment in res.json()["detail"]
    session.expire_all()
    assert render.latest_render(session, ids["video_id"], "final") is None


@needs_ffmpeg
def test_upload_rejects_bad_params_empty_body_and_non_video(
    client: TestClient, session: Session, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    m = edit_package.build_manifest(session, ids["video_id"], user_token[0], base_url="")
    url = m["upload"]["url"]
    assert client.post(f"{url}?part=3&total=2", content=b"x").status_code == 422
    assert client.post(f"{url}?part=1&total=1", content=b"").status_code == 422
    junk = client.post(f"{url}?part=1&total=1", content=b"isso nao e video" * 100)
    assert junk.status_code == 422 and "vídeo" in junk.json()["detail"]
    # link de download não serve para enviar
    dl = edit_package.build_manifest(session, ids["video_id"], user_token[0], base_url="")[
        "zip_url"
    ]
    assert client.post(f"{dl}/upload?part=1&total=1", content=b"x").status_code == 403


@needs_ffmpeg
def test_session_fallback_zip_link_and_card(
    client: TestClient, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    vid = ids["video_id"]
    assert client.get(f"/videos/{vid}/edit-package.zip", follow_redirects=False).status_code == 303
    client.post("/login", data={"token": user_token[1]})
    assert client.get(f"/videos/{vid}/edit-package.zip").status_code == 200
    link = client.get(f"/videos/{vid}/external-render/link").json()
    assert link["url"].startswith("/x/") and link["max_part_bytes"] == 90 * 1024 * 1024
    page = client.get(f"/videos/{vid}").text
    assert "Edição livre pelo Claude" in page and "external-render-form" in page
    r = client.post(f"/videos/{vid}/edit/request", data={"instructions": "faixa azul"})
    assert r.status_code in (200, 303)
    page = client.get(f"/videos/{vid}").text
    assert "claim_next_task(type=&#34;edit_video&#34;" in page or 'type="edit_video"' in page
    assert "faixa azul" in page


@pytest.mark.anyio
@needs_ffmpeg
async def test_mcp_get_edit_package_and_task_type(
    session: Session, user_token: tuple[User, str], ids: dict[str, int]
) -> None:
    current_user.set(user_token[0])
    res: Any = await server.mcp.call_tool("get_edit_package", {"video_id": ids["video_id"]})
    data = res.structured_content if hasattr(res, "structured_content") else res
    assert data["target"]["quality"] == "720p" and data["files"] and data["upload"]["url"]
    assert "edit_video" in {t.value for t in TaskType}


def test_edit_video_not_in_offline_mode() -> None:
    from canaisdark.services import offline_tasks

    assert "edit_video" not in offline_tasks.SUPPORTED


def test_user_prompt_for_edit_video_mentions_package() -> None:
    from canaisdark.services import task_context

    text = task_context.build_user_prompt(5, "tema", "Canal", "edit_video")
    assert "get_edit_package(5)" in text and "upload.url" in text
    assert "save_script" in task_context.build_user_prompt(5, "t", "C", "generate_script")
