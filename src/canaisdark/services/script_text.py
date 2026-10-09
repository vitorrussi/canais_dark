"""Utilitários puros de texto de roteiro (contagem, estimativa, divisão e limpeza)."""

import re

WORDS_PER_MINUTE = 150

_NBSP = " ​  "


def word_count(text: str) -> int:
    return len(text.split())


def estimated_seconds(words: int, wpm: int = WORDS_PER_MINUTE) -> int:
    return round(words / wpm * 60)


def format_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def clean_text(text: str) -> str:
    """Normaliza espaços, quebras, aspas curvas e reticências (bom para TTS)."""
    for ch in _NBSP:
        text = text.replace(ch, " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("…", "...")):
        text = text.replace(src, dst)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_blocks(text: str) -> list[str]:
    """Blocos separados por linha em branco; quebras simples dentro do bloco viram espaço."""
    blocks = []
    for raw in re.split(r"\n\s*\n", clean_text(text)):
        block = " ".join(line.strip() for line in raw.splitlines() if line.strip())
        if block:
            blocks.append(block)
    return blocks


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'“‘(\[]?[A-ZÀ-ÖØ-Þ0-9])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]
