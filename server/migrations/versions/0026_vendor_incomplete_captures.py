"""Vendor captures may omit failed resources; their archives say so and review must too."""

from alembic import op
from sqlalchemy import text

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

ARCHIVE_OLD = "OR jsonb_typeof(NEW.provenance) IS DISTINCT FROM 'object' THEN"
ARCHIVE_NEW = """OR jsonb_typeof(NEW.provenance) IS DISTINCT FROM 'object'
    OR jsonb_typeof(NEW.provenance->'incomplete') IS DISTINCT FROM 'boolean'
    OR (NEW.provenance->>'failed_request_count')::bigint IS DISTINCT FROM (
      SELECT count(*) FROM public.sandbox_fetch_receipts f
      WHERE f.org_id=NEW.org_id AND f.attempt_record_id=a.id AND f.decision_code<>'allowed')
    OR (NEW.provenance->>'incomplete')::boolean IS DISTINCT FROM (
      (NEW.provenance->>'failed_request_count')::bigint > 0
      OR jsonb_path_exists(a.issues, '$[*] ? (@.code == "vendor_resources_incomplete")')) THEN"""
WARNING_OLD = "OR (image.origin='vendor' AND NOT NEW.reviewed_warning_codes ? 'vendor_model_scope')"
WARNING_NEW = (
    WARNING_OLD
    + "\n      OR (image.origin='vendor' AND NOT NEW.reviewed_warning_codes ? "
    + "'vendor_capture_incomplete' AND EXISTS (SELECT 1 FROM public.screenshot_vendor_archives v"
    + " WHERE v.org_id=image.org_id AND v.id=image.vendor_archive_id"
    + " AND (v.provenance->>'incomplete')::boolean))"
)


def _patch(function: str, old: str, new: str):
    body = (
        op.get_bind()
        .execute(text(f"SELECT pg_get_functiondef('public.{function}()'::regprocedure)"))
        .scalar_one()
    )
    if body.count(old) != 1:
        raise RuntimeError(f"{function} fragment not unique")
    op.execute(body.replace(old, new))


def upgrade():
    _patch("screenshot_vendor_archive_gate", ARCHIVE_OLD, ARCHIVE_NEW)
    _patch("screenshot_card_warning_gate", WARNING_OLD, WARNING_NEW)


def downgrade():
    _patch("screenshot_card_warning_gate", WARNING_NEW, WARNING_OLD)
    _patch("screenshot_vendor_archive_gate", ARCHIVE_NEW, ARCHIVE_OLD)
