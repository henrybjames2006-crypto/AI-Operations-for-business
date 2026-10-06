"""FastAPI web app: server-rendered pages, plain HTML forms, no JavaScript required.

Permission checks live in the workflow service. The web layer only decides which buttons
to show; hiding a button is a convenience, never a control.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from ..authz import load_actor, require
from ..clock import Clock
from ..config import Settings
from ..container import Container, build
from ..domain.errors import CatalogImportError, DomainError, NotFound, PermissionDenied
from ..domain.money import format_usd
from ..domain.roles import ROLE_TEXT, Permission, Role, has_permission
from ..persistence.models import Tenant, User
from ..redaction import redact
from ..workflow.catalog_import import MAX_BYTES as MAX_IMPORT_BYTES
from . import views
from .samples import SAMPLES

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent


class LoginRequired(Exception):
    pass


def create_app(
    settings: Settings, clock: Clock | None = None, container: Container | None = None
) -> FastAPI:
    c = container or build(settings, clock)
    app = FastAPI(
        title="Operations workflow prototype", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.container = c
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="opsapp_session",
        same_site="strict",
        https_only=False,
        max_age=8 * 3600,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(usd=format_usd, ROLE_TEXT=ROLE_TEXT)
    templates.env.filters["usd"] = lambda v: format_usd(Decimal(str(v))) if v is not None else ""
    # AI costs are fractions of a cent per request, so show four decimal places.
    templates.env.filters["usd4"] = lambda v: f"${Decimal(str(v)):.4f}" if v is not None else ""
    templates.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else ""

    # ------------------------------------------------------------------ helpers

    def current_user(request: Request) -> User:
        uid = request.session.get("user_id")
        if not uid:
            raise LoginRequired()
        with c.read_sf() as s:
            try:
                user = load_actor(s, uid)
            except PermissionDenied as exc:
                request.session.clear()
                raise LoginRequired() from exc
            s.expunge(user)
            return user

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
        request.session.setdefault("flash", []).append({"kind": kind, "message": message})

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
        except LoginRequired:
            return None

    # ------------------------------------------------------------------ sign-in

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        with c.read_sf() as s:
            tenants = list(s.scalars(select(Tenant).order_by(Tenant.name)))
            users = list(
                s.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))
            )
        return render(request, "login.html", {"tenants": tenants, "users": users})

    @app.post("/login")
    def login(request: Request, user_id: str = Form(...), csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        with c.read_sf() as s:
            user = s.get(User, user_id)
            if user is None or not user.is_active:
                raise HTTPException(400, "Unknown user")
        request.session.clear()
        request.session["user_id"] = user_id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")) -> Response:
        check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

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

    return app
