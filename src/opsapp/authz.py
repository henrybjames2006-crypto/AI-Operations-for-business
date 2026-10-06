"""Server-side authorization and tenant scoping.

Every use case calls ``require`` for the acting user's role and loads records through
``get_scoped`` so a record from another tenant behaves exactly like a missing one.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session

from .domain.errors import NotFound, PermissionDenied
from .domain.roles import Permission, Role, has_permission
from .persistence.models import User


class TenantScoped(Protocol):
    id: Any
    tenant_id: Any


def require(user: User, permission: Permission) -> None:
    if not user.is_active or not has_permission(Role(user.role), permission):
        raise PermissionDenied(
            f"Your role ({user.role}) does not allow this action ({permission.value})."
        )


def get_scoped[T](s: Session, model: type[T], record_id: str, tenant_id: str) -> T:
    obj = s.get(model, record_id)
    if obj is None or getattr(obj, "tenant_id", None) != tenant_id:
        raise NotFound(f"{model.__name__} not found.")
    return obj


def load_actor(s: Session, user_id: str) -> User:
    user = s.get(User, user_id)
    if user is None or not user.is_active:
        raise PermissionDenied("Unknown or inactive user.")
    return user
