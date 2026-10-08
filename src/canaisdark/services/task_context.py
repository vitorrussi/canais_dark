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
        "Siga idioma, tom/template e duração alvo do canal (cerca de 150 palavras por minuto). "
        "Se houver 'instructions' ou 'previous_script', trate-as como pedido de revisão. "
        "Depois chame save_script(video_id, scenes, task_id) e, em seguida, complete_task(task_id, resumo). "
        "Se não conseguir, chame fail_task(task_id, motivo)."
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
    return {
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
        },
        "previous_script": previous,
    }
