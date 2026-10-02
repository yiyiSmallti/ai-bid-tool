"""Reasoning levels per catalog model, the level of each job, and requirements per extraction."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    # Catalog models list the vendor's official reasoning levels; empty means not levelled.
    op.execute("ALTER TABLE platform_models ADD COLUMN reasoning jsonb NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE platform_models ADD COLUMN default_reasoning varchar(20)")
    op.execute(
        "ALTER TABLE platform_models ADD CONSTRAINT platform_model_reasoning CHECK ("
        "jsonb_typeof(reasoning) = 'array' AND "
        "((jsonb_array_length(reasoning) = 0 AND default_reasoning IS NULL) OR "
        "(jsonb_array_length(reasoning) > 0 AND default_reasoning IS NOT NULL)))"
    )

    # The level a job ran at; null for parse jobs and models without levels.
    op.execute("ALTER TABLE jobs ADD COLUMN reasoning varchar(20)")

    # Each extraction keeps its own requirements instead of merging into the task.
    op.execute("ALTER TABLE requirements ADD COLUMN job_id uuid")
    op.execute(
        "UPDATE requirements r SET job_id = ("
        "SELECT j.id FROM jobs j WHERE j.org_id = r.org_id AND j.document_id = r.document_id "
        "AND j.kind = 'extract' AND j.status = 'succeeded' "
        "ORDER BY j.finished_at DESC NULLS LAST, j.created_at DESC LIMIT 1)"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM requirements WHERE job_id IS NULL) THEN "
        "RAISE EXCEPTION 'requirements without a succeeded extraction job; repair before 0014'; "
        "END IF; END $$"
    )
    op.execute("ALTER TABLE requirements ALTER COLUMN job_id SET NOT NULL")
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT requirements_org_id_job_id_fkey "
        "FOREIGN KEY (org_id, job_id) REFERENCES jobs (org_id, id)"
    )
    op.execute(
        "ALTER TABLE requirements DROP CONSTRAINT requirements_org_id_task_id_fingerprint_key"
    )
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT requirements_job_fingerprint "
        "UNIQUE (org_id, job_id, fingerprint)"
    )


def downgrade():
    raise RuntimeError("Data-preserving rollback required; extractions cannot be merged back")
