"""Prompts de imagem por cena: composição, salvamento (Claude/UI), reaproveitamento e pacote para copiar/baixar."""

import json
from dataclasses import dataclass

from sqlmodel import Session, select

from canaisdark.models import Asset, Channel, ClaudeTask, Scene, TaskType, User
from canaisdark.schemas.scene import PromptIn, SceneUpdate
from canaisdark.services import channels, scripts, tasks, videos
from canaisdark.services.errors import InvalidState, NotFound

ASPECT_SUFFIX = {
    False: "wide 16:9 landscape composition",
    True: "vertical 9:16 portrait composition",
}


def compose_prompt(channel: Channel, scene: Scene, vertical: bool = False) -> str:
    """Prefixo fixo de estilo do canal + trecho da cena + proporção. Vazio se a cena ainda não tem prompt."""
    body = scene.image_prompt.strip().rstrip(".,; ")
    if not body:
        return ""
    parts = [channel.visual_style_prompt.strip().rstrip(".,; "), body, ASPECT_SUFFIX[vertical]]
    return ", ".join(p for p in parts if p)


def approved_scenes(session: Session, video_id: int) -> list[Scene]:
    video = videos.get_video(session, video_id)
    if video.approved_script_version_id is None:
        raise InvalidState(
            "O roteiro ainda não foi aprovado: aprove-o antes de criar prompts de imagem."
        )
    return scripts.scenes_of(
        session, scripts.get_version(session, video.approved_script_version_id)
    )


@dataclass
class SaveResult:
    saved: list[int]
    missing: list[int]  # cenas (0-based) ainda sem image_prompt


def save_prompts(session: Session, video_id: int, items: list[PromptIn]) -> SaveResult:
    if not items:
        raise InvalidState("Envie ao menos um prompt em `prompts`.")
    scenes = approved_scenes(session, video_id)
    by_index = {s.index: s for s in scenes}
    unknown = sorted({i.scene_index for i in items if i.scene_index not in by_index})
    if unknown:
        raise InvalidState(
            f"scene_index inexistente: {unknown}. O roteiro aprovado tem {len(scenes)} cenas (índices 0 a {len(scenes) - 1})."
        )
    saved = []
    for item in items:
        scene = by_index[item.scene_index]
        scene.image_prompt = item.image_prompt.strip()
        scene.video_prompt = item.video_prompt.strip()
        if item.vertical is not None:
            scene.wants_vertical = item.vertical
        if item.key_scene is not None:
            scene.is_key_scene = item.key_scene
        session.add(scene)
        saved.append(scene.index)
    session.commit()
    return SaveResult(
        saved=sorted(saved), missing=[s.index for s in scenes if not s.image_prompt.strip()]
    )


def update_scene(session: Session, video_id: int, scene_id: int, data: SceneUpdate) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None or scene.video_id != video_id:
        raise NotFound(f"Cena {scene_id} não encontrada neste vídeo")
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(scene, key, value.strip() if isinstance(value, str) else value)
    session.add(scene)
    session.commit()
    session.refresh(scene)
    return scene


def prompt_coverage(scenes: list[Scene]) -> tuple[int, int]:
    return sum(1 for s in scenes if s.image_prompt.strip()), len(scenes)


def carry_over(session: Session, from_version_id: int, to_version_id: int) -> dict[str, int]:
    """Cenas com texto idêntico herdam prompts, flags e assets da versão anterior (assets são re-vinculados)."""
    old = scripts.scenes_of(session, scripts.get_version(session, from_version_id))
    new = scripts.scenes_of(session, scripts.get_version(session, to_version_id))
    pool: dict[str, list[Scene]] = {}
    for s in old:
        pool.setdefault(" ".join(s.text.split()), []).append(s)
    carried_prompts = carried_assets = 0
    for scene in new:
        candidates = pool.get(" ".join(scene.text.split()))
        if not candidates:
            continue
        src = candidates.pop(0)
        if not scene.image_prompt.strip() and src.image_prompt.strip():
            scene.image_prompt, scene.video_prompt = src.image_prompt, src.video_prompt
            scene.wants_vertical, scene.is_key_scene = src.wants_vertical, src.is_key_scene
            carried_prompts += 1
        for asset in session.exec(select(Asset).where(Asset.scene_id == src.id)):
            asset.scene_id = scene.id
            session.add(asset)
            carried_assets += 1
        session.add(scene)
    session.commit()
    return {"prompts": carried_prompts, "assets": carried_assets}


# ----- Pacote para copiar/baixar -----


def package(session: Session, video_id: int, fmt: str) -> tuple[str, str, str]:
    """(nome_do_arquivo, mime, conteúdo) com os prompts completos (prefixo do canal já aplicado)."""
    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    scenes = approved_scenes(session, video_id)
    stem = f"{channel.slug}-{video.id}-prompts"
    if fmt == "json":
        data = [
            {
                "scene": s.index + 1,
                "narration": s.text,
                "image_prompt_full": compose_prompt(channel, s),
                "image_prompt_vertical": compose_prompt(channel, s, True)
                if s.wants_vertical
                else "",
                "video_prompt": s.video_prompt,
                "key_scene": s.is_key_scene,
            }
            for s in scenes
        ]
        return f"{stem}.json", "application/json", json.dumps(data, ensure_ascii=False, indent=2)
    lines: list[str] = []
    for s in scenes:
        full = compose_prompt(channel, s) or "(sem prompt ainda)"
        if fmt == "md":
            lines.append(f"## Cena {s.index + 1}\n\n> {s.text}\n\n{full}\n")
            if s.wants_vertical:
                lines.append(f"**Vertical 9:16:** {compose_prompt(channel, s, True)}\n")
            if s.video_prompt:
                lines.append(f"**Prompt de vídeo:** {s.video_prompt}\n")
        elif fmt == "txt":
            lines.append(f"[Cena {s.index + 1}] {full}")
            if s.wants_vertical:
                lines.append(
                    f"[Cena {s.index + 1} — vertical 9:16] {compose_prompt(channel, s, True)}"
                )
        else:
            raise InvalidState("Formato inválido. Use: txt, md ou json")
    sep = "\n" if fmt == "md" else "\n\n"
    mime = "text/markdown; charset=utf-8" if fmt == "md" else "text/plain; charset=utf-8"
    return f"{stem}.{fmt}", mime, sep.join(lines) + "\n"


def request_prompts(session: Session, video_id: int, instructions: str, actor: User) -> ClaudeTask:
    """Enfileira a tarefa `generate_prompts` para o Claude (exige roteiro aprovado)."""
    approved_scenes(session, video_id)  # valida
    return tasks.create_task(
        session, video_id, TaskType.GENERATE_PROMPTS, instructions.strip(), actor
    )


# ----- Prompts em lote (várias imagens numa única mensagem) -----

BATCH_PREAMBLE = (
    "Gere as {n} imagens abaixo, UMA POR VEZ, na ordem, sem pular nenhuma e sem juntar várias numa só imagem. "
    "Em cada imagem: proporção {aspect}; sem texto, legendas, logotipos nem marca d'água. "
    "Estilo visual para TODAS: {style}. "
    'Se não conseguir gerar todas de uma vez, gere o máximo possível e eu digo "continue" para você seguir do próximo item.'
)
ASPECT_PT = {False: "16:9 (paisagem/widescreen)", True: "9:16 (vertical)"}


def batch_prompts(
    session: Session,
    video_id: int,
    size: int = 10,
    vertical: bool = False,
    only_missing: bool = False,
) -> dict[str, object]:
    """Divide as cenas em lotes de `size` e monta UMA mensagem por lote (o estilo vai uma vez, no cabeçalho).

    `size=0` = tudo numa mensagem só. Cenas sem prompt são puladas (e listadas em `skipped`)."""
    from canaisdark.services import scene_assets

    video = videos.get_video(session, video_id)
    channel = channels.get_channel(session, video.channel_id)
    scenes = approved_scenes(session, video_id)
    amap = scene_assets.scene_assets(session, [s.id for s in scenes if s.id])
    variant = "vertical" if vertical else "main"
    chosen: list[Scene] = []
    skipped: list[int] = []
    for sc in scenes:
        if vertical and not sc.wants_vertical:
            continue
        if only_missing and variant in amap.get(sc.id or 0, {}):
            continue
        if not sc.image_prompt.strip():
            skipped.append(sc.index + 1)
            continue
        chosen.append(sc)
    style = (
        channel.visual_style_prompt.strip().rstrip(".,; ")
        or "cinematográfico, consistente entre as imagens"
    )
    step = size if size > 0 else max(len(chosen), 1)
    batches: list[dict[str, object]] = []
    for i in range(0, len(chosen), step):
        group = chosen[i : i + step]
        lines = [
            f"Cena {sc.index + 1:02d}: {sc.image_prompt.strip().rstrip('.,; ')}" for sc in group
        ]
        text = (
            BATCH_PREAMBLE.format(n=len(group), aspect=ASPECT_PT[vertical], style=style)
            + "\n\n"
            + "\n".join(lines)
        )
        batches.append(
            {
                "number": len(batches) + 1,
                "first": group[0].index + 1,
                "last": group[-1].index + 1,
                "count": len(group),
                "text": text,
            }
        )
    return {"batches": batches, "total": len(chosen), "skipped": skipped, "variant": variant}
