"""Word documents cite structural locations (section, paragraph, table cell) instead of pages."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    # Chunks: PDF chunks keep a page; Word chunks carry their blocks and no page.
    op.execute("ALTER TABLE chunks ALTER COLUMN page DROP NOT NULL")
    op.execute("ALTER TABLE chunks ADD COLUMN seq integer")
    op.execute("UPDATE chunks SET seq = page")
    op.execute("ALTER TABLE chunks ALTER COLUMN seq SET NOT NULL")
    op.execute("ALTER TABLE chunks ADD CONSTRAINT chunk_seq_positive CHECK (seq > 0)")
    op.execute("ALTER TABLE chunks ADD COLUMN blocks jsonb")
    op.execute(
        "ALTER TABLE chunks ADD CONSTRAINT chunk_position CHECK ("
        "(page IS NOT NULL AND blocks IS NULL) OR "
        "(page IS NULL AND jsonb_typeof(blocks) = 'array' AND jsonb_array_length(blocks) > 0))"
    )
    op.execute("ALTER TABLE chunks DROP CONSTRAINT chunks_org_id_document_id_page_key")
    op.execute(
        "ALTER TABLE chunks ADD CONSTRAINT chunks_document_seq UNIQUE (org_id, document_id, seq)"
    )
    op.execute(
        "CREATE UNIQUE INDEX chunks_document_page ON chunks (org_id, document_id, page) "
        "WHERE page IS NOT NULL"
    )

    # Requirements cite exactly one of a page or a structural location.
    op.execute("ALTER TABLE requirements ALTER COLUMN page DROP NOT NULL")
    op.execute("ALTER TABLE requirements ADD COLUMN location jsonb")
    op.execute("ALTER TABLE requirements DROP CONSTRAINT requirement_citation")
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT requirement_citation CHECK ("
        "length(quote) > 0 AND ("
        "(page > 0 AND location IS NULL) OR "
        "(page IS NULL AND jsonb_typeof(location) = 'object' "
        "AND length(coalesce(location->>'block_id', '')) > 0 "
        "AND length(coalesce(location->>'label', '')) > 0)))"
    )

    op.execute(
        "ALTER TABLE documents ADD COLUMN citation_mode varchar(10) "
        "CHECK (citation_mode IN ('page', 'block'))"
    )


def downgrade():
    raise RuntimeError("Data-preserving rollback required; Word locations cannot become pages")
