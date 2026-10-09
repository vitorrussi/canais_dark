import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from sqlmodel import Session

from canaisdark.config import get_settings, public_host
from canaisdark.db import get_engine
from canaisdark.mcp.auth import BearerAuthMiddleware
from canaisdark.mcp.context import current_user
from canaisdark.models import TaskType, User, VideoStatus
from canaisdark.schemas.metadata import MetadataIn
from canaisdark.schemas.scene import PromptIn
from canaisdark.schemas.script import SceneIn
from canaisdark.schemas.shorts import ShortIn
from canaisdark.schemas.timeline import Timeline
from canaisdark.services import (
    channels,
    jobs,
    metadata,
    render,
    scene_prompts,
    scripts,
    shorts_render,
    task_context,
    tasks,
    video_status,
    videos,
)
from canaisdark.services import (
    shorts as shorts_service,
)
from canaisdark.services import (
    timeline as tl_service,
)
from canaisdark.services.errors import DomainError

mcp = MCPServer(
    "canais-dark",
    instructions=(
        "Estúdio de Canais Dark. Fluxo de trabalho: list_pending_tasks → claim_next_task "
        "(reserva a tarefa e devolve todo o contexto) → executar → ferramenta de escrita "
        "(ex.: save_script) → complete_task. Em caso de problema use fail_task ou release_task."
    ),
)


@contextmanager
def _db() -> Iterator[tuple[Session, User]]:
    user = current_user.get()
    if user is None:
        raise ToolError("Não autenticado")
    with Session(get_engine()) as session:
        yield session, user


def safe[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Converte erros de domínio em mensagens claras para o Claude."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except DomainError as e:
            raise ToolError(str(e)) from e

    return wrapper


def _task_dict(t: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "type": t.type,
        "video_id": t.video_id,
        "status": t.status,
        "instructions": t.instructions,
        "claimed_by": t.claimed_by,
        "claimed_at": t.claimed_at.isoformat() if t.claimed_at else None,
    }


# ----- Leitura -----


@mcp.tool()
@safe
def list_channels() -> list[dict[str, Any]]:
    """Lista os canais ativos (id, nome, slug, idioma)."""
    with _db() as (s, _):
        return [
            {"id": c.id, "name": c.name, "slug": c.slug, "language": c.language}
            for c in channels.list_channels(s)
        ]


@mcp.tool()
@safe
def get_channel(channel_id: int) -> dict[str, Any]:
    """Detalhes do canal: idioma, template de roteiro, prompt de estilo visual, duração alvo etc."""
    with _db() as (s, _):
        c = channels.get_channel(s, channel_id)
        return {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "description": c.description,
            "language": c.language,
            "extra_languages": c.extra_languages,
            "script_template": c.script_template,
            "visual_style_prompt": c.visual_style_prompt,
            "target_duration_minutes": c.target_duration_minutes,
            "words_per_scene": c.words_per_scene,
            "script_rules": c.script_rules,
            "default_script_instructions": c.default_script_instructions,
            "elevenlabs_voice_id": c.elevenlabs_voice_id,
        }


@mcp.tool()
@safe
def list_videos(channel_id: int, status: str | None = None) -> list[dict[str, Any]]:
    """Lista os vídeos de um canal (mais novos primeiro). `status` opcional, ex.: 'draft'."""
    with _db() as (s, _):
        try:
            st = VideoStatus(status) if status else None
        except ValueError as e:
            raise ToolError(
                f"status inválido '{status}'. Valores: {[x.value for x in VideoStatus]}"
            ) from e
        return [
            {"id": v.id, "topic": v.topic[:120], "title": v.title, "status": v.status}
            for v in videos.list_videos(s, channel_id, st)
        ]


@mcp.tool()
@safe
def get_video(video_id: int) -> dict[str, Any]:
    """Resumo do vídeo: tema, metadados, estado de cada etapa e versões do roteiro.

    O texto das cenas não vem aqui; use get_script(video_id)."""
    with _db() as (s, _):
        v = videos.get_video(s, video_id)
        return {
            "id": v.id,
            "channel_id": v.channel_id,
            "topic": v.topic,
            "status": v.status,
            "title": v.title,
            "description": v.description,
            "tags": v.tags,
            "language": v.language,
            "approved_script_version_id": v.approved_script_version_id,
            "stages": [st.model_dump() for st in video_status.get_stages(s, v)],
            "script_versions": [
                {
                    "number": ver.number,
                    "approved": ver.approved,
                    "author": ver.author,
                    "scene_count": scripts.scene_count(s, ver.id or 0),
                }
                for ver in scripts.list_versions(s, video_id)
            ],
        }


@mcp.tool()
@safe
def get_script(video_id: int, version: int | None = None) -> dict[str, Any]:
    """Roteiro dividido em cenas. Sem `version`, devolve a mais recente."""
    with _db() as (s, _):
        ver, scenes = scripts.get_script(s, video_id, version)
        return {
            "version": ver.number,
            "approved": ver.approved,
            "scenes": [
                {
                    "index": x.index,
                    "text": x.text,
                    "notes": x.notes,
                    "image_prompt": x.image_prompt,
                    "wants_vertical": x.wants_vertical,
                    "is_key_scene": x.is_key_scene,
                }
                for x in scenes
            ],
        }


@mcp.tool()
@safe
def list_pending_tasks() -> list[dict[str, Any]]:
    """Tarefas abertas (pendentes ou em andamento) para o Claude, mais antigas primeiro."""
    with _db() as (s, _):
        return [_task_dict(t) for t in tasks.list_tasks(s, open_only=True)]


def _job_dict(j: Any, with_log: bool = False) -> dict[str, Any]:
    out = {
        "id": j.id,
        "type": j.type,
        "video_id": j.video_id,
        "status": j.status,
        "progress": j.progress,
        "error": j.error,
        "result": j.result,
        "created_at": j.created_at.isoformat(),
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }
    if with_log:
        out["log_tail"] = "\n".join(j.log.strip().splitlines()[-15:])
    return out


@mcp.tool()
@safe
def get_job_status(job_id: int) -> dict[str, Any]:
    """Estado de um job (narração/alinhamento): status, progresso 0-100, erro, resultado e fim do log.

    Jobs são disparados por pessoas no site (gastam créditos); aqui você só acompanha."""
    with _db() as (s, _):
        return _job_dict(jobs.get_job(s, job_id), with_log=True)


@mcp.tool()
@safe
def list_jobs(video_id: int | None = None, status: str | None = None) -> list[dict[str, Any]]:
    """Jobs recentes (mais novos primeiro). Filtros opcionais: video_id, status (queued|running|done|failed|cancelled)."""
    with _db() as (s, _):
        return [_job_dict(j) for j in jobs.list_jobs(s, video_id, status, limit=20)]


# ----- Tarefas -----


@mcp.tool()
@safe
def claim_next_task(type: str | None = None, video_id: int | None = None) -> dict[str, Any]:
    """Reserva a próxima tarefa pendente e devolve o contexto completo para executá-la.

    A reserva dura 30 minutos e evita que outra pessoa processe a mesma tarefa. `type` opcional
    (ex.: 'generate_script') e `video_id` opcional restringem a busca. Se não houver tarefas, retorna {"task": null}."""
    with _db() as (s, user):
        if type and type not in {t.value for t in TaskType}:
            raise ToolError(f"type inválido '{type}'. Valores: {[t.value for t in TaskType]}")
        task = tasks.claim_next(s, user, type, video_id)
        if task is None:
            return {"task": None, "message": "Nenhuma tarefa pendente."}
        return task_context.build_task_context(s, task)


@mcp.tool()
@safe
def complete_task(task_id: int, result_summary: str) -> dict[str, Any]:
    """Marca a tarefa reservada por você como concluída, com um resumo curto do que foi feito."""
    with _db() as (s, user):
        return _task_dict(tasks.complete_task(s, task_id, user, result_summary))


@mcp.tool()
@safe
def fail_task(task_id: int, reason: str) -> dict[str, Any]:
    """Marca a tarefa como falha, explicando o motivo (aparece no site)."""
    with _db() as (s, user):
        return _task_dict(tasks.fail_task(s, task_id, user, reason))


@mcp.tool()
@safe
def release_task(task_id: int) -> dict[str, Any]:
    """Devolve a tarefa à fila sem concluí-la (para outra pessoa/sessão pegar depois)."""
    with _db() as (s, user):
        return _task_dict(tasks.release_task(s, task_id, user))


# ----- Escrita -----


@mcp.tool()
@safe
def save_script(video_id: int, scenes: list[SceneIn], task_id: int | None = None) -> dict[str, Any]:
    """Salva uma NOVA versão do roteiro, já dividido em cenas (1 a 200).

    Cada cena: {"text": "texto exato da narração", "notes": "observação opcional"}.
    Não aprova o roteiro: a aprovação é humana, no site. Passe `task_id` para vincular à tarefa.
    Exemplo: save_script(42, [{"text": "Em 1938, uma carta chegou..."}, {"text": "Mas ninguém..."}], 7)"""
    with _db() as (s, user):
        v = scripts.save_script(s, video_id, scenes, user, task_id=task_id)
        return {"version": v.number, "scene_count": len(scenes), "approved": v.approved}


@mcp.tool()
@safe
def save_scene_prompts(
    video_id: int, prompts: list[PromptIn], task_id: int | None = None
) -> dict[str, Any]:
    """Salva prompts de imagem por cena do roteiro APROVADO (pode ser em lotes; salvar de novo sobrescreve a cena).

    Cada item: {"scene_index": 0-based, "image_prompt": "...", "video_prompt"?: "...", "vertical"?: bool, "key_scene"?: bool}.
    Escreva só o conteúdo da cena em inglês; o prefixo de estilo do canal é adicionado automaticamente.
    Retorna `missing_scene_indexes` (cenas ainda sem prompt). Exemplo:
    save_scene_prompts(42, [{"scene_index": 0, "image_prompt": "an abandoned lighthouse at dusk, wide shot, fog", "vertical": true}], 9)"""
    with _db() as (s, _):
        result = scene_prompts.save_prompts(s, video_id, prompts)
        return {
            "saved_scene_indexes": result.saved,
            "missing_scene_indexes": result.missing,
            "task_id": task_id,
        }


@mcp.tool()
@safe
def get_timeline(video_id: int) -> dict[str, Any]:
    """Timeline de edição atual do vídeo (a última versão salva ou, se não houver, a padrão automática) + tempos das cenas.

    Exige roteiro aprovado e narração com tempos."""
    with _db() as (s, _):
        video = videos.get_video(s, video_id)
        found = tl_service.latest(s, video_id)
        tl = found[1] if found else tl_service.build_default(s, video)
        timing = tl_service.collect_timing(s, video)
        return {
            "saved_version": found[0].meta.get("number") if found else None,
            "timeline": tl.model_dump(mode="json"),
            "scenes": [
                {"index": sp.scene.index, "start_s": round(sp.start, 2), "end_s": round(sp.end, 2)}
                for sp in timing.spans
            ],
        }


@mcp.tool()
@safe
def save_timeline(video_id: int, timeline: Timeline, task_id: int | None = None) -> dict[str, Any]:
    """Salva uma NOVA versão da timeline de edição (nunca sobrescreve; o servidor valida tudo).

    `timeline`: {"scenes": [{"scene_index": 0, "motion": {"kind": "zoom_in", "intensity": 0.1},
    "transition_in": {"kind": "crossfade", "duration": 0.6}, "texts": [{"text": "1900", "position": "top"}]}, ...],
    "fade_in_s": 0.6, "fade_out_s": 0.8, "effects": {"vignette": true, "grain": false}}.
    Movimentos: zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, static. Transições: cut, crossfade.
    Parta de get_timeline e envie o JSON completo."""
    with _db() as (s, user):
        asset = tl_service.save(s, video_id, timeline, "claude", f"claude:{user.id}")
        return {"version": asset.meta["number"], "timeline_asset_id": asset.id, "task_id": task_id}


@mcp.tool()
@safe
def start_render(
    video_id: int,
    quality: str = "preview",
    subtitles: bool | None = None,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Enfileira o render (ffmpeg) e retorna `job_id` na hora; acompanhe com get_job_status.

    quality: "preview" (720p, rápido) ou "final" (1080p). Não gasta créditos; usa só a CPU do servidor.
    Exige roteiro aprovado, narração com tempos e imagens em todas as cenas (ou allow_missing=true para
    preencher cenas sem imagem com fundo escuro, só para testes)."""
    with _db() as (s, user):
        job = render.start(s, video_id, user, quality, subtitles, allow_missing)
        return {"job_id": job.id, "status": job.status, "quality": quality}


# ----- Shorts (etapa OPCIONAL) -----


@mcp.tool()
@safe
def get_shorts(video_id: int) -> dict[str, Any]:
    """Shorts do vídeo: seleção atual (trechos, tempos, duração), se foi dispensada e os arquivos já renderizados."""
    with _db() as (s, _):
        videos.get_video(s, video_id)
        found = shorts_service.latest_plan(s, video_id)
        rendered = shorts_render.best_for(s, video_id)
        plan = found[1] if found else None
        return {
            "version": found[0].meta.get("number") if found else None,
            "skipped": bool(plan and plan.skipped),
            "timing": plan.timing if plan else None,
            "shorts": [
                {
                    "index": i,
                    **sel.model_dump(),
                    "duration": round(sel.duration, 2),
                    "render": (
                        {
                            "asset_id": r.id,
                            "kind": r.meta.get("kind"),
                            "file_url": f"/assets/{r.id}/file",
                        }
                        if (r := rendered.get(i))
                        else None
                    ),
                }
                for i, sel in enumerate(plan.shorts if plan else [])
            ],
        }


@mcp.tool()
@safe
def save_short_selections(
    video_id: int, shorts: list[ShortIn], task_id: int | None = None
) -> dict[str, Any]:
    """Salva NOVA seleção de Shorts (2 a 3 trechos de 25 a 55 s). O servidor localiza cada `exact_text` na narração.

    shorts: [{"exact_text": "<trecho copiado EXATAMENTE do roteiro>", "title": "Título curto",
    "hook_override": "texto do topo (opcional)"}, ...]. Erros listam o que não foi encontrado/duração fora de
    15–59 s/sobreposição; corrija e reenvie. Não renderiza: a pessoa decide no site."""
    with _db() as (s, user):
        asset, plan = shorts_service.save_selections(
            s, video_id, shorts, "claude", f"claude:{user.id}"
        )
        return {
            "version": asset.meta["number"],
            "task_id": task_id,
            "timing": plan.timing,
            "shorts": [
                {
                    "index": i,
                    "title": sel.title,
                    "start_s": sel.start_s,
                    "end_s": sel.end_s,
                    "duration": round(sel.duration, 1),
                    "located": sel.located,
                }
                for i, sel in enumerate(plan.shorts)
            ],
        }


@mcp.tool()
@safe
def start_shorts(
    video_id: int,
    quality: str = "preview",
    index: int | None = None,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Enfileira o render dos Shorts 9:16 e retorna `job_id` (acompanhe com get_job_status).

    quality: "preview" (720×1280) ou "final" (1080×1920). index: 0-based para renderizar só um Short.
    Exige seleção salva e imagens nas cenas do trecho (allow_missing=true preenche com fundo escuro)."""
    with _db() as (s, user):
        job = shorts_render.start(s, video_id, user, quality, index, allow_missing)
        return {"job_id": job.id, "status": job.status, "quality": quality}


# ----- Metadados do YouTube -----


@mcp.tool()
@safe
def get_metadata(video_id: int) -> dict[str, Any]:
    """Título/descrição/tags atuais do vídeo, a última proposta salva e se ela já foi aplicada."""
    with _db() as (s, _):
        video = videos.get_video(s, video_id)
        found = metadata.latest(s, video_id)
        return {
            "current": metadata.current(video),
            "problems": metadata.problems(video),
            "proposal": (
                {
                    "version": found[0].meta.get("number"),
                    "applied": bool(found[0].meta.get("applied")),
                    **found[1].model_dump(),
                }
                if found
                else None
            ),
        }


@mcp.tool()
@safe
def save_metadata(
    video_id: int,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    thumbnail_ideas: list[str] | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """Salva uma NOVA proposta de metadados do YouTube (a pessoa revisa e aplica no site).

    title: até 100 caracteres, sem < >. description: até 5000 bytes. tags: sem #, até 500 caracteres no total.
    thumbnail_ideas: 2–4 ideias curtas. Nada é enviado ao YouTube por esta ferramenta."""
    try:
        data = MetadataIn(
            title=title,
            description=description,
            tags=tags or [],
            thumbnail_ideas=thumbnail_ideas or [],
        )
    except ValidationError as e:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
            for err in e.errors()
        )
        raise ToolError(f"Metadados inválidos: {detail}") from e
    with _db() as (s, user):
        asset = metadata.save_proposal(s, video_id, data, "claude", f"claude:{user.id}")
        return {"version": asset.meta["number"], "applied": False, "task_id": task_id}


# ----- Prompt (vira slash command no Claude Code) -----


@mcp.prompt()
def process_pending() -> str:
    """Processa todas as tarefas pendentes do Estúdio de Canais Dark."""
    return (
        "Processe as pendências do Estúdio de Canais Dark: repita até não haver mais tarefas — "
        "1) chame claim_next_task; 2) leia o contexto e o campo how_to; 3) execute o trabalho e "
        "salve com a ferramenta de escrita correspondente (ex.: save_script); 4) chame "
        "complete_task com um resumo curto (ou fail_task se não for possível). "
        "No fim, resuma o que foi processado. Não aprove nada: a aprovação é feita no site."
    )


def build_mcp_app() -> BearerAuthMiddleware:
    settings = get_settings()
    extra = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    if public_host(settings) and public_host(settings) not in extra:
        extra.append(public_host(settings))
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *extra],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
        + [f"https://{h}" for h in extra],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, transport_security=security
    )
    return BearerAuthMiddleware(app)
