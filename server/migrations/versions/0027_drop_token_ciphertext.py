"""API tokens keep only their digest; the decryptable copy of each secret is removed."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE api_tokens DROP COLUMN encrypted_secret")


def downgrade():
    # The removed ciphertexts cannot be restored; older code only writes the column.
    op.execute("ALTER TABLE api_tokens ADD COLUMN encrypted_secret TEXT")
