import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from canaisdark.config import get_settings
from canaisdark.models import ClaudeTask, TaskStatus, User, Video
from canaisdark.services import offline_tasks, scripts
from canaisdark.services.errors import DomainError, InvalidState
from tests.media import setup_video


@pytest.fixture
def vid(
    session: Session, user_token: tuple[User, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "files")
    return setup_video(session, user_token[0], [10.0, 10.0, 10.0])["video_id"]


def _task(session: Session, vid: int, kind: str) -> ClaudeTask:
    return session.exec(
        select(ClaudeTask).where(ClaudeTask.video_id == vid, ClaudeTask.type == kind)
    ).one()


def test_prompt_has_context_format_and_no_tool_calls(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    for kind in offline_tasks.SUPPORTED:
        text = offline_tasks.build_prompt(session, vid, kind, user_token[0])
        assert "SOMENTE com um bloco de código JSON" in text
        assert "Formato da resposta" in text and "Canal Teste" in text
        assert "save_" not in text.split("## Contexto")[0]
        assert "complete_task" not in text.split("## Contexto")[0]


def test_extract_json_variants() -> None:
    assert offline_tasks.extract_json('{"a": 1}') == {"a": 1}
    assert offline_tasks.extract_json('Claro!\n```json\n{"a": 1}\n```\nFim') == {"a": 1}
    assert offline_tasks.extract_json('texto antes {"a": [1, 2]} depois') == {"a": [1, 2]}
    assert offline_tasks.extract_json("[1, 2]") == [1, 2]
    with pytest.raises(InvalidState, match="Não consegui ler"):
        offline_tasks.extract_json("sem json aqui")
    with pytest.raises(InvalidState, match="Cole a resposta"):
        offline_tasks.extract_json("   ")


def test_apply_script_creates_version_and_completes_task(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    resp = '```json\n{"scenes": [{"text": "Primeira."}, {"text": "Segunda."}]}\n```'
    out = offline_tasks.apply_result(session, vid, "generate_script", user, resp)
    assert out["version"] == 2 and "2 cenas" in out["summary"]
    task = _task(session, vid, "generate_script")
    assert task.status == TaskStatus.DONE.value and "copiar/colar" in task.result_summary
    _, scenes = scripts.get_script(session, vid)
    assert [s.text for s in scenes] == ["Primeira.", "Segunda."]


def test_apply_accepts_bare_list(session: Session, vid: int, user_token: tuple[User, str]) -> None:
    out = offline_tasks.apply_result(
        session, vid, "generate_script", user_token[0], '[{"text": "Só uma."}]'
    )
    assert out["version"] == 2


def test_invalid_response_saves_nothing_and_keeps_task_pending(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    offline_tasks.build_prompt(session, vid, "generate_script", user)
    with pytest.raises(InvalidState, match="Roteiro inválido"):
        offline_tasks.apply_result(session, vid, "generate_script", user, '{"scenes": [{"x": 1}]}')
    assert _task(session, vid, "generate_script").status == TaskStatus.PENDING.value
    assert scripts.get_script(session, vid)[0].number == 1


def test_domain_error_releases_task(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    bad = '{"prompts": [{"scene_index": 99, "image_prompt": "x"}]}'
    with pytest.raises(DomainError):
        offline_tasks.apply_result(session, vid, "generate_prompts", user, bad)
    assert _task(session, vid, "generate_prompts").status == TaskStatus.PENDING.value


def test_apply_prompts_metadata_shorts(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    user = user_token[0]
    p = offline_tasks.apply_result(
        session,
        vid,
        "generate_prompts",
        user,
        json.dumps(
            {"prompts": [{"scene_index": i, "image_prompt": f"scene {i}"} for i in range(3)]}
        ),
    )
    assert p["saved"] == 3 and p["missing_scene_indexes"] == []
    m = offline_tasks.apply_result(
        session,
        vid,
        "generate_metadata",
        user,
        '{"title": "Os guardiões", "description": "d", "tags": ["a"], "thumbnail_ideas": []}',
    )
    assert m["version"] == 1
    s = offline_tasks.apply_result(
        session,
        vid,
        "select_shorts",
        user,
        '{"shorts": [{"exact_text": "Cena número 1. Cena número 2.", "title": "Abertura"}]}',
    )
    assert s["version"] == 1
    video = session.get(Video, vid)
    assert video is not None and video.title == ""  # nada é aplicado/aprovado sozinho


def test_apply_timeline_uses_default(
    session: Session, vid: int, user_token: tuple[User, str]
) -> None:
    from canaisdark.services import timeline as tl

    video = session.get(Video, vid)
    assert video is not None
    default = tl.build_default(session, video).model_dump(mode="json")
    out = offline_tasks.apply_result(
        session, vid, "generate_timeline", user_token[0], json.dumps({"timeline": default})
    )
    assert out["version"] == 1


def test_reuses_open_task(session: Session, vid: int, user_token: tuple[User, str]) -> None:
    user = user_token[0]
    before = {t.id for t in session.exec(select(ClaudeTask)).all()}
    offline_tasks.build_prompt(
        session, vid, "generate_script", user
    )  # já há uma aberta (criar vídeo)
    offline_tasks.build_prompt(session, vid, "generate_metadata", user)  # esta é criada
    after = session.exec(select(ClaudeTask)).all()
    assert before <= {t.id for t in after} and len(after) == len(before) + 1
