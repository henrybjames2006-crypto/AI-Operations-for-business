"""Operations: migrations, backup/restore, audit chain, secret handling."""

import json
import logging
import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import approve, make_env, to_awaiting
from opsapp.persistence import migrate
from opsapp.persistence.backup import backup, restore
from opsapp.persistence.models import AuditEvent, WorkflowInstance
from opsapp.redaction import RedactingFilter, redact
from opsapp.web.views import workflow_export
from opsapp.workflow.audit import verify_chain

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite"
    migrate.upgrade(db)
    tables = {
        r[0]
        for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"workflows", "quote_versions", "audit_events", "outbox"} <= tables
    migrate.downgrade(db, "-1")  # 0002: the catalog_changes column goes, tables stay
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(pricing_versions)")}
    assert "catalog_changes" not in cols and "notes" in cols
    migrate.downgrade(db, "base")
    tables = {
        r[0]
        for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables <= {"alembic_version"}
    migrate.upgrade(db)


def test_migrated_schema_matches_models(tmp_path: Path) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from opsapp.persistence.models import Base

    db = tmp_path / "m.sqlite"
    migrate.upgrade(db)
    with create_engine(f"sqlite:///{db}").connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diffs == []


def _snapshot(env):  # type: ignore[no-untyped-def]
    with env.c.read_sf() as s:
        wfs = sorted((w.id, w.state, w.state_version) for w in s.scalars(select(WorkflowInstance)))
        evs = [
            (e.seq, e.hash)
            for e in s.scalars(select(AuditEvent).order_by(AuditEvent.tenant_id, AuditEvent.seq))
        ]
    return wfs, evs


def test_backup_and_restore_round_trip(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    wf_id = to_awaiting(env)
    approve(env, wf_id)
    env.dispatcher().run_once()
    before = _snapshot(env)
    out = backup(env.c.settings.database_path, tmp_path / "backups" / "b1.sqlite")
    with pytest.raises(FileExistsError):
        backup(env.c.settings.database_path, out)
    # Restore into a fresh database file and compare.
    target = tmp_path / "restored" / "db.sqlite"
    restore(out, target)
    from conftest import START
    from opsapp.clock import FixedClock
    from opsapp.config import Settings
    from opsapp.container import build

    c2 = build(Settings(database_path=target, session_secret="x" * 16), FixedClock(START))
    env2 = type(env)(c2, env.clock, env.ids)
    assert _snapshot(env2) == before
    with c2.read_sf() as s:
        for t in env.ids["tenant_brightline"], env.ids["tenant_northgate"]:
            assert verify_chain(s, t)[0]
    env.c.engine.dispose()
    c2.engine.dispose()


def test_restore_rejects_non_database(tmp_path: Path) -> None:
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    with pytest.raises(Exception):  # noqa: B017
        restore(bad, tmp_path / "x.sqlite")


def test_audit_chain_detects_tampering(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = to_awaiting(env)
    tenant = env.ids["tenant_brightline"]
    with env.c.read_sf() as s:
        assert verify_chain(s, tenant)[0]
    with env.c.sf.begin() as s:
        ev = s.scalars(
            select(AuditEvent).where(AuditEvent.workflow_id == wf_id).order_by(AuditEvent.seq)
        ).first()
        ev.message = "Nothing to see here."
    with env.c.read_sf() as s:
        ok, bad, _ = verify_chain(s, tenant)
    assert not ok and bad is not None


def test_planted_secret_never_reaches_logs_or_exports(env, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    planted = "planted-secret-7Q2x9VbL4mN8"
    monkeypatch.setenv("OPSAPP_SESSION_SECRET", planted)
    monkeypatch.setenv("OPSAPP_AI_API_KEY", "sk-ant-planted-key-0000000000000000")
    handler_filter = RedactingFilter()
    caplog.handler.addFilter(handler_filter)
    caplog.set_level(logging.DEBUG)
    text = (
        f"Please set up 2 printers. My password: {planted} and key "
        f"sk-ant-planted-key-0000000000000000"
    )
    wf_id = env.svc.submit_request(
        env.ids["priya"], text, "it@maplestreetlaw.example", "secret-test"
    ).workflow_id
    logging.getLogger("opsapp.test").warning("config secret is %s", planted)
    env.svc.submit_for_approval(env.ids["priya"], wf_id)
    approve(env, wf_id)
    env.dispatcher().run_once()
    with env.c.read_sf() as s:
        exported = json.dumps(redact(workflow_export(s, env.svc, wf_id)))
    assert planted not in exported and "sk-ant-planted" not in exported
    assert planted not in caplog.text
    assert "[REDACTED]" in exported


def test_no_credentials_in_source_or_fixtures() -> None:
    patterns = [
        re.compile(p)
        for p in (
            r"sk-ant-[A-Za-z0-9]{20,}",
            r"AKIA[0-9A-Z]{16}",
            r"ghp_[A-Za-z0-9]{30,}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY",
        )
    ]
    for path in list((ROOT / "src").rglob("*")) + list((ROOT / "tests").rglob("*.py")):
        if path.is_file() and path.suffix in {".py", ".html", ".json", ".md", ".css"}:
            text = path.read_text(encoding="utf-8")
            for p in patterns:
                assert not p.search(text), f"{path} matches {p.pattern}"
    assert not (ROOT / ".env").exists() or ".env" in (ROOT / ".gitignore").read_text()


def test_server_refuses_non_local_binding() -> None:
    from opsapp.config import Settings

    with pytest.raises(ValueError, match="127.0.0.1"):
        Settings(database_path=Path("x"), session_secret="s", host="0.0.0.0")  # noqa: S104


def test_redaction_covers_common_secret_shapes() -> None:
    from opsapp.redaction import redact_text

    # Built at runtime so the repository credential scan does not flag this test.
    pem = "-" * 5 + "{} RSA PRIVATE KEY" + "-" * 5
    samples = [
        "Authorization: " + "Bearer " + "abcdefghijklmnop1234567890",
        pem.format("BEGIN") + "\nMIIEabc\n" + pem.format("END"),
        "key " + "sk-" + "abcdefghijklmnopqrstuv",
    ]
    for text in samples:
        out = redact_text(text)
        assert "abcdefghijklmnop" not in out and "MIIEabc" not in out, out
