import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from canaisdark.integrations.drive import (
    DriveCancelled,
    DriveClient,
    DriveError,
    FakeDrive,
    map_error,
)


def client(transport: httpx.MockTransport, chunk: int = 4) -> DriveClient:
    return DriveClient("cid", "secret", transport=transport, sleep=lambda s: None, chunk_size=chunk)


def test_requires_credentials_and_scope_is_drive_file() -> None:
    with pytest.raises(DriveError) as e:
        DriveClient("", "")
    assert e.value.code == "config"
    url = client(httpx.MockTransport(lambda r: httpx.Response(200))).authorization_url(
        "S", "https://x/oauth/drive/callback"
    )
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert q["access_type"] == ["offline"] and "secret" not in url


def test_ensure_folder_reuses_or_creates() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            q = req.url.params["q"]
            calls.append("list")
            if "'Existe'" in q:
                return httpx.Response(200, json={"files": [{"id": "F1", "name": "Existe"}]})
            return httpx.Response(200, json={"files": []})
        body = json.loads(req.content)
        calls.append("create")
        assert body["parents"] == ["P"] and body["mimeType"].endswith("folder")
        return httpx.Response(200, json={"id": "NEW", "webViewLink": "https://d/NEW"})

    c = client(httpx.MockTransport(handler))
    assert c.ensure_folder("t", "Existe", "P").id == "F1"
    made = c.ensure_folder("t", "Novo", "P")
    assert made.id == "NEW" and made.url == "https://d/NEW"
    assert calls == ["list", "list", "create"]


def test_query_escapes_quotes() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            seen.append(req.url.params["q"])
            return httpx.Response(200, json={"files": []})
        return httpx.Response(200, json={"id": "X"})

    client(httpx.MockTransport(handler)).ensure_folder("t", "O'Brien", None)
    assert "O\\'Brien" in seen[0] and "'root' in parents" in seen[0]


def _upload_transport(log: list[str], fail_first_put: bool = False) -> httpx.MockTransport:
    state = {"received": 0, "failed": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "upload.example":
            rng = req.headers["content-range"]
            log.append(rng)
            if rng.startswith("bytes */"):
                return httpx.Response(308, headers={"Range": f"bytes=0-{state['received'] - 1}"})
            if fail_first_put and not state["failed"]:
                state["failed"] = True
                raise httpx.ConnectError("boom")
            _, rest = rng.removeprefix("bytes ").split("-")
            b, total = rest.split("/")
            state["received"] = int(b) + 1
            if int(b) + 1 >= int(total):
                return httpx.Response(200, json={"id": "FILE1", "webViewLink": "https://d/FILE1"})
            return httpx.Response(308, headers={"Range": f"bytes=0-{b}"})
        log.append(f"{req.method} {req.url.path}")
        return httpx.Response(200, headers={"Location": "https://upload.example/s"}, json={})

    return httpx.MockTransport(handler)


def test_resumable_create_and_update(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0123456789")
    log: list[str] = []
    seen: list[tuple[int, int]] = []
    out = client(_upload_transport(log)).upload_file(
        "t", f, "v.mp4", "PARENT", on_progress=lambda s, t: seen.append((s, t))
    )
    assert out.id == "FILE1" and out.url == "https://d/FILE1"
    assert log[0] == "POST /upload/drive/v3/files"
    assert log[1:] == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"] and seen[-1] == (10, 10)
    log2: list[str] = []
    client(_upload_transport(log2)).upload_file("t", f, "v.mp4", "P", existing_id="FILE1")
    assert log2[0] == "PATCH /upload/drive/v3/files/FILE1"


def test_resume_after_network_failure_and_cancel(tmp_path: Path) -> None:
    f = tmp_path / "v.bin"
    f.write_bytes(b"0123456789")
    log: list[str] = []
    out = client(_upload_transport(log, fail_first_put=True)).upload_file("t", f, "v.bin", "P")
    assert out.id == "FILE1" and "bytes */10" in log
    with pytest.raises(DriveCancelled):
        client(_upload_transport([])).upload_file("t", f, "v.bin", "P", should_cancel=lambda: True)


def test_error_mapping_and_folder_exists() -> None:
    full = httpx.Response(
        403, json={"error": {"message": "x", "errors": [{"reason": "storageQuotaExceeded"}]}}
    )
    err = map_error(full)
    assert err.code == "storage_full" and "sem espaço" in str(err) and not err.retryable
    assert map_error(httpx.Response(404, json={})).code == "not_found"
    assert map_error(httpx.Response(400, json={"error": "invalid_grant"})).code == "invalid_grant"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/GONE"):
            return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
        if req.url.path.endswith("/TRASH"):
            return httpx.Response(
                200, json={"trashed": True, "mimeType": "application/vnd.google-apps.folder"}
            )
        return httpx.Response(
            200, json={"trashed": False, "mimeType": "application/vnd.google-apps.folder"}
        )

    c = client(httpx.MockTransport(handler))
    assert c.folder_exists("t", "OK") and not c.folder_exists("t", "GONE")
    assert not c.folder_exists("t", "TRASH")


def test_fake_drive_create_update_and_missing_folder(tmp_path: Path) -> None:
    d = FakeDrive()
    root = d.ensure_folder("t", "Estúdio", None)
    assert d.ensure_folder("t", "Estúdio", None).id == root.id
    f = tmp_path / "a.txt"
    f.write_text("um")
    first = d.upload_file("t", f, "a.txt", root.id)
    f.write_text("dois")
    second = d.upload_file("t", f, "a.txt", root.id, existing_id=first.id)
    assert second.id == first.id and d.files[first.id]["bytes"] == b"dois"
    assert d.uploads == [("create", "a.txt"), ("update", "a.txt")]
    with pytest.raises(DriveError) as e:
        d.upload_file("t", f, "a.txt", "naoexiste")
    assert e.value.code == "not_found"
