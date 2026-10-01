"""Add retained tenant certificate PDF originals without rewriting prior versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "certificate_files",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("certificate_revision_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("file", postgresql.JSONB(), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "certificate_revision_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_revisions.org_id",
                "certificate_revisions.certificate_id",
                "certificate_revisions.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("jsonb_typeof(file) = 'object'", name="certificate_file_object"),
        sa.CheckConstraint(
            "file->>'name' IS NOT NULL AND jsonb_typeof(file->'name') = 'string' AND length(btrim(file->>'name')) BETWEEN 1 AND 200 AND position('/' in file->>'name') = 0 AND position(chr(92) in file->>'name') = 0 AND file->>'name' !~ '[[:cntrl:]]' AND lower(right(file->>'name',4)) = '.pdf'",
            name="certificate_file_name",
        ),
        sa.CheckConstraint(
            "file->>'sha256' IS NOT NULL AND jsonb_typeof(file->'sha256') = 'string' AND (file->>'sha256') ~ '^[0-9a-f]{64}$'",
            name="certificate_file_hash",
        ),
        sa.CheckConstraint(
            "file->>'size_bytes' IS NOT NULL AND jsonb_typeof(file->'size_bytes') = 'number' AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (file->>'size_bytes')::numeric = trunc((file->>'size_bytes')::numeric)",
            name="certificate_file_size",
        ),
        sa.CheckConstraint(
            "file->>'page_count' IS NOT NULL AND jsonb_typeof(file->'page_count') = 'number' AND (file->>'page_count')::numeric BETWEEN 1 AND 200 AND (file->>'page_count')::numeric = trunc((file->>'page_count')::numeric)",
            name="certificate_file_pages",
        ),
        sa.CheckConstraint(
            "file->>'media_type' IS NOT NULL AND file->>'media_type' = 'application/pdf'",
            name="certificate_file_type",
        ),
        sa.CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/certificate/' || certificate_id::text || '/' || certificate_revision_id::text || '/' || (file->>'sha256') || '.pdf'",
            name="certificate_file_binding",
        ),
    )
    op.create_index("ix_certificate_files_org_id", "certificate_files", ["org_id"])
    policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    op.execute("ALTER TABLE certificate_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE certificate_files FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_scope ON certificate_files USING ({policy}) WITH CHECK ({policy})"
    )
    op.execute("GRANT SELECT, INSERT ON certificate_files TO bid_app")
    # A file must be created in the same transaction as its immutable revision.
    # Invoker security retains the caller's RLS visibility; the composite FK
    # rejects missing/invisible or mismatched parents independently.
    op.execute("""
        CREATE FUNCTION certificate_file_new_revision_only() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE revision_xmin xid;
        BEGIN
            SELECT xmin INTO revision_xmin
            FROM public.certificate_revisions
            WHERE org_id = NEW.org_id AND certificate_id = NEW.certificate_id
                AND id = NEW.certificate_revision_id;
            IF FOUND AND revision_xmin <> pg_current_xact_id()::xid THEN
                RAISE EXCEPTION 'File requires a newly created revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER certificate_file_new_revision_only
        BEFORE INSERT ON certificate_files FOR EACH ROW
        EXECUTE FUNCTION certificate_file_new_revision_only()
    """)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; do not drop retained certificate files")
