from datetime import datetime

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    youtube_url: str = ""
    language: str = "pt-BR"
    extra_languages: list[str] = []
    elevenlabs_voice_id: str = ""
    visual_style_prompt: str = ""
    script_template: str = ""
    target_duration_minutes: int = Field(default=8, ge=1, le=240)
    drive_folder_id: str = ""
    words_per_scene: int = Field(default=40, ge=10, le=300)
    tts_model: str = "eleven_flash_v2_5"
    tts_stability: float = Field(default=0.5, ge=0, le=1)
    tts_similarity: float = Field(default=0.75, ge=0, le=1)
    tts_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    forced_alignment: bool = True
    script_rules: str = ""
    default_script_instructions: str = ""


class ChannelUpdate(BaseModel):
    """Todos opcionais; o slug não pode ser alterado."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    youtube_url: str | None = None
    language: str | None = None
    extra_languages: list[str] | None = None
    elevenlabs_voice_id: str | None = None
    visual_style_prompt: str | None = None
    script_template: str | None = None
    target_duration_minutes: int | None = Field(default=None, ge=1, le=240)
    drive_folder_id: str | None = None
    words_per_scene: int | None = Field(default=None, ge=10, le=300)
    tts_model: str | None = None
    tts_stability: float | None = Field(default=None, ge=0, le=1)
    tts_similarity: float | None = Field(default=None, ge=0, le=1)
    tts_speed: float | None = Field(default=None, ge=0.7, le=1.2)
    forced_alignment: bool | None = None
    script_rules: str | None = None
    default_script_instructions: str | None = None


class ChannelRead(ChannelCreate):
    id: int
    slug: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime
