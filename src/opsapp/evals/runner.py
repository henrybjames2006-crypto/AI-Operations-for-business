"""Run the synthetic evaluation set and write a Markdown + JSON report.

Every case runs in a fresh in-memory database with the fictional seed data and a fixed
clock, so results are repeatable. Scores describe this software on these invented cases
only; they say nothing about real customers, time saved or willingness to pay.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..clock import FixedClock
from ..config import Settings
from ..container import Container, build
from ..domain.errors import DomainError, NotFound, PermissionDenied, StaleApproval
from ..persistence.models import (
    AIUsage,
    Approval,
    Base,
    ClarificationQuestion,
    SimOperation,
    WorkflowInstance,
)
from ..seed import seed
from .cases import CASES, Case

START = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
BASELINE = "mock/deterministic-rules-v1"


class Run:
    def __init__(self) -> None:
        self.clock = FixedClock(START)
        self.c: Container = build(
            Settings(database_path=Path("unused"), session_secret="eval-only-secret"),
            self.clock,
            memory=True,
        )
        Base.metadata.create_all(self.c.engine)
        with self.c.sf.begin() as s:
            self.ids = seed(s, now=START)
        self.n = 0

    def submit(
        self, case: Case, user: str = "priya", key: str | None = None, allow_duplicate: bool = False
    ) -> str:
        self.n += 1
        return self.c.service.submit_request(
            self.ids[user],
            case.text,
            case.sender,
            key or f"eval-{self.n}",
            allow_duplicate=allow_duplicate,
        ).workflow_id

    def wf(self, wf_id: str) -> WorkflowInstance:
        with self.c.read_sf() as s:
            w = s.get(WorkflowInstance, wf_id)
            assert w is not None
            s.expunge(w)
            return w

    def questions(self, wf_id: str) -> list[ClarificationQuestion]:
        with self.c.read_sf() as s:
            qs = list(
                s.scalars(
                    select(ClarificationQuestion).where(
                        ClarificationQuestion.workflow_id == wf_id,
                        ClarificationQuestion.answer.is_(None),
                        ClarificationQuestion.superseded.is_(False),
                    )
                )
            )
            s.expunge_all()
            return qs

    def total(self, wf_id: str) -> Decimal | None:
        with self.c.read_sf() as s:
            w = s.get(WorkflowInstance, wf_id)
            qv = self.c.service.current_quote_version(s, w)  # type: ignore[arg-type]
            return qv.total if qv else None

    def answer_demo(self, wf_id: str) -> None:
        for q in self.questions(wf_id):
            if q.kind == "choose_site":
                self.c.service.answer_question(
                    self.ids["priya"], wf_id, q.id, self.ids["site_harbor_elm_street"]
                )
            elif q.kind == "quantity":
                self.c.service.answer_question(self.ids["priya"], wf_id, q.id, "5")

    def to_awaiting(self, case: Case) -> str:
        wf_id = self.submit(case)
        self.answer_demo(wf_id)
        self.c.service.submit_for_approval(self.ids["priya"], wf_id)
        return wf_id

    def approve(self, wf_id: str, user: str = "marcus") -> None:
        h = self.c.service.approval_subject_hash(self.ids[user], wf_id)
        self.c.service.approve(self.ids[user], wf_id, h)

    def ops(self, adapter: str) -> int:
        with self.c.read_sf() as s:
            return len(s.scalars(select(SimOperation).where(SimOperation.adapter == adapter)).all())

    def close(self) -> None:
        self.c.engine.dispose()


def _norm_qty(q: str | None) -> str | None:
    return None if q is None else format(Decimal(q).normalize(), "f")


def score_extraction(r: Run, case: Case) -> dict[str, Any]:
    wf_id = r.submit(case)
    wf = r.wf(wf_id)
    scope = wf.scope
    qs = r.questions(wf_id)
    checks: dict[str, bool] = {}
    if case.customer == "ask":
        checks["customer"] = scope.get("customer_id") is None and any(
            q.kind == "choose_customer" for q in qs
        )
    else:
        checks["customer"] = scope.get("customer_id") == r.ids[f"customer_{case.customer}"]
    got_items = {i["sku"]: _norm_qty(i["quantity"]) for i in scope.get("items", [])}
    want_items = {k: _norm_qty(v) for k, v in case.items.items()}
    checks["items"] = got_items == want_items
    checks["timeframe"] = scope.get("timeframe") == case.timeframe
    checks["instruction_flag"] = bool(scope.get("flags")) == case.flagged
    checks["unsupported"] = len(scope.get("unsupported", [])) == case.unsupported
    detail = {
        "got_items": got_items,
        "want_items": want_items,
        "got_timeframe": scope.get("timeframe"),
        "state": wf.state,
    }
    if case.expect_state:
        checks["state"] = wf.state == case.expect_state
    if case.expect_total:
        total = r.total(wf_id)
        checks["quote_total"] = total == Decimal(case.expect_total)
        detail["total"] = str(total) if total is not None else None
    return {"checks": checks, "detail": detail, "final_state": wf.state}


def _expect_raise(fn: Callable[[], Any], *errors: type[BaseException]) -> bool:
    try:
        fn()
    except errors:
        return True
    except DomainError:
        return False
    return False


def run_scenario(r: Run, case: Case) -> dict[str, Any]:
    p = case.params
    svc = r.c.service
    checks: dict[str, bool] = {}
    blocked = attempted = 0
    if case.scenario == "edit_after_approval":
        wf_id = r.to_awaiting(case)
        r.approve(wf_id)
        kw: dict[str, Any] = {
            "recipient": "office@harbordental.example",
            "site_id": r.ids["site_harbor_elm_street"],
            "timeframe": "next_week",
        }
        lines = [("WS-INSTALL", "5"), ("DATA-MIGR", "5")]
        if p["field"] == "quantity":
            lines = [("WS-INSTALL", "7"), ("DATA-MIGR", "5")]
        elif p["field"] == "recipient":
            kw["recipient"] = "someone-else@harbordental.example"
        else:
            kw["site_id"] = r.ids["site_harbor_bay_road"]
        svc.edit_quote(r.ids["priya"], wf_id, lines=lines, **kw)
        r.c.dispatcher(backoff_base_seconds=0).run_once()
        with r.c.read_sf() as s:
            apv = s.scalars(select(Approval).where(Approval.workflow_id == wf_id)).one()
            checks["approval_voided"] = apv.invalidated_at is not None
        checks["nothing_executed"] = r.ops("sim_email") == 0 and r.ops("sim_calendar") == 0
        checks["back_to_draft"] = r.wf(wf_id).state == "draft_ready"
    elif case.scenario == "stale_approval":
        wf_id = r.to_awaiting(case)
        old = svc.approval_subject_hash(r.ids["marcus"], wf_id)
        svc.edit_quote(
            r.ids["priya"],
            wf_id,
            lines=[("WS-INSTALL", "4"), ("DATA-MIGR", "4")],
            recipient="office@harbordental.example",
            site_id=r.ids["site_harbor_elm_street"],
            timeframe="next_week",
        )
        svc.submit_for_approval(r.ids["priya"], wf_id)
        attempted += 1
        ok = _expect_raise(lambda: svc.approve(r.ids["marcus"], wf_id, old), StaleApproval)
        blocked += ok
        checks["stale_refused"] = ok
    elif case.scenario == "self_approval":
        wf_id = r.to_awaiting(case)
        h = svc.approval_subject_hash(r.ids["priya"], wf_id)
        attempted += 1
        ok = _expect_raise(lambda: svc.approve(r.ids["priya"], wf_id, h), PermissionDenied)
        blocked += ok
        checks["self_approval_refused"] = ok
    elif case.scenario == "wrong_role_approval":
        wf_id = r.to_awaiting(case)
        h = svc.approval_subject_hash(r.ids["marcus"], wf_id)
        attempted += 1
        ok = _expect_raise(lambda: svc.approve(r.ids[p["user"]], wf_id, h), PermissionDenied)
        blocked += ok
        checks["refused"] = ok and r.wf(wf_id).state == "awaiting_approval"
    elif case.scenario == "faults":
        wf_id = r.to_awaiting(case)
        r.approve(wf_id)
        for adapter, mode in p["faults"]:
            svc.queue_fault(r.ids["dana"], adapter, mode)
        r.c.dispatcher(backoff_base_seconds=0).run_once()
        checks["final_state"] = r.wf(wf_id).state == p["state"]
        if "email_ops" in p:
            checks["email_records"] = r.ops("sim_email") == p["email_ops"]
        if "calendar_ops" in p:
            checks["calendar_records"] = r.ops("sim_calendar") == p["calendar_ops"]
    elif case.scenario == "duplicate":
        a = r.submit(case, key="dup-key")
        b = r.submit(case, key="dup-key" if p["same_key"] else "dup-key-2")
        attempted += 1
        blocked += a == b
        checks["one_workflow"] = a == b
    elif case.scenario == "double_approve":
        wf_id = r.to_awaiting(case)
        r.approve(wf_id)
        attempted += 1
        ok = _expect_raise(lambda: r.approve(wf_id), DomainError)
        blocked += ok
        r.c.dispatcher(backoff_base_seconds=0).run_once()
        r.c.dispatcher(backoff_base_seconds=0).run_once()
        checks["second_refused"] = ok
        checks["one_email"] = r.ops("sim_email") == 1
    elif case.scenario == "cross_tenant":
        wf_id = r.to_awaiting(case)
        h = svc.approval_subject_hash(r.ids["marcus"], wf_id)
        user = r.ids["grace"]  # owner of the OTHER tenant: has every permission there
        ops: dict[str, Callable[[], Any]] = {
            "view": lambda: svc.approval_subject_hash(user, wf_id),
            "approve": lambda: svc.approve(user, wf_id, h),
            "cancel": lambda: svc.cancel(user, wf_id, "x"),
            "answer": lambda: svc.answer_question(user, wf_id, "clq_none", "x"),
        }
        attempted += 1
        ok = _expect_raise(ops[p["op"]], NotFound)
        blocked += ok
        checks["not_found"] = ok and r.wf(wf_id).state == "awaiting_approval"
    else:  # pragma: no cover
        raise ValueError(case.scenario)
    return {"checks": checks, "blocked": blocked, "attempted": attempted}


def run_all() -> dict[str, Any]:
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    for case in CASES:
        run = Run()
        try:
            res = run_scenario(run, case) if case.scenario else score_extraction(run, case)
            with run.c.read_sf() as s:
                usage = s.scalars(select(AIUsage)).all()
                res["ai_calls"] = len(usage)
                res["ai_cost_usd"] = str(sum((u.est_cost_usd for u in usage), Decimal("0")))
                states = [w.state for w in s.scalars(select(WorkflowInstance))]
            res["states"] = states
        except Exception as exc:  # noqa: BLE001 - a crash is a recorded failure
            res = {
                "checks": {"ran_without_error": False},
                "error": f"{type(exc).__name__}: {exc}",
                "states": [],
            }
        finally:
            run.close()
        res.update(
            {"id": case.id, "category": case.category, "passed": all(res["checks"].values())}
        )
        results.append(res)

    def ratio(a: int, b: int) -> str:
        return f"{a}/{b} ({a / b:.0%})" if b else "n/a"

    extraction = [
        r
        for r in results
        if r["category"]
        not in (
            "approval_edit",
            "adapter_failure",
            "timeout_uncertain",
            "duplicate",
            "cross_tenant",
        )
    ]
    field_checks = [
        v
        for r in extraction
        for k, v in r["checks"].items()
        if k in ("customer", "items", "timeframe", "instruction_flag", "unsupported")
    ]
    per_field: dict[str, list[bool]] = {}
    for r in extraction:
        for k, v in r["checks"].items():
            per_field.setdefault(k, []).append(v)
    totals = [r["checks"]["quote_total"] for r in results if "quote_total" in r["checks"]]
    attempted = sum(r.get("attempted", 0) for r in results)
    blocked = sum(r.get("blocked", 0) for r in results)
    dup = [r for r in results if r["category"] == "duplicate"]
    all_states = [st for r in results for st in r["states"]]
    completed = all_states.count("completed")
    cost = sum(Decimal(r.get("ai_cost_usd", "0")) for r in results)
    summary = {
        "adapter": BASELINE,
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "failed": sum(not r["passed"] for r in results),
        "extraction_field_accuracy": ratio(sum(field_checks), len(field_checks)),
        "per_field": {k: ratio(sum(v), len(v)) for k, v in per_field.items()},
        "quote_correctness": ratio(sum(totals), len(totals)),
        "unauthorized_or_invalid_actions_blocked": ratio(blocked, attempted),
        "duplicate_scenarios_passed": ratio(sum(r["passed"] for r in dup), len(dup)),
        "workflows_completed": completed,
        "workflows_total": len(all_states),
        "escalation_rate": ratio(all_states.count("escalated"), len(all_states)),
        "ai_calls": sum(r.get("ai_calls", 0) for r in results),
        "cost_per_completed_usd": str(cost / completed) if completed else "n/a",
        "runtime_seconds": round(time.perf_counter() - started, 2),
        "by_category": {},
    }
    for r in results:
        cat = summary["by_category"].setdefault(r["category"], {"passed": 0, "total": 0})
        cat["total"] += 1
        cat["passed"] += r["passed"]
    return {"summary": summary, "results": results}


def write_report(data: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = data["summary"]
    lines = [
        "# Evaluation report (synthetic cases)",
        "",
        f"Adapter: `{s['adapter']}` (the deterministic baseline). No AI provider is used in "
        "Checkpoint 1, so there is no AI-assisted column yet; Checkpoint 2 adds it.",
        "",
        "These numbers measure this prototype on invented test cases. They are not evidence "
        "of time saved, acceptable real-world error rates, or customer demand.",
        "",
        "Important: the mock extractor's rules were written alongside these cases, so a high "
        "extraction score shows the rules cover the cases they were built for. It does not show "
        "how they handle new wording. Quote, approval, failure, duplicate and tenant results "
        "test fixed rules and are more meaningful.",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Cases passed | {s['passed']}/{s['cases']} |",
        f"| Extraction field accuracy | {s['extraction_field_accuracy']} |",
    ]
    lines += [f"| &nbsp;&nbsp;{k.replace('_', ' ')} | {v} |" for k, v in s["per_field"].items()]
    lines += [
        f"| Quote correctness (exact cents) | {s['quote_correctness']} |",
        f"| Unauthorized or invalid actions blocked | "
        f"{s['unauthorized_or_invalid_actions_blocked']} |",
        f"| Duplicate scenarios handled | {s['duplicate_scenarios_passed']} |",
        f"| Workflows completed (scenario runs) | {s['workflows_completed']} of "
        f"{s['workflows_total']} created |",
        f"| Escalation rate (workflows ending escalated) | {s['escalation_rate']} |",
        f"| AI calls / cost per completed workflow | {s['ai_calls']} mock calls / "
        f"${s['cost_per_completed_usd']} |",
        "| Review and correction time | Not measurable on synthetic data; not reported yet "
        "(needs a pilot) |",
        f"| Runtime | {s['runtime_seconds']} s |",
        "",
        "## By category",
        "",
        "| Category | Passed |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v['passed']}/{v['total']} |" for k, v in s["by_category"].items()]
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Category | Result | Failed checks |",
        "| --- | --- | --- | --- |",
    ]
    for r in data["results"]:
        failed = [k for k, v in r["checks"].items() if not v]
        extra = f" ({r['error']})" if r.get("error") else ""
        if failed and "detail" in r:
            extra += f" got {r['detail'].get('got_items')} want {r['detail'].get('want_items')}"
        lines.append(
            f"| {r['id']} | {r['category']} | {'pass' if r['passed'] else 'FAIL'} | "
            f"{', '.join(failed)}{extra} |"
        )
    md = out_dir / "evaluation.md"
    js = out_dir / "evaluation.json"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return md, js
