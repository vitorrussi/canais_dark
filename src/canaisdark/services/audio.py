"""Utilidades de áudio (ffmpeg/ffprobe opcionais)."""

import shutil
import subprocess
from pathlib import Path

from canaisdark.integrations.elevenlabs import MP3_BITRATE
from canaisdark.services.errors import InvalidState


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def concat_mp3(parts: list[Path], out: Path) -> None:
    """Junta MP3 de mesmo formato. 1 trecho só copia (sem ffmpeg)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        shutil.copyfile(parts[0], out)
        return
    if not ffmpeg_available():
        raise InvalidState(
            "A narração tem mais de um trecho e o ffmpeg não está instalado. "
            "Instale com: sudo apt install ffmpeg (ou use o teste curto, que gera um trecho só)."
        )
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as e:
        raise InvalidState(f"ffmpeg falhou ao juntar os trechos: {e.stderr[-300:]}") from e
    finally:
        listing.unlink(missing_ok=True)


def duration_seconds(path: Path) -> float:
    """Duração exata via ffprobe; sem ele, estima pelo bitrate (MP3 CBR 128 kbps do ElevenLabs)."""
    if shutil.which("ffprobe"):
        try:
            res = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return float(res.stdout.strip())
        except (subprocess.SubprocessError, ValueError):
            pass
    return path.stat().st_size * 8 / MP3_BITRATE
