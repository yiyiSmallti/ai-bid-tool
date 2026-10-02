"""Add tenant-scoped immutable profile revisions and task selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("org_profiles", "org_profile_revisions", "task_org_profiles")


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
        "org_profiles",
        *common(),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("current_revision > 0", name="profile_revision_positive"),
    )
    op.create_table(
        "org_profile_revisions",
        *common(),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("org_id", "profile_id", "id"),
        sa.UniqueConstraint("org_id", "profile_id", "revision"),
        sa.ForeignKeyConstraint(
            ["org_id", "profile_id"], ["org_profiles.org_id", "org_profiles.id"]
        ),
        sa.CheckConstraint("revision > 0", name="profile_revision_number_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="profile_revision_object"),
        sa.CheckConstraint(
            "data->>'name' IS NOT NULL AND jsonb_typeof(data->'name') = 'string' "
            "AND length(btrim(data->>'name')) BETWEEN 1 AND 200",
            name="profile_declared_name",
        ),
        sa.CheckConstraint(
            "data->>'registration_details' IS NULL OR (jsonb_typeof(data->'registration_details') = 'string' "
            "AND length(btrim(data->>'registration_details')) BETWEEN 1 AND 10000)",
            name="profile_registration_details_text",
        ),
        sa.CheckConstraint(
            "data->>'performance_summary' IS NULL OR (jsonb_typeof(data->'performance_summary') = 'string' "
            "AND length(btrim(data->>'performance_summary')) BETWEEN 1 AND 20000)",
            name="profile_performance_summary_text",
        ),
        sa.CheckConstraint(
            "data->>'standard_wording' IS NULL OR (jsonb_typeof(data->'standard_wording') = 'string' "
            "AND length(btrim(data->>'standard_wording')) BETWEEN 1 AND 20000)",
            name="profile_standard_wording_text",
        ),
    )
    op.create_foreign_key(
        "profile_current_revision",
        "org_profiles",
        "org_profile_revisions",
        ["org_id", "id", "current_revision"],
        ["org_id", "profile_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "task_org_profiles",
        *common(),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("profile_revision_id", sa.UUID(), nullable=False),
        sa.Column("lot", sa.String(100), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "profile_id", "profile_revision_id"],
            [
                "org_profile_revisions.org_id",
                "org_profile_revisions.profile_id",
                "org_profile_revisions.id",
            ],
        ),
    )
    op.create_index(
        "task_profile_active_slot",
        "task_org_profiles",
        ["org_id", "task_id", "profile_id", "lot"],
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
    op.execute("GRANT UPDATE(current_revision) ON org_profiles TO bid_app")
    op.execute("GRANT UPDATE(active) ON task_org_profiles TO bid_app")
    # Snapshots reference immutable revisions, so no duplicated payload can drift.
    # History/audit cannot be updated or deleted through the restricted runtime.


def downgrade():
    # Retain business history rather than silently deleting it during rollback.
    raise RuntimeError(
        "Data-preserving rollback required; restore the previous application without dropping history tables"
    )
