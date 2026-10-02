"""Preserve model input dependencies through human review and draft consumption."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    # Catalog identity includes a 40-character model id plus adapter and revision.
    op.execute("ALTER TABLE card_generation_runs ALTER COLUMN adapter_version TYPE varchar(100)")
    op.execute("""
    CREATE FUNCTION response_generation_materials_active(p_org uuid,p_job uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT p_job IS NULL OR EXISTS (
        SELECT 1 FROM public.card_generation_runs g WHERE g.org_id=p_org AND g.generation_job_id=p_job
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(g.input_manifest->'materials') m
            WHERE CASE m->>'kind'
              WHEN 'product' THEN NOT EXISTS (SELECT 1 FROM public.task_resources s
                WHERE s.org_id=p_org AND s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.product_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'feature' THEN NOT EXISTS (SELECT 1 FROM public.task_features s
                WHERE s.org_id=p_org AND s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.feature_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'certificate' THEN NOT EXISTS (SELECT 1 FROM public.task_certificates s
                WHERE s.org_id=p_org AND s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.certificate_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'certificate_pdf_page' THEN NOT EXISTS (SELECT 1 FROM public.task_certificates s
                JOIN public.evidence_sources e ON e.org_id=s.org_id AND e.task_certificate_id=s.id
                WHERE s.org_id=p_org AND s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.certificate_revision_id=(m->>'revision_id')::uuid AND s.active
                AND e.id=(m->>'evidence_source_id')::uuid AND e.page=(m->>'page')::int)
              WHEN 'org_profile' THEN NOT EXISTS (SELECT 1 FROM public.task_org_profiles s
                WHERE s.org_id=p_org AND s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.profile_revision_id=(m->>'revision_id')::uuid AND s.active)
              ELSE true END))
    $$;
    CREATE FUNCTION response_generation_revision_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE previous public.response_card_revisions%ROWTYPE;
    BEGIN
      SELECT * INTO previous FROM public.response_card_revisions
        WHERE org_id=NEW.org_id AND card_id=NEW.card_id AND revision=NEW.revision-1;
      IF previous.state IN ('pending_review','confirmed') AND
        NEW.model_job_id IS DISTINCT FROM previous.model_job_id THEN
        RAISE EXCEPTION 'Protected model dependencies cannot change' USING ERRCODE='23514';
      END IF;
      IF NEW.origin='model' AND previous.deviation='negative' AND NEW.deviation<>'negative' THEN
        RAISE EXCEPTION 'Model cannot weaken negative deviation' USING ERRCODE='23514';
      END IF;
      IF NEW.state='confirmed' AND
        public.response_generation_materials_active(NEW.org_id,NEW.model_job_id) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Model input material requires renewed review' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER response_generation_revision_gate AFTER INSERT ON response_card_revisions
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION response_generation_revision_gate();
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION response_item_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE run public.draft_runs%ROWTYPE; run_xid text; requirement public.requirements%ROWTYPE;
            card public.response_cards%ROWTYPE; revision public.response_card_revisions%ROWTYPE; target_table text;
            citation_ok boolean; material_ok boolean; quote_current boolean; expected_reasons jsonb := '[]'::jsonb;
    BEGIN
      SELECT * INTO run FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      SELECT d.xmin::text INTO run_xid FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      IF run_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical draft snapshot is closed' USING ERRCODE='42501';
      END IF;
      SELECT * INTO requirement FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
      IF requirement.id IS NULL OR requirement.task_id IS DISTINCT FROM run.task_id
        OR requirement.job_id IS DISTINCT FROM run.extraction_job_id
        OR NEW.category IS DISTINCT FROM requirement.category
        OR NEW.starred IS DISTINCT FROM requirement.starred
        OR NEW.source->>'quote' IS DISTINCT FROM requirement.quote
        OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',requirement.document_id,
            'chunk_id',requirement.chunk_id,'page',requirement.page,'location',requirement.location,
            'quote',requirement.quote) THEN
        RAISE EXCEPTION 'Response item source binding invalid' USING ERRCODE='23514';
      END IF;
      IF NEW.card_id IS NOT NULL THEN
        SELECT * INTO card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
        SELECT * INTO revision FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id;
        IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id
          OR card.current_revision_id IS DISTINCT FROM revision.id THEN
          RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      citation_ok := public.response_citation_valid(NEW.org_id,NEW.requirement_id);
      material_ok := NOT EXISTS (SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=NEW.org_id
        AND l.revision_id=NEW.card_revision_id AND public.response_evidence_active(l.org_id,l.evidence_id) IS DISTINCT FROM true);
      material_ok := material_ok AND public.response_generation_materials_active(NEW.org_id,revision.model_job_id);
      quote_current := public.response_quote_current(NEW.org_id,NEW.card_revision_id);
      IF NEW.kind IN ('row','comply_only') AND
        (citation_ok IS DISTINCT FROM true OR quote_current IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
      END IF;
      IF NEW.kind='gap' THEN
        IF citation_ok AND quote_current AND (revision.disposition='comply_only' OR
          (revision.state='confirmed' AND revision.review_domain IS NOT NULL AND material_ok)) THEN
          RAISE EXCEPTION 'Eligible response cannot be hidden as gap' USING ERRCODE='23514';
        END IF;
        IF NEW.card_id IS NULL THEN expected_reasons := expected_reasons || '["missing_card"]'::jsonb;
        ELSE
          IF revision.state<>'confirmed' THEN expected_reasons := expected_reasons ||
            jsonb_build_array(CASE WHEN revision.state IN ('rejected','needs_material') THEN revision.state ELSE 'unconfirmed' END); END IF;
          IF revision.review_domain IS NULL THEN expected_reasons := expected_reasons || '["unclassified"]'::jsonb; END IF;
          IF NOT material_ok THEN expected_reasons := expected_reasons || '["stale_material"]'::jsonb; END IF;
          IF citation_ok AND quote_current IS DISTINCT FROM true THEN
            expected_reasons := expected_reasons || '["needs_reconfirmation"]'::jsonb;
          END IF;
        END IF;
        IF citation_ok IS DISTINCT FROM true THEN expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb; END IF;
        IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN
          RAISE EXCEPTION 'Gap reasons must reflect current inputs' USING ERRCODE='23514';
        END IF;
      END IF;
      IF NEW.kind='row' AND material_ok IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Model input material requires renewed review' USING ERRCODE='23514';
      END IF;
      IF NEW.kind='row' THEN
        target_table := CASE WHEN requirement.starred OR requirement.category='substantive' THEN 'substantive' ELSE revision.review_domain END;
        IF revision.state IS DISTINCT FROM 'confirmed' OR revision.disposition IS DISTINCT FROM 'respond'
          OR NEW."table" IS DISTINCT FROM target_table
          OR ROW(NEW.response_kind,NEW.response_text,NEW.deviation,NEW.deviation_note)
            IS DISTINCT FROM ROW(revision.response_kind,revision.response_text,revision.deviation,revision.deviation_note) THEN
          RAISE EXCEPTION 'Response row is not confirmed content' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.kind='comply_only' THEN
        IF revision.disposition IS DISTINCT FROM 'comply_only' OR revision.review_domain IS NULL
          OR ROW(NEW.disposition_by,NEW.disposition_at) IS DISTINCT FROM ROW(revision.disposition_by,revision.disposition_at) THEN
          RAISE EXCEPTION 'Comply-only item lacks human disposition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)


def downgrade():
    raise RuntimeError(
        "Keep model input dependencies and review history; rollback is application-only"
    )
