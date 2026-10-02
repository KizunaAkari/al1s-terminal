"""Create the durable terminal runtime foundation.

Revision ID: 20260831_0001
Revises:
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terminal_installation",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("terminal_id", sa.String(length=36), nullable=True),
        sa.Column("registration_status", sa.String(length=32), nullable=False),
        sa.Column("terminal_row_version", sa.Integer(), nullable=True),
        sa.Column("credential_epoch", sa.Integer(), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("capability_revision", sa.Integer(), nullable=False),
        sa.Column("last_capability_hash", sa.String(length=64), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_id = 1", name="ck_terminal_installation_singleton"),
        sa.CheckConstraint(
            "registration_status IN ('unregistered', 'registered', 'credential_rejected')",
            name="ck_terminal_installation_status",
        ),
        sa.CheckConstraint("credential_epoch >= 0", name="ck_terminal_installation_epoch"),
        sa.CheckConstraint("capability_revision >= 0", name="ck_terminal_capability_revision"),
        sa.CheckConstraint("row_version > 0", name="ck_terminal_installation_version"),
        sa.PrimaryKeyConstraint("singleton_id"),
        sa.UniqueConstraint("installation_id"),
        sa.UniqueConstraint("terminal_id"),
    )
    op.create_table(
        "inbox_work_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("remote_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("target_device_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=True),
        sa.Column("lease_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("row_version > 0", name="ck_inbox_version"),
        sa.CheckConstraint("kind IN ('formal_task', 'quick_test')", name="ck_inbox_kind"),
        sa.CheckConstraint(
            "status IN ('receiving', 'queued', 'running', 'result_pending', "
            "'completed', 'interrupted', 'rejected')",
            name="ck_inbox_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "remote_id", name="uq_inbox_kind_remote"),
    )
    op.create_index(
        "ix_inbox_runnable",
        "inbox_work_items",
        ["status", "available_at", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "outbox_reports",
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        sa.CheckConstraint(
            "kind IN ('package_receipt', 'attempt_start', 'attempt_result', 'quick_test_result')",
            name="ck_outbox_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'confirmed', 'dead_letter')",
            name="ck_outbox_status",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_outbox_version"),
        sa.ForeignKeyConstraint(["work_item_id"], ["inbox_work_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("report_id"),
    )
    op.create_index(
        "ix_outbox_available",
        "outbox_reports",
        ["status", "available_at", "created_at", "report_id"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox_reports",
        ["status", "claimed_until"],
        unique=False,
    )
    op.create_table(
        "local_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_item_id"], ["inbox_work_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_local_transition_work_item",
        "local_transitions",
        ["work_item_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_local_transition_work_item", table_name="local_transitions")
    op.drop_table("local_transitions")
    op.drop_index("ix_outbox_claim", table_name="outbox_reports")
    op.drop_index("ix_outbox_available", table_name="outbox_reports")
    op.drop_table("outbox_reports")
    op.drop_index("ix_inbox_runnable", table_name="inbox_work_items")
    op.drop_table("inbox_work_items")
    op.drop_table("terminal_installation")
