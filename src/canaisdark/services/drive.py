"""Google Drive por canal: conexão OAuth (escopo drive.file), pastas criadas pelo app e sincronização."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.models import Channel, DriveAccount, User
from canaisdark.models.channel import utcnow
from canaisdark.services import channels, crypto
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
