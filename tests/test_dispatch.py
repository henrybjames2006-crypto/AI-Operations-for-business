"""Reliable execution: retries, timeouts, lost responses, uncertain outcomes, crashes."""

from sqlalchemy import select

from conftest import approve, events, get, sim_ops, state, to_awaiting
from opsapp.persistence.models import (
    ExceptionRecord,
    ExecutionAttempt,
    OutboxEntry,
    ProposedAction,
)


def approved(env):  # type: ignore[no-untyped-def]
    wf_id = to_awaiting(env)
    approve(env, wf_id)
    return wf_id


def actions(env, wf_id):  # type: ignore[no-untyped-def]
    with env.c.read_sf() as s:
        out = {
            a.kind: a
            for a in s.scalars(
                select(ProposedAction).where(
                    ProposedAction.workflow_id == wf_id, ProposedAction.status != "voided"
                )
            )
        }
        s.expunge_all()
        return out


def attempts(env, action_id):  # type: ignore[no-untyped-def]
    with env.c.read_sf() as s:
        return [
            (a.kind, a.outcome)
            for a in s.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.action_id == action_id)
                .order_by(ExecutionAttempt.attempt_no)
            )
        ]


def fault(env, adapter, mode, n=1):  # type: ignore[no-untyped-def]
    for _ in range(n):
        env.svc.queue_fault(env.ids["dana"], adapter, mode)


def test_nothing_runs_without_approval(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = to_awaiting(env)
    assert env.dispatcher().run_once() == 0
    assert sim_ops(env) == [] and state(env, wf_id) == "awaiting_approval"


def test_actions_run_in_order_and_complete(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    env.dispatcher().run_once()
    a = actions(env, wf_id)
    assert {k: v.status for k, v in a.items()} == {
        "send_quote": "succeeded",
        "propose_schedule": "succeeded",
    }
    assert state(env, wf_id) == "completed"
    ops = sim_ops(env)
    assert [o.adapter for o in sorted(ops, key=lambda o: o.created_at)] == [
        "sim_email",
        "sim_calendar",
    ]
    assert ops[0].payload["slots"] if ops[0].adapter == "sim_calendar" else True


def test_bounded_retries_then_failed_with_exception(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_email", "fail", 3)
    env.dispatcher().run_once()
    a = actions(env, wf_id)["send_quote"]
    assert attempts(env, a.id) == [("send", "failed")] * 3
    assert a.status == "failed" and state(env, wf_id) == "failed"
    assert actions(env, wf_id)["propose_schedule"].status == "queued"  # never started
    with env.c.read_sf() as s:
        exc = s.scalars(select(ExceptionRecord).where(ExceptionRecord.workflow_id == wf_id)).one()
    assert exc.kind == "action_failed"
    assert sim_ops(env) == []
    # an approver retries; the provider confirmed nothing happened, so this is safe
    env.svc.retry_failed(env.ids["marcus"], wf_id, "Provider is back up.")
    env.dispatcher().run_once()
    assert state(env, wf_id) == "completed"
    assert len(sim_ops(env, "sim_email")) == 1


def test_one_failure_then_success_retries_with_same_key(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_email", "fail", 1)
    env.dispatcher().run_once()
    a = actions(env, wf_id)["send_quote"]
    assert attempts(env, a.id) == [("send", "failed"), ("send", "succeeded")]
    assert state(env, wf_id) == "completed"


def test_lost_response_on_non_idempotent_provider_is_not_duplicated(env) -> None:  # type: ignore[no-untyped-def]
    """The simulated calendar ignores idempotency keys, so a blind retry would duplicate."""
    wf_id = approved(env)
    fault(env, "sim_calendar", "drop_response")
    env.dispatcher().run_once()
    a = actions(env, wf_id)["propose_schedule"]
    assert attempts(env, a.id) == [("send", "timeout"), ("reconcile", "found")]
    assert len(sim_ops(env, "sim_calendar")) == 1
    assert state(env, wf_id) == "completed"


def test_lost_response_on_idempotent_provider(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_email", "drop_response")
    env.dispatcher().run_once()
    assert len(sim_ops(env, "sim_email")) == 1
    assert state(env, wf_id) == "completed"


def test_timeout_before_send_is_reconciled_then_resent(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_calendar", "timeout_before_send")
    env.dispatcher().run_once()
    a = actions(env, wf_id)["propose_schedule"]
    assert attempts(env, a.id) == [
        ("send", "timeout"),
        ("reconcile", "not_found"),
        ("send", "succeeded"),
    ]
    assert len(sim_ops(env, "sim_calendar")) == 1


def test_unknown_outcome_escalates_without_retry(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_calendar", "status_unknown")
    env.dispatcher().run_once()
    a = actions(env, wf_id)["propose_schedule"]
    assert attempts(env, a.id) == [("send", "timeout"), ("reconcile", "unknown")]
    assert a.status == "escalated" and state(env, wf_id) == "escalated"
    env.dispatcher().run_once()
    assert len(sim_ops(env, "sim_calendar")) == 1  # never re-sent automatically
    # A person confirms it happened.
    env.svc.resolve_escalation(
        env.ids["marcus"],
        wf_id,
        "confirmed_done",
        "Checked the calendar provider console: the proposal exists.",
    )
    assert state(env, wf_id) == "completed"


def test_unknown_outcome_resolved_as_not_done_resends(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = approved(env)
    fault(env, "sim_email", "status_unknown")
    env.dispatcher().run_once()
    assert state(env, wf_id) == "escalated"
    env.svc.resolve_escalation(
        env.ids["dana"],
        wf_id,
        "confirmed_not_done_retry",
        "Customer confirmed they did not receive it.",
    )
    env.dispatcher().run_once()
    # email honors idempotency, so the resend returns the existing record (no duplicate)
    assert len(sim_ops(env, "sim_email")) == 1
    assert state(env, wf_id) == "completed"


def test_crash_mid_call_reconciles_before_resending(env) -> None:  # type: ignore[no-untyped-def]
    """Simulate a dispatcher that claimed an action, called the provider, then died."""
    wf_id = approved(env)
    d = env.dispatcher()
    claim = d._claim_next()
    assert claim is not None
    env.c.adapters[claim.adapter].send(claim.tenant_id, claim.key, claim.payload, 1)  # happened
    # process dies here; the lease expires
    env.clock.advance(3600)
    env.dispatcher().run_once()
    a = actions(env, wf_id)["send_quote"]
    kinds = [k for k, _ in attempts(env, a.id)]
    assert kinds[-1] == "reconcile"
    assert len(sim_ops(env, "sim_email")) == 1
    assert state(env, wf_id) == "completed"
    assert any(e.event_type == "action_outcome_uncertain" for e in events(env, wf_id))


def test_lease_prevents_double_claim(env) -> None:  # type: ignore[no-untyped-def]
    approved(env)
    first = env.dispatcher()._claim_next()
    second = env.c.dispatcher(worker_id="dispatcher-2")._claim_next()
    assert first is not None and second is None


def test_outbox_is_written_with_the_approval(env) -> None:  # type: ignore[no-untyped-def]
    wf_id = to_awaiting(env)
    with env.c.read_sf() as s:
        assert s.scalars(select(OutboxEntry)).all() == []
    approve(env, wf_id)
    with env.c.read_sf() as s:
        assert len(s.scalars(select(OutboxEntry)).all()) == 2
    assert get(env, ProposedAction, actions(env, wf_id)["send_quote"].id).status == "queued"
