"""Sign-in, sessions and user management.

Every rule here is enforced on the server. Failed sign-ins, lockouts, sign-ins, sign-outs
and every change an owner makes to a user are written to the tenant's audit log, without
passwords or codes.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..authz import get_scoped, load_actor, require
from ..clock import Clock
from ..domain.errors import Conflict, NotFound, SignInFailed, ValidationError
from ..domain.roles import Permission, Role
from ..ids import new_id
from ..persistence.models import AuthSession, RecoveryCode, Tenant, User
from ..workflow import audit
from . import totp
from .passwords import hash_password, password_problems, temporary_password, verify_password

log = logging.getLogger(__name__)

MAX_FAILED = 5
LOCK_MINUTES = 15
IDLE_MINUTES = 30
MAX_SESSION_HOURS = 8
PENDING_MINUTES = 5
RECOVERY_CODES = 10
_SEEN_EVERY = timedelta(seconds=60)

FAILED_MESSAGE = (
    "Sign-in failed. Check your details and try again. After 5 wrong tries an account is "
    "locked for 15 minutes."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _recovery_code() -> str:
    raw = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


@dataclass(frozen=True)
class PasswordOk:
    user_id: str
    needs_setup: bool  # True when no authenticator app is set up yet


@dataclass(frozen=True)
class UserRow:
    id: str
    display_name: str
    email: str
    role: str
    is_active: bool
    has_password: bool
    second_factor: bool
    locked: bool
    must_change_password: bool


class AuthService:
    def __init__(self, sf: sessionmaker[Session], clock: Clock) -> None:
        self.sf = sf
        self.clock = clock

    # ------------------------------------------------------------------ helpers

    def _audit(
        self, s: Session, user: User, event_type: str, message: str, actor: str | None = None
    ) -> None:
        audit.append(
            s,
            tenant_id=user.tenant_id,
            event_type=event_type,
            message=message,
            at=self.clock.now(),
            actor_type="system" if actor == "system" else "user",
            actor_id=None if actor == "system" else (actor or user.id),
            data={"user_id": user.id},
        )

    def _fail(self, s: Session, user: User, what: str) -> None:
        now = self.clock.now()
        user.failed_logins = (user.failed_logins or 0) + 1
        self._audit(s, user, "sign_in_failed", f"Sign-in failed for {user.display_name}: {what}.")
        if user.failed_logins >= MAX_FAILED:
            user.failed_logins = 0
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            self._audit(
                s,
                user,
                "account_locked",
                f"{user.display_name} locked for {LOCK_MINUTES} minutes after "
                f"{MAX_FAILED} failed sign-in attempts.",
            )

    def _locked(self, user: User) -> bool:
        return user.locked_until is not None and user.locked_until > self.clock.now()

    def _revoke_all(self, s: Session, user_id: str, reason: str) -> None:
        s.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=self.clock.now(), revoked_reason=reason)
        )

    def _new_recovery_codes(self, s: Session, user: User) -> list[str]:
        for old in s.scalars(select(RecoveryCode).where(RecoveryCode.user_id == user.id)):
            s.delete(old)
        codes = [_recovery_code() for _ in range(RECOVERY_CODES)]
        for code in codes:
            s.add(
                RecoveryCode(
                    id=new_id("recovery_code"),
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    code_sha256=_sha(code),
                    created_at=self.clock.now(),
                )
            )
        return codes

    # ------------------------------------------------------------------ sign-in

    def check_password(self, email: str, password: str) -> PasswordOk:
        """First step. Raises SignInFailed with the same message whatever was wrong."""
        with self.sf.begin() as s:
            user = s.scalars(select(User).where(User.email == _normalize_email(email))).first()
            ok = verify_password(password, user.password_hash if user else None)
            if user is None or not user.password_hash:
                log.info("sign-in failed: unknown email or no password set")
                raise SignInFailed(FAILED_MESSAGE)
            result: PasswordOk | None = None
            if not user.is_active or self._locked(user):
                why = "account disabled" if not user.is_active else "account locked"
                self._audit(
                    s, user, "sign_in_failed", f"Sign-in refused for {user.display_name}: {why}."
                )
            elif not ok:
                self._fail(s, user, "wrong password")
            else:
                result = PasswordOk(user.id, needs_setup=user.totp_confirmed_at is None)
        if result is None:
            raise SignInFailed(FAILED_MESSAGE)
        return result

    def check_second_factor(self, user_id: str, code: str) -> bool:
        """Second step. Returns True when a recovery code was used."""
        code = code.strip()
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            ready = (
                user is not None
                and user.is_active
                and not self._locked(user)
                and user.totp_secret is not None
                and user.totp_confirmed_at is not None
            )
            if user is None or not ready or user.totp_secret is None:
                raise SignInFailed(FAILED_MESSAGE)
            used_recovery = False
            step = totp.matching_step(user.totp_secret, code, self.clock.now(), user.totp_last_step)
            if step is not None:
                user.totp_last_step = step
            elif self._use_recovery_code(s, user, code):
                used_recovery = True
            else:
                self._fail(s, user, "wrong authenticator code")
            failed = step is None and not used_recovery
            if not failed:
                user.failed_logins = 0
                user.locked_until = None
        if failed:
            raise SignInFailed(FAILED_MESSAGE)
        return used_recovery

    def _use_recovery_code(self, s: Session, user: User, code: str) -> bool:
        rc = s.scalars(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_sha256 == _sha(code.lower()),
                RecoveryCode.used_at.is_(None),
            )
        ).first()
        if rc is None:
            return False
        rc.used_at = self.clock.now()
        left = s.scalar(
            select(func.count())
            .select_from(RecoveryCode)
            .where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
        )
        self._audit(
            s,
            user,
            "recovery_code_used",
            f"{user.display_name} signed in with a recovery code ({left} left).",
        )
        return True

    def totp_setup_secret(self, user_id: str) -> tuple[str, str]:
        """Secret and account label for a user setting up an authenticator app."""
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None or not user.is_active:
                raise SignInFailed(FAILED_MESSAGE)
            if user.totp_confirmed_at is not None:
                raise Conflict("An authenticator app is already set up for this account.")
            if user.totp_secret is None:
                user.totp_secret = totp.new_secret()
            return user.totp_secret, user.email

    def confirm_totp(self, user_id: str, code: str) -> list[str]:
        """Checks the first code from the app and returns new recovery codes (shown once)."""
        with self.sf.begin() as s:
            user = s.get(User, user_id)
            if user is None or not user.is_active or user.totp_secret is None:
                raise SignInFailed(FAILED_MESSAGE)
            if user.totp_confirmed_at is not None:
                raise Conflict("An authenticator app is already set up for this account.")
            step = totp.matching_step(user.totp_secret, code, self.clock.now(), None)
            if step is None:
                raise ValidationError(
                    "That code didn't match. Check the time on your phone is set automatically "
                    "and enter the newest code."
                )
            user.totp_confirmed_at = self.clock.now()
            user.totp_last_step = step
            codes = self._new_recovery_codes(s, user)
            self._audit(
                s, user, "second_factor_set_up", f"{user.display_name} set up an authenticator app."
            )
            return codes

    def new_recovery_codes(self, user_id: str) -> list[str]:
        with self.sf.begin() as s:
            user = load_actor(s, user_id)
            codes = self._new_recovery_codes(s, user)
            self._audit(
                s, user, "recovery_codes_replaced", f"{user.display_name} made new recovery codes."
            )
            return codes

    # ------------------------------------------------------------------ sessions

    def start_session(self, user_id: str, *, demo: bool = False) -> str:
        """Creates a session and returns the token for the cookie. Only its hash is kept."""
        token = secrets.token_urlsafe(32)
        now = self.clock.now()
        with self.sf.begin() as s:
            user = load_actor(s, user_id)
            s.add(
                AuthSession(
                    id=new_id("auth_session"),
                    token_sha256=_sha(token),
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(hours=MAX_SESSION_HOURS),
                )
            )
            how = "demo sign-in (no password)" if demo else "password and second factor"
            self._audit(s, user, "signed_in", f"{user.display_name} signed in ({how}).")
        return token

    def session_user(self, token: str) -> str | None:
        """The signed-in user for a cookie token, or None if the session is over."""
        if not token:
            return None
        now = self.clock.now()
        with self.sf.begin() as s:
            row = s.scalars(
                select(AuthSession).where(AuthSession.token_sha256 == _sha(token))
            ).first()
            if row is None or row.revoked_at is not None:
                return None
            user = s.get(User, row.user_id)
            reason = None
            if user is None or not user.is_active:
                reason = "user disabled"
            elif now >= row.expires_at:
                reason = "expired"
            elif now - row.last_seen_at >= timedelta(minutes=IDLE_MINUTES):
                reason = "idle"
            if reason:
                row.revoked_at = now
                row.revoked_reason = reason
                return None
            if now - row.last_seen_at >= _SEEN_EVERY:
                row.last_seen_at = now
            return row.user_id

    def end_session(self, token: str) -> None:
        with self.sf.begin() as s:
            row = s.scalars(
                select(AuthSession).where(AuthSession.token_sha256 == _sha(token))
            ).first()
            if row is None or row.revoked_at is not None:
                return
            row.revoked_at = self.clock.now()
            row.revoked_reason = "signed out"
            user = s.get(User, row.user_id)
            if user is not None:
                self._audit(s, user, "signed_out", f"{user.display_name} signed out.")

    # ------------------------------------------------------------------ own account

    def change_password(
        self, user_id: str, current: str, new: str, keep_token: str | None = None
    ) -> None:
        with self.sf.begin() as s:
            user = load_actor(s, user_id)
            if not verify_password(current, user.password_hash):
                raise ValidationError("Your current password is not right.")
            problems = password_problems(new, user.email, user.display_name)
            if verify_password(new, user.password_hash):
                problems.append("Choose a password different from the current one.")
            if problems:
                raise ValidationError(" ".join(problems))
            user.password_hash = hash_password(new)
            user.password_changed_at = self.clock.now()
            user.must_change_password = False
            # Sign out every other browser; keep this one.
            q = update(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
            )
            if keep_token:
                q = q.where(AuthSession.token_sha256 != _sha(keep_token))
            s.execute(q.values(revoked_at=self.clock.now(), revoked_reason="password changed"))
            self._audit(s, user, "password_changed", f"{user.display_name} changed their password.")

    def must_change_password(self, user_id: str) -> bool:
        with self.sf() as s:
            user = s.get(User, user_id)
            return bool(user and user.must_change_password)

    # ------------------------------------------------------------------ owner: users

    def list_users(self, actor_id: str) -> list[UserRow]:
        with self.sf() as s:
            actor = load_actor(s, actor_id)
            require(actor, Permission.MANAGE_USERS)
            users = s.scalars(
                select(User).where(User.tenant_id == actor.tenant_id).order_by(User.display_name)
            )
            return [
                UserRow(
                    id=u.id,
                    display_name=u.display_name,
                    email=u.email,
                    role=u.role,
                    is_active=u.is_active,
                    has_password=u.password_hash is not None,
                    second_factor=u.totp_confirmed_at is not None,
                    locked=self._locked(u),
                    must_change_password=u.must_change_password,
                )
                for u in users
            ]

    def add_user(self, actor_id: str, display_name: str, email: str, role: str) -> str:
        """Adds a user and returns their temporary password (shown once to the owner)."""
        name = display_name.strip()
        address = _normalize_email(email)
        if not name or len(name) > 200:
            raise ValidationError("Enter a name (up to 200 characters).")
        if "@" not in address or len(address) > 200 or " " in address:
            raise ValidationError("Enter a valid email address.")
        if role not in {r.value for r in Role}:
            raise ValidationError("Choose a role.")
        temp = temporary_password()
        with self.sf.begin() as s:
            actor = load_actor(s, actor_id)
            require(actor, Permission.MANAGE_USERS)
            if s.scalars(select(User).where(User.email == address)).first() is not None:
                raise Conflict("A user with that email address already exists.")
            user = User(
                id=new_id("user"),
                tenant_id=actor.tenant_id,
                display_name=name,
                email=address,
                role=role,
                is_active=True,
                password_hash=hash_password(temp),
                must_change_password=True,
                failed_logins=0,
            )
            s.add(user)
            s.flush()
            self._audit(
                s, user, "user_added", f"{actor.display_name} added {name} as {role}.", actor.id
            )
        return temp

    def _target(self, s: Session, actor_id: str, user_id: str) -> tuple[User, User]:
        actor = load_actor(s, actor_id)
        require(actor, Permission.MANAGE_USERS)
        target = get_scoped(s, User, user_id, actor.tenant_id)
        return actor, target

    def set_active(self, actor_id: str, user_id: str, active: bool) -> None:
        with self.sf.begin() as s:
            actor, target = self._target(s, actor_id, user_id)
            if target.is_active == active:
                return
            if not active:
                if target.id == actor.id:
                    raise ValidationError("You can't disable your own account.")
                if target.role == Role.OWNER and self._active_owners(s, target.tenant_id) <= 1:
                    raise ValidationError("The company must keep at least one active owner.")
                self._revoke_all(s, target.id, "user disabled")
            target.is_active = active
            verb = "enabled" if active else "disabled"
            self._audit(
                s,
                target,
                f"user_{verb}",
                f"{actor.display_name} {verb} {target.display_name}.",
                actor.id,
            )

    def _active_owners(self, s: Session, tenant_id: str) -> int:
        return (
            s.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.tenant_id == tenant_id,
                    User.role == Role.OWNER.value,
                    User.is_active.is_(True),
                )
            )
            or 0
        )

    def reset_password(self, actor_id: str, user_id: str) -> str:
        """New temporary password for another user; ends their sessions and unlocks them."""
        temp = temporary_password()
        with self.sf.begin() as s:
            actor, target = self._target(s, actor_id, user_id)
            if target.id == actor.id:
                raise ValidationError("Change your own password on the Account page.")
            target.password_hash = hash_password(temp)
            target.must_change_password = True
            target.failed_logins = 0
            target.locked_until = None
            self._revoke_all(s, target.id, "password reset")
            self._audit(
                s,
                target,
                "password_reset",
                f"{actor.display_name} reset the password of {target.display_name}.",
                actor.id,
            )
        return temp

    def reset_second_factor(self, actor_id: str, user_id: str) -> None:
        """The user sets up their authenticator app again at next sign-in."""
        with self.sf.begin() as s:
            actor, target = self._target(s, actor_id, user_id)
            if target.id == actor.id:
                raise ValidationError("Ask another owner to reset your authenticator app.")
            target.totp_secret = None
            target.totp_confirmed_at = None
            target.totp_last_step = None
            for rc in s.scalars(select(RecoveryCode).where(RecoveryCode.user_id == target.id)):
                s.delete(rc)
            self._revoke_all(s, target.id, "second factor reset")
            self._audit(
                s,
                target,
                "second_factor_reset",
                f"{actor.display_name} reset the authenticator app of {target.display_name}.",
                actor.id,
            )

    # ------------------------------------------------------------------ command line

    def create_owner(self, company: str, display_name: str, email: str, password: str) -> str:
        """First owner of a company, from the command line. Returns the user id."""
        address = self._check_new_owner(display_name, email, password)
        with self.sf.begin() as s:
            tenants = list(s.scalars(select(Tenant).order_by(Tenant.name)))
            matches = [t for t in tenants if t.name.lower() == company.strip().lower()]
            if not matches:
                names = ", ".join(t.name for t in tenants) or "none yet"
                raise NotFound(
                    f"No company called {company!r}. Companies: {names}. Create one with: "
                    "python -m opsapp company create"
                )
            return self._add_owner(s, matches[0], display_name, address, password)

    def create_company(
        self, name: str, timezone: str, owner_name: str, owner_email: str, password: str
    ) -> str:
        """An empty company and its first owner, in one step. Returns the company id.

        Works on a fresh database without the demo data. The company starts with no
        services, prices, customers or other users; the owner adds them in the app.
        """
        company = " ".join(name.split())
        if not 2 <= len(company) <= 100:
            raise ValidationError("The company name must be 2 to 100 characters.")
        zone = check_timezone(timezone)
        address = self._check_new_owner(owner_name, owner_email, password)
        with self.sf.begin() as s:
            taken = s.scalars(select(Tenant).where(func.lower(Tenant.name) == company.lower()))
            if taken.first() is not None:
                raise Conflict(f"A company called {company!r} already exists.")
            tenant = Tenant(
                id=new_id("tenant"),
                name=company,
                currency="USD",
                timezone=zone,
                created_at=self.clock.now(),
            )
            s.add(tenant)
            s.flush()
            audit.append(
                s,
                tenant_id=tenant.id,
                event_type="company_created",
                message=f"Company {company} created from the command line (time zone {zone}, "
                f"currency USD).",
                at=self.clock.now(),
                actor_type="system",
                data={"name": company, "timezone": zone},
            )
            self._add_owner(s, tenant, owner_name, address, password)
            return tenant.id

    def _check_new_owner(self, display_name: str, email: str, password: str) -> str:
        address = _normalize_email(email)
        if not display_name.strip() or len(display_name.strip()) > 100:
            raise ValidationError("Enter a name of up to 100 characters.")
        if "@" not in address or " " in address:
            raise ValidationError("Enter a valid email address.")
        problems = password_problems(password, address, display_name)
        if problems:
            raise ValidationError(" ".join(problems))
        return address

    def _add_owner(
        self, s: Session, tenant: Tenant, display_name: str, address: str, password: str
    ) -> str:
        if s.scalars(select(User).where(User.email == address)).first() is not None:
            raise Conflict("A user with that email address already exists.")
        user = User(
            id=new_id("user"),
            tenant_id=tenant.id,
            display_name=display_name.strip(),
            email=address,
            role=Role.OWNER.value,
            is_active=True,
            password_hash=hash_password(password),
            password_changed_at=self.clock.now(),
            failed_logins=0,
        )
        s.add(user)
        s.flush()
        self._audit(
            s,
            user,
            "user_added",
            f"{user.display_name} was added as owner from the command line.",
            actor="system",
        )
        return user.id


def check_timezone(name: str) -> str:
    """A time zone name such as Europe/London, checked against the time zone database."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

    zone = name.strip()
    if zone not in available_timezones():
        raise ValidationError(
            f"Unknown time zone {zone!r}. Use a name such as America/Chicago, "
            "America/New_York or Europe/London."
        )
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - listed but broken
        raise ValidationError(f"Time zone {zone!r} can't be loaded.") from exc
    return zone


def any_password_set(sf: sessionmaker[Session]) -> bool:
    with sf() as s:
        return (
            s.scalars(select(User.id).where(User.password_hash.is_not(None)).limit(1)).first()
            is not None
        )
