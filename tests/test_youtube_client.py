import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from canaisdark.integrations.youtube import (
    FakeYouTube,
    UploadCancelled,
    YouTubeClient,
    YouTubeError,
    map_error,
)


def client(handler: httpx.MockTransport, chunk: int = 4) -> YouTubeClient:
    return YouTubeClient("cid", "secret", transport=handler, sleep=lambda s: None, chunk_size=chunk)


def test_requires_credentials() -> None:
    with pytest.raises(YouTubeError) as e:
        YouTubeClient("", "")
    assert e.value.code == "config"


def test_authorization_url_has_offline_scopes_and_state() -> None:
    url = client(httpx.MockTransport(lambda r: httpx.Response(200))).authorization_url(
        "STATE", "https://x.app/oauth/youtube/callback"
    )
    q = parse_qs(urlparse(url).query)
    assert q["access_type"] == ["offline"] and q["state"] == ["STATE"]
    assert q["redirect_uri"] == ["https://x.app/oauth/youtube/callback"]
    assert "youtube.upload" in q["scope"][0] and "youtube.readonly" in q["scope"][0]
    assert "secret" not in url


def test_exchange_and_refresh_and_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = parse_qs(req.content.decode())
        if body["grant_type"] == ["authorization_code"]:
            if body["code"] == ["nope"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if body["code"] == ["norefresh"]:
                return httpx.Response(200, json={"access_token": "a"})
            return httpx.Response(
                200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 100}
            )
        if body["refresh_token"] == ["R1"]:
            return httpx.Response(200, json={"access_token": "A2"})
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad"})

    c = client(httpx.MockTransport(handler))
    t = c.exchange_code("good", "https://x/cb")
    assert (t.access_token, t.refresh_token) == ("A1", "R1")
    assert c.refresh_access_token("R1") == "A2"
    with pytest.raises(YouTubeError) as e:
        c.exchange_code("nope", "https://x/cb")
    assert e.value.code == "invalid_grant"
    with pytest.raises(YouTubeError, match="refresh token"):
        c.exchange_code("norefresh", "https://x/cb")
    with pytest.raises(YouTubeError) as e2:
        c.refresh_access_token("old")
    assert e2.value.code == "invalid_grant" and "Reconecte" in str(e2.value)


def test_my_channel() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Meu"}}]})

    info = client(httpx.MockTransport(handler)).my_channel("tok")
    assert info and (info.id, info.title) == ("UC1", "Meu")
    empty = client(httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})))
    assert empty.my_channel("tok") is None


def _upload_handler(log: list[str], fail_first_put: bool = False) -> httpx.MockTransport:
    state = {"received": 0, "failed": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private"
            log.append("init")
            return httpx.Response(200, headers={"Location": "https://upload.example/session"})
        rng = req.headers["content-range"]
        log.append(rng)
        if rng.startswith("bytes */"):
            return httpx.Response(308, headers={"Range": f"bytes=0-{state['received'] - 1}"})
        if fail_first_put and not state["failed"]:
            state["failed"] = True
            raise httpx.ConnectError("boom")
        a, rest = rng.removeprefix("bytes ").split("-")
        b, total = rest.split("/")
        state["received"] = int(b) + 1
        if int(b) + 1 >= int(total):
            return httpx.Response(200, json={"id": "VID123"})
        return httpx.Response(308, headers={"Range": f"bytes=0-{b}"})

    return httpx.MockTransport(handler)


def test_resumable_upload_in_chunks(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")  # 10 bytes, blocos de 4
    log: list[str] = []
    seen: list[tuple[int, int]] = []
    vid = client(_upload_handler(log)).upload_video(
        "tok",
        f,
        {"title": "T"},
        {"privacyStatus": "private"},
        on_progress=lambda s, t: seen.append((s, t)),
    )
    assert vid == "VID123"
    assert log == ["init", "bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]
    assert seen[-1] == (10, 10)


def test_upload_resumes_after_network_failure(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")
    log: list[str] = []
    vid = client(_upload_handler(log, fail_first_put=True)).upload_video(
        "tok", f, {"title": "T"}, {"privacyStatus": "private"}
    )
    assert vid == "VID123"
    assert "bytes */10" in log  # perguntou onde parou antes de continuar


def test_upload_cancel_and_quota_error(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")
    with pytest.raises(UploadCancelled):
        client(_upload_handler([])).upload_video(
            "tok", f, {}, {"privacyStatus": "private"}, should_cancel=lambda: True
        )
    quota = httpx.Response(
        403, json={"error": {"message": "x", "errors": [{"reason": "quotaExceeded"}]}}
    )
    err = map_error(quota)
    assert err.code == "quota" and "meia-noite" in str(err)
    denied = client(httpx.MockTransport(lambda r: quota))
    with pytest.raises(YouTubeError) as e:
        denied.upload_video("tok", f, {}, {"privacyStatus": "private"})
    assert e.value.code == "quota"


def test_set_thumbnail_and_fake(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"] = req.url.query.decode()
        seen["ct"] = req.headers["content-type"]
        return httpx.Response(200, json={})

    client(httpx.MockTransport(handler)).set_thumbnail("tok", "VID", b"jpg", "image/jpeg")
    assert "videoId=VID" in seen["q"] and seen["ct"] == "image/jpeg"
    fake = FakeYouTube()
    f = tmp_path / "v.mp4"
    f.write_bytes(b"abc")
    assert fake.upload_video("t", f, {"title": "x"}, {"privacyStatus": "private"}) == "fakeVid0001"
    assert fake.uploads[0]["status"]["privacyStatus"] == "private"
