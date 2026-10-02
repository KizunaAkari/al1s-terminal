"""Persist the explicit upgrade-risk confirmation independently of task interruption."""

import sqlalchemy as sa
from alembic import op

revision = "host_0002"
down_revision = "host_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_commands", sa.Column("confirmed_upgrade_id", sa.String(100)))


def downgrade() -> None:
    raise RuntimeError("Cannot discard persisted maintenance risk confirmations")
