"""Persist immutable release identity for host-side upgrade recovery."""

import sqlalchemy as sa
from alembic import op

revision = "host_0003"
down_revision = "host_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_commands", sa.Column("release_id", sa.String(36)))


def downgrade() -> None:
    raise RuntimeError("Cannot discard persisted upgrade command identities")
