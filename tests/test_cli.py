from sqlmodel import Session
from typer.testing import CliRunner

from canaisdark.cli import app
from canaisdark.services import users

runner = CliRunner()


def test_create_user_and_rotate(session: Session) -> None:
    r = runner.invoke(app, ["create-user", "--name", "Ana", "--email", "ana@example.com"])
    assert r.exit_code == 0
    token = r.stdout.strip()
    assert users.authenticate_token(session, token) is not None

    r2 = runner.invoke(app, ["rotate-token", "--email", "ana@example.com"])
    assert r2.exit_code == 0
    session.expire_all()
    assert users.authenticate_token(session, token) is None
    assert users.authenticate_token(session, r2.stdout.strip()) is not None


def test_create_duplicate_fails(session: Session) -> None:
    args = ["create-user", "--name", "Ana", "--email", "ana@example.com"]
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code == 1
