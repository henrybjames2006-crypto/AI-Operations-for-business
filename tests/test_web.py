"""HTTP-level checks: permissions and tenant isolation are enforced by the server."""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import approve, make_env, state, to_awaiting
from opsapp.web.app import create_app


@pytest.fixture
def web(tmp_path):  # type: ignore[no-untyped-def]
    env = make_env(tmp_path)
    app = create_app(env.c.settings, container=env.c)
    yield env, app
    env.c.engine.dispose()


def client_as(app, env, user):  # type: ignore[no-untyped-def]
    c = TestClient(app, follow_redirects=False)
    page = c.get("/login")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = c.post("/login", data={"user_id": env.ids[user], "csrf": token})
    assert r.status_code == 303
    # The session (and its form token) is replaced at sign-in.
    page = c.get("/workflows")
    c.csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)  # type: ignore[attr-defined]
    return c


def test_pages_require_sign_in(web) -> None:  # type: ignore[no-untyped-def]
    _env, app = web
    c = TestClient(app, follow_redirects=False)
    for path in ["/", "/workflows", "/approvals", "/requests/new", "/audit"]:
        assert c.get(path).status_code == 303


def test_full_flow_over_http(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    priya = client_as(app, env, "priya")
    r = priya.post(
        "/requests",
        data={
            "text": "Please set up 3 printers next week.",
            "sender": "it@maplestreetlaw.example",
            "submission_key": "k1",
            "csrf": priya.csrf,
        },
    )
    assert r.status_code == 303
    wf_url = r.headers["location"]
    page = priya.get(wf_url)
    assert page.status_code == 200 and "$435.00" in page.text and "Draft ready" in page.text
    version = re.search(r'name="version" value="(\d+)"', page.text).group(1)
    assert (
        priya.post(wf_url + "/submit", data={"version": version, "csrf": priya.csrf}).status_code
        == 303
    )
    marcus = client_as(app, env, "marcus")
    page = marcus.get(wf_url)
    assert "SIMULATED" in page.text and "Approve this version" in page.text
    h = re.search(r'name="subject_hash" value="([0-9a-f]+)"', page.text).group(1)
    assert (
        marcus.post(wf_url + "/approve", data={"subject_hash": h, "csrf": marcus.csrf}).status_code
        == 303
    )
    assert state(env, wf_url.rsplit("/", 1)[1]) == "approved"


def test_csrf_token_required(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    priya = client_as(app, env, "priya")
    r = priya.post("/requests", data={"text": "x", "submission_key": "k", "csrf": "wrong"})
    assert r.status_code == 400


@pytest.mark.parametrize("who", ["priya", "victor"])
def test_approve_endpoint_refuses_wrong_roles(web, who) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    wf_id = to_awaiting(env)
    h = env.svc.approval_subject_hash(env.ids["marcus"], wf_id)
    c = client_as(app, env, who)
    r = c.post(f"/workflows/{wf_id}/approve", data={"subject_hash": h, "csrf": c.csrf})
    assert r.status_code == 403
    assert state(env, wf_id) == "awaiting_approval"


def test_viewer_cannot_post_requests_or_simulation_controls(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    v = client_as(app, env, "victor")
    assert v.get("/requests/new").status_code == 403
    r = v.post(
        "/requests", data={"text": "Set up 2 printers", "submission_key": "z", "csrf": v.csrf}
    )
    assert r.status_code == 403
    assert (
        v.post(
            "/simulation/fault", data={"adapter": "sim_email", "mode": "fail", "csrf": v.csrf}
        ).status_code
        == 403
    )
    assert v.post("/simulation/dispatch", data={"csrf": v.csrf}).status_code == 403


def test_cross_tenant_access_is_not_found_everywhere(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    wf_id = to_awaiting(env)
    h = env.svc.approval_subject_hash(env.ids["marcus"], wf_id)
    for user in ["omar", "grace", "nia"]:  # Northgate users, including their owner
        c = client_as(app, env, user)
        base = f"/workflows/{wf_id}"
        assert c.get(base).status_code == 404
        assert c.get(base + "/export.json").status_code == 404
        posts = {
            "/approve": {"subject_hash": h},
            "/reject": {"subject_hash": h, "reason": "x"},
            "/submit": {"version": "1"},
            "/cancel": {"reason": "x"},
            "/resolve": {"resolution": "confirmed_done", "note": "x"},
            "/retry": {"note": "x"},
            "/revise": {},
            "/answer/clq_x": {"answer": "1", "version": "1"},
        }
        for suffix, data in posts.items():
            r = c.post(base + suffix, data={**data, "csrf": c.csrf})
            # A role check may refuse first (403, same for any ID); otherwise the record is
            # invisible (404). The owner has every permission, so always sees 404.
            expected = {404} if user == "grace" else {403, 404}
            assert r.status_code in expected, (user, suffix, r.status_code)
        assert wf_id not in c.get("/workflows").text
        assert wf_id not in c.get("/audit").text
    assert state(env, wf_id) == "awaiting_approval"


def test_cross_tenant_pricing_and_dispatch(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    wf_id = to_awaiting(env)
    approve(env, wf_id)
    grace = client_as(app, env, "grace")
    from sqlalchemy import select

    from opsapp.persistence.models import PricingVersion

    with env.c.read_sf() as s:
        draft = s.scalars(select(PricingVersion).where(PricingVersion.status == "draft")).one()
    r = grace.post(f"/catalog/pricing/{draft.id}/approve", data={"csrf": grace.csrf})
    assert r.status_code == 404
    assert "Brightline" not in grace.get("/catalog").text
    assert "harbordental" not in grace.get("/simulation").text


def test_simulated_actions_are_labeled(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    wf_id = to_awaiting(env)
    page = client_as(app, env, "marcus").get(f"/workflows/{wf_id}")
    assert page.text.count("SIMULATED") >= 3
    assert "availability NOT checked" in page.text
