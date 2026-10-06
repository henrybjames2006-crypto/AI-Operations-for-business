"""Roles and the permission matrix. Enforced on the server in ``opsapp.authz``."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    OPERATOR = "operator"
    APPROVER = "approver"
    VIEWER = "viewer"


class Permission(StrEnum):
    VIEW = "view"
    CREATE_REQUEST = "create_request"
    ANSWER_CLARIFICATION = "answer_clarification"
    PREPARE_QUOTE = "prepare_quote"
    SUBMIT_FOR_APPROVAL = "submit_for_approval"
    APPROVE = "approve"
    CANCEL_BEFORE_APPROVAL = "cancel_before_approval"
    CANCEL_ANY = "cancel_any"
    RESOLVE_ESCALATION = "resolve_escalation"
    RETRY_EXECUTION = "retry_execution"
    MANAGE_CATALOG = "manage_catalog"
    SIMULATION_CONTROLS = "simulation_controls"
    MANAGE_USERS = "manage_users"
    EXPORT_AUDIT = "export_audit"
    MANAGE_CUSTOMERS = "manage_customers"
    MANAGE_COMPANY = "manage_company"


P = Permission

_PREPARER = {
    P.CREATE_REQUEST,
    P.ANSWER_CLARIFICATION,
    P.PREPARE_QUOTE,
    P.SUBMIT_FOR_APPROVAL,
    P.CANCEL_BEFORE_APPROVAL,
}
_APPROVER = {
    P.APPROVE,
    P.CANCEL_BEFORE_APPROVAL,
    P.CANCEL_ANY,
    P.RESOLVE_ESCALATION,
    P.RETRY_EXECUTION,
}

MATRIX: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.OPERATOR: frozenset({P.VIEW, P.MANAGE_CUSTOMERS, *_PREPARER}),
    Role.APPROVER: frozenset({P.VIEW, *_APPROVER}),
    Role.VIEWER: frozenset({P.VIEW}),
}

ROLE_TEXT: dict[Role, str] = {
    Role.OWNER: "Owner/admin: everything, but never approves work they prepared",
    Role.OPERATOR: "Operator/preparer: intake, clarification, quote preparation, customers",
    Role.APPROVER: "Approver: approves or rejects quotes, resolves exceptions",
    Role.VIEWER: "Viewer: read only",
}


def has_permission(role: Role | str, permission: Permission) -> bool:
    return permission in MATRIX[Role(role)]
