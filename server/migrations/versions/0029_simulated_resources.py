"""Register products and features that a simulation job created, so they stay marked."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "simulated_resources",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("feature_id", sa.UUID(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "product_id"),
        sa.UniqueConstraint("org_id", "feature_id"),
        sa.ForeignKeyConstraint(["org_id", "job_id"], ["jobs.org_id", "jobs.id"]),
        sa.ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        sa.ForeignKeyConstraint(["org_id", "feature_id"], ["features.org_id", "features.id"]),
        sa.CheckConstraint(
            "(product_id IS NULL) <> (feature_id IS NULL)", name="simulated_one_resource"
        ),
    )
    policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    op.create_index("ix_simulated_resources_org_id", "simulated_resources", ["org_id"])
    op.execute('ALTER TABLE "simulated_resources" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "simulated_resources" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_scope ON "simulated_resources" USING ({policy}) WITH CHECK ({policy})'
    )
    # A mark is never edited or removed, so no later revision can launder a simulated resource.
    op.execute('GRANT SELECT, INSERT ON "simulated_resources" TO bid_app')


def downgrade():
    # Retain the marks rather than silently turning simulated material into ordinary material.
    raise RuntimeError("Data-preserving rollback required; keep simulated_resources")
