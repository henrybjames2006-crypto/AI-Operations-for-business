"""Read models for pages and exports. Everything is loaded within the actor's tenant."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import get_scoped, load_actor, require
from ..domain.money import format_usd
from ..domain.roles import Permission, Role, has_permission
from ..domain.states import LABEL, PRE_EXECUTION, STATUS_TEXT, State, can_transition
from ..persistence.models import (
    AIUsage,
    Approval,
    AuditEvent,
    CatalogItem,
    ClarificationQuestion,
    Customer,
    CustomerRequest,
    CustomerSite,
    ExceptionRecord,
    ExecutionAttempt,
    ExternalOperation,
    ExtractionResult,
    PriceEntryRow,
    PricingVersion,
    ProposedAction,
    Quote,
    QuoteVersion,
    SimFault,
    SimOperation,
    Tenant,
    User,
    WorkflowInstance,
)
from ..workflow.audit import verify_chain
from ..workflow.catalog_import import to_csv
from ..workflow.service import ACTION_LABELS, TIMEFRAMES, WorkflowService

TIMEFRAME_TEXT = {
    "next_week": "Next week",
    "this_week": "This week",
    "tomorrow": "Tomorrow",
    "asap": "As soon as possible",
    None: "Not stated",
}


def timeframe_text(tf: str | None) -> str:
    if tf and tf.startswith("date:"):
        return f"On or after {tf[5:]}"
    return TIMEFRAME_TEXT.get(tf, tf or "Not stated")


def _users(s: Session, tenant_id: str) -> dict[str, User]:
    return {u.id: u for u in s.scalars(select(User).where(User.tenant_id == tenant_id))}


def next_step_text(state: State, qv: QuoteVersion | None, actions: list[ProposedAction]) -> str:
    match state:
        case State.RECEIVED:
            return "The request is being read."
        case State.NEEDS_CLARIFICATION:
            return (
                "Answer the questions below. The quote is calculated automatically once "
                "every required answer is in. Nothing is sent to the customer."
            )
        case State.DRAFT_READY:
            return (
                "Review the quote and submit it for approval. Nothing is sent until a "
                "different person (an approver) approves this exact version."
            )
        case State.AWAITING_APPROVAL:
            steps = "; ".join(
                f"{i}) {ACTION_LABELS[a.kind]}" for i, a in enumerate(actions, start=1)
            )
            return (
                f"If approved, these run in order: {steps}. Editing anything first "
                f"withdraws this request for approval."
            )
        case State.APPROVED | State.EXECUTING:
            return (
                "The dispatcher is running the approved simulated actions. Failures are "
                "retried a limited number of times; unclear outcomes go to a person."
            )
        case State.COMPLETED:
            return (
                "Finished. The quote email and schedule proposal were simulated; nothing left "
                "this computer."
            )
        case State.FAILED:
            return (
                "An action failed after its retries. The provider confirmed it did not "
                "happen, so an approver can retry it safely or cancel."
            )
        case State.ESCALATED:
            return (
                "Waiting for a person. Nothing more runs automatically until an approver or "
                "owner records a decision below."
            )
        case State.REJECTED:
            return "The approver rejected the quote. The preparer can revise it or cancel."
        case State.CANCELED:
            return "Canceled. Actions that had already happened were not undone (see history)."
    return ""


def workflow_detail(
    s: Session, svc: WorkflowService, actor_id: str, workflow_id: str
) -> dict[str, Any]:
    actor = load_actor(s, actor_id)
    require(actor, Permission.VIEW)
    wf = get_scoped(s, WorkflowInstance, workflow_id, actor.tenant_id)
    users = _users(s, actor.tenant_id)
    state = State(wf.state)
    req = s.get(CustomerRequest, wf.request_id)
    extraction = s.scalars(
        select(ExtractionResult)
        .where(ExtractionResult.request_id == wf.request_id)
        .order_by(ExtractionResult.created_at.desc())
    ).first()
    scope = wf.scope
    customer = s.get(Customer, scope["customer_id"]) if scope.get("customer_id") else None
    site = s.get(CustomerSite, scope["site_id"]) if scope.get("site_id") else None
    customer_sites = (
        list(
            s.scalars(
                select(CustomerSite)
                .where(CustomerSite.customer_id == customer.id)
                .order_by(CustomerSite.label)
            )
        )
        if customer
        else []
    )
    questions = list(
        s.scalars(
            select(ClarificationQuestion)
            .where(ClarificationQuestion.workflow_id == wf.id)
            .order_by(ClarificationQuestion.created_at)
        )
    )
    quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()
    versions = (
        list(
            s.scalars(
                select(QuoteVersion)
                .where(QuoteVersion.quote_id == quote.id)
                .order_by(QuoteVersion.version_no.desc())
            )
        )
        if quote
        else []
    )
    current = svc.current_quote_version(s, wf)
    pv_numbers = {
        pv.id: pv.version_no
        for pv in s.scalars(
            select(PricingVersion).where(PricingVersion.tenant_id == actor.tenant_id)
        )
    }
    actions = list(
        s.scalars(
            select(ProposedAction)
            .where(ProposedAction.workflow_id == wf.id)
            .order_by(ProposedAction.created_at, ProposedAction.sequence)
        )
    )
    attempts = {
        a.id: list(
            s.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.action_id == a.id)
                .order_by(ExecutionAttempt.attempt_no)
            )
        )
        for a in actions
    }
    ext_ops = {
        o.action_id: o
        for o in s.scalars(
            select(ExternalOperation).where(
                ExternalOperation.action_id.in_([a.id for a in actions])
            )
        )
    }
    approvals = list(
        s.scalars(
            select(Approval).where(Approval.workflow_id == wf.id).order_by(Approval.decided_at)
        )
    )
    exceptions = list(
        s.scalars(
            select(ExceptionRecord)
            .where(ExceptionRecord.workflow_id == wf.id)
            .order_by(ExceptionRecord.opened_at)
        )
    )
    events = list(
        s.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == actor.tenant_id, AuditEvent.workflow_id == wf.id)
            .order_by(AuditEvent.seq)
        )
    )
    usage = list(s.scalars(select(AIUsage).where(AIUsage.workflow_id == wf.id)))
    live_actions = [
        a
        for a in actions
        if a.status != "voided" and current is not None and a.quote_version_id == current.id
    ]
    subject_hash = None
    if state == State.AWAITING_APPROVAL:
        subject_hash = svc._subject(s, wf)[2]
    role = Role(actor.role)
    own_work = current is not None and actor.id in (current.prepared_by, wf.submitted_by)

    def can(p: Permission) -> bool:
        return has_permission(role, p)

    catalog = []
    if current is not None or state == State.NEEDS_CLARIFICATION:
        pv = s.scalars(
            select(PricingVersion)
            .where(PricingVersion.tenant_id == actor.tenant_id, PricingVersion.status == "approved")
            .order_by(PricingVersion.version_no.desc())
        ).first()
        if pv:
            catalog = [
                (item.sku, item.name, item.unit)
                for _row, item in s.execute(
                    select(PriceEntryRow, CatalogItem)
                    .join(CatalogItem, CatalogItem.id == PriceEntryRow.catalog_item_id)
                    .where(PriceEntryRow.pricing_version_id == pv.id)
                    .order_by(CatalogItem.sku)
                ).all()
            ]
    return {
        "wf": wf,
        "state": state,
        "label": LABEL[state],
        "status_text": STATUS_TEXT[state],
        "next_step": next_step_text(state, current, live_actions),
        "req": req,
        "extraction": extraction,
        "scope": scope,
        "customer": customer,
        "site": site,
        "customer_sites": customer_sites,
        "customers": list(
            s.scalars(
                select(Customer)
                .where(Customer.tenant_id == actor.tenant_id)
                .order_by(Customer.name)
            )
        ),
        "questions": questions,
        "open_questions": [q for q in questions if q.answer is None and not q.superseded],
        "quote": current,
        "versions": versions,
        "pv_numbers": pv_numbers,
        "actions": actions,
        "live_actions": live_actions,
        "attempts": attempts,
        "ext_ops": ext_ops,
        "action_labels": ACTION_LABELS,
        "approvals": approvals,
        "exceptions": exceptions,
        "open_exceptions": [e for e in exceptions if e.resolved_at is None],
        "events": events,
        "usage": usage,
        "users": users,
        "subject_hash": subject_hash,
        "timeframe_text": timeframe_text,
        "timeframes": TIMEFRAMES,
        "catalog": catalog,
        "own_work": own_work,
        "can": {
            "answer": can(Permission.ANSWER_CLARIFICATION) and state == State.NEEDS_CLARIFICATION,
            "edit": can(Permission.PREPARE_QUOTE)
            and state in (State.DRAFT_READY, State.AWAITING_APPROVAL, State.APPROVED),
            "submit": can(Permission.SUBMIT_FOR_APPROVAL) and state == State.DRAFT_READY,
            "approve": can(Permission.APPROVE) and state == State.AWAITING_APPROVAL,
            "revise": can(Permission.PREPARE_QUOTE) and state == State.REJECTED,
            "cancel": can_transition(state, State.CANCELED)
            and (
                can(Permission.CANCEL_ANY)
                or (state in PRE_EXECUTION and can(Permission.CANCEL_BEFORE_APPROVAL))
            ),
            "resolve": can(Permission.RESOLVE_ESCALATION) and state == State.ESCALATED,
            "retry": can(Permission.RETRY_EXECUTION) and state == State.FAILED,
        },
    }


def workflow_rows(s: Session, actor: User, state: str | None = None) -> list[dict[str, Any]]:
    q = select(WorkflowInstance).where(WorkflowInstance.tenant_id == actor.tenant_id)
    if state:
        q = q.where(WorkflowInstance.state == state)
    rows = []
    customers = {
        c.id: c.name
        for c in s.scalars(select(Customer).where(Customer.tenant_id == actor.tenant_id))
    }
    for wf in s.scalars(q.order_by(WorkflowInstance.updated_at.desc())):
        req = s.get(CustomerRequest, wf.request_id)
        quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()
        qv = (
            s.get(QuoteVersion, quote.current_version_id)
            if quote and quote.current_version_id
            else None
        )
        open_q = len(
            [
                1
                for q2 in s.scalars(
                    select(ClarificationQuestion).where(
                        ClarificationQuestion.workflow_id == wf.id,
                        ClarificationQuestion.answer.is_(None),
                        ClarificationQuestion.superseded.is_(False),
                    )
                )
            ]
        )
        rows.append(
            {
                "wf": wf,
                "label": LABEL[State(wf.state)],
                "state": wf.state,
                "customer": customers.get(wf.scope.get("customer_id") or "", "Not identified yet"),
                "summary": (req.raw_text if req else "")[:110],
                "total": format_usd(qv.total) if qv else "",
                "quote_version": qv.version_no if qv else None,
                "open_questions": open_q,
            }
        )
    return rows


def dashboard(s: Session, actor: User) -> dict[str, Any]:
    rows = workflow_rows(s, actor)
    counts = Counter(r["state"] for r in rows)
    total = len(rows)
    completed = counts.get("completed", 0)
    ever_escalated = {
        e.workflow_id
        for e in s.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == actor.tenant_id, AuditEvent.event_type == "state_changed"
            )
        )
        if e.data.get("to") == "escalated"
    }
    # Review time: from first reaching Draft ready to submission for approval.
    drafted: dict[str, Any] = {}
    review_secs: list[float] = []
    for e in s.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == actor.tenant_id, AuditEvent.event_type == "state_changed")
        .order_by(AuditEvent.seq)
    ):
        if e.data.get("to") == "draft_ready" and e.workflow_id not in drafted:
            drafted[e.workflow_id or ""] = e.at
        if e.data.get("to") == "awaiting_approval" and e.workflow_id in drafted:
            review_secs.append((e.at - drafted.pop(e.workflow_id or "")).total_seconds())
    cost = sum(
        (
            u.est_cost_usd
            for u in s.scalars(select(AIUsage).where(AIUsage.tenant_id == actor.tenant_id))
        ),
        Decimal("0"),
    )
    open_exc = s.scalars(
        select(ExceptionRecord).where(
            ExceptionRecord.tenant_id == actor.tenant_id, ExceptionRecord.resolved_at.is_(None)
        )
    ).all()
    return {
        "rows": rows,
        "counts": {LABEL[st]: counts.get(st.value, 0) for st in State},
        "total": total,
        "completed": completed,
        "completion_rate": f"{completed / total:.0%}" if total else "n/a",
        "escalation_rate": f"{len(ever_escalated) / total:.0%}" if total else "n/a",
        "avg_review": (
            f"{sum(review_secs) / len(review_secs) / 60:.1f} min" if review_secs else "n/a"
        ),
        "cost_per_completed": f"${cost / completed:.4f}" if completed else "n/a",
        "ai_cost_total": f"${cost:.4f}",
        "open_exceptions": len(open_exc),
        "awaiting": counts.get("awaiting_approval", 0),
        "recent_completed": [r for r in rows if r["state"] == "completed"][:5],
        "attention": [
            r
            for r in rows
            if r["state"] in ("needs_clarification", "failed", "escalated", "rejected")
        ],
    }


def approvals_queue(s: Session, svc: WorkflowService, actor: User) -> list[dict[str, Any]]:
    return [r for r in workflow_rows(s, actor, "awaiting_approval")]


def exceptions_list(s: Session, actor: User) -> list[dict[str, Any]]:
    users = _users(s, actor.tenant_id)
    out = []
    for e in s.scalars(
        select(ExceptionRecord)
        .where(ExceptionRecord.tenant_id == actor.tenant_id)
        .order_by(ExceptionRecord.opened_at.desc())
    ):
        wf = s.get(WorkflowInstance, e.workflow_id)
        out.append(
            {
                "e": e,
                "wf": wf,
                "state_label": LABEL[State(wf.state)] if wf else "",
                "resolver": users[e.resolved_by].display_name if e.resolved_by else "",
            }
        )
    return out


def catalog_view(s: Session, actor: User) -> dict[str, Any]:
    items = {
        i.id: i
        for i in s.scalars(
            select(CatalogItem)
            .where(CatalogItem.tenant_id == actor.tenant_id)
            .order_by(CatalogItem.sku)
        )
    }
    versions = []
    for pv in s.scalars(
        select(PricingVersion)
        .where(PricingVersion.tenant_id == actor.tenant_id)
        .order_by(PricingVersion.version_no.desc())
    ):
        entries = s.scalars(
            select(PriceEntryRow).where(PriceEntryRow.pricing_version_id == pv.id)
        ).all()
        pending = {c["sku"]: c for c in pv.catalog_changes or []} if pv.status == "draft" else {}
        versions.append(
            {
                "pv": pv,
                "entries": sorted(
                    [(items[e.catalog_item_id], e) for e in entries], key=lambda x: x[0].sku
                ),
                "pending": pending,
                "dropped": sorted(
                    i.sku for i in items.values() if i.active and i.sku not in pending
                )
                if pending
                else [],
            }
        )
    return {
        "items": [i for i in items.values() if i.active],
        "versions": versions,
        "can_manage": has_permission(Role(actor.role), Permission.MANAGE_CATALOG),
        "import_problems": [],
    }


def catalog_csv(s: Session, actor: User) -> str:
    """The services and prices in effect, in the import format, for editing in Excel."""
    pv = s.scalars(
        select(PricingVersion)
        .where(PricingVersion.tenant_id == actor.tenant_id, PricingVersion.status == "approved")
        .order_by(PricingVersion.version_no.desc())
    ).first()
    if pv is None:
        return to_csv([])
    rows = s.execute(
        select(PriceEntryRow, CatalogItem)
        .join(CatalogItem, CatalogItem.id == PriceEntryRow.catalog_item_id)
        .where(PriceEntryRow.pricing_version_id == pv.id, CatalogItem.active.is_(True))
        .order_by(CatalogItem.sku)
    ).all()
    return to_csv(
        [
            {
                "sku": item.sku,
                "name": item.name,
                "unit": item.unit,
                "unit_price": str(e.unit_price),
                "min_qty": str(e.min_qty),
                "max_qty": str(e.max_qty),
                "quantity_step": str(item.quantity_step),
                "onsite": "yes" if item.onsite else "no",
                "keywords": "; ".join(item.keywords),
                "description": item.description,
            }
            for e, item in rows
        ]
    )


def simulation_view(s: Session, actor: User) -> dict[str, Any]:
    return {
        "ops": list(
            s.scalars(
                select(SimOperation)
                .where(SimOperation.tenant_id == actor.tenant_id)
                .order_by(SimOperation.created_at.desc())
            )
        ),
        "faults": list(
            s.scalars(
                select(SimFault)
                .where(SimFault.tenant_id == actor.tenant_id)
                .order_by(SimFault.created_at.desc())
            )
        ),
    }


def audit_view(s: Session, actor: User) -> dict[str, Any]:
    ok, bad, n = verify_chain(s, actor.tenant_id)
    events = list(
        s.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == actor.tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(300)
        )
    )
    return {"ok": ok, "bad": bad, "count": n, "events": events, "users": _users(s, actor.tenant_id)}


def workflow_export(
    s: Session, svc: WorkflowService, workflow_id: str, actor_id: str | None = None
) -> dict[str, Any]:
    """A JSON-safe record of one workflow (for the CLI and the download link)."""
    wf = s.get(WorkflowInstance, workflow_id)
    if wf is None:
        raise ValueError("Workflow not found.")
    if actor_id is not None:
        actor = load_actor(s, actor_id)
        require(actor, Permission.VIEW)
        get_scoped(s, WorkflowInstance, workflow_id, actor.tenant_id)
    tenant = s.get(Tenant, wf.tenant_id)
    req = s.get(CustomerRequest, wf.request_id)
    quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()

    def iso(d: Any) -> Any:
        return d.isoformat() if d else None

    return {
        "notice": "Fictional data. All actions simulated. Exported from a local prototype.",
        "tenant": tenant.name if tenant else None,
        "workflow": {
            "id": wf.id,
            "state": wf.state,
            "opened_at": iso(wf.opened_at),
            "closed_at": iso(wf.closed_at),
            "scope": wf.scope,
        },
        "request": {
            "id": req.id,
            "sender": req.sender,
            "received_at": iso(req.received_at),
            "sha256": req.raw_sha256,
            "text": req.raw_text,
        }
        if req
        else None,
        "quote_versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "total": str(v.total),
                "currency": v.currency,
                "content_hash": v.content_hash,
                "inputs": v.inputs,
                "calculation": v.calculation,
                "assumptions": v.assumptions,
                "prepared_by": v.prepared_by,
                "created_at": iso(v.created_at),
            }
            for v in (
                s.scalars(
                    select(QuoteVersion)
                    .where(QuoteVersion.quote_id == quote.id)
                    .order_by(QuoteVersion.version_no)
                )
                if quote
                else []
            )
        ],
        "actions": [
            {
                "id": a.id,
                "kind": a.kind,
                "status": a.status,
                "simulated": True,
                "idempotency_key": a.idempotency_key,
                "payload": a.payload,
                "attempts": [
                    {
                        "no": t.attempt_no,
                        "kind": t.kind,
                        "outcome": t.outcome,
                        "error": t.error,
                        "started_at": iso(t.started_at),
                    }
                    for t in s.scalars(
                        select(ExecutionAttempt)
                        .where(ExecutionAttempt.action_id == a.id)
                        .order_by(ExecutionAttempt.attempt_no)
                    )
                ],
            }
            for a in s.scalars(
                select(ProposedAction)
                .where(ProposedAction.workflow_id == wf.id)
                .order_by(ProposedAction.created_at)
            )
        ],
        "approvals": [
            {
                "decision": a.decision,
                "approver_id": a.approver_id,
                "quote_version_id": a.quote_version_id,
                "subject_hash": a.subject_hash,
                "decided_at": iso(a.decided_at),
                "reason": a.reason,
                "invalidated_at": iso(a.invalidated_at),
            }
            for a in s.scalars(select(Approval).where(Approval.workflow_id == wf.id))
        ],
        "history": [
            {
                "seq": e.seq,
                "at": iso(e.at),
                "actor_type": e.actor_type,
                "actor_id": e.actor_id,
                "event": e.event_type,
                "message": e.message,
                "hash": e.hash,
            }
            for e in s.scalars(
                select(AuditEvent)
                .where(AuditEvent.tenant_id == wf.tenant_id, AuditEvent.workflow_id == wf.id)
                .order_by(AuditEvent.seq)
            )
        ],
    }
