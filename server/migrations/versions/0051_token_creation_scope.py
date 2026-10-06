"""Keep token issuance human-only at the database boundary as well as the API."""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    # Add to the existing domain checks; replacing them could reopen older scopes.
    # Validate existing rows, refusing the upgrade if a forbidden token already exists.
    op.execute(
        "ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_creation_scope "
        "CHECK (NOT (scopes ? 'token:create'))"
    )


def downgrade():
    raise RuntimeError("Preserve the human-only token scope guard and repair forward")
