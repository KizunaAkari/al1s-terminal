"""Add durable local ADB target observations.

Revision ID: 20260831_0003
Revises: 20260831_0002
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0003"
down_revision: str | None = "20260831_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "target_device_observations",
        sa.Column("adb_serial", sa.String(length=512), nullable=False),
        sa.Column("identifier_id", sa.String(length=36), nullable=False),
        sa.Column("target_device_id", sa.String(length=36), nullable=True),
        sa.Column("identifier_row_version", sa.Integer(), nullable=False),
        sa.Column("adb_state", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "adb_state IN ('device', 'offline', 'unauthorized', 'other')",
            name="ck_target_device_observations_state",
        ),
        sa.CheckConstraint("identifier_row_version > 0", name="ck_target_identifier_version"),
        sa.CheckConstraint("row_version > 0", name="ck_target_device_observations_version"),
        sa.PrimaryKeyConstraint("adb_serial"),
        sa.UniqueConstraint("identifier_id"),
    )
    op.create_index(
        "ix_target_device_observations_target",
        "target_device_observations",
        ["target_device_id", "adb_state", "last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_target_device_observations_target",
        table_name="target_device_observations",
    )
    op.drop_table("target_device_observations")
