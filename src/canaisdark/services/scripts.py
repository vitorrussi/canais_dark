from sqlalchemy import func
from sqlmodel import Session, col, select

from canaisdark.models import ClaudeTask, Scene, ScriptVersion, TaskType, User, Video
from canaisdark.models.channel import utcnow
from canaisdark.schemas.script import MAX_SCENE_CHARS, MAX_SCENES, SceneIn
from canaisdark.services import tasks, videos
from canaisdark.services.errors import Conflict, InvalidState, NotFound


def save_script(
    session: Session,
    video_id: int,
    scenes: list[SceneIn],
    actor: User,
    author_kind: str = "claude",
    task_id: int | None = None,
) -> ScriptVersion:
    """Cria uma nova versão do roteiro (nunca sobrescreve versões anteriores)."""
    videos.get_video(session, video_id)
    if not 1 <= len(scenes) <= MAX_SCENES:
        raise InvalidState(
            f"O roteiro deve ter entre 1 e {MAX_SCENES} cenas (recebidas {len(scenes)})"
        )
    cleaned = []
    for i, scene in enumerate(scenes):
        text = scene.text.strip()
        if not text:
            raise InvalidState(f"A cena {i} está vazia")
        if len(text) > MAX_SCENE_CHARS:
            raise InvalidState(f"A cena {i} passa de {MAX_SCENE_CHARS} caracteres; divida-a")
        cleaned.append((text, scene.notes.strip()))
    if task_id is not None:
        task = session.get(ClaudeTask, task_id)
        if task is None or task.video_id != video_id:
            raise NotFound(f"Tarefa {task_id} não encontrada para o vídeo {video_id}")
    last = session.exec(
        select(func.max(ScriptVersion.number)).where(ScriptVersion.video_id == video_id)
    ).one()
    version = ScriptVersion(
        video_id=video_id,
        number=(last or 0) + 1,
        full_text="\n\n".join(t for t, _ in cleaned),
        author=f"{author_kind}:{actor.id}",
        task_id=task_id,
    )
    session.add(version)
    session.flush()
    assert version.id is not None
    for i, (text, notes) in enumerate(cleaned):
        session.add(
            Scene(video_id=video_id, script_version_id=version.id, index=i, text=text, notes=notes)
        )
    session.commit()
    session.refresh(version)
    return version


def list_versions(session: Session, video_id: int) -> list[ScriptVersion]:
    stmt = (
        select(ScriptVersion)
        .where(ScriptVersion.video_id == video_id)
        .order_by(col(ScriptVersion.number).desc())
    )
    return list(session.exec(stmt))


def get_version(session: Session, version_id: int) -> ScriptVersion:
    version = session.get(ScriptVersion, version_id)
    if version is None:
        raise NotFound(f"Versão de roteiro {version_id} não encontrada")
    return version


def get_script(
    session: Session, video_id: int, number: int | None = None
) -> tuple[ScriptVersion, list[Scene]]:
    """Versão pedida (ou a mais recente) com suas cenas, em ordem."""
    videos.get_video(session, video_id)
    stmt = select(ScriptVersion).where(ScriptVersion.video_id == video_id)
    if number is not None:
        stmt = stmt.where(ScriptVersion.number == number)
    else:
        stmt = stmt.order_by(col(ScriptVersion.number).desc())
    version = session.exec(stmt).first()
    if version is None:
        what = f"versão {number} do roteiro" if number else "roteiro"
        raise NotFound(f"O vídeo {video_id} ainda não tem {what}")
    return version, scenes_of(session, version)


def scenes_of(session: Session, version: ScriptVersion) -> list[Scene]:
    stmt = select(Scene).where(Scene.script_version_id == version.id).order_by(col(Scene.index))
    return list(session.exec(stmt))


def scene_count(session: Session, version_id: int) -> int:
    return session.exec(
        select(func.count()).select_from(Scene).where(Scene.script_version_id == version_id)
    ).one()


def approve_script(session: Session, version_id: int, actor: User) -> ScriptVersion:
    """Aprovação humana: só uma versão fica aprovada por vídeo."""
    version = get_version(session, version_id)
    video = session.get(Video, version.video_id)
    assert video is not None
    previous_approved = video.approved_script_version_id
    if (
        video.approved_script_version_id is not None
        and video.approved_script_version_id != version_id
    ):
        previous = get_version(session, video.approved_script_version_id)
        previous.approved = False
        session.add(previous)
    version.approved = True
    version.approved_by = actor.id
    version.approved_at = utcnow()
    video.approved_script_version_id = version_id
    video.updated_at = utcnow()
    session.add(version)
    session.add(video)
    session.commit()
    session.refresh(version)
    if previous_approved is not None and previous_approved != version_id:
        from canaisdark.services import scene_prompts

        scene_prompts.carry_over(
            session, previous_approved, version_id
        )  # reaproveita prompts/imagens de cenas iguais
    return version


def request_revision(session: Session, video_id: int, notes: str, actor: User) -> ClaudeTask:
    """Pede ao Claude uma nova versão do roteiro, com as observações do revisor."""
    if not notes.strip():
        raise InvalidState("Escreva o que deve ser ajustado no roteiro")
    return request_script(session, video_id, notes.strip(), actor)


def request_script(session: Session, video_id: int, instructions: str, actor: User) -> ClaudeTask:
    try:
        return tasks.create_task(session, video_id, TaskType.GENERATE_SCRIPT, instructions, actor)
    except Conflict as e:
        raise Conflict("Já há uma tarefa de roteiro aguardando/em andamento para este vídeo") from e


def save_user_script(
    session: Session, video_id: int, scenes: list[SceneIn], actor: User, approve: bool = False
) -> ScriptVersion:
    """Roteiro escrito/editado à mão: nova versão (autor `user:<id>`), opcionalmente já aprovada."""
    version = save_script(session, video_id, scenes, actor, author_kind="user")
    if approve:
        assert version.id is not None
        version = approve_script(session, version.id, actor)
    return version
