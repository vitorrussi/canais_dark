from datetime import datetime

from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class ScriptVersion(SQLModel, table=True):
    __tablename__ = "script_version"

    id: int | None = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    number: int
    full_text: str
    approved: bool = False
    approved_by: int | None = Field(default=None, foreign_key="user.id")
    approved_at: datetime | None = None
    author: str  # "claude:<user_id>" | "user:<user_id>"
    task_id: int | None = Field(default=None, foreign_key="claude_task.id")
    created_at: datetime = Field(default_factory=utcnow)


class Scene(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    script_version_id: int = Field(foreign_key="script_version.id", index=True)
    index: int
    text: str
    notes: str = ""
    image_prompt: str = ""  # só a parte da cena; o prefixo de estilo do canal é composto na hora
    video_prompt: str = ""  # cenas-chave (clipe de vídeo de IA)
    wants_vertical: bool = False  # cena merece versão 9:16 (Shorts)
    is_key_scene: bool = False
    start_s: float | None = None  # preenchido pelo alinhamento (Fase 3)
    end_s: float | None = None
