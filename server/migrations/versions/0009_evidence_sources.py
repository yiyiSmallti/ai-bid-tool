"""Add unconfirmed source archives and genuine retained PDF page previews."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "task_certificate_source_binding",
        "task_certificates",
        ["org_id", "id", "task_id", "certificate_id", "certificate_revision_id"],
    )
    op.create_unique_constraint(
        "certificate_file_source_binding",
        "certificate_files",
        ["org_id", "id", "certificate_id", "certificate_revision_id"],
    )
    op.create_table(
        "evidence_sources",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("task_certificate_id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("certificate_revision_id", sa.UUID(), nullable=False),
        sa.Column("certificate_file_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("render_profile", sa.String(40), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.Column("rendered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("eligible_for_draft_export", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "task_certificate_id", "page", "render_profile"),
        sa.ForeignKeyConstraint(
            [
                "org_id",
                "task_certificate_id",
                "task_id",
                "certificate_id",
                "certificate_revision_id",
            ],
            [
                "task_certificates.org_id",
                "task_certificates.id",
                "task_certificates.task_id",
                "task_certificates.certificate_id",
                "task_certificates.certificate_revision_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.org_id",
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
        ),
        sa.ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        sa.ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        sa.CheckConstraint("page BETWEEN 1 AND 200", name="source_page"),
        sa.CheckConstraint(
            "render_profile = 'pdf-page-preview-v1' AND dpi = 150", name="source_profile"
        ),
        sa.CheckConstraint(
            "status = 'unconfirmed_source' AND confirmed_by IS NULL AND eligible_for_draft_export = false",
            name="source_unconfirmed",
        ),
        sa.CheckConstraint("jsonb_typeof(preview) = 'object'", name="source_preview_object"),
        sa.CheckConstraint(
            "preview->>'name' IS NOT NULL AND jsonb_typeof(preview->'name') = 'string' AND length(btrim(preview->>'name')) BETWEEN 1 AND 200 AND position('/' in preview->>'name') = 0 AND position(chr(92) in preview->>'name') = 0 AND preview->>'name' !~ '[[:cntrl:]]' AND lower(right(preview->>'name',4)) = '.png'",
            name="source_preview_name",
        ),
        sa.CheckConstraint(
            "preview->>'sha256' IS NOT NULL AND jsonb_typeof(preview->'sha256') = 'string' AND preview->>'sha256' ~ '^[0-9a-f]{64}$'",
            name="source_preview_hash",
        ),
        sa.CheckConstraint(
            "preview->>'media_type' IS NOT NULL AND preview->>'media_type' = 'image/png'",
            name="source_preview_media",
        ),
        sa.CheckConstraint(
            "preview->>'size_bytes' IS NOT NULL AND jsonb_typeof(preview->'size_bytes') = 'number' AND (preview->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (preview->>'size_bytes')::numeric = trunc((preview->>'size_bytes')::numeric)",
            name="source_preview_size",
        ),
        sa.CheckConstraint(
            "preview->>'width_px' IS NOT NULL AND preview->>'height_px' IS NOT NULL AND jsonb_typeof(preview->'width_px') = 'number' AND jsonb_typeof(preview->'height_px') = 'number' AND (preview->>'width_px')::numeric BETWEEN 1 AND 8192 AND (preview->>'height_px')::numeric BETWEEN 1 AND 8192 AND (preview->>'width_px')::numeric = trunc((preview->>'width_px')::numeric) AND (preview->>'height_px')::numeric = trunc((preview->>'height_px')::numeric) AND (preview->>'width_px')::numeric * (preview->>'height_px')::numeric <= 20000000",
            name="source_preview_dimensions",
        ),
        sa.CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/evidence-source/' || id::text || '/' || (preview->>'sha256') || '.png'",
            name="source_preview_binding",
        ),
    )
    op.create_index("ix_evidence_sources_org_id", "evidence_sources", ["org_id"])
    op.execute("ALTER TABLE evidence_sources ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evidence_sources FORCE ROW LEVEL SECURITY")
    policy = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
    op.execute(
        f"CREATE POLICY tenant_scope ON evidence_sources USING ({policy}) WITH CHECK ({policy})"
    )
    op.execute("GRANT SELECT, INSERT ON evidence_sources TO bid_app")
    op.execute("""
        CREATE FUNCTION evidence_source_page_bounds() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE parent_pages integer;
        BEGIN
            SELECT (file->>'page_count')::integer INTO parent_pages
            FROM public.certificate_files WHERE org_id = NEW.org_id
                AND id = NEW.certificate_file_id AND certificate_id = NEW.certificate_id
                AND certificate_revision_id = NEW.certificate_revision_id;
            IF FOUND AND NEW.page > parent_pages THEN
                RAISE EXCEPTION 'Page is outside source original' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER evidence_source_page_bounds BEFORE INSERT ON evidence_sources
        FOR EACH ROW EXECUTE FUNCTION evidence_source_page_bounds()
    """)


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain source history and original previews"
    )
