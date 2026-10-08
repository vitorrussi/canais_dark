import pytest
from sqlmodel import Session

from canaisdark.models import User
from canaisdark.services import users


def test_token_roundtrip(session: Session, user_token: tuple[User, str]) -> None:
    user, token = user_token
    assert token.startswith("cd_")
    assert user.token_hash != token
    assert users.authenticate_token(session, token) == user
    assert users.authenticate_token(session, "cd_errado") is None


def test_rotate_invalidates_old_token(session: Session, user_token: tuple[User, str]) -> None:
    user, old = user_token
    new = users.issue_token(session, user)
    assert users.authenticate_token(session, old) is None
    assert users.authenticate_token(session, new) is not None


def test_inactive_user_rejected(session: Session, user_token: tuple[User, str]) -> None:
    user, token = user_token
    users.deactivate_user(session, user)
    assert users.authenticate_token(session, token) is None


def test_duplicate_email(session: Session, user_token: tuple[User, str]) -> None:
    with pytest.raises(users.UserError):
        users.create_user(session, "Outro", "teste@example.com")
