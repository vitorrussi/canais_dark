"""Edição livre pelo Claude: pacote com links assinados + recebimento do MP4 editado.

O Claude (claude.ai, com execução de código e acesso à rede liberado) baixa os arquivos do vídeo
no sandbox dele, edita com ffmpeg/Python e devolve o MP4 em partes. O servidor monta, valida com
ffprobe e registra como render final (`meta.source = "claude_external"`).

Nada aqui executa código recebido, aprova ou envia a terceiros. Os links são credenciais de curta
duração (3 h), de um único vídeo, amarradas ao token da pessoa que pediu o pacote.
"""

import hashlib
import json
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session

from canaisdark.config import get_settings, oauth_issuer
from canaisdark.models import AssetType, User, Video
from canaisdark.services import (
    channels,
    metadata,
    narration,
    render,
    scene_assets,
    scene_prompts,
    shorts,
    thumbnail,
    videos,
)
from canaisdark.services import timeline as tl_service
from canaisdark.services.errors import Forbidden, InvalidState, NotFound

log = structlog.get_logger()

SALT = "edit-package"
LINK_TTL_SECONDS = 3 * 3600
MAX_PART_BYTES = 95 * 1024 * 1024  # Cloudflare corta requisições acima de ~100 MB
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_PARTS = 40
DURATION_TOLERANCE_S = 3.0
EXTERNAL_SOURCE = "claude_external"

TARGETS: dict[str, tuple[int, int, int]] = {"720p": (1280, 720, 30), "1080p": (1920, 1080, 30)}
QUALITY_ALIASES = {"preview": "720p", "final": "1080p"}


# ----- Links assinados -----


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=SALT)


def _fingerprint(user: User) -> str:
    return (user.token_hash or "")[:16]


def sign(video: Video, user: User, key: str) -> str:
    if not user.is_active:
        raise InvalidState("Pessoa desativada")
    return _serializer().dumps({"v": video.id, "u": user.id, "f": _fingerprint(user), "k": key})


@dataclass(frozen=True)
class Grant:
    video: Video
    user: User
    key: str


def verify(session: Session, signed: str) -> Grant:
    """Valida assinatura, validade, vídeo e pessoa (token rotacionado/desativada invalida)."""
    try:
        data = _serializer().loads(signed, max_age=LINK_TTL_SECONDS)
    except SignatureExpired as e:
        raise Forbidden("Este link expirou. Peça um pacote novo (get_edit_package).") from e
    except BadSignature as e:
        raise Forbidden("Link inválido.") from e
    if not isinstance(data, dict):
        raise Forbidden("Link inválido.")
    user = session.get(User, data.get("u"))
    if user is None or not user.is_active or data.get("f") != _fingerprint(user):
        raise Forbidden("Este link não vale mais (o token da pessoa mudou ou ela foi desativada).")
    video = session.get(Video, data.get("v"))
    if video is None:
        raise NotFound("Vídeo não encontrado")
    return Grant(video, user, str(data.get("k", "")))


# ----- Arquivos do pacote -----


@dataclass(frozen=True)
class PackageFile:
    name: str  # nome lógico dentro do pacote (ex.: "scenes/001_main.jpg")
    path: Path
    role: str  # narration | image | vertical | clip | music | subtitles | alignment
    scene_index: int | None = None


def collect_files(session: Session, video: Video) -> list[PackageFile]:
    assert video.id is not None
    timing = tl_service.collect_timing(session, video)
    out: list[PackageFile] = []
    audio_path = narration.asset_path(timing.audio)
    out.append(
        PackageFile(f"narration/narration{audio_path.suffix or '.mp3'}", audio_path, "narration")
    )
    amap = scene_assets.scene_assets(session, [sp.scene.id for sp in timing.spans if sp.scene.id])
    for sp in timing.spans:
        variants = amap.get(sp.scene.id or 0, {})
        for variant, asset in sorted(variants.items()):
            path = narration.asset_path(asset)
            if not path.is_file():
                continue
            role = (
                "clip"
                if asset.type == AssetType.VIDEO_CLIP.value
                else ("vertical" if variant == "vertical" else "image")
            )
            suffix = path.suffix or ".jpg"
            out.append(
                PackageFile(
                    f"scenes/{sp.scene.index + 1:03d}_{variant}{suffix}", path, role, sp.scene.index
                )
            )
    found = tl_service.latest(session, video.id)
    try:
        tl = found[1] if found else tl_service.build_default(session, video)
        music = render.music_file(session, tl)
    except InvalidState:
        music = None
    if music is not None and music.is_file():
        out.append(PackageFile(f"music/{music.name}", music, "music"))
    for fmt in ("ass", "srt"):
        sub = narration.latest_subtitle(session, video.id, fmt)
        if sub is not None and narration.asset_path(sub).is_file():
            out.append(
                PackageFile(f"subtitles/narration.{fmt}", narration.asset_path(sub), "subtitles")
            )
    align = narration.latest_asset(session, video.id, AssetType.ALIGNMENT, "alignment")
    if align is not None and narration.asset_path(align).is_file():
        out.append(
            PackageFile("narration/alignment.json", narration.asset_path(align), "alignment")
        )
    return [f for f in out if f.path.is_file()]


def find_file(session: Session, video: Video, name: str) -> PackageFile:
    for f in collect_files(session, video):
        if f.name == name:
            return f
    raise NotFound(
        "Arquivo do pacote não encontrado (o vídeo pode ter mudado; peça um pacote novo)."
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _url(base: str, signed: str, suffix: str = "") -> str:
    return f"{base}/x/{signed}{suffix}"


def normalize_quality(quality: str) -> str:
    q = QUALITY_ALIASES.get(quality, quality)
    if q not in TARGETS:
        raise InvalidState("Qualidade inválida. Use: 720p ou 1080p.")
    return q


# ----- Manifesto -----


def build_manifest(
    session: Session, video_id: int, user: User, quality: str = "720p", base_url: str | None = None
) -> dict[str, Any]:
    video = videos.get_video(session, video_id)
    if not video.approved_script_version_id:
        raise InvalidState("Aprove o roteiro (e gere a narração) antes da edição livre.")
    q = normalize_quality(quality)
    width, height, fps = TARGETS[q]
    base = (base_url or oauth_issuer(get_settings())).rstrip("/")
    channel = channels.get_channel(session, video.channel_id)
    timing = tl_service.collect_timing(session, video)
    files = collect_files(session, video)
    by_scene: dict[int, dict[str, str]] = {}
    for f in files:
        if f.scene_index is not None:
            by_scene.setdefault(f.scene_index, {})[f.role] = f.name
    try:
        words, words_mode = shorts.load_words(session, video)
    except InvalidState:
        words, words_mode = [], "none"
    scenes = []
    for sp in timing.spans:
        files_of_scene = by_scene.get(sp.scene.index, {})
        in_scene = [
            [w["text"], round(float(w["start"]), 2), round(float(w["end"]), 2)]
            for w in words
            if sp.start - 0.01 <= float(w["start"]) < sp.end
        ]
        scenes.append(
            {
                "index": sp.scene.index,
                "text": sp.scene.text,
                "start_s": round(sp.start, 2),
                "end_s": round(sp.end, 2),
                "length_s": round(sp.length, 2),
                "image": files_of_scene.get("image"),
                "vertical_image": files_of_scene.get("vertical"),
                "clip": files_of_scene.get("clip"),
                "words": in_scene,
            }
        )
    listing = [
        {
            "name": f.name,
            "role": f.role,
            "scene_index": f.scene_index,
            "bytes": f.path.stat().st_size,
            "sha256": _sha256(f.path),
            "url": _url(base, sign(video, user, f"f:{f.name}")),
        }
        for f in files
    ]
    found = tl_service.latest(session, video_id)
    current_tl = (found[1] if found else tl_service.build_default(session, video)).model_dump(
        mode="json"
    )
    return {
        "video": {
            "id": video.id,
            "title": video.title,
            "topic": video.topic,
            "language": video.language,
        },
        "channel": {
            "name": channel.name,
            "language": channel.language,
            "visual_style_prompt": channel.visual_style_prompt,
        },
        "target": {
            "quality": q,
            "width": width,
            "height": height,
            "fps": fps,
            "duration_s": round(timing.duration, 2),
            "duration_tolerance_s": DURATION_TOLERANCE_S,
            "container": "mp4 (H.264 + AAC)",
            "partial_narration": timing.partial,
        },
        "scenes": scenes,
        "words_timing": words_mode,
        "files": listing,
        "zip_url": _url(base, sign(video, user, "zip")),
        "upload": {
            "url": _url(base, sign(video, user, "upload"), "/upload"),
            "method": "POST (corpo bruto, Content-Type: application/octet-stream)",
            "query": "?part=<n começando em 1>&total=<quantidade de partes>",
            "max_part_bytes": MAX_PART_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "tip": "split -b 90m video.mp4 parte_ ; envie cada parte em ordem (a última devolve o resultado).",
        },
        "current_timeline": current_tl,
        "expires_in_s": LINK_TTL_SECONDS,
        "how_to": HOW_TO,
    }


HOW_TO = (
    "Edite o vídeo no seu sandbox (ffmpeg/Python/Pillow) e devolva um MP4. Passos: (1) baixe o `zip_url` com "
    "curl -L -o pacote.zip e extraia (ou baixe cada `files[].url`; confira `sha256`); (2) monte o vídeo a "
    "partir de `scenes` (imagem/clipe de cada cena, `start_s`/`end_s`, `words` com tempo por palavra para "
    "ancorar textos na fala); (3) use SEMPRE a narração do pacote como áudio e mantenha a duração em "
    "`target.duration_s` (±`duration_tolerance_s`); gere em `target.width`x`target.height`, `target.fps`, "
    "H.264+AAC, `-movflags +faststart`; (4) efeitos livres: movimento de câmera, transições, faixas de "
    "capítulo, selos de data, textos animados, cores e fontes coerentes com `channel.visual_style_prompt`; "
    "evite texto ilegível e respeite o idioma do vídeo; (5) os comandos do sandbox têm tempo limite: "
    "renderize por trechos e concatene; (6) envie o MP4 para `upload.url` em partes de até 90 MB "
    "(`split -b 90m`), `?part=1&total=N`; a resposta da última parte confirma o registro (status `ok`) ou "
    "explica o problema; (7) depois chame complete_task (se houver task_id) com um resumo do que fez. "
    "Não aprove nem envie ao YouTube/Drive: isso é feito pela pessoa no site."
)


# ----- ZIP -----


def build_zip(session: Session, video: Video, manifest: dict[str, Any] | None = None) -> Path:
    assert video.id is not None
    files = collect_files(session, video)
    sig = hashlib.sha1(
        "|".join(
            f"{f.name}:{f.path.stat().st_size}:{int(f.path.stat().st_mtime)}" for f in files
        ).encode()
    ).hexdigest()[:12]
    out_dir = render.render_dir(session, video)
    out = out_dir / f"edit-package-{sig}.zip"
    if out.is_file():
        return out
    for old in out_dir.glob("edit-package-*.zip"):
        old.unlink(missing_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(f.path, f.name)
        if manifest is not None:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    tmp.replace(out)
    return out


# ----- Upload em partes + validação -----


def _staging(session: Session, video: Video) -> Path:
    path = render.render_dir(session, video) / "external-upload"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reset_staging(session: Session, video: Video) -> None:
    shutil.rmtree(_staging(session, video), ignore_errors=True)


def part_path(session: Session, video: Video, part: int, total: int) -> Path:
    """Caminho onde gravar a parte `part` (1..total). Mudar `total` descarta partes anteriores."""
    if total < 1 or total > MAX_PARTS or part < 1 or part > total:
        raise InvalidState(
            f"part/total inválidos (part={part}, total={total}; máximo {MAX_PARTS} partes)."
        )
    staging = _staging(session, video)
    marker = staging / "total"
    if marker.is_file() and marker.read_text().strip() != str(total):
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(total))
    return staging / f"part-{part:03d}"


def assemble(session: Session, video: Video, total: int) -> Path | None:
    """Junta as partes se todas chegaram; devolve o MP4 montado ou None se faltam partes."""
    staging = _staging(session, video)
    parts = [staging / f"part-{i:03d}" for i in range(1, total + 1)]
    if not all(p.is_file() for p in parts):
        return None
    size = sum(p.stat().st_size for p in parts)
    if size > MAX_TOTAL_BYTES:
        reset_staging(session, video)
        raise InvalidState("O arquivo passa de 2 GB.")
    out = staging / "assembled.mp4"
    with out.open("wb") as dst:
        for p in parts:
            with p.open("rb") as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    for p in parts:
        p.unlink(missing_ok=True)
    return out


def received_parts(session: Session, video: Video) -> list[int]:
    return sorted(int(p.name.split("-")[1]) for p in _staging(session, video).glob("part-*"))


def _fps(value: Any) -> int:
    try:
        num, _, den = str(value).partition("/")
        return round(float(num) / float(den or 1))
    except (ValueError, ZeroDivisionError):
        return 30


def probe(path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise InvalidState("ffprobe não está instalado no servidor.")
    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads(res.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        raise InvalidState(
            "Não consegui ler o arquivo como vídeo (corrompido ou incompleto)."
        ) from e
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "format": str(data.get("format", {}).get("format_name", "")),
        "duration": float(data.get("format", {}).get("duration") or 0),
        "video": v,
        "audio": a,
    }


def validate(info: dict[str, Any], expected_duration: float) -> None:
    problems: list[str] = []
    if "mp4" not in info["format"] and "mov" not in info["format"]:
        problems.append(f"O contêiner deve ser MP4 (veio '{info['format']}').")
    v, a = info["video"], info["audio"]
    if v is None:
        problems.append("O arquivo não tem faixa de vídeo.")
    else:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if w < 640 or h < 360:
            problems.append(f"Resolução muito baixa ({w}x{h}); use pelo menos 1280x720.")
        elif abs(w / h - 16 / 9) > 0.04:
            problems.append(f"A proporção deve ser 16:9 (veio {w}x{h}).")
    if a is None:
        problems.append("O arquivo não tem áudio (use a narração do pacote).")
    if abs(info["duration"] - expected_duration) > DURATION_TOLERANCE_S:
        problems.append(
            f"Duração {info['duration']:.1f}s; esperado {expected_duration:.1f}s "
            f"(±{DURATION_TOLERANCE_S:.0f}s). Mantenha o ritmo da narração."
        )
    if problems:
        raise InvalidState(" ".join(problems))


def register(session: Session, video: Video, assembled: Path, actor: User) -> dict[str, Any]:
    """Valida o MP4 montado e o registra como render final (substitui o final anterior)."""
    assert video.id is not None
    timing = tl_service.collect_timing(session, video)
    info = probe(assembled)
    try:
        validate(info, timing.duration)
    except InvalidState:
        assembled.unlink(missing_ok=True)
        raise
    dest = render.render_dir(session, video) / f"external-{int(time.time())}.mp4"
    shutil.move(str(assembled), dest)
    shutil.rmtree(_staging(session, video), ignore_errors=True)
    versions = tl_service.list_versions(session, video.id)
    try:
        inputs = tl_service.inputs_hash(session, video)
    except InvalidState:
        inputs = ""
    v = info["video"] or {}
    result = render.RenderResult(
        path=dest,
        duration=info["duration"],
        segments_total=0,
        segments_cached=0,
        size=dest.stat().st_size,
    )
    asset = render.store_result(
        session,
        video,
        result,
        "final",
        {
            "source": EXTERNAL_SOURCE,
            "external": True,
            "uploaded_by": actor.id,
            "inputs_hash": inputs,
            "timeline_asset_id": versions[-1].id if versions else None,
            "width": v.get("width"),
            "height": v.get("height"),
            "codec": v.get("codec_name"),
            "fps": _fps(v.get("avg_frame_rate")),
            "partial": timing.partial,
        },
        None,
    )
    log.info("edit_package.registered", video_id=video.id, asset_id=asset.id, bytes=result.size)
    return {
        "status": "ok",
        "asset_id": asset.id,
        "duration_s": round(info["duration"], 1),
        "bytes": result.size,
        "message": "Render final registrado. A pessoa revisa e aprova no site.",
    }


# ----- Thumbnail pelo Claude -----

THUMB_MAX_BYTES = 10 * 1024 * 1024  # entrada; o serviço recompõe para ≤ 2 MB (limite do YouTube)

THUMB_HOW_TO = (
    "Crie a thumbnail no seu sandbox (Python + Pillow) e envie. Passos: (1) baixe 2 a 4 imagens de `scenes` "
    "pelos links (`curl -L -o`); escolha a cena visualmente mais forte (use `title` e `thumbnail_ideas` como "
    "guia); (2) componha uma imagem de `spec.width`x`spec.height` (16:9): recorte/zoom no ponto de interesse, "
    "aumente contraste e saturação, escureça um lado se for pôr texto; (3) texto curto (até 4 palavras, "
    "fonte grossa tipo DejaVu Sans Bold/Liberation Sans Bold, contorno ou sombra, legível em tamanho "
    "pequeno), no idioma do vídeo; sem rostos de pessoas reais e sem promessas enganosas; (4) salve JPG "
    "(qualidade 90) ou PNG com até `spec.max_bytes`; (5) envie com `curl -s -X POST --data-binary @thumb.jpg "
    "-H 'Content-Type: application/octet-stream' '<upload.url>'`; a resposta confirma (`status: ok`) ou "
    "explica o problema. Cada envio substitui o anterior (a última vale); (6) chame complete_task (se "
    "houver task_id) com um resumo. Não envie nada ao YouTube: a pessoa revisa no site."
)


def _scene_files(session: Session, video: Video) -> list[PackageFile]:
    scenes = scene_prompts.approved_scenes(session, video.id or 0)
    amap = scene_assets.scene_assets(session, [sc.id for sc in scenes if sc.id])
    out: list[PackageFile] = []
    for sc in scenes:
        for variant, asset in sorted(amap.get(sc.id or 0, {}).items()):
            path = narration.asset_path(asset)
            if not path.is_file():
                continue
            role = (
                "clip"
                if asset.type == AssetType.VIDEO_CLIP.value
                else ("vertical" if variant == "vertical" else "image")
            )
            out.append(
                PackageFile(
                    f"scenes/{sc.index + 1:03d}_{variant}{path.suffix or '.jpg'}",
                    path,
                    role,
                    sc.index,
                )
            )
    return out


def find_thumb_file(session: Session, video: Video, name: str) -> PackageFile:
    for f in _scene_files(session, video):
        if f.name == name:
            return f
    raise NotFound("Arquivo não encontrado (as imagens podem ter mudado; peça um pacote novo).")


def build_thumbnail_manifest(
    session: Session, video_id: int, user: User, base_url: str | None = None
) -> dict[str, Any]:
    video = videos.get_video(session, video_id)
    if not video.approved_script_version_id:
        raise InvalidState("Aprove o roteiro e envie as imagens das cenas antes da thumbnail.")
    base = (base_url or oauth_issuer(get_settings())).rstrip("/")
    channel = channels.get_channel(session, video.channel_id)
    files = [f for f in _scene_files(session, video) if f.role in ("image", "vertical")]
    if not files:
        raise InvalidState(
            "Ainda não há imagens nas cenas. Envie as imagens no storyboard primeiro."
        )
    scenes = scene_prompts.approved_scenes(session, video_id)
    names: dict[int, dict[str, str]] = {}
    for f in files:
        if f.scene_index is not None:
            names.setdefault(f.scene_index, {})[f.role] = f.name
    found = metadata.latest(session, video_id)
    current = metadata.current(video)
    title = (found[1].title if found else "") or str(current.get("title") or video.title or "")
    ideas = list(found[1].thumbnail_ideas) if found else []
    current_thumb = thumbnail.latest(session, video_id)
    return {
        "video": {"id": video.id, "topic": video.topic, "language": video.language},
        "title": title or video.topic,
        "thumbnail_ideas": ideas,
        "channel": {
            "name": channel.name,
            "language": channel.language,
            "visual_style_prompt": channel.visual_style_prompt,
        },
        "scenes": [
            {
                "index": sc.index,
                "text": sc.text,
                "image": names.get(sc.index, {}).get("image"),
                "vertical_image": names.get(sc.index, {}).get("vertical"),
            }
            for sc in scenes
        ],
        "files": [
            {
                "name": f.name,
                "role": f.role,
                "scene_index": f.scene_index,
                "bytes": f.path.stat().st_size,
                "url": _url(base, sign(video, user, f"t:{f.name}")),
            }
            for f in files
        ],
        "spec": {
            "width": 1280,
            "height": 720,
            "formats": ["jpg", "png"],
            "max_bytes": thumbnail.MAX_BYTES,
        },
        "has_current_thumbnail": current_thumb is not None,
        "upload": {
            "url": _url(base, sign(video, user, "thumb"), "/thumbnail"),
            "method": "POST (corpo bruto, Content-Type: application/octet-stream)",
            "max_bytes": THUMB_MAX_BYTES,
        },
        "expires_in_s": LINK_TTL_SECONDS,
        "how_to": THUMB_HOW_TO,
    }


def register_thumbnail(session: Session, video: Video, data: bytes, actor: User) -> dict[str, Any]:
    """Valida pelo conteúdo (Pillow) e grava como a thumbnail do vídeo (a última vale)."""
    assert video.id is not None
    asset = thumbnail.save_upload(session, video.id, data)
    log.info("edit_package.thumbnail", video_id=video.id, asset_id=asset.id, by=actor.id)
    return {
        "status": "ok",
        "asset_id": asset.id,
        "width": asset.meta.get("width"),
        "height": asset.meta.get("height"),
        "bytes": asset.meta.get("bytes"),
        "warnings": asset.meta.get("warnings", []),
        "message": "Thumbnail registrada. A pessoa revisa no site.",
    }
