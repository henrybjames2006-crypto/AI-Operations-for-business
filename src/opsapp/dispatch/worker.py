"""Outbox dispatcher: executes approved actions through adapters.

Guarantees and non-guarantees:
- An action runs only if it was approved; the approval and its outbox entries are written
  in the same transaction.
- Each action has one idempotency key, reused on every attempt.
- After a timeout or a crash mid-call, the dispatcher asks the provider for the action's
  status before doing anything else. It re-sends only when the provider confirms the
  action did not happen. If the provider cannot tell, the workflow is escalated to a person.
- Clear failures are retried at most ``max_attempts`` times with backoff.
- This is not exactly-once delivery. A provider that neither honors idempotency keys nor
  reports status accurately can still cause a duplicate; such outcomes are escalated, not
  hidden.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..adapters.ports import ActionAdapter, AdapterTimeout, LookupResult, SendResult
from ..clock import Clock
from ..domain.states import LABEL, TERMINAL, State, check_transition
from ..ids import new_id
from ..persistence.models import (
    ExceptionRecord,
    ExecutionAttempt,
    ExternalOperation,
    OutboxEntry,
    ProposedAction,
    WorkflowInstance,
)
from ..workflow import audit

log = logging.getLogger(__name__)

LEASE_SECONDS = 120
KIND_TEXT = {
    "send_quote": "simulated quote email",
    "propose_schedule": "simulated schedule proposal",
}


@dataclass
class _Claim:
    entry_id: str
    action_id: str
    mode: str  # send | reconcile
    attempt_id: str
    adapter: str
    tenant_id: str
    key: str
    payload: dict[str, object]


def call_with_timeout[R](fn: Callable[[], R], timeout_seconds: float) -> R:
    """Run ``fn`` with a deadline. On timeout the call may still complete in the background,
    which is exactly why the caller must reconcile instead of assuming failure."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        raise AdapterTimeout(f"No response within {timeout_seconds:g}s.") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class Dispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        adapters: dict[str, ActionAdapter],
        *,
        max_attempts: int = 3,
        timeout_seconds: float = 10.0,
        worker_id: str = "dispatcher-1",
        backoff_base_seconds: float = 2.0,
    ) -> None:
        self.sf = session_factory
        self.clock = clock
        self.adapters = adapters
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.worker_id = worker_id
        self.backoff_base = backoff_base_seconds

    # ------------------------------------------------------------------ public

    def run_once(self, limit: int = 50) -> int:
        """Process every entry that is due now. Returns the number of steps taken."""
        steps = 0
        while steps < limit:
            claim = self._claim_next()
            if claim is None:
                break
            self._execute(claim)
            steps += 1
        return steps

    def run_forever(self, poll_seconds: float, stop: Callable[[], bool] = lambda: False) -> None:
        log.info("Dispatcher %s started; polling every %ss", self.worker_id, poll_seconds)
        while not stop():
            try:
                done = self.run_once()
                if done:
                    log.info("Dispatcher processed %s step(s)", done)
            except Exception:
                log.exception("Dispatcher loop error; continuing after pause")
            time.sleep(poll_seconds)

    # ------------------------------------------------------------------ claim

    def _audit(
        self,
        s: Session,
        wf: WorkflowInstance,
        event_type: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> None:
        audit.append(
            s,
            tenant_id=wf.tenant_id,
            event_type=event_type,
            message=message,
            at=self.clock.now(),
            actor_type="dispatcher",
            actor_id=None,
            workflow_id=wf.id,
            data=dict(data or {}),
        )

    def _transition(self, s: Session, wf: WorkflowInstance, target: State, reason: str) -> None:
        current = State(wf.state)
        check_transition(current, target)
        wf.state = target.value
        wf.state_version += 1
        wf.updated_at = self.clock.now()
        if target in TERMINAL:
            wf.closed_at = wf.updated_at
        self._audit(
            s,
            wf,
            "state_changed",
            f"{LABEL[current]} → {LABEL[target]}: {reason}",
            {"from": current.value, "to": target.value},
        )

    def _claim_next(self) -> _Claim | None:
        now = self.clock.now()
        with self.sf.begin() as s:
            candidates = s.scalars(
                select(OutboxEntry)
                .where(
                    OutboxEntry.done.is_(False),
                    OutboxEntry.available_at <= now,
                    or_(OutboxEntry.claimed_until.is_(None), OutboxEntry.claimed_until < now),
                )
                .order_by(OutboxEntry.available_at, OutboxEntry.id)
            ).all()
            for entry in candidates:
                action = s.get(ProposedAction, entry.action_id)
                if action is None:  # a broken invariant, never a user error
                    raise RuntimeError("action is missing")
                wf = s.get(WorkflowInstance, action.workflow_id)
                if wf is None:  # a broken invariant, never a user error
                    raise RuntimeError("wf is missing")
                if action.status in ("voided", "succeeded", "failed", "escalated"):
                    entry.done = True
                    continue
                if State(wf.state) not in (State.APPROVED, State.EXECUTING):
                    continue  # paused (escalated/failed elsewhere); a person decides first
                earlier = s.scalars(
                    select(ProposedAction).where(
                        ProposedAction.workflow_id == wf.id,
                        ProposedAction.sequence < action.sequence,
                        ProposedAction.status != "voided",
                    )
                ).all()
                if any(e.status != "succeeded" for e in earlier):
                    continue  # runs after the earlier actions succeed
                if State(wf.state) == State.APPROVED:
                    self._transition(
                        s, wf, State.EXECUTING, "Dispatcher started the approved simulated actions."
                    )
                if action.status == "in_progress":
                    # A previous run crashed mid-call: the outcome is unknown.
                    entry.needs_reconcile = True
                    self._audit(
                        s,
                        wf,
                        "action_outcome_uncertain",
                        f"A previous attempt at the {KIND_TEXT[action.kind]} did not "
                        f"finish; checking with the provider before anything else.",
                    )
                mode = "reconcile" if entry.needs_reconcile else "send"
                entry.claimed_by = self.worker_id
                entry.claimed_until = now + timedelta(seconds=LEASE_SECONDS)
                action.status = "in_progress"
                action.updated_at = now
                attempt_no = (
                    len(
                        s.scalars(
                            select(ExecutionAttempt).where(ExecutionAttempt.action_id == action.id)
                        ).all()
                    )
                    + 1
                )
                attempt = ExecutionAttempt(
                    id=new_id("attempt"),
                    tenant_id=action.tenant_id,
                    action_id=action.id,
                    attempt_no=attempt_no,
                    kind=mode,
                    started_at=now,
                    outcome="started",
                )
                s.add(attempt)
                return _Claim(
                    entry.id,
                    action.id,
                    mode,
                    attempt.id,
                    action.adapter,
                    action.tenant_id,
                    action.idempotency_key,
                    dict(action.payload),
                )
        return None

    # ------------------------------------------------------------------ execute

    def _execute(self, claim: _Claim) -> None:
        adapter = self.adapters[claim.adapter]
        result: SendResult | LookupResult | None = None
        timed_out: str | None = None
        try:
            if claim.mode == "send":
                result = call_with_timeout(
                    lambda: adapter.send(
                        claim.tenant_id, claim.key, claim.payload, self.timeout_seconds
                    ),
                    self.timeout_seconds,
                )
            else:
                result = call_with_timeout(
                    lambda: adapter.lookup(claim.tenant_id, claim.key), self.timeout_seconds
                )
        except AdapterTimeout as exc:
            timed_out = str(exc)
        except Exception as exc:  # noqa: BLE001 - any surprise after sending is "unknown"
            timed_out = f"Unexpected adapter error: {type(exc).__name__}"
            log.warning("Adapter %s raised %s", claim.adapter, type(exc).__name__)
        self._record(claim, result, timed_out)

    def _record(
        self, claim: _Claim, result: SendResult | LookupResult | None, timed_out: str | None
    ) -> None:
        now = self.clock.now()
        with self.sf.begin() as s:
            entry = s.get(OutboxEntry, claim.entry_id)
            action = s.get(ProposedAction, claim.action_id)
            attempt = s.get(ExecutionAttempt, claim.attempt_id)
            if not (entry and action and attempt):
                raise RuntimeError("outbox entry, action or attempt is missing")
            wf = s.get(WorkflowInstance, action.workflow_id)
            if wf is None:  # a broken invariant, never a user error
                raise RuntimeError("wf is missing")
            attempt.finished_at = now
            entry.claimed_by = None
            entry.claimed_until = None
            what = KIND_TEXT[action.kind]

            if timed_out is not None:
                attempt.outcome = "timeout"
                attempt.error = timed_out
                if claim.mode == "send":
                    entry.attempt_count += 1
                entry.needs_reconcile = True
                action.status = "uncertain"
                entry.available_at = now
                self._audit(
                    s,
                    wf,
                    "action_outcome_uncertain",
                    f"No answer for the {what} ({timed_out}). It may or may not have "
                    f"happened; checking status before any retry.",
                    {"action_id": action.id, "attempt": attempt.attempt_no},
                )
                if claim.mode == "reconcile":
                    failed_checks = len(
                        s.scalars(
                            select(ExecutionAttempt).where(
                                ExecutionAttempt.action_id == action.id,
                                ExecutionAttempt.kind == "reconcile",
                                ExecutionAttempt.outcome == "timeout",
                            )
                        ).all()
                    )
                    if failed_checks >= self.max_attempts:
                        self._escalate(
                            s,
                            wf,
                            action,
                            entry,
                            f"Could not confirm whether the {what} happened after "
                            f"{failed_checks} status checks. A person must check.",
                        )
                    else:
                        entry.available_at = now + timedelta(
                            seconds=self.backoff_base * (2 ** (failed_checks - 1))
                        )
                return

            if isinstance(result, SendResult):
                if result.status == "succeeded":
                    attempt.outcome = "succeeded"
                    attempt.external_ref = result.external_ref
                    self._succeed(s, wf, action, entry, result.external_ref or "", None)
                    return
                attempt.outcome = "failed"
                attempt.error = result.error
                entry.attempt_count += 1
                if result.retryable and entry.attempt_count < self.max_attempts:
                    delay = self.backoff_base * (2 ** (entry.attempt_count - 1))
                    entry.available_at = now + timedelta(seconds=delay)
                    action.status = "queued"
                    self._audit(
                        s,
                        wf,
                        "action_failed_will_retry",
                        f"The {what} failed ({result.error}). Attempt "
                        f"{entry.attempt_count} of {self.max_attempts}; retrying in "
                        f"{delay:g}s with the same idempotency key.",
                        {"action_id": action.id},
                    )
                else:
                    self._fail(
                        s,
                        wf,
                        action,
                        entry,
                        f"The {what} failed after {entry.attempt_count} attempt(s): {result.error}",
                    )
                return

            if not isinstance(result, LookupResult):
                raise RuntimeError("lookup returned an unexpected result")
            attempt.outcome = result.status
            if result.status == "found":
                attempt.external_ref = result.external_ref
                self._succeed(
                    s,
                    wf,
                    action,
                    entry,
                    result.external_ref or "",
                    now,
                    note="Status check found it had already happened; not sent again.",
                )
            elif result.status == "not_found":
                entry.needs_reconcile = False
                if entry.attempt_count < self.max_attempts:
                    action.status = "queued"
                    entry.available_at = now
                    self._audit(
                        s,
                        wf,
                        "action_reconciled_not_found",
                        f"Status check confirms the {what} did not happen; it will be "
                        f"sent again with the same idempotency key.",
                        {"action_id": action.id},
                    )
                else:
                    self._fail(
                        s,
                        wf,
                        action,
                        entry,
                        f"The {what} did not happen after {entry.attempt_count} attempts.",
                    )
            else:
                self._escalate(
                    s,
                    wf,
                    action,
                    entry,
                    f"The provider cannot say whether the {what} happened. It was not "
                    f"retried, to avoid a duplicate. A person must check.",
                )

    def _succeed(
        self,
        s: Session,
        wf: WorkflowInstance,
        action: ProposedAction,
        entry: OutboxEntry,
        ref: str,
        reconciled_at: object | None,
        note: str = "",
    ) -> None:
        now = self.clock.now()
        action.status = "succeeded"
        action.updated_at = now
        entry.done = True
        if (
            s.scalars(
                select(ExternalOperation).where(ExternalOperation.action_id == action.id)
            ).first()
            is None
        ):
            s.add(
                ExternalOperation(
                    id=new_id("external_operation"),
                    tenant_id=wf.tenant_id,
                    action_id=action.id,
                    adapter=action.adapter,
                    idempotency_key=action.idempotency_key,
                    external_ref=ref,
                    status="succeeded",
                    last_reconciled_at=now if reconciled_at else None,
                )
            )
        self._audit(
            s,
            wf,
            "action_succeeded",
            f"The {KIND_TEXT[action.kind]} completed (simulator reference {ref}). {note}".strip(),
            {"action_id": action.id, "external_ref": ref},
        )
        remaining = s.scalars(
            select(ProposedAction).where(
                ProposedAction.workflow_id == wf.id,
                ProposedAction.status.not_in(["voided", "succeeded"]),
            )
        ).all()
        if not remaining and State(wf.state) == State.EXECUTING:
            self._transition(s, wf, State.COMPLETED, "All approved simulated actions succeeded.")

    def _fail(
        self,
        s: Session,
        wf: WorkflowInstance,
        action: ProposedAction,
        entry: OutboxEntry,
        detail: str,
    ) -> None:
        action.status = "failed"
        action.updated_at = self.clock.now()
        entry.done = True
        s.add(
            ExceptionRecord(
                id=new_id("exception"),
                tenant_id=wf.tenant_id,
                workflow_id=wf.id,
                action_id=action.id,
                kind="action_failed",
                detail=detail,
                severity="medium",
                opened_at=self.clock.now(),
            )
        )
        self._audit(s, wf, "exception_opened", detail, {"kind": "action_failed"})
        if State(wf.state) == State.EXECUTING:
            self._transition(s, wf, State.FAILED, detail)

    def _escalate(
        self,
        s: Session,
        wf: WorkflowInstance,
        action: ProposedAction,
        entry: OutboxEntry,
        detail: str,
    ) -> None:
        action.status = "escalated"
        action.updated_at = self.clock.now()
        entry.done = True
        s.add(
            ExceptionRecord(
                id=new_id("exception"),
                tenant_id=wf.tenant_id,
                workflow_id=wf.id,
                action_id=action.id,
                kind="uncertain_outcome",
                detail=detail,
                severity="high",
                opened_at=self.clock.now(),
            )
        )
        self._audit(s, wf, "exception_opened", detail, {"kind": "uncertain_outcome"})
        if State(wf.state) == State.EXECUTING:
            self._transition(s, wf, State.ESCALATED, detail)
