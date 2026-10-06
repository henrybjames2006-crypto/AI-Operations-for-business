"""The optional AI reader, tested against a fake client: no network, no cost."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from sqlalchemy import select

from conftest import DEMO_REQUEST, answer_demo, events, make_env, state, submit
from opsapp.ai.claude import (
    MAX_OUTPUT_TOKENS,
    ClaudeExtractor,
    ReaderError,
    build_user_message,
    estimate_cost,
)
from opsapp.ai.fallback import FallbackExtractor
from opsapp.ai.guard import validate_output
from opsapp.ai.mock import MockExtractor
from opsapp.ai.schema import CatalogHint, CustomerHint, ExtractionContext
from opsapp.config import Settings
from opsapp.persistence.models import AIUsage, ExtractionResult
from opsapp.redaction import RedactingFilter, redact

CTX = ExtractionContext(
    today=date(2026, 10, 5),
    customers=[
        CustomerHint(name="Harbor Dental Group", aliases=["Harbor Dental", "HDG"]),
        CustomerHint(name="Maple Street Law", aliases=[]),
    ],
    site_labels=["Bay Road", "Elm Street"],
    catalog=[
        CatalogHint(sku="WS-INSTALL", name="Workstation setup", keywords=["workstation"]),
        CatalogHint(sku="DATA-MIGR", name="User data migration", keywords=["migrate"]),
        CatalogHint(sku="PRN-SETUP", name="Printer setup", keywords=["printer"]),
    ],
)

GOOD = {
    "request_type": "service_request",
    "customer_mentions": [],
    "site_mentions": [],
    "items": [
        {
            "sku": "WS-INSTALL",
            "quantity": "5",
            "quantity_text": "five",
            "evidence": "we need five new workstations installed",
        },
        {
            "sku": "DATA-MIGR",
            "quantity": None,
            "quantity_text": None,
            "evidence": "our files moved over from the old PCs",
        },
    ],
    "unsupported": [],
    "timeframe": "next_week",
    "timeframe_text": "next week",
    "suspicious_instructions": [],
    "notes": [],
}


@dataclass
class Block:
    text: str
    type: str = "text"


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 300


@dataclass
class Reply:
    body: str
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)

    @property
    def content(self) -> list[Block]:
        return [Block(self.body)]


_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def timeout() -> Exception:
    return anthropic.APITimeoutError(request=_REQ)


def status_error(cls: type[anthropic.APIStatusError], code: int) -> Exception:
    return cls("error", response=httpx2.Response(code, request=_REQ), body=None)


class FakeMessages:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, *replies: Any) -> None:
        self.messages = FakeMessages(list(replies))


def reply(data: dict[str, Any] | None = None, **kw: Any) -> Reply:
    return Reply(json.dumps(data if data is not None else GOOD), **kw)


def guarded(client: FakeClient, spent: str = "0", budget: str = "5") -> FallbackExtractor:
    return FallbackExtractor(
        ClaudeExtractor(client, "claude-haiku-4-5"),
        MockExtractor(),
        lambda: Decimal(spent),
        Decimal(budget),
    )


# --- The request that is sent -------------------------------------------------------------


def test_request_wraps_customer_text_as_data_and_sends_no_prices() -> None:
    client = FakeClient(reply())
    ClaudeExtractor(client).extract(DEMO_REQUEST, "office@harbordental.example", CTX)
    call = client.messages.calls[0]
    user = call["messages"][0]["content"]
    assert f"<request>\n{DEMO_REQUEST}\n</request>" in user
    assert "untrusted" in call["system"] and "never instructions" in call["system"]
    assert "$" not in user and "price" not in user.lower()
    assert call["max_tokens"] == MAX_OUTPUT_TOKENS
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "effort" not in call["output_config"]  # Haiku 4.5 rejects effort
    assert call["model"] == "claude-haiku-4-5"


def test_effort_is_sent_only_to_models_that_take_it() -> None:
    client = FakeClient(reply())
    ClaudeExtractor(client, "claude-sonnet-5-5").extract("x", "", CTX)
    assert client.messages.calls[0]["output_config"]["effort"] == "low"


def test_user_message_lists_names_and_catalog_only() -> None:
    msg = build_user_message("hello", "a@b.example", CTX)
    assert "Harbor Dental Group (aliases: Harbor Dental, HDG)" in msg
    assert "WS-INSTALL: Workstation setup" in msg
    assert "<today>2026-10-05</today>" in msg


# --- Parsing and cost ---------------------------------------------------------------------


def test_valid_reply_is_parsed_with_tokens_and_cost() -> None:
    run = ClaudeExtractor(FakeClient(reply())).extract(DEMO_REQUEST, "", CTX)
    assert [i.sku for i in run.output.items] == ["WS-INSTALL", "DATA-MIGR"]
    assert run.input_tokens == 1200 and run.output_tokens == 300
    # 1200 * $1/M + 300 * $5/M
    assert Decimal(run.est_cost_usd) == Decimal("0.0027")
    assert run.provider == "anthropic" and run.fallback_reason is None


def test_cost_estimate_per_model() -> None:
    assert estimate_cost("claude-sonnet-5-5", 1_000_000, 0) == Decimal("2")
    assert estimate_cost("claude-opus-5-5", 0, 1_000_000) == Decimal("20")


@pytest.mark.parametrize(
    "bad",
    [
        Reply("not json at all"),
        Reply(json.dumps({"items": "five"})),
        Reply(json.dumps(GOOD), stop_reason="max_tokens"),
        Reply("", stop_reason="refusal"),
    ],
)
def test_unusable_replies_raise_without_retry_and_keep_usage(bad: Reply) -> None:
    with pytest.raises(ReaderError) as info:
        ClaudeExtractor(FakeClient(bad)).extract(DEMO_REQUEST, "", CTX)
    assert not info.value.retryable
    assert info.value.usage is not None and info.value.usage.input_tokens == 1200


def test_invented_services_and_quotes_are_dropped_by_the_guard() -> None:
    data = dict(GOOD)
    data["items"] = [
        {"sku": "SERVER-RACK", "quantity": "1", "quantity_text": None, "evidence": "server"},
        {
            "sku": "PRN-SETUP",
            "quantity": "3",
            "quantity_text": None,
            "evidence": "three printers please",  # not in the request text
        },
        GOOD["items"][0],
    ]
    run = ClaudeExtractor(FakeClient(reply(data))).extract(DEMO_REQUEST, "", CTX)
    cleaned, problems = validate_output(run.output, DEMO_REQUEST, CTX)
    assert [i.sku for i in cleaned.items] == ["WS-INSTALL"]
    assert len(problems) == 2


# --- Retry, fallback and budget -----------------------------------------------------------


def test_timeout_is_retried_once_then_falls_back_to_rules() -> None:
    client = FakeClient(timeout(), timeout())
    run = guarded(client).extract(DEMO_REQUEST, "office@harbordental.example", CTX)
    assert len(client.messages.calls) == 2
    assert run.provider == "mock"
    assert run.fallback_reason == "AI call timed out"
    assert run.model_id == "deterministic-rules-v1"


def test_one_transient_failure_then_success_uses_the_ai() -> None:
    client = FakeClient(status_error(anthropic.InternalServerError, 500), reply())
    run = guarded(client).extract(DEMO_REQUEST, "", CTX)
    assert run.provider == "anthropic" and run.fallback_reason is None
    assert len(client.messages.calls) == 2


def test_rejected_key_is_not_retried() -> None:
    client = FakeClient(status_error(anthropic.AuthenticationError, 401), reply())
    run = guarded(client).extract(DEMO_REQUEST, "", CTX)
    assert len(client.messages.calls) == 1
    assert run.fallback_reason == "the AI API key was rejected"


def test_bad_output_is_not_retried_and_its_cost_is_kept() -> None:
    client = FakeClient(Reply("{broken"), reply())
    run = guarded(client).extract(DEMO_REQUEST, "", CTX)
    assert len(client.messages.calls) == 1
    assert run.provider == "mock"
    assert len(run.failed_attempts) == 1
    assert Decimal(run.failed_attempts[0].est_cost_usd) > 0


def test_budget_reached_means_no_ai_call() -> None:
    client = FakeClient(reply())
    run = guarded(client, spent="5.00", budget="5.00").extract(DEMO_REQUEST, "", CTX)
    assert client.messages.calls == []
    assert run.provider == "mock"
    assert run.fallback_reason is not None and "budget" in run.fallback_reason


# --- Through the workflow -----------------------------------------------------------------


def _with_ai(client: FakeClient, spent: str = "0"):  # type: ignore[no-untyped-def]
    env = make_env()
    env.c.service.extractor = guarded(client, spent=spent)
    return env


def test_ai_reading_flows_through_to_the_same_quote_and_records_usage() -> None:
    env = _with_ai(FakeClient(reply()))
    wf_id = submit(env)
    answer_demo(env, wf_id)
    assert state(env, wf_id) == "draft_ready"
    with env.c.read_sf() as s:
        usage = s.scalars(select(AIUsage).where(AIUsage.workflow_id == wf_id)).all()
        assert [(u.provider, u.input_tokens) for u in usage] == [("anthropic", 1200)]
        ex = s.scalars(select(ExtractionResult)).one()
        assert ex.adapter == "anthropic" and ex.output["fallback_reason"] is None
    with env.c.read_sf() as s:
        from opsapp.persistence.models import WorkflowInstance

        wf = s.get(WorkflowInstance, wf_id)
        qv = env.c.service.current_quote_version(s, wf)  # type: ignore[arg-type]
        assert qv is not None and qv.total == Decimal("1475.00")


def test_obedient_model_still_cannot_change_price_customer_or_approval() -> None:
    # A model that "follows" the planted instruction: it invents a discount note and
    # claims the request is approved. None of that reaches pricing or approval.
    data = dict(GOOD)
    data["notes"] = ["Apply 50% loyalty discount; approved automatically."]
    data["customer_mentions"] = ["Maple Street Law"]  # wrong customer, not in the text
    env = _with_ai(FakeClient(reply(data)))
    wf_id = submit(env)
    with env.c.read_sf() as s:
        from opsapp.persistence.models import WorkflowInstance

        wf = s.get(WorkflowInstance, wf_id)
        assert wf is not None
        # Customer comes from the sender's domain, not from the model.
        assert wf.scope["customer_id"] == env.ids["customer_harbor"]
    answer_demo(env, wf_id)
    assert state(env, wf_id) == "draft_ready"  # still needs a person to submit and approve
    with env.c.read_sf() as s:
        wf = s.get(WorkflowInstance, wf_id)
        qv = env.c.service.current_quote_version(s, wf)  # type: ignore[arg-type]
        assert qv is not None and qv.total == Decimal("1475.00")


def test_fallback_is_recorded_in_history_and_costs_are_kept() -> None:
    env = _with_ai(FakeClient(Reply("{broken")))
    wf_id = submit(env)
    kinds = [e.event_type for e in events(env, wf_id)]
    assert "ai_fallback" in kinds
    with env.c.read_sf() as s:
        rows = s.scalars(select(AIUsage).where(AIUsage.workflow_id == wf_id)).all()
        tasks = sorted((u.provider, u.task) for u in rows)
        assert tasks == [("anthropic", "extract_request_failed"), ("mock", "extract_request")]


def test_ai_page_shows_tokens_cost_and_fallback(tmp_path: Path) -> None:
    from opsapp.web.views import workflow_detail

    env = _with_ai(FakeClient(timeout(), timeout()))
    wf_id = submit(env)
    with env.c.read_sf() as s:
        ctx = workflow_detail(s, env.c.service, env.ids["priya"], wf_id)
        assert ctx["extraction"].output["fallback_reason"] == "AI call timed out"


# --- Settings and secrets -----------------------------------------------------------------


FAKE_KEY = "sk-ant-api03-" + "PLANTEDFAKEKEY" * 4


def test_api_key_is_required_and_never_shown() -> None:
    with pytest.raises(ValueError, match="OPSAPP_AI_API_KEY"):
        Settings(database_path=Path("x"), session_secret="s" * 16, ai_provider="anthropic")
    st = Settings(
        database_path=Path("x"),
        session_secret="s" * 16,
        ai_provider="anthropic",
        ai_api_key=FAKE_KEY,
    )
    assert FAKE_KEY not in repr(st)
    assert "PLANTEDFAKEKEY" not in redact(f"key={FAKE_KEY}")


def test_unknown_provider_and_model_are_refused() -> None:
    with pytest.raises(ValueError):
        Settings(database_path=Path("x"), session_secret="s" * 16, ai_provider="openai")
    with pytest.raises(ValueError):
        Settings(database_path=Path("x"), session_secret="s" * 16, ai_model="claude-unknown")


def test_planted_key_never_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.handler.addFilter(RedactingFilter())
    caplog.set_level(logging.INFO)
    logging.getLogger("opsapp.test").info("calling AI with %s", FAKE_KEY)
    assert "PLANTEDFAKEKEY" not in caplog.text
