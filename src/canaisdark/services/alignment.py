"""Normaliza o resultado do Forced Alignment, mapeia palavras → cenas e estima tempos quando não há alinhamento."""

import re
import statistics
from difflib import SequenceMatcher
from typing import Any

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_token(token: str) -> str:
    return _NON_WORD.sub("", token.lower())


def _tokens(text: str) -> list[str]:
    return [t for t in (normalize_token(x) for x in text.split()) if t]


def map_words_to_scenes(
    words: list[dict[str, Any]], scene_texts: list[str]
) -> list[tuple[float, float] | None]:
    """Para cada cena, (início, fim) em segundos. Casamento sequencial tolerante a divergências de tokenização."""
    flat: list[tuple[int, str]] = [
        (i, t) for i, text in enumerate(scene_texts) for t in _tokens(text)
    ]
    wnorm = [(k, normalize_token(str(w["text"]))) for k, w in enumerate(words)]
    wnorm = [(k, t) for k, t in wnorm if t]
    matcher = SequenceMatcher(None, [t for _, t in flat], [t for _, t in wnorm], autojunk=False)
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for a, b, size in matcher.get_matching_blocks():
        for off in range(size):
            scene = flat[a + off][0]
            widx = wnorm[b + off][0]
            first[scene] = min(first.get(scene, widx), widx)
            last[scene] = max(last.get(scene, widx), widx)
    spans: list[tuple[float, float] | None] = []
    for i in range(len(scene_texts)):
        if i in first:
            spans.append((float(words[first[i]]["start"]), float(words[last[i]]["end"])))
        else:
            spans.append(None)
    return _fill_gaps(spans, float(words[-1]["end"]) if words else 0.0)


def _fill_gaps(
    spans: list[tuple[float, float] | None], total: float
) -> list[tuple[float, float] | None]:
    """Cenas sem nenhuma palavra casada recebem o intervalo entre as vizinhas (dividido igualmente)."""
    out = list(spans)
    i = 0
    while i < len(out):
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is None:
            j += 1
        start = out[i - 1][1] if i > 0 and out[i - 1] else 0.0  # type: ignore[index]
        end = out[j][0] if j < len(out) and out[j] else total  # type: ignore[index]
        step = (end - start) / (j - i) if j > i else 0
        for k in range(i, j):
            out[k] = (start + step * (k - i), start + step * (k - i + 1))
        i = j
    return out


def loss_threshold(words: list[dict[str, Any]]) -> float:
    """Heurística: palavra suspeita se loss > max(0,2; 3 × mediana)."""
    losses = [float(w.get("loss", 0)) for w in words]
    return max(0.2, 3 * statistics.median(losses)) if losses else 0.2


def low_confidence_words(words: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    threshold = loss_threshold(words)
    bad = [w for w in words if float(w.get("loss", 0)) > threshold]
    return sorted(bad, key=lambda w: -float(w["loss"]))[:limit]


def estimate_scene_times(scene_texts: list[str], total_seconds: float) -> list[tuple[float, float]]:
    """Sem alinhamento: reparte a duração do áudio proporcionalmente a palavras (+ peso de pontuação)."""
    weights = []
    for text in scene_texts:
        w = (
            len(text.split())
            + 0.6 * len(re.findall(r"[.!?…]", text))
            + 0.3 * len(re.findall(r"[,;:]", text))
        )
        weights.append(max(w, 1.0))
    scale = total_seconds / sum(weights) if weights else 0
    spans, cursor = [], 0.0
    for w in weights:
        spans.append((cursor, cursor + w * scale))
        cursor += w * scale
    return spans


def words_from_scene_spans(
    scene_texts: list[str], spans: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """Palavras sintéticas (tempo repartido por tamanho dentro de cada cena) para gerar legendas estimadas."""
    out: list[dict[str, Any]] = []
    for i, (text, (start, end)) in enumerate(zip(scene_texts, spans, strict=True)):
        toks = text.split()
        weights = [max(1, len(t)) for t in toks]
        scale = (end - start) / sum(weights) if weights else 0
        cursor = start
        for tok, w in zip(toks, weights, strict=True):
            out.append(
                {
                    "text": tok,
                    "start": round(cursor, 3),
                    "end": round(cursor + w * scale, 3),
                    "loss": 0.0,
                    "scene": i,
                }
            )
            cursor += w * scale
    return out


def build_alignment_doc(
    timing: str,
    duration: float,
    words: list[dict[str, Any]],
    scene_texts: list[str],
    spans: list[tuple[float, float]],
    loss: float | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "timing": timing,  # "forced" | "estimated"
        "duration": round(duration, 3),
        "loss": loss,
        "words": [
            {"text": w["text"], "start": w["start"], "end": w["end"], "loss": w.get("loss", 0.0)}
            for w in words
        ],
        "scenes": [
            {"index": i, "start": round(s, 3), "end": round(e, 3), "text": scene_texts[i]}
            for i, (s, e) in enumerate(spans)
        ],
        "low_confidence": [] if timing == "estimated" else low_confidence_words(words),
    }
