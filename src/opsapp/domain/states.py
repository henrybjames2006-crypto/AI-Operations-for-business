"""Workflow state machine.

Every state change in the application goes through ``check_transition``. A move that is
not listed in ``TRANSITIONS`` raises ``InvalidTransition``; the caller records the refusal
in the audit log.
"""

from __future__ import annotations

from enum import StrEnum

from .errors import InvalidTransition


class State(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    DRAFT_READY = "draft_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    CANCELED = "canceled"


S = State

TRANSITIONS: dict[State, frozenset[State]] = {
    S.RECEIVED: frozenset({S.NEEDS_CLARIFICATION, S.DRAFT_READY, S.CANCELED, S.ESCALATED}),
    S.NEEDS_CLARIFICATION: frozenset({S.DRAFT_READY, S.CANCELED, S.ESCALATED}),
    S.DRAFT_READY: frozenset({S.AWAITING_APPROVAL, S.NEEDS_CLARIFICATION, S.CANCELED}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.DRAFT_READY, S.CANCELED}),
    S.APPROVED: frozenset({S.EXECUTING, S.DRAFT_READY, S.CANCELED}),
    S.EXECUTING: frozenset({S.COMPLETED, S.FAILED, S.ESCALATED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset({S.ESCALATED, S.EXECUTING, S.CANCELED}),
    S.ESCALATED: frozenset(
        {S.NEEDS_CLARIFICATION, S.DRAFT_READY, S.EXECUTING, S.COMPLETED, S.CANCELED}
    ),
    S.REJECTED: frozenset({S.DRAFT_READY, S.CANCELED}),
    S.CANCELED: frozenset(),
}

TERMINAL: frozenset[State] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)

# States in which no approved action can have started yet.
PRE_EXECUTION: frozenset[State] = frozenset(
    {S.RECEIVED, S.NEEDS_CLARIFICATION, S.DRAFT_READY, S.AWAITING_APPROVAL, S.REJECTED}
)

STATUS_TEXT: dict[State, str] = {
    S.RECEIVED: "We have the request and are reading it.",
    S.NEEDS_CLARIFICATION: "We need answers before we can quote.",
    S.DRAFT_READY: "A quote draft is ready for review.",
    S.AWAITING_APPROVAL: "Waiting for a manager to approve this exact quote.",
    S.APPROVED: "Approved. Simulated actions are queued.",
    S.EXECUTING: "Running simulated delivery and scheduling.",
    S.COMPLETED: "Done: quote delivered (simulated) and schedule proposed (simulated).",
    S.FAILED: "An action failed and needs attention.",
    S.ESCALATED: "A person must decide. Nothing further runs automatically.",
    S.REJECTED: "The manager rejected the quote.",
    S.CANCELED: "Canceled. See which actions had already happened.",
}

LABEL: dict[State, str] = {
    S.RECEIVED: "Received",
    S.NEEDS_CLARIFICATION: "Needs clarification",
    S.DRAFT_READY: "Draft ready",
    S.AWAITING_APPROVAL: "Awaiting approval",
    S.APPROVED: "Approved",
    S.EXECUTING: "Executing",
    S.COMPLETED: "Completed",
    S.FAILED: "Failed",
    S.ESCALATED: "Escalated",
    S.REJECTED: "Rejected",
    S.CANCELED: "Canceled",
}


def can_transition(current: State, target: State) -> bool:
    return target in TRANSITIONS[current]


def check_transition(current: State | str, target: State | str) -> State:
    cur, tgt = State(current), State(target)
    if not can_transition(cur, tgt):
        raise InvalidTransition(f"A workflow cannot move from {LABEL[cur]} to {LABEL[tgt]}.")
    return tgt
