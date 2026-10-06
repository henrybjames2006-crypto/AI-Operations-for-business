"""Command line: ``python -m opsapp <command>``.

Commands: db upgrade | db downgrade | db current | seed | company create | user create | serve |
dispatch |
eval run | redact | backup | restore | audit verify | audit verify-export | export |
recompute-quote
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import load_settings
from .redaction import configure_logging, redact

if TYPE_CHECKING:
    from .container import Container


def _container() -> Container:
    from .container import build

    return build(load_settings())


def cmd_db(args: argparse.Namespace) -> int:
    from .persistence import migrate

    settings = load_settings()
    if args.action == "upgrade":
        migrate.upgrade(settings.database_path)
        print(f"Database at {settings.database_path} is at the latest schema.")
    elif args.action == "downgrade":
        migrate.downgrade(settings.database_path, args.revision)
        print(f"Database downgraded to {args.revision}.")
    else:
        migrate.current(settings.database_path)
    return 0


def cmd_seed(_args: argparse.Namespace) -> int:
    from .seed import seed

    c = _container()
    try:
        with c.sf.begin() as s:
            ids = seed(s)
    except RuntimeError as exc:
        print(exc)
        return 1
    print("Seeded fictional tenants: Brightline IT Services and Northgate Tech.")
    print(
        f"{len([k for k in ids if not k.startswith(('customer_', 'site_', 'tenant_'))])} demo "
        "users created, without passwords. Either set OPSAPP_DEMO_MODE=true to pick them on "
        "the sign-in page, or create your own owner with: python -m opsapp user create "
        '--company "Brightline IT Services" --name "Your Name" --email you@example.com'
    )
    return 0


def cmd_serve(_args: argparse.Namespace) -> int:
    import uvicorn

    from .web.app import create_app

    settings = load_settings()
    if not settings.database_path.exists():
        print("No database yet. Run: python -m opsapp db upgrade; python -m opsapp seed")
        return 1
    try:
        app = create_app(settings)
    except RuntimeError as exc:
        print(exc)
        return 1
    mode = "DEMO MODE, no passwords" if settings.demo_mode else "sign-in with password and code"
    print(
        f"Serving on http://{settings.host}:{settings.port} (local only, {mode}). Ctrl+C to stop."
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    c = _container()
    d = c.dispatcher()
    if args.once:
        print(f"Processed {d.run_once()} step(s).")
        return 0
    print("Dispatcher running (simulated adapters only). Ctrl+C to stop.")
    try:
        d.run_forever(c.settings.dispatch_poll_seconds)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


def _load_samples(folder: str) -> list[Any] | None:
    from .evals.samples import load_samples

    cases, problems = load_samples(Path(folder))
    if problems:
        print("The samples could not be used:")
        for p in problems:
            print(f"  - {p}")
        return None
    return cases


def cmd_eval(args: argparse.Namespace) -> int:
    if args.action == "compare":
        return _eval_compare(args)
    if args.samples:
        from .evals.runner import run_samples, write_samples_report

        cases = _load_samples(args.samples)
        if cases is None:
            return 1
        md, js = write_samples_report(run_samples(cases), Path(args.out))
        print(md.read_text(encoding="utf-8").split("\n## Samples")[0])
        print(f"Report: {md}\nData:   {js}")
        return 0
    from .evals.runner import SCENARIO_CATEGORIES, run_all, write_report

    results = run_all()
    md, js = write_report(results, Path(args.out))
    print(md.read_text(encoding="utf-8").split("\n## Cases")[0])
    print(f"Report: {md}\nData:   {js}")
    # Reading mistakes are reported, not fatal. Safety scenarios must all pass.
    unsafe = [
        r for r in results["results"] if r["category"] in SCENARIO_CATEGORIES and not r["passed"]
    ]
    return 1 if unsafe else 0


def _eval_compare(args: argparse.Namespace) -> int:
    from decimal import Decimal

    from .ai.claude import ClaudeExtractor, make_client
    from .ai.fallback import FallbackExtractor
    from .ai.mock import MockExtractor
    from .evals.runner import run_compare, write_compare_report

    settings = load_settings()
    if settings.ai_provider != "anthropic":
        env_file = Path(".env").resolve()
        found = "found" if env_file.is_file() else "NOT found"
        print(
            f"OPSAPP_AI_PROVIDER is {settings.ai_provider!r}; it must be 'anthropic'.\n"
            f"Settings file {env_file} was {found}. Set OPSAPP_AI_PROVIDER=anthropic and "
            "OPSAPP_AI_API_KEY there (one line each), save, and run this again."
        )
        return 1
    cases = None
    if args.samples:
        cases = _load_samples(args.samples)
        if cases is None:
            return 1
        if not args.yes:
            print(
                f"This sends {len(cases)} redacted real requests to {settings.ai_model} (a "
                f"paid API run by Anthropic). Only do this if the firm agreed to it, and read "
                f"every redacted file first. Spending stops at ${settings.ai_budget_usd}. "
                "Re-run with --yes to go ahead."
            )
            return 1
    if not args.yes:
        print(
            f"This sends about 70 fictional requests to {settings.ai_model} (a paid API). "
            f"Spending stops at the budget of ${settings.ai_budget_usd}. "
            "Re-run with --yes to go ahead."
        )
        return 1
    client = make_client(settings.ai_api_key, settings.ai_timeout_seconds)
    spent = {"usd": Decimal("0")}

    def reader() -> FallbackExtractor:
        return FallbackExtractor(
            ClaudeExtractor(client, settings.ai_model, settings.ai_timeout_seconds),
            MockExtractor(),
            lambda: spent["usd"],
            settings.ai_budget_usd,
        )

    def track(res: dict[str, object]) -> None:
        spent["usd"] += Decimal(str(res.get("ai_cost_usd", "0")))

    label = f"anthropic/{settings.ai_model}"
    data = run_compare(reader, label, after_case=track, cases=cases)
    md, js = write_compare_report(data, Path(args.out))
    print(md.read_text(encoding="utf-8").split("\n## Cases where")[0])
    print(f"Report: {md}\nData:   {js}")
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    from collections import Counter

    from .discovery.redact import read_names, redact_folder

    names = read_names(Path(args.names)) if args.names else []
    try:
        count, report = redact_folder(Path(args.source), Path(args.out), names)
    except ValueError as exc:
        print(exc)
        return 1
    kinds = Counter(r.kind for r in report)
    print(f"Redacted {count} file(s) into {Path(args.out).resolve()}.")
    print(
        "Replaced: "
        + (", ".join(f"{n} {k.lower()}" for k, n in sorted(kinds.items())) or "nothing")
        + "."
    )
    if not names:
        print("No names list was given, so people's and companies' names were NOT removed.")
    print(
        "Files are renamed sample-001.txt and so on, in alphabetical order of the originals.\n"
        "Read every redacted file before sharing it: patterns miss things. Then fill in "
        "labels.csv (see docs/discovery/labelling.md)."
    )
    return 0


def _ask_secret(prompt: str, *, twice: bool = False) -> str:
    import getpass

    value = getpass.getpass(prompt)
    if twice and getpass.getpass("Type it again: ") != value:
        raise SystemExit("The two entries were not the same. Nothing was done.")
    return value


def _key(args: argparse.Namespace, path: Path) -> bytes | None:
    """The key file's key for backups made with one, else None."""
    from .persistence.backup import BackupError, read_key_file, uses_key_file

    if not uses_key_file(path):
        return None
    if not args.key:
        raise BackupError("This backup was made with a key file. Give it with --key FILE.")
    return read_key_file(Path(args.key))


def cmd_backup(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from .persistence.backup import (
        BackupError,
        backup,
        backup_to_folder,
        drill,
        is_encrypted,
        make_key_file,
        uses_key_file,
        verify_backup,
    )

    settings = load_settings()
    try:
        if args.action == "make-key":
            if not args.key:
                print(
                    "Give the new key file: backup make-key --key "
                    '"$HOME\\Documents\\opsapp-backup.key"'
                )
                return 1
            path = make_key_file(Path(args.key))
            print(f"Backup key written to {path}.")
            print(
                "Keep it OUT of the backup folder, and keep a second copy somewhere safe (a USB "
                "stick or a password manager). Without it, backups made with it can't be opened."
            )
            return 0
        if args.action == "drill":
            if not (args.folder and args.key):
                print("Give the backup folder and key: backup drill --folder DIR --key FILE")
                return 1
            ok, lines, report = drill(
                settings.database_path, Path(args.folder), Path(args.key), datetime.now(UTC)
            )
            print("\n".join(lines))
            print(f"Report saved to {report}")
            return 0 if ok else 1
        if args.verify:
            path = Path(args.verify)
            if not path.is_file():
                print(f"Backup not found: {path}")
                return 1
            key = _key(args, path)
            needs_pass = is_encrypted(path) and not uses_key_file(path)
            passphrase = _ask_secret("Backup passphrase: ") if needs_pass else None
            ok, lines = verify_backup(path, passphrase, key)
            print("\n".join(lines))
            return 0 if ok else 1
        if args.folder:
            if not args.key:
                print("Give the key file too: backup --to-folder DIR --key FILE")
                return 1
            out, removed = backup_to_folder(
                settings.database_path,
                Path(args.folder),
                Path(args.key),
                args.keep,
                datetime.now(UTC),
            )
            print(f"Encrypted backup written and checked: {out}")
            if removed:
                print(f"Removed {len(removed)} older backup(s); keeping the newest {args.keep}.")
            return 0
        if not args.out:
            print(
                "Give --out FILE to write a backup, --to-folder DIR --key FILE for a scheduled "
                "one, or --verify FILE to check one."
            )
            return 1
        passphrase = None
        if args.encrypt:
            print(
                "Choose a passphrase of at least 12 characters. It is not stored anywhere: "
                "without it this backup can't be restored."
            )
            passphrase = _ask_secret("Backup passphrase: ", twice=True)
        out = backup(settings.database_path, Path(args.out), passphrase)
    except (BackupError, FileExistsError, FileNotFoundError) as exc:
        print(exc)
        return 1
    kind = "Encrypted backup" if passphrase else "Backup (NOT encrypted)"
    print(f"{kind} written and integrity-checked: {out}")
    print(f"Check it can be restored: python -m opsapp backup --verify {out}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from .persistence.backup import BackupError, is_encrypted, restore, uses_key_file

    settings = load_settings()
    if not args.yes:
        print(
            "Restore overwrites the current database. Stop the server and dispatcher, then "
            "re-run with --yes."
        )
        return 1
    source = Path(args.source)
    if not source.is_file():
        print(f"Backup not found: {source}")
        return 1
    try:
        key = _key(args, source)
        needs_pass = is_encrypted(source) and not uses_key_file(source)
        passphrase = _ask_secret("Backup passphrase: ") if needs_pass else None
        restore(source, settings.database_path, passphrase, key)
    except BackupError as exc:
        print(exc)
        return 1
    print(f"Restored {args.source} into {settings.database_path}.")
    return 0


def cmd_user(args: argparse.Namespace) -> int:
    from .domain.errors import DomainError

    c = _container()
    print(
        "Choose a password of at least 12 characters. You will set up an authenticator app "
        "the first time you sign in."
    )
    password = _ask_secret("Password: ", twice=True)
    try:
        c.auth.create_owner(args.company, args.name, args.email, password)
    except DomainError as exc:
        print(exc)
        return 1
    print(f"Owner {args.name} ({args.email.strip().lower()}) created in {args.company}.")
    if c.settings.demo_mode:
        print("Turn OPSAPP_DEMO_MODE off in .env to sign in with it.")
    return 0


def cmd_company(args: argparse.Namespace) -> int:
    if args.action in ("export", "delete"):
        return _company_data(args)
    from .domain.errors import DomainError

    if not (args.timezone and args.owner_name and args.owner_email):
        print("company create needs --timezone, --owner-name and --owner-email.")
        return 1
    settings = load_settings()
    if not settings.database_path.exists():
        print("No database yet. Run first: python -m opsapp db upgrade")
        return 1
    c = _container()
    print(
        f"Creating {args.name!r} with owner {args.owner_name} ({args.owner_email.strip().lower()})."
        " Choose the owner's password: at least 12 characters. They set up an authenticator "
        "app the first time they sign in."
    )
    password = _ask_secret("Password: ", twice=True)
    try:
        c.auth.create_company(args.name, args.timezone, args.owner_name, args.owner_email, password)
    except DomainError as exc:
        print(exc)
        return 1
    print(f"Company {' '.join(args.name.split())} created, with no services or customers yet.")
    print("Start the app (python -m opsapp serve), sign in, and follow the setup checklist.")
    if c.settings.demo_mode:
        print("Turn OPSAPP_DEMO_MODE off in .env to sign in with it.")
    return 0


def _company_data(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from .persistence.company_data import CompanyDataError, delete_company, write_export

    settings = load_settings()
    if not settings.database_path.exists():
        print("No database found.")
        return 1
    if not args.out:
        print(f'Give the export file: company {args.action} "{args.name}" --out FILE.json')
        return 1
    now = datetime.now(UTC)
    c = _container()
    try:
        if args.action == "export":
            doc = write_export(c.sf, args.name, Path(args.out), now)
            rows = sum(doc["counts"].values())
            print(f"Exported {doc['company']['name']} ({rows} records) to {args.out}.")
            print("Sign-in secrets are left out. The file holds the company's data: keep it safe.")
            return 0
        if not args.yes:
            print(
                f"This permanently removes {args.name!r} and all its records from "
                f"{settings.database_path}, after exporting them to {args.out}. Make a backup "
                "first, stop the server, then re-run with --yes."
            )
            return 1
        removed = delete_company(
            c.sf, args.name, Path(args.out), settings.database_path.parent, now
        )
    except CompanyDataError as exc:
        print(exc)
        return 1
    finally:
        c.engine.dispose()
    print(f"Deleted {args.name}: {sum(removed.values())} records. Export kept at {args.out}.")
    print("Older backups still contain it: delete or keep them as your agreement says.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    if args.action == "verify-export":
        from .verify import verify_audit_export

        if not args.file:
            print("Give the exported file: python -m opsapp audit verify-export audit-log.json")
            return 1
        ok, message = verify_audit_export(Path(args.file).read_text(encoding="utf-8"))
        print(message)
        return 0 if ok else 1

    from sqlalchemy import select

    from .persistence.models import Tenant
    from .workflow.audit import verify_chain

    c = _container()
    ok_all = True
    with c.read_sf() as s:
        for t in s.scalars(select(Tenant)):
            ok, bad, n = verify_chain(s, t.id)
            ok_all &= ok
            print(f"{t.name}: {n} events, chain {'intact' if ok else f'BROKEN at #{bad}'}")
    return 0 if ok_all else 1


def cmd_export(args: argparse.Namespace) -> int:
    from .web.views import workflow_export

    c = _container()
    with c.read_sf() as s:
        data = workflow_export(s, c.service, args.workflow_id)
    text = json.dumps(redact(data), indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Exported (redacted) to {args.out}")
    else:
        print(text)
    return 0


def cmd_recompute(args: argparse.Namespace) -> int:
    from .verify import recompute_quote_version

    c = _container()
    with c.read_sf() as s:
        ok, report = recompute_quote_version(s, args.quote_version_id)
    print(report)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(
        prog="python -m opsapp",
        description="Local operations workflow prototype (fictional data, simulated actions).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    db = sub.add_parser("db", help="database migrations")
    db.add_argument("action", choices=["upgrade", "downgrade", "current"])
    db.add_argument("revision", nargs="?", default="-1")
    db.set_defaults(fn=cmd_db)
    sub.add_parser("seed", help="load fictional demo data").set_defaults(fn=cmd_seed)
    sub.add_parser("serve", help="run the web app on 127.0.0.1").set_defaults(fn=cmd_serve)
    dp = sub.add_parser("dispatch", help="run the action dispatcher")
    dp.add_argument("--once", action="store_true", help="process due work and exit")
    dp.set_defaults(fn=cmd_dispatch)
    ev = sub.add_parser("eval", help="run the synthetic evaluation set")
    ev.add_argument("action", choices=["run", "compare"])
    ev.add_argument("--out", default="reports")
    ev.add_argument("--yes", action="store_true", help="confirm the paid comparison run")
    ev.add_argument("--samples", help="folder of redacted, labelled real samples")
    ev.set_defaults(fn=cmd_eval)
    rd = sub.add_parser("redact", help="remove personal details from sample request files")
    rd.add_argument("source", help="folder with the original .txt/.eml/.md files")
    rd.add_argument("--out", required=True, help="new folder for the redacted copies")
    rd.add_argument("--names", help="text file with names to hide, one per line")
    rd.set_defaults(fn=cmd_redact)
    co = sub.add_parser("company", help="create, export or delete a company")
    co.add_argument("action", choices=["create", "export", "delete"])
    co.add_argument("name", help="the firm's name, in quotes")
    co.add_argument("--timezone", help="create: for schedule times, e.g. America/Chicago")
    co.add_argument("--owner-name", help="create: the first owner's name")
    co.add_argument("--owner-email", help="create: the first owner's email")
    co.add_argument("--out", help="export/delete: the JSON file to write")
    co.add_argument("--yes", action="store_true", help="delete: confirm")
    co.set_defaults(fn=cmd_company)
    us = sub.add_parser("user", help="create the first owner of a company")
    us.add_argument("action", choices=["create"])
    us.add_argument("--company", required=True, help="company name, as shown in the app")
    us.add_argument("--name", required=True, help="your name")
    us.add_argument("--email", required=True)
    us.set_defaults(fn=cmd_user)
    bk = sub.add_parser("backup", help="write a consistent copy of the database, or check one")
    bk.add_argument(
        "action", nargs="?", choices=["make-key", "drill"], help="make a key file, or a drill"
    )
    bk.add_argument("--out", help="backup file to write")
    bk.add_argument(
        "--to-folder", "--folder", dest="folder", help="folder for dated, scheduled backups"
    )
    bk.add_argument("--key", help="backup key file (kept outside the backup folder)")
    bk.add_argument("--keep", type=int, default=14, help="how many backups to keep (14)")
    bk.add_argument("--encrypt", action="store_true", help="lock it with a passphrase")
    bk.add_argument("--verify", metavar="FILE", help="check an existing backup instead")
    bk.set_defaults(fn=cmd_backup)
    rs = sub.add_parser("restore", help="replace the database with a backup")
    rs.add_argument("--from", dest="source", required=True)
    rs.add_argument("--yes", action="store_true")
    rs.add_argument("--key", help="key file, for backups made with one")
    rs.set_defaults(fn=cmd_restore)
    au = sub.add_parser("audit", help="verify audit hash chains, or an exported log")
    au.add_argument("action", choices=["verify", "verify-export"])
    au.add_argument("file", nargs="?", help="exported audit-log.json (verify-export)")
    au.set_defaults(fn=cmd_audit)
    ex = sub.add_parser("export", help="export one workflow as redacted JSON")
    ex.add_argument("workflow_id")
    ex.add_argument("--out")
    ex.set_defaults(fn=cmd_export)
    rq = sub.add_parser("recompute-quote", help="independently recompute a stored quote")
    rq.add_argument("quote_version_id")
    rq.set_defaults(fn=cmd_recompute)
    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
