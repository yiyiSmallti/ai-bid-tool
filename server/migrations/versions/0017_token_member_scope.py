"""No `bid_` token manages members (ADR 0013).

Accounts are managed only by an administrator signed in in person, so the
member-bound exception 0005 made for the CLI is withdrawn. Existing tokens lose
the scope and keep the rest.
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
UPDATE public.api_tokens SET scopes = scopes - 'member:manage' WHERE scopes ? 'member:manage';
ALTER TABLE public.api_tokens DROP CONSTRAINT token_forbidden_member_scope;
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_member_scope
    CHECK (NOT (scopes ? 'member:manage'));
"""
    )


def downgrade():
    raise NotImplementedError("Member management by token has no earlier representation")
