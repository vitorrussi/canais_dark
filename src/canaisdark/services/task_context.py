"""Monta o pacote de contexto que o Claude recebe ao reservar uma tarefa."""

from typing import Any

from sqlmodel import Session

from canaisdark.models import ClaudeTask, TaskType
from canaisdark.services import channels, scripts, videos
from canaisdark.services.errors import NotFound

HOW_TO: dict[str, str] = {
    TaskType.GENERATE_SCRIPT.value: (
        "Escreva o roteiro narrado do vídeo, já dividido em cenas (uma ideia/imagem por cena, "
        "texto exatamente como será narrado: sem marcações de cena, sem emojis, sem rubricas). "
        "Siga idioma, tom/template, duração alvo e `words_per_scene` (palavras por cena) do canal "
        "(cerca de 150 palavras por minuto), e respeite SEMPRE `script_rules` e `default_script_instructions`. "
        "Se houver 'instructions' ou 'previous_script', trate-as como pedido de revisão. "
        "Depois chame save_script(video_id, scenes, task_id) e, em seguida, complete_task(task_id, resumo). "
        "Se não conseguir, chame fail_task(task_id, motivo)."
    ),
    TaskType.GENERATE_PROMPTS.value: (
        "Escreva UM prompt de imagem por cena do roteiro APROVADO (`scenes`). Escreva em INGLÊS, em frases curtas separadas por vírgula, "
        "descrevendo só o conteúdo visual da cena: sujeito, ação, cenário, enquadramento/plano, luz e clima. "
        "NÃO repita o estilo do canal (o prefixo `channel.visual_style_prompt` é adicionado automaticamente na hora de copiar) "
        "e NÃO peça texto, legendas, logotipos nem rostos de pessoas reais reconhecíveis. "
        "Varie os planos entre cenas seguidas (aberto, médio, detalhe, silhueta…) e mantenha coerência de época, lugar e elementos recorrentes. "
        "Marque `vertical=true` nas cenas visualmente fortes para virarem Shorts 9:16 (cerca de 20–30%) e `key_scene=true` (com `video_prompt` curto "
        "descrevendo o movimento) nas poucas cenas que merecem clipe de vídeo de IA (no máximo ~10%). Respeite `channel.script_rules`. "
        "Salve com save_scene_prompts(video_id, prompts, task_id) — pode salvar em lotes — e depois chame complete_task(task_id, resumo). "
        "Confira o campo `missing_scene_indexes` da resposta até ficar vazio."
    ),
    TaskType.GENERATE_TIMELINE.value: (
        "Refine a timeline de edição do vídeo. O sistema já gera uma timeline padrão (`default_timeline`); "
        "parta dela e ajuste só o que melhora o vídeo, devolvendo o JSON COMPLETO. Por cena escolha `motion` "
        "(kind: zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, static; intensity 0 a 0,3, sugestão 0,06 a 0,12) "
        "combinando com o conteúdo (revelação = zoom_in; ambiente/panorama = pan; tensão crescente = zoom_in lento; "
        "nunca o mesmo movimento em cenas vizinhas) e `transition_in` (crossfade 0,3 a 0,8 s; cut em cenas muito curtas "
        "ou em quebras de assunto). Use `texts` (até 4 por cena, curtos; start_s/end_s relativos à cena) só para dados "
        "que merecem destaque (datas, nomes de lugares). Cenas com clipe (`asset.kind = clip`) devem ficar `static`. "
        "NÃO mude `music` nem `subtitles` (são escolhas das pessoas). Salve com save_timeline(video_id, timeline, task_id) "
        "(o servidor valida e explica erros) e depois complete_task(task_id, resumo). Não inicie o render: isso é feito no site."
    ),
    TaskType.SELECT_SHORTS.value: (
        "Escolha de 2 a 3 trechos da narração para virarem Shorts verticais (etapa OPCIONAL, pedida pela pessoa). "
        "Cada trecho deve: ser contínuo (frases seguidas, podendo cruzar cenas), fazer sentido sozinho, ter um GANCHO forte "
        "nas primeiras palavras (pergunta, afirmação surpreendente, dado chocante), terminar em ponto final natural "
        "(ou com um fecho que instigue a ver o vídeo completo), e durar de 25 a 55 s (cerca de 60 a 140 palavras; use os "
        "tempos de `scenes`). Não repita nem sobreponha trechos e evite spoilers do final. COPIE `exact_text` EXATAMENTE como "
        "está em `scenes[].text` (mesmas palavras, na ordem); o servidor localiza o trecho no alinhamento e recusa o que não achar. "
        "`title` = título curto do Short (até ~60 caracteres); `hook_override` (opcional, até ~80) = texto curto para "
        "aparecer no topo nos primeiros segundos. Salve com save_short_selections(video_id, shorts, task_id) e depois "
        "complete_task(task_id, resumo). Não inicie o render: isso é feito no site."
    ),
    TaskType.GENERATE_METADATA.value: (
        "Escreva os metadados do vídeo para o YouTube, no idioma do vídeo, a partir do roteiro APROVADO (`scenes`). "
        "`title`: até 100 caracteres (ideal 50–70), específico e curioso, sem clickbait enganoso, sem < >, com a palavra-chave "
        "principal no começo. `description`: 1–2 parágrafos que resumem sem entregar o final, depois capítulos opcionais e "
        "hashtags (até 3); até 5000 bytes, sem < >. `tags`: 8–15 termos relevantes (sem #; no total até 500 caracteres). "
        "`thumbnail_ideas`: 2–4 ideias curtas (cena, texto de até 4 palavras, contraste) para a pessoa criar a imagem. "
        "Respeite o tom e as regras do canal (`channel.script_rules`). Salve com save_metadata(video_id, title, description, "
        "tags, thumbnail_ideas, task_id) e depois complete_task(task_id, resumo). A pessoa revisa e aplica no site; "
        "nada é enviado ao YouTube por aqui."
    ),
    TaskType.EDIT_VIDEO.value: (
        "Edição LIVRE do vídeo no seu sandbox (claude.ai com execução de código e rede liberada). Chame "
        "get_edit_package(video_id) e siga o `how_to` do manifesto: baixe os arquivos pelos links assinados, "
        "edite com ffmpeg/Python (movimento de câmera, transições, faixas de capítulo, selos de data, textos "
        "animados), mantenha a narração e a duração, e envie o MP4 em partes para `upload.url`. A resposta da "
        "última parte confirma o registro. Em seguida complete_task(task_id, resumo). Não aprove nem envie ao "
        "YouTube/Drive: a pessoa revisa no site. Siga o pedido em `instructions`."
    ),
}


def build_task_context(session: Session, task: ClaudeTask) -> dict[str, Any]:
    video = videos.get_video(session, task.video_id)
    channel = channels.get_channel(session, video.channel_id)
    previous: dict[str, Any] | None = None
    try:
        version, scenes = scripts.get_script(session, video.id or 0)
        previous = {
            "version": version.number,
            "approved": version.approved,
            "scenes": [{"index": s.index, "text": s.text, "notes": s.notes} for s in scenes],
        }
    except NotFound:
        pass
    scenes_ctx: list[dict[str, Any]] | None = None
    if task.type == TaskType.GENERATE_PROMPTS.value and video.approved_script_version_id:
        scenes_ctx = [
            {
                "index": sc.index,
                "text": sc.text,
                "notes": sc.notes,
                "start_s": sc.start_s,
                "end_s": sc.end_s,
                "existing_image_prompt": sc.image_prompt,
            }
            for sc in scripts.scenes_of(
                session, scripts.get_version(session, video.approved_script_version_id)
            )
        ]
    default_timeline: dict[str, Any] | None = None
    if task.type == TaskType.GENERATE_TIMELINE.value and video.approved_script_version_id:
        from canaisdark.services import timeline as tl_service

        timing = tl_service.collect_timing(session, video)
        assets = tl_service.main_assets(session, [sp.scene for sp in timing.spans])
        scenes_ctx = [
            {
                "index": sp.scene.index,
                "text": sp.scene.text,
                "start_s": round(sp.start, 2),
                "end_s": round(sp.end, 2),
                "length_s": round(sp.length, 2),
                "asset": {
                    "id": a.id,
                    "kind": "clip" if a.type == "video_clip" else "image",
                    "width": a.meta.get("width"),
                    "height": a.meta.get("height"),
                }
                if (a := assets.get(sp.scene.id or 0))
                else None,
            }
            for sp in timing.spans
        ]
        found = tl_service.latest(session, video.id or 0)
        default_timeline = (
            found[1] if found else tl_service.build_default(session, video)
        ).model_dump(mode="json")
    shorts_limits: dict[str, Any] | None = None
    metadata_ctx: dict[str, Any] | None = None
    if task.type == TaskType.GENERATE_METADATA.value and video.approved_script_version_id:
        from canaisdark.services import metadata as metadata_service
        from canaisdark.services import shorts as shorts_service

        scenes_ctx = [
            {"index": sc.index, "text": sc.text}
            for sc in scripts.scenes_of(
                session, scripts.get_version(session, video.approved_script_version_id)
            )
        ]
        found_shorts = shorts_service.latest_plan(session, video.id or 0)
        metadata_ctx = {
            "current": metadata_service.current(video),
            "limits": {"title_max": 100, "description_max_bytes": 5000, "tags_max_chars": 500},
            "shorts": [s.title for s in found_shorts[1].shorts] if found_shorts else [],
        }
    if task.type == TaskType.SELECT_SHORTS.value and video.approved_script_version_id:
        from canaisdark.schemas.shorts import MAX_SECONDS, MAX_SHORTS, MIN_SECONDS
        from canaisdark.services import timeline as tl_service

        timing = tl_service.collect_timing(session, video)
        scenes_ctx = [
            {
                "index": sp.scene.index,
                "text": sp.scene.text,
                "start_s": round(sp.start, 2),
                "end_s": round(sp.end, 2),
                "wants_vertical": sp.scene.wants_vertical,
            }
            for sp in timing.spans
        ]
        shorts_limits = {
            "min_seconds": MIN_SECONDS,
            "max_seconds": MAX_SECONDS,
            "recommended": "2 a 3 Shorts de 25 a 55 s",
            "max_shorts": MAX_SHORTS,
        }
    return {
        "default_timeline": default_timeline,
        "shorts_limits": shorts_limits,
        "metadata": metadata_ctx,
        "scenes": scenes_ctx,
        "task": {
            "id": task.id,
            "type": task.type,
            "instructions": task.instructions,
            "attempts": task.attempts,
        },
        "how_to": HOW_TO.get(task.type, "Sem instruções específicas para este tipo ainda."),
        "video": {
            "id": video.id,
            "topic": video.topic,
            "title": video.title,
            "language": video.language,
        },
        "channel": {
            "id": channel.id,
            "name": channel.name,
            "language": channel.language,
            "extra_languages": channel.extra_languages,
            "script_template": channel.script_template,
            "visual_style_prompt": channel.visual_style_prompt,
            "target_duration_minutes": channel.target_duration_minutes,
            "words_per_scene": channel.words_per_scene,
            "script_rules": channel.script_rules,
            "default_script_instructions": channel.default_script_instructions,
        },
        "previous_script": previous,
    }


def build_user_prompt(video_id: int, video_topic: str, channel_name: str, task_type: str) -> str:
    """Texto pronto para o usuário colar no Claude Code (com o MCP `canais` conectado)."""
    topic = " ".join(video_topic.split())
    topic = topic if len(topic) <= 200 else topic[:197] + "..."
    return (
        f"Use o MCP `canais` para processar a tarefa `{task_type}` do vídeo #{video_id} "
        f'(canal "{channel_name}"; tema: {topic}).\n'
        f"1. Chame claim_next_task(video_id={video_id}) e leia o contexto e o campo how_to.\n"
        "2. Execute o trabalho e salve com a ferramenta de escrita indicada (ex.: save_script).\n"
        "3. Chame complete_task com um resumo curto (ou fail_task se não for possível).\n"
        "Não aprove nada: a aprovação é feita no site."
    )
