"""SQLAlchemy models. Every business table carries ``tenant_id``.

Money is stored as decimal strings and datetimes as ISO-8601 UTC strings so SQLite never
converts them to floats or drops the time zone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DecimalString(TypeDecorator[Decimal]):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("Money and quantities must be Decimal")
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator[datetime]):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise TypeError("Datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def tenant_fk() -> Mapped[str]:
    return mapped_column(String(40), ForeignKey("tenants.id"), index=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ux_users_email", "email", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Sign-in (0.4.0). Users without a password can only be used in demo mode.
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuthSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)


class RecoveryCode(Base):
    """One-time codes for when the authenticator app is lost. Stored as SHA-256 only."""

    __tablename__ = "recovery_codes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    email_domains: Mapped[list[Any]] = mapped_column(JSON, default=list)
    contact_email: Mapped[str] = mapped_column(String(200))


class CustomerSite(Base):
    __tablename__ = "customer_sites"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(400))


class CustomerRequest(Base):
    __tablename__ = "customer_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "submission_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    source_channel: Mapped[str] = mapped_column(String(40))
    sender: Mapped[str] = mapped_column(String(200))
    raw_text: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64), index=True)
    submission_key: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    request_id: Mapped[str] = mapped_column(ForeignKey("customer_requests.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(20))
    output: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_chars: Mapped[int] = mapped_column(Integer)
    output_chars: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WorkflowInstance(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    request_id: Mapped[str] = mapped_column(ForeignKey("customer_requests.id"), unique=True)
    state: Mapped[str] = mapped_column(String(30), index=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    # Working scope: customer, site, recipient, items, timeframe and their provenance.
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ClarificationQuestion(Base):
    __tablename__ = "clarification_questions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(80))
    question: Mapped[str] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[Any]] = mapped_column(JSON, default=list)
    quantity_step: Mapped[Decimal] = mapped_column(DecimalString())
    onsite: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PricingVersion(Base):
    __tablename__ = "pricing_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "version_no"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    version_no: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))  # draft | approved | retired | discarded
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime())
    rules: Mapped[list[Any]] = mapped_column(JSON, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Set on versions imported from a file: the catalog details (names, keywords, steps)
    # applied to the services when this version is approved. None for other versions.
    catalog_changes: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)


class PriceEntryRow(Base):
    __tablename__ = "price_entries"
    __table_args__ = (UniqueConstraint("pricing_version_id", "catalog_item_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    pricing_version_id: Mapped[str] = mapped_column(ForeignKey("pricing_versions.id"), index=True)
    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"))
    unit_price: Mapped[Decimal] = mapped_column(DecimalString())
    currency: Mapped[str] = mapped_column(String(3))
    min_qty: Mapped[Decimal] = mapped_column(DecimalString())
    max_qty: Mapped[Decimal] = mapped_column(DecimalString())


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), unique=True)
    current_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class QuoteVersion(Base):
    __tablename__ = "quote_versions"
    __table_args__ = (UniqueConstraint("quote_id", "version_no"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    pricing_version_id: Mapped[str] = mapped_column(ForeignKey("pricing_versions.id"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    site_id: Mapped[str] = mapped_column(ForeignKey("customer_sites.id"))
    recipient_email: Mapped[str] = mapped_column(String(200))
    schedule_timeframe: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Exact inputs (requested lines + the price entries and rules used) for reproduction.
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    calculation: Mapped[dict[str, Any]] = mapped_column(JSON)
    assumptions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    total: Mapped[Decimal] = mapped_column(DecimalString())
    currency: Mapped[str] = mapped_column(String(3))
    content_hash: Mapped[str] = mapped_column(String(64))
    prepared_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ProposedAction(Base):
    __tablename__ = "proposed_actions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    quote_version_id: Mapped[str] = mapped_column(ForeignKey("quote_versions.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))  # send_quote | propose_schedule
    adapter: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    # proposed | queued | in_progress | succeeded | failed | uncertain | escalated | voided
    status: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    quote_version_id: Mapped[str] = mapped_column(ForeignKey("quote_versions.id"))
    subject_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected
    approver_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(Text, default="")
    invalidated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OutboxEntry(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id"), index=True)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    claimed_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    needs_reconcile: Mapped[bool] = mapped_column(Boolean, default=False)


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # send | reconcile
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # succeeded | failed | timeout | found | not_found | unknown
    outcome: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ExternalOperation(Base):
    __tablename__ = "external_operations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id"), unique=True)
    adapter: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(80))
    external_ref: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq"),
        Index("ix_audit_workflow", "tenant_id", "workflow_id"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    seq: Mapped[int] = mapped_column(Integer)
    workflow_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | system | dispatcher
    actor_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class ExceptionRecord(Base):
    __tablename__ = "exceptions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    action_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model_id: Mapped[str] = mapped_column(String(80))
    task: Mapped[str] = mapped_column(String(40))
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    est_cost_usd: Mapped[Decimal] = mapped_column(DecimalString())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


# --- The simulated external systems' own storage. -----------------------------------------
# These two tables stand in for a third-party email or calendar service. The workflow code
# never reads them directly; it only talks to the adapters.


class SimOperation(Base):
    __tablename__ = "sim_operations"
    __table_args__ = (UniqueConstraint("adapter", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    adapter: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SimFault(Base):
    __tablename__ = "sim_faults"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    adapter: Mapped[str] = mapped_column(String(40))
    # fail | timeout_before_send | drop_response | status_unknown
    mode: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
