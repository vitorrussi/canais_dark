import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import drive as gd
from canaisdark.models import User
from canaisdark.schemas.channel import ChannelCreate, ChannelUpdate
from canaisdark.services import channels, crypto
from canaisdark.services import drive as drive_service
from canaisdark.services.errors import InvalidState


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> gd.FakeDrive:
    s = get_settings()
    monkeypatch.setattr(s, "drive_fake", True)
    monkeypatch.setattr(s, "fernet_key", Fernet.generate_key().decode())
    f = gd.FakeDrive()
    monkeypatch.setattr(gd, "_demo", f)
    return f


@pytest.fixture
def chan(session: Session) -> str:
    return channels.create_channel(session, ChannelCreate(name="Canal Drive")).slug


def test_checklist_and_blocked_connect(
    client: TestClient, user_token: tuple[User, str], chan: str
) -> None:
    client.post("/login", data={"token": user_token[1]})
    page = client.get(f"/channels/{chan}?tab=settings").text
    assert "Google Drive (cópia" in page and "Falta configurar" in page
    assert "/oauth/drive/callback" in page and "Conectar Google Drive" not in page
    r = client.get(f"/channels/{chan}/drive/connect", follow_redirects=True)
    assert "Falta configurar" in r.text


def test_connect_flow_encrypts_and_disconnects(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    chan: str,
    fake: gd.FakeDrive,
) -> None:
    client.post("/login", data={"token": user_token[1]})
    r = client.get(f"/channels/{chan}/drive/connect", follow_redirects=True)
    assert "Google Drive conectado (fake@example.com)" in r.text
    channel = channels.get_channel_by_slug(session, chan)
    acc = drive_service.get_account(session, channel.id or 0)
    assert acc and acc.account_email == "fake@example.com"
    assert "fake-drive-refresh" not in acc.refresh_token_enc
    assert crypto.decrypt(acc.refresh_token_enc) == "fake-drive-refresh"
    assert "fake-drive-refresh" not in client.get(f"/channels/{chan}?tab=settings").text
    out = client.post(f"/channels/{chan}/drive/disconnect", follow_redirects=True)
    assert "Google Drive desconectado" in out.text and fake.revoked == ["fake-drive-refresh"]
    assert drive_service.get_account(session, channel.id or 0) is None


def test_state_is_separate_from_youtube_and_user_bound(
    session: Session, user_token: tuple[User, str], chan: str, fake: gd.FakeDrive
) -> None:
    from canaisdark.services import youtube as yt_service

    channel = channels.get_channel_by_slug(session, chan)
    user = user_token[0]
    yt_state = yt_service.make_state(channel.id or 0, user.id or 0)
    with pytest.raises(InvalidState, match="inválido"):
        drive_service.read_state(yt_state)  # salt diferente: não vale entre serviços
    state = drive_service.make_state(channel.id or 0, user.id or 0)
    other = User(name="O", email="o@o.com", token_hash="x")
    session.add(other)
    session.commit()
    session.refresh(other)
    with pytest.raises(InvalidState, match="outra pessoa"):
        drive_service.complete_connection(session, "fake-code", state, other)


def test_channel_form_keeps_drive_folder_id(
    client: TestClient, session: Session, user_token: tuple[User, str], chan: str
) -> None:
    client.post("/login", data={"token": user_token[1]})
    channel = channels.get_channel_by_slug(session, chan)
    channels.update_channel(session, channel.id or 0, ChannelUpdate(drive_folder_id="FOLDER123"))
    page = client.get(f"/channels/{chan}?tab=settings").text
    assert 'name="drive_folder_id" value="FOLDER123"' in page and "Criada pelo sistema" in page
    client.post(f"/channels/{chan}", data={"name": "Canal Drive", "drive_folder_id": "FOLDER123"})
    assert channels.get_channel_by_slug(session, chan).drive_folder_id == "FOLDER123"
