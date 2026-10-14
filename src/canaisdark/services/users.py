import hashlib
import secrets

from sqlmodel import Session, col, select

from canaisdark.models import User

TOKEN_PREFIX = "cd_"


class UserError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(session: Session, name: str, email: str, is_admin: bool = False) -> User:
    email = email.strip().lower()
    if session.exec(select(User).where(User.email == email)).first():
        raise UserError(f"Já existe usuário com o e-mail {email}")
    if not name.strip() or "@" not in email:
        raise UserError("Informe um nome e um e-mail válido")
    user = User(name=name.strip(), email=email.strip().lower(), is_admin=is_admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def issue_token(session: Session, user: User) -> str:
    """Gera um novo token (invalida o anterior e os conectores OAuth). O valor em claro só é retornado aqui."""
    from canaisdark.services import oauth

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    user.token_hash = hash_token(token)
    session.add(user)
    session.commit()
    if user.id is not None:  # rotacionar = suspeita de vazamento: derruba também os conectores
        oauth.revoke_for_user(session, user.id)
    return token


def authenticate_token(session: Session, token: str) -> User | None:
    candidate = hash_token(token)
    user = session.exec(select(User).where(User.token_hash == candidate)).first()
    if user is None or not user.is_active or user.token_hash is None:
        return None
    return user if secrets.compare_digest(user.token_hash, candidate) else None


def deactivate_user(session: Session, user: User) -> None:
    user.is_active = False
    user.token_hash = None
    session.add(user)
    session.commit()


def get_by_email(session: Session, email: str) -> User | None:
    return session.exec(select(User).where(User.email == email)).first()


def list_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(col(User.id))))


def get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserError(f"Usuário {user_id} não encontrado")
    return user


def _active_admins(session: Session) -> int:
    return len([u for u in list_users(session) if u.is_active and u.is_admin])


def set_active(session: Session, user: User, active: bool, actor: User) -> None:
    if not active:
        if user.id == actor.id:
            raise UserError("Você não pode desativar a si mesmo")
        if user.is_admin and _active_admins(session) <= 1:
            raise UserError("É preciso manter ao menos um administrador ativo")
        user.token_hash = None  # desativar também revoga o token
    user.is_active = active
    session.add(user)
    session.commit()


def set_admin(session: Session, user: User, admin: bool, actor: User) -> None:
    if not admin and user.is_admin and _active_admins(session) <= 1:
        raise UserError("É preciso manter ao menos um administrador ativo")
    user.is_admin = admin
    session.add(user)
    session.commit()


AUTHOR_LABELS = {
    "claude": "Claude (via {name})",
    "user": "{name}",
    "import": "Importado por {name}",
    "copy": "Cópia feita por {name}",
}


def author_label(session: Session, author: str) -> str:
    """Converte `claude:1` / `user:2` em texto legível com o nome da pessoa."""
    kind, _, raw = author.partition(":")
    template = AUTHOR_LABELS.get(kind)
    if template is None or not raw.isdigit():
        return author
    user = session.get(User, int(raw))
    return template.format(name=user.name if user else f"usuário {raw}")
