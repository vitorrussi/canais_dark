"""Shorts 9:16 (etapa OPCIONAL): seleção de trechos da narração, localização no alinhamento e plano versionado.

A seleção guarda o TEXTO EXATO + os tempos localizados; o render fica em `services/shorts_render.py`.
"""

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from canaisdark.config import get_settings
from canaisdark.models import Asset, AssetType, Video
from canaisdark.schemas.shorts import (
    MAX_SECONDS,
    MAX_SHORTS,
    MIN_SECONDS,
    ShortIn,
    ShortSelection,
    ShortsPlan,
)
from canaisdark.services import alignment, channels, narration, storage, timeline, videos
from canaisdark.services.errors import InvalidState

PAD_BEFORE_S = 0.15
PAD_AFTER_S = 0.35
MIN_CONFIDENCE = 0.9
EXACT_CONFIDENCE = 0.98


@dataclass
class Located:
    start: float
    end: float
    confidence: float
    first_word: int
    last_word: int


def load_words(session: Session, video: Video) -> tuple[list[dict[str, Any]], str]:
    """Palavras com tempo da narração atual: do alinhamento forçado ou estimadas pelas cenas."""
    assert video.id is not None
    asset = narration.latest_asset(session, video.id, AssetType.ALIGNMENT, "alignment")
    if asset is not None:
        path = narration.asset_path(asset)
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            words = alignment.clean_words(doc.get("words") or [])
            if words:
                return words, str(doc.get("timing") or "forced")
    scenes = [
        s for s in timeline.approved_scenes(session, video) if s.start_s is not None and s.end_s
    ]
    if not scenes:
        raise InvalidState("A narração ainda não tem tempos. Gere a narração antes dos Shorts.")
    spans = [(float(s.start_s or 0.0), float(s.end_s or 0.0)) for s in scenes]
    return alignment.words_from_scene_spans([s.text for s in scenes], spans), "estimated"


def locate(words: list[dict[str, Any]], exact_text: str) -> Located | None:
    """Acha `exact_text` (tolerante a pontuação/caixa) na sequência de palavras; None se não achar."""
    wanted = [t for t in (alignment.normalize_token(x) for x in exact_text.split()) if t]
    indexed = [
        (k, t)
        for k, t in ((k, alignment.normalize_token(str(w["text"]))) for k, w in enumerate(words))
        if t
    ]
    if len(wanted) < 4 or not indexed:
        return None
    toks = [t for _, t in indexed]
    seed = SequenceMatcher(None, wanted, toks, autojunk=False).find_longest_match(
        0, len(wanted), 0, len(toks)
    )
    if seed.size < min(4, len(wanted)):
        return None
    lo = max(0, seed.b - seed.a - 8)
    hi = min(len(toks), seed.b - seed.a + len(wanted) + 8)
    matcher = SequenceMatcher(None, wanted, toks[lo:hi], autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    matched = sum(b.size for b in blocks)
    confidence = matched / len(wanted)
    if confidence < MIN_CONFIDENCE or not blocks:
        return None
    first = indexed[lo + blocks[0].b][0]
    last = indexed[lo + blocks[-1].b + blocks[-1].size - 1][0]
    start = float(words[first]["start"]) - PAD_BEFORE_S
    if first > 0:
        start = max(start, float(words[first - 1]["end"]))
    end = float(words[last]["end"]) + PAD_AFTER_S
    if last + 1 < len(words):
        end = min(end, float(words[last + 1]["start"]) + 0.05)
    return Located(max(0.0, round(start, 3)), round(end, 3), confidence, first, last)


def build_plan(
    session: Session, video: Video, items: list[ShortIn], source: str, skipped: bool = False
) -> ShortsPlan:
    """Localiza e valida todos os trechos; qualquer problema vira UMA mensagem com a lista completa."""
    if len(items) > MAX_SHORTS:
        raise InvalidState(f"No máximo {MAX_SHORTS} Shorts por vídeo.")
    words, timing = load_words(session, video)
    errors: list[str] = []
    selections: list[ShortSelection] = []
    for n, item in enumerate(items, start=1):
        found = locate(words, item.exact_text)
        if found is None:
            errors.append(
                f"Short {n} ({item.title!r}): o texto não foi encontrado na narração. "
                "Copie o trecho EXATAMENTE como está no roteiro aprovado."
            )
            continue
        duration = found.end - found.start
        if not MIN_SECONDS <= duration <= MAX_SECONDS:
            errors.append(
                f"Short {n} ({item.title!r}): {duration:.0f} s. "
                f"A duração precisa ficar entre {MIN_SECONDS:.0f} e {MAX_SECONDS:.0f} segundos."
            )
            continue
        selections.append(
            ShortSelection(
                **item.model_dump(),
                start_s=found.start,
                end_s=found.end,
                source="claude" if source == "claude" else "user",
                located="exact" if found.confidence >= EXACT_CONFIDENCE else "fuzzy",
                confidence=round(found.confidence, 3),
            )
        )
    ordered = sorted(selections, key=lambda s: s.start_s)
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b.start_s < a.end_s - 0.01:
            errors.append(
                f"Os Shorts {a.title!r} e {b.title!r} se sobrepõem. Escolha trechos distintos."
            )
    if errors:
        raise InvalidState(" ".join(errors))
    return ShortsPlan(
        timing="estimated" if timing == "estimated" else "forced",
        shorts=selections,
        skipped=skipped,
    )


# ----- Versões salvas (ativo `shorts_plan`) -----


def _folder(session: Session, video: Video) -> Path:
    channel = channels.get_channel(session, video.channel_id)
    assert video.id is not None
    path = storage.video_dir(channel.slug, video.id, create=True) / "shorts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_versions(session: Session, video_id: int) -> list[Asset]:
    stmt = (
        select(Asset)
        .where(Asset.video_id == video_id, Asset.type == AssetType.SHORTS_PLAN.value)
        .order_by(col(Asset.id))
    )
    return list(session.exec(stmt))


def save_plan(session: Session, video_id: int, plan: ShortsPlan, source: str, author: str) -> Asset:
    """Salva SEMPRE uma versão nova (nunca sobrescreve)."""
    video = videos.get_video(session, video_id)
    number = len(list_versions(session, video_id)) + 1
    path = _folder(session, video) / f"shorts_plan_v{number}.json"
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    asset = Asset(
        video_id=video_id,
        type=AssetType.SHORTS_PLAN.value,
        path=narration.relpath(path),
        meta={"source": source, "author": author, "number": number, "count": len(plan.shorts)},
        based_on_script_version_id=video.approved_script_version_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def load_plan(asset: Asset) -> ShortsPlan:
    return ShortsPlan.model_validate_json(
        (get_settings().data_dir / asset.path).read_text(encoding="utf-8")
    )


def latest_plan(session: Session, video_id: int) -> tuple[Asset, ShortsPlan] | None:
    versions = list_versions(session, video_id)
    if not versions:
        return None
    return versions[-1], load_plan(versions[-1])


def save_selections(
    session: Session, video_id: int, items: list[ShortIn], source: str, author: str
) -> tuple[Asset, ShortsPlan]:
    video = videos.get_video(session, video_id)
    plan = build_plan(session, video, items, source)
    return save_plan(session, video_id, plan, source, author), plan


def set_skipped(session: Session, video_id: int, skipped: bool, author: str) -> ShortsPlan:
    """Marca/desmarca "não fazer Shorts" (nova versão do plano, mantendo os trechos)."""
    found = latest_plan(session, video_id)
    plan = found[1] if found else ShortsPlan()
    plan = plan.model_copy(update={"skipped": skipped})
    save_plan(session, video_id, plan, "user", author)
    return plan
