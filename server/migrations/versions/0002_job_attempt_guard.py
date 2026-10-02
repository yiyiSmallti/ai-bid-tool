"""Prevent cancelled/retried worker attempts from committing stale results."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("run_id", sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column("jobs", "run_id")
