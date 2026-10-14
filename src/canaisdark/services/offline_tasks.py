"""Modo sem MCP: prompt autossuficiente para colar no Claude e aplicação da resposta (JSON).

Usa os MESMOS services das tools MCP (`save_script`, `save_scene_prompts`...), então as validações
e o histórico são idênticos. Nada aqui aprova, renderiza ou envia a terceiros.
"""

import json
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session, col, select

from canaisdark.models import ClaudeTask, TaskStatus, TaskType, User
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.schemas.scene import PromptIn
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.shorts import ShortIn
from canaisdark.schemas.timeline import Timeline
from canaisdark.services import metadata as metadata_service
from canaisdark.services import scene_prompts, scripts, task_context, tasks, videos
from canaisdark.services import shorts as shorts_service
from canaisdark.services import timeline as timeline_service
from canaisdark.services.errors import DomainError, InvalidState

# edit_video/make_thumbnail exigem o sandbox do claude.ai (baixa/envia arquivos): não existem no modo copiar/colar
_SANDBOX_ONLY = (TaskType.EDIT_VIDEO, TaskType.MAKE_THUMBNAIL)
SUPPORTED = tuple(t.value for t in TaskType if t not in _SANDBOX_ONLY)

_TOOL_WORDS = ("save_", "complete_task", "fail_task", "claim_next", "Salve com", "chame ", "Chame ")

FORMATS: dict[str, str] = {
    TaskType.GENERATE_SCRIPT.value: (
        '{"scenes": [{"text": "texto exato da narração da cena 1", "notes": ""}, '
        '{"text": "texto da cena 2", "notes": ""}]}'
    ),
    TaskType.GENERATE_PROMPTS.value: (
        '{"prompts": [{"scene_index": 0, "image_prompt": "an abandoned lighthouse at dusk, wide shot, fog", '
        '"vertical": true, "key_scene": false, "video_prompt": ""}]}'
    ),
    TaskType.GENERATE_TIMELINE.value: (
        '{"timeline": {"scenes": [{"scene_index": 0, "motion": {"kind": "zoom_in", "intensity": 0.1}, '
        '"transition_in": {"kind": "crossfade", "duration": 0.6}, "texts": []}], '
        '"fade_in_s": 0.6, "fade_out_s": 0.8}}  // devolva o JSON COMPLETO, partindo de `default_timeline`'
    ),
    TaskType.SELECT_SHORTS.value: (
        '{"shorts": [{"exact_text": "trecho copiado EXATAMENTE do roteiro", "title": "Título curto", '
        '"hook_override": ""}]}'
    ),
    TaskType.GENERATE_METADATA.value: (
        '{"title": "Título do vídeo", "description": "Descrição...", "tags": ["tag1", "tag2"], '
        '"thumbnail_ideas": ["ideia 1", "ideia 2"]}'
    ),
}

LABELS: dict[str, str] = {
    TaskType.GENERATE_SCRIPT.value: "roteiro",
    TaskType.GENERATE_PROMPTS.value: "prompts de imagem",
    TaskType.GENERATE_TIMELINE.value: "timeline de edição",
    TaskType.SELECT_SHORTS.value: "seleção de Shorts",
    TaskType.GENERATE_METADATA.value: "título, descrição e tags",
}


def _check_type(task_type: str) -> None:
    if task_type not in SUPPORTED:
        raise InvalidState(f"Tipo de tarefa inválido '{task_type}'. Valores: {list(SUPPORTED)}")


def open_task(session: Session, video_id: int, task_type: str, actor: User) -> ClaudeTask:
    """Tarefa aberta (vídeo, tipo); cria uma se ainda não existir (1 aberta por par)."""
    _check_type(task_type)
    found = session.exec(
        select(ClaudeTask).where(
            ClaudeTask.video_id == video_id,
            ClaudeTask.type == task_type,
            col(ClaudeTask.status).in_(tasks.OPEN_STATUSES),
        )
    ).first()
    return found or tasks.create_task(session, video_id, TaskType(task_type), actor=actor)


def _offline_how_to(task_type: str) -> str:
    """`how_to` do MCP sem as frases que mandam chamar ferramentas."""
    text = task_context.HOW_TO[task_type]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not any(w in s for w in _TOOL_WORDS)]
    return " ".join(kept)


def build_prompt(session: Session, video_id: int, task_type: str, actor: User) -> str:
    task = open_task(session, video_id, task_type, actor)
    ctx = task_context.build_task_context(session, task)
    ctx.pop("how_to", None)
    ctx.pop("task", None)
    instructions = (task.instructions or "").strip()
    context_json = json.dumps(ctx, ensure_ascii=False, indent=1)
    parts = [
        'Você é o assistente de produção do "Estúdio de Canais Dark". Execute a tarefa abaixo e '
        "responda SOMENTE com um bloco de código JSON (```json ... ```), sem nenhum texto antes ou depois.",
        f"## Tarefa: {LABELS[task_type]}\n{_offline_how_to(task_type)}",
    ]
    if instructions:
        parts.append(f"## Pedido da pessoa\n{instructions}")
    parts.append(
        f"## Formato da resposta (siga exatamente estas chaves)\n```json\n{FORMATS[task_type]}\n```"
    )
    parts.append(f"## Contexto\n```json\n{context_json}\n```")
    return "\n\n".join(parts)


def extract_json(text: str) -> Any:
    """Aceita bloco ```json, texto em volta ou JSON puro."""
    raw = text.strip()
    if not raw:
        raise InvalidState("Cole a resposta do Claude na caixa antes de aplicar.")
    fenced = re.findall(r"```(?:json|JSON)?\s*\n?(.*?)```", raw, flags=re.DOTALL)
    candidates = [f.strip() for f in fenced] + [raw]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            candidates.append(raw[start : end + 1])
    last: Exception | None = None
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError as e:
            last = e
    raise InvalidState(
        f"Não consegui ler o JSON da resposta ({last}). Cole a resposta inteira do Claude, "
        "incluindo as chaves { }."
    )


def _validate(model: Any, data: Any, what: str) -> Any:
    try:
        return TypeAdapter(model).validate_python(data)
    except ValidationError as e:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
            for err in e.errors()[:6]
        )
        raise InvalidState(f"{what} inválido(a): {detail}") from e


def _pick(data: Any, key: str) -> Any:
    """Aceita {"key": ...} ou o valor direto."""
    if isinstance(data, dict) and key in data:
        return data[key]
    return data


def apply_result(
    session: Session, video_id: int, task_type: str, actor: User, text: str
) -> dict[str, Any]:
    """Valida a resposta e salva pelos mesmos services das tools MCP; conclui a tarefa."""
    _check_type(task_type)
    videos.get_video(session, video_id)
    data = extract_json(text)

    # 1) valida tudo ANTES de reservar/gravar qualquer coisa
    parsed: Any
    if task_type == TaskType.GENERATE_SCRIPT.value:
        parsed = _validate(list[SceneIn], _pick(data, "scenes"), "Roteiro")
    elif task_type == TaskType.GENERATE_PROMPTS.value:
        parsed = _validate(list[PromptIn], _pick(data, "prompts"), "Lista de prompts")
    elif task_type == TaskType.GENERATE_TIMELINE.value:
        parsed = _validate(Timeline, _pick(data, "timeline"), "Timeline")
    elif task_type == TaskType.SELECT_SHORTS.value:
        parsed = _validate(list[ShortIn], _pick(data, "shorts"), "Seleção de Shorts")
    else:
        parsed = _validate(MetadataIn, data, "Metadados")

    # 2) reserva a tarefa para esta pessoa
    task = open_task(session, video_id, task_type, actor)
    if task.status == TaskStatus.PENDING.value:
        claimed = tasks.claim_next(session, actor, task_type, video_id)
        if claimed is None or claimed.id != task.id:
            raise InvalidState(
                "A tarefa foi reservada por outra pessoa; tente de novo em instantes."
            )
        task = claimed
    assert task.id is not None

    # 3) grava e conclui; se o service recusar, devolve a tarefa à fila
    try:
        summary = _save(session, video_id, task_type, actor, parsed, task.id)
    except DomainError:
        tasks.release_task(session, task.id, actor)
        raise
    tasks.complete_task(
        session, task.id, actor, f"Aplicado manualmente (copiar/colar): {summary['summary']}"
    )
    return {"task_id": task.id, "type": task_type, **summary}


def _save(
    session: Session, video_id: int, task_type: str, actor: User, parsed: Any, task_id: int
) -> dict[str, Any]:
    author = f"claude:{actor.id}"
    if task_type == TaskType.GENERATE_SCRIPT.value:
        v = scripts.save_script(session, video_id, parsed, actor, task_id=task_id)
        return {"summary": f"roteiro v{v.number} com {len(parsed)} cenas", "version": v.number}
    if task_type == TaskType.GENERATE_PROMPTS.value:
        result = scene_prompts.save_prompts(session, video_id, parsed)
        return {
            "summary": f"{len(result.saved)} prompts salvos",
            "saved": len(result.saved),
            "missing_scene_indexes": result.missing,
        }
    if task_type == TaskType.GENERATE_TIMELINE.value:
        asset = timeline_service.save(session, video_id, parsed, "claude", author)
        return {
            "summary": f"timeline v{asset.meta['number']}",
            "version": asset.meta["number"],
        }
    if task_type == TaskType.SELECT_SHORTS.value:
        asset, plan = shorts_service.save_selections(session, video_id, parsed, "claude", author)
        return {
            "summary": f"{len(plan.shorts)} Shorts selecionados (v{asset.meta['number']})",
            "version": asset.meta["number"],
        }
    asset = metadata_service.save_proposal(session, video_id, parsed, "claude", author)
    return {
        "summary": f"proposta de metadados v{asset.meta['number']}",
        "version": asset.meta["number"],
    }
