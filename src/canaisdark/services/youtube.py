"""YouTube por canal: conexão OAuth, tokens criptografados e (adiante) upload privado com cota."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.integrations import youtube as yt
from canaisdark.models import Channel, User, YouTubeAccount
from canaisdark.models.channel import utcnow
from canaisdark.services import channels, crypto
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
