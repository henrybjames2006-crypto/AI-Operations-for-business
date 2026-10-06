"""Timings and correction counts, checked on scripted workflows with known answers."""

from __future__ import annotations

from conftest import Env, answer_demo, approve, make_env, submit
from opsapp.workflow.measures import measure, summarize, to_csv


def _measures(env: Env):  # type: ignore[no-untyped-def]
    with env.c.read_sf() as s:
        return measure(s, env.ids["tenant_brightline"])


def test_step_timings_come_from_the_audit_log(env: Env) -> None:
    wf = submit(env)  # demo request: needs a site and a migration count
    env.clock.advance(10 * 60)
    answer_demo(env, wf)  # draft ready 10 minutes after receipt
    env.clock.advance(4 * 60)
    env.svc.submit_for_approval(env.ids["priya"], wf)
    env.clock.advance(30 * 60)
    approve(env, wf)
    (m,) = _measures(env)
    assert m.minutes == {"to_draft": 10.0, "to_submit": 4.0, "to_decision": 30.0}
    summary = summarize([m])
    assert summary["stages"][0]["median"] == 10.0 and summary["stages"][2]["n"] == 1


def test_unfinished_steps_are_left_out_not_guessed(env: Env) -> None:
    submit(env)
    (m,) = _measures(env)
    assert m.minutes == {"to_draft": None, "to_submit": None, "to_decision": None}
    assert m.corrections == dict.fromkeys(("customer", "site", "items", "timeframe"))
    assert summarize([m])["stages"][0]["n"] == 0


def test_corrections_kept_filled_and_changed(env: Env) -> None:
    # Demo request: customer from the sender's domain (kept), site asked for (filled),
    # workstations stated but migration count asked for (filled), timeframe stated (kept).
    wf = submit(env)
    answer_demo(env, wf)
    (m,) = _measures(env)
    assert m.corrections == {
        "customer": "kept",
        "site": "filled",
        "items": "filled",
        "timeframe": "kept",
    }

    # A person then changes the stated quantity and the timeframe.
    env.svc.edit_quote(
        env.ids["priya"],
        wf,
        lines=[("WS-INSTALL", "6"), ("DATA-MIGR", "5")],
        recipient="office@harbordental.example",
        site_id=env.ids["site_harbor_elm_street"],
        timeframe="asap",
        reason="customer called",
    )
    (m,) = _measures(env)
    assert m.corrections["items"] == "changed" and m.corrections["timeframe"] == "changed"
    assert m.corrections["customer"] == "kept"
    fields = {f["field"]: f for f in summarize([m])["fields"]}
    assert fields["items"]["changed"] == 1 and fields["customer"]["kept"] == 1


def test_complete_request_is_all_kept(env: Env) -> None:
    submit(env, "Please set up 3 printers next week.", "it@maplestreetlaw.example")
    (m,) = _measures(env)
    assert set(m.corrections.values()) == {"kept"}
    assert m.reader == "mock/deterministic-rules-v1"


def test_measures_stay_inside_the_tenant(env: Env) -> None:
    submit(env)
    with env.c.read_sf() as s:
        assert measure(s, env.ids["tenant_northgate"]) == []


def test_csv_has_one_row_per_workflow(env: Env) -> None:
    submit(env)
    submit(env, "Please set up 3 printers next week.", "it@maplestreetlaw.example")
    lines = to_csv(_measures(env)).strip().splitlines()
    assert lines[0].startswith("workflow_id,received_at_utc,state,reader")
    assert len(lines) == 3


def test_measurements_page_and_csv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from opsapp.web.app import create_app
    from test_web import client_as

    env = make_env(tmp_path)
    app = create_app(env.c.settings, container=env.c)
    try:
        submit(env, "Please set up 3 printers next week.", "it@maplestreetlaw.example")
        victor = client_as(app, env, "victor")  # read-only users can see measurements
        page = victor.get("/measurements")
        assert page.status_code == 200 and "Time per step" in page.text and "kept" in page.text
        csv_page = victor.get("/measurements.csv")
        assert csv_page.status_code == 200 and csv_page.text.count("\n") == 2
        grace = client_as(app, env, "grace")
        assert "maplestreetlaw" not in grace.get("/measurements").text
        assert grace.get("/measurements.csv").text.count("\n") == 1
    finally:
        env.c.engine.dispose()
