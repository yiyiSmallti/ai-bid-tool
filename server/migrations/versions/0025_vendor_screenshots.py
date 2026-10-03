"""Vendor page images bound to the sandbox capture that observed their bytes."""

from alembic import op
from sqlalchemy import text

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Each vendor image card review must acknowledge that the model and page scope were checked.
WARNING_OLD = (
    "OR (image.image_kind='diagram' AND NOT NEW.reviewed_warning_codes ? 'image_design_only')"
)
WARNING_NEW = (
    WARNING_OLD
    + "\n      OR (image.origin='vendor' AND NOT NEW.reviewed_warning_codes ? 'vendor_model_scope')"
)


def _patch_warning_gate():
    connection = op.get_bind()
    body = connection.execute(
        text("SELECT pg_get_functiondef('public.screenshot_card_warning_gate()'::regprocedure)")
    ).scalar_one()
    if body.count(WARNING_OLD) != 1:
        raise RuntimeError("screenshot_card_warning_gate fragment not unique")
    op.execute(body.replace(WARNING_OLD, WARNING_NEW))


def upgrade():
    # No code path wrote vendor archives before this revision; NOT NULL fails closed if any exist.
    op.execute("""
ALTER TABLE screenshot_vendor_archives
  ADD COLUMN sandbox_run_id uuid NOT NULL,
  ADD COLUMN archive_artifact_id uuid NOT NULL,
  ADD COLUMN entry_receipt_id uuid NOT NULL,
  ADD CONSTRAINT screenshot_vendor_archive_run FOREIGN KEY (org_id, sandbox_run_id)
    REFERENCES sandbox_runs (org_id, id),
  ADD CONSTRAINT screenshot_vendor_archive_artifact FOREIGN KEY (org_id, archive_artifact_id)
    REFERENCES sandbox_artifacts (org_id, id),
  ADD CONSTRAINT screenshot_vendor_archive_entry FOREIGN KEY (org_id, entry_receipt_id)
    REFERENCES sandbox_fetch_receipts (org_id, id),
  ADD CONSTRAINT screenshot_vendor_archive_single UNIQUE (org_id, sandbox_run_id)
""")
    op.execute("""
CREATE FUNCTION screenshot_vendor_archive_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.sandbox_runs; i public.sandbox_inputs; j public.jobs; a public.sandbox_attempts;
        art public.sandbox_artifacts; rec public.sandbox_fetch_receipts;
BEGIN
  PERFORM public.screenshot_require_ingest(NEW.org_id);
  SELECT * INTO r FROM public.sandbox_runs WHERE org_id=NEW.org_id AND id=NEW.sandbox_run_id;
  SELECT * INTO i FROM public.sandbox_inputs WHERE org_id=NEW.org_id AND id=r.input_id;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=r.job_id;
  SELECT * INTO a FROM public.sandbox_attempts
    WHERE org_id=NEW.org_id AND sandbox_run_id=r.id AND job_id=j.id AND attempt_id=j.run_id;
  SELECT * INTO art FROM public.sandbox_artifacts WHERE org_id=NEW.org_id AND id=NEW.archive_artifact_id;
  SELECT * INTO rec FROM public.sandbox_fetch_receipts WHERE org_id=NEW.org_id AND id=NEW.entry_receipt_id;
  IF r.id IS NULL OR r.task_id IS DISTINCT FROM NEW.task_id
    OR i.purpose IS DISTINCT FROM 'vendor_capture'
    OR i.extraction_job_id IS DISTINCT FROM NEW.extraction_job_id
    OR (i.task_resource_id,i.product_revision_id) IS DISTINCT FROM (NEW.task_resource_id,NEW.product_revision_id)
    OR j.status IS DISTINCT FROM 'succeeded' OR a.termination_code IS DISTINCT FROM 'succeeded'
    OR a.cleanup_state IS DISTINCT FROM 'complete'
    OR art.attempt_record_id IS DISTINCT FROM a.id
    OR art.kind IS DISTINCT FROM (CASE WHEN i.spec->>'format'='pdf' THEN 'source_pdf' ELSE 'capture_archive' END)
    OR art.plaintext_sha256 IS DISTINCT FROM NEW.archive_sha256
    OR art.object_key IS DISTINCT FROM NEW.storage_key
    OR rec.attempt_record_id IS DISTINCT FROM a.id OR rec.decision_code IS DISTINCT FROM 'allowed'
    OR rec.status_code IS DISTINCT FROM 200 OR rec.response_sha256 IS DISTINCT FROM NEW.content_sha256
    OR (i.spec->>'format'='pdf' AND NEW.content_sha256 IS DISTINCT FROM NEW.archive_sha256)
    -- Search candidates have no producer yet; an unchecked candidate link is refused.
    OR NEW.search_candidate_id IS NOT NULL
    OR jsonb_typeof(NEW.descriptor) IS DISTINCT FROM 'object'
    OR NEW.descriptor->>'sha256' IS DISTINCT FROM NEW.archive_sha256
    OR (NEW.descriptor->>'size_bytes')::bigint IS DISTINCT FROM art.size_bytes::bigint
    OR jsonb_typeof(NEW.provenance) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'Vendor archive must match its succeeded sandbox capture' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_vendor_asset_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE v public.screenshot_vendor_archives; archive public.sandbox_artifacts; art public.sandbox_artifacts;
BEGIN
  IF NEW.vendor_archive_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO v FROM public.screenshot_vendor_archives WHERE org_id=NEW.org_id AND id=NEW.vendor_archive_id;
  SELECT * INTO archive FROM public.sandbox_artifacts WHERE org_id=NEW.org_id AND id=v.archive_artifact_id;
  SELECT * INTO art FROM public.sandbox_artifacts
    WHERE org_id=NEW.org_id AND id=(NEW.source->>'sandbox_artifact_id')::uuid;
  IF (v.task_resource_id,v.product_revision_id) IS DISTINCT FROM (NEW.task_resource_id,NEW.product_revision_id)
    OR NEW.source - ARRAY['kind','sandbox_artifact_id'] <> '{}'::jsonb
    OR art.attempt_record_id IS DISTINCT FROM archive.attempt_record_id
    OR art.kind IS DISTINCT FROM (CASE NEW.source_kind WHEN 'vendor_web' THEN 'capture_png' ELSE 'pdf_page_png' END)
    OR (NEW.source_kind='vendor_pdf') IS DISTINCT FROM (archive.kind='source_pdf')
    OR art.plaintext_sha256 IS DISTINCT FROM NEW.source_sha256
    OR (art.width,art.height) IS DISTINCT FROM (NEW.source_width,NEW.source_height)
    OR NEW.source_hash_assurance IS DISTINCT FROM 'server_verified' THEN
    RAISE EXCEPTION 'Vendor image must be a page of its archived capture' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
""")
    op.execute(
        "CREATE TRIGGER screenshot_vendor_shape BEFORE INSERT ON screenshot_vendor_archives "
        "FOR EACH ROW EXECUTE FUNCTION screenshot_vendor_archive_gate()"
    )
    op.execute(
        "CREATE TRIGGER screenshot_vendor_source BEFORE INSERT ON screenshot_assets "
        "FOR EACH ROW EXECUTE FUNCTION screenshot_vendor_asset_gate()"
    )
    _patch_warning_gate()


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain vendor archive and sandbox provenance"
    )
