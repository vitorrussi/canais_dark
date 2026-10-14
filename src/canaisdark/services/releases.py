"""Notas de versão: lê `releases/vX.Y.Z.md` (front-matter simples + seções em Markdown mínimo)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from canaisdark.services import version

log = structlog.get_logger()

RELEASES_DIR = version.REPO_ROOT / "releases"
SECTION_ORDER = ("Novidades", "Melhorias", "Correções", "Para quem administra")
SECTION_ICONS = {
    "Novidades": "sparkles",
    "Melhorias": "play",
    "Correções": "check",
    "Para quem administra": "users",
}


@dataclass
class Release:
    version: str
    date: str
    title: str
    sections: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int, int]:
        return version.parse_semver(self.version)


def _inline(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def rich(text: str) -> str:
    """HTML seguro com **negrito** e `código` (o resto é escapado)."""
    import html

    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


def parse(text: str) -> Release:
    """Levanta ValueError se o front-matter estiver incompleto ou a versão for inválida."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text.strip(), flags=re.DOTALL)
    if not match:
        raise ValueError("Falta o cabeçalho (---) com version, date e title")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip().strip("\"'")
    for needed in ("version", "date", "title"):
        if not meta.get(needed):
            raise ValueError(f"Falta '{needed}' no cabeçalho")
    version.parse_semver(meta["version"])  # valida
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for raw in match.group(2).splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = []
            sections.append((line[3:].strip(), current))
        elif line and current is not None:
            current.append(_inline(line[2:] if line.startswith(("- ", "* ")) else line))
    sections = [(name, items) for name, items in sections if items]
    rank = {name: i for i, name in enumerate(SECTION_ORDER)}
    sections.sort(key=lambda s: rank.get(s[0], len(rank)))
    return Release(meta["version"].lstrip("v"), meta["date"], meta["title"], sections)


def load_all(directory: Path | None = None) -> list[Release]:
    """Todas as versões válidas, da mais nova para a mais antiga; arquivos quebrados são ignorados."""
    folder = directory or RELEASES_DIR
    found: list[Release] = []
    if folder.is_dir():
        for path in sorted(folder.glob("v*.md")):
            try:
                found.append(parse(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                log.warning("releases.skip", file=path.name, error=str(e))
    found.sort(key=lambda r: r.key, reverse=True)
    return found


def latest(directory: Path | None = None) -> Release | None:
    items = load_all(directory)
    return items[0] if items else None
