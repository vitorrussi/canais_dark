from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from sqlmodel import Session

from canaisdark.db import get_engine
from canaisdark.services import users

app = typer.Typer(help="Administração do Estúdio de Canais Dark")


@app.command("create-user")
def create_user(
    name: Annotated[str, typer.Option(help="Nome do usuário")],
    email: Annotated[str, typer.Option(help="E-mail (único)")],
) -> None:
    """Cria um usuário e imprime o token de API (exibido uma única vez)."""
    with Session(get_engine()) as session:
        try:
            user = users.create_user(session, name, email)
        except users.UserError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
        typer.echo(users.issue_token(session, user))


@app.command("rotate-token")
def rotate_token(email: Annotated[str, typer.Option(help="E-mail do usuário")]) -> None:
    """Gera um novo token para o usuário (o anterior deixa de valer)."""
    with Session(get_engine()) as session:
        user = users.get_by_email(session, email)
        if user is None:
            typer.echo(f"Usuário {email} não encontrado", err=True)
            raise typer.Exit(1)
        typer.echo(users.issue_token(session, user))


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Aplica as migrações Alembic (equivale a `alembic upgrade head`)."""
    command.upgrade(Config("alembic.ini"), "head")
