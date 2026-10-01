"""Add tenant-scoped immutable certificate revisions and task selections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ("certificates", "certificate_revisions", "task_certificates")


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
        "certificates",
        *common(),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("current_revision > 0", name="certificate_revision_positive"),
    )
    op.create_table(
        "certificate_revisions",
        *common(),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("org_id", "certificate_id", "id"),
        sa.UniqueConstraint("org_id", "certificate_id", "revision"),
        sa.ForeignKeyConstraint(
            ["org_id", "certificate_id"], ["certificates.org_id", "certificates.id"]
        ),
        sa.CheckConstraint("revision > 0", name="certificate_revision_number_positive"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="certificate_revision_object"),
        sa.CheckConstraint(
            "data->>'kind' IS NOT NULL AND data->>'kind' IN ('qualification', 'personnel')",
            name="certificate_declared_kind",
        ),
        sa.CheckConstraint(
            "data->>'name' IS NOT NULL AND length(btrim(data->>'name')) BETWEEN 1 AND 200 "
            "AND data->>'number' IS NOT NULL AND length(btrim(data->>'number')) BETWEEN 1 AND 200",
            name="certificate_declared_identity",
        ),
        sa.CheckConstraint(
            "(data->>'valid_from' IS NULL OR (data->>'valid_from' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND (data->>'valid_from')::date IS NOT NULL)) "
            "AND (data->>'valid_until' IS NULL OR (data->>'valid_until' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND (data->>'valid_until')::date IS NOT NULL)) "
            "AND (data->>'valid_from' IS NULL OR data->>'valid_until' IS NULL OR (data->>'valid_from')::date <= (data->>'valid_until')::date)",
            name="certificate_declared_dates",
        ),
    )
    op.create_foreign_key(
        "certificate_current_revision",
        "certificates",
        "certificate_revisions",
        ["org_id", "id", "current_revision"],
        ["org_id", "certificate_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "task_certificates",
        *common(),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("certificate_revision_id", sa.UUID(), nullable=False),
        sa.Column("lot", sa.String(100), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_revisions.org_id",
                "certificate_revisions.certificate_id",
                "certificate_revisions.id",
            ],
        ),
    )
    op.create_index(
        "task_certificate_active_slot",
        "task_certificates",
        ["org_id", "task_id", "certificate_id", "lot"],
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
    op.execute("GRANT UPDATE ON certificates TO bid_app")
    op.execute("GRANT UPDATE(active) ON task_certificates TO bid_app")
    # Snapshots reference immutable revisions, so no duplicated payload can drift.
    # History/audit cannot be updated or deleted through the restricted runtime.


def downgrade():
    # Retain business history rather than silently deleting it during rollback.
    raise RuntimeError(
        "Data-preserving rollback required; restore the previous application without dropping history tables"
    )
