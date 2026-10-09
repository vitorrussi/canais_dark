from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Channel(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    description: str = ""
    youtube_url: str = ""
    language: str = "pt-BR"
    extra_languages: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    elevenlabs_voice_id: str = ""
    visual_style_prompt: str = ""
    script_template: str = ""
    target_duration_minutes: int = 8
    words_per_scene: int = 40
    tts_model: str = "eleven_flash_v2_5"
    tts_stability: float = 0.5
    tts_similarity: float = 0.75
    tts_speed: float = 1.0
    forced_alignment: bool = True
    script_rules: str = ""
    default_script_instructions: str = ""
    drive_folder_id: str = ""
    youtube_category_id: str = "24"  # 24 = Entretenimento; ver docs/youtube.md
    is_archived: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
