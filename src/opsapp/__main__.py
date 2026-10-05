"""Command line: ``python -m opsapp <command>``.

Commands: db upgrade | db downgrade | db current | seed | serve | dispatch | eval run |
backup | restore | audit verify | export | recompute-quote
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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
        f"users created. Open the app and pick a user on the sign-in page."
    )
    return 0


def cmd_serve(_args: argparse.Namespace) -> int:
    import uvicorn

    from .web.app import create_app

    settings = load_settings()
    if not settings.database_path.exists():
        print("No database yet. Run: python -m opsapp db upgrade; python -m opsapp seed")
        return 1
    print(f"Serving on http://{settings.host}:{settings.port} (local only). Ctrl+C to stop.")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
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


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals.runner import run_all, write_report

    results = run_all()
    md, js = write_report(results, Path(args.out))
    print(md.read_text(encoding="utf-8").split("\n## Cases")[0])
    print(f"Report: {md}\nData:   {js}")
    return 0 if results["summary"]["failed"] == 0 else 1


def cmd_backup(args: argparse.Namespace) -> int:
    from .persistence.backup import backup

    settings = load_settings()
    out = backup(settings.database_path, Path(args.out))
    print(f"Backup written and integrity-checked: {out}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from .persistence.backup import restore

    settings = load_settings()
    if not args.yes:
        print(
            "Restore overwrites the current database. Stop the server and dispatcher, then "
            "re-run with --yes."
        )
        return 1
    restore(Path(args.source), settings.database_path)
    print(f"Restored {args.source} into {settings.database_path}.")
    return 0


def cmd_audit(_args: argparse.Namespace) -> int:
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
    ev.add_argument("action", choices=["run"])
    ev.add_argument("--out", default="reports")
    ev.set_defaults(fn=cmd_eval)
    bk = sub.add_parser("backup", help="write a consistent copy of the database")
    bk.add_argument("--out", required=True)
    bk.set_defaults(fn=cmd_backup)
    rs = sub.add_parser("restore", help="replace the database with a backup")
    rs.add_argument("--from", dest="source", required=True)
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(fn=cmd_restore)
    au = sub.add_parser("audit", help="verify audit hash chains")
    au.add_argument("action", choices=["verify"])
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
