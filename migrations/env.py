from alembic import context
from sqlmodel import SQLModel

import canaisdark.models  # noqa: F401  (registra tabelas no metadata)
from canaisdark.db import get_engine

target_metadata = SQLModel.metadata


def run_migrations() -> None:
    connectable = context.config.attributes.get("connection") or get_engine().connect()
    with connectable as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        if is_sqlite:
            # O modo batch do SQLite recria tabelas (DROP/RENAME); com FKs ligadas isso falha
            # quando há linhas dependentes. Desliga durante a migração e confere a integridade depois.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()  # a transação foi aberta implicitamente pelo PRAGMA acima
        if is_sqlite:
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"Violação de chave estrangeira após a migração: {broken[:5]}")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


run_migrations()
