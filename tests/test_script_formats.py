import json

import pytest

from canaisdark.models import Channel, Scene, ScriptVersion, Video
from canaisdark.services import script_formats as sf
from canaisdark.services.errors import InvalidState


def _objs() -> tuple[ScriptVersion, list[Scene], Channel, Video]:
    ch = Channel(id=1, name="Canal", slug="canal")
    v = Video(id=7, channel_id=1, topic="t", created_by=1)
    ver = ScriptVersion(id=1, video_id=7, number=3, full_text="", author="user:1", approved=True)
    scenes = [
        Scene(video_id=7, script_version_id=1, index=0, text="Um", notes="close"),
        Scene(video_id=7, script_version_id=1, index=1, text="Dois"),
    ]
    return ver, scenes, ch, v


def test_render_formats() -> None:
    ver, scenes, ch, v = _objs()
    txt = sf.render_script("txt", "Meu vídeo", ver, scenes, ch, v)
    assert txt.filename == "canal-7-v3.txt" and txt.content == "Um\n\nDois\n"
    md = sf.render_script("md", "Meu vídeo", ver, scenes, ch, v).content
    assert md.startswith("# Meu vídeo") and "## Cena 1\n\nUm\n\n> close" in md and "## Cena 2" in md
    data = json.loads(sf.render_script("json", "Meu vídeo", ver, scenes, ch, v).content)
    assert data["format"] == "canaisdark.script" and data["scenes"][0]["notes"] == "close"
    with pytest.raises(InvalidState):
        sf.render_script("pdf", "x", ver, scenes, ch, v)


def test_md_roundtrip_via_headings() -> None:
    ver, scenes, ch, v = _objs()
    md = sf.render_script("md", "Meu vídeo", ver, scenes, ch, v).content
    back = sf.text_to_scenes(md, "auto")
    assert [s.text for s in back] == ["Um", "Dois"] and back[0].notes == "close"


def test_split_rules() -> None:
    text = "Era noite. Ninguém viu.\n\nO farol apagou! Quem foi?"
    assert [s.text for s in sf.text_to_scenes(text, "blocks")] == [
        "Era noite. Ninguém viu.",
        "O farol apagou! Quem foi?",
    ]
    assert len(sf.text_to_scenes(text, "sentences")) == 4
    assert len(sf.text_to_scenes("a\nb\nc", "lines")) == 3
    grouped = sf.text_to_scenes(
        "Frase um aqui. Frase dois aqui. Frase três aqui. Frase quatro aqui.", "chars", max_chars=50
    )
    assert all(len(s.text) <= 50 for s in grouped) and len(grouped) == 2
    assert sf.detect_rule("Cena 1: abertura\nTexto") == "headings"
    assert sf.detect_rule("a\n\nb") == "blocks" and sf.detect_rule("Só um bloco.") == "sentences"
    h = sf.text_to_scenes("Cena 1: Abertura forte\nContinua aqui\nCena 2\nSegunda cena", "headings")
    assert [s.text for s in h] == ["Abertura forte Continua aqui", "Segunda cena"]


def test_errors() -> None:
    with pytest.raises(InvalidState):
        sf.text_to_scenes("   ", "auto")
    with pytest.raises(InvalidState):
        sf.text_to_scenes("abc", "inexistente")
    with pytest.raises(InvalidState):
        sf.text_to_scenes("abc. def.", "chars", max_chars=10)
    with pytest.raises(InvalidState):
        sf.decode_upload(b"x" * (sf.MAX_UPLOAD_BYTES + 1))


def test_parse_input_json_and_latin1() -> None:
    raw = json.dumps(
        {
            "format": "canaisdark.script",
            "scenes": [{"text": "A", "notes": "n"}, {"text": " "}, {"text": "B"}],
        }
    ).encode()
    scenes = sf.parse_input("", "x.json", raw)
    assert [s.text for s in scenes] == ["A", "B"]
    assert sf.parse_input("", "x.txt", "Olá\n\nMundo".encode("latin-1"))[0].text == "Olá"
    assert [s.text for s in sf.parse_input('["a", "b"]', "l.json", b'["a", "b"]')] == ["a", "b"]
    assert sf.parse_input("Colado\n\nTexto", None, None)[1].text == "Texto"


def test_summarize() -> None:
    s = sf.text_to_scenes("um dois três quatro cinco\n\nseis", "blocks")
    assert sf.summarize(s) == {"scenes": 2, "words": 6, "duration": "0:02", "too_long": []}
