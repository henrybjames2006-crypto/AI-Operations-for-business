"""Export one company's records, and delete a company, for the end of a pilot.

Both are run from the command line on the computer that holds the database. The export
leaves out sign-in secrets: password hashes, authenticator secrets, sessions and recovery
codes. Deletion always writes an export first, removes every row of that company in one
transaction, and appends a line (no personal data) to ``deletions.log`` next to the
database.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Tenant

SKIPPED_TABLES = {"auth_sessions", "recovery_codes"}
SKIPPED_COLUMNS = {
    "users": {"password_hash", "totp_secret", "totp_last_step", "failed_logins", "locked_until"}
}


class CompanyDataError(Exception):
    pass


def _tenant_tables() -> list[Any]:
    """Tables holding company rows, parents before children."""
    return [t for t in Base.metadata.sorted_tables if "tenant_id" in t.c]


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def find_company(s: Session, name: str) -> Tenant:
    wanted = " ".join(name.split()).lower()
    matches = s.scalars(select(Tenant).where(func.lower(Tenant.name) == wanted)).all()
    if not matches:
        names = ", ".join(t.name for t in s.scalars(select(Tenant).order_by(Tenant.name)))
        raise CompanyDataError(f"No company called {name!r}. Companies: {names or 'none'}.")
    return matches[0]


def export_company(s: Session, tenant: Tenant, now: datetime) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _tenant_tables():
        if table.name in SKIPPED_TABLES:
            continue
        hidden = SKIPPED_COLUMNS.get(table.name, set())
        cols = [c for c in table.c if c.name not in hidden]
        rows = s.execute(select(*cols).where(table.c.tenant_id == tenant.id)).mappings()
        tables[table.name] = [{k: _plain(v) for k, v in row.items()} for row in rows]
    return {
        "format": "opsapp-company-export-1",
        "exported_at": now.isoformat(),
        "company": {
            "id": tenant.id,
            "name": tenant.name,
            "currency": tenant.currency,
            "timezone": tenant.timezone,
            "created_at": _plain(tenant.created_at),
        },
        "left_out": "password hashes, authenticator secrets, sign-in sessions, recovery codes",
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }


def write_export(sf: sessionmaker[Session], name: str, out: Path, now: datetime) -> dict[str, Any]:
    if out.exists():
        raise CompanyDataError(f"File already exists, not replaced: {out}")
    with sf() as s:
        doc = export_company(s, find_company(s, name), now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return doc


def delete_company(
    sf: sessionmaker[Session], name: str, export_to: Path, log_dir: Path, now: datetime
) -> dict[str, int]:
    """Export, then remove every row of the company. Returns rows removed per table."""
    doc = write_export(sf, name, export_to, now)
    removed: dict[str, int] = {}
    with sf.begin() as s:
        tenant = find_company(s, name)
        if tenant.id != doc["company"]["id"]:  # pragma: no cover - renamed in between
            raise CompanyDataError("The company changed during the export. Nothing deleted.")
        for table in reversed(_tenant_tables()):
            result = s.execute(delete(table).where(table.c.tenant_id == tenant.id))
            removed[table.name] = int(result.rowcount or 0)  # type: ignore[attr-defined]
        s.delete(tenant)
    digest = hashlib.sha256(export_to.read_bytes()).hexdigest()
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "deletions.log").open("a", encoding="utf-8") as log:
        log.write(
            f"{now.isoformat()} deleted company id {doc['company']['id']}: "
            f"{sum(removed.values())} rows; export sha256 {digest}\n"
        )
    return removed
