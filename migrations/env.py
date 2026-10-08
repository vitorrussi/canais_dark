from alembic import context
from sqlmodel import SQLModel

import canaisdark.models  # noqa: F401  (registra tabelas no metadata)
from canaisdark.db import get_engine

target_metadata = SQLModel.metadata


def run_migrations() -> None:
    connectable = context.config.attributes.get("connection") or get_engine().connect()
    with connectable as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
