"""Backup and restore of the SQLite database using SQLite's online backup API.

The backup is a consistent snapshot even while the server or dispatcher is running.
Restore overwrites the target database: stop the server and dispatcher first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def backup(database_path: Path, out_path: Path) -> Path:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database not found: {database_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        raise FileExistsError(f"Backup file already exists: {out_path}")
    src = sqlite3.connect(database_path)
    dst = sqlite3.connect(out_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(out_path)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"Backup failed integrity check: {result}")
    return out_path


def restore(backup_path: Path, database_path: Path) -> None:
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    check = sqlite3.connect(backup_path)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
        has_version = check.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='alembic_version'"
        ).fetchone()[0]
    finally:
        check.close()
    if result != "ok" or not has_version:
        raise RuntimeError("Backup file is not a valid opsapp database.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(backup_path)
    dst = sqlite3.connect(database_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
