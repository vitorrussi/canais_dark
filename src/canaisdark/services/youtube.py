"""YouTube por canal: conexão OAuth, tokens criptografados e (adiante) upload privado com cota."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.integrations import youtube as yt
from canaisdark.models import Asset, AssetType, Channel, Job, User, Video, YouTubeAccount
from canaisdark.models.channel import utcnow
from canaisdark.services import (
    channels,
    crypto,
    shorts_render,
    video_status,
    videos,
)
from canaisdark.services import metadata as metadata_service
from canaisdark.services import render as render_service
from canaisdark.services import thumbnail as thumbnail_service
from canaisdark.services.errors import InvalidState, NotFound

STATE_SALT = "youtube-oauth"
STATE_MAX_AGE_S = 600
CALLBACK_PATH = "/oauth/youtube/callback"

ClientFactory = Callable[[], yt.YouTubeLike]


@contextmanager
def _translated() -> Iterator[None]:
    """Erros do cliente viram erros de domínio (mensagem em português, 409 no site/REST)."""
    try:
        yield
    except yt.YouTubeError as e:
        raise InvalidState(str(e)) from e


def redirect_uri(base_url: str = "") -> str:
    settings = get_settings()
    root = (settings.public_url or base_url).rstrip("/")
    return f"{root}{CALLBACK_PATH}"


def setup_checklist(base_url: str = "") -> dict[str, Any]:
    """O que falta configurar (mostrado no canal; nenhum valor secreto aparece)."""
    s = get_settings()
    items = [
        {
            "key": "client",
            "ok": s.youtube_fake or bool(s.google_client_id and s.google_client_secret),
            "label": "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no .env",
        },
        {
            "key": "fernet",
            "ok": crypto.configured(),
            "label": "FERNET_KEY no .env (criptografa o token do YouTube)",
        },
    ]
    return {
        "ready": all(i["ok"] for i in items),
        "items": items,
        "fake": s.youtube_fake,
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
        raise InvalidState("A conexão demorou demais. Clique em “Conectar YouTube” de novo.") from e
    except (BadSignature, KeyError, TypeError, ValueError) as e:
        raise InvalidState("Retorno do Google inválido. Comece a conexão de novo pelo site.") from e


def get_account(session: Session, channel_id: int) -> YouTubeAccount | None:
    return session.exec(
        select(YouTubeAccount).where(YouTubeAccount.channel_id == channel_id)
    ).first()


def _client(factory: ClientFactory | None) -> yt.YouTubeLike:
    with _translated():
        return (factory or yt.default_client)()


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
        raise InvalidState(f"Falta configurar: {missing}. Veja docs/youtube.md.")
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
) -> tuple[Channel, YouTubeAccount]:
    channel_id, state_user = read_state(state)
    if state_user != user.id:
        raise InvalidState("Esta conexão foi iniciada por outra pessoa. Comece de novo.")
    channel = channels.get_channel(session, channel_id)
    client = _client(factory)
    with _translated():
        tokens = client.exchange_code(code, redirect_uri(base_url))
        info = client.my_channel(tokens.access_token)
    if info is None:
        client.revoke(tokens.refresh_token)
        raise InvalidState(
            "A conta Google escolhida não tem um canal do YouTube. "
            "Escolha a conta/canal certo e tente de novo."
        )
    account = get_account(session, channel_id)
    encrypted = crypto.encrypt(tokens.refresh_token)
    if account is None:
        account = YouTubeAccount(channel_id=channel_id, refresh_token_enc=encrypted)
    account.refresh_token_enc = encrypted
    account.scopes = tokens.scope
    account.youtube_channel_id = info.id
    account.youtube_channel_title = info.title
    account.connected_by = user.id
    account.connected_at = utcnow()
    account.needs_reconnect = False
    session.add(account)
    session.commit()
    session.refresh(account)
    return channel, account


def disconnect(session: Session, channel_id: int, factory: ClientFactory | None = None) -> None:
    account = get_account(session, channel_id)
    if account is None:
        raise NotFound("Este canal não está conectado ao YouTube.")
    try:
        token = crypto.decrypt(account.refresh_token_enc)
        _client(factory).revoke(token)
    except InvalidState:
        pass  # sem chave/cliente: apaga o registro local mesmo assim
    session.delete(account)
    session.commit()


def access_token(session: Session, channel_id: int, factory: ClientFactory | None = None) -> str:
    """Access token novo a partir do refresh token; `invalid_grant` marca "reconectar"."""
    account = get_account(session, channel_id)
    if account is None:
        raise InvalidState(
            "Este canal não está conectado ao YouTube. Conecte-o na página do canal."
        )
    refresh = crypto.decrypt(account.refresh_token_enc)
    client = _client(factory)
    try:
        return client.refresh_access_token(refresh)
    except yt.YouTubeError as e:
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
        "category_id": channel.youtube_category_id,
        "categories": CATEGORIES,
    }


# Categorias mais úteis para canais dark (ID da API → rótulo)
CATEGORIES = {
    "24": "Entretenimento",
    "27": "Educação",
    "22": "Pessoas e blogs",
    "25": "Notícias e política",
    "28": "Ciência e tecnologia",
    "23": "Comédia",
    "20": "Games",
    "10": "Música",
    "1": "Filmes e animações",
}


def set_category(session: Session, channel_id: int, category_id: str) -> Channel:
    if category_id not in CATEGORIES:
        raise InvalidState("Categoria desconhecida.")
    channel = channels.get_channel(session, channel_id)
    channel.youtube_category_id = category_id
    channel.updated_at = utcnow()
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


# ----- Upload (job `youtube_upload`) -----

UPLOAD_COST = 1600  # unidades de cota por videos.insert
THUMBNAIL_COST = 50
SHORT_SUFFIX = " #Shorts"


def quota_day_start() -> datetime:
    """Início do dia de cota do Google (meia-noite no Pacífico), em UTC."""
    try:
        tz: tzinfo = ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:  # sem tzdata no sistema
        tz = timezone(timedelta(hours=-8))
    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def list_uploads(session: Session, video_id: int | None = None) -> list[Asset]:
    stmt = select(Asset).where(Asset.type == AssetType.UPLOAD.value).order_by(col(Asset.id).desc())
    if video_id is not None:
        stmt = stmt.where(Asset.video_id == video_id)
    return list(session.exec(stmt))


def quota_used(session: Session) -> int:
    """Unidades gastas hoje por este sistema (a cota é do projeto Google, vale para todos os canais)."""
    since = quota_day_start()
    total = 0
    for a in list_uploads(session):
        created = a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=UTC)
        if created >= since:
            total += int(a.meta.get("units", UPLOAD_COST))
    return total


def quota_info(session: Session) -> dict[str, int]:
    limit = get_settings().youtube_daily_quota
    used = quota_used(session)
    return {
        "limit": limit,
        "used": used,
        "remaining": max(limit - used, 0),
        "uploads_left": max(limit - used, 0) // (UPLOAD_COST + THUMBNAIL_COST),
    }


@dataclass
class UploadPlan:
    kind: str  # long | short
    index: int | None
    file_asset: Asset
    title: str
    description: str
    tags: list[str]
    category_id: str
    language: str
    thumbnail: Asset | None
    already: Asset | None  # upload anterior do MESMO arquivo


def _long_url(session: Session, video: Video) -> str:
    return video.youtube_url


def prepare(
    session: Session, video_id: int, kind: str = "long", index: int | None = None
) -> UploadPlan:
    """Reúne e valida tudo para um upload. Erros em português, sem tocar na rede."""
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    if kind not in ("long", "short"):
        raise InvalidState("Alvo inválido. Use: long ou short.")
    account = get_account(session, channel.id or 0)
    if account is None:
        raise InvalidState(
            f"O canal “{channel.name}” não está conectado ao YouTube. Conecte na página do canal."
        )
    if account.needs_reconnect:
        raise InvalidState(
            "A autorização do YouTube deste canal expirou. Reconecte na página do canal."
        )
    if kind == "long":
        final = render_service.latest_render(session, video_id, "final")
        if final is None:
            raise InvalidState("Ainda não há render final. Gere o render final primeiro.")
        if final.meta.get("partial"):
            raise InvalidState(
                "O render final é só um teste curto (narração parcial). Gere o completo."
            )
        if video_status.stage_state(session, video, "render") != "done":
            raise InvalidState(
                "O render final está desatualizado (áudio, imagens ou timeline mudaram). Renderize de novo."
            )
        if not final.meta.get("approved_by"):
            raise InvalidState("Aprove o vídeo na revisão (cartão ④) antes de enviar ao YouTube.")
        problems = metadata_service.problems(video)
        if problems:
            raise InvalidState(" ".join(problems) + " Preencha os metadados (cartão ⑥).")
        file_asset = final
        title, description, tags = video.title, video.description, list(video.tags)
    else:
        if index is None:
            raise InvalidState("Informe qual Short enviar (index).")
        finals = {
            int(a.meta.get("index", -1)): a
            for a in reversed(shorts_render.list_shorts(session, video_id, "final"))
        }
        file_asset = finals.get(index)  # type: ignore[assignment]
        if file_asset is None:
            raise InvalidState(
                f"O Short {index + 1} não tem render final. Renderize em 1080×1920 antes de enviar."
            )
        title_base = str(file_asset.meta.get("title") or video.title)
        title = (
            title_base if len(title_base) + len(SHORT_SUFFIX) > 100 else title_base + SHORT_SUFFIX
        )
        link = _long_url(session, video)
        description = "\n\n".join(
            p
            for p in (
                str(file_asset.meta.get("title") or ""),
                f"Vídeo completo: {link}" if link else "",
                "#Shorts",
            )
            if p
        )
        tags = [*video.tags, "shorts"]
    path = get_settings().data_dir / file_asset.path
    if not path.is_file():
        raise InvalidState("O arquivo renderizado não está mais no disco. Renderize de novo.")
    already = next(
        (
            u
            for u in list_uploads(session, video_id)
            if u.meta.get("file_asset_id") == file_asset.id and u.meta.get("target") == kind
        ),
        None,
    )
    thumb = thumbnail_service.latest(session, video_id) if kind == "long" else None
    return UploadPlan(
        kind=kind,
        index=index,
        file_asset=file_asset,
        title=title[:100],
        description=description,
        tags=tags,
        category_id=channel.youtube_category_id,
        language=video.language,
        thumbnail=thumb,
        already=already,
    )


def start_upload(
    session: Session,
    video_id: int,
    actor: User,
    kind: str = "long",
    index: int | None = None,
    force: bool = False,
) -> Job:
    """Valida e enfileira o upload (sempre PRIVADO). Só pelo site/REST: nunca por tool MCP."""
    from canaisdark.models import JobType
    from canaisdark.services import jobs

    plan = prepare(session, video_id, kind, index)
    if plan.already is not None and not force:
        raise InvalidState(
            "Este arquivo já foi enviado ao YouTube "
            f"({plan.already.meta.get('url', '')}). Enviar de novo cria um vídeo duplicado: "
            "confirme o reenvio se for isso mesmo."
        )
    cost = UPLOAD_COST + (THUMBNAIL_COST if plan.thumbnail else 0)
    q = quota_info(session)
    if q["remaining"] < cost:
        raise InvalidState(
            f"Cota do YouTube de hoje quase no fim: restam {q['remaining']} de {q['limit']} unidades "
            f"e este envio gasta cerca de {cost}. Tente depois da meia-noite (horário do Pacífico)."
        )
    return jobs.create_job(
        session,
        video_id,
        JobType.YOUTUBE_UPLOAD,
        {
            "target": kind,
            "index": index,
            "force": force,
            "file_asset_id": plan.file_asset.id,
            "title": plan.title,
        },
        actor,
    )


def run_job(session: Session, job: Job, factory: ClientFactory | None = None) -> None:
    from canaisdark.services import jobs

    assert job.id is not None
    p = job.params
    kind = str(p["target"])
    plan = prepare(session, job.video_id, kind, p.get("index"))
    video = videos.get_video(session, job.video_id)
    channel = channels.get_channel(session, video.channel_id)
    path = get_settings().data_dir / plan.file_asset.path
    size = path.stat().st_size
    jobs.append_log(
        session,
        job.id,
        f"Enviando “{plan.title}” ({size / 1e6:.1f} MB) como PRIVADO para "
        f"{(get_account(session, channel.id or 0) or YouTubeAccount(channel_id=0, refresh_token_enc='')).youtube_channel_title or 'o YouTube'}.",
    )
    client = _client(factory)
    token = access_token(session, channel.id or 0, factory)
    last = [-10]

    def progress(sent: int, total: int) -> None:
        pct = int(sent * 100 / max(total, 1))
        if pct - last[0] >= 5 or pct == 100:
            last[0] = pct
            jobs.heartbeat(session, job.id or 0, min(pct, 99))

    snippet: dict[str, Any] = {
        "title": plan.title,
        "description": plan.description,
        "tags": plan.tags,
        "categoryId": plan.category_id,
        "defaultLanguage": plan.language,
        "defaultAudioLanguage": plan.language,
    }
    status_body = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    try:
        video_id = client.upload_video(
            token,
            path,
            snippet,
            status_body,
            on_progress=progress,
            should_cancel=lambda: jobs.is_cancelled(session, job.id or 0),
        )
    except yt.YouTubeError as e:
        if e.code == "invalid_grant":
            account = get_account(session, channel.id or 0)
            if account:
                account.needs_reconnect = True
                session.add(account)
                session.commit()
        raise InvalidState(str(e)) from e
    jobs.append_log(session, job.id, f"Upload concluído: id {video_id}.")
    units = UPLOAD_COST
    thumb_error = ""
    if plan.thumbnail is not None:
        try:
            data = (get_settings().data_dir / plan.thumbnail.path).read_bytes()
            client.set_thumbnail(token, video_id, data, "image/jpeg")
            units += THUMBNAIL_COST
            jobs.append_log(session, job.id, "Thumbnail enviada.")
        except (yt.YouTubeError, OSError) as e:
            thumb_error = str(e)
            jobs.append_log(session, job.id, f"Aviso: a thumbnail não foi enviada ({e}).")
    url = f"https://youtu.be/{video_id}"
    session.add(
        Asset(
            video_id=job.video_id,
            type=AssetType.UPLOAD.value,
            path=plan.file_asset.path,
            meta={
                "target": kind,
                "short_index": plan.index,
                "youtube_video_id": video_id,
                "url": url,
                "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
                "privacy": "private",
                "file_asset_id": plan.file_asset.id,
                "title": plan.title,
                "units": units,
                "thumbnail_error": thumb_error,
                "uploaded_by": job.created_by,
            },
            job_id=job.id,
        )
    )
    if kind == "long":
        video.youtube_url = url
        session.add(video)
    session.commit()
    jobs.complete(session, job.id, {"youtube_video_id": video_id, "url": url})
