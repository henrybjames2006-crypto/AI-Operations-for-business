"""Workflow use cases.

Every public method:
1. loads the acting user and checks their permission (server side),
2. loads records only within that user's tenant,
3. changes state only through ``domain.states.check_transition``,
4. writes an audit event in the same transaction as the change.

The extractor's output is treated as a proposal. Customer matching, pricing, approvals and
execution are decided here and in the domain layer, never by the extractor.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..ai.guard import validate_output
from ..ai.ports import Extractor
from ..ai.schema import CatalogHint, CustomerHint, ExtractionContext
from ..authz import get_scoped, load_actor, require
from ..clock import Clock
from ..domain.errors import (
    CatalogImportError,
    Conflict,
    DomainError,
    InvalidTransition,
    PermissionDenied,
    StaleApproval,
    ValidationError,
)
from ..domain.hashing import sha256_hex
from ..domain.money import format_usd, to_decimal
from ..domain.pricing import RequestedLine, calculate_quote, quantity_problem
from ..domain.roles import Permission
from ..domain.scheduling import propose_slots
from ..domain.states import LABEL, PRE_EXECUTION, TERMINAL, State, check_transition
from ..ids import new_id
from ..persistence.models import (
    AIUsage,
    Approval,
    CatalogItem,
    ClarificationQuestion,
    Customer,
    CustomerRequest,
    CustomerSite,
    ExceptionRecord,
    ExternalOperation,
    ExtractionResult,
    OutboxEntry,
    PriceEntryRow,
    PricingVersion,
    ProposedAction,
    Quote,
    QuoteVersion,
    SimFault,
    Tenant,
    User,
    WorkflowInstance,
)
from . import audit
from .catalog import current_pricing_version, snapshot_for, snapshot_to_json
from .catalog_import import parse_catalog_csv

T = TypeVar("T")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIMEFRAMES = ("next_week", "this_week", "tomorrow", "asap")
ACTION_LABELS = {
    "send_quote": "Send quote to customer (SIMULATED email, nothing is sent)",
    "propose_schedule": "Propose appointment times (SIMULATED calendar, no event is created)",
}


@dataclass(frozen=True)
class SubmitResult:
    workflow_id: str
    duplicate: bool = False


@dataclass(frozen=True)
class CancelSummary:
    voided: list[str]
    already_happened: list[str]
    outcome_unknown: list[str]
    did_not_happen: list[str]


class WorkflowService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        extractor: Extractor,
        max_request_chars: int = 20_000,
    ) -> None:
        self.sf = session_factory
        self.clock = clock
        self.extractor = extractor
        self.max_request_chars = max_request_chars

    # ------------------------------------------------------------------ helpers

    def _run(
        self, actor_id: str, fn: Callable[[Session, User], T], workflow_id: str | None = None
    ) -> T:
        """Run a use case in one transaction. Refused transitions are audited separately."""
        try:
            with self.sf.begin() as s:
                actor = load_actor(s, actor_id)
                return fn(s, actor)
        except (InvalidTransition, PermissionDenied, StaleApproval) as exc:
            self._audit_refusal(actor_id, workflow_id, exc)
            raise

    def _audit_refusal(self, actor_id: str, workflow_id: str | None, exc: DomainError) -> None:
        with self.sf.begin() as s:
            actor = s.get(User, actor_id)
            if actor is None:
                return
            if workflow_id is not None:
                wf = s.get(WorkflowInstance, workflow_id)
                if wf is None or wf.tenant_id != actor.tenant_id:
                    return  # never write into another tenant's log
            audit.append(
                s,
                tenant_id=actor.tenant_id,
                event_type="action_refused",
                message=f"Refused: {exc}",
                at=self.clock.now(),
                actor_id=actor.id,
                workflow_id=workflow_id,
                data={"error": type(exc).__name__},
            )

    def _audit(
        self,
        s: Session,
        wf: WorkflowInstance | None,
        actor: User | None,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
        actor_type: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        audit.append(
            s,
            tenant_id=tenant_id or (wf.tenant_id if wf else actor.tenant_id),  # type: ignore[union-attr]
            event_type=event_type,
            message=message,
            at=self.clock.now(),
            actor_type=actor_type or ("user" if actor else "system"),
            actor_id=actor.id if actor else None,
            workflow_id=wf.id if wf else None,
            data=data,
        )

    def _transition(
        self,
        s: Session,
        wf: WorkflowInstance,
        target: State,
        actor: User | None,
        reason: str,
        actor_type: str | None = None,
    ) -> None:
        current = State(wf.state)
        check_transition(current, target)
        wf.state = target.value
        wf.state_version += 1
        now = self.clock.now()
        wf.updated_at = now
        if target in TERMINAL:
            wf.closed_at = now
        if target == State.DRAFT_READY and wf.review_started_at is None:
            wf.review_started_at = now
        self._audit(
            s,
            wf,
            actor,
            "state_changed",
            f"{LABEL[current]} → {LABEL[target]}: {reason}",
            {"from": current.value, "to": target.value},
            actor_type=actor_type,
        )

    @staticmethod
    def _check_version(wf: WorkflowInstance, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != wf.state_version:
            raise Conflict("This workflow changed since you opened it. Reload and try again.")

    def _wf(self, s: Session, actor: User, workflow_id: str) -> WorkflowInstance:
        return get_scoped(s, WorkflowInstance, workflow_id, actor.tenant_id)

    def _tenant(self, s: Session, tenant_id: str) -> Tenant:
        tenant = s.get(Tenant, tenant_id)
        if tenant is None:  # a broken invariant, never a user error
            raise RuntimeError("tenant is missing")
        return tenant

    # ------------------------------------------------------------------ intake

    def submit_request(
        self,
        actor_id: str,
        text: str,
        sender: str,
        submission_key: str,
        allow_duplicate: bool = False,
    ) -> SubmitResult:
        def run(s: Session, actor: User) -> SubmitResult:
            require(actor, Permission.CREATE_REQUEST)
            body = (text or "").strip()
            if not body:
                raise ValidationError("Paste the customer's request text.")
            if len(body) > self.max_request_chars:
                raise ValidationError(
                    f"Request is longer than {self.max_request_chars} "
                    f"characters; shorten it or split it."
                )
            sender_clean = (sender or "").strip().lower()
            if sender_clean and not EMAIL_RE.match(sender_clean):
                raise ValidationError("Sender must be an email address, or left empty.")
            key = (submission_key or "").strip()
            if not key or len(key) > 80:
                raise ValidationError("Missing form submission key; reload the form.")

            existing = s.scalars(
                select(CustomerRequest).where(
                    CustomerRequest.tenant_id == actor.tenant_id,
                    CustomerRequest.submission_key == key,
                )
            ).first()
            if existing is not None:
                wf = s.scalars(
                    select(WorkflowInstance).where(WorkflowInstance.request_id == existing.id)
                ).one()
                return SubmitResult(wf.id, duplicate=True)

            digest = sha256_hex(body)
            if not allow_duplicate:
                dup = (
                    s.execute(
                        select(WorkflowInstance)
                        .join(CustomerRequest, CustomerRequest.id == WorkflowInstance.request_id)
                        .where(
                            CustomerRequest.tenant_id == actor.tenant_id,
                            CustomerRequest.raw_sha256 == digest,
                            CustomerRequest.sender == sender_clean,
                            WorkflowInstance.state.not_in(
                                [State.CANCELED.value, State.COMPLETED.value]
                            ),
                        )
                    )
                    .scalars()
                    .first()
                )
                if dup is not None:
                    self._audit(
                        s,
                        dup,
                        actor,
                        "duplicate_submission_detected",
                        "The same request text from the same sender was submitted "
                        "again; no new workflow was created.",
                    )
                    return SubmitResult(dup.id, duplicate=True)

            now = self.clock.now()
            req = CustomerRequest(
                id=new_id("request"),
                tenant_id=actor.tenant_id,
                source_channel="pasted",
                sender=sender_clean,
                raw_text=body,
                raw_sha256=digest,
                submission_key=key,
                received_at=now,
                received_by=actor.id,
            )
            wf = WorkflowInstance(
                id=new_id("workflow"),
                tenant_id=actor.tenant_id,
                request_id=req.id,
                state=State.RECEIVED.value,
                state_version=1,
                scope={},
                opened_at=now,
                updated_at=now,
            )
            s.add_all([req, wf])
            s.flush()
            self._audit(
                s,
                wf,
                actor,
                "request_received",
                f"Request received from {sender_clean or 'unknown sender'} "
                f"({len(body)} characters, stored unchanged).",
                {"request_id": req.id, "sha256": digest},
            )
            self._analyze(s, wf, req, actor)
            return SubmitResult(wf.id)

        return self._run(actor_id, run)

    def _context(self, s: Session, tenant_id: str) -> ExtractionContext:
        customers = s.scalars(select(Customer).where(Customer.tenant_id == tenant_id)).all()
        sites = s.scalars(select(CustomerSite).where(CustomerSite.tenant_id == tenant_id)).all()
        items = s.scalars(
            select(CatalogItem).where(
                CatalogItem.tenant_id == tenant_id, CatalogItem.active.is_(True)
            )
        ).all()
        return ExtractionContext(
            today=self.clock.now().date(),
            customers=[CustomerHint(name=c.name, aliases=list(c.aliases)) for c in customers],
            site_labels=sorted({site.label for site in sites}),
            catalog=[CatalogHint(sku=i.sku, name=i.name, keywords=list(i.keywords)) for i in items],
        )

    def _analyze(self, s: Session, wf: WorkflowInstance, req: CustomerRequest, actor: User) -> None:
        ctx = self._context(s, wf.tenant_id)
        run = self.extractor.extract(req.raw_text, req.sender, ctx)
        output, problems = validate_output(run.output, req.raw_text, ctx)
        now = self.clock.now()
        s.add(
            ExtractionResult(
                id=new_id("extraction"),
                tenant_id=wf.tenant_id,
                request_id=req.id,
                adapter=run.provider,
                model_id=run.model_id,
                schema_version=output.schema_version,
                output={
                    **output.model_dump(mode="json"),
                    "validation_problems": problems,
                    "fallback_reason": run.fallback_reason,
                },
                input_chars=run.input_chars,
                output_chars=run.output_chars,
                latency_ms=run.latency_ms,
                created_at=now,
            )
        )
        s.add(
            AIUsage(
                id=new_id("ai_usage"),
                tenant_id=wf.tenant_id,
                workflow_id=wf.id,
                provider=run.provider,
                model_id=run.model_id,
                task="extract_request",
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                latency_ms=run.latency_ms,
                est_cost_usd=Decimal(run.est_cost_usd),
                created_at=now,
            )
        )
        for attempt in run.failed_attempts:
            s.add(
                AIUsage(
                    id=new_id("ai_usage"),
                    tenant_id=wf.tenant_id,
                    workflow_id=wf.id,
                    provider=attempt.provider,
                    model_id=attempt.model_id,
                    task="extract_request_failed",
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    latency_ms=attempt.latency_ms,
                    est_cost_usd=Decimal(attempt.est_cost_usd),
                    created_at=now,
                )
            )
        if run.fallback_reason:
            self._audit(
                s,
                wf,
                None,
                "ai_fallback",
                f"The AI reader was not used ({run.fallback_reason}); the request was read "
                f"by the rule-based reader instead.",
                {"reason": run.fallback_reason, "failed_attempts": len(run.failed_attempts)},
                actor_type="system",
            )
        self._audit(
            s,
            wf,
            None,
            "extraction_completed",
            f"Request read by {run.provider}/{run.model_id}: {len(output.items)} catalog "
            f"match(es), {len(output.unsupported)} unmatched request(s).",
            {
                "provider": run.provider,
                "model_id": run.model_id,
                "input_chars": run.input_chars,
                "output_chars": run.output_chars,
                "latency_ms": run.latency_ms,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "est_cost_usd": run.est_cost_usd,
                "fallback_reason": run.fallback_reason,
                "validation_problems": problems,
            },
            actor_type="system",
        )
        for text in output.suspicious_instructions:
            self._audit(
                s,
                wf,
                None,
                "untrusted_instruction_ignored",
                "The request contains text that reads like an instruction to the "
                "system. It was treated as customer content and ignored.",
                {"excerpt": text[:200]},
                actor_type="system",
            )

        scope: dict[str, Any] = {
            "customer_id": None,
            "customer_source": None,
            "site_id": None,
            "site_source": None,
            "recipient": None,
            "recipient_source": None,
            "items": [
                {
                    "sku": i.sku,
                    "quantity": i.quantity,
                    "quantity_text": i.quantity_text,
                    "evidence": i.evidence,
                    "source": "extracted",
                    "removed": False,
                }
                for i in output.items
            ],
            "unsupported": [{"text": u.text, "status": "open"} for u in output.unsupported],
            "timeframe": output.timeframe,
            "timeframe_text": output.timeframe_text,
            "flags": [
                f"Ignored instruction-like text: “{t[:120]}”"
                for t in output.suspicious_instructions
            ],
            "notes": list(output.notes) + problems,
            "customer_mentions": output.customer_mentions,
            "site_mentions": output.site_mentions,
        }
        self._resolve_customer(s, wf, scope, req.sender, output.customer_mentions)
        # What the reader and the record lookups proposed before any person changed
        # anything. Kept unchanged so corrections can be measured (see workflow/measures.py).
        scope["proposed"] = {
            "customer_id": scope["customer_id"],
            "site_id": scope["site_id"],
            "items": {i["sku"]: i["quantity"] for i in scope["items"]},
            "timeframe": scope["timeframe"],
        }
        wf.scope = scope
        self._ensure_questions(s, wf)
        self._advance(s, wf, actor)

    # ------------------------------------------------------------------ resolution

    def _resolve_customer(
        self,
        s: Session,
        wf: WorkflowInstance,
        scope: dict[str, Any],
        sender: str,
        mentions: list[str],
    ) -> None:
        customers = s.scalars(
            select(Customer).where(Customer.tenant_id == wf.tenant_id).order_by(Customer.name)
        ).all()
        domain = sender.split("@")[-1] if "@" in sender else ""
        by_domain = [c for c in customers if domain and domain in c.email_domains]
        by_mention = [c for c in customers if any(m in mentions for m in [c.name, *c.aliases])]
        chosen: Customer | None = None
        if len(by_domain) == 1:
            other = [c for c in by_mention if c.id != by_domain[0].id]
            if not other:
                chosen = by_domain[0]
                scope["customer_source"] = f"Stated: sender address domain {domain}"
        elif not by_domain and len(by_mention) == 1:
            chosen = by_mention[0]
            said = next(m for m in mentions if m in [chosen.name, *chosen.aliases])
            scope["customer_source"] = f"Inferred: request mentions “{said}”"
        if chosen is not None:
            self._set_customer(s, scope, chosen, sender)

    def _set_customer(
        self, s: Session, scope: dict[str, Any], customer: Customer, sender: str
    ) -> None:
        scope["customer_id"] = customer.id
        domain = sender.split("@")[-1] if "@" in sender else ""
        if sender and domain in customer.email_domains:
            scope["recipient"] = sender
            scope["recipient_source"] = "Stated: the sender of the request"
        else:
            scope["recipient"] = customer.contact_email
            scope["recipient_source"] = "From records: customer's contact address on file"
        sites = s.scalars(
            select(CustomerSite)
            .where(CustomerSite.customer_id == customer.id)
            .order_by(CustomerSite.label)
        ).all()
        mentioned = [site for site in sites if site.label in scope.get("site_mentions", [])]
        scope["site_id"] = None
        scope["site_source"] = None
        if len(mentioned) == 1:
            scope["site_id"] = mentioned[0].id
            scope["site_source"] = f"Stated: request mentions “{mentioned[0].label}”"
        elif len(sites) == 1:
            scope["site_id"] = sites[0].id
            scope["site_source"] = "From records: the customer's only site"

    def _open_questions(self, s: Session, wf: WorkflowInstance) -> list[ClarificationQuestion]:
        return list(
            s.scalars(
                select(ClarificationQuestion).where(
                    ClarificationQuestion.workflow_id == wf.id,
                    ClarificationQuestion.answer.is_(None),
                    ClarificationQuestion.superseded.is_(False),
                )
            ).all()
        )

    def _ask(
        self,
        s: Session,
        wf: WorkflowInstance,
        kind: str,
        field: str,
        question: str,
        options: list[dict[str, str]] | None = None,
        blocking: bool = True,
    ) -> None:
        for q in self._open_questions(s, wf):
            if q.kind == kind and q.field == field:
                return
        s.add(
            ClarificationQuestion(
                id=new_id("question"),
                tenant_id=wf.tenant_id,
                workflow_id=wf.id,
                kind=kind,
                field=field,
                question=question,
                blocking=blocking,
                options=options or [],
                created_at=self.clock.now(),
            )
        )
        s.flush()
        self._audit(
            s,
            wf,
            None,
            "clarification_needed",
            question,
            {"kind": kind, "field": field},
            actor_type="system",
        )

    def _ensure_questions(self, s: Session, wf: WorkflowInstance) -> None:
        """Create a question for every gap in the working scope that has none yet."""
        scope = dict(wf.scope)
        tenant_id = wf.tenant_id
        if scope.get("customer_id") is None:
            customers = s.scalars(
                select(Customer).where(Customer.tenant_id == tenant_id).order_by(Customer.name)
            ).all()
            mentions = scope.get("customer_mentions", [])
            candidates = [c for c in customers if any(m in mentions for m in [c.name, *c.aliases])]
            if len(candidates) >= 2:
                text = (
                    "The request could refer to more than one customer ("
                    + ", ".join(c.name for c in candidates)
                    + "). Which customer is it?"
                )
            elif candidates:
                text = (
                    "The sender's address and the customer named in the text do not match. "
                    "Which customer is it?"
                )
                domain_matches = [c for c in customers if c not in candidates]
                candidates = candidates + domain_matches
            else:
                partial = self._partial_customer_matches(s, wf, list(customers))
                if len(partial) >= 2:
                    text = (
                        "The request could refer to more than one customer ("
                        + ", ".join(c.name for c in partial)
                        + "). Which customer is it?"
                    )
                    candidates = partial
                else:
                    text = (
                        "We could not identify the customer from the sender or the text. "
                        "Which customer is it? (New customers cannot be created in this "
                        "prototype.)"
                    )
                    candidates = partial + [c for c in customers if c not in partial]
            self._ask(
                s,
                wf,
                "choose_customer",
                "customer",
                text,
                [{"value": c.id, "label": c.name} for c in candidates],
            )
            return  # site, recipient and quantities depend on the customer
        if scope.get("site_id") is None:
            sites = s.scalars(
                select(CustomerSite)
                .where(CustomerSite.customer_id == scope["customer_id"])
                .order_by(CustomerSite.label)
            ).all()
            self._ask(
                s,
                wf,
                "choose_site",
                "site",
                "The customer has more than one site. Which site is the work for?",
                [{"value": x.id, "label": f"{x.label} ({x.address})"} for x in sites],
            )
        snap = snapshot_for(s, current_pricing_version(s, tenant_id, self.clock.now()))
        for item in scope.get("items", []):
            if item.get("removed"):
                continue
            entry = snap.entries.get(item["sku"])
            if entry is None:
                self._ask(
                    s,
                    wf,
                    "unsupported",
                    f"sku:{item['sku']}",
                    f"{item['sku']} is not in the approved price list. Remove it, or "
                    f"escalate to a manager?",
                    [
                        {"value": "remove", "label": "Remove it from the quote"},
                        {"value": "escalate", "label": "Escalate"},
                    ],
                )
                continue
            qty = Decimal(item["quantity"]) if item.get("quantity") is not None else None
            problem = quantity_problem(entry, qty)
            if problem:
                said = (
                    f" The request says “{item['quantity_text']}”."
                    if item.get("quantity_text")
                    else ""
                )
                self._ask(
                    s,
                    wf,
                    "quantity",
                    item["sku"],
                    f"{problem}{said} Enter the number of {entry.unit}s, or "
                    f"“remove” to drop this service.",
                )
        for idx, u in enumerate(scope.get("unsupported", [])):
            if u["status"] == "open":
                self._ask(
                    s,
                    wf,
                    "unsupported",
                    f"unsupported:{idx}",
                    f"“{u['text']}” does not match any approved service. Remove "
                    f"it from this request, or escalate to a manager?",
                    [
                        {"value": "remove", "label": "Remove (not part of this quote)"},
                        {"value": "escalate", "label": "Escalate"},
                    ],
                )
        if not any(not i.get("removed") for i in scope.get("items", [])):
            self._ask(
                s,
                wf,
                "add_item",
                "items",
                "No approved catalog service was identified. Which service and quantity "
                "did the customer ask for?",
                [
                    {"value": sku, "label": f"{e.sku}: {e.name} (per {e.unit})"}
                    for sku, e in sorted(snap.entries.items())
                ],
            )

    def _partial_customer_matches(
        self, s: Session, wf: WorkflowInstance, customers: list[Customer]
    ) -> list[Customer]:
        """Customers with a name word that starts with a word in the request (e.g. "Harbor").

        Used only to narrow the options in a question; a partial match never selects a
        customer by itself.
        """
        req = s.get(CustomerRequest, wf.request_id)
        words = {w for w in re.findall(r"[a-z]{4,}", (req.raw_text if req else "").lower())}
        out = []
        for c in customers:
            name_words = {
                w for n in [c.name, *c.aliases] for w in re.findall(r"[a-z]{4,}", n.lower())
            }
            if any(nw.startswith(w) for w in words for nw in name_words):
                out.append(c)
        return out

    def _advance(self, s: Session, wf: WorkflowInstance, actor: User) -> None:
        if State(wf.state) == State.ESCALATED:
            return
        blocking = [q for q in self._open_questions(s, wf) if q.blocking]
        if blocking:
            if State(wf.state) != State.NEEDS_CLARIFICATION:
                self._transition(
                    s,
                    wf,
                    State.NEEDS_CLARIFICATION,
                    None,
                    f"{len(blocking)} question(s) must be answered before quoting.",
                    actor_type="system",
                )
            return
        self._build_quote_version(s, wf, actor, "Draft built from the request and answers.")
        self._transition(
            s,
            wf,
            State.DRAFT_READY,
            None,
            "All required information is available; quote calculated.",
            actor_type="system",
        )

    # ------------------------------------------------------------------ clarification

    def answer_question(
        self,
        actor_id: str,
        workflow_id: str,
        question_id: str,
        answer: str,
        quantity: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.ANSWER_CLARIFICATION)
            wf = self._wf(s, actor, workflow_id)
            self._check_version(wf, expected_version)
            if State(wf.state) != State.NEEDS_CLARIFICATION:
                raise InvalidTransition(
                    "Questions can only be answered while the workflow needs clarification."
                )
            q = get_scoped(s, ClarificationQuestion, question_id, actor.tenant_id)
            if q.workflow_id != wf.id or q.answer is not None or q.superseded:
                raise Conflict("This question was already answered or no longer applies.")
            scope = dict(wf.scope)
            scope["items"] = [dict(i) for i in scope.get("items", [])]
            scope["unsupported"] = [dict(u) for u in scope.get("unsupported", [])]
            value = (answer or "").strip()
            recorded = value
            escalate_reason: str | None = None

            if q.kind in ("choose_customer", "choose_site"):
                allowed = {o["value"]: o["label"] for o in q.options}
                if value not in allowed:
                    raise ValidationError("Choose one of the listed options.")
                recorded = allowed[value]
                if q.kind == "choose_customer":
                    customer = get_scoped(s, Customer, value, actor.tenant_id)
                    req = s.get(CustomerRequest, wf.request_id)
                    if req is None:  # a broken invariant, never a user error
                        raise RuntimeError("req is missing")
                    self._set_customer(s, scope, customer, req.sender)
                    scope["customer_source"] = f"Operator answer: {customer.name}"
                else:
                    site = get_scoped(s, CustomerSite, value, actor.tenant_id)
                    if site.customer_id != scope.get("customer_id"):
                        raise ValidationError("That site belongs to a different customer.")
                    scope["site_id"] = site.id
                    scope["site_source"] = f"Operator answer: {site.label}"
            elif q.kind == "quantity":
                item = next(i for i in scope["items"] if i["sku"] == q.field and not i["removed"])
                if value.lower() == "remove":
                    item["removed"] = True
                    recorded = "remove"
                else:
                    qty = to_decimal(value, field="quantity")
                    snap = snapshot_for(
                        s, current_pricing_version(s, wf.tenant_id, self.clock.now())
                    )
                    problem = quantity_problem(snap.entries[q.field], qty)
                    if problem:
                        raise ValidationError(problem)
                    item["quantity"] = str(qty)
                    item["source"] = "operator"
            elif q.kind == "unsupported":
                if value not in ("remove", "escalate"):
                    raise ValidationError("Choose remove or escalate.")
                if q.field.startswith("unsupported:"):
                    target = scope["unsupported"][int(q.field.split(":")[1])]
                    target["status"] = "removed" if value == "remove" else "escalated"
                    label = target["text"]
                else:
                    sku = q.field.split(":", 1)[1]
                    for i in scope["items"]:
                        if i["sku"] == sku:
                            i["removed"] = value == "remove"
                    label = sku
                if value == "escalate":
                    escalate_reason = f"Unsupported service requested: “{label}”"
            elif q.kind == "add_item":
                if value == "escalate":
                    escalate_reason = "No approved service matches the request."
                else:
                    allowed_skus = {o["value"] for o in q.options}
                    if value not in allowed_skus:
                        raise ValidationError("Choose a service from the catalog.")
                    qty = to_decimal(quantity or "", field="quantity")
                    snap = snapshot_for(
                        s, current_pricing_version(s, wf.tenant_id, self.clock.now())
                    )
                    problem = quantity_problem(snap.entries[value], qty)
                    if problem:
                        raise ValidationError(problem)
                    scope["items"] = [i for i in scope["items"] if i["sku"] != value]
                    scope["items"].append(
                        {
                            "sku": value,
                            "quantity": str(qty),
                            "quantity_text": None,
                            "evidence": "",
                            "source": "operator",
                            "removed": False,
                        }
                    )
                    recorded = f"{value} x {qty}"
            else:  # pragma: no cover - defensive
                raise ValidationError("Unknown question type.")

            q.answer = recorded
            q.answered_by = actor.id
            q.answered_at = self.clock.now()
            wf.scope = scope
            wf.state_version += 1
            self._audit(
                s,
                wf,
                actor,
                "clarification_answered",
                f"Answered “{q.question}” with “{recorded}”.",
                {"question_id": q.id, "kind": q.kind},
            )
            if escalate_reason:
                self._open_exception(s, wf, None, "unsupported_service", escalate_reason, "medium")
                self._transition(s, wf, State.ESCALATED, actor, escalate_reason)
                return
            # Questions that depended on an earlier answer (e.g. site after customer) are
            # recomputed rather than trusted.
            if q.kind == "choose_customer":
                for other in self._open_questions(s, wf):
                    if other.kind == "choose_site":
                        other.superseded = True
            self._ensure_questions(s, wf)
            self._advance(s, wf, actor)

        self._run(actor_id, run, workflow_id)

    # ------------------------------------------------------------------ quotes

    def _build_quote_version(
        self, s: Session, wf: WorkflowInstance, actor: User, reason: str
    ) -> QuoteVersion:
        scope = wf.scope
        now = self.clock.now()
        pv = current_pricing_version(s, wf.tenant_id, now)
        snap = snapshot_for(s, pv)
        active = [i for i in scope.get("items", []) if not i.get("removed")]
        requested = [RequestedLine(sku=i["sku"], quantity=Decimal(i["quantity"])) for i in active]
        calc = calculate_quote(requested, snap)
        customer = get_scoped(s, Customer, scope["customer_id"], wf.tenant_id)
        site = get_scoped(s, CustomerSite, scope["site_id"], wf.tenant_id)
        if site.customer_id != customer.id:
            raise ValidationError("The site does not belong to the customer.")
        recipient = scope.get("recipient") or customer.contact_email
        if not EMAIL_RE.match(recipient):
            raise ValidationError("Recipient must be an email address.")

        items = s.scalars(
            select(CatalogItem).where(
                CatalogItem.tenant_id == wf.tenant_id,
                CatalogItem.sku.in_([r.sku for r in requested]),
            )
        ).all()
        assumptions = [f"{i.name}: {i.description}" for i in sorted(items, key=lambda x: x.sku)]
        assumptions += [r.explanation for r in calc.rules if r.applied and r.kind == "trip_fee"]
        if not scope.get("timeframe"):
            assumptions.append("No timeframe was stated; proposed times start two days out.")
        assumptions.append(calc.tax_note)

        content = {
            "customer_id": customer.id,
            "site_id": site.id,
            "recipient": recipient,
            "timeframe": scope.get("timeframe"),
            "pricing_version_id": pv.id,
            "currency": calc.currency,
            "lines": [
                {
                    "sku": ln.sku,
                    "kind": ln.kind,
                    "quantity": str(ln.quantity),
                    "unit_price": str(ln.unit_price),
                    "amount": str(ln.amount),
                }
                for ln in calc.lines
            ],
            "total": str(calc.total),
        }
        content_hash = sha256_hex(content)

        quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()
        if quote is None:
            quote = Quote(id=new_id("quote"), tenant_id=wf.tenant_id, workflow_id=wf.id)
            s.add(quote)
            s.flush()
        elif quote.current_version_id:
            current = s.get(QuoteVersion, quote.current_version_id)
            if current is not None and current.content_hash == content_hash:
                return current
        version_no = (
            s.scalar(
                select(func.max(QuoteVersion.version_no)).where(QuoteVersion.quote_id == quote.id)
            )
            or 0
        ) + 1
        qv = QuoteVersion(
            id=new_id("quote_version"),
            tenant_id=wf.tenant_id,
            quote_id=quote.id,
            version_no=version_no,
            pricing_version_id=pv.id,
            customer_id=customer.id,
            site_id=site.id,
            recipient_email=recipient,
            schedule_timeframe=scope.get("timeframe"),
            inputs={
                "requested": [{"sku": r.sku, "quantity": str(r.quantity)} for r in requested],
                "pricing": snapshot_to_json(snap, [r.sku for r in requested]),
            },
            calculation=calc.to_json(),
            assumptions=assumptions,
            total=calc.total,
            currency=calc.currency,
            content_hash=content_hash,
            prepared_by=actor.id,
            change_reason=reason,
            created_at=now,
        )
        s.add(qv)
        s.flush()
        quote.current_version_id = qv.id
        self._audit(
            s,
            wf,
            actor,
            "quote_version_created",
            f"Quote version {version_no} calculated from pricing version "
            f"{pv.version_no}: total {format_usd(calc.total)} {calc.currency}. {reason}",
            {"quote_version_id": qv.id, "content_hash": content_hash, "total": str(calc.total)},
        )
        return qv

    def current_quote_version(self, s: Session, wf: WorkflowInstance) -> QuoteVersion | None:
        quote = s.scalars(select(Quote).where(Quote.workflow_id == wf.id)).first()
        if quote is None or quote.current_version_id is None:
            return None
        return s.get(QuoteVersion, quote.current_version_id)

    def edit_quote(
        self,
        actor_id: str,
        workflow_id: str,
        *,
        lines: list[tuple[str, str]],
        recipient: str,
        site_id: str,
        timeframe: str | None,
        customer_id: str | None = None,
        reason: str = "",
        expected_version: int | None = None,
    ) -> None:
        """Change material quote details. Any change voids pending or granted approval."""

        def run(s: Session, actor: User) -> None:
            require(actor, Permission.PREPARE_QUOTE)
            wf = self._wf(s, actor, workflow_id)
            self._check_version(wf, expected_version)
            state = State(wf.state)
            if state not in (State.DRAFT_READY, State.AWAITING_APPROVAL, State.APPROVED):
                raise InvalidTransition(
                    f"A quote cannot be edited while the workflow is {LABEL[state]}."
                )
            scope = dict(wf.scope)
            if customer_id and customer_id != scope.get("customer_id"):
                get_scoped(s, Customer, customer_id, actor.tenant_id)
                scope["customer_id"] = customer_id
                scope["customer_source"] = f"Edited by {actor.display_name}"
            site = get_scoped(s, CustomerSite, site_id, actor.tenant_id)
            if site.customer_id != scope["customer_id"]:
                raise ValidationError("The site does not belong to the customer.")
            if site.id != scope.get("site_id"):
                scope["site_id"] = site.id
                scope["site_source"] = f"Edited by {actor.display_name}"
            rec = (recipient or "").strip().lower()
            if not EMAIL_RE.match(rec):
                raise ValidationError("Recipient must be an email address.")
            if rec != scope.get("recipient"):
                scope["recipient"] = rec
                scope["recipient_source"] = f"Edited by {actor.display_name}"
            if timeframe not in (None, "", *TIMEFRAMES) and not re.fullmatch(
                r"date:\d{4}-\d\d-\d\d", timeframe or ""
            ):
                raise ValidationError("Unknown timeframe.")
            scope["timeframe"] = timeframe or None
            snap = snapshot_for(s, current_pricing_version(s, wf.tenant_id, self.clock.now()))
            new_items: list[dict[str, Any]] = []
            seen: set[str] = set()
            for sku, qty_text in lines:
                if not sku:
                    continue
                if sku in seen:
                    raise ValidationError(f"{sku} is listed twice; combine the quantities.")
                seen.add(sku)
                if sku not in snap.entries:
                    raise ValidationError(f"{sku} is not in the approved price list.")
                qty = to_decimal(qty_text, field=f"quantity for {sku}")
                problem = quantity_problem(snap.entries[sku], qty)
                if problem:
                    raise ValidationError(problem)
                old = next((i for i in scope.get("items", []) if i["sku"] == sku), None)
                new_items.append(
                    {
                        "sku": sku,
                        "quantity": str(qty),
                        "quantity_text": old.get("quantity_text") if old else None,
                        "evidence": old.get("evidence", "") if old else "",
                        "source": "operator"
                        if not old or old["quantity"] != str(qty) or old.get("removed")
                        else old["source"],
                        "removed": False,
                    }
                )
            if not new_items:
                raise ValidationError("A quote needs at least one catalog item.")
            scope["items"] = new_items
            old_qv = self.current_quote_version(s, wf)
            wf.scope = scope
            qv = self._build_quote_version(
                s, wf, actor, f"Edited by {actor.display_name}" + (f": {reason}" if reason else ".")
            )
            if old_qv is not None and qv.id == old_qv.id:
                raise ValidationError("Nothing changed.")
            if state == State.AWAITING_APPROVAL:
                self._void_actions(s, wf, "Quote edited while awaiting approval.")
                self._transition(
                    s,
                    wf,
                    State.DRAFT_READY,
                    actor,
                    "Material details changed; the approval request was withdrawn "
                    "and must be resubmitted.",
                )
            elif state == State.APPROVED:
                self._ensure_nothing_started(s, wf)
                for apv in s.scalars(
                    select(Approval).where(
                        Approval.workflow_id == wf.id,
                        Approval.decision == "approved",
                        Approval.invalidated_at.is_(None),
                    )
                ):
                    apv.invalidated_at = self.clock.now()
                    apv.invalidated_reason = "Material details changed after approval."
                self._void_actions(s, wf, "Quote edited after approval.")
                self._audit(
                    s,
                    wf,
                    actor,
                    "approval_invalidated",
                    "The approval no longer applies because material details changed.",
                )
                self._transition(
                    s,
                    wf,
                    State.DRAFT_READY,
                    actor,
                    "Material details changed after approval; approval voided.",
                )
            else:
                wf.state_version += 1

        self._run(actor_id, run, workflow_id)

    def _ensure_nothing_started(self, s: Session, wf: WorkflowInstance) -> None:
        actions = s.scalars(
            select(ProposedAction).where(
                ProposedAction.workflow_id == wf.id, ProposedAction.status.not_in(["voided"])
            )
        ).all()
        for a in actions:
            if a.status != "queued":
                raise Conflict(
                    "Execution has already started; edits are no longer possible. "
                    "Cancel or escalate instead."
                )
            entry = s.scalars(
                select(OutboxEntry).where(
                    OutboxEntry.action_id == a.id, OutboxEntry.done.is_(False)
                )
            ).first()
            if entry is not None and (entry.claimed_by or entry.attempt_count):
                raise Conflict("Execution has already started; edits are no longer possible.")

    def _void_actions(self, s: Session, wf: WorkflowInstance, reason: str) -> list[ProposedAction]:
        voided = []
        for a in s.scalars(
            select(ProposedAction).where(
                ProposedAction.workflow_id == wf.id,
                ProposedAction.status.in_(["proposed", "queued"]),
            )
        ):
            a.status = "voided"
            a.updated_at = self.clock.now()
            for entry in s.scalars(
                select(OutboxEntry).where(
                    OutboxEntry.action_id == a.id, OutboxEntry.done.is_(False)
                )
            ):
                entry.done = True
            voided.append(a)
        if voided:
            self._audit(
                s,
                wf,
                None,
                "actions_voided",
                f"{len(voided)} proposed action(s) voided before running: {reason}",
                {"action_ids": [a.id for a in voided]},
                actor_type="system",
            )
        return voided

    # ------------------------------------------------------------------ approval

    def _subject(
        self, s: Session, wf: WorkflowInstance
    ) -> tuple[QuoteVersion, list[ProposedAction], str]:
        qv = self.current_quote_version(s, wf)
        if qv is None:
            raise ValidationError("There is no quote to approve.")
        actions = list(
            s.scalars(
                select(ProposedAction)
                .where(
                    ProposedAction.workflow_id == wf.id,
                    ProposedAction.quote_version_id == qv.id,
                    ProposedAction.status != "voided",
                )
                .order_by(ProposedAction.sequence)
            ).all()
        )
        subject_hash = sha256_hex(
            {
                "quote_version_id": qv.id,
                "content_hash": qv.content_hash,
                "actions": [{"kind": a.kind, "payload_hash": a.payload_hash} for a in actions],
            }
        )
        return qv, actions, subject_hash

    def approval_subject_hash(self, actor_id: str, workflow_id: str) -> str:
        with self.sf.begin() as s:
            actor = load_actor(s, actor_id)
            require(actor, Permission.VIEW)
            return self._subject(s, self._wf(s, actor, workflow_id))[2]

    def submit_for_approval(
        self, actor_id: str, workflow_id: str, expected_version: int | None = None
    ) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.SUBMIT_FOR_APPROVAL)
            wf = self._wf(s, actor, workflow_id)
            self._check_version(wf, expected_version)
            check_transition(wf.state, State.AWAITING_APPROVAL)
            qv = self.current_quote_version(s, wf)
            if qv is None:
                raise ValidationError("There is no quote to submit.")
            customer = get_scoped(s, Customer, qv.customer_id, wf.tenant_id)
            site = get_scoped(s, CustomerSite, qv.site_id, wf.tenant_id)
            tenant = self._tenant(s, wf.tenant_id)
            slots, slot_note = propose_slots(
                self.clock.now(), qv.schedule_timeframe, tenant.timezone
            )
            lines_text = "\n".join(
                f"  {ln['description']}: {ln['calculation']}" for ln in qv.calculation["lines"]
            )
            payloads = [
                (
                    "send_quote",
                    "sim_email",
                    {
                        "to": qv.recipient_email,
                        "subject": f"Quote {qv.version_no} from {tenant.name} for {customer.name}",
                        "body": (
                            f"Hello {customer.name},\n\nHere is our quote for work at "
                            f"{site.label}, {site.address}:\n{lines_text}\n"
                            f"Total: {format_usd(qv.total)} {qv.currency} "
                            f"(tax not included).\n\n{tenant.name}"
                        ),
                        "quote_version_id": qv.id,
                        "quote_content_hash": qv.content_hash,
                        "total": str(qv.total),
                    },
                ),
                (
                    "propose_schedule",
                    "sim_calendar",
                    {
                        "customer": customer.name,
                        "site": f"{site.label}, {site.address}",
                        "slots": slots,
                        "basis": slot_note,
                        "availability_checked": False,
                        "quote_version_id": qv.id,
                    },
                ),
            ]
            now = self.clock.now()
            for seq, (kind, adapter, payload) in enumerate(payloads, start=1):
                s.add(
                    ProposedAction(
                        id=new_id("action"),
                        tenant_id=wf.tenant_id,
                        workflow_id=wf.id,
                        quote_version_id=qv.id,
                        sequence=seq,
                        kind=kind,
                        adapter=adapter,
                        payload=payload,
                        payload_hash=sha256_hex(payload),
                        status="proposed",
                        idempotency_key=f"{wf.id}:{kind}:{qv.id}",
                        created_at=now,
                        updated_at=now,
                    )
                )
            s.flush()
            wf.submitted_by = actor.id
            self._transition(
                s,
                wf,
                State.AWAITING_APPROVAL,
                actor,
                f"Quote version {qv.version_no} and 2 simulated actions submitted for approval.",
            )

        self._run(actor_id, run, workflow_id)

    def _check_not_own_work(self, actor: User, wf: WorkflowInstance, qv: QuoteVersion) -> None:
        if actor.id in (qv.prepared_by, wf.submitted_by):
            raise PermissionDenied(
                "You prepared or submitted this version, so you cannot "
                "decide on it. Another approver must."
            )

    def approve(self, actor_id: str, workflow_id: str, subject_hash: str, note: str = "") -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.APPROVE)
            wf = self._wf(s, actor, workflow_id)
            check_transition(wf.state, State.APPROVED)
            qv, actions, current_hash = self._subject(s, wf)
            if subject_hash != current_hash:
                raise StaleApproval(
                    "The quote or actions changed since you opened them. "
                    "Review the current version before approving."
                )
            self._check_not_own_work(actor, wf, qv)
            now = self.clock.now()
            s.add(
                Approval(
                    id=new_id("approval"),
                    tenant_id=wf.tenant_id,
                    workflow_id=wf.id,
                    quote_version_id=qv.id,
                    subject_hash=current_hash,
                    decision="approved",
                    approver_id=actor.id,
                    decided_at=now,
                    reason=note.strip()[:1000],
                )
            )
            for a in actions:
                a.status = "queued"
                a.updated_at = now
                s.add(
                    OutboxEntry(
                        id=new_id("outbox"),
                        tenant_id=wf.tenant_id,
                        action_id=a.id,
                        available_at=now,
                    )
                )
            self._audit(
                s,
                wf,
                actor,
                "quote_approved",
                f"{actor.display_name} approved quote version {qv.version_no} "
                f"({format_usd(qv.total)}) and its {len(actions)} simulated actions.",
                {"quote_version_id": qv.id, "subject_hash": current_hash},
            )
            self._transition(
                s,
                wf,
                State.APPROVED,
                actor,
                "Approved; simulated actions queued for the dispatcher.",
            )

        self._run(actor_id, run, workflow_id)

    def reject(self, actor_id: str, workflow_id: str, subject_hash: str, reason: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.APPROVE)
            wf = self._wf(s, actor, workflow_id)
            check_transition(wf.state, State.REJECTED)
            if not reason.strip():
                raise ValidationError("Give a reason so the preparer knows what to change.")
            qv, _actions, current_hash = self._subject(s, wf)
            if subject_hash != current_hash:
                raise StaleApproval("The quote changed since you opened it. Review it again.")
            self._check_not_own_work(actor, wf, qv)
            s.add(
                Approval(
                    id=new_id("approval"),
                    tenant_id=wf.tenant_id,
                    workflow_id=wf.id,
                    quote_version_id=qv.id,
                    subject_hash=current_hash,
                    decision="rejected",
                    approver_id=actor.id,
                    decided_at=self.clock.now(),
                    reason=reason.strip()[:1000],
                )
            )
            self._void_actions(s, wf, "Quote rejected.")
            self._transition(s, wf, State.REJECTED, actor, f"Rejected: {reason.strip()}")

        self._run(actor_id, run, workflow_id)

    def revise(self, actor_id: str, workflow_id: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.PREPARE_QUOTE)
            wf = self._wf(s, actor, workflow_id)
            if State(wf.state) != State.REJECTED:
                raise InvalidTransition("Only a rejected quote can be reopened for revision.")
            self._transition(s, wf, State.DRAFT_READY, actor, "Reopened for revision.")

        self._run(actor_id, run, workflow_id)

    # ------------------------------------------------------------------ cancel & exceptions

    def cancel(self, actor_id: str, workflow_id: str, reason: str) -> CancelSummary:
        def run(s: Session, actor: User) -> CancelSummary:
            wf = self._wf(s, actor, workflow_id)
            state = State(wf.state)
            require(
                actor,
                Permission.CANCEL_BEFORE_APPROVAL
                if state in PRE_EXECUTION
                else Permission.CANCEL_ANY,
            )
            check_transition(state, State.CANCELED)
            if not reason.strip():
                raise ValidationError("Give a reason for canceling.")
            actions = s.scalars(
                select(ProposedAction).where(ProposedAction.workflow_id == wf.id)
            ).all()
            happened = [ACTION_LABELS[a.kind] for a in actions if a.status == "succeeded"]
            unknown = [
                ACTION_LABELS[a.kind]
                for a in actions
                if a.status in ("uncertain", "escalated", "in_progress")
            ]
            failed = [ACTION_LABELS[a.kind] for a in actions if a.status == "failed"]
            voided = [
                ACTION_LABELS[a.kind] for a in self._void_actions(s, wf, "Workflow canceled.")
            ]
            summary = CancelSummary(voided, happened, unknown, failed)
            parts = [f"Canceled: {reason.strip()}."]
            if voided:
                parts.append(f"Not run (voided): {len(voided)}.")
            if happened:
                parts.append(f"Already happened and NOT undone: {', '.join(happened)}.")
            if unknown:
                parts.append(f"Outcome unknown and NOT undone: {', '.join(unknown)}.")
            self._audit(
                s,
                wf,
                actor,
                "workflow_canceled",
                " ".join(parts),
                {"voided": voided, "already_happened": happened, "unknown": unknown},
            )
            for exc in self._open_exceptions(s, wf):
                exc.resolved_by = actor.id
                exc.resolved_at = self.clock.now()
                exc.resolution = "Workflow canceled."
            self._transition(s, wf, State.CANCELED, actor, reason.strip())
            return summary

        return self._run(actor_id, run, workflow_id)

    def _open_exception(
        self,
        s: Session,
        wf: WorkflowInstance,
        action_id: str | None,
        kind: str,
        detail: str,
        severity: str,
    ) -> None:
        s.add(
            ExceptionRecord(
                id=new_id("exception"),
                tenant_id=wf.tenant_id,
                workflow_id=wf.id,
                action_id=action_id,
                kind=kind,
                detail=detail,
                severity=severity,
                opened_at=self.clock.now(),
            )
        )
        self._audit(s, wf, None, "exception_opened", detail, {"kind": kind}, actor_type="system")

    def _open_exceptions(self, s: Session, wf: WorkflowInstance) -> list[ExceptionRecord]:
        return list(
            s.scalars(
                select(ExceptionRecord).where(
                    ExceptionRecord.workflow_id == wf.id, ExceptionRecord.resolved_at.is_(None)
                )
            ).all()
        )

    def resolve_escalation(
        self, actor_id: str, workflow_id: str, resolution: str, note: str
    ) -> None:
        """Resolutions:

        - ``return_to_clarification``: nothing has executed; reopen the questions.
        - ``confirmed_done``: a person confirmed the uncertain action happened.
        - ``confirmed_not_done_retry``: a person confirmed it did not happen; run it again.
        """

        def run(s: Session, actor: User) -> None:
            require(actor, Permission.RESOLVE_ESCALATION)
            wf = self._wf(s, actor, workflow_id)
            if State(wf.state) != State.ESCALATED:
                raise InvalidTransition("Only escalated workflows can be resolved here.")
            if not note.strip():
                raise ValidationError("Record what you checked or decided.")
            actions = s.scalars(
                select(ProposedAction).where(
                    ProposedAction.workflow_id == wf.id, ProposedAction.status != "voided"
                )
            ).all()
            stuck = [a for a in actions if a.status in ("uncertain", "escalated")]
            now = self.clock.now()
            if resolution == "return_to_clarification":
                if any(a.status not in ("proposed", "queued") for a in actions):
                    raise Conflict(
                        "Actions have already run; this cannot go back to clarification."
                    )
                scope = dict(wf.scope)
                scope["unsupported"] = [
                    {**u, "status": "open" if u["status"] == "escalated" else u["status"]}
                    for u in scope.get("unsupported", [])
                ]
                wf.scope = scope
                self._void_actions(s, wf, "Returned to clarification.")
                self._transition(s, wf, State.NEEDS_CLARIFICATION, actor, note.strip())
                self._ensure_questions(s, wf)
                self._advance(s, wf, actor)
            elif resolution == "confirmed_done":
                if not stuck:
                    raise ValidationError("No action has an uncertain outcome.")
                for a in stuck:
                    a.status = "succeeded"
                    a.updated_at = now
                    op = s.scalars(
                        select(ExternalOperation).where(ExternalOperation.action_id == a.id)
                    ).first()
                    if op is None:
                        s.add(
                            ExternalOperation(
                                id=new_id("external_operation"),
                                tenant_id=wf.tenant_id,
                                action_id=a.id,
                                adapter=a.adapter,
                                idempotency_key=a.idempotency_key,
                                external_ref="confirmed-by-person",
                                status="confirmed_by_person",
                                last_reconciled_at=now,
                            )
                        )
                remaining = [a for a in actions if a.status == "queued"]
                if remaining:
                    self._transition(
                        s,
                        wf,
                        State.EXECUTING,
                        actor,
                        f"Confirmed done by {actor.display_name}: {note.strip()}",
                    )
                else:
                    self._transition(
                        s,
                        wf,
                        State.COMPLETED,
                        actor,
                        f"Confirmed done by {actor.display_name}: {note.strip()}",
                    )
            elif resolution == "confirmed_not_done_retry":
                if not stuck:
                    raise ValidationError("No action has an uncertain outcome.")
                for a in stuck:
                    a.status = "queued"
                    a.updated_at = now
                    s.add(
                        OutboxEntry(
                            id=new_id("outbox"),
                            tenant_id=wf.tenant_id,
                            action_id=a.id,
                            available_at=now,
                        )
                    )
                self._transition(
                    s,
                    wf,
                    State.EXECUTING,
                    actor,
                    f"Confirmed not done by {actor.display_name}; retrying with the "
                    f"same idempotency key: {note.strip()}",
                )
            else:
                raise ValidationError("Unknown resolution.")
            for exc in self._open_exceptions(s, wf):
                exc.resolved_by = actor.id
                exc.resolved_at = now
                exc.resolution = f"{resolution}: {note.strip()}"

        self._run(actor_id, run, workflow_id)

    def retry_failed(self, actor_id: str, workflow_id: str, note: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.RETRY_EXECUTION)
            wf = self._wf(s, actor, workflow_id)
            check_transition(wf.state, State.EXECUTING)
            if State(wf.state) != State.FAILED:
                raise InvalidTransition("Only failed workflows can be retried.")
            failed = s.scalars(
                select(ProposedAction).where(
                    ProposedAction.workflow_id == wf.id, ProposedAction.status == "failed"
                )
            ).all()
            now = self.clock.now()
            for a in failed:
                # Failed means the provider confirmed it did not happen, so re-sending with the
                # same idempotency key is safe.
                a.status = "queued"
                a.updated_at = now
                s.add(
                    OutboxEntry(
                        id=new_id("outbox"),
                        tenant_id=wf.tenant_id,
                        action_id=a.id,
                        available_at=now,
                    )
                )
            for exc in self._open_exceptions(s, wf):
                exc.resolved_by = actor.id
                exc.resolved_at = now
                exc.resolution = f"Retry requested: {note.strip()}"
            self._transition(
                s,
                wf,
                State.EXECUTING,
                actor,
                f"Retry requested by {actor.display_name}. {note.strip()}",
            )

        self._run(actor_id, run, workflow_id)

    # ------------------------------------------------------------------ admin

    def approve_pricing_version(self, actor_id: str, pricing_version_id: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.MANAGE_CATALOG)
            pv = get_scoped(s, PricingVersion, pricing_version_id, actor.tenant_id)
            if pv.status != "draft":
                raise ValidationError("Only draft pricing versions can be approved.")
            now = self.clock.now()
            for old in s.scalars(
                select(PricingVersion).where(
                    PricingVersion.tenant_id == actor.tenant_id, PricingVersion.status == "approved"
                )
            ):
                old.status = "retired"
            pv.status = "approved"
            pv.approved_by = actor.id
            pv.approved_at = now
            pv.effective_from = max(pv.effective_from, now)
            if pv.catalog_changes is not None:
                self._apply_catalog_changes(s, actor, pv)
            self._audit(
                s,
                None,
                actor,
                "pricing_version_approved",
                f"Pricing version {pv.version_no} approved by {actor.display_name}. "
                f"Existing quotes keep the version they were calculated with.",
            )

        self._run(actor_id, run)

    def _apply_catalog_changes(self, s: Session, actor: User, pv: PricingVersion) -> None:
        """Bring the service list in line with an imported version as it is approved."""
        changes = {c["sku"]: c for c in pv.catalog_changes or []}
        items = s.scalars(select(CatalogItem).where(CatalogItem.tenant_id == actor.tenant_id))
        removed: list[str] = []
        for item in items:
            change = changes.get(item.sku)
            if change is None:
                if item.active:
                    item.active = False
                    removed.append(item.sku)
                continue
            item.name = change["name"]
            item.unit = change["unit"]
            item.description = change["description"]
            item.keywords = list(change["keywords"])
            item.quantity_step = Decimal(change["quantity_step"])
            item.onsite = bool(change["onsite"])
            item.active = True
        if removed:
            self._audit(
                s,
                None,
                actor,
                "catalog_services_removed",
                f"Services no longer offered after pricing version {pv.version_no}: "
                f"{', '.join(sorted(removed))}.",
                {"skus": sorted(removed)},
            )

    def import_catalog(self, actor_id: str, data: bytes, filename: str) -> int:
        """Save a CSV price list as a draft pricing version. Returns its version number."""

        def run(s: Session, actor: User) -> int:
            require(actor, Permission.MANAGE_CATALOG)
            parsed = parse_catalog_csv(data)
            if parsed.errors:
                raise CatalogImportError(parsed.errors)
            pending = s.scalars(
                select(PricingVersion).where(
                    PricingVersion.tenant_id == actor.tenant_id, PricingVersion.status == "draft"
                )
            ).first()
            if pending is not None:
                raise ValidationError(
                    f"Pricing version {pending.version_no} is still a draft. Approve or "
                    f"discard it before importing another file."
                )
            now = self.clock.now()
            tenant = s.get(Tenant, actor.tenant_id)
            if tenant is None:  # a broken invariant, never a user error
                raise RuntimeError("tenant is missing")
            current = current_pricing_version(s, actor.tenant_id, now)
            existing = {
                i.sku: i
                for i in s.scalars(
                    select(CatalogItem).where(CatalogItem.tenant_id == actor.tenant_id)
                )
            }
            active = {sku for sku, i in existing.items() if i.active}
            number = (
                s.scalar(
                    select(func.max(PricingVersion.version_no)).where(
                        PricingVersion.tenant_id == actor.tenant_id
                    )
                )
                or 0
            ) + 1
            new_skus = [r.sku for r in parsed.rows if r.sku not in active]
            dropped = sorted(active - {r.sku for r in parsed.rows})
            name = re.sub(r"[^\w .()-]", "", filename)[:80] or "a file"
            pv = PricingVersion(
                id=new_id("pricing_version"),
                tenant_id=actor.tenant_id,
                version_no=number,
                currency=tenant.currency,
                status="draft",
                effective_from=now,
                rules=list(current.rules),
                notes=(
                    f"Imported from {name} by {actor.display_name}: {len(parsed.rows)} "
                    f"services ({len(new_skus)} new, {len(dropped)} no longer offered). "
                    f"Rules copied from version {current.version_no}."
                ),
                catalog_changes=[r.catalog_fields() for r in parsed.rows],
            )
            s.add(pv)
            s.flush()
            for r in parsed.rows:
                item = existing.get(r.sku)
                if item is None:
                    # New services stay hidden from readers and quotes until approval.
                    item = CatalogItem(
                        id=new_id("catalog_item"),
                        tenant_id=actor.tenant_id,
                        sku=r.sku,
                        name=r.name,
                        unit=r.unit,
                        description=r.description,
                        keywords=list(r.keywords),
                        quantity_step=r.quantity_step,
                        onsite=r.onsite,
                        active=False,
                    )
                    s.add(item)
                    s.flush()
                s.add(
                    PriceEntryRow(
                        id=new_id("price_entry"),
                        tenant_id=actor.tenant_id,
                        pricing_version_id=pv.id,
                        catalog_item_id=item.id,
                        unit_price=r.unit_price,
                        currency=tenant.currency,
                        min_qty=r.min_qty,
                        max_qty=r.max_qty,
                    )
                )
            self._audit(
                s,
                None,
                actor,
                "catalog_imported",
                f"Price list imported from {name} as draft pricing version {number}: "
                f"{len(parsed.rows)} services, {len(new_skus)} new, {len(dropped)} no longer "
                f"offered. Nothing changes until it is approved.",
                {
                    "pricing_version_id": pv.id,
                    "services": len(parsed.rows),
                    "new": new_skus,
                    "no_longer_offered": dropped,
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            )
            return number

        return self._run(actor_id, run)

    def discard_pricing_version(self, actor_id: str, pricing_version_id: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.MANAGE_CATALOG)
            pv = get_scoped(s, PricingVersion, pricing_version_id, actor.tenant_id)
            if pv.status != "draft":
                raise ValidationError("Only draft pricing versions can be discarded.")
            pv.status = "discarded"
            self._audit(
                s,
                None,
                actor,
                "pricing_version_discarded",
                f"Draft pricing version {pv.version_no} discarded by {actor.display_name}.",
            )

        self._run(actor_id, run)

    def queue_fault(self, actor_id: str, adapter: str, mode: str) -> None:
        from ..adapters.simulated import FAULT_MODES

        def run(s: Session, actor: User) -> None:
            require(actor, Permission.SIMULATION_CONTROLS)
            if adapter not in ("sim_email", "sim_calendar") or mode not in FAULT_MODES:
                raise ValidationError("Unknown adapter or fault mode.")
            s.add(
                SimFault(
                    id=new_id("sim_fault"),
                    tenant_id=actor.tenant_id,
                    adapter=adapter,
                    mode=mode,
                    created_at=self.clock.now(),
                )
            )
            self._audit(
                s,
                None,
                actor,
                "simulation_fault_queued",
                f"Simulation control: next {adapter} call will '{mode}'.",
            )

        self._run(actor_id, run)
