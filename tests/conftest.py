from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from canaisdark import db
from canaisdark.main import create_app
from canaisdark.models import User
from canaisdark.services import users


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Testes não podem depender do .env do desenvolvedor (ex.: PUBLIC_URL deixa o cookie Secure)."""
    from canaisdark.config import get_settings

    for key, value in {
        "ENV": "dev",
        "PUBLIC_URL": "",
        "MCP_ALLOWED_HOSTS": "",
        "SECRET_KEY": "dev-insecure-change-me",
        "ELEVENLABS_API_KEY": "",
        "ELEVENLABS_FAKE": "false",
        "FORCED_ALIGNMENT_ENABLED": "true",
        "ELEVENLABS_MAX_CHARS_PER_JOB": "2500",
        "TTS_CHUNK_CHARS": "2500",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "_engine", engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(session: Session) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def user_token(session: Session) -> tuple[User, str]:
    user = users.create_user(session, "Teste", "teste@example.com")
    return user, users.issue_token(session, user)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
