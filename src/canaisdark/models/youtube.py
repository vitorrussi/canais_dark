from datetime import datetime

from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class YouTubeAccount(SQLModel, table=True):
    """Conexão OAuth de UM canal do estúdio com UM canal do YouTube (refresh token criptografado)."""

    __tablename__ = "youtube_account"

    id: int | None = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channel.id", unique=True, index=True)
    refresh_token_enc: str  # Fernet; nunca exibir/logar/exportar
    scopes: str = ""
    youtube_channel_id: str = ""
    youtube_channel_title: str = ""
    connected_by: int | None = Field(default=None, foreign_key="user.id")
    connected_at: datetime = Field(default_factory=utcnow)
    needs_reconnect: bool = False  # invalid_grant: o token foi revogado/expirou
