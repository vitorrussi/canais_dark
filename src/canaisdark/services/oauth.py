"""OAuth do `/mcp` (conectores do Claude Desktop/claude.ai): clientes, códigos e tokens.

Tokens são opacos e aleatórios; só o SHA-256 fica no banco. Cada autorização tem um `grant_id`
que liga código, access e refresh, para revogar tudo de uma vez. Sem FastAPI/MCP aqui.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from canaisdark.models import OAuthClient, OAuthToken, User
from canaisdark.models.channel import utcnow

PENDING_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=90)
TOUCH_EVERY = timedelta(minutes=1)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _valid(row: OAuthToken | None, kind: str) -> OAuthToken | None:
    if row is None or row.kind != kind or row.revoked_at is not None:
        return None
    return row if _aware(row.expires_at) > utcnow() else None


def _find(session: Session, raw: str) -> OAuthToken | None:
    return session.exec(select(OAuthToken).where(OAuthToken.token_hash == _hash(raw))).first()


# ----- clientes -----


def save_client(session: Session, client_id: str, info: dict[str, Any]) -> None:
    session.add(OAuthClient(client_id=client_id, info=info))
    session.commit()


def get_client(session: Session, client_id: str) -> dict[str, Any] | None:
    row = session.exec(select(OAuthClient).where(OAuthClient.client_id == client_id)).first()
    return row.info if row else None


# ----- fluxo de autorização -----


def create_pending(
    session: Session, client_id: str, scopes: list[str], data: dict[str, Any]
) -> str:
    """Guarda o pedido validado pelo SDK e devolve o id que vai na URL de consentimento."""
    raw = _new_secret("req_")
    session.add(
        OAuthToken(
            kind="pending",
            token_hash=_hash(raw),
            client_id=client_id,
            scopes=scopes,
            data=data,
            expires_at=utcnow() + PENDING_TTL,
        )
    )
    session.commit()
    return raw


@dataclass
class PendingView:
    client_id: str
    client_name: str
    redirect_uri: str
    state: str | None


def get_pending(session: Session, req: str) -> PendingView | None:
    row = _valid(_find(session, req), "pending")
    if row is None:
        return None
    info = get_client(session, row.client_id) or {}
    return PendingView(
        client_id=row.client_id,
        client_name=str(info.get("client_name") or "Um aplicativo do Claude"),
        redirect_uri=str(row.data["redirect_uri"]),
        state=row.data.get("state"),
    )


def approve(session: Session, req: str, user: User) -> tuple[str, str, str | None]:
    """Consumiu o pedido pendente: cria o código (uso único) e devolve (redirect_uri, code, state)."""
    row = _valid(_find(session, req), "pending")
    if row is None:
        raise LookupError("pedido expirado")
    assert user.id is not None
    code = _new_secret("code_")
    row.revoked_at = utcnow()
    session.add(row)
    session.add(
        OAuthToken(
            kind="code",
            token_hash=_hash(code),
            grant_id=secrets.token_hex(8),
            user_id=user.id,
            client_id=row.client_id,
            scopes=row.scopes,
            data=row.data,
            expires_at=utcnow() + CODE_TTL,
        )
    )
    session.commit()
    return str(row.data["redirect_uri"]), code, row.data.get("state")


def deny(session: Session, req: str) -> tuple[str, str | None] | None:
    row = _valid(_find(session, req), "pending")
    if row is None:
        return None
    row.revoked_at = utcnow()
    session.add(row)
    session.commit()
    return str(row.data["redirect_uri"]), row.data.get("state")


# ----- troca de código / refresh -----


def load_code(session: Session, client_id: str, code: str) -> OAuthToken | None:
    row = _valid(_find(session, code), "code")
    return row if row and row.client_id == client_id else None


def _issue_pair(
    session: Session, *, user_id: int, client_id: str, grant_id: str, scopes: list[str]
) -> tuple[str, str, int]:
    access, refresh = _new_secret("at_"), _new_secret("rt_")
    now = utcnow()
    session.add(
        OAuthToken(
            kind="access",
            token_hash=_hash(access),
            grant_id=grant_id,
            user_id=user_id,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TTL,
        )
    )
    session.add(
        OAuthToken(
            kind="refresh",
            token_hash=_hash(refresh),
            grant_id=grant_id,
            user_id=user_id,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TTL,
        )
    )
    session.commit()
    return access, refresh, int(ACCESS_TTL.total_seconds())


def exchange_code(session: Session, code_row: OAuthToken) -> tuple[str, str, int]:
    """Troca o código (consumido, uso único) por access + refresh."""
    assert code_row.user_id is not None
    code_row.revoked_at = utcnow()
    session.add(code_row)
    return _issue_pair(
        session,
        user_id=code_row.user_id,
        client_id=code_row.client_id,
        grant_id=code_row.grant_id,
        scopes=code_row.scopes,
    )


def load_refresh(session: Session, client_id: str, raw: str) -> OAuthToken | None:
    row = _valid(_find(session, raw), "refresh")
    if row is None or row.client_id != client_id:
        return None
    return row if _user_active(session, row.user_id) else None


def exchange_refresh(
    session: Session, refresh_row: OAuthToken, scopes: list[str] | None = None
) -> tuple[str, str, int]:
    """Rotaciona: o refresh usado é revogado e um par novo (com 90 dias) é emitido."""
    assert refresh_row.user_id is not None
    refresh_row.revoked_at = utcnow()
    session.add(refresh_row)
    return _issue_pair(
        session,
        user_id=refresh_row.user_id,
        client_id=refresh_row.client_id,
        grant_id=refresh_row.grant_id,
        scopes=scopes or refresh_row.scopes,
    )


# ----- verificação -----


def _user_active(session: Session, user_id: int | None) -> bool:
    user = session.get(User, user_id) if user_id is not None else None
    return bool(user and user.is_active)


def load_access(session: Session, raw: str) -> OAuthToken | None:
    row = _valid(_find(session, raw), "access")
    return row if row and _user_active(session, row.user_id) else None


def authenticate_access(session: Session, raw: str) -> User | None:
    """Usuário dono do access token (para o middleware do /mcp)."""
    row = load_access(session, raw)
    if row is None or row.user_id is None:
        return None
    now = utcnow()
    if row.last_used_at is None or now - _aware(row.last_used_at) > TOUCH_EVERY:
        row.last_used_at = now
        session.add(row)
        session.commit()
    return session.get(User, row.user_id)


# ----- revogação e gestão -----


def revoke_by_raw(session: Session, raw: str) -> None:
    row = _find(session, raw)
    if row is not None and row.grant_id:
        _revoke_grant_rows(session, row.grant_id)


def _revoke_grant_rows(session: Session, grant_id: str, user_id: int | None = None) -> int:
    stmt = select(OAuthToken).where(
        OAuthToken.grant_id == grant_id, col(OAuthToken.revoked_at).is_(None)
    )
    if user_id is not None:
        stmt = stmt.where(OAuthToken.user_id == user_id)
    rows = session.exec(stmt).all()
    now = utcnow()
    for r in rows:
        r.revoked_at = now
        session.add(r)
    session.commit()
    return len(rows)


def revoke_for_user(session: Session, user_id: int) -> int:
    rows = session.exec(
        select(OAuthToken).where(
            OAuthToken.user_id == user_id, col(OAuthToken.revoked_at).is_(None)
        )
    ).all()
    now = utcnow()
    for r in rows:
        r.revoked_at = now
        session.add(r)
    session.commit()
    return len(rows)


@dataclass
class GrantView:
    grant_id: str
    client_name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


def list_grants(session: Session, user_id: int) -> list[GrantView]:
    """Conectores com refresh token válido (ainda autorizados) deste usuário."""
    refreshes = session.exec(
        select(OAuthToken).where(
            OAuthToken.user_id == user_id,
            OAuthToken.kind == "refresh",
            col(OAuthToken.revoked_at).is_(None),
        )
    ).all()
    out: list[GrantView] = []
    for r in refreshes:
        if _aware(r.expires_at) <= utcnow():
            continue
        used = session.exec(
            select(OAuthToken.last_used_at)
            .where(OAuthToken.grant_id == r.grant_id, OAuthToken.kind == "access")
            .order_by(col(OAuthToken.last_used_at).desc())
        ).first()
        info = get_client(session, r.client_id) or {}
        out.append(
            GrantView(
                grant_id=r.grant_id,
                client_name=str(info.get("client_name") or "Aplicativo do Claude"),
                created_at=r.created_at,
                last_used_at=used,
                expires_at=r.expires_at,
            )
        )
    return sorted(out, key=lambda g: g.created_at, reverse=True)


def revoke_grant(session: Session, user_id: int, grant_id: str) -> bool:
    return _revoke_grant_rows(session, grant_id, user_id) > 0
