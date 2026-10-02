"""Add tenant-scoped immutable product revisions and task selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("products", "product_revisions", "task_resources", "audit_logs")


def common():
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id"),
    ]


def upgrade():
    op.create_table(
        "products",
        *common(),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("current_revision > 0", name="product_revision_positive"),
    )
    op.create_table(
        "product_revisions",
        *common(),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("org_id", "product_id", "id"),
        sa.UniqueConstraint("org_id", "product_id", "revision"),
        sa.ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="revision_object"),
    )
    op.create_foreign_key(
        "product_current_revision",
        "products",
        "product_revisions",
        ["org_id", "id", "current_revision"],
        ["org_id", "product_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "task_resources",
        *common(),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_revision_id", sa.UUID(), nullable=False),
        sa.Column("lot", sa.String(100), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "product_id", "product_revision_id"],
            ["product_revisions.org_id", "product_revisions.product_id", "product_revisions.id"],
        ),
    )
    op.create_index(
        "task_product_active_slot",
        "task_resources",
        ["org_id", "task_id", "product_id", "lot"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "audit_logs",
        *common(),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("actor_token_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "actor_user_id"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "actor_token_id"], ["api_tokens.org_id", "api_tokens.id"]
        ),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="audit_object"),
    )
    policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    for table in TABLES:
        op.create_index("ix_" + table + "_org_id", table, ["org_id"])
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({policy}) WITH CHECK ({policy})'
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
    op.execute("GRANT UPDATE ON products TO bid_app")
    op.execute("GRANT UPDATE(active) ON task_resources TO bid_app")
    # Snapshots reference immutable revisions, so no duplicated payload can drift.
    # History/audit cannot be updated or deleted through the restricted runtime.


def downgrade():
    # Retain business history rather than silently deleting it during rollback.
    raise RuntimeError(
        "Data-preserving rollback required; restore the previous application without dropping history tables"
    )
