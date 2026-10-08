from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from canaisdark.config import get_settings

_engine: Engine | None = None


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    if is_sqlite and ":memory:" not in url:
        path = url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
