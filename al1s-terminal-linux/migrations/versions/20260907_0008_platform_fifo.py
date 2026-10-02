"""Persist authoritative platform FIFO keys without rewriting old packages."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0008"
down_revision = "20260901_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inbox_work_items", sa.Column("queue_enqueued_at", sa.DateTime(timezone=True)))
    op.add_column("inbox_work_items", sa.Column("queue_order_id", sa.String(36)))
    # Preserve the old local ordering for existing rows; their package remains immutable.
    work = sa.table(
        "inbox_work_items",
        sa.column("queue_enqueued_at"),
        sa.column("queue_order_id"),
        sa.column("created_at"),
        sa.column("id"),
    )
    op.execute(work.update().values(queue_enqueued_at=work.c.created_at, queue_order_id=work.c.id))
    with op.batch_alter_table("inbox_work_items") as batch:
        batch.alter_column(
            "queue_enqueued_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch.alter_column("queue_order_id", existing_type=sa.String(36), nullable=False)
        batch.create_index(
            "ix_inbox_platform_fifo",
            ["status", "available_at", "queue_enqueued_at", "queue_order_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("inbox_work_items") as batch:
        batch.drop_index("ix_inbox_platform_fifo")
        batch.drop_column("queue_order_id")
        batch.drop_column("queue_enqueued_at")
