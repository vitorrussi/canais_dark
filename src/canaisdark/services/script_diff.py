"""Comparação entre duas versões de roteiro (por cena, com diff de palavras nas alteradas)."""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class DiffRow:
    kind: str  # same | changed | added | removed
    old_index: int | None = None
    new_index: int | None = None
    old_text: str = ""
    new_text: str = ""
    segments: list[tuple[str, str]] = field(default_factory=list)  # (equal|ins|del, texto)


def _tokens(text: str) -> list[str]:
    return re.findall(r"\s+|\S+", text)


def inline_segments(old: str, new: str) -> list[tuple[str, str]]:
    a, b = _tokens(old), _tokens(new)
    segments: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            segments.append(("equal", "".join(a[i1:i2])))
            continue
        if i2 > i1:
            segments.append(("del", "".join(a[i1:i2])))
        if j2 > j1:
            segments.append(("ins", "".join(b[j1:j2])))
    return segments


def diff_scenes(old: list[str], new: list[str]) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [
                DiffRow("same", i, j, old[i], new[j])
                for i, j in zip(range(i1, i2), range(j1, j2), strict=True)
            ]
            continue
        pairs = min(i2 - i1, j2 - j1)
        for k in range(pairs):
            i, j = i1 + k, j1 + k
            rows.append(DiffRow("changed", i, j, old[i], new[j], inline_segments(old[i], new[j])))
        rows += [DiffRow("removed", i, None, old[i]) for i in range(i1 + pairs, i2)]
        rows += [DiffRow("added", None, j, "", new[j]) for j in range(j1 + pairs, j2)]
    return rows


def summarize(rows: list[DiffRow]) -> dict[str, int]:
    out = {"same": 0, "changed": 0, "added": 0, "removed": 0}
    for r in rows:
        out[r.kind] += 1
    return out
