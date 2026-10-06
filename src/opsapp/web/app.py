"""FastAPI web app: server-rendered pages, plain HTML forms, no JavaScript required.

Permission checks live in the workflow service. The web layer only decides which buttons
to show; hiding a button is a convenience, never a control.
"""

from __future__ import annotations

import base64
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..auth import totp
from ..auth.service import IDLE_MINUTES, PENDING_MINUTES, any_password_set
from ..authz import load_actor, require
from ..clock import Clock
from ..config import Settings
from ..container import Container, build
from ..domain.errors import (
    CatalogImportError,
    DomainError,
    NotFound,
    PermissionDenied,
    SignInFailed,
)
from ..domain.money import format_usd
from ..domain.roles import ROLE_TEXT, Permission, Role, has_permission
from ..persistence.models import Tenant, User
from ..redaction import redact
from ..workflow import audit_export, measures
from ..workflow.catalog_import import MAX_BYTES as MAX_IMPORT_BYTES
from ..workflow.customer_import import MAX_BYTES as MAX_CUSTOMER_BYTES
from ..workflow.customer_import import TEMPLATE as CUSTOMER_TEMPLATE
from . import views
from .hardening import SecurityHeaders, SizeLimit
from .samples import SAMPLES

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent


class LoginRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    pass


# Pages a user who must change their password can still reach.
_PASSWORD_CHANGE_PATHS = {"/account/password", "/logout"}


def create_app(
    settings: Settings, clock: Clock | None = None, container: Container | None = None
) -> FastAPI:
    c = container or build(settings, clock)
    if settings.demo_mode and any_password_set(c.read_sf):
        raise RuntimeError(
            "Demo mode is only for databases where nobody has a password. Use a separate "
            "database for the demo (OPSAPP_DATABASE_PATH=data/demo.sqlite) or turn "
            "OPSAPP_DEMO_MODE off."
        )
    if not settings.demo_mode and settings.session_secret_generated:
        raise RuntimeError(
            "Set OPSAPP_SESSION_SECRET in .env to your own random value (see .env.example). "
            "Sign-in needs it."
        )
    app = FastAPI(
        title="Operations workflow prototype", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.container = c
    # Order: the last added runs first. Size limits apply before anything reads the body.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="opsapp_session",
        same_site="strict",
        # The app is only served on http://127.0.0.1, so the cookie can't require HTTPS.
        # A hosted version must set this to True.
        https_only=False,
        max_age=8 * 3600,
    )
    app.add_middleware(SecurityHeaders)
    app.add_middleware(SizeLimit, max_bytes=MAX_IMPORT_BYTES + 100_000)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(usd=format_usd, ROLE_TEXT=ROLE_TEXT, demo_mode=settings.demo_mode)
    templates.env.filters["usd"] = lambda v: format_usd(Decimal(str(v))) if v is not None else ""
    # AI costs are fractions of a cent per request, so show four decimal places.
    templates.env.filters["usd4"] = lambda v: f"${Decimal(str(v)):.4f}" if v is not None else ""
    templates.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else ""

    # ------------------------------------------------------------------ helpers

    def current_user(request: Request) -> User:
        uid = c.auth.session_user(str(request.session.get("sid", "")))
        if not uid:
            if request.session.get("sid"):
                request.session.clear()
                flash(
                    request,
                    f"You were signed out (after {IDLE_MINUTES} minutes without activity, at "
                    "the end of the day, or by an owner). Sign in again.",
                    "warn",
                )
            raise LoginRequired()
        with c.read_sf() as s:
            try:
                user = load_actor(s, uid)
            except PermissionDenied as exc:
                request.session.clear()
                raise LoginRequired() from exc
            s.expunge(user)
        if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_PATHS:
            raise PasswordChangeRequired()
        return user

    def pending_user(request: Request) -> str:
        """The user between the password step and the code step, if still in time."""
        uid = request.session.get("pending_user")
        at = request.session.get("pending_at")
        if not uid or not at:
            raise LoginRequired()
        started = datetime.fromisoformat(str(at))
        if c.clock.now() - started > timedelta(minutes=PENDING_MINUTES):
            request.session.clear()
            flash(request, "That took too long. Sign in again.", "warn")
            raise LoginRequired()
        return str(uid)

    def finish_sign_in(request: Request, user_id: str, *, demo: bool = False) -> None:
        token = c.auth.start_session(user_id, demo=demo)
        flashes = request.session.get("flash", [])
        # A new cookie session at sign-in, so nothing from before carries over.
        request.session.clear()
        request.session["sid"] = token
        if flashes:
            request.session["flash"] = flashes

    def csrf_token(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = secrets.token_urlsafe(24)
            request.session["csrf"] = tok
        return str(tok)

    def check_csrf(request: Request, token: str) -> None:
        if not token or not secrets.compare_digest(token, str(request.session.get("csrf", ""))):
            raise HTTPException(
                status_code=400, detail="Form expired. Reload the page and try again."
            )

    def flash(request: Request, message: str, kind: str = "info") -> None:
        # Reassigned, not appended in place: the session is only saved when a key is set.
        request.session["flash"] = [
            *request.session.get("flash", []),
            {"kind": kind, "message": message},
        ]

    def render(
        request: Request,
        name: str,
        ctx: dict[str, Any],
        status: int = 200,
        user: User | None = None,
    ) -> HTMLResponse:
        tenant_name = ""
        if user is not None:
            with c.read_sf() as s:
                t = s.get(Tenant, user.tenant_id)
                tenant_name = t.name if t else ""
        messages = request.session.pop("flash", [])
        return templates.TemplateResponse(
            request,
            name,
            {
                **ctx,
                "user": user,
                "tenant_name": tenant_name,
                "csrf": csrf_token(request),
                "messages": messages,
                "Permission": Permission,
                "perm": (lambda p: has_permission(Role(user.role), p))
                if user
                else (lambda p: False),
            },
            status_code=status,
        )

    def act(
        request: Request,
        user: User,
        fn: Callable[[], Any],
        redirect_to: str,
        success: str | Callable[[Any], str] | None = None,
    ) -> Response:
        """Run a use case; domain errors become a message on the page they came from."""
        try:
            result = fn()
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse(redirect_to, status_code=303)
        if success:
            flash(request, success if not callable(success) else success(result), "ok")
        return RedirectResponse(redirect_to, status_code=303)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(PasswordChangeRequired)
    async def _password_change(request: Request, _exc: PasswordChangeRequired) -> Response:
        return RedirectResponse("/account/password", status_code=303)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> Response:
        return render(
            request,
            "error.html",
            {"title": "Not found", "detail": "That record does not exist."},
            status=404,
            user=_maybe_user(request),
        )

    @app.exception_handler(PermissionDenied)
    async def _forbidden(request: Request, exc: PermissionDenied) -> Response:
        return render(
            request,
            "error.html",
            {"title": "Not allowed", "detail": str(exc)},
            status=403,
            user=_maybe_user(request),
        )

    def _maybe_user(request: Request) -> User | None:
        try:
            return current_user(request)
        except LoginRequired, PasswordChangeRequired:
            return None

    # ------------------------------------------------------------------ sign-in

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        if not settings.demo_mode:
            return render(request, "login.html", {"email": ""})
        with c.read_sf() as s:
            tenants = list(s.scalars(select(Tenant).order_by(Tenant.name)))
            users = list(
                s.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
            )
        return render(request, "login_demo.html", {"tenants": tenants, "users": users})

    @app.post("/login")
    def login(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        user_id: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        check_csrf(request, csrf)
        if settings.demo_mode:
            with c.read_sf() as s:
                user = s.get(User, user_id)
                if user is None or not user.is_active or user.password_hash is not None:
                    raise HTTPException(400, "Unknown user")
            finish_sign_in(request, user_id, demo=True)
            return RedirectResponse("/", status_code=303)
        try:
            ok = c.auth.check_password(email, password)
        except SignInFailed as exc:
            return render(request, "login.html", {"email": email, "error": str(exc)}, status=400)
        request.session.clear()
        request.session["pending_user"] = ok.user_id
        request.session["pending_at"] = c.clock.now().isoformat()
        return RedirectResponse(
            "/login/setup" if ok.needs_setup else "/login/code", status_code=303
        )

    @app.get("/login/code", response_class=HTMLResponse)
    def code_page(request: Request) -> Response:
        pending_user(request)
        return render(request, "login_code.html", {})

    @app.post("/login/code")
    def code_submit(request: Request, code: str = Form(""), csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        uid = pending_user(request)
        try:
            used_recovery = c.auth.check_second_factor(uid, code)
        except SignInFailed as exc:
            return render(request, "login_code.html", {"error": str(exc)}, status=400)
        if used_recovery:
            flash(
                request,
                "You signed in with a recovery code, which can't be used again. If you lost "
                "your phone, ask an owner to reset your authenticator app.",
                "warn",
            )
        finish_sign_in(request, uid)
        return RedirectResponse("/", status_code=303)

    @app.get("/login/setup", response_class=HTMLResponse)
    def setup_page(request: Request) -> Response:
        uid = pending_user(request)
        try:
            secret, account = c.auth.totp_setup_secret(uid)
        except DomainError:
            request.session.clear()
            raise LoginRequired() from None
        uri = totp.provisioning_uri(secret, account)
        return render(
            request,
            "login_setup.html",
            {"qr": totp.qr_svg_data_uri(uri), "key": totp.grouped(secret)},
        )

    @app.post("/login/setup")
    def setup_submit(request: Request, code: str = Form(""), csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        uid = pending_user(request)
        try:
            codes = c.auth.confirm_totp(uid, code)
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/login/setup", status_code=303)
        finish_sign_in(request, uid)
        user = _maybe_user(request)
        return render(request, "recovery_codes.html", {"codes": codes, "first": True}, user=user)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        token = request.session.get("sid")
        if token:
            c.auth.end_session(str(token))
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------------ own account

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request) -> Response:
        user = current_user(request)
        return render(request, "account.html", {}, user=user)

    @app.get("/account/password", response_class=HTMLResponse)
    def password_page(request: Request) -> Response:
        user = current_user(request)
        return render(request, "password.html", {}, user=user)

    @app.post("/account/password")
    def password_submit(
        request: Request,
        current: str = Form(""),
        new: str = Form(""),
        repeat: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if new != repeat:
            flash(request, "The two new passwords are not the same.", "error")
            return RedirectResponse("/account/password", status_code=303)
        try:
            c.auth.change_password(user.id, current, new, keep_token=request.session.get("sid"))
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/account/password", status_code=303)
        flash(request, "Password changed. Other browsers signed in as you were signed out.", "ok")
        return RedirectResponse("/", status_code=303)

    @app.post("/account/recovery-codes")
    def recovery_codes(request: Request, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if user.totp_confirmed_at is None:
            raise PermissionDenied("Set up an authenticator app first.")
        codes = c.auth.new_recovery_codes(user.id)
        return render(request, "recovery_codes.html", {"codes": codes, "first": False}, user=user)

    # ------------------------------------------------------------------ owner: users

    @app.get("/users", response_class=HTMLResponse)
    def users_page(request: Request) -> Response:
        user = current_user(request)
        rows = c.auth.list_users(user.id)
        return render(request, "users.html", {"rows": rows, "roles": list(Role)}, user=user)

    def no_user_changes_in_demo(request: Request) -> Response | None:
        if not settings.demo_mode:
            return None
        flash(
            request,
            "Users can't be changed in demo mode. Turn demo mode off to use real sign-in.",
            "error",
        )
        return RedirectResponse("/users", status_code=303)

    def show_temporary(request: Request, user: User, who: str, temp: str) -> Response:
        return render(request, "temporary_password.html", {"who": who, "temp": temp}, user=user)

    @app.post("/users")
    def add_user(
        request: Request,
        display_name: str = Form(""),
        email: str = Form(""),
        role: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if (blocked := no_user_changes_in_demo(request)) is not None:
            return blocked
        try:
            temp = c.auth.add_user(user.id, display_name, email, role)
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/users", status_code=303)
        return show_temporary(request, user, display_name.strip(), temp)

    @app.post("/users/{user_id}/reset-password")
    def reset_password(request: Request, user_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if (blocked := no_user_changes_in_demo(request)) is not None:
            return blocked
        try:
            temp = c.auth.reset_password(user.id, user_id)
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/users", status_code=303)
        with c.read_sf() as s:
            target = s.get(User, user_id)
            name = target.display_name if target else ""
        return show_temporary(request, user, name, temp)

    @app.post("/users/{user_id}/reset-second-factor")
    def reset_second_factor(request: Request, user_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if (blocked := no_user_changes_in_demo(request)) is not None:
            return blocked
        return act(
            request,
            user,
            lambda: c.auth.reset_second_factor(user.id, user_id),
            "/users",
            "Authenticator app reset. They set it up again at their next sign-in, and their "
            "sessions were ended.",
        )

    @app.post("/users/{user_id}/disable")
    def disable_user(request: Request, user_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if (blocked := no_user_changes_in_demo(request)) is not None:
            return blocked
        return act(
            request,
            user,
            lambda: c.auth.set_active(user.id, user_id, False),
            "/users",
            "User disabled and signed out everywhere.",
        )

    @app.post("/users/{user_id}/enable")
    def enable_user(request: Request, user_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        if (blocked := no_user_changes_in_demo(request)) is not None:
            return blocked
        return act(
            request,
            user,
            lambda: c.auth.set_active(user.id, user_id, True),
            "/users",
            "User enabled.",
        )

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.dashboard(s, user)
        return render(request, "dashboard.html", data, user=user)

    @app.get("/requests/new", response_class=HTMLResponse)
    def new_request(request: Request, sample: str = "") -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            require(load_actor(s, user.id), Permission.CREATE_REQUEST)
        chosen = SAMPLES.get(sample, {"sender": "", "text": ""})
        return render(
            request,
            "new_request.html",
            {
                "submission_key": uuid.uuid4().hex,
                "samples": SAMPLES,
                "chosen": chosen,
            },
            user=user,
        )

    @app.post("/requests")
    def submit_request(
        request: Request,
        text: str = Form(""),
        sender: str = Form(""),
        submission_key: str = Form(""),
        allow_duplicate: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        try:
            result = c.service.submit_request(
                user.id, text, sender, submission_key, allow_duplicate=bool(allow_duplicate)
            )
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/requests/new", status_code=303)
        if result.duplicate:
            flash(
                request,
                "This request was already submitted, so no new workflow was "
                "created. Showing the existing one.",
                "warn",
            )
        return RedirectResponse(f"/workflows/{result.workflow_id}", status_code=303)

    @app.get("/workflows", response_class=HTMLResponse)
    def workflows(request: Request, state: str = "") -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            rows = views.workflow_rows(s, user, state or None)
        return render(
            request,
            "workflows.html",
            {"rows": rows, "filter": state, "states": views.LABEL},
            user=user,
        )

    @app.get("/workflows/{wf_id}", response_class=HTMLResponse)
    def workflow(request: Request, wf_id: str) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.workflow_detail(s, c.service, user.id, wf_id)
            return render(request, "workflow.html", data, user=user)

    @app.get("/workflows/{wf_id}/export.json")
    def export(request: Request, wf_id: str) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.workflow_export(s, c.service, wf_id, actor_id=user.id)
        return JSONResponse(
            redact(data), headers={"Content-Disposition": f'attachment; filename="{wf_id}.json"'}
        )

    def wf_url(wf_id: str) -> str:
        return f"/workflows/{wf_id}"

    @app.post("/workflows/{wf_id}/answer/{q_id}")
    def answer(
        request: Request,
        wf_id: str,
        q_id: str,
        answer: str = Form(""),
        quantity: str = Form(""),
        version: int = Form(...),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.answer_question(
                user.id, wf_id, q_id, answer, quantity or None, expected_version=version
            ),
            wf_url(wf_id),
            "Answer recorded.",
        )

    @app.post("/workflows/{wf_id}/edit")
    async def edit(request: Request, wf_id: str) -> Response:
        user = current_user(request)
        form = await request.form()
        check_csrf(request, str(form.get("csrf", "")))
        lines = []
        for i in range(12):
            sku = str(form.get(f"sku_{i}", "")).strip()
            qty = str(form.get(f"qty_{i}", "")).strip()
            if sku:
                lines.append((sku, qty))
        return act(
            request,
            user,
            lambda: c.service.edit_quote(
                user.id,
                wf_id,
                lines=lines,
                recipient=str(form.get("recipient", "")),
                site_id=str(form.get("site_id", "")),
                timeframe=str(form.get("timeframe", "")) or None,
                customer_id=str(form.get("customer_id", "")) or None,
                reason=str(form.get("reason", "")),
                expected_version=int(str(form.get("version"))),
            ),
            wf_url(wf_id),
            "Quote updated. A new quote version was created.",
        )

    @app.post("/workflows/{wf_id}/submit")
    def submit(
        request: Request, wf_id: str, version: int = Form(...), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.submit_for_approval(user.id, wf_id, expected_version=version),
            wf_url(wf_id),
            "Submitted for approval. Nothing runs until an approver approves.",
        )

    @app.post("/workflows/{wf_id}/approve")
    def approve(
        request: Request,
        wf_id: str,
        subject_hash: str = Form(...),
        note: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.approve(user.id, wf_id, subject_hash, note),
            wf_url(wf_id),
            "Approved. The simulated actions are queued.",
        )

    @app.post("/workflows/{wf_id}/reject")
    def reject(
        request: Request,
        wf_id: str,
        subject_hash: str = Form(...),
        reason: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.reject(user.id, wf_id, subject_hash, reason),
            wf_url(wf_id),
            "Rejected.",
        )

    @app.post("/workflows/{wf_id}/revise")
    def revise(request: Request, wf_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.revise(user.id, wf_id),
            wf_url(wf_id),
            "Reopened for revision.",
        )

    @app.post("/workflows/{wf_id}/cancel")
    def cancel(
        request: Request, wf_id: str, reason: str = Form(""), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)

        def msg(summary: Any) -> str:
            parts = ["Canceled."]
            if summary.voided:
                parts.append(f"{len(summary.voided)} queued action(s) will not run.")
            if summary.already_happened:
                parts.append(
                    "Already happened and NOT undone: " + "; ".join(summary.already_happened) + "."
                )
            if summary.outcome_unknown:
                parts.append(
                    "Outcome unknown and NOT undone: " + "; ".join(summary.outcome_unknown) + "."
                )
            return " ".join(parts)

        return act(
            request, user, lambda: c.service.cancel(user.id, wf_id, reason), wf_url(wf_id), msg
        )

    @app.post("/workflows/{wf_id}/resolve")
    def resolve(
        request: Request,
        wf_id: str,
        resolution: str = Form(...),
        note: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.resolve_escalation(user.id, wf_id, resolution, note),
            wf_url(wf_id),
            "Decision recorded.",
        )

    @app.post("/workflows/{wf_id}/retry")
    def retry(request: Request, wf_id: str, note: str = Form(""), csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.retry_failed(user.id, wf_id, note),
            wf_url(wf_id),
            "Retry queued.",
        )

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            rows = views.approvals_queue(s, c.service, user)
        return render(request, "approvals.html", {"rows": rows}, user=user)

    @app.get("/exceptions", response_class=HTMLResponse)
    def exceptions(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            rows = views.exceptions_list(s, user)
        return render(request, "exceptions.html", {"rows": rows}, user=user)

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.catalog_view(s, user)
        return render(request, "catalog.html", data, user=user)

    @app.get("/measurements", response_class=HTMLResponse)
    def measurements(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            rows = measures.measure(s, user.tenant_id)
        return render(
            request,
            "measurements.html",
            {"rows": rows, "summary": measures.summarize(rows), "stages": measures.STAGES},
            user=user,
        )

    @app.get("/measurements.csv")
    def measurements_csv(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            body = measures.to_csv(measures.measure(s, user.tenant_id))
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="measurements.csv"'},
        )

    @app.get("/catalog/template.csv")
    def catalog_template(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            body = views.catalog_csv(s, user)
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="price-list.csv"'},
        )

    @app.post("/catalog/import")
    async def catalog_import(
        request: Request, file: UploadFile = File(...), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        data = await file.read(MAX_IMPORT_BYTES + 1)
        try:
            number = c.service.import_catalog(user.id, data, file.filename or "")
        except CatalogImportError as exc:
            with c.read_sf() as s:
                ctx = views.catalog_view(s, user)
            ctx["import_problems"] = exc.problems
            return render(request, "catalog.html", ctx, status=422, user=user)
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/catalog", status_code=303)
        flash(
            request,
            f"Imported as draft pricing version {number}. Check it below, then approve it "
            f"to start using it.",
            "ok",
        )
        return RedirectResponse("/catalog", status_code=303)

    @app.post("/catalog/pricing/{pv_id}/discard")
    def discard_pricing(request: Request, pv_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.discard_pricing_version(user.id, pv_id),
            "/catalog",
            "Draft discarded. Nothing was changed.",
        )

    @app.post("/catalog/pricing/{pv_id}/approve")
    def approve_pricing(request: Request, pv_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.approve_pricing_version(user.id, pv_id),
            "/catalog",
            "Pricing version approved. New quotes will use it.",
        )

    @app.post("/catalog/rules")
    def add_rule(
        request: Request,
        kind: str = Form(""),
        sku: str = Form(""),
        min_qty: str = Form(""),
        percent_off: str = Form(""),
        amount: str = Form(""),
        label: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        form = {
            "sku": sku,
            "min_qty": min_qty,
            "percent_off": percent_off,
            "amount": amount,
            "label": label,
        }
        return act(
            request,
            user,
            lambda: c.firm.add_pricing_rule(user.id, kind, form),
            "/catalog",
            lambda n: f"Rule saved in draft pricing version {n}. Approve the draft to use it.",
        )

    @app.post("/catalog/rules/{position}/remove")
    def remove_rule(request: Request, position: int, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.remove_pricing_rule(user.id, position),
            "/catalog",
            lambda n: f"Rule removed in draft pricing version {n}. Approve the draft to use it.",
        )

    # ------------------------------------------------------------------ customers

    @app.get("/customers", response_class=HTMLResponse)
    def customers(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.customers_view(s, user)
        return render(request, "customers.html", data, user=user)

    @app.get("/customers/template.csv")
    def customers_template(request: Request) -> Response:
        current_user(request)
        return Response(
            CUSTOMER_TEMPLATE,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="customers.csv"'},
        )

    @app.post("/customers")
    def add_customer(
        request: Request,
        name: str = Form(""),
        other_names: str = Form(""),
        email_domains: str = Form(""),
        contact_email: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        try:
            customer_id = c.firm.save_customer(
                user.id, None, name, other_names, email_domains, contact_email
            )
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/customers", status_code=303)
        flash(request, "Customer added. Now add its sites.", "ok")
        return RedirectResponse(f"/customers/{customer_id}", status_code=303)

    def customer_preview(request: Request, user: User, ctx: dict[str, Any]) -> Response:
        with c.read_sf() as s:
            page = views.customers_view(s, user)
        return render(request, "customers.html", {**page, **ctx}, status=422, user=user)

    @app.post("/customers/import")
    async def customers_import(
        request: Request, file: UploadFile = File(...), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        data = await file.read(MAX_CUSTOMER_BYTES + 1)
        try:
            checked = c.firm.check_customer_import(user.id, data)
        except CatalogImportError as exc:
            return customer_preview(request, user, {"import_problems": exc.problems})
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/customers", status_code=303)
        return render(
            request,
            "customers_preview.html",
            {
                **checked,
                "payload": base64.b64encode(data).decode("ascii"),
                "filename": file.filename or "",
            },
            user=user,
        )

    @app.post("/customers/import/confirm")
    def customers_import_confirm(
        request: Request,
        payload: str = Form(""),
        sha256: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        try:
            data = base64.b64decode(payload, validate=True)
        except ValueError:
            flash(request, "The preview was damaged. Upload the file again.", "error")
            return RedirectResponse("/customers", status_code=303)
        try:
            count = c.firm.import_customers(user.id, data, sha256)
        except CatalogImportError as exc:
            return customer_preview(request, user, {"import_problems": exc.problems})
        except NotFound, PermissionDenied:
            raise
        except DomainError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/customers", status_code=303)
        flash(request, f"{count} customers imported.", "ok")
        return RedirectResponse("/customers", status_code=303)

    @app.get("/customers/{customer_id}", response_class=HTMLResponse)
    def customer_page(request: Request, customer_id: str) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.customer_detail(s, user, customer_id)
        return render(request, "customer.html", data, user=user)

    @app.post("/customers/{customer_id}")
    def edit_customer(
        request: Request,
        customer_id: str,
        name: str = Form(""),
        other_names: str = Form(""),
        email_domains: str = Form(""),
        contact_email: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.save_customer(
                user.id, customer_id, name, other_names, email_domains, contact_email
            ),
            f"/customers/{customer_id}",
            "Customer details saved.",
        )

    @app.post("/customers/{customer_id}/deactivate")
    def deactivate_customer(request: Request, customer_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.set_customer_active(user.id, customer_id, False),
            f"/customers/{customer_id}",
            "Customer deactivated. Its past quotes are unchanged.",
        )

    @app.post("/customers/{customer_id}/reactivate")
    def reactivate_customer(request: Request, customer_id: str, csrf: str = Form("")) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.set_customer_active(user.id, customer_id, True),
            f"/customers/{customer_id}",
            "Customer reactivated.",
        )

    @app.post("/customers/{customer_id}/sites")
    def add_site(
        request: Request,
        customer_id: str,
        label: str = Form(""),
        address: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.save_site(user.id, customer_id, None, label, address),
            f"/customers/{customer_id}",
            "Site added.",
        )

    @app.post("/customers/{customer_id}/sites/{site_id}")
    def edit_site(
        request: Request,
        customer_id: str,
        site_id: str,
        label: str = Form(""),
        address: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.save_site(user.id, customer_id, site_id, label, address),
            f"/customers/{customer_id}",
            "Site saved.",
        )

    @app.post("/customers/{customer_id}/sites/{site_id}/remove")
    def remove_site(
        request: Request, customer_id: str, site_id: str, csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.remove_site(user.id, site_id),
            f"/customers/{customer_id}",
            "Site removed. Requests and quotes that already use it keep it.",
        )

    # ------------------------------------------------------------------ company

    @app.get("/company", response_class=HTMLResponse)
    def company(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.company_view(s, user)
        return render(request, "company.html", data, user=user)

    @app.post("/company")
    def company_save(
        request: Request,
        name: str = Form(""),
        timezone: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.firm.update_company(user.id, name, timezone),
            "/company",
            "Company settings saved.",
        )

    @app.get("/simulation", response_class=HTMLResponse)
    def simulation(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.simulation_view(s, user)
        return render(request, "simulation.html", data, user=user)

    @app.post("/simulation/fault")
    def add_fault(
        request: Request, adapter: str = Form(...), mode: str = Form(...), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        return act(
            request,
            user,
            lambda: c.service.queue_fault(user.id, adapter, mode),
            "/simulation",
            "Fault queued for the next call.",
        )

    @app.post("/simulation/dispatch")
    def dispatch_once(
        request: Request, back: str = Form("/simulation"), csrf: str = Form("")
    ) -> Response:
        user = current_user(request)
        check_csrf(request, csrf)
        with c.read_sf() as s:
            require(load_actor(s, user.id), Permission.SIMULATION_CONTROLS)
        steps = c.dispatcher(worker_id="web-run-once").run_once()
        flash(request, f"Dispatcher ran once: {steps} step(s).", "ok")
        return RedirectResponse(back if back.startswith("/") else "/simulation", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_log(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            data = views.audit_view(s, user)
        return render(request, "audit.html", data, user=user)

    @app.get("/audit/export.json")
    def audit_export_json(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            require(load_actor(s, user.id), Permission.EXPORT_AUDIT)
            body = audit_export.to_json(s, user.tenant_id, c.clock.now())
        return Response(
            body,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit-log.json"'},
        )

    @app.get("/audit/export.csv")
    def audit_export_csv(request: Request) -> Response:
        user = current_user(request)
        with c.read_sf() as s:
            require(load_actor(s, user.id), Permission.EXPORT_AUDIT)
            body = audit_export.to_csv(s, user.tenant_id)
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
        )

    return app
