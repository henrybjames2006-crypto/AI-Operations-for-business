"""Export of a company's audit log, with what is needed to check its hash chain.

``opsapp.verify.verify_audit_export`` checks an exported JSON file using only the standard
library and the rules written in the file itself, so the firm can check it without this app.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import AuditEvent, Tenant
from .audit import GENESIS, verify_chain

HASH_RULE = (
    "Each event's hash is the SHA-256 (hex) of the UTF-8 JSON object with the keys tenant_id, "
    "seq, workflow_id, actor_type, actor_id, event_type, message, data, at and prev_hash, "
    "written with sorted keys, no spaces (separators ',' and ':') and non-ASCII characters "
    "kept as they are. prev_hash is the previous event's hash; the first event uses "
    f"{GENESIS[:8]}... (64 zeros)."
)
FIELDS = (
    "seq",
    "at",
    "actor_type",
    "actor_id",
    "event_type",
    "workflow_id",
    "message",
    "data",
    "prev_hash",
    "hash",
)


def _events(s: Session, tenant_id: str) -> list[dict[str, Any]]:
    rows = s.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    )
    return [
        {
            "seq": e.seq,
            "at": e.at.isoformat(),
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "event_type": e.event_type,
            "workflow_id": e.workflow_id,
            "message": e.message,
            "data": e.data,
            "prev_hash": e.prev_hash,
            "hash": e.hash,
        }
        for e in rows
    ]


def to_json(s: Session, tenant_id: str, exported_at: datetime) -> str:
    tenant = s.get(Tenant, tenant_id)
    ok, bad, n = verify_chain(s, tenant_id)
    payload = {
        "format": "opsapp-audit-export-1",
        "tenant_id": tenant_id,
        "company": tenant.name if tenant else "",
        "exported_at": exported_at.isoformat(),
        "event_count": n,
        "chain_intact_at_export": ok,
        "first_broken_seq": bad,
        "hash_rule": HASH_RULE,
        "events": _events(s, tenant_id),
    }
    return json.dumps(payload, indent=1, ensure_ascii=False)


def to_csv(s: Session, tenant_id: str) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(FIELDS)
    for e in _events(s, tenant_id):
        row = dict(e, data=json.dumps(e["data"], sort_keys=True, ensure_ascii=False))
        # A cell starting with a formula character is prefixed so spreadsheets show it as text.
        w.writerow([_safe_cell(row[f]) for f in FIELDS])
    return out.getvalue()


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value
