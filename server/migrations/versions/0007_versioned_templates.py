"""Add tenant-scoped immutable template revisions and task selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = ("templates", "template_revisions", "task_templates")


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
        "templates",
        *common(),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("current_revision > 0", name="template_revision_positive"),
    )
    op.create_table(
        "template_revisions",
        *common(),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("file", postgresql.JSONB(), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.UniqueConstraint("org_id", "template_id", "id"),
        sa.UniqueConstraint("org_id", "template_id", "revision"),
        sa.ForeignKeyConstraint(["org_id", "template_id"], ["templates.org_id", "templates.id"]),
        sa.CheckConstraint("revision > 0", name="template_revision_number_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="template_revision_object"),
        sa.CheckConstraint(
            "data->>'name' IS NOT NULL AND jsonb_typeof(data->'name') = 'string' "
            "AND length(btrim(data->>'name')) BETWEEN 1 AND 200",
            name="template_declared_name",
        ),
        sa.CheckConstraint("jsonb_typeof(file) = 'object'", name="template_file_object"),
        sa.CheckConstraint(
            "file->>'name' IS NOT NULL AND jsonb_typeof(file->'name') = 'string' "
            "AND length(btrim(file->>'name')) BETWEEN 1 AND 200",
            name="template_file_name",
        ),
        sa.CheckConstraint(
            "file->>'sha256' IS NOT NULL AND (file->>'sha256') ~ '^[0-9a-f]{64}$'",
            name="template_file_hash",
        ),
        sa.CheckConstraint(
            "file->>'size_bytes' IS NOT NULL AND jsonb_typeof(file->'size_bytes') = 'number' "
            "AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 "
            "AND (file->>'size_bytes')::numeric = trunc((file->>'size_bytes')::numeric)",
            name="template_file_size",
        ),
        sa.CheckConstraint(
            "file->>'media_type' IS NOT NULL AND file->>'media_type' = "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document'",
            name="template_file_type",
        ),
        sa.CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/template/' || template_id::text "
            "|| '/' || id::text || '/' || (file->>'sha256') || '.docx'",
            name="template_file_binding",
        ),
    )
    op.create_foreign_key(
        "template_current_revision",
        "templates",
        "template_revisions",
        ["org_id", "id", "current_revision"],
        ["org_id", "template_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "task_templates",
        *common(),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("template_revision_id", sa.UUID(), nullable=False),
        sa.Column("lot", sa.String(100), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "template_id", "template_revision_id"],
            [
                "template_revisions.org_id",
                "template_revisions.template_id",
                "template_revisions.id",
            ],
        ),
    )
    op.create_index(
        "task_template_active_slot",
        "task_templates",
        ["org_id", "task_id", "template_id", "lot"],
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
    op.execute("GRANT UPDATE(current_revision) ON templates TO bid_app")
    op.execute("GRANT UPDATE(active) ON task_templates TO bid_app")
    # Snapshots reference immutable revisions, so no duplicated payload can drift.
    # History/audit cannot be updated or deleted through the restricted runtime.


def downgrade():
    # Retain business history rather than silently deleting it during rollback.
    raise RuntimeError(
        "Data-preserving rollback required; restore the previous application without dropping history tables"
    )
