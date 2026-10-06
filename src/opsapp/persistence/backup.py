"""Backup and restore of the SQLite database using SQLite's online backup API.

The backup is a consistent snapshot even while the server or dispatcher is running.
Restore overwrites the target database: stop the server and dispatcher first.

Encrypted backups (0.4.0) use AES-256-GCM with a key derived from a passphrase by scrypt.
The passphrase is typed in each time and never stored. Without it the backup cannot be
read, and any change to the file makes decryption fail instead of restoring bad data.

Scheduled backups (0.6.0) can't ask for a passphrase, so they use a random 256-bit key kept
in a key file outside the backup folder. ``backup_to_folder`` writes a dated, encrypted,
checked backup and keeps the newest N; ``drill`` restores the newest one into a temporary
copy and compares it with the live database. Both record their result in a small status
file next to the database, which the dashboard reads.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

MAGIC = b"OPSAPP-ENCRYPTED-BACKUP-1\n"
KEY_MAGIC = b"OPSAPP-ENCRYPTED-BACKUP-2\n"  # encrypted with a key file, not a passphrase
KEY_FILE_HEADER = "OPSAPP-BACKUP-KEY-1"
_KEY_ID = 8
BACKUP_PATTERN = "opsapp-*.opsbak"
MAX_KEEP = 365
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
    """Encrypted with a passphrase (typed in) or with a key file."""
    with path.open("rb") as fh:
        start = fh.read(len(MAGIC))
    return start in (MAGIC, KEY_MAGIC)


def uses_key_file(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(len(KEY_MAGIC)) == KEY_MAGIC


# ------------------------------------------------------------------ key files


def make_key_file(path: Path) -> Path:
    """A new random backup key. Refuses to overwrite: an old key may still open backups."""
    if path.exists():
        raise BackupError(f"Key file already exists, not replaced: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    text = f"{KEY_FILE_HEADER}\n{base64.b64encode(key).decode('ascii')}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(text)
    return path


def read_key_file(path: Path) -> bytes:
    if not path.is_file():
        raise BackupError(f"Key file not found: {path}")
    lines = path.read_text(encoding="ascii", errors="replace").split()
    try:
        if lines[0] != KEY_FILE_HEADER:
            raise ValueError
        key = base64.b64decode(lines[1], validate=True)
    except IndexError, ValueError:
        raise BackupError(f"Not an opsapp backup key file: {path}") from None
    if len(key) != 32:
        raise BackupError(f"Not an opsapp backup key file: {path}")
    return key


def _key_id(key: bytes) -> bytes:
    return hashlib.sha256(b"opsapp-backup-key-id" + key).digest()[:_KEY_ID]


def encrypt_with_key(data: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_NONCE)
    header = KEY_MAGIC + _key_id(key) + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def decrypt_with_key(blob: bytes, key: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    size = len(KEY_MAGIC) + _KEY_ID + _NONCE
    if not blob.startswith(KEY_MAGIC) or len(blob) < size + 16:
        raise BackupError("This is not a backup made with a key file.")
    if blob[len(KEY_MAGIC) : len(KEY_MAGIC) + _KEY_ID] != _key_id(key):
        raise BackupError("This backup was made with a different key file.")
    header, nonce = blob[:size], blob[size - _NONCE : size]
    try:
        return AESGCM(key).decrypt(nonce, blob[size:], header)
    except InvalidTag:
        raise BackupError("The backup file was changed or damaged.") from None


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


def backup(
    database_path: Path, out_path: Path, passphrase: str | None = None, key: bytes | None = None
) -> Path:
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
    if key is not None:
        out_path.write_bytes(encrypt_with_key(data, key))
    elif passphrase is None:
        out_path.write_bytes(data)
    else:
        out_path.write_bytes(encrypt_bytes(data, passphrase))
    return out_path


@contextmanager
def opened_backup(
    backup_path: Path, passphrase: str | None, key: bytes | None = None
) -> Iterator[Path]:
    """A plain, checked copy of a backup in a temporary folder, deleted afterwards."""
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    with tempfile.TemporaryDirectory(prefix="opsapp-restore-") as tmp:
        plain = Path(tmp) / "backup.sqlite"
        if uses_key_file(backup_path):
            if key is None:
                raise BackupError("This backup was made with a key file. Give it with --key.")
            plain.write_bytes(decrypt_with_key(backup_path.read_bytes(), key))
        elif is_encrypted(backup_path):
            if passphrase is None:
                raise BackupError("This backup is encrypted. Its passphrase is needed.")
            plain.write_bytes(decrypt_bytes(backup_path.read_bytes(), passphrase))
        else:
            plain.write_bytes(backup_path.read_bytes())
        result, has_version = _integrity(plain)
        if result != "ok" or not has_version:
            raise BackupError("Backup file is not a valid opsapp database.")
        yield plain


def restore(
    backup_path: Path,
    database_path: Path,
    passphrase: str | None = None,
    key: bytes | None = None,
) -> None:
    with opened_backup(backup_path, passphrase, key) as plain:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        _copy(plain, database_path)


def verify_backup(
    backup_path: Path, passphrase: str | None = None, key: bytes | None = None
) -> tuple[bool, list[str]]:
    """Opens a backup in a temporary copy and checks it, without touching the live database.

    Checks: it decrypts (if encrypted), SQLite integrity, the schema version, every company's
    audit hash chain, and row counts. Returns (ok, report lines).
    """
    try:
        with opened_backup(backup_path, passphrase, key) as plain:
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


# ------------------------------------------------------------------ scheduled backups


def _inside(child: Path, parent: Path) -> bool:
    return child.resolve().is_relative_to(parent.resolve())


def status_path(database_path: Path) -> Path:
    return database_path.parent / "backup-status.json"


def read_status(database_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(status_path(database_path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _record(database_path: Path, kind: str, ok: bool, at: datetime, detail: str) -> None:
    """Remember the latest result, and append a line to backup.log, next to the database."""
    status = read_status(database_path)
    entry = {"ok": ok, "at": at.isoformat(), "detail": detail}
    status[f"last_{kind}"] = entry
    if ok:
        status[f"last_good_{kind}"] = entry
    database_path.parent.mkdir(parents=True, exist_ok=True)
    status_path(database_path).write_text(json.dumps(status, indent=2), encoding="utf-8")
    with (database_path.parent / "backup.log").open("a", encoding="utf-8") as log:
        log.write(f"{at.isoformat()} {kind} {'ok' if ok else 'FAILED'}: {detail}\n")


def backups_in(folder: Path) -> list[Path]:
    """Scheduled backups in a folder, oldest first (their names sort by date)."""
    return sorted(p for p in folder.glob(BACKUP_PATTERN) if p.is_file())


def backup_to_folder(
    database_path: Path, folder: Path, key_path: Path, keep: int, now: datetime
) -> tuple[Path, list[Path]]:
    """Write a dated, key-encrypted, checked backup; keep the newest ``keep``.

    Returns the new file and the old files removed. The result is recorded either way.
    """
    try:
        if not 1 <= keep <= MAX_KEEP:
            raise BackupError(f"--keep must be between 1 and {MAX_KEEP}.")
        if _inside(key_path, folder):
            raise BackupError(
                "The key file must not be inside the backup folder: anyone who gets the "
                "backups would get the key too. Keep it somewhere else, such as Documents."
            )
        key = read_key_file(key_path)
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"opsapp-{now.strftime('%Y%m%d-%H%M%S')}.opsbak"
        backup(database_path, out, key=key)
        ok, lines = verify_backup(out, key=key)
        if not ok:
            raise BackupError("The new backup did not check out: " + " ".join(lines))
        removed = backups_in(folder)[:-keep]
        for old in removed:
            old.unlink()
    except (BackupError, OSError, RuntimeError) as exc:
        _record(database_path, "backup", False, now, str(exc))
        raise BackupError(str(exc)) from exc
    _record(
        database_path,
        "backup",
        True,
        now,
        f"{out.name} written and checked; {len(removed)} older backup(s) removed.",
    )
    return out, removed


_COUNTED = ("users", "customers", "workflows", "audit_events")


def _counts(path: Path) -> dict[str, tuple[str, dict[str, int]]]:
    """Record counts per company, keyed by company id: {id: (name, {table: count})}."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tenants = conn.execute("SELECT id, name FROM tenants ORDER BY name").fetchall()
        out: dict[str, tuple[str, dict[str, int]]] = {}
        for tid, name in tenants:
            counts = {
                table: int(
                    conn.execute(
                        f"SELECT count(*) FROM {table} WHERE tenant_id = ?",  # noqa: S608 # nosec B608
                        (tid,),
                    ).fetchone()[0]
                )
                for table in _COUNTED
            }
            out[tid] = (name, counts)
        return out
    finally:
        conn.close()


def drill(
    database_path: Path, folder: Path, key_path: Path, now: datetime
) -> tuple[bool, list[str], Path]:
    """Restore the newest backup into a temporary copy and check it against the live data.

    The live database is only read. Returns (ok, report lines, report file).
    """
    lines = [f"Restore drill at {now.isoformat()}"]
    ok = False
    try:
        key = read_key_file(key_path)
        found = backups_in(folder) if folder.is_dir() else []
        if not found:
            raise BackupError(f"No backups found in {folder}.")
        newest = found[-1]
        lines.append(f"Newest backup: {newest.name} ({newest.stat().st_size:,} bytes).")
        with opened_backup(newest, None, key) as plain:
            ok, check_lines = _check(plain, encrypted=True)
            lines += check_lines
            restored = _counts(plain)
        live = _counts(database_path)
        lines.append("Compared with the live database (backup / live):")
        names = {tid: name for tid, (name, _) in (restored | live).items()}
        for tid in sorted(names, key=lambda i: names[i].lower()):
            name = names[tid]
            if tid not in restored:
                lines.append(f"  {name}: only in the live database (added since the backup).")
                continue
            b = restored[tid][1]
            if tid not in live:
                ok = False
                lines.append(
                    f"  {name}: only in the backup. PROBLEM: this company has more records in "
                    "the backup than in the live database. If it was deleted on purpose with "
                    "'company delete', make a new backup and run the drill again."
                )
                continue
            cur = live[tid][1]
            parts = ", ".join(f"{t} {b[t]}/{cur[t]}" for t in b)
            lines.append(f"  {name}: {parts}.")
            if any(b[t] > cur[t] for t in b):
                ok = False
                lines.append("  PROBLEM: the backup has more records than the live database.")
        lines.append(
            "The live database was not changed. Records added since the backup was made "
            "show as a lower backup count."
        )
    except BackupError as exc:
        lines.append(str(exc))
        ok = False
    lines.append("Result: " + ("restore drill passed." if ok else "restore drill FAILED."))
    reports = database_path.parent / "drills"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"drill-{now.strftime('%Y%m%d-%H%M%S')}.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _record(database_path, "drill", ok, now, f"{report.name}: " + lines[-1])
    return ok, lines, report
