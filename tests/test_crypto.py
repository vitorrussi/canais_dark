import pytest
from cryptography.fernet import Fernet

from canaisdark.config import get_settings
from canaisdark.services import crypto
from canaisdark.services.errors import InvalidState


def test_round_trip_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "fernet_key", "")
    assert not crypto.configured()
    with pytest.raises(InvalidState, match="FERNET_KEY não está configurada"):
        crypto.encrypt("x")
    monkeypatch.setattr(get_settings(), "fernet_key", "isto-nao-e-uma-chave")
    with pytest.raises(InvalidState, match="inválida"):
        crypto.encrypt("x")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "fernet_key", key)
    token = crypto.encrypt("refresh-secret")
    assert "refresh-secret" not in token and crypto.decrypt(token) == "refresh-secret"
    monkeypatch.setattr(get_settings(), "fernet_key", Fernet.generate_key().decode())
    with pytest.raises(InvalidState, match="Reconecte"):
        crypto.decrypt(token)
