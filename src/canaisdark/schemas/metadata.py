"""Metadados do YouTube (título, descrição, tags) e ideias de thumbnail, com os limites da plataforma."""

from pydantic import BaseModel, Field, field_validator, model_validator

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
THUMBNAIL_IDEAS_MAX = 5


def tags_length(tags: list[str]) -> int:
    """Tamanho como o YouTube conta: vírgulas entre tags e aspas em tags com espaço."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


class MetadataIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str = Field(default="", max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=40)
    thumbnail_ideas: list[str] = Field(default_factory=list, max_length=THUMBNAIL_IDEAS_MAX)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if "<" in v or ">" in v:
            raise ValueError("o título não pode conter < ou > (o YouTube recusa)")
        if not v:
            raise ValueError("o título não pode ficar vazio")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        v = v.strip()
        if "<" in v or ">" in v:
            raise ValueError("a descrição não pode conter < ou > (o YouTube recusa)")
        if len(v.encode()) > DESCRIPTION_MAX_BYTES:
            raise ValueError(f"a descrição passa de {DESCRIPTION_MAX_BYTES} bytes")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, tags: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in tags:
            tag = " ".join(raw.replace(",", " ").replace("#", "").split())
            if not tag or tag.lower() in seen:
                continue
            if "<" in tag or ">" in tag:
                raise ValueError("tags não podem conter < ou >")
            if len(tag) > 100:
                raise ValueError(f"tag longa demais ({len(tag)} caracteres; máximo 100)")
            seen.add(tag.lower())
            out.append(tag)
        return out

    @field_validator("thumbnail_ideas")
    @classmethod
    def _ideas(cls, ideas: list[str]) -> list[str]:
        return [i.strip()[:300] for i in ideas if i.strip()]

    @model_validator(mode="after")
    def _tags_total(self) -> "MetadataIn":
        total = tags_length(self.tags)
        if total > TAGS_MAX_CHARS:
            raise ValueError(
                f"as tags somam {total} caracteres (máximo {TAGS_MAX_CHARS}); remova algumas"
            )
        return self
