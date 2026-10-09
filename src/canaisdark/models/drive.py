from datetime import datetime

from sqlmodel import Field, SQLModel

from canaisdark.models.channel import utcnow


class DriveAccount(SQLModel, table=True):
    """Conexão OAuth (escopo drive.file) de UM canal do estúdio com UM Google Drive."""

    __tablename__ = "drive_account"

    id: int | None = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channel.id", unique=True, index=True)
    refresh_token_enc: str  # Fernet; nunca exibir/logar/exportar
    scopes: str = ""
    account_email: str = ""
    connected_by: int | None = Field(default=None, foreign_key="user.id")
    connected_at: datetime = Field(default_factory=utcnow)
    needs_reconnect: bool = False
