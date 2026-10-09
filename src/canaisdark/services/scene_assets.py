"""Imagens/clipes por cena: validação pelo CONTEÚDO, miniaturas, substituir/remover e mapeamento de lote."""

import hashlib
import io
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType
from canaisdark.services import channels, scene_prompts, storage, videos
from canaisdark.services.errors import InvalidState, NotFound

VARIANTS = ("main", "vertical")
SCENE_ASSET_TYPES = (AssetType.IMAGE.value, AssetType.VIDEO_CLIP.value)
ASPECT = {"main": 16 / 9, "vertical": 9 / 16}
TOLERANCE = 0.08
THUMB_WIDTH = 480
MIN_SIDE = 640


def detect_kind(data: bytes) -> tuple[str, str] | None:
    """(tipo, extensão) pelos primeiros bytes; None se não for formato aceito."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "webp"
    if data[4:8] == b"ftyp":
        return "video_clip", "mov" if data[8:12] == b"qt  " else "mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video_clip", "webm"
    return None


def _image_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise InvalidState(
            "A imagem está corrompida ou incompleta (não foi possível abri-la)."
        ) from e


def _probe_clip(path: Path) -> dict[str, Any] | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        data = json.loads(res.stdout)
        stream = (data.get("streams") or [{}])[0]
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "duration": float(data.get("format", {}).get("duration", 0) or 0),
        }
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def check_dimensions(width: int, height: int, variant: str) -> list[str]:
    """Levanta erro para casos inutilizáveis e devolve avisos para os discutíveis."""
    warnings: list[str] = []
    if max(width, height) < MIN_SIDE:
        raise InvalidState(
            f"Resolução muito baixa ({width}×{height}). Use pelo menos {MIN_SIDE} px no maior lado (ideal 1920×1080)."
        )
    portrait = height > width
    if variant == "main" and portrait:
        raise InvalidState(
            f"A imagem é vertical ({width}×{height}). Para a versão principal use paisagem 16:9; para Shorts, envie como versão 9:16."
        )
    if variant == "vertical" and not portrait:
        raise InvalidState(
            f"A imagem é horizontal ({width}×{height}). A versão 9:16 precisa ser vertical."
        )
    ratio = width / height
    target = ASPECT[variant]
    if abs(ratio - target) / target > TOLERANCE:
        want = "16:9" if variant == "main" else "9:16"
        warnings.append(f"Proporção {width}:{height} ≠ {want}; será ajustada/cortada no render.")
    if variant == "main" and width < 1920:
        warnings.append(
            f"Resolução {width}×{height} abaixo de 1920×1080: pode ficar mole no vídeo final."
        )
    if variant == "vertical" and height < 1920:
        warnings.append(
            f"Resolução {width}×{height} abaixo de 1080×1920: pode ficar mole nos Shorts."
        )
    return warnings


@dataclass
class UploadResult:
    asset: Asset
    replaced: bool
    warnings: list[str] = field(default_factory=list)


def _assets_dir(session: Session, video_id: int) -> Path:
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    path = storage.video_dir(channel.slug, video_id, create=True) / "images"
    (path / "thumbs").mkdir(parents=True, exist_ok=True)
    return path


def _delete_files(asset: Asset) -> None:
    base = get_settings().data_dir
    for rel in (asset.path, str(asset.meta.get("thumb") or "")):
        if rel:
            (base / rel).unlink(missing_ok=True)


def _rel(path: Path) -> str:
    return str(path.relative_to(get_settings().data_dir))


def scene_assets(session: Session, scene_ids: list[int]) -> dict[int, dict[str, Asset]]:
    """{scene_id: {variant: asset}}; se houver mais de um por variante, vale o mais novo."""
    out: dict[int, dict[str, Asset]] = {}
    if not scene_ids:
        return out
    stmt = (
        select(Asset)
        .where(col(Asset.scene_id).in_(scene_ids), col(Asset.type).in_(SCENE_ASSET_TYPES))
        .order_by(col(Asset.id))
    )
    for a in session.exec(stmt):
        if a.scene_id is not None:
            out.setdefault(a.scene_id, {})[str(a.meta.get("variant", "main"))] = a
    return out


def save_upload(
    session: Session,
    video_id: int,
    scene_index: int,
    data: bytes,
    filename: str,
    variant: str = "main",
) -> UploadResult:
    if variant not in VARIANTS:
        raise InvalidState(f"Variante inválida '{variant}'. Use: main (16:9) ou vertical (9:16).")
    scenes = scene_prompts.approved_scenes(session, video_id)
    scene = next((s for s in scenes if s.index == scene_index), None)
    if scene is None:
        raise NotFound(
            f"Cena {scene_index + 1} não existe (o roteiro aprovado tem {len(scenes)} cenas)."
        )
    limit = get_settings().max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise InvalidState(
            f"Arquivo de {len(data) / 1048576:.0f} MB passa do limite de {get_settings().max_upload_mb} MB (o Cloudflare corta uploads maiores). Comprima o arquivo."
        )
    if not data:
        raise InvalidState("Arquivo vazio.")
    detected = detect_kind(data)
    if detected is None:
        raise InvalidState(
            "Formato não suportado. Envie PNG, JPEG, WebP (imagem) ou MP4, WebM, MOV (clipe)."
        )
    kind, ext = detected
    folder = _assets_dir(session, video_id)
    digest = hashlib.sha1(data).hexdigest()[:8]
    path = folder / f"s{scene.index + 1:03d}_{variant}_{digest}.{ext}"
    meta: dict[str, Any] = {
        "variant": variant,
        "bytes": len(data),
        "original_name": Path(filename).name[:120],
        "kind": kind,
    }
    if kind == "image":
        width, height = _image_size(data)
        warnings = check_dimensions(width, height, variant)
        meta.update(width=width, height=height)
        path.write_bytes(data)
        thumb = folder / "thumbs" / f"{path.stem}.jpg"
        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            rgb.save(thumb, "JPEG", quality=80)
        meta["thumb"] = _rel(thumb)
    else:
        path.write_bytes(data)
        info = _probe_clip(path)
        warnings = []
        if info and info.get("width") and info.get("height"):
            try:
                warnings = check_dimensions(int(info["width"]), int(info["height"]), variant)
            except InvalidState:
                path.unlink(missing_ok=True)
                raise
            meta.update(
                width=info["width"], height=info["height"], duration=round(info["duration"], 2)
            )
        else:
            warnings.append(
                "Clipe aceito sem verificar resolução/duração (ffprobe não está instalado)."
            )
    meta["warnings"] = warnings
    old = scene_assets(session, [scene.id or 0]).get(scene.id or 0, {}).get(variant)
    asset = Asset(
        video_id=video_id,
        scene_id=scene.id,
        type=AssetType.IMAGE.value if kind == "image" else AssetType.VIDEO_CLIP.value,
        path=_rel(path),
        meta=meta,
        based_on_script_version_id=scene.script_version_id,
    )
    session.add(asset)
    if variant == "vertical" and not scene.wants_vertical:
        scene.wants_vertical = True
        session.add(scene)
    session.commit()
    session.refresh(asset)
    replaced = False
    if old is not None and old.id != asset.id:
        if old.path != asset.path:
            _delete_files(old)
        session.delete(old)
        session.commit()
        replaced = True
    return UploadResult(asset=asset, replaced=replaced, warnings=warnings)


def remove_asset(session: Session, video_id: int, asset_id: int) -> None:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.video_id != video_id or asset.type not in SCENE_ASSET_TYPES:
        raise NotFound("Arquivo não encontrado neste vídeo")
    _delete_files(asset)
    session.delete(asset)
    session.commit()


# ----- Lote -----


def _natural_key(name: str) -> list[object]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def map_filenames(filenames: list[str], scene_count: int, filled: set[int]) -> list[dict[str, Any]]:
    """Decide a cena (0-based) de cada arquivo: pelo número no nome; sem número, ocupa as cenas ainda vazias em ordem."""
    result: list[dict[str, Any]] = [{"name": n, "scene": None, "reason": ""} for n in filenames]
    taken: dict[int, int] = {}
    unnumbered: list[int] = []
    for i, name in enumerate(filenames):
        match = re.search(r"\d+", Path(name).stem)
        number = int(match.group()) if match else None
        if number is not None and 1 <= number <= scene_count:
            idx = number - 1
            if idx in taken:
                result[i]["reason"] = f"Duas imagens para a cena {number} neste lote (ignorada)."
            else:
                taken[idx] = i
                result[i].update(scene=idx, reason=f"número {number} no nome")
        else:
            unnumbered.append(i)
    free = [s for s in range(scene_count) if s not in filled and s not in taken]
    order = sorted(unnumbered, key=lambda i: _natural_key(filenames[i]))
    for i in order:
        if free:
            result[i].update(scene=free.pop(0), reason="próxima cena sem imagem")
        else:
            result[i]["reason"] = "Não há mais cenas sem imagem para este arquivo."
    return result
