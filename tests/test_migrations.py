import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from canaisdark import db
from canaisdark.config import get_settings


def test_upgrade_with_existing_rows_keeps_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressão: a migração em lote do SQLite falhava com FKs ligadas quando havia linhas dependentes."""
    path = tmp_path / "mig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    monkeypatch.setattr(db, "_engine", None)
    cfg = Config("alembic.ini")

    command.upgrade(cfg, "35aa92c14e4f")  # antes de user.is_admin
    con = sqlite3.connect(path)
    con.execute(
        "insert into user(name,email,is_active,created_at) values ('A','a@a.com',1,'2026-01-01'),('B','b@b.com',1,'2026-01-02')"
    )
    con.execute(
        "insert into channel(name,slug,description,youtube_url,language,extra_languages,elevenlabs_voice_id,visual_style_prompt,script_template,target_duration_minutes,words_per_scene,script_rules,default_script_instructions,drive_folder_id,is_archived,created_at,updated_at) values ('C','c','','','pt-BR','[]','','','',8,40,'','','',0,'2026-01-01','2026-01-01')"
    )
    con.execute(
        "insert into video(channel_id,topic,status,title,description,tags,language,drive_url,youtube_url,created_by,created_at,updated_at) values (1,'t','draft','','','[]','pt-BR','','',1,'2026-01-01','2026-01-01')"
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(path)
    assert con.execute("select name,is_admin from user order by id").fetchall() == [
        ("A", 1),
        ("B", 0),
    ]
    assert con.execute("select count(*) from video").fetchone() == (1,)
    assert con.execute("pragma foreign_key_check").fetchall() == []
    assert con.execute("select version_num from alembic_version").fetchone()[0] != "35aa92c14e4f"
    con.close()
