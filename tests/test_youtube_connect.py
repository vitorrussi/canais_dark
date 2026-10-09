import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from canaisdark.config import get_settings
from canaisdark.integrations import youtube as yt
from canaisdark.models import User
from canaisdark.schemas.channel import ChannelCreate
from canaisdark.services import channels, crypto
from canaisdark.services import youtube as yt_service
from canaisdark.services.errors import InvalidState


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> yt.FakeYouTube:
    s = get_settings()
    monkeypatch.setattr(s, "youtube_fake", True)
    monkeypatch.setattr(s, "fernet_key", Fernet.generate_key().decode())
    f = yt.FakeYouTube()
    monkeypatch.setattr(yt, "_demo", f)
    return f


@pytest.fixture
def chan(session: Session) -> str:
    return channels.create_channel(session, ChannelCreate(name="Canal YT")).slug


def test_checklist_lists_missing_configuration(
    client: TestClient, user_token: tuple[User, str], chan: str
) -> None:
    client.post("/login", data={"token": user_token[1]})
    page = client.get(f"/channels/{chan}?tab=settings").text
    assert "Falta configurar" in page and "GOOGLE_CLIENT_ID" in page and "FERNET_KEY" in page
    assert "/oauth/youtube/callback" in page and "Conectar YouTube" not in page
    r = client.get(f"/channels/{chan}/youtube/connect", follow_redirects=True)
    assert "Falta configurar" in r.text


def test_connect_flow_stores_encrypted_token(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    chan: str,
    fake: yt.FakeYouTube,
) -> None:
    client.post("/login", data={"token": user_token[1]})
    page = client.get(f"/channels/{chan}?tab=settings").text
    assert "Conectar YouTube" in page and "modo demonstração" in page.lower()
    r = client.get(f"/channels/{chan}/youtube/connect", follow_redirects=True)
    assert "YouTube conectado: Canal Fake" in r.text
    channel = channels.get_channel_by_slug(session, chan)
    account = yt_service.get_account(session, channel.id or 0)
    assert account and account.youtube_channel_id == "UCfake0001"
    assert account.refresh_token_enc != "fake-refresh" and "fake" not in account.refresh_token_enc
    assert crypto.decrypt(account.refresh_token_enc) == "fake-refresh"
    assert "fake-refresh" not in client.get(f"/channels/{chan}?tab=settings").text
    dis = client.post(f"/channels/{chan}/youtube/disconnect", follow_redirects=True)
    assert "YouTube desconectado" in dis.text
    assert fake.revoked == ["fake-refresh"]
    assert yt_service.get_account(session, channel.id or 0) is None


def test_state_is_signed_expiring_and_user_bound(
    session: Session,
    user_token: tuple[User, str],
    chan: str,
    fake: yt.FakeYouTube,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = user_token[0]
    channel = channels.get_channel_by_slug(session, chan)
    state = yt_service.make_state(channel.id or 0, user.id or 0)
    assert yt_service.read_state(state) == (channel.id, user.id)
    with pytest.raises(InvalidState, match="inválido"):
        yt_service.read_state(state[:-3] + "xyz")
    other = User(name="Outra", email="o@o.com", token_hash="x")
    session.add(other)
    session.commit()
    session.refresh(other)
    with pytest.raises(InvalidState, match="outra pessoa"):
        yt_service.complete_connection(session, "fake-code", state, other)
    monkeypatch.setattr(yt_service, "STATE_MAX_AGE_S", -1)
    with pytest.raises(InvalidState, match="demorou"):
        yt_service.read_state(state)


def test_invalid_grant_marks_reconnect_and_denied_callback(
    client: TestClient,
    session: Session,
    user_token: tuple[User, str],
    chan: str,
    fake: yt.FakeYouTube,
) -> None:
    client.post("/login", data={"token": user_token[1]})
    client.get(f"/channels/{chan}/youtube/connect", follow_redirects=True)
    channel = channels.get_channel_by_slug(session, chan)

    class Revoked(yt.FakeYouTube):
        def refresh_access_token(self, refresh_token: str) -> str:
            raise yt.YouTubeError("revogado", "invalid_grant")

    with pytest.raises(InvalidState, match="revogado"):
        yt_service.access_token(session, channel.id or 0, Revoked)
    account = yt_service.get_account(session, channel.id or 0)
    assert account and account.needs_reconnect
    page = client.get(f"/channels/{chan}?tab=settings").text
    assert "reconectar" in page.lower()
    state = yt_service.make_state(channel.id or 0, user_token[0].id or 0)
    denied = client.get(
        f"/oauth/youtube/callback?error=access_denied&state={state}", follow_redirects=True
    )
    assert "cancelou" in denied.text


def test_category_setting(
    client: TestClient, session: Session, user_token: tuple[User, str], chan: str
) -> None:
    client.post("/login", data={"token": user_token[1]})
    client.post(f"/channels/{chan}/youtube/category", data={"category_id": "27"})
    assert channels.get_channel_by_slug(session, chan).youtube_category_id == "27"
    r = client.post(
        f"/channels/{chan}/youtube/category", data={"category_id": "999"}, follow_redirects=True
    )
    assert "Categoria desconhecida" in r.text
