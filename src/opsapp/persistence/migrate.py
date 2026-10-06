"""Run Alembic migrations without needing an alembic.ini file."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(database_path: Path | str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    path = Path(database_path)
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return cfg


def upgrade(database_path: Path | str, revision: str = "head") -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(database_path), revision)


def downgrade(database_path: Path | str, revision: str) -> None:
    command.downgrade(alembic_config(database_path), revision)


def current(database_path: Path | str) -> None:
    command.current(alembic_config(database_path), verbose=False)


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(alembic_config("unused.sqlite")).get_current_head()
    return str(head)
