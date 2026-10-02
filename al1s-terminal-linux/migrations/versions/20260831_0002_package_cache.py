"""Add durable package manifests and content-addressed resources.

Revision ID: 20260831_0002
Revises: 20260831_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0002"
down_revision: str | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "package_manifests",
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("package_schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_schema_version", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("protocol_version > 0", name="ck_package_manifests_protocol"),
        sa.CheckConstraint("package_schema_version > 0", name="ck_package_manifests_schema"),
        sa.CheckConstraint("snapshot_schema_version > 0", name="ck_package_snapshot_schema"),
        sa.CheckConstraint(
            "status IN ('receiving', 'ready', 'rejected')",
            name="ck_package_manifests_status",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_package_manifests_version"),
        sa.ForeignKeyConstraint(["work_item_id"], ["inbox_work_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("package_id"),
        sa.UniqueConstraint("work_item_id"),
    )
    op.create_index(
        "ix_package_manifests_status",
        "package_manifests",
        ["status", "created_at", "package_id"],
        unique=False,
    )
    op.create_table(
        "cached_resources",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("blob_id", sa.String(length=36), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_cached_resources_size"),
        sa.CheckConstraint(
            "status IN ('downloading', 'ready', 'quarantined')",
            name="ck_cached_resources_status",
        ),
        sa.CheckConstraint("row_version > 0", name="ck_cached_resources_version"),
        sa.PrimaryKeyConstraint("sha256"),
    )
    op.create_index(
        "ix_cached_resources_status",
        "cached_resources",
        ["status", "updated_at", "sha256"],
        unique=False,
    )
    op.create_table(
        "work_item_resources",
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("resource_key", sa.String(length=255), nullable=False),
        sa.Column("blob_id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["sha256"], ["cached_resources.sha256"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_item_id"], ["inbox_work_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("work_item_id", "resource_key"),
        sa.UniqueConstraint(
            "work_item_id", "blob_id", "role", name="uq_work_item_resources_blob_role"
        ),
    )
    op.create_index(
        "ix_work_item_resources_sha",
        "work_item_resources",
        ["sha256", "work_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_item_resources_sha", table_name="work_item_resources")
    op.drop_table("work_item_resources")
    op.drop_index("ix_cached_resources_status", table_name="cached_resources")
    op.drop_table("cached_resources")
    op.drop_index("ix_package_manifests_status", table_name="package_manifests")
    op.drop_table("package_manifests")
