"""Append-only audit log with a per-tenant hash chain.

Each event stores the hash of the previous event, so editing or deleting a row breaks the
chain and ``verify_chain`` reports where. This detects tampering in the local database; it
does not prevent someone with file access from rewriting the whole chain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.hashing import sha256_hex
from ..ids import new_id
from ..persistence.models import AuditEvent

GENESIS = "0" * 64


def _event_hash(e: AuditEvent) -> str:
    return sha256_hex(
        {
            "tenant_id": e.tenant_id,
            "seq": e.seq,
            "workflow_id": e.workflow_id,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "event_type": e.event_type,
            "message": e.message,
            "data": e.data,
            "at": e.at.isoformat(),
            "prev_hash": e.prev_hash,
        }
    )


def append(
    s: Session,
    *,
    tenant_id: str,
    event_type: str,
    message: str,
    at: datetime,
    actor_type: str = "user",
    actor_id: str | None = None,
    workflow_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    last = s.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    ).first()
    seq = (last.seq + 1) if last else 1
    event = AuditEvent(
        id=new_id("audit"),
        tenant_id=tenant_id,
        seq=seq,
        workflow_id=workflow_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type=event_type,
        message=message,
        data=data or {},
        at=at.astimezone(UTC),
        prev_hash=last.hash if last else GENESIS,
        hash="",
    )
    event.hash = _event_hash(event)
    s.add(event)
    s.flush()
    return event


def verify_chain(s: Session, tenant_id: str) -> tuple[bool, int | None, int]:
    """Return (ok, first_bad_seq, events_checked)."""
    prev = GENESIS
    count = 0
    expected_seq = 1
    for e in s.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    ):
        count += 1
        if e.seq != expected_seq or e.prev_hash != prev or _event_hash(e) != e.hash:
            return False, e.seq, count
        prev = e.hash
        expected_seq += 1
    return True, None, count


def event_count(s: Session, tenant_id: str) -> int:
    return (
        s.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        )
        or 0
    )
