from datetime import datetime

from pydantic import BaseModel, Field

MAX_SCENES = 200
MAX_SCENE_CHARS = 5000


class SceneIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SCENE_CHARS)
    notes: str = ""


class SceneRead(BaseModel):
    index: int
    text: str
    notes: str


class ScriptVersionRead(BaseModel):
    id: int
    video_id: int
    number: int
    approved: bool
    approved_at: datetime | None
    author: str
    created_at: datetime
    scene_count: int
    scenes: list[SceneRead] | None = None


class RevisionRequest(BaseModel):
    notes: str = Field(min_length=1)


class SaveScriptRequest(BaseModel):
    """Salvar roteiro editado por uma pessoa: sempre cria uma nova versão."""

    scenes: list[SceneIn]
    approve: bool = False
