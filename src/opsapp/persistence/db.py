"""Engine and session setup for SQLite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(database_path: str | Path, *, memory: bool = False) -> Engine:
    if memory:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite+pysqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        if not memory:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
        if not memory:
            # Let SQLAlchemy's "begin" event below choose the BEGIN statement.
            dbapi_conn.isolation_level = None

    if not memory:

        @event.listens_for(engine, "begin")
        def _begin(conn: Any) -> None:
            # Writers take the lock at BEGIN so the web server and the dispatcher queue up
            # instead of failing mid-transaction. Readers use a plain (deferred) BEGIN.
            if conn.get_execution_options().get("sqlite_immediate"):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.exec_driver_sql("BEGIN")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions for use cases that write. Each transaction takes the write lock up front."""
    return sessionmaker(
        bind=engine.execution_options(sqlite_immediate=True), expire_on_commit=False
    )


def make_read_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions for pages and reports. Never hold the write lock."""
    return sessionmaker(bind=engine, expire_on_commit=False)
