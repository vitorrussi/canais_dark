"""Cliente do YouTube (OAuth + upload resumable + thumbnail) e um FAKE para testes/demonstração.

Nunca loga tokens nem o client secret. Mensagens de erro já em português para exibir no site.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
CHUNK = 8 * 1024 * 1024  # múltiplo de 256 KiB, exigência do upload resumable
MAX_CHUNK_RETRIES = 5


class YouTubeError(Exception):
    """`code`: quota | upload_limit | invalid_grant | forbidden | no_channel | config | network | other."""

    def __init__(
        self, message: str, code: str = "other", status: int = 0, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


class UploadCancelled(Exception):
    pass


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int = 3600
    scope: str = ""


@dataclass
class ChannelInfo:
    id: str
    title: str


class YouTubeLike(Protocol):
    def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...

    def refresh_access_token(self, refresh_token: str) -> str: ...

    def my_channel(self, access_token: str) -> ChannelInfo | None: ...

    def revoke(self, token: str) -> None: ...

    def upload_video(
        self,
        access_token: str,
        path: Path,
        snippet: dict[str, Any],
        status: dict[str, Any],
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str: ...

    def set_thumbnail(self, access_token: str, video_id: str, data: bytes, mime: str) -> None: ...


_REASONS: dict[str, tuple[str, str]] = {
    "quotaExceeded": (
        "quota",
        "A cota diária da API do YouTube acabou. Tente de novo depois da meia-noite (horário do Pacífico).",
    ),
    "dailyLimitExceeded": (
        "quota",
        "A cota diária da API do YouTube acabou. Tente de novo depois da meia-noite (horário do Pacífico).",
    ),
    "rateLimitExceeded": (
        "quota",
        "Muitas requisições ao YouTube em pouco tempo. Aguarde um pouco.",
    ),
    "uploadLimitExceeded": (
        "upload_limit",
        "O canal atingiu o limite de uploads do YouTube por hoje.",
    ),
    "forbidden": (
        "forbidden",
        "O YouTube recusou a operação (permissão). Reconecte o canal e confira a conta escolhida.",
    ),
    "insufficientPermissions": (
        "forbidden",
        "Faltam permissões do YouTube. Reconecte o canal aceitando todas as permissões.",
    ),
    "youtubeSignupRequired": (
        "no_channel",
        "A conta Google escolhida não tem um canal do YouTube.",
    ),
    "authError": ("invalid_grant", "A autorização do YouTube expirou. Reconecte o canal."),
    "invalidTitle": ("other", "O YouTube recusou o título. Revise caracteres e tamanho (até 100)."),
    "invalidDescription": ("other", "O YouTube recusou a descrição. Revise caracteres e tamanho."),
    "invalidTags": ("other", "O YouTube recusou as tags. Revise tamanho e caracteres."),
    "mediaBodyRequired": ("other", "O arquivo enviado está vazio."),
}


def map_error(resp: httpx.Response) -> YouTubeError:
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
    elif isinstance(err, str):  # endpoint de token: {"error": "invalid_grant", ...}
        reason = err
        detail = str(body.get("error_description", ""))
    if reason == "invalid_grant":
        return YouTubeError(
            "A autorização do YouTube foi revogada ou expirou. Reconecte o canal.",
            "invalid_grant",
            resp.status_code,
        )
    if reason in _REASONS:
        code, msg = _REASONS[reason]
        return YouTubeError(msg, code, resp.status_code)
    if resp.status_code == 401:
        return YouTubeError(*(_REASONS["authError"][1], "invalid_grant", 401))
    if resp.status_code >= 500 or resp.status_code == 429:
        return YouTubeError(
            f"O YouTube está instável (HTTP {resp.status_code}).",
            "network",
            resp.status_code,
            retryable=True,
        )
    return YouTubeError(
        f"O YouTube recusou o pedido (HTTP {resp.status_code}). {detail}".strip(),
        "other",
        resp.status_code,
    )


class YouTubeClient:
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
            raise YouTubeError(
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
                "include_granted_scopes": "true",
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
            raise YouTubeError(
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
            raise YouTubeError(
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
            pass  # revogar é "melhor esforço": o registro local é apagado de qualquer forma

    # --- API ---
    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def my_channel(self, access_token: str) -> ChannelInfo | None:
        try:
            resp = self._http.get(
                f"{API}/channels",
                params={"part": "snippet", "mine": "true"},
                headers=self._auth(access_token),
            )
        except httpx.HTTPError as e:
            raise YouTubeError(
                "Falha de rede ao falar com o YouTube.", "network", retryable=True
            ) from e
        if resp.status_code >= 400:
            raise map_error(resp)
        items = resp.json().get("items") or []
        if not items:
            return None
        return ChannelInfo(id=str(items[0]["id"]), title=str(items[0]["snippet"]["title"]))

    def upload_video(
        self,
        access_token: str,
        path: Path,
        snippet: dict[str, Any],
        status: dict[str, Any],
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        total = path.stat().st_size
        if total == 0:
            raise YouTubeError("O arquivo de vídeo está vazio.", "other")
        try:
            init = self._http.post(
                f"{UPLOAD_API}/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    **self._auth(access_token),
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(total),
                },
                json={"snippet": snippet, "status": status},
            )
        except httpx.HTTPError as e:
            raise YouTubeError(
                "Falha de rede ao iniciar o upload.", "network", retryable=True
            ) from e
        if init.status_code >= 400:
            raise map_error(init)
        session_url = init.headers.get("Location")
        if not session_url:
            raise YouTubeError("O YouTube não devolveu o endereço de upload.", "other")
        sent = 0
        failures = 0
        with path.open("rb") as fh:
            while True:
                if should_cancel and should_cancel():
                    raise UploadCancelled
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
                            "Content-Type": "video/mp4",
                        },
                    )
                except httpx.HTTPError as e:
                    failures += 1
                    if failures > MAX_CHUNK_RETRIES:
                        raise YouTubeError(
                            "A conexão caiu várias vezes durante o upload.",
                            "network",
                            retryable=True,
                        ) from e
                    self._sleep(min(2.0 * failures, 20.0))
                    sent = self._query_progress(session_url, total, sent)
                    continue
                if resp.status_code in (200, 201):
                    if on_progress:
                        on_progress(total, total)
                    return str(resp.json()["id"])
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
        """Pergunta ao YouTube quantos bytes ele já recebeu (retomada)."""
        try:
            resp = self._http.put(
                session_url, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"}
            )
        except httpx.HTTPError:
            return fallback
        if resp.status_code == 308:
            return _range_end(resp.headers.get("Range"), 0)
        return fallback

    def set_thumbnail(self, access_token: str, video_id: str, data: bytes, mime: str) -> None:
        try:
            resp = self._http.post(
                f"{UPLOAD_API}/thumbnails/set",
                params={"videoId": video_id, "uploadType": "media"},
                headers={**self._auth(access_token), "Content-Type": mime},
                content=data,
            )
        except httpx.HTTPError as e:
            raise YouTubeError(
                "Falha de rede ao enviar a thumbnail.", "network", retryable=True
            ) from e
        if resp.status_code >= 400:
            raise map_error(resp)


def _range_end(header: str | None, default: int) -> int:
    """`Range: bytes=0-8388607` → próximo byte a enviar (8388608)."""
    if not header or "-" not in header:
        return default
    try:
        return int(header.rsplit("-", 1)[1]) + 1
    except ValueError:
        return default


@dataclass
class FakeYouTube:
    """YouTube de mentira: sem rede; guarda o que foi 'enviado' para os testes conferirem."""

    channel: ChannelInfo = field(default_factory=lambda: ChannelInfo("UCfake0001", "Canal Fake"))
    fail_upload_with: YouTubeError | None = None
    fail_thumbnail_with: YouTubeError | None = None
    uploads: list[dict[str, Any]] = field(default_factory=list)
    thumbnails: list[tuple[str, int, str]] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    refreshes: int = 0

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"{redirect_uri}?{urlencode({'code': 'fake-code', 'state': state})}"

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        return OAuthTokens("fake-access", "fake-refresh", scope=" ".join(SCOPES))

    def refresh_access_token(self, refresh_token: str) -> str:
        self.refreshes += 1
        return "fake-access"

    def my_channel(self, access_token: str) -> ChannelInfo | None:
        return self.channel

    def revoke(self, token: str) -> None:
        self.revoked.append(token)

    def upload_video(
        self,
        access_token: str,
        path: Path,
        snippet: dict[str, Any],
        status: dict[str, Any],
        on_progress: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        if self.fail_upload_with is not None:
            raise self.fail_upload_with
        if should_cancel and should_cancel():
            raise UploadCancelled
        size = path.stat().st_size
        if on_progress:
            on_progress(size // 2, size)
            on_progress(size, size)
        video_id = f"fakeVid{len(self.uploads) + 1:04d}"
        self.uploads.append(
            {"id": video_id, "path": str(path), "bytes": size, "snippet": snippet, "status": status}
        )
        return video_id

    def set_thumbnail(self, access_token: str, video_id: str, data: bytes, mime: str) -> None:
        if self.fail_thumbnail_with is not None:
            raise self.fail_thumbnail_with
        self.thumbnails.append((video_id, len(data), mime))


_demo: FakeYouTube | None = None


def default_client() -> YouTubeLike:
    from canaisdark.config import get_settings

    settings = get_settings()
    if settings.youtube_fake:
        global _demo
        if _demo is None:
            _demo = FakeYouTube()
        return _demo
    return YouTubeClient(settings.google_client_id, settings.google_client_secret)
