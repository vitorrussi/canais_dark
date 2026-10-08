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


class ChannelRead(ChannelCreate):
    id: int
    slug: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime
