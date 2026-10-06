"""initial schema

Revision ID: 0001
Revises: (none)
Create Date: 2026-10-05 09:41:51.478414
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Money and timestamps are stored as strings (see persistence/models.py).
    op.create_table(
        "sim_faults",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("consumed_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sim_faults", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sim_faults_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "sim_operations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adapter", "idempotency_key"),
    )
    with op.batch_alter_table("sim_operations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sim_operations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=40), nullable=True),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("at", sa.String(length=40), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "seq"),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index("ix_audit_workflow", ["tenant_id", "workflow_id"], unique=False)

    op.create_table(
        "catalog_items",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("sku", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("quantity_step", sa.String(length=40), nullable=False),
        sa.Column("onsite", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sku"),
    )
    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_catalog_items_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("email_domains", sa.JSON(), nullable=False),
        sa.Column("contact_email", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_customers_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "pricing_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.String(length=40), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(length=40), nullable=True),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "version_no"),
    )
    with op.batch_alter_table("pricing_versions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pricing_versions_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "customer_requests",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("source_channel", sa.String(length=40), nullable=False),
        sa.Column("sender", sa.String(length=200), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("submission_key", sa.String(length=80), nullable=False),
        sa.Column("received_at", sa.String(length=40), nullable=False),
        sa.Column("received_by", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["received_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "submission_key"),
    )
    with op.batch_alter_table("customer_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_customer_requests_raw_sha256"), ["raw_sha256"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_customer_requests_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "customer_sites",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=400), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("customer_sites", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_customer_sites_customer_id"), ["customer_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_customer_sites_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "price_entries",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("pricing_version_id", sa.String(length=40), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=40), nullable=False),
        sa.Column("unit_price", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("min_qty", sa.String(length=40), nullable=False),
        sa.Column("max_qty", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["pricing_version_id"],
            ["pricing_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pricing_version_id", "catalog_item_id"),
    )
    with op.batch_alter_table("price_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_price_entries_pricing_version_id"), ["pricing_version_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_price_entries_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "extraction_results",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("model_id", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("input_chars", sa.Integer(), nullable=False),
        sa.Column("output_chars", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["customer_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("extraction_results", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_extraction_results_request_id"), ["request_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_extraction_results_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("closed_at", sa.String(length=40), nullable=True),
        sa.Column("submitted_by", sa.String(length=40), nullable=True),
        sa.Column("review_started_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["customer_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    with op.batch_alter_table("workflows", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workflows_state"), ["state"], unique=False)
        batch_op.create_index(batch_op.f("ix_workflows_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_id", sa.String(length=80), nullable=False),
        sa.Column("task", sa.String(length=40), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("est_cost_usd", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ai_usage", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ai_usage_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_usage_workflow_id"), ["workflow_id"], unique=False)

    op.create_table(
        "clarification_questions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("field", sa.String(length=80), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.String(length=40), nullable=True),
        sa.Column("answered_at", sa.String(length=40), nullable=True),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("clarification_questions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_clarification_questions_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_clarification_questions_workflow_id"), ["workflow_id"], unique=False
        )

    op.create_table(
        "exceptions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("opened_at", sa.String(length=40), nullable=False),
        sa.Column("resolved_by", sa.String(length=40), nullable=True),
        sa.Column("resolved_at", sa.String(length=40), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("exceptions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_exceptions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_exceptions_workflow_id"), ["workflow_id"], unique=False
        )

    op.create_table(
        "quotes",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("current_version_id", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id"),
    )
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quotes_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "quote_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("quote_id", sa.String(length=40), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("pricing_version_id", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.String(length=40), nullable=False),
        sa.Column("site_id", sa.String(length=40), nullable=False),
        sa.Column("recipient_email", sa.String(length=200), nullable=False),
        sa.Column("schedule_timeframe", sa.String(length=40), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("calculation", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("total", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("prepared_by", sa.String(length=40), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["pricing_version_id"],
            ["pricing_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["customer_sites.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id", "version_no"),
    )
    with op.batch_alter_table("quote_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quote_versions_quote_id"), ["quote_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_quote_versions_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("quote_version_id", sa.String(length=40), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("approver_id", sa.String(length=40), nullable=False),
        sa.Column("decided_at", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("invalidated_at", sa.String(length=40), nullable=True),
        sa.Column("invalidated_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["quote_version_id"],
            ["quote_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approvals_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_approvals_workflow_id"), ["workflow_id"], unique=False)

    op.create_table(
        "proposed_actions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("workflow_id", sa.String(length=40), nullable=False),
        sa.Column("quote_version_id", sa.String(length=40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["quote_version_id"],
            ["quote_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    with op.batch_alter_table("proposed_actions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_proposed_actions_status"), ["status"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_proposed_actions_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_proposed_actions_workflow_id"), ["workflow_id"], unique=False
        )

    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.String(length=40), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("external_ref", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["proposed_actions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("execution_attempts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_execution_attempts_action_id"), ["action_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_execution_attempts_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "external_operations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.String(length=40), nullable=False),
        sa.Column("adapter", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("external_ref", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_reconciled_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["proposed_actions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id"),
    )
    with op.batch_alter_table("external_operations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_external_operations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "outbox",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("tenant_id", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.String(length=40), nullable=False),
        sa.Column("available_at", sa.String(length=40), nullable=False),
        sa.Column("claimed_by", sa.String(length=80), nullable=True),
        sa.Column("claimed_until", sa.String(length=40), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("needs_reconcile", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["proposed_actions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_outbox_action_id"), ["action_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_available_at"), ["available_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_done"), ["done"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_tenant_id"), ["tenant_id"], unique=False)


def downgrade() -> None:
    # Money and timestamps are stored as strings (see persistence/models.py).
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outbox_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_outbox_done"))
        batch_op.drop_index(batch_op.f("ix_outbox_available_at"))
        batch_op.drop_index(batch_op.f("ix_outbox_action_id"))

    op.drop_table("outbox")
    with op.batch_alter_table("external_operations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_external_operations_tenant_id"))

    op.drop_table("external_operations")
    with op.batch_alter_table("execution_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_execution_attempts_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_execution_attempts_action_id"))

    op.drop_table("execution_attempts")
    with op.batch_alter_table("proposed_actions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_proposed_actions_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_proposed_actions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_proposed_actions_status"))

    op.drop_table("proposed_actions")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approvals_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_approvals_tenant_id"))

    op.drop_table("approvals")
    with op.batch_alter_table("quote_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quote_versions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_quote_versions_quote_id"))

    op.drop_table("quote_versions")
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotes_tenant_id"))

    op.drop_table("quotes")
    with op.batch_alter_table("exceptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exceptions_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_exceptions_tenant_id"))

    op.drop_table("exceptions")
    with op.batch_alter_table("clarification_questions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_clarification_questions_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_clarification_questions_tenant_id"))

    op.drop_table("clarification_questions")
    with op.batch_alter_table("ai_usage", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_usage_workflow_id"))
        batch_op.drop_index(batch_op.f("ix_ai_usage_tenant_id"))

    op.drop_table("ai_usage")
    with op.batch_alter_table("workflows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workflows_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_workflows_state"))

    op.drop_table("workflows")
    with op.batch_alter_table("extraction_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_extraction_results_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_extraction_results_request_id"))

    op.drop_table("extraction_results")
    with op.batch_alter_table("price_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_price_entries_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_price_entries_pricing_version_id"))

    op.drop_table("price_entries")
    with op.batch_alter_table("customer_sites", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customer_sites_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_customer_sites_customer_id"))

    op.drop_table("customer_sites")
    with op.batch_alter_table("customer_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customer_requests_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_customer_requests_raw_sha256"))

    op.drop_table("customer_requests")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_tenant_id"))

    op.drop_table("users")
    with op.batch_alter_table("pricing_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pricing_versions_tenant_id"))

    op.drop_table("pricing_versions")
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customers_tenant_id"))

    op.drop_table("customers")
    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_catalog_items_tenant_id"))

    op.drop_table("catalog_items")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_workflow")
        batch_op.drop_index(batch_op.f("ix_audit_events_tenant_id"))

    op.drop_table("audit_events")
    op.drop_table("tenants")
    with op.batch_alter_table("sim_operations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sim_operations_tenant_id"))

    op.drop_table("sim_operations")
    with op.batch_alter_table("sim_faults", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sim_faults_tenant_id"))

    op.drop_table("sim_faults")
