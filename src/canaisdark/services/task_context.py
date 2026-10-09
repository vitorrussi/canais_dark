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
    return {
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
