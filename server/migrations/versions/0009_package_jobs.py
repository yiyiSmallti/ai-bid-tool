"""Package export and import jobs.

An export job belongs to its task and reads no single document. An import job
has no task when it is queued: the task it creates is recorded in its result.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
ALTER TABLE public.jobs DROP CONSTRAINT job_document_binding;
ALTER TABLE public.jobs ADD CONSTRAINT job_document_binding CHECK (
    ((kind)::text = ANY (ARRAY['provider_test', 'package_import']) AND task_id IS NULL
        AND document_id IS NULL)
    OR ((kind)::text = ANY (ARRAY['bid_review_prepare', 'package_export'])
        AND task_id IS NOT NULL AND document_id IS NULL)
    OR ((kind)::text = ANY (ARRAY['bid_review', 'bid_review_report']) AND task_id IS NOT NULL
        AND document_id IS NULL AND bid_submission_document_id IS NOT NULL)
    OR ((kind)::text <> ALL (ARRAY['provider_test', 'package_import', 'bid_review_prepare',
            'package_export', 'bid_review', 'bid_review_report'])
        AND task_id IS NOT NULL AND document_id IS NOT NULL)
);
"""
    )


def downgrade():
    raise NotImplementedError("Package jobs have no earlier representation")
