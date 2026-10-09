from pydantic import BaseModel, Field

MAX_PROMPT_CHARS = 1500


class PromptIn(BaseModel):
    """Prompt de uma cena. `scene_index` é 0-based (igual ao `index` devolvido por get_script)."""

    scene_index: int = Field(ge=0)
    image_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    video_prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    vertical: bool | None = None  # cena merece versão 9:16 para Shorts
    key_scene: bool | None = None  # cena-chave (candidata a clipe de vídeo de IA)


class SceneUpdate(BaseModel):
    image_prompt: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    video_prompt: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    wants_vertical: bool | None = None
    is_key_scene: bool | None = None


class SavePromptsRequest(BaseModel):
    prompts: list[PromptIn] = Field(min_length=1)
