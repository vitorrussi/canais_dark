"""Gera legendas .srt e .ass a partir de palavras com tempo."""

from typing import Any

MAX_LINE = 42
MAX_CUE_SECONDS = 5.0
MIN_CUE_SECONDS = 1.0


def _cues(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    buf: list[dict[str, Any]] = []

    def flush() -> None:
        if not buf:
            return
        start, end = float(buf[0]["start"]), float(buf[-1]["end"])
        if end - start < MIN_CUE_SECONDS:
            end = start + MIN_CUE_SECONDS
        cues.append((start, end, _wrap(" ".join(str(w["text"]) for w in buf))))
        buf.clear()

    for w in words:
        buf.append(w)
        text = " ".join(str(x["text"]) for x in buf)
        duration = float(w["end"]) - float(buf[0]["start"])
        sentence_end = str(w["text"]).rstrip("\"')").endswith((".", "!", "?", "…"))
        if (
            len(text) >= MAX_LINE * 2 - 8
            or duration >= MAX_CUE_SECONDS
            or (sentence_end and len(text) >= 20)
        ):
            flush()
    flush()
    return cues


def _wrap(text: str) -> str:
    """Até 2 linhas, equilibradas, com no máximo ~42 caracteres cada."""
    if len(text) <= MAX_LINE:
        return text
    words = text.split()
    best, best_diff = text, len(text)
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if diff < best_diff and max(len(a), len(b)) <= MAX_LINE + 6:
            best, best_diff = f"{a}\n{b}", diff
    return best


def _srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(words: list[dict[str, Any]]) -> str:
    out = []
    for i, (start, end, text) in enumerate(_cues(words), 1):
        out.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(out)


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,80,80,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def to_ass(words: list[dict[str, Any]]) -> str:
    lines = [ASS_HEADER.rstrip("\n")]
    for start, end, text in _cues(words):
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text.replace(chr(10), chr(92) + 'N')}"
        )
    return "\n".join(lines) + "\n"


# ----- Shorts: legendas grandes, palavra a palavra, com destaque da palavra falada -----

SHORT_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Short,Arial,104,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,9,2,2,70,70,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
SHORT_HIGHLIGHT = "&H0000D4FF&"  # amarelo (BGR)
SHORT_WORDS_PER_CUE = 3
SHORT_MAX_CHARS = 24


def _short_groups(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Agrupa 2–3 palavras por vez, fechando o grupo em fim de frase ou quando fica longo."""
    groups: list[list[dict[str, Any]]] = []
    buf: list[dict[str, Any]] = []
    for w in words:
        buf.append(w)
        text = " ".join(str(x["text"]) for x in buf)
        ends = str(w["text"]).rstrip("\"')").endswith((".", "!", "?", "…", ",", ";", ":"))
        if len(buf) >= SHORT_WORDS_PER_CUE or len(text) >= SHORT_MAX_CHARS or ends:
            groups.append(buf)
            buf = []
    if buf:
        groups.append(buf)
    return groups


def _safe(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass_shorts(words: list[dict[str, Any]], start: float, end: float) -> str:
    """ASS 1080×1920 só do trecho [start, end] da narração, com os tempos já deslocados para 0."""
    inside = [
        {
            **w,
            "start": max(float(w["start"]), start) - start,
            "end": min(float(w["end"]), end) - start,
        }
        for w in words
        if float(w["end"]) > start and float(w["start"]) < end and str(w["text"]).strip()
    ]
    lines = [SHORT_ASS_HEADER.rstrip("\n")]
    total = end - start
    for group in _short_groups(inside):
        for k, current in enumerate(group):
            t0 = float(current["start"])
            nxt = float(group[k + 1]["start"]) if k + 1 < len(group) else float(current["end"])
            t1 = max(nxt, t0 + 0.08)
            if k + 1 == len(group):
                t1 = max(t1, float(current["end"]))
            pieces = []
            for j, w in enumerate(group):
                word = _safe(str(w["text"])).upper()
                if j == k:
                    pieces.append(f"{{\\c{SHORT_HIGHLIGHT}}}{word}{{\\c&H00FFFFFF&}}")
                else:
                    pieces.append(word)
            lines.append(
                f"Dialogue: 0,{_ass_time(t0)},{_ass_time(min(t1, total))},Short,,0,0,0,,"
                + " ".join(pieces)
            )
    return "\n".join(lines) + "\n"
