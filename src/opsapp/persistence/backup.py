"""Backup and restore of the SQLite database using SQLite's online backup API.

The backup is a consistent snapshot even while the server or dispatcher is running.
Restore overwrites the target database: stop the server and dispatcher first.

Encrypted backups (0.4.0) use AES-256-GCM with a key derived from a passphrase by scrypt.
The passphrase is typed in each time and never stored. Without it the backup cannot be
read, and any change to the file makes decryption fail instead of restoring bad data.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MAGIC = b"OPSAPP-ENCRYPTED-BACKUP-1\n"
_SALT, _NONCE = 16, 12
_N, _R, _P = 2**16, 8, 1
MIN_PASSPHRASE = 12


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * 1024 * 1024, dklen=32
    )


def is_encrypted(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(len(MAGIC)) == MAGIC


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    salt, nonce = os.urandom(_SALT), os.urandom(_NONCE)
    header = MAGIC + salt + nonce
    return header + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, header)


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + _SALT + _NONCE + 16:
        raise BackupError("This is not an encrypted backup file.")
    salt = blob[len(MAGIC) : len(MAGIC) + _SALT]
    nonce = blob[len(MAGIC) + _SALT : len(MAGIC) + _SALT + _NONCE]
    header = blob[: len(MAGIC) + _SALT + _NONCE]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, blob[len(header) :], header)
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the backup file was changed or damaged.") from None


def _copy(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _integrity(path: Path) -> tuple[str, bool]:
    check = sqlite3.connect(path)
    try:
        result = str(check.execute("PRAGMA integrity_check").fetchone()[0])
        has_version = bool(
            check.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='alembic_version'"
            ).fetchone()[0]
        )
    except sqlite3.DatabaseError as exc:
        return str(exc), False
    finally:
        check.close()
    return result, has_version


def backup(database_path: Path, out_path: Path, passphrase: str | None = None) -> Path:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database not found: {database_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        raise FileExistsError(f"Backup file already exists: {out_path}")
    if passphrase is not None and len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    with tempfile.TemporaryDirectory(prefix="opsapp-backup-") as tmp:
        plain = Path(tmp) / "copy.sqlite"
        _copy(database_path, plain)
        result, _ = _integrity(plain)
        if result != "ok":
            raise RuntimeError(f"Backup failed integrity check: {result}")
        data = plain.read_bytes()
    if passphrase is None:
        out_path.write_bytes(data)
    else:
        out_path.write_bytes(encrypt_bytes(data, passphrase))
    return out_path


@contextmanager
def opened_backup(backup_path: Path, passphrase: str | None) -> Iterator[Path]:
    """A plain, checked copy of a backup in a temporary folder, deleted afterwards."""
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    with tempfile.TemporaryDirectory(prefix="opsapp-restore-") as tmp:
        plain = Path(tmp) / "backup.sqlite"
        if is_encrypted(backup_path):
            if passphrase is None:
                raise BackupError("This backup is encrypted. Its passphrase is needed.")
            plain.write_bytes(decrypt_bytes(backup_path.read_bytes(), passphrase))
        else:
            plain.write_bytes(backup_path.read_bytes())
        result, has_version = _integrity(plain)
        if result != "ok" or not has_version:
            raise BackupError("Backup file is not a valid opsapp database.")
        yield plain


def restore(backup_path: Path, database_path: Path, passphrase: str | None = None) -> None:
    with opened_backup(backup_path, passphrase) as plain:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        _copy(plain, database_path)


def verify_backup(backup_path: Path, passphrase: str | None = None) -> tuple[bool, list[str]]:
    """Opens a backup in a temporary copy and checks it, without touching the live database.

    Checks: it decrypts (if encrypted), SQLite integrity, the schema version, every company's
    audit hash chain, and row counts. Returns (ok, report lines).
    """
    try:
        with opened_backup(backup_path, passphrase) as plain:
            encrypted = is_encrypted(backup_path)
            return _check(plain, encrypted)
    except BackupError as exc:
        return False, [str(exc)]


def _check(plain: Path, encrypted: bool) -> tuple[bool, list[str]]:
    from sqlalchemy import func, select

    from ..workflow.audit import verify_chain
    from .db import make_engine, make_read_session_factory
    from .migrate import head_revision
    from .models import Tenant, User, WorkflowInstance

    lines = [f"Opened {'encrypted ' if encrypted else ''}backup: SQLite integrity ok."]
    ok = True
    conn = sqlite3.connect(plain)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    head = head_revision()
    if version == head:
        lines.append(f"Schema version {version} (current).")
    else:
        lines.append(
            f"Schema version {version}; this app is at {head}. Run db upgrade after restoring."
        )
    engine = make_engine(plain)
    try:
        with make_read_session_factory(engine)() as s:
            for t in s.scalars(select(Tenant).order_by(Tenant.name)):
                chain_ok, bad, n = verify_chain(s, t.id)
                ok &= chain_ok
                users = s.scalar(
                    select(func.count()).select_from(User).where(User.tenant_id == t.id)
                )
                workflows = s.scalar(
                    select(func.count())
                    .select_from(WorkflowInstance)
                    .where(WorkflowInstance.tenant_id == t.id)
                )
                lines.append(
                    f"{t.name}: {users} users, {workflows} workflows, {n} audit events, "
                    f"chain {'intact' if chain_ok else f'BROKEN at #{bad}'}."
                )
    finally:
        engine.dispose()
    lines.append("Result: " + ("backup is good." if ok else "PROBLEM found, see above."))
    return ok, lines
