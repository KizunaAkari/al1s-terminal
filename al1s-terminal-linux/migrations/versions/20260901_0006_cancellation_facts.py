"""Persist local cancellation delivery facts.

Revision ID: 20260901_0006
Revises: 20260831_0005
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0006"
down_revision: str | None = "20260831_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inbox_work_items", recreate="always") as batch:
        batch.drop_constraint("ck_inbox_status", type_="check")
        batch.add_column(sa.Column("cancel_command_id", sa.String(length=36)))
        batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancel_reason", sa.String(length=32)))
        batch.add_column(sa.Column("cancel_outcome", sa.String(length=32)))
        batch.create_check_constraint(
            "ck_inbox_status",
            "status IN ('receiving', 'queued', 'running', 'result_pending', "
            "'completed', 'cancelled', 'interrupted', 'rejected')",
        )
        batch.create_check_constraint(
            "ck_inbox_cancel_outcome",
            "cancel_outcome IS NULL OR cancel_outcome IN "
            "('running_cancel_accepted', 'already_completed', 'cancelled_before_start')",
        )
        batch.create_check_constraint(
            "ck_inbox_cancel_fact",
            "(cancel_command_id IS NULL AND cancel_requested_at IS NULL "
            "AND cancel_reason IS NULL AND cancel_outcome IS NULL) OR "
            "(cancel_command_id IS NOT NULL AND cancel_requested_at IS NOT NULL "
            "AND cancel_reason IS NOT NULL AND cancel_outcome IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("inbox_work_items", recreate="always") as batch:
        batch.drop_constraint("ck_inbox_cancel_fact", type_="check")
        batch.drop_constraint("ck_inbox_cancel_outcome", type_="check")
        batch.drop_constraint("ck_inbox_status", type_="check")
        batch.drop_column("cancel_outcome")
        batch.drop_column("cancel_reason")
        batch.drop_column("cancel_requested_at")
        batch.drop_column("cancel_command_id")
        batch.create_check_constraint(
            "ck_inbox_status",
            "status IN ('receiving', 'queued', 'running', 'result_pending', "
            "'completed', 'interrupted', 'rejected')",
        )
