from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class OAuthClient(SQLModel, table=True):
    """Cliente registrado dinamicamente (Claude Desktop/claude.ai) no `/register`."""

    __tablename__ = "oauth_client"

    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(unique=True, index=True)
    info: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class OAuthToken(SQLModel, table=True):
    """Pedido pendente, código de autorização, access ou refresh token (só o hash é guardado)."""

    __tablename__ = "oauth_token"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # pending | code | access | refresh
    token_hash: str = Field(unique=True, index=True)
    grant_id: str = Field(default="", index=True)  # agrupa code/access/refresh de uma autorização
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    client_id: str = Field(index=True)
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
