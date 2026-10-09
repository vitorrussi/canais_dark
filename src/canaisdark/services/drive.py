"""Google Drive por canal: conexão OAuth (escopo drive.file), pastas criadas pelo app e sincronização."""

import hashlib
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.models import Asset, AssetType, Channel, DriveAccount, Job, User, Video
from canaisdark.models.channel import utcnow
from canaisdark.services import (
    channels,
    crypto,
    music,
    narration,
    scene_assets,
    script_formats,
    scripts,
    shorts_render,
    thumbnail,
    videos,
)
from canaisdark.services import render as render_service
from canaisdark.services.errors import InvalidState, NotFound

STATE_SALT = "drive-oauth"
STATE_MAX_AGE_S = 600
CALLBACK_PATH = "/oauth/drive/callback"

ClientFactory = Callable[[], gd.DriveLike]


@contextmanager
def _translated() -> Iterator[None]:
    """Erros do cliente viram erros de domínio (mensagem em português)."""
    try:
        yield
    except gd.DriveError as e:
        raise InvalidState(str(e)) from e


def redirect_uri(base_url: str = "") -> str:
    root = (get_settings().public_url or base_url).rstrip("/")
    return f"{root}{CALLBACK_PATH}"


def setup_checklist(base_url: str = "") -> dict[str, Any]:
    s = get_settings()
    items = [
        {
            "key": "client",
            "ok": s.drive_ready,
            "label": "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no .env (com a Google Drive API ativada)",
        },
        {
            "key": "fernet",
            "ok": crypto.configured(),
            "label": "FERNET_KEY no .env (criptografa o token do Drive)",
        },
    ]
    return {
        "ready": all(i["ok"] for i in items),
        "items": items,
        "fake": s.drive_fake,
        "redirect_uri": redirect_uri(base_url),
    }


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=STATE_SALT)


def make_state(channel_id: int, user_id: int) -> str:
    return _serializer().dumps({"c": channel_id, "u": user_id})


def read_state(state: str) -> tuple[int, int]:
    try:
        data = _serializer().loads(state, max_age=STATE_MAX_AGE_S)
        return int(data["c"]), int(data["u"])
    except SignatureExpired as e:
        raise InvalidState("A conexão demorou demais. Clique em “Conectar Drive” de novo.") from e
    except (BadSignature, KeyError, TypeError, ValueError) as e:
        raise InvalidState("Retorno do Google inválido. Comece a conexão de novo pelo site.") from e


def get_account(session: Session, channel_id: int) -> DriveAccount | None:
    return session.exec(select(DriveAccount).where(DriveAccount.channel_id == channel_id)).first()


def _client(factory: ClientFactory | None) -> gd.DriveLike:
    with _translated():
        return (factory or gd.default_client)()


def connect_url(
    session: Session,
    channel: Channel,
    user: User,
    base_url: str = "",
    factory: ClientFactory | None = None,
) -> str:
    check = setup_checklist(base_url)
    if not check["ready"]:
        missing = "; ".join(i["label"] for i in check["items"] if not i["ok"])
        raise InvalidState(f"Falta configurar: {missing}. Veja docs/drive.md.")
    assert channel.id is not None and user.id is not None
    client = _client(factory)
    with _translated():
        return client.authorization_url(make_state(channel.id, user.id), redirect_uri(base_url))


def complete_connection(
    session: Session,
    code: str,
    state: str,
    user: User,
    base_url: str = "",
    factory: ClientFactory | None = None,
) -> tuple[Channel, DriveAccount]:
    channel_id, state_user = read_state(state)
    if state_user != user.id:
        raise InvalidState("Esta conexão foi iniciada por outra pessoa. Comece de novo.")
    channel = channels.get_channel(session, channel_id)
    client = _client(factory)
    with _translated():
        tokens = client.exchange_code(code, redirect_uri(base_url))
        email = client.account_email(tokens.access_token)
    account = get_account(session, channel_id)
    encrypted = crypto.encrypt(tokens.refresh_token)
    if account is None:
        account = DriveAccount(channel_id=channel_id, refresh_token_enc=encrypted)
    account.refresh_token_enc = encrypted
    account.scopes = tokens.scope
    account.account_email = email
    account.connected_by = user.id
    account.connected_at = utcnow()
    account.needs_reconnect = False
    session.add(account)
    session.commit()
    session.refresh(account)
    return channel, account


def disconnect(session: Session, channel_id: int, factory: ClientFactory | None = None) -> None:
    """Desconecta (revoga o token). Os arquivos já enviados continuam no Drive."""
    account = get_account(session, channel_id)
    if account is None:
        raise NotFound("Este canal não está conectado ao Google Drive.")
    try:
        _client(factory).revoke(crypto.decrypt(account.refresh_token_enc))
    except InvalidState:
        pass
    session.delete(account)
    session.commit()


def access_token(session: Session, channel_id: int, factory: ClientFactory | None = None) -> str:
    account = get_account(session, channel_id)
    if account is None:
        raise InvalidState(
            "Este canal não está conectado ao Google Drive. Conecte na página do canal."
        )
    refresh = crypto.decrypt(account.refresh_token_enc)
    client = _client(factory)
    try:
        return client.refresh_access_token(refresh)
    except gd.DriveError as e:
        if e.code == "invalid_grant":
            account.needs_reconnect = True
            session.add(account)
            session.commit()
        raise InvalidState(str(e)) from e


def status(session: Session, channel: Channel, base_url: str = "") -> dict[str, Any]:
    assert channel.id is not None
    account = get_account(session, channel.id)
    return {
        "setup": setup_checklist(base_url),
        "account": account,
        "connected": account is not None and not account.needs_reconnect,
        "needs_reconnect": bool(account and account.needs_reconnect),
        "folder_id": channel.drive_folder_id,
    }


# ----- Manifesto e sincronização -----

ROOT_NAME = "Estúdio de Canais Dark"
OPTION_LABELS = {
    "video": "Vídeo final",
    "shorts": "Shorts finais",
    "thumbnail": "Thumbnail",
    "script": "Roteiro (.md e .json)",
    "subtitles": "Legendas (.srt e .ass)",
    "audio": "Narração (.mp3)",
    "media": "Imagens, clipes e trilha",
}
DEFAULT_OPTIONS = ("video", "shorts", "thumbnail", "script", "subtitles", "audio")
ALL_OPTIONS = tuple(OPTION_LABELS)
FOLDER_OF = {
    "video": "video",
    "shorts": "shorts",
    "thumbnail": "thumbnail",
    "script": "roteiro",
    "subtitles": "roteiro",
    "audio": "audio",
    "media": "midia",
}


@dataclass
class Item:
    key: str  # estável entre sincronizações (identifica o arquivo no Drive)
    option: str
    name: str
    stamp: str  # muda quando o conteúdo muda (barato: tamanho+mtime, ou versão do roteiro)
    path: Path | None = None
    content: str | None = None  # arquivos gerados na hora (roteiro)

    @property
    def folder(self) -> str:
        return FOLDER_OF[self.option]


def _safe(name: str) -> str:
    cleaned = "".join("-" if c in '/\\:*?"<>|' or ord(c) < 32 else c for c in name).strip(" .")
    return cleaned[:100] or "arquivo"


def _file_stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _file_item(key: str, option: str, name: str, asset_path: str) -> Item | None:
    path = get_settings().data_dir / asset_path
    if not path.is_file():
        return None
    return Item(key, option, _safe(name), _file_stamp(path), path=path)


def build_manifest(
    session: Session, video: Video, options: tuple[str, ...] | list[str]
) -> list[Item]:
    """Arquivos locais que podem ir ao Drive para as opções escolhidas (só os que existem)."""
    assert video.id is not None
    vid = video.id
    base = channels.slugify(video.title or f"video-{vid}")[:60] or f"video-{vid}"
    items: list[Item | None] = []
    if "video" in options:
        final = render_service.latest_render(session, vid, "final")
        if final:
            items.append(_file_item("render:final", "video", f"{base}.mp4", final.path))
    if "shorts" in options:
        done: set[int] = set()
        for a in shorts_render.list_shorts(session, vid, "final"):
            index = int(a.meta.get("index", -1))
            if index in done:
                continue
            done.add(index)
            title = channels.slugify(str(a.meta.get("title", ""))) or "short"
            items.append(
                _file_item(
                    f"short:{index}:final", "shorts", f"short-{index + 1}-{title}.mp4", a.path
                )
            )
    if "thumbnail" in options:
        thumb = thumbnail.latest(session, vid)
        if thumb:
            items.append(_file_item("thumbnail", "thumbnail", "thumbnail.jpg", thumb.path))
    if "script" in options and video.approved_script_version_id:
        version = scripts.get_version(session, video.approved_script_version_id)
        scenes = scripts.scenes_of(session, version)
        channel = channels.get_channel(session, video.channel_id)
        title = video.title or video.topic[:80]
        for fmt in ("md", "json"):
            dl = script_formats.render_script(fmt, title, version, scenes, channel, video)
            items.append(
                Item(
                    f"script:{fmt}",
                    "script",
                    f"roteiro.{fmt}",
                    f"v{version.id}:{hashlib.sha256(dl.content.encode()).hexdigest()[:16]}",
                    content=dl.content,
                )
            )
    if "subtitles" in options:
        for fmt in ("srt", "ass"):
            sub = narration.latest_subtitle(session, vid, fmt)
            if sub:
                items.append(_file_item(f"sub:{fmt}", "subtitles", f"legendas.{fmt}", sub.path))
    if "audio" in options:
        audio_asset = narration.latest_asset(session, vid, AssetType.AUDIO, "narration")
        if audio_asset:
            items.append(_file_item("audio:narration", "audio", "narracao.mp3", audio_asset.path))
    if "media" in options and video.approved_script_version_id:
        version = scripts.get_version(session, video.approved_script_version_id)
        scenes = scripts.scenes_of(session, version)
        amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
        by_id = {s.id: s for s in scenes}
        for scene_id, variants in amap.items():
            for variant, asset in variants.items():
                ext = Path(asset.path).suffix or ".jpg"
                idx = by_id[scene_id].index + 1 if scene_id in by_id else 0
                items.append(
                    _file_item(
                        f"scene:{idx - 1}:{variant}",
                        "media",
                        f"cena-{idx:03d}-{variant}{ext}",
                        asset.path,
                    )
                )
        for m in music.list_music(session, vid):
            items.append(
                _file_item(
                    f"music:{m.id}", "media", f"trilha-{m.meta.get('original_name', m.id)}", m.path
                )
            )
    return [i for i in items if i is not None]


def registry(session: Session, video_id: int) -> dict[str, Asset]:
    """Último registro por chave (o que já está no Drive)."""
    out: dict[str, Asset] = {}
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.DRIVE_FILE.value)
        .order_by(col(Asset.id))
    )
    for a in session.exec(stmt):
        out[str(a.meta.get("key"))] = a
    return out


def item_state(item: Item, prior: Asset | None) -> str:
    if prior is None:
        return "new"
    same = prior.meta.get("stamp") == item.stamp and prior.meta.get("name") == item.name
    return "synced" if same else "changed"


def last_options(session: Session, video_id: int) -> tuple[str, ...]:
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    job = jobs.latest_job(session, video_id, JobType.DRIVE_SYNC)
    opts = job.params.get("options") if job else None
    if isinstance(opts, list) and opts:
        return tuple(o for o in opts if o in ALL_OPTIONS)
    return DEFAULT_OPTIONS


def overview(
    session: Session, video: Video, options: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """Linhas para a interface: cada arquivo do manifesto + estado (new|changed|synced)."""
    assert video.id is not None
    reg = registry(session, video.id)
    rows = []
    for item in build_manifest(session, video, options or last_options(session, video.id)):
        prior = reg.get(item.key)
        rows.append(
            {
                "key": item.key,
                "name": item.name,
                "option": item.option,
                "folder": item.folder,
                "state": item_state(item, prior),
                "url": prior.meta.get("url", "") if prior else "",
                "synced_at": prior.meta.get("synced_at") if prior else None,
            }
        )
    return rows


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def start_sync(
    session: Session,
    video_id: int,
    actor: User,
    options: list[str] | tuple[str, ...] | None = None,
) -> Job:
    """Valida e enfileira o job `drive_sync` (só site/REST; nunca por tool MCP)."""
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    account = get_account(session, channel.id or 0)
    if account is None:
        raise InvalidState(
            f"O canal “{channel.name}” não está conectado ao Google Drive. Conecte na página do canal."
        )
    if account.needs_reconnect:
        raise InvalidState(
            "A autorização do Drive deste canal expirou. Reconecte na página do canal."
        )
    chosen = tuple(options) if options else DEFAULT_OPTIONS
    unknown = [o for o in chosen if o not in ALL_OPTIONS]
    if unknown:
        raise InvalidState(f"Opção desconhecida: {', '.join(unknown)}.")
    if not build_manifest(session, video, chosen):
        raise InvalidState(
            "Ainda não há arquivos para enviar com essas opções (renderize o vídeo, gere a narração…)."
        )
    return jobs.create_job(session, video_id, JobType.DRIVE_SYNC, {"options": list(chosen)}, actor)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _record(
    session: Session,
    video_id: int,
    item: Item,
    drive_id: str,
    url: str,
    sha: str,
    folder_id: str,
    job_id: int | None,
) -> None:
    prior = registry(session, video_id).get(item.key)
    meta = {
        "key": item.key,
        "drive_file_id": drive_id,
        "url": url,
        "sha256": sha,
        "stamp": item.stamp,
        "name": item.name,
        "folder": item.folder,
        "folder_id": folder_id,
        "synced_at": int(time.time()),
    }
    if prior is None:
        session.add(
            Asset(
                video_id=video_id,
                type=AssetType.DRIVE_FILE.value,
                path="",
                meta=meta,
                drive_file_id=drive_id,
                job_id=job_id,
            )
        )
    else:
        prior.meta = meta
        prior.drive_file_id = drive_id
        session.add(prior)
    session.commit()


def run_job(session: Session, job: Job, factory: ClientFactory | None = None) -> None:
    from canaisdark.services import jobs

    assert job.id is not None
    job_id = job.id
    video = videos.get_video(session, job.video_id)
    channel = channels.get_channel(session, video.channel_id)
    options = tuple(job.params.get("options") or DEFAULT_OPTIONS)
    items = build_manifest(session, video, options)
    reg = registry(session, job.video_id)
    client = _client(factory)
    token = access_token(session, channel.id or 0, factory)

    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except gd.DriveError as e:
            if e.code == "invalid_grant":
                account = get_account(session, channel.id or 0)
                if account:
                    account.needs_reconnect = True
                    session.add(account)
                    session.commit()
            raise

    # pastas: canal (reaproveita o ID salvo se ainda existir) → vídeo → subpastas sob demanda
    root = call(lambda: client.ensure_folder(token, ROOT_NAME, None))
    if not channel.drive_folder_id or not call(
        lambda: client.folder_exists(token, channel.drive_folder_id)
    ):
        chan_folder = call(lambda: client.ensure_folder(token, _safe(channel.name), root.id))
        channel.drive_folder_id = chan_folder.id
        session.add(channel)
        session.commit()
    recreated = False
    if not video.drive_folder_id or not call(
        lambda: client.folder_exists(token, video.drive_folder_id)
    ):
        recreated = bool(video.drive_folder_id)
        name = _safe(f"#{video.id} {video.title or video.topic[:50]}")
        vf = call(lambda: client.ensure_folder(token, name, channel.drive_folder_id))
        video.drive_folder_id = vf.id
    video.drive_url = folder_url(video.drive_folder_id)
    session.add(video)
    session.commit()
    jobs.append_log(session, job_id, f"Pasta do vídeo: {video.drive_url}")
    sub_ids: dict[str, str] = {}

    def subfolder(name: str) -> str:
        if name not in sub_ids:
            sub_ids[name] = call(
                lambda: client.ensure_folder(token, name, video.drive_folder_id)
            ).id
        return sub_ids[name]

    def send(
        path: Path,
        name: str,
        parent: str,
        existing: str | None,
        progress: Callable[[int, int], None],
    ) -> gd.DriveFile:
        out: gd.DriveFile = call(
            lambda: client.upload_file(
                token,
                path,
                name,
                parent,
                existing,
                progress,
                lambda: jobs.is_cancelled(session, job_id),
            )
        )
        return out

    sent = skipped = 0
    failed: list[str] = []
    todo = [i for i in items if recreated or item_state(i, reg.get(i.key)) != "synced"]
    skipped = len(items) - len(todo)
    total = max(len(todo), 1)
    with tempfile.TemporaryDirectory(prefix="drive-sync-") as tmp:
        for n, item in enumerate(todo):
            if jobs.is_cancelled(session, job_id):
                raise gd.DriveCancelled
            path = item.path
            if path is None:
                path = Path(tmp) / item.name
                path.write_text(item.content or "", encoding="utf-8")
            sha = _sha256(path)
            prior = reg.get(item.key)
            if (
                prior
                and not recreated
                and prior.meta.get("sha256") == sha
                and (prior.meta.get("folder") == item.folder)
            ):
                _record(
                    session,
                    job.video_id,
                    item,
                    str(prior.meta["drive_file_id"]),
                    str(prior.meta.get("url", "")),
                    sha,
                    str(prior.meta.get("folder_id", "")),
                    job_id,
                )
                skipped += 1  # só o carimbo mudou (ex.: arquivo regravado igual)
                continue
            existing = (
                str(prior.meta["drive_file_id"])
                if prior and not recreated and prior.meta.get("folder") == item.folder
                else None
            )

            def progress(done: int, tot: int, n: int = n) -> None:
                jobs.heartbeat(
                    session, job_id, min(int((n + done / max(tot, 1)) * 100 / total), 99)
                )

            parent = subfolder(item.folder)
            try:
                try:
                    out = send(path, item.name, parent, existing, progress)
                except gd.DriveError as e:
                    if e.code != "not_found":
                        raise
                    # arquivo ou subpasta apagados no Drive: recria sem duplicar o resto
                    sub_ids.pop(item.folder, None)
                    parent = subfolder(item.folder)
                    out = send(path, item.name, parent, None, progress)
            except gd.DriveError as e:
                failed.append(f"{item.name}: {e}")
                jobs.append_log(session, job_id, f"ERRO em {item.name}: {e}")
                if e.code in ("storage_full", "invalid_grant", "forbidden"):
                    break  # insistir nos outros só repetiria o erro
                continue
            _record(session, job.video_id, item, out.id, out.url, sha, parent, job_id)
            sent += 1
            jobs.append_log(
                session,
                job_id,
                f"{'Atualizado' if existing else 'Enviado'}: {item.folder}/{item.name}",
            )
    summary = f"{sent} enviado(s), {skipped} sem mudança, {len(failed)} com erro."
    jobs.append_log(session, job_id, summary)
    if failed:
        raise InvalidState(f"{summary} Primeiro erro: {failed[0]}")
    jobs.complete(session, job_id, {"sent": sent, "skipped": skipped, "folder": video.drive_url})
