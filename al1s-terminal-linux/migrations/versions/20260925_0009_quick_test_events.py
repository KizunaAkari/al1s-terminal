"""Queue bounded quick-test node events for offline replay."""

import sqlalchemy as sa
from alembic import op

revision = "20260925_0009"
down_revision = "20260907_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quick_test_events",
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("work_item_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("step_number", sa.Integer()),
        sa.Column("code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["work_item_id"], ["inbox_work_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("session_id", "sequence"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 1000", name="ck_local_quick_event_seq"),
        sa.CheckConstraint(
            "kind IN ('started','step_started','step_succeeded','step_failed','log')",
            name="ck_local_quick_event_kind",
        ),
    )
    op.create_index(
        "ix_local_quick_event_pending", "quick_test_events",
        ["confirmed_at", "created_at", "session_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_local_quick_event_pending", table_name="quick_test_events")
    op.drop_table("quick_test_events")
