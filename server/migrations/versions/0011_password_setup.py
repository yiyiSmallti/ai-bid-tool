"""Let the runtime set a password through the one-time setup link."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("GRANT UPDATE (password_hash) ON users TO bid_app")


def downgrade():
    op.execute("REVOKE UPDATE (password_hash) ON users FROM bid_app")
