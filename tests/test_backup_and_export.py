"""Encrypted backups, backup checks, audit log export and the new commands."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from conftest import approve, make_env, to_awaiting
from opsapp.__main__ import main
from opsapp.persistence.backup import (
    MAGIC,
    BackupError,
    backup,
    decrypt_bytes,
    encrypt_bytes,
    restore,
    verify_backup,
)
from opsapp.verify import verify_audit_export
from opsapp.workflow import audit_export
from test_web import client_as

PASS = "a long backup passphrase"  # noqa: S105 - test only


def _db(tmp_path: Path):  # type: ignore[no-untyped-def]
    env = make_env(tmp_path)
    approve(env, to_awaiting(env))
    return env


def test_encrypted_backup_round_trip(tmp_path: Path) -> None:
    env = _db(tmp_path)
    db = env.c.settings.database_path
    out = backup(db, tmp_path / "b.opsbak", PASS)
    blob = out.read_bytes()
    assert blob.startswith(MAGIC) and b"SQLite format" not in blob
    assert b"brightline" not in blob.lower()  # nothing readable inside
    ok, lines = verify_backup(out, PASS)
    assert ok, lines
    assert any("Brightline IT Services" in ln and "chain intact" in ln for ln in lines)
    target = tmp_path / "restored.sqlite"
    restore(out, target, PASS)
    assert verify_backup(target)[0]  # a plain database file checks out too
    env.c.engine.dispose()


def test_wrong_passphrase_and_damage_are_caught(tmp_path: Path) -> None:
    env = _db(tmp_path)
    out = backup(env.c.settings.database_path, tmp_path / "b.opsbak", PASS)
    ok, lines = verify_backup(out, "not the passphrase")
    assert not ok and "Wrong passphrase" in lines[0]
    with pytest.raises(BackupError, match="passphrase is needed"):
        restore(out, tmp_path / "x.sqlite")
    damaged = bytearray(out.read_bytes())
    damaged[len(damaged) // 2] ^= 0xFF
    bad = tmp_path / "damaged.opsbak"
    bad.write_bytes(bytes(damaged))
    ok, lines = verify_backup(bad, PASS)
    assert not ok and "damaged" in lines[0]
    with pytest.raises(BackupError, match="at least 12"):
        backup(env.c.settings.database_path, tmp_path / "c.opsbak", "short")
    with pytest.raises(BackupError):
        decrypt_bytes(b"not a backup", PASS)
    assert decrypt_bytes(encrypt_bytes(b"data", PASS), PASS) == b"data"
    env.c.engine.dispose()


def test_verify_reports_a_broken_audit_chain(tmp_path: Path) -> None:
    import sqlite3

    env = _db(tmp_path)
    env.c.engine.dispose()
    db = env.c.settings.database_path
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE audit_events SET message = 'edited' WHERE seq = 3")
    out = backup(db, tmp_path / "plain.sqlite")
    ok, lines = verify_backup(out)
    assert not ok and any("BROKEN at #3" in ln for ln in lines)


def test_cli_backup_encrypt_verify_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _db(tmp_path)
    env.c.engine.dispose()
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(env.c.settings.database_path))
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": PASS)
    out = tmp_path / "cli.opsbak"
    assert main(["backup", "--out", str(out), "--encrypt"]) == 0
    assert "Encrypted backup written" in capsys.readouterr().out
    assert main(["backup", "--verify", str(out)]) == 0
    assert "backup is good" in capsys.readouterr().out
    assert main(["restore", "--from", str(out), "--yes"]) == 0
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "wrong passphrase here")
    assert main(["backup", "--verify", str(out)]) == 1
    assert main(["restore", "--from", str(out), "--yes"]) == 1


def test_cli_user_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(tmp_path)
    env.c.engine.dispose()
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(env.c.settings.database_path))
    answers = iter(["birch harbor violet sky", "birch harbor violet sky"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(answers))
    args = ["user", "create", "--company", "brightline it services", "--name", "Henry"]
    assert main([*args, "--email", "Henry@Example.com"]) == 0
    assert "created in" in capsys.readouterr().out
    answers = iter(["short", "short"])
    assert main([*args, "--email", "other@example.com"]) == 1
    answers = iter(["birch harbor violet sky", "different entry here"])
    with pytest.raises(SystemExit):
        main([*args, "--email", "third@example.com"])


def test_audit_export_checks_out_independently(tmp_path: Path) -> None:
    env = _db(tmp_path)
    tenant = env.ids["tenant_brightline"]
    with env.c.read_sf() as s:
        text = audit_export.to_json(s, tenant, env.clock.now())
        table = audit_export.to_csv(s, tenant)
    doc = json.loads(text)
    assert doc["chain_intact_at_export"] and doc["event_count"] == len(doc["events"]) > 5
    ok, message = verify_audit_export(text)
    assert ok, message
    doc["events"][2]["message"] = "quietly edited"
    assert verify_audit_export(json.dumps(doc)) == (
        False,
        "Event 3: contents do not match its hash (edited?).",
    )
    doc = json.loads(text)
    del doc["events"][1]
    assert not verify_audit_export(json.dumps(doc))[0]
    rows = list(csv.reader(io.StringIO(table)))
    assert rows[0][0] == "seq" and len(rows) == len(json.loads(text)["events"]) + 1
    assert audit_export._safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    env.c.engine.dispose()


def test_audit_export_pages_are_owner_only_and_company_only(tmp_path: Path) -> None:
    from opsapp.web.app import create_app

    env = _db(tmp_path)
    app = create_app(env.c.settings, container=env.c)
    try:
        assert client_as(app, env, "marcus").get("/audit/export.json").status_code == 403
        owner = client_as(app, env, "dana")
        r = owner.get("/audit/export.json")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        doc = r.json()
        assert doc["company"] == "Brightline IT Services"
        assert verify_audit_export(r.text)[0]
        assert "audit/export.csv" in owner.get("/audit").text
        north = client_as(app, env, "grace").get("/audit/export.json").json()
        assert (
            north["tenant_id"] == env.ids["tenant_northgate"] and north["events"] != doc["events"]
        )
        path = tmp_path / "audit-log.json"
        path.write_text(r.text, encoding="utf-8")
        assert main(["audit", "verify-export", str(path)]) == 0
    finally:
        env.c.engine.dispose()
