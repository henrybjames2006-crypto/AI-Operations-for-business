"""0.6.0: scheduled backups with a key file, restore drill, company export and deletion,
and the customer copy of an approved quote that a person sends by hand."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import START, approve, events, make_env, sim_ops, to_awaiting
from opsapp.__main__ import main
from opsapp.domain.errors import PermissionDenied, ValidationError
from opsapp.persistence.backup import (
    BackupError,
    backup_to_folder,
    backups_in,
    drill,
    make_key_file,
    read_key_file,
    read_status,
    restore,
    verify_backup,
)
from opsapp.persistence.company_data import CompanyDataError, delete_company, write_export
from opsapp.persistence.models import Customer, Tenant, User
from opsapp.workflow.audit import verify_chain
from test_web import client_as, web  # noqa: F401 - pytest fixture

T0 = datetime(2026, 10, 6, 2, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    env = make_env(tmp_path)
    approve(env, to_awaiting(env))
    yield env
    env.c.engine.dispose()


# ---------------------------------------------------------------- key files and folders


def test_key_file_is_new_random_and_never_overwritten(tmp_path: Path) -> None:
    a, b = tmp_path / "a.key", tmp_path / "b.key"
    make_key_file(a)
    make_key_file(b)
    assert len(read_key_file(a)) == 32 and read_key_file(a) != read_key_file(b)
    with pytest.raises(BackupError, match="already exists"):
        make_key_file(a)
    (tmp_path / "junk.key").write_text("hello", encoding="ascii")
    with pytest.raises(BackupError, match="Not an opsapp backup key"):
        read_key_file(tmp_path / "junk.key")
    with pytest.raises(BackupError, match="not found"):
        read_key_file(tmp_path / "missing.key")


def test_backup_to_folder_keeps_the_newest_and_records_status(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    live = db.c.settings.database_path
    folder, key = tmp_path / "OneDrive" / "opsapp", tmp_path / "Documents" / "opsapp.key"
    make_key_file(key)
    made = [backup_to_folder(live, folder, key, 2, T0 + timedelta(days=i))[0] for i in range(3)]
    assert backups_in(folder) == made[1:]
    assert not made[0].exists()
    ok, lines = verify_backup(made[-1], key=read_key_file(key))
    assert ok, lines
    assert b"Brightline" not in made[-1].read_bytes()  # encrypted
    status = read_status(live)
    assert status["last_good_backup"]["at"] == (T0 + timedelta(days=2)).isoformat()
    assert "backup ok" in (live.parent / "backup.log").read_text(encoding="utf-8")

    restored = tmp_path / "restored.sqlite"
    restore(made[-1], restored, key=read_key_file(key))
    assert verify_backup(restored)[0]


def test_backup_to_folder_refuses_unsafe_or_wrong_keys(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    live = db.c.settings.database_path
    folder = tmp_path / "backups"
    inside = folder / "opsapp.key"
    make_key_file(inside)
    with pytest.raises(BackupError, match="must not be inside the backup folder"):
        backup_to_folder(live, folder, inside, 14, T0)
    status = read_status(live)
    assert status["last_backup"]["ok"] is False and "last_good_backup" not in status
    with pytest.raises(BackupError, match="between 1 and"):
        backup_to_folder(live, folder, tmp_path / "x.key", 0, T0)

    key, other = tmp_path / "k1.key", tmp_path / "k2.key"
    make_key_file(key)
    make_key_file(other)
    out, _ = backup_to_folder(live, folder, key, 14, T0)
    ok, lines = verify_backup(out, key=read_key_file(other))
    assert not ok and "different key file" in lines[0]
    ok, lines = verify_backup(out)
    assert not ok and "--key" in lines[0]
    damaged = bytearray(out.read_bytes())
    damaged[-40] ^= 1
    out.write_bytes(bytes(damaged))
    ok, lines = verify_backup(out, key=read_key_file(key))
    assert not ok and "changed or damaged" in lines[0]


def test_restore_drill_passes_without_touching_the_live_database(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    live = db.c.settings.database_path
    folder, key = tmp_path / "backups", tmp_path / "opsapp.key"
    make_key_file(key)
    ok, lines, report = drill(live, folder, key, T0)
    assert not ok and "No backups found" in "\n".join(lines)

    backup_to_folder(live, folder, key, 14, T0)
    db.c.engine.dispose()
    before = live.read_bytes()
    ok, lines, report = drill(live, folder, key, T0 + timedelta(minutes=5))
    assert ok, lines
    assert live.read_bytes() == before
    text = report.read_text(encoding="utf-8")
    assert "Brightline IT Services: users" in text and "restore drill passed" in text
    assert read_status(live)["last_good_drill"]["at"] == (T0 + timedelta(minutes=5)).isoformat()

    newest = backups_in(folder)[-1]
    damaged = bytearray(newest.read_bytes())
    damaged[100] ^= 1
    newest.write_bytes(bytes(damaged))
    ok, lines, _ = drill(live, folder, key, T0 + timedelta(minutes=10))
    assert not ok and "restore drill FAILED" in lines[-1]
    assert read_status(live)["last_drill"]["ok"] is False


def test_drill_fails_when_the_backup_has_more_than_the_live_database(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    live = db.c.settings.database_path
    folder, key = tmp_path / "backups", tmp_path / "opsapp.key"
    make_key_file(key)
    backup_to_folder(live, folder, key, 14, T0)
    # Records vanish from the live database after the backup: the drill must notice.
    delete_company(db.c.sf, "Northgate Tech", tmp_path / "n.json", tmp_path, T0)
    db.c.engine.dispose()
    ok, lines, _ = drill(live, folder, key, T0 + timedelta(minutes=5))
    assert not ok and "restore drill FAILED" in lines[-1]
    assert any("Northgate Tech" in line and "more" in line for line in lines), lines


def test_backup_commands(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(db.c.settings.database_path))
    key, folder = str(tmp_path / "opsapp.key"), str(tmp_path / "backups")
    assert main(["backup", "make-key", "--key", key]) == 0
    assert "Keep it OUT of the backup folder" in capsys.readouterr().out
    assert main(["backup", "make-key", "--key", key]) == 1
    assert main(["backup", "--to-folder", folder, "--key", key, "--keep", "3"]) == 0
    out = backups_in(Path(folder))[0]
    assert main(["backup", "--verify", str(out)]) != 0  # needs --key
    assert main(["backup", "--verify", str(out), "--key", key]) == 0
    assert main(["backup", "drill", "--folder", folder, "--key", key]) == 0
    assert "restore drill passed" in capsys.readouterr().out
    target = tmp_path / "restored.sqlite"
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(target))
    assert main(["restore", "--from", str(out), "--yes", "--key", key]) == 0
    assert verify_backup(target)[0]


# ---------------------------------------------------------------- company export and delete


def test_company_export_leaves_out_secrets_and_other_companies(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from opsapp.auth.passwords import hash_password

    with db.c.sf.begin() as s:
        dana = s.get(User, db.ids["dana"])
        dana.password_hash = hash_password("a long password for dana")
        dana.totp_secret = "JBSWY3DPEHPK3PXP"
    doc = write_export(db.c.sf, "brightline it services", tmp_path / "export.json", T0)
    text = (tmp_path / "export.json").read_text(encoding="utf-8")
    assert "scrypt" not in text and "JBSWY3DPEHPK3PXP" not in text
    assert "auth_sessions" not in doc["tables"] and "password_hash" not in doc["tables"]["users"][0]
    assert all(
        r["tenant_id"] == db.ids["tenant_brightline"] for t in doc["tables"].values() for r in t
    )
    assert "Northgate" not in text
    assert doc["counts"]["workflows"] >= 1 and doc["counts"]["audit_events"] > 5
    with pytest.raises(CompanyDataError, match="already exists"):
        write_export(db.c.sf, "Brightline IT Services", tmp_path / "export.json", T0)
    with pytest.raises(CompanyDataError, match="No company called"):
        write_export(db.c.sf, "Nobody Ltd", tmp_path / "other.json", T0)


def test_company_delete_removes_one_company_only(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    removed = delete_company(
        db.c.sf, "Northgate Tech", tmp_path / "northgate.json", tmp_path / "logs", T0
    )
    assert removed["customers"] >= 1 and removed["users"] >= 1
    with db.c.read_sf() as s:
        assert [t.name for t in s.scalars(select(Tenant))] == ["Brightline IT Services"]
        assert (
            s.scalars(
                select(Customer).where(Customer.tenant_id == db.ids["tenant_northgate"])
            ).all()
            == []
        )
        assert verify_chain(s, db.ids["tenant_brightline"])[0]
    exported = json.loads((tmp_path / "northgate.json").read_text(encoding="utf-8"))
    assert exported["company"]["name"] == "Northgate Tech"
    log = (tmp_path / "logs" / "deletions.log").read_text(encoding="utf-8")
    assert db.ids["tenant_northgate"] in log and "Northgate" not in log


def test_company_delete_command_needs_yes(db, tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(db.c.settings.database_path))
    out = str(tmp_path / "ng.json")
    assert main(["company", "delete", "Northgate Tech", "--out", out]) == 1
    assert "re-run with --yes" in capsys.readouterr().out
    assert not Path(out).exists()
    assert main(["company", "export", "Northgate Tech"]) == 1  # needs --out
    assert main(["company", "export", "Northgate Tech", "--out", out]) == 0
    assert main(["company", "delete", "Northgate Tech", "--out", out, "--yes"]) == 1  # file exists
    out2 = str(tmp_path / "ng2.json")
    assert main(["company", "delete", "Northgate Tech", "--out", out2, "--yes"]) == 0
    assert main(["company", "create", "X Firm"]) == 1  # create still needs its options


# ---------------------------------------------------------------- sending the quote by hand


def test_mark_sent_by_hand_needs_an_approved_quote_and_permission(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    wf = to_awaiting(env)
    with pytest.raises(ValidationError, match="Only an approved quote"):
        env.c.firm.record_sent_by_hand(env.ids["priya"], wf, "email", "")
    approve(env, wf)
    with pytest.raises(PermissionDenied):
        env.c.firm.record_sent_by_hand(env.ids["victor"], wf, "email", "")
    with pytest.raises(ValidationError, match="Choose how"):
        env.c.firm.record_sent_by_hand(env.ids["priya"], wf, "pigeon", "")
    env.c.firm.record_sent_by_hand(env.ids["priya"], wf, "email", "to the office manager")
    sent = [e for e in events(env, wf) if e.event_type == "quote_sent_by_hand"]
    assert len(sent) == 1 and sent[0].data["method"] == "email"
    assert "the app sent nothing" in sent[0].message
    assert sim_ops(env) == []  # recording it sends nothing
    with pytest.raises(Exception):  # another company's workflow  # noqa: B017
        env.c.firm.record_sent_by_hand(env.ids["omar"], wf, "email", "")
    env.c.service.cancel(env.ids["dana"], wf, "Customer withdrew.")
    with pytest.raises(ValidationError, match="Only an approved quote"):
        env.c.firm.record_sent_by_hand(env.ids["priya"], wf, "email", "")
    env.c.engine.dispose()


def test_customer_copy_page_shows_only_the_approved_quote(web) -> None:  # noqa: F811
    env, app = web
    wf = to_awaiting(env)
    priya = client_as(app, env, "priya")
    r = priya.get(f"/workflows/{wf}/customer-copy")
    assert r.status_code == 303  # not approved yet
    assert "Customer copy" not in priya.get(f"/workflows/{wf}").text
    approve(env, wf)
    page = priya.get(f"/workflows/{wf}").text
    assert "Customer copy and email text" in page
    copy = priya.get(f"/workflows/{wf}/customer-copy")
    assert copy.status_code == 200
    assert "Quote 1 from Brightline IT Services" in copy.text
    assert "Harbor Dental Group" in copy.text and "Tax is not included" in copy.text
    assert "The app does not send it" in copy.text and "Subject:" in copy.text
    assert "SIMULATED" not in copy.text.split("quote-copy")[1].split("noprint")[0]
    r = priya.post(
        f"/workflows/{wf}/sent-by-hand",
        data={"method": "print", "note": "handed over", "csrf": priya.csrf},
        follow_redirects=True,
    )
    assert "Recorded in the audit log" in r.text and "printed or saved as PDF" in r.text
    victor = client_as(app, env, "victor")
    assert "Mark as sent by hand" not in victor.get(f"/workflows/{wf}/customer-copy").text
    r = victor.post(f"/workflows/{wf}/sent-by-hand", data={"method": "email", "csrf": victor.csrf})
    assert r.status_code == 403
    nia = client_as(app, env, "nia")
    assert nia.get(f"/workflows/{wf}/customer-copy").status_code == 404


def test_owner_dashboard_shows_backup_status(web, tmp_path: Path) -> None:  # noqa: F811
    env, app = web
    dana = client_as(app, env, "dana")
    assert "Last good backup: none yet" in dana.get("/").text
    key = tmp_path / "k.key"
    make_key_file(key)
    backup_to_folder(env.c.settings.database_path, tmp_path / "b", key, 14, START)
    page = dana.get("/").text
    assert "Last good backup: 2026-10-05 15:00 UTC" in page
    assert "Backups on this computer" not in client_as(app, env, "priya").get("/").text
