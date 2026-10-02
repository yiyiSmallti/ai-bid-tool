"""Initial tenant foundation and forced row isolation."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "orgs",
    "memberships",
    "api_tokens",
    "tasks",
    "documents",
    "chunks",
    "requirements",
    "usage_records",
    "jobs",
)


def upgrade():
    # The schema is immutable: future ORM changes cannot rewrite this migration.
    schema = Path(__file__).with_name("0001_schema.sql").read_text()
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement)
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({policy}) WITH CHECK ({policy})'
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO bid_app')
    # Global login identity is the sole documented org/RLS exception.
    op.execute("GRANT SELECT ON users TO bid_app")


def downgrade():
    for table in (
        "jobs",
        "usage_records",
        "requirements",
        "chunks",
        "documents",
        "tasks",
        "api_tokens",
        "memberships",
        "users",
        "orgs",
    ):
        op.execute(f'DROP TABLE "{table}"')
