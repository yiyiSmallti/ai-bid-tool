"""Confidential fields: org-defined placeholders whose encrypted values fill exports."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def protect(table: str) -> None:
    op.create_index(f"ix_{table}_org_id", table, ["org_id"])
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({POLICY}) WITH CHECK ({POLICY})')


def upgrade():
    op.create_table(
        "confidential_fields",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "key"),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{1,47}$'", name="confidential_field_key"),
        sa.CheckConstraint("btrim(label) <> ''", name="confidential_field_label"),
        sa.CheckConstraint(
            "kind IN ('amount', 'contact', 'identity', 'bank_account', 'other')",
            name="confidential_field_kind",
        ),
        sa.CheckConstraint("scope IN ('org', 'task')", name="confidential_field_scope"),
        sa.CheckConstraint("revision >= 1", name="confidential_field_revision"),
    )
    protect("confidential_fields")
    # Key, kind and scope are fixed: confirmed cards refer to the key.
    op.execute('GRANT SELECT, INSERT ON "confidential_fields" TO bid_app')
    op.execute('GRANT UPDATE (label, archived, revision) ON "confidential_fields" TO bid_app')

    op.create_table(
        "confidential_values",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("field_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("tail", sa.String(4), nullable=True),
        sa.UniqueConstraint("org_id", "id"),
        sa.ForeignKeyConstraint(
            ["org_id", "field_id"], ["confidential_fields.org_id", "confidential_fields.id"]
        ),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("version >= 1", name="confidential_value_version"),
    )
    op.create_index(
        "uq_confidential_values_org_version",
        "confidential_values",
        ["org_id", "field_id", "version"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )
    op.create_index(
        "uq_confidential_values_task_version",
        "confidential_values",
        ["org_id", "field_id", "task_id", "version"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    protect("confidential_values")
    # Values are append-only: a later version supersedes, nothing rewrites history.
    op.execute('GRANT SELECT, INSERT ON "confidential_values" TO bid_app')
    # Additive, like token_forbidden_screenshot_scopes: tokens never write or read values.
    op.execute(
        "ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_confidential_scopes "
        "CHECK (NOT (scopes ? 'confidential:write') AND NOT (scopes ? 'confidential:reveal'))"
    )


def downgrade():
    # Dropping the tables would destroy the only copy of values cards and exports refer to.
    raise RuntimeError("Data-preserving rollback required; keep confidential fields")
