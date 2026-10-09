"""Cliente do Google Drive (OAuth drive.file, pastas e upload resumable) + FAKE para testes/demonstração.

Nunca loga tokens nem o client secret. Mensagens de erro já em português para exibir no site.
"""

import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from canaisdark.integrations.youtube import AUTH_URL, REVOKE_URL, TOKEN_URL, OAuthTokens

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
SCOPES = ("https://www.googleapis.com/auth/drive.file",)
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024  # múltiplo de 256 KiB
MAX_CHUNK_RETRIES = 5
FILE_FIELDS = "id,name,webViewLink,md5Checksum,size"


class DriveError(Exception):
    """`code`: storage_full | rate | not_found | forbidden | invalid_grant | config | network | other."""

    def __init__(
        self, message: str, code: str = "other", status: int = 0, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


class DriveCancelled(Exception):
    pass


@dataclass
class DriveFile:
    id: str
    url: str = ""
    name: str = ""
    md5: str = ""


class DriveLike(Protocol):
    """Interface implementada por `DriveClient` e `FakeDrive`."""

    def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...

    def refresh_access_token(self, refresh_token: str) -> str: ...

    def revoke(self, token: str) -> None: ...

    def account_email(self, access_token: str) -> str: ...

    def ensure_folder(self, access_token: str, name: str, parent_id: str | None) -> DriveFile: ...

    def folder_exists(self, access_token: str, folder_id: str) -> bool: ...

    def upload_file(
        self,
        access_token: str,
        path: Path,
        name: str,
        parent_id: str,
        existing_id: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DriveFile: ...


_REASONS: dict[str, tuple[str, str, bool]] = {
    "storageQuotaExceeded": (
        "storage_full",
        "O Google Drive está sem espaço. Libere espaço (ou aumente o plano) e tente de novo.",
        False,
    ),
    "rateLimitExceeded": ("rate", "Muitas requisições ao Drive. Aguarde um pouco.", True),
    "userRateLimitExceeded": ("rate", "Muitas requisições ao Drive. Aguarde um pouco.", True),
    "notFound": ("not_found", "O arquivo ou a pasta não existe mais no Drive.", False),
    "forbidden": (
        "forbidden",
        "O Drive recusou a operação (permissão). Reconecte o canal ao Drive.",
        False,
    ),
    "insufficientFilePermissions": (
        "forbidden",
        "Sem permissão para alterar este arquivo no Drive. Reconecte o canal ao Drive.",
        False,
    ),
    "authError": ("invalid_grant", "A autorização do Drive expirou. Reconecte o canal.", False),
}


def map_error(resp: httpx.Response) -> DriveError:
    reason, detail = "", ""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        errors = err.get("errors") or [{}]
        reason = str(errors[0].get("reason", ""))
        detail = str(err.get("message", ""))
    elif isinstance(err, str):
        reason = err
        detail = str(body.get("error_description", ""))
    if reason == "invalid_grant":
        return DriveError(
            "A autorização do Drive foi revogada ou expirou. Reconecte o canal.",
            "invalid_grant",
            resp.status_code,
        )
    if reason in _REASONS:
        code, msg, retry = _REASONS[reason]
        return DriveError(msg, code, resp.status_code, retry)
    if resp.status_code == 401:
        return DriveError(_REASONS["authError"][1], "invalid_grant", 401)
    if resp.status_code == 404:
        return DriveError(_REASONS["notFound"][1], "not_found", 404)
    if resp.status_code >= 500 or resp.status_code == 429:
        return DriveError(
            f"O Google Drive está instável (HTTP {resp.status_code}).",
            "network",
            resp.status_code,
            retryable=True,
        )
    return DriveError(
        f"O Drive recusou o pedido (HTTP {resp.status_code}). {detail}".strip(),
        "other",
        resp.status_code,
    )


def _range_end(header: str | None, default: int) -> int:
    if not header or "-" not in header:
        return default
    try:
        return int(header.rsplit("-", 1)[1]) + 1
    except ValueError:
        return default


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
        chunk_size: int = CHUNK,
    ) -> None:
        if not client_id or not client_secret:
            raise DriveError(
                "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET não estão configurados no .env.", "config"
            )
        self._id, self._secret = client_id, client_secret
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._sleep = sleep
        self._chunk = chunk_size

    # --- OAuth ---
    def authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self._id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent select_account",
                "state": state,
            }
        )
        return f"{AUTH_URL}?{query}"

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._http.post(
                TOKEN_URL, data={**data, "client_id": self._id, "client_secret": self._secret}
            )
        except httpx.HTTPError as e:
            raise DriveError(
                "Falha de rede ao falar com o Google.", "network", retryable=True
            ) from e
        if resp.status_code >= 400:
            raise map_error(resp)
        out: dict[str, Any] = resp.json()
        return out

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        data = self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        )
        if not data.get("refresh_token"):
            raise DriveError(
                "O Google não devolveu um refresh token. Remova o acesso do app em "
                "myaccount.google.com/permissions e conecte de novo.",
                "invalid_grant",
            )
        return OAuthTokens(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_in=int(data.get("expires_in", 3600)),
            scope=str(data.get("scope", "")),
        )

    def refresh_access_token(self, refresh_token: str) -> str:
        data = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        return str(data["access_token"])

    def revoke(self, token: str) -> None:
        try:
            self._http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            pass

    # --- API ---
    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, url: str, token: str, **kw: Any) -> httpx.Response:
        last: DriveError | None = None
        headers = {**self._auth(token), **kw.pop("headers", {})}
        for attempt in range(3):
            try:
                resp = self._http.request(method, url, headers=headers, **kw)
            except httpx.HTTPError as e:
                last = DriveError("Falha de rede ao falar com o Drive.", "network", retryable=True)
                last.__cause__ = e
            else:
                if resp.status_code < 400:
                    return resp
                last = map_error(resp)
            if not last.retryable:
                raise last
            self._sleep(2.0 * (attempt + 1))
        assert last is not None
        raise last

    def account_email(self, access_token: str) -> str:
        try:
            resp = self._request("GET", f"{API}/about", access_token, params={"fields": "user"})
        except DriveError:
            return ""
        user = resp.json().get("user") or {}
        return str(user.get("emailAddress", ""))

    def ensure_folder(self, access_token: str, name: str, parent_id: str | None) -> DriveFile:
        parent = parent_id or "root"
        query = (
            f"name = '{_q(name)}' and '{_q(parent)}' in parents and mimeType = '{FOLDER_MIME}' "
            "and trashed = false"
        )
        found = (
            self._request(
                "GET",
                f"{API}/files",
                access_token,
                params={"q": query, "fields": "files(id,name,webViewLink)", "pageSize": 1},
            )
            .json()
            .get("files")
            or []
        )
        if found:
            f = found[0]
            return DriveFile(str(f["id"]), str(f.get("webViewLink", "")), str(f.get("name", name)))
        body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        created = self._request(
            "POST", f"{API}/files", access_token, params={"fields": FILE_FIELDS}, json=body
        ).json()
        return DriveFile(str(created["id"]), str(created.get("webViewLink", "")), name)

    def folder_exists(self, access_token: str, folder_id: str) -> bool:
        try:
            resp = self._request(
                "GET",
                f"{API}/files/{folder_id}",
                access_token,
                params={"fields": "id,trashed,mimeType"},
            )
        except DriveError as e:
            if e.code == "not_found":
                return False
            raise
        data = resp.json()
        return not data.get("trashed", False) and data.get("mimeType") == FOLDER_MIME

    def upload_file(
        self,
        access_token: str,
        path: Path,
        name: str,
        parent_id: str,
        existing_id: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DriveFile:
        total = path.stat().st_size
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        meta: dict[str, Any]
        if existing_id:
            url = f"{UPLOAD_API}/files/{existing_id}"
            method, meta = "PATCH", {"name": name}
        else:
            url = f"{UPLOAD_API}/files"
            method, meta = "POST", {"name": name, "parents": [parent_id]}
        init = self._request(
            method,
            url,
            access_token,
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(total)},
            json=meta,
        )
        session_url = init.headers.get("Location")
        if not session_url:
            raise DriveError("O Drive não devolveu o endereço de upload.", "other")
        if total == 0:  # arquivo vazio: uma requisição sem corpo
            resp = self._http.put(session_url, content=b"", headers={"Content-Length": "0"})
            if resp.status_code >= 400:
                raise map_error(resp)
            return self._file(resp.json(), name)
        sent, failures = 0, 0
        with path.open("rb") as fh:
            while True:
                if should_cancel and should_cancel():
                    raise DriveCancelled
                fh.seek(sent)
                block = fh.read(self._chunk)
                end = sent + len(block) - 1
                try:
                    resp = self._http.put(
                        session_url,
                        content=block,
                        headers={
                            "Content-Length": str(len(block)),
                            "Content-Range": f"bytes {sent}-{end}/{total}",
                            "Content-Type": mime,
                        },
                    )
                except httpx.HTTPError as e:
                    failures += 1
                    if failures > MAX_CHUNK_RETRIES:
                        raise DriveError(
                            "A conexão caiu várias vezes durante o envio ao Drive.",
                            "network",
                            retryable=True,
                        ) from e
                    self._sleep(min(2.0 * failures, 20.0))
                    sent = self._query_progress(session_url, total, sent)
                    continue
                if resp.status_code in (200, 201):
                    if on_progress:
                        on_progress(total, total)
                    return self._file(resp.json(), name)
                if resp.status_code == 308:
                    sent = _range_end(resp.headers.get("Range"), sent + len(block))
                    failures = 0
                    if on_progress:
                        on_progress(sent, total)
                    continue
                err = map_error(resp)
                if err.retryable and failures < MAX_CHUNK_RETRIES:
                    failures += 1
                    self._sleep(min(2.0 * failures, 20.0))
                    sent = self._query_progress(session_url, total, sent)
                    continue
                raise err

    def _query_progress(self, session_url: str, total: int, fallback: int) -> int:
        try:
            resp = self._http.put(
                session_url, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"}
            )
        except httpx.HTTPError:
            return fallback
        if resp.status_code == 308:
            return _range_end(resp.headers.get("Range"), 0)
        return fallback

    @staticmethod
    def _file(data: dict[str, Any], name: str) -> DriveFile:
        return DriveFile(
            id=str(data["id"]),
            url=str(data.get("webViewLink", "")),
            name=str(data.get("name", name)),
            md5=str(data.get("md5Checksum", "")),
        )


@dataclass
class FakeDrive:
    """Drive de mentira (memória): guarda pastas e arquivos para os testes conferirem."""

    email: str = "fake@example.com"
    folders: dict[str, dict[str, Any]] = field(default_factory=dict)  # id → {name, parent}
    files: dict[str, dict[str, Any]] = field(default_factory=dict)  # id → {name, parent, bytes}
    uploads: list[tuple[str, str]] = field(default_factory=list)  # (ação, nome)
    fail_names: dict[str, DriveError] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    _seq: int = 0

    def _new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:04d}"

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"{redirect_uri}?{urlencode({'code': 'fake-code', 'state': state})}"

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        return OAuthTokens("fake-access", "fake-drive-refresh", scope=" ".join(SCOPES))

    def refresh_access_token(self, refresh_token: str) -> str:
        return "fake-access"

    def revoke(self, token: str) -> None:
        self.revoked.append(token)

    def account_email(self, access_token: str) -> str:
        return self.email

    def ensure_folder(self, access_token: str, name: str, parent_id: str | None) -> DriveFile:
        for fid, f in self.folders.items():
            if f["name"] == name and f["parent"] == parent_id:
                return DriveFile(fid, f"https://drive.fake/folders/{fid}", name)
        fid = self._new_id("folder")
        self.folders[fid] = {"name": name, "parent": parent_id}
        return DriveFile(fid, f"https://drive.fake/folders/{fid}", name)

    def folder_exists(self, access_token: str, folder_id: str) -> bool:
        return folder_id in self.folders

    def upload_file(
        self,
        access_token: str,
        path: Path,
        name: str,
        parent_id: str,
        existing_id: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DriveFile:
        if name in self.fail_names:
            raise self.fail_names[name]
        if should_cancel and should_cancel():
            raise DriveCancelled
        if parent_id not in self.folders:
            raise DriveError("A pasta não existe mais no Drive.", "not_found", 404)
        data = path.read_bytes()
        if on_progress:
            on_progress(len(data) // 2, len(data))
            on_progress(len(data), len(data))
        if existing_id:
            if existing_id not in self.files:
                raise DriveError("O arquivo não existe mais no Drive.", "not_found", 404)
            self.files[existing_id].update({"name": name, "bytes": data})
            self.uploads.append(("update", name))
            fid = existing_id
        else:
            fid = self._new_id("file")
            self.files[fid] = {"name": name, "parent": parent_id, "bytes": data}
            self.uploads.append(("create", name))
        return DriveFile(fid, f"https://drive.fake/files/{fid}", name)


_demo: FakeDrive | None = None


def default_client() -> DriveLike:
    from canaisdark.config import get_settings

    settings = get_settings()
    if settings.drive_fake:
        global _demo
        if _demo is None:
            _demo = FakeDrive()
        return _demo
    return DriveClient(settings.google_client_id, settings.google_client_secret)
