from canaisdark.services import alignment, subtitles


def W(text: str, start: float, end: float, loss: float = 0.01) -> dict[str, object]:
    return {"text": text, "start": start, "end": end, "loss": loss}


def test_map_words_to_scenes_exact() -> None:
    words = [W("Era", 0, 0.3), W("noite.", 0.3, 0.9), W("Ninguém", 1.0, 1.5), W("viu!", 1.5, 2.0)]
    assert alignment.map_words_to_scenes(words, ["Era noite.", "Ninguém viu!"]) == [
        (0.0, 0.9),
        (1.0, 2.0),
    ]


def test_map_words_tolerates_tokenization_differences() -> None:
    # a API separou "farol," em "farol" e "," e fundiu "de" + "repente"
    words = [
        W("O", 0, 0.1),
        W("farol", 0.1, 0.5),
        W(",", 0.5, 0.5),
        W("apagou", 0.6, 1.0),
        W("derepente", 1.0, 1.6),
        W("Fim", 2.0, 2.4),
    ]
    spans = alignment.map_words_to_scenes(words, ["O farol, apagou", "de repente", "Fim"])
    assert spans[0] == (0.0, 1.0) and spans[2] == (2.0, 2.4)
    assert (
        spans[1] is not None and 1.0 <= spans[1][0] <= spans[1][1] <= 2.0
    )  # cena sem casamento ocupa a lacuna


def test_missing_scene_gets_gap_and_empty_words() -> None:
    words = [W("a", 0, 1), W("c", 3, 4)]
    spans = alignment.map_words_to_scenes(words, ["a", "xxx yyy", "c"])
    assert spans == [(0.0, 1.0), (1.0, 3.0), (3.0, 4.0)]
    assert alignment.map_words_to_scenes([], ["a"]) == [(0.0, 0.0)]


def test_low_confidence_heuristic() -> None:
    words = [W(f"p{i}", i, i + 1, 0.02) for i in range(20)] + [
        W("estranha", 20, 21, 0.9),
        W("meio", 21, 22, 0.1),
    ]
    bad = alignment.low_confidence_words(words)
    assert [w["text"] for w in bad] == ["estranha"]  # 0.1 < 3×mediana? (mediana 0.02 → limiar 0.2)
    assert alignment.low_confidence_words([]) == []


def test_estimate_and_synthetic_words() -> None:
    texts = ["Uma frase curta.", "Outra frase bem mais longa que a primeira, com vírgula."]
    spans = alignment.estimate_scene_times(texts, 10.0)
    assert (
        spans[0][0] == 0
        and spans[-1][1] == 10.0
        and (spans[1][1] - spans[1][0]) > (spans[0][1] - spans[0][0])
    )
    words = alignment.words_from_scene_spans(texts, spans)
    assert (
        len(words) == 3 + 10
        and words[0]["start"] == 0
        and words[-1]["end"] == 10.0
        and words[3]["scene"] == 1
    )
    doc = alignment.build_alignment_doc("estimated", 10.0, words, texts, spans)
    assert doc["timing"] == "estimated" and doc["low_confidence"] == [] and len(doc["scenes"]) == 2


def test_srt_and_ass() -> None:
    words = [
        W(t, i * 0.4, i * 0.4 + 0.35)
        for i, t in enumerate(
            "Era uma noite escura e ninguém sabia o que fazer. Então o farol apagou de repente.".split()
        )
    ]
    srt = subtitles.to_srt(words)
    blocks = srt.strip().split("\n\n")
    assert blocks[0].startswith("1\n00:00:00,000 --> ") and len(blocks) >= 2
    for b in blocks:
        lines = b.split("\n")
        assert len(lines) in (3, 4) and all(len(x) <= 50 for x in lines[2:])
    ass = subtitles.to_ass(words)
    assert (
        "[Events]" in ass and "Dialogue: 0,0:00:00.00," in ass and "Style: Default,Arial,64" in ass
    )
    assert (
        subtitles._srt_time(3725.5) == "01:02:05,500" and subtitles._ass_time(61.25) == "0:01:01.25"
    )


def test_short_cue_gets_min_duration() -> None:
    srt = subtitles.to_srt([W("Oi.", 0, 0.2)])
    assert "00:00:00,000 --> 00:00:01,000" in srt


def test_clean_words_drops_whitespace_tokens_from_real_api_shape() -> None:
    # formato real observado: espaços vêm como "palavras" com tempos próprios
    real = [
        W("Abril", 0.04, 0.36, 0.74),
        W(" ", 0.36, 0.4, 0.61),
        W("de", 0.4, 0.48, 0.76),
        W(" ", 0.48, 0.52, 0.55),
        W("1986.", 0.52, 2.08, 1.26),
    ]
    cleaned = alignment.clean_words(real)
    assert [w["text"] for w in cleaned] == ["Abril", "de", "1986."]
    assert alignment.map_words_to_scenes(cleaned, ["Abril de 1986."]) == [(0.04, 2.08)]
    srt = subtitles.to_srt(cleaned)
    assert "Abril de 1986." in srt and "  " not in srt


def test_shorts_ass_is_big_highlighted_and_shifted() -> None:
    words = [
        W("Antes", 9.0, 9.4),
        W("O", 10.0, 10.2),
        W("farol", 10.2, 10.8),
        W("ficava", 10.8, 11.3),
        W("longe.", 11.3, 12.0),
        W("Depois", 12.0, 12.5),
        W("fim", 30.0, 30.5),
    ]
    out = subtitles.to_ass_shorts(words, 10.0, 12.5)
    assert "PlayResX: 1080" in out and "PlayResY: 1920" in out
    dialogues = [ln for ln in out.splitlines() if ln.startswith("Dialogue")]
    assert "ANTES" not in out and "FIM" not in out  # só o trecho
    assert dialogues[0].startswith("Dialogue: 0,0:00:00.00,")  # deslocado para 0
    assert "{\\c&H0000D4FF&}O{" in dialogues[0] and "FAROL" in dialogues[0]
    assert "{\\c&H0000D4FF&}FAROL" in dialogues[1]
