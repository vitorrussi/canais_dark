import hashlib
import secrets

from sqlmodel import Session, select

from canaisdark.models import User

TOKEN_PREFIX = "cd_"


class UserError(Exception):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(session: Session, name: str, email: str) -> User:
    if session.exec(select(User).where(User.email == email)).first():
        raise UserError(f"Já existe usuário com o e-mail {email}")
    user = User(name=name, email=email)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def issue_token(session: Session, user: User) -> str:
    """Gera um novo token (invalida o anterior). O valor em claro só é retornado aqui."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    user.token_hash = hash_token(token)
    session.add(user)
    session.commit()
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
