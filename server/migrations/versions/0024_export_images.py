"""Image evidence attachments and prototype keep decisions in human exports."""

from alembic import op
from sqlalchemy import text

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# export_complete_gate from 0021 is patched in place so the image branch sits next to
# the certificate branch; each fragment must occur exactly once in the stored body.
QUOTE_OLD = "AND material->'input'->>'quote'=e.quote"
QUOTE_NEW = "AND material->'input'->>'quote' IS NOT DISTINCT FROM e.quote"
ELSE_OLD = "ELSE material->'input'->>'field_path'=e.field_path"
ELSE_NEW = """WHEN e.kind='image_region' THEN
                  material->>'screenshot_rendition_id'=e.screenshot_rendition_id::text
                  AND material->>'image_sha256'=e.image_sha256
                  AND material->'input'->>'rendition_id'=e.screenshot_rendition_id::text
                  AND l.attachment_ordinal IS NOT NULL
                  AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'attachments') attachment
                    WHERE (attachment->>'ordinal')::integer=l.attachment_ordinal
                      AND attachment->>'kind'='image'
                      AND attachment->>'rendition_id'=e.screenshot_rendition_id::text
                      AND attachment->>'png_sha256'=e.image_sha256)
                  AND public.export_prototype_kept(r.org_id,r.mode,e.id,i.card_revision_id,
                    l.prototype_decision_id)
                  AND (l.prototype_decision_id IS NULL OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(r.manifest->'prototype_decisions'->'decisions') d
                    WHERE d->>'evidence_id'=e.id::text AND d->>'decision'='keep'
                      AND d->>'decision_id'=l.prototype_decision_id::text))
                ELSE l.prototype_decision_id IS NULL
                  AND material->'input'->>'field_path'=e.field_path"""


def _patch(old_new):
    connection = op.get_bind()
    body = connection.execute(
        text("SELECT pg_get_functiondef('public.export_complete_gate()'::regprocedure)")
    ).scalar_one()
    for old, new in old_new:
        if body.count(old) != 1:
            raise RuntimeError(f"export_complete_gate fragment not unique: {old[:60]}")
        body = body.replace(old, new)
    op.execute(body)


def upgrade():
    op.execute("ALTER TABLE export_run_evidence ADD COLUMN prototype_decision_id uuid")
    op.execute(
        "ALTER TABLE export_run_evidence ADD CONSTRAINT export_run_evidence_prototype_decision "
        "FOREIGN KEY (org_id, evidence_id, prototype_decision_id) "
        "REFERENCES prototype_evidence_decisions (org_id, evidence_id, id)"
    )
    op.execute("""
    CREATE FUNCTION export_prototype_kept(
      p_org uuid, p_mode text, p_evidence uuid, p_revision uuid, p_decision uuid
    ) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog, public AS $$
    DECLARE e public.evidence; d public.prototype_evidence_decisions; a public.screenshot_assets;
    BEGIN
      SELECT * INTO e FROM public.evidence WHERE org_id=p_org AND id=p_evidence;
      SELECT * INTO a FROM public.screenshot_assets x
        WHERE x.org_id=e.org_id AND x.id=e.screenshot_asset_id;
      -- Review copies and non-prototype images never depend on a decision.
      IF p_mode<>'final_section' OR a.origin IS DISTINCT FROM 'prototype' THEN
        RETURN p_decision IS NULL;
      END IF;
      SELECT * INTO d FROM public.prototype_evidence_decisions x
        WHERE x.org_id=p_org AND x.evidence_id=p_evidence
        ORDER BY x.created_at DESC, x.id DESC LIMIT 1;
      RETURN d.id IS NOT NULL AND d.id=p_decision AND d.decision='keep'
        AND d.card_revision_id=p_revision AND d.asset_id=e.screenshot_asset_id
        AND d.rendition_id=e.screenshot_rendition_id AND d.image_sha256=e.image_sha256
        AND d.task_feature_id=a.task_feature_id AND d.feature_revision_id=a.feature_revision_id;
    END $$;
    """)
    _patch([(QUOTE_OLD, QUOTE_NEW), (ELSE_OLD, ELSE_NEW)])


def downgrade():
    _patch([(ELSE_NEW, ELSE_OLD), (QUOTE_NEW, QUOTE_OLD)])
    op.execute("DROP FUNCTION export_prototype_kept(uuid, text, uuid, uuid, uuid)")
    op.execute("ALTER TABLE export_run_evidence DROP COLUMN prototype_decision_id")
