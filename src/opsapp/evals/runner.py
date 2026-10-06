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

from ..ai.ports import Extractor
from ..clock import FixedClock
from ..config import Settings
from ..container import Container, build
from ..domain.errors import DomainError, NotFound, PermissionDenied, StaleApproval
from ..domain.money import format_usd
from ..persistence.models import (
    AIUsage,
    Approval,
    Base,
    ClarificationQuestion,
    ExtractionResult,
    SimOperation,
    WorkflowInstance,
)
from ..seed import seed
from .cases import CASES, Case
from .heldout import HELDOUT

START = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
BASELINE = "mock/deterministic-rules-v1"


ReaderFactory = Callable[[], Extractor]


class Run:
    def __init__(self, reader: ReaderFactory | None = None) -> None:
        self.clock = FixedClock(START)
        self.c: Container = build(
            Settings(database_path=Path("unused"), session_secret="eval-only-secret"),
            self.clock,
            memory=True,
        )
        if reader is not None:
            self.c.service.extractor = reader()
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
    with r.c.read_sf() as s:
        ex = s.scalars(
            select(ExtractionResult).where(ExtractionResult.request_id == wf.request_id)
        ).first()
        detail["reader"] = f"{ex.adapter}/{ex.model_id}" if ex else None
        detail["fallback_reason"] = ex.output.get("fallback_reason") if ex else None
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


SCENARIO_CATEGORIES = (
    "approval_edit",
    "adapter_failure",
    "timeout_uncertain",
    "duplicate",
    "cross_tenant",
)
FIELDS = ("customer", "items", "timeframe", "instruction_flag", "unsupported")


def ratio(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.0%})" if b else "n/a"


def split_of(case_category: str) -> str:
    return "heldout" if case_category.startswith("heldout") else "original"


def run_cases(
    cases: list[Case],
    reader: ReaderFactory | None = None,
    after_case: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        run = Run(reader)
        try:
            res = run_scenario(run, case) if case.scenario else score_extraction(run, case)
            with run.c.read_sf() as s:
                usage = s.scalars(select(AIUsage)).all()
                paid = [u for u in usage if u.provider != "mock"]
                res["ai_calls"] = len(paid)
                res["input_tokens"] = sum(u.input_tokens or 0 for u in paid)
                res["output_tokens"] = sum(u.output_tokens or 0 for u in paid)
                res["ai_cost_usd"] = str(sum((u.est_cost_usd for u in paid), Decimal("0")))
                res["states"] = [w.state for w in s.scalars(select(WorkflowInstance))]
        except Exception as exc:  # noqa: BLE001 - a crash is a recorded failure
            res = {
                "checks": {"ran_without_error": False},
                "error": f"{type(exc).__name__}: {exc}",
                "states": [],
            }
        finally:
            run.close()
        res.update(
            {
                "id": case.id,
                "category": case.category,
                "split": split_of(case.category),
                "passed": all(res["checks"].values()),
            }
        )
        if after_case is not None:
            after_case(res)
        results.append(res)
    return results


def reading_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Request-reading metrics for extraction cases (no scenarios)."""
    rows = [r for r in results if r["category"] not in SCENARIO_CATEGORIES]
    per_field: dict[str, list[bool]] = {}
    for r in rows:
        for k, v in r["checks"].items():
            per_field.setdefault(k, []).append(v)
    field_checks = [v for k in FIELDS for v in per_field.get(k, [])]
    manip = [r for r in rows if "manipulation" in r["category"]]
    caught = sum(r["checks"].get("instruction_flag", False) for r in manip)
    cost = sum(Decimal(r.get("ai_cost_usd", "0")) for r in rows)
    calls = sum(r.get("ai_calls", 0) for r in rows)
    fallbacks = [r for r in rows if (r.get("detail") or {}).get("fallback_reason")]
    return {
        "cases": len(rows),
        "cases_fully_correct": ratio(sum(r["passed"] for r in rows), len(rows)),
        "field_accuracy": ratio(sum(field_checks), len(field_checks)),
        "per_field": {k: ratio(sum(v), len(v)) for k, v in per_field.items()},
        "manipulation_flagged": ratio(caught, len(manip)),
        "fallbacks": len(fallbacks),
        "ai_calls": calls,
        "input_tokens": sum(r.get("input_tokens", 0) for r in rows),
        "output_tokens": sum(r.get("output_tokens", 0) for r in rows),
        "ai_cost_usd": str(cost),
        "cost_per_request_usd": str((cost / Decimal(len(rows))).quantize(Decimal("0.000001")))
        if rows
        else "n/a",
    }


def run_all(reader: ReaderFactory | None = None, label: str = BASELINE) -> dict[str, Any]:
    """Every case (0.1.0 set, scenarios and held-out) with one reader."""
    started = time.perf_counter()
    results = run_cases([*CASES, *HELDOUT], reader)
    totals = [r["checks"]["quote_total"] for r in results if "quote_total" in r["checks"]]
    attempted = sum(r.get("attempted", 0) for r in results)
    blocked = sum(r.get("blocked", 0) for r in results)
    dup = [r for r in results if r["category"] == "duplicate"]
    all_states = [st for r in results for st in r["states"]]
    completed = all_states.count("completed")
    cost = sum(Decimal(r.get("ai_cost_usd", "0")) for r in results)
    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        cat = by_category.setdefault(r["category"], {"passed": 0, "total": 0})
        cat["total"] += 1
        cat["passed"] += r["passed"]
    summary = {
        "adapter": label,
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "failed": sum(not r["passed"] for r in results),
        "reading": {
            split: reading_summary([r for r in results if r["split"] == split])
            for split in ("original", "heldout")
        },
        "quote_correctness": ratio(sum(totals), len(totals)),
        "unauthorized_or_invalid_actions_blocked": ratio(blocked, attempted),
        "duplicate_scenarios_passed": ratio(sum(r["passed"] for r in dup), len(dup)),
        "workflows_completed": completed,
        "workflows_total": len(all_states),
        "escalation_rate": ratio(all_states.count("escalated"), len(all_states)),
        "ai_calls": sum(r.get("ai_calls", 0) for r in results),
        "cost_per_completed_usd": str(cost / completed) if completed else "n/a",
        "runtime_seconds": round(time.perf_counter() - started, 2),
        "by_category": by_category,
    }
    return {"summary": summary, "results": results}


def _case_rows(results: list[dict[str, Any]]) -> list[str]:
    lines = ["| Case | Category | Result | Failed checks |", "| --- | --- | --- | --- |"]
    for r in results:
        failed = [k for k, v in r["checks"].items() if not v]
        extra = f" ({r['error']})" if r.get("error") else ""
        d = r.get("detail") or {}
        if failed and d:
            extra += f" got {d.get('got_items')} want {d.get('want_items')}"
            if "timeframe" in failed:
                extra += f"; timeframe {d.get('got_timeframe')}"
        if d.get("fallback_reason"):
            extra += f" [fell back: {d['fallback_reason']}]"
        lines.append(
            f"| {r['id']} | {r['category']} | {'pass' if r['passed'] else 'FAIL'} | "
            f"{', '.join(failed)}{extra} |"
        )
    return lines


CAVEAT = (
    "These numbers measure this prototype on invented test cases. They are not evidence of "
    "time saved, acceptable real-world error rates, or customer demand."
)


def write_report(data: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = data["summary"]
    lines = [
        "# Evaluation report (synthetic cases)",
        "",
        f"Reader: `{s['adapter']}`.",
        "",
        CAVEAT,
        "",
        "The original cases were written alongside the rule-based reader, so its score on "
        "them only shows the rules cover what they were built for. The held-out cases "
        "(added in 0.2.0) were written before any reader ran on them and are the fairer "
        "measure of request reading. Quote, approval, failure, duplicate and tenant results "
        "test fixed rules.",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Cases passed | {s['passed']}/{s['cases']} |",
    ]
    for split, label in (("original", "original cases"), ("heldout", "held-out cases")):
        rd = s["reading"][split]
        lines.append(f"| Reading, {label}: fully correct | {rd['cases_fully_correct']} |")
        lines.append(f"| Reading, {label}: field accuracy | {rd['field_accuracy']} |")
    lines += [
        f"| Quote correctness (exact cents) | {s['quote_correctness']} |",
        f"| Unauthorized or invalid actions blocked | "
        f"{s['unauthorized_or_invalid_actions_blocked']} |",
        f"| Duplicate scenarios handled | {s['duplicate_scenarios_passed']} |",
        f"| Workflows completed (scenario runs) | {s['workflows_completed']} of "
        f"{s['workflows_total']} created |",
        f"| Escalation rate (workflows ending escalated) | {s['escalation_rate']} |",
        f"| Paid AI calls / cost per completed workflow | {s['ai_calls']} / "
        f"${s['cost_per_completed_usd']} |",
        "| Review and correction time | Not measurable on synthetic data (the dashboard shows "
        "average time from draft to submission for real use) |",
        f"| Runtime | {s['runtime_seconds']} s |",
        "",
        "## By category",
        "",
        "| Category | Passed |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v['passed']}/{v['total']} |" for k, v in s["by_category"].items()]
    lines += ["", "## Cases", "", *_case_rows(data["results"])]
    md = out_dir / "evaluation.md"
    js = out_dir / "evaluation.json"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return md, js


# --- Comparison: rule-based reader versus the AI reader -----------------------------------


def run_compare(
    ai_reader: ReaderFactory,
    ai_label: str,
    after_case: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run only the request-reading cases through both readers, original and held-out."""
    cases = [c for c in [*CASES, *HELDOUT] if not c.scenario]
    mock = run_cases(cases)
    ai = run_cases(cases, ai_reader, after_case)
    out: dict[str, Any] = {"readers": {BASELINE: {}, ai_label: {}}, "results": {}}
    for label, results in ((BASELINE, mock), (ai_label, ai)):
        totals = [r["checks"]["quote_total"] for r in results if "quote_total" in r["checks"]]
        states = [r["checks"]["state"] for r in results if "state" in r["checks"]]
        out["readers"][label] = {
            "original": reading_summary([r for r in results if r["split"] == "original"]),
            "heldout": reading_summary([r for r in results if r["split"] == "heldout"]),
            "quote_correctness": ratio(sum(totals), len(totals)),
            "correct_next_step": ratio(sum(states), len(states)),
        }
        out["results"][label] = results
    by_id = {r["id"]: r for r in mock}
    out["disagreements"] = [
        {
            "id": r["id"],
            "rules_passed": by_id[r["id"]]["passed"],
            "ai_passed": r["passed"],
            "ai_failed_checks": [k for k, v in r["checks"].items() if not v],
            "rules_failed_checks": [k for k, v in by_id[r["id"]]["checks"].items() if not v],
        }
        for r in ai
        if r["passed"] != by_id[r["id"]]["passed"]
    ]
    return out


def write_compare_report(data: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = list(data["readers"])
    head = "| Metric | " + " | ".join(f"`{x}`" for x in labels) + " |"
    sep = "| --- |" + " --- |" * len(labels)

    def row(name: str, get: Callable[[dict[str, Any]], Any]) -> str:
        return f"| {name} | " + " | ".join(str(get(data["readers"][x])) for x in labels) + " |"

    lines = ["# Reader comparison (synthetic cases)", "", CAVEAT, "", head, sep]

    def pick(*path: str) -> Callable[[dict[str, Any]], Any]:
        def get(r: dict[str, Any]) -> Any:
            value: Any = r
            for key in path:
                value = value.get(key, "n/a") if isinstance(value, dict) else "n/a"
            return value

        return get

    for split, label in (("original", "Original cases"), ("heldout", "Held-out cases")):
        lines += [
            row(f"{label}: fully correct", pick(split, "cases_fully_correct")),
            row(f"{label}: field accuracy", pick(split, "field_accuracy")),
            row(f"{label}: manipulation flagged", pick(split, "manipulation_flagged")),
        ]
    for f in data["readers"][labels[0]]["heldout"]["per_field"]:
        lines.append(row(f"Held-out: {f.replace('_', ' ')}", pick("heldout", "per_field", f)))
    lines += [
        row("Quote correctness (exact cents)", lambda r: r["quote_correctness"]),
        row("Correct next step (quote vs ask)", lambda r: r["correct_next_step"]),
        row(
            "Fell back to rules",
            lambda r: r["original"]["fallbacks"] + r["heldout"]["fallbacks"],
        ),
        row("Paid AI calls", lambda r: r["original"]["ai_calls"] + r["heldout"]["ai_calls"]),
        row(
            "Tokens in / out",
            lambda r: (
                f"{r['original']['input_tokens'] + r['heldout']['input_tokens']:,} / "
                f"{r['original']['output_tokens'] + r['heldout']['output_tokens']:,}"
            ),
        ),
        row(
            "Estimated AI cost, whole run",
            lambda r: format_usd(
                Decimal(r["original"]["ai_cost_usd"]) + Decimal(r["heldout"]["ai_cost_usd"])
            ),
        ),
        row("Estimated AI cost per request", lambda r: f"${r['heldout']['cost_per_request_usd']}"),
        "",
        "Costs are estimated from reported token counts and list prices; the provider's bill "
        "is authoritative.",
        "",
        "## Cases where the readers differ",
        "",
        "| Case | Rules | AI | What the loser got wrong |",
        "| --- | --- | --- | --- |",
    ]
    for d in data["disagreements"]:
        wrong = d["ai_failed_checks"] if d["rules_passed"] else d["rules_failed_checks"]
        lines.append(
            f"| {d['id']} | {'pass' if d['rules_passed'] else 'FAIL'} | "
            f"{'pass' if d['ai_passed'] else 'FAIL'} | {', '.join(wrong)} |"
        )
    if not data["disagreements"]:
        lines.append("| (none) | | | |")
    for label in labels:
        lines += ["", f"## Cases: `{label}`", "", *_case_rows(data["results"][label])]
    md = out_dir / "comparison.md"
    js = out_dir / "comparison.json"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return md, js
