"""Add durable two-phase local garbage collection jobs.

Revision ID: 20260901_0007
Revises: 20260901_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0007"
down_revision: str | None = "20260901_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_gc_jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_local_gc_attempt_count"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'dead_letter')",
            name="ck_local_gc_status",
        ),
        sa.CheckConstraint(
            "target_kind IN ('artifact', 'work_item', 'resource')",
            name="ck_local_gc_target_kind",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_local_gc_version"),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("target_kind", "target_id", name="uq_local_gc_target"),
    )
    op.create_index(
        "ix_local_gc_available",
        "local_gc_jobs",
        ["status", "available_at", "created_at", "job_id"],
    )
    op.create_index(
        "ix_local_gc_claim",
        "local_gc_jobs",
        ["status", "claimed_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_local_gc_claim", table_name="local_gc_jobs")
    op.drop_index("ix_local_gc_available", table_name="local_gc_jobs")
    op.drop_table("local_gc_jobs")
