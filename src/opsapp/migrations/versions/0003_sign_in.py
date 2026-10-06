"""sign-in: passwords, authenticator codes, sessions and recovery codes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 03:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(200), nullable=True))
        batch_op.add_column(
            sa.Column(
                "must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("password_changed_at", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("totp_secret", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("totp_confirmed_at", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("totp_last_step", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("locked_until", sa.String(40), nullable=True))
        batch_op.create_index("ux_users_email", ["email"], unique=True)
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", sa.String(40), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("last_seen_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
        sa.Column("revoked_reason", sa.String(60), nullable=True),
    )
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("tenant_id", sa.String(40), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("used_at", sa.String(40), nullable=True),
    )
    op.create_index("ix_recovery_codes_tenant_id", "recovery_codes", ["tenant_id"])
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_table("recovery_codes")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ux_users_email")
        for col in (
            "locked_until",
            "failed_logins",
            "totp_last_step",
            "totp_confirmed_at",
            "totp_secret",
            "password_changed_at",
            "must_change_password",
            "password_hash",
        ):
            batch_op.drop_column(col)
