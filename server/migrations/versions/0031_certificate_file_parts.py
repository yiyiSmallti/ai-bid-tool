"""Uploaded files behind a composed certificate original: images and PDFs in order."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def upgrade():
    op.create_table(
        "certificate_file_parts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("certificate_revision_id", sa.UUID(), nullable=False),
        sa.Column("certificate_file_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.UniqueConstraint("org_id", "id"),
        sa.UniqueConstraint("org_id", "certificate_file_id", "ordinal"),
        sa.ForeignKeyConstraint(
            ["org_id", "certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.org_id",
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
        ),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 20", name="certificate_part_ordinal"),
        sa.CheckConstraint(
            "media_type IN ('application/pdf', 'image/png', 'image/jpeg')",
            name="certificate_part_type",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="certificate_part_hash"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 41943040", name="certificate_part_size"),
        sa.CheckConstraint(
            "page_start BETWEEN 1 AND 200 AND page_count BETWEEN 1 AND 200 "
            "AND page_start + page_count - 1 <= 200",
            name="certificate_part_pages",
        ),
        sa.CheckConstraint("rotation IN (0, 90, 180, 270)", name="certificate_part_rotation"),
        sa.CheckConstraint(
            "btrim(name) <> '' AND position('/' in name) = 0 AND position(chr(92) in name) = 0 "
            "AND name !~ '[[:cntrl:]]'",
            name="certificate_part_name",
        ),
        sa.CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/certificate/' || certificate_id::text || '/' "
            "|| certificate_revision_id::text || '/parts/' || ordinal::text || '-' || sha256",
            name="certificate_part_binding",
        ),
    )
    op.create_index("ix_certificate_file_parts_org_id", "certificate_file_parts", ["org_id"])
    op.execute('ALTER TABLE "certificate_file_parts" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "certificate_file_parts" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_scope ON "certificate_file_parts" USING ({POLICY}) WITH CHECK ({POLICY})'
    )
    op.execute('GRANT SELECT, INSERT ON "certificate_file_parts" TO bid_app')
    # Parts belong to the original they were composed into: a committed original
    # can never gain, lose or swap a part.
    op.execute("""
        CREATE FUNCTION certificate_part_new_file_only() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE file_xmin xid;
        BEGIN
            SELECT xmin INTO file_xmin
            FROM public.certificate_files
            WHERE org_id = NEW.org_id AND id = NEW.certificate_file_id;
            IF FOUND AND file_xmin <> pg_current_xact_id()::xid THEN
                RAISE EXCEPTION 'Part requires a newly created certificate file'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER certificate_part_new_file_only
        BEFORE INSERT ON certificate_file_parts FOR EACH ROW
        EXECUTE FUNCTION certificate_part_new_file_only()
    """)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; keep certificate file parts")
