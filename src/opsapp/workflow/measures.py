"""Timings and correction counts measured from real use of the app.

Nothing here is estimated. Timings come from the audit log; corrections compare what the
reader proposed (``scope["proposed"]``, recorded since 0.3.0) with the quote a person
prepared. Workflows from before 0.3.0 have no recorded proposal and are counted as such.
"""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.models import (
    AuditEvent,
    CustomerRequest,
    ExtractionResult,
    Quote,
    QuoteVersion,
    WorkflowInstance,
)

FIELDS = ("customer", "site", "items", "timeframe")
# kept: the proposal was used as is. filled: nothing was proposed and a person supplied it.
# changed: a person replaced or removed what was proposed.
OUTCOMES = ("kept", "filled", "changed")
STAGES = (
    ("to_draft", "Request received to draft quote ready"),
    ("to_submit", "Draft ready to submitted for approval"),
    ("to_decision", "Submitted to approval decision"),
)


@dataclass(frozen=True)
class WorkflowMeasure:
    workflow_id: str
    received_at: datetime
    state: str
    reader: str
    minutes: dict[str, float | None]
    corrections: dict[str, str | None]  # field -> outcome, None when not measurable


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 1)


def _outcome(proposed: Any, final: Any) -> str:
    if proposed == final:
        return "kept"
    return "filled" if proposed in (None, "") else "changed"


def _items_outcome(proposed: dict[str, Any], final: dict[str, Any]) -> str:
    if proposed == final:
        return "kept"
    if not proposed:
        return "filled"
    same_services = set(proposed) == set(final)
    only_blanks = all(proposed[k] is None or proposed[k] == final[k] for k in proposed)
    return "filled" if same_services and only_blanks else "changed"


def _norm_qty(q: Any) -> str | None:
    if q is None:
        return None
    text = str(q)
    return text.rstrip("0").rstrip(".") if "." in text else text


def measure(s: Session, tenant_id: str) -> list[WorkflowMeasure]:
    out: list[WorkflowMeasure] = []
    workflows = s.scalars(
        select(WorkflowInstance)
        .where(WorkflowInstance.tenant_id == tenant_id)
        .order_by(WorkflowInstance.opened_at)
    ).all()
    for wf in workflows:
        req = s.get(CustomerRequest, wf.request_id)
        if req is None:  # pragma: no cover - every workflow has a request
            continue
        first: dict[str, datetime] = {}
        for ev in s.scalars(
            select(AuditEvent)
            .where(AuditEvent.workflow_id == wf.id, AuditEvent.event_type == "state_changed")
            .order_by(AuditEvent.seq)
        ):
            first.setdefault(str(ev.data.get("to")), ev.at)
        decided = min((first[k] for k in ("approved", "rejected") if k in first), default=None)
        extraction = s.scalars(
            select(ExtractionResult)
            .where(ExtractionResult.request_id == req.id)
            .order_by(ExtractionResult.created_at)
        ).first()

        corrections: dict[str, str | None] = dict.fromkeys(FIELDS)
        proposed = (wf.scope or {}).get("proposed")
        quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()
        current = s.get(QuoteVersion, quote.current_version_id) if quote else None
        if proposed is not None and current is not None:
            final_items = {
                r["sku"]: _norm_qty(r["quantity"]) for r in current.inputs.get("requested", [])
            }
            proposed_items = {k: _norm_qty(v) for k, v in proposed["items"].items()}
            corrections = {
                "customer": _outcome(proposed["customer_id"], current.customer_id),
                "site": _outcome(proposed["site_id"], current.site_id),
                "items": _items_outcome(proposed_items, final_items),
                "timeframe": _outcome(proposed["timeframe"], current.schedule_timeframe),
            }
        out.append(
            WorkflowMeasure(
                workflow_id=wf.id,
                received_at=req.received_at,
                state=wf.state,
                reader=f"{extraction.adapter}/{extraction.model_id}" if extraction else "",
                minutes={
                    "to_draft": _minutes(req.received_at, first.get("draft_ready")),
                    "to_submit": _minutes(first.get("draft_ready"), first.get("awaiting_approval")),
                    "to_decision": _minutes(first.get("awaiting_approval"), decided),
                },
                corrections=corrections,
            )
        )
    return out


def summarize(rows: list[WorkflowMeasure]) -> dict[str, Any]:
    stages = []
    for key, label in STAGES:
        values = [r.minutes[key] for r in rows if r.minutes[key] is not None]
        vals = [v for v in values if v is not None]
        stages.append(
            {
                "label": label,
                "n": len(vals),
                "median": round(statistics.median(vals), 1) if vals else None,
                "mean": round(statistics.fmean(vals), 1) if vals else None,
                "max": max(vals) if vals else None,
            }
        )
    measurable = [r for r in rows if r.corrections["customer"] is not None]
    fields = []
    for f in FIELDS:
        counts = {o: sum(1 for r in measurable if r.corrections[f] == o) for o in OUTCOMES}
        fields.append({"field": f, "n": len(measurable), **counts})
    return {
        "workflows": len(rows),
        "with_quote": len(measurable),
        "not_measurable": len(rows) - len(measurable),
        "stages": stages,
        "fields": fields,
    }


def to_csv(rows: list[WorkflowMeasure]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(
        [
            "workflow_id",
            "received_at_utc",
            "state",
            "reader",
            *(f"minutes_{k}" for k, _ in STAGES),
            *(f"{f}_correction" for f in FIELDS),
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.workflow_id,
                r.received_at.isoformat(),
                r.state,
                r.reader,
                *("" if r.minutes[k] is None else r.minutes[k] for k, _ in STAGES),
                *(r.corrections[f] or "" for f in FIELDS),
            ]
        )
    return out.getvalue()
