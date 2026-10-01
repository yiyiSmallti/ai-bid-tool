"""Add tenant-scoped declared software metadata and retained selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("features", "feature_revisions", "task_features")


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
        "features",
        *common(),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("current_revision > 0", name="feature_revision_positive"),
    )
    op.create_table(
        "feature_revisions",
        *common(),
        sa.Column("feature_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("org_id", "feature_id", "id"),
        sa.UniqueConstraint("org_id", "feature_id", "revision"),
        sa.ForeignKeyConstraint(["org_id", "feature_id"], ["features.org_id", "features.id"]),
        sa.ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        sa.CheckConstraint("revision > 0", name="feature_revision_number_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="feature_revision_object"),
        sa.CheckConstraint(
            "data->>'product_id' IS NOT NULL AND data->>'product_id' = product_id::text",
            name="feature_product_matches",
        ),
        sa.CheckConstraint(
            "data->>'status' IS NOT NULL AND data->>'status' IN ('implemented', 'developing', 'planned')",
            name="feature_declared_status",
        ),
    )
    op.create_foreign_key(
        "feature_current_revision",
        "features",
        "feature_revisions",
        ["org_id", "id", "current_revision"],
        ["org_id", "feature_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "task_features",
        *common(),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("feature_id", sa.UUID(), nullable=False),
        sa.Column("feature_revision_id", sa.UUID(), nullable=False),
        sa.Column("lot", sa.String(100), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "feature_id", "feature_revision_id"],
            ["feature_revisions.org_id", "feature_revisions.feature_id", "feature_revisions.id"],
        ),
    )
    op.create_index(
        "task_feature_active_slot",
        "task_features",
        ["org_id", "task_id", "feature_id", "lot"],
        unique=True,
        postgresql_where=sa.text("active"),
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
    op.execute("GRANT UPDATE ON features TO bid_app")
    op.execute("GRANT UPDATE(active) ON task_features TO bid_app")


def downgrade():
    raise RuntimeError("Data-preserving rollback required; feature history must be retained")
