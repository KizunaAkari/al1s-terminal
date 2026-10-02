"""Add durable local artifact upload queue.

Revision ID: 20260831_0004
Revises: 20260831_0003
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0004"
down_revision: str | None = "20260831_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_artifacts",
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platform_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("platform_blob_id", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "owner_kind IN ('formal_attempt', 'quick_test')",
            name="ck_local_artifacts_owner_kind",
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('screenshot', 'video', 'log', 'diagnostic')",
            name="ck_local_artifacts_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'confirmed', 'dead_letter')",
            name="ck_local_artifacts_status",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_local_artifacts_size"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_local_artifacts_attempt_count"),
        sa.CheckConstraint("row_version > 0", name="ck_local_artifacts_version"),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["inbox_work_items.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("work_item_id", "relative_path", name="uq_local_artifact_path"),
    )
    op.create_index(
        "ix_local_artifacts_available",
        "local_artifacts",
        ["status", "available_at", "created_at", "artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_local_artifacts_claim",
        "local_artifacts",
        ["status", "claimed_until"],
        unique=False,
    )
    op.create_index(
        "ix_local_artifacts_work",
        "local_artifacts",
        ["work_item_id", "created_at", "artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_local_artifacts_work", table_name="local_artifacts")
    op.drop_index("ix_local_artifacts_claim", table_name="local_artifacts")
    op.drop_index("ix_local_artifacts_available", table_name="local_artifacts")
    op.drop_table("local_artifacts")
