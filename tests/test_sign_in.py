"""Sign-in, sessions, user management and demo mode, checked through the real pages."""

from __future__ import annotations

import base64
import dataclasses
import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from conftest import make_env
from opsapp.auth import totp
from opsapp.auth.passwords import hash_password, password_problems, verify_password
from opsapp.domain.errors import Conflict, NotFound, PermissionDenied, ValidationError
from opsapp.persistence.models import AuthSession
from opsapp.web.app import create_app
from test_web import PASSWORD, TOTP_SECRET, client_as, csrf_of, give_sign_in


@pytest.fixture
def web(tmp_path):  # type: ignore[no-untyped-def]
    env = make_env(tmp_path)
    app = create_app(env.c.settings, container=env.c)
    yield env, app
    env.c.engine.dispose()


def _password_step(app, email: str, password: str):  # type: ignore[no-untyped-def]
    c = TestClient(app, follow_redirects=False)
    r = c.post(
        "/login", data={"email": email, "password": password, "csrf": csrf_of(c.get("/login").text)}
    )
    return c, r


def _code_step(c, code: str):  # type: ignore[no-untyped-def]
    page = c.get("/login/code")
    return c.post("/login/code", data={"code": code, "csrf": csrf_of(page.text)})


PRIYA = "priya@brightline-it.example"


# ----------------------------------------------------------------- building blocks


def test_totp_matches_rfc_6238_test_vectors() -> None:
    secret = base64.b32encode(b"12345678901234567890").decode()
    for unix, expected in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924")]:
        assert totp.code_at(secret, datetime.fromtimestamp(unix, UTC)) == expected


def test_totp_allows_one_step_of_drift_and_refuses_replay() -> None:
    now = datetime(2026, 10, 5, 15, 0, 10, tzinfo=UTC)
    code = totp.code_at(TOTP_SECRET, now)
    step = totp.matching_step(TOTP_SECRET, code, now, None)
    assert step is not None
    assert totp.matching_step(TOTP_SECRET, code, now, step) is None  # replay
    from datetime import timedelta

    assert totp.matching_step(TOTP_SECRET, code, now + timedelta(seconds=30), None) == step
    assert totp.matching_step(TOTP_SECRET, code, now + timedelta(seconds=90), None) is None
    assert totp.matching_step(TOTP_SECRET, "12345", now, None) is None
    uri = totp.provisioning_uri(TOTP_SECRET, PRIYA)
    assert uri.startswith("otpauth://totp/") and "secret=" + TOTP_SECRET in uri
    assert totp.qr_svg_data_uri(uri).startswith("data:image/svg+xml")


def test_password_hashing_and_rules() -> None:
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterY", stored)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "md5$abc")
    assert hash_password("same input here") != hash_password("same input here")  # salted
    assert password_problems("short") and password_problems("password1234")
    assert password_problems("aaaaaaaaaaaaaaa")
    assert password_problems("priya-in-the-office", "priya@x.example")
    assert password_problems("four rivers meet at dawn", "priya@x.example") == []


# ----------------------------------------------------------------- password and code


def test_full_sign_in_and_sign_out(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    c = client_as(app, env, "priya")
    assert c.get("/workflows").status_code == 200
    old_cookie = c.cookies.get("opsapp_session")
    assert c.post("/logout", data={"csrf": c.csrf}).status_code == 303
    assert c.get("/workflows").status_code == 303
    # The old cookie no longer works after sign-out: the session was ended on the server.
    replay = TestClient(app, follow_redirects=False)
    replay.cookies.set("opsapp_session", old_cookie)
    assert replay.get("/workflows").status_code == 303


def test_wrong_password_and_unknown_email_look_the_same(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    give_sign_in(env, "priya")
    _c, wrong = _password_step(app, PRIYA, "not the password at all")
    _c, unknown = _password_step(app, "nobody@brightline-it.example", PASSWORD)
    assert wrong.status_code == unknown.status_code == 400
    msg = re.compile(r'<div class="msg error">([^<]+)</div>')
    assert msg.search(wrong.text).group(1) == msg.search(unknown.text).group(1)  # type: ignore[union-attr]
    # A seed user without a password can't sign in outside demo mode.
    _c, demo_user = _password_step(app, "marcus@brightline-it.example", "")
    assert demo_user.status_code == 400


def test_five_wrong_tries_lock_the_account_for_15_minutes(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    give_sign_in(env, "priya")
    for _ in range(5):
        assert _password_step(app, PRIYA, "wrong wrong wrong")[1].status_code == 400
    # Locked: even the right password is refused, with the same message.
    assert _password_step(app, PRIYA, PASSWORD)[1].status_code == 400
    env.clock.advance(15 * 60 + 1)
    c, r = _password_step(app, PRIYA, PASSWORD)
    assert r.headers["location"] == "/login/code"
    from conftest import events

    kinds = [e.event_type for e in events(env)]
    assert kinds.count("sign_in_failed") >= 6 and "account_locked" in kinds
    assert all(PASSWORD not in (e.message + str(e.data)) for e in events(env))


def test_wrong_codes_count_towards_the_lock_and_codes_cannot_be_replayed(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    give_sign_in(env, "priya")
    c, _ = _password_step(app, PRIYA, PASSWORD)
    code = totp.code_at(TOTP_SECRET, env.clock.now())
    wrong = "000000" if code != "000000" else "111111"
    assert _code_step(c, wrong).status_code == 400
    assert _code_step(c, code).status_code == 303
    # The same code a second time (another browser, same 30 seconds) is refused.
    c2, _ = _password_step(app, PRIYA, PASSWORD)
    assert _code_step(c2, code).status_code == 400


def test_password_alone_reaches_nothing(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    give_sign_in(env, "priya")
    c, r = _password_step(app, PRIYA, PASSWORD)
    assert r.status_code == 303
    for path in ["/", "/workflows", "/catalog", "/audit", "/account"]:
        assert c.get(path).headers["location"] == "/login"
    # The code step has to happen within 5 minutes.
    env.clock.advance(5 * 60 + 1)
    assert c.get("/login/code").headers["location"] == "/login"


def test_recovery_code_works_once(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    owner = client_as(app, env, "dana")
    codes = owner.post("/account/recovery-codes", data={"csrf": owner.csrf})
    found = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", codes.text)
    assert len(found) == 10
    c, _ = _password_step(app, "dana@brightline-it.example", PASSWORD)
    r = _code_step(c, found[0].upper())
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "recovery code" in c.get("/").text
    c2, _ = _password_step(app, "dana@brightline-it.example", PASSWORD)
    assert _code_step(c2, found[0]).status_code == 400


def test_first_sign_in_sets_up_the_authenticator(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    owner = client_as(app, env, "dana")
    page = owner.post(
        "/users",
        data={
            "display_name": "Sam Rivera",
            "email": "Sam@Brightline-IT.example",
            "role": "operator",
            "csrf": owner.csrf,
        },
    )
    temp = re.search(r'<span class="secret">([^<]+)</span>', page.text).group(1)  # type: ignore[union-attr]
    c, r = _password_step(app, "sam@brightline-it.example", temp)
    assert r.headers["location"] == "/login/setup"
    setup = c.get("/login/setup")
    assert "data:image/svg+xml" in setup.text
    key = re.search(r'<span class="secret">([A-Z2-7 ]+)</span>', setup.text).group(1)  # type: ignore[union-attr]
    secret = key.replace(" ", "")
    bad = c.post("/login/setup", data={"code": "123456", "csrf": csrf_of(setup.text)})
    assert bad.status_code == 303 and bad.headers["location"] == "/login/setup"
    page = c.get("/login/setup")
    done = c.post(
        "/login/setup",
        data={"code": totp.code_at(secret, env.clock.now()), "csrf": csrf_of(page.text)},
    )
    assert done.status_code == 200 and len(re.findall(r"<li>[a-z0-9]{5}-", done.text)) == 10
    # A temporary password has to be replaced before anything else.
    assert c.get("/workflows").headers["location"] == "/account/password"
    form = c.get("/account/password")
    r = c.post(
        "/account/password",
        data={
            "current": temp,
            "new": "lantern orchard quietly",
            "repeat": "lantern orchard quietly",
            "csrf": csrf_of(form.text),
        },
    )
    assert r.headers["location"] == "/"
    assert c.get("/workflows").status_code == 200


# ----------------------------------------------------------------- sessions


def test_sessions_end_after_30_idle_minutes_and_after_8_hours(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    c = client_as(app, env, "priya")
    env.clock.advance(29 * 60)
    assert c.get("/workflows").status_code == 200
    env.clock.advance(31 * 60)
    r = c.get("/workflows")
    assert r.headers["location"] == "/login"
    assert "signed out" in c.get("/login").text

    c = client_as(app, env, "priya")
    for _ in range(17):  # active all day, every 29 minutes
        env.clock.advance(29 * 60)
        if c.get("/workflows").status_code != 200:
            break
    assert c.get("/workflows").status_code == 303
    with env.c.read_sf() as s:
        from sqlalchemy import select

        reasons = set(s.scalars(select(AuthSession.revoked_reason)))
    assert {"idle", "expired"} <= reasons


def test_disabling_a_user_signs_them_out_at_once(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    priya = client_as(app, env, "priya")
    owner = client_as(app, env, "dana")
    r = owner.post(f"/users/{env.ids['priya']}/disable", data={"csrf": owner.csrf})
    assert r.status_code == 303
    assert priya.get("/workflows").headers["location"] == "/login"
    assert _password_step(app, PRIYA, PASSWORD)[1].status_code == 400
    owner.post(f"/users/{env.ids['priya']}/enable", data={"csrf": owner.csrf})
    assert _password_step(app, PRIYA, PASSWORD)[1].status_code == 303


def test_reset_password_and_authenticator(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    priya = client_as(app, env, "priya")
    owner = client_as(app, env, "dana")
    page = owner.post(f"/users/{env.ids['priya']}/reset-password", data={"csrf": owner.csrf})
    temp = re.search(r'<span class="secret">([^<]+)</span>', page.text).group(1)  # type: ignore[union-attr]
    assert priya.get("/workflows").status_code == 303  # signed out
    assert _password_step(app, PRIYA, PASSWORD)[1].status_code == 400
    assert _password_step(app, PRIYA, temp)[1].headers["location"] == "/login/code"
    owner.post(f"/users/{env.ids['priya']}/reset-second-factor", data={"csrf": owner.csrf})
    assert _password_step(app, PRIYA, temp)[1].headers["location"] == "/login/setup"


# ----------------------------------------------------------------- owner controls


def test_only_owners_manage_users_and_only_in_their_company(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    marcus = client_as(app, env, "marcus")
    assert marcus.get("/users").status_code == 403
    assert (
        marcus.post(
            "/users",
            data={
                "display_name": "X",
                "email": "x@x.example",
                "role": "owner",
                "csrf": marcus.csrf,
            },
        ).status_code
        == 403
    )
    owner = client_as(app, env, "dana")
    page = owner.get("/users")
    assert "Priya" in page.text and "northgate" not in page.text
    other = env.ids["nia"]
    assert owner.post(f"/users/{other}/disable", data={"csrf": owner.csrf}).status_code == 404
    r = owner.post(f"/users/{env.ids['dana']}/disable", data={"csrf": owner.csrf})
    assert r.status_code == 303
    assert "own account" in owner.get("/users").text
    dup = owner.post(
        "/users",
        data={
            "display_name": "Copy",
            "email": PRIYA.upper(),
            "role": "viewer",
            "csrf": owner.csrf,
        },
    )
    assert dup.status_code == 303 and "already exists" in owner.get("/users").text


def test_an_owner_can_disable_another_owner_but_not_themselves(web) -> None:  # type: ignore[no-untyped-def]
    env, _app = web
    ava = env.c.auth.create_owner(
        "Brightline IT Services", "Ava Stone", "Ava@Example.com", "maple river stone path"
    )
    with pytest.raises(ValidationError, match="own account"):
        env.c.auth.set_active(ava, ava, False)
    env.c.auth.set_active(ava, env.ids["dana"], False)
    with pytest.raises(PermissionDenied):  # dana is disabled now
        env.c.auth.set_active(env.ids["dana"], ava, False)
    with pytest.raises(Conflict):
        env.c.auth.create_owner(
            "Brightline IT Services", "Ava 2", "ava@example.com", "willow canyon lamp oak"
        )
    with pytest.raises(NotFound, match="Companies"):
        env.c.auth.create_owner("No Such Co", "Bo", "bo@example.com", "maple river stone path")


def test_every_page_needs_a_full_sign_in(web) -> None:  # type: ignore[no-untyped-def]
    env, app = web
    give_sign_in(env, "priya")
    anonymous = TestClient(app, follow_redirects=False)
    half, _ = _password_step(app, PRIYA, PASSWORD)
    open_paths = {"/login", "/login/code", "/login/setup", "/logout", "/static"}
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path or path in open_paths or path.startswith("/static"):
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in methods & {"GET", "POST"}:
            for client in (anonymous, half):
                r = client.request(method, url, data={"csrf": "x"})
                # Redirected to sign-in, or refused before the page runs (bad form): never shown.
                assert r.status_code in (303, 400, 422), (method, url, r.status_code)
                if method == "GET" or r.status_code == 303:
                    assert r.headers["location"] == "/login", (method, url)
                checked += 1
    assert checked > 60


# ----------------------------------------------------------------- demo mode and settings


def test_demo_mode_works_only_without_passwords(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env = make_env(tmp_path)
    try:
        demo = dataclasses.replace(env.c.settings, demo_mode=True)
        app = create_app(demo, container=env.c)
        c = TestClient(app, follow_redirects=False)
        page = c.get("/login")
        assert "DEMO MODE" in page.text and "Priya" in page.text
        r = c.post("/login", data={"user_id": env.ids["dana"], "csrf": csrf_of(page.text)})
        assert r.headers["location"] == "/"
        users = c.get("/users")
        assert users.status_code == 200
        blocked = c.post(
            f"/users/{env.ids['priya']}/reset-password", data={"csrf": csrf_of(users.text)}
        )
        assert blocked.status_code == 303 and "demo mode" in c.get("/users").text
        give_sign_in(env, "priya")
        with pytest.raises(RuntimeError, match="nobody has a password"):
            create_app(demo, container=env.c)
    finally:
        env.c.engine.dispose()


def test_real_sign_in_needs_a_configured_session_secret(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from opsapp.config import load_settings

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"OPSAPP_DATABASE_PATH={tmp_path / 'x.sqlite'}\n"
        "OPSAPP_SESSION_SECRET=change-me-to-a-random-string\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file)
    assert settings.session_secret_generated
    with pytest.raises(RuntimeError, match="OPSAPP_SESSION_SECRET"):
        create_app(settings)
    env_file.write_text(env_file.read_text() + "OPSAPP_DEMO_MODE=maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DEMO_MODE"):
        load_settings(env_file)


# ----------------------------------------------------------------- hardening


def test_security_headers_and_size_limit(web) -> None:  # type: ignore[no-untyped-def]
    _env, app = web
    c = TestClient(app, follow_redirects=False)
    r = c.get("/login")
    assert "script-src 'none'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    cookie = r.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    big = c.post("/login", content=b"x" * 400_000, headers={"content-type": "text/plain"})
    assert big.status_code == 413


def test_templates_use_no_inline_styles_or_scripts() -> None:
    from pathlib import Path

    import opsapp.web

    folder = Path(opsapp.web.__file__).parent / "templates"
    for page in folder.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "style=" not in text and "<script" not in text and "onclick" not in text, page.name
