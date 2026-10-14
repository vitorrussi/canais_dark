"""Versão do sistema: número (pyproject.toml) e canal (estável = HEAD numa tag; senão em desenvolvimento)."""

import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class VersionInfo:
    version: str
    channel: str  # "stable" | "dev"
    commit: str  # sha curto ("" se não houver git)
    dirty: bool

    @property
    def label(self) -> str:
        return f"v{self.version}" + ("" if self.channel == "stable" else " · em desenvolvimento")


def _git(*args: str) -> str | None:
    try:
        res = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip()


def _number() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data["project"]["version"])
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return metadata.version("canaisdark")
    except metadata.PackageNotFoundError:
        return "0.0.0"


@lru_cache(maxsize=1)
def current() -> VersionInfo:
    number = _number()
    commit = _git("rev-parse", "--short", "HEAD") or ""
    exact = _git("describe", "--tags", "--exact-match", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    stable = bool(exact) and exact == f"v{number}" and not dirty
    return VersionInfo(number, "stable" if stable else "dev", commit, dirty)


def parse_semver(value: str) -> tuple[int, int, int]:
    """'0.10.2' → (0, 10, 2); levanta ValueError se não for MAJOR.MINOR.PATCH."""
    parts = value.strip().lstrip("v").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Versão inválida: {value!r} (use MAJOR.MINOR.PATCH)")
    return int(parts[0]), int(parts[1]), int(parts[2])
