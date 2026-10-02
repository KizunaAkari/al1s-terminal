"""Initial host journal; independent from the business container database."""

import sqlalchemy as sa
from alembic import op

revision = "host_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unversioned development databases are deliberately not silently stamped.
    op.create_table(
        "host_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("accepted_at", sa.Float, nullable=False),
        sa.Column("started_at", sa.Float),
        sa.Column("recovery_start_at", sa.Float),
        sa.Column("late_state", sa.String(24)),
        sa.Column("previous_boot", sa.String(64), nullable=False),
        sa.Column("previous_container", sa.String(64), nullable=False),
        sa.Column("previous_start", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("version", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Host command journal downgrade requires explicit data handling")
