from canaisdark.services import script_diff, script_text


def test_counts_and_duration() -> None:
    assert script_text.word_count("um dois  três\nquatro") == 4
    assert script_text.estimated_seconds(150) == 60
    assert script_text.format_duration(462) == "7:42"


def test_clean_text() -> None:
    raw = "Olá   “mundo”… tudo\tbem  \n\n\n\nFim ’ok’"
    assert script_text.clean_text(raw) == "Olá \"mundo\"... tudo bem\n\nFim 'ok'"


def test_split_blocks_joins_inner_lines() -> None:
    text = "Primeira linha\nsegunda linha\n\n\nOutro bloco\n  \nTerceiro"
    assert script_text.split_blocks(text) == [
        "Primeira linha segunda linha",
        "Outro bloco",
        "Terceiro",
    ]
    assert script_text.split_blocks("   ") == []


def test_split_sentences() -> None:
    parts = script_text.split_sentences("Era noite. Ninguém sabia! Mas ele viu? Sim, viu.")
    assert parts == ["Era noite.", "Ninguém sabia!", "Mas ele viu?", "Sim, viu."]
    assert script_text.split_sentences("Sem pontuação final") == ["Sem pontuação final"]


def test_diff_scenes() -> None:
    old = ["A", "B velho texto", "C", "D"]
    new = ["A", "B novo texto", "E", "C"]
    rows = script_diff.diff_scenes(old, new)
    kinds = [r.kind for r in rows]
    assert kinds == ["same", "changed", "added", "same", "removed"]
    changed = rows[1]
    assert ("del", "velho") in changed.segments and ("ins", "novo") in changed.segments
    assert script_diff.summarize(rows) == {"same": 2, "changed": 1, "added": 1, "removed": 1}
