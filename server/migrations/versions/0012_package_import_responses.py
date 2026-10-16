"""Import branches for response cards, revisions, runs and drafts (ADR 0014).

An imported card, revision, run or draft item keeps its requirement, card and
generation bindings and its revision order; the actor, lease and review steps
belong to the installation that wrote it.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.response_card_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    BEGIN
        -- An imported card arrives at its source revision with the same requirement binding.
        IF TG_OP='INSERT' AND public.package_imported(NEW.task_id) THEN
            IF NEW.revision < 1 OR NOT EXISTS (SELECT 1 FROM public.requirements r
              JOIN public.jobs j ON j.id=r.job_id
              WHERE r.id=NEW.requirement_id AND r.task_id=NEW.task_id
              AND r.job_id=NEW.extraction_job_id AND j.task_id=NEW.task_id
              AND j.document_id=r.document_id AND j.kind='extract' AND j.status='succeeded') THEN
                RAISE EXCEPTION 'Invalid response requirement binding' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Cards cannot be deleted' USING ERRCODE='42501';
        ELSIF TG_OP='INSERT' THEN
            IF NEW.revision <> 1 OR NOT EXISTS (SELECT 1 FROM public.requirements r
              JOIN public.jobs j ON j.id=r.job_id
              WHERE r.id=NEW.requirement_id AND r.task_id=NEW.task_id
              AND r.job_id=NEW.extraction_job_id AND j.task_id=NEW.task_id
              AND j.document_id=r.document_id AND j.kind='extract' AND j.status='succeeded') THEN
                RAISE EXCEPTION 'Invalid response requirement binding' USING ERRCODE='23514';
            END IF;
        ELSE
            IF (to_jsonb(NEW)-'revision'-'current_revision_id') IS DISTINCT FROM
               (to_jsonb(OLD)-'revision'-'current_revision_id') OR (NEW.revision<>OLD.revision+1 AND NOT (NEW.revision=1 AND OLD.revision=1 AND EXISTS (SELECT 1 FROM public.response_cards c WHERE c.id=OLD.id AND c.xmin::text=pg_current_xact_id()::text AND NOT EXISTS (SELECT 1 FROM public.response_card_revisions r WHERE r.card_id=OLD.id AND r.revision>1)))) THEN
                RAISE EXCEPTION 'Invalid response pointer advance' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_draft_complete()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    DECLARE total integer; actual integer; gaps integer;
    BEGIN
      -- An imported draft was complete against its source's cards and evidence.
      IF public.package_imported(NEW.task_id) THEN RETURN NULL; END IF;
      IF NOT EXISTS (SELECT 1 FROM public.jobs g WHERE g.id=NEW.generation_job_id
        AND g.status IN ('running','succeeded') AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Draft generation attempt is no longer current' USING ERRCODE='23514';
      END IF;
      SELECT count(*) INTO total FROM public.requirements WHERE task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
      SELECT count(*),count(*) FILTER(WHERE kind='gap') INTO actual,gaps FROM public.response_items
        WHERE draft_id=NEW.id;
      IF total=0 OR actual<>total OR NEW.completion IS DISTINCT FROM (CASE WHEN gaps>0 THEN 'partial' ELSE 'complete' END) THEN
        RAISE EXCEPTION 'Draft must cover every requirement exactly once' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i LEFT JOIN public.response_cards c
        ON c.id=i.card_id WHERE i.draft_id=NEW.id
        AND ((i.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM i.card_revision_id)
          OR (i.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.requirement_id=i.requirement_id)))) THEN
        RAISE EXCEPTION 'Draft input changed' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i WHERE i.draft_id=NEW.id
        AND i.kind IN ('row','comply_only') AND public.response_citation_valid(i.requirement_id) IS DISTINCT FROM true)
        OR EXISTS (SELECT 1 FROM public.response_items i JOIN public.card_evidence_links l
        ON l.revision_id=i.card_revision_id JOIN public.evidence e ON e.id=l.evidence_id
        WHERE i.draft_id=NEW.id AND i.kind='row'
          AND (e.confirmed_at IS NULL OR public.response_evidence_active(e.id) IS DISTINCT FROM true)) THEN
        RAISE EXCEPTION 'Draft material or citation is not eligible' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_item_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    DECLARE run public.draft_runs%ROWTYPE; run_xid text; requirement public.requirements%ROWTYPE;
            card public.response_cards%ROWTYPE; revision public.response_card_revisions%ROWTYPE; target_table text;
            requirement_ok boolean; requirement_state text; citation_ok boolean; material_ok boolean; quote_current boolean; expected_reasons jsonb := '[]'::jsonb;
    BEGIN
      -- An imported draft item keeps its source and card bindings; it was current at its source.
      SELECT * INTO run FROM public.draft_runs d WHERE d.id=NEW.draft_id;
      IF public.package_imported(run.task_id) THEN
        SELECT * INTO requirement FROM public.requirements WHERE id=NEW.requirement_id;
        IF requirement.id IS NULL OR requirement.task_id IS DISTINCT FROM run.task_id
          OR requirement.job_id IS DISTINCT FROM run.extraction_job_id
          OR NEW.category IS DISTINCT FROM requirement.category
          OR NEW.starred IS DISTINCT FROM requirement.starred THEN
          RAISE EXCEPTION 'Response item source binding invalid' USING ERRCODE='23514';
        END IF;
        IF NEW.card_id IS NOT NULL THEN
          SELECT * INTO card FROM public.response_cards WHERE id=NEW.card_id;
          SELECT * INTO revision FROM public.response_card_revisions WHERE id=NEW.card_revision_id;
          IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id THEN
            RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END IF;
      SELECT * INTO run FROM public.draft_runs d WHERE d.id=NEW.draft_id;
      SELECT d.xmin::text INTO run_xid FROM public.draft_runs d WHERE d.id=NEW.draft_id;
      IF run_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical draft snapshot is closed' USING ERRCODE='42501';
      END IF;
      SELECT * INTO requirement FROM public.requirements WHERE id=NEW.requirement_id;
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
        SELECT * INTO card FROM public.response_cards WHERE id=NEW.card_id;
        SELECT * INTO revision FROM public.response_card_revisions WHERE id=NEW.card_revision_id;
        IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id
          OR card.current_revision_id IS DISTINCT FROM revision.id THEN
          RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      requirement_ok := public.requirement_review_current(NEW.requirement_id);
 requirement_state := public.requirement_review_state(NEW.requirement_id);
 IF NEW.kind IN ('row','comply_only') AND NOT requirement_ok THEN RAISE EXCEPTION 'requirement_unconfirmed' USING ERRCODE='23514'; END IF;
      citation_ok := public.response_citation_valid(NEW.requirement_id);
      material_ok := NOT EXISTS (SELECT 1 FROM public.card_evidence_links l WHERE l.revision_id=NEW.card_revision_id AND public.response_evidence_active(l.evidence_id) IS DISTINCT FROM true);
      material_ok := material_ok AND public.response_generation_materials_active(revision.model_job_id);
      quote_current := public.response_quote_current(NEW.card_revision_id);
      IF NEW.kind IN ('row','comply_only') AND
        (citation_ok IS DISTINCT FROM true OR quote_current IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
      END IF;
      IF NEW.kind='gap' THEN
        IF requirement_ok AND citation_ok AND quote_current AND (revision.disposition='comply_only' OR
          (revision.state='confirmed' AND revision.review_domain IS NOT NULL AND material_ok)) THEN
          RAISE EXCEPTION 'Eligible response cannot be hidden as gap' USING ERRCODE='23514';
        END IF;
        IF NOT requirement_ok THEN expected_reasons := jsonb_build_array(CASE WHEN requirement_state='invalidated' THEN 'requirement_invalidated' ELSE 'requirement_unconfirmed' END); END IF;
        IF NEW.card_id IS NULL THEN expected_reasons := expected_reasons || '["missing_card"]'::jsonb;
        ELSE
          IF revision.state<>'confirmed' THEN expected_reasons := expected_reasons ||
            jsonb_build_array(CASE WHEN revision.state IN ('rejected','needs_material') THEN revision.state ELSE 'unconfirmed' END); END IF;
          IF revision.review_domain IS NULL THEN expected_reasons := expected_reasons || '["unclassified"]'::jsonb; END IF;
          IF NOT material_ok THEN expected_reasons := expected_reasons || '["stale_material"]'::jsonb; END IF;
          IF quote_current IS DISTINCT FROM true THEN
            expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb;
          END IF;
        END IF;
        IF citation_ok IS DISTINCT FROM true AND NOT (expected_reasons ? 'invalid_citation') THEN
          expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb;
        END IF;
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
          OR NEW.disposition_at IS DISTINCT FROM revision.disposition_at THEN
          RAISE EXCEPTION 'Comply-only item lacks human disposition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_link_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    DECLARE revision_xid text;
    BEGIN
      -- Imported links belong to revisions the same import wrote.
      IF public.package_imported((SELECT task_id FROM public.response_cards WHERE id=NEW.card_id)) THEN
        IF NOT EXISTS (SELECT 1 FROM public.response_card_revisions WHERE id=NEW.revision_id AND card_id=NEW.card_id) THEN
          RAISE EXCEPTION 'Historical revision links are closed' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
      END IF;
      SELECT xmin::text INTO revision_xid FROM public.response_card_revisions
        WHERE id=NEW.revision_id AND card_id=NEW.card_id;
      IF revision_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical revision links are closed' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_redaction_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    BEGIN
        -- An imported project keeps its redaction setting and revision.
        IF TG_OP='INSERT' AND public.package_imported(NEW.id) THEN
            IF NEW.model_redaction_revision < 1
              OR (NOT NEW.model_redaction_enabled AND NEW.model_redaction_revision < 2) THEN
                RAISE EXCEPTION 'Invalid imported redaction state' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP='INSERT' THEN
            IF NOT NEW.model_redaction_enabled OR NEW.model_redaction_revision<>1 THEN
                RAISE EXCEPTION 'Initial redaction must be enabled' USING ERRCODE='23514';
            END IF;
        ELSIF ROW(NEW.model_redaction_enabled,NEW.model_redaction_revision)
          IS DISTINCT FROM ROW(OLD.model_redaction_enabled,OLD.model_redaction_revision) THEN
            PERFORM public.response_require_human();
            IF NEW.model_redaction_revision<>OLD.model_redaction_revision+1 THEN
                RAISE EXCEPTION 'Invalid redaction revision' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_revision_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    DECLARE previous public.response_card_revisions%ROWTYPE; card public.response_cards%ROWTYPE;
            category text; decision boolean; content_changed boolean;
    BEGIN
        -- Imported revisions arrive in order with their bindings; review steps were the source's.
        IF public.package_imported((SELECT task_id FROM public.response_cards WHERE id=NEW.card_id)) THEN
            SELECT * INTO STRICT card FROM public.response_cards WHERE id=NEW.card_id FOR UPDATE;
            SELECT r.category INTO category FROM public.requirements r WHERE r.id=card.requirement_id;
            SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
            IF NEW.revision <> COALESCE(previous.revision,0)+1 THEN
                RAISE EXCEPTION 'Response revision conflict' USING ERRCODE='23514';
            END IF;
            IF category='technical' AND NEW.review_domain IS DISTINCT FROM 'technical'
               OR category='qualification' AND NEW.review_domain IS DISTINCT FROM 'commercial' THEN
                RAISE EXCEPTION 'Fixed review domain cannot change' USING ERRCODE='23514';
            END IF;
            IF NEW.origin='model' AND NOT EXISTS (SELECT 1 FROM public.jobs g JOIN public.jobs e
              ON e.id=card.extraction_job_id
              WHERE g.id=NEW.model_job_id AND g.task_id=card.task_id
                AND g.document_id=e.document_id AND g.kind='card_generate') THEN
                RAISE EXCEPTION 'Model origin requires its bound generation job' USING ERRCODE='23514';
            END IF;
            IF NEW.state='confirmed' AND NEW.confirmed_at IS NULL THEN
                RAISE EXCEPTION 'Invalid response confirmation' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END IF;
        PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
        SELECT * INTO STRICT card FROM public.response_cards WHERE id=NEW.card_id FOR UPDATE;
        SELECT r.category INTO category FROM public.requirements r WHERE r.id=card.requirement_id;
        SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
        IF NEW.actor_kind='worker' AND (NEW.state<>'draft'
          OR previous.state IN ('pending_review','confirmed') OR previous.disposition='comply_only') THEN
            RAISE EXCEPTION 'Worker may only write editable drafts' USING ERRCODE='42501';
        END IF;
        IF NEW.origin='model' AND NOT EXISTS (SELECT 1 FROM public.jobs g JOIN public.jobs e
          ON e.id=card.extraction_job_id
          WHERE g.id=NEW.model_job_id AND g.task_id=card.task_id
            AND g.document_id=e.document_id AND g.kind='card_generate' AND g.status='running') THEN
            RAISE EXCEPTION 'Model origin requires its bound generation job' USING ERRCODE='23514';
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_at);
        IF NEW.revision <> COALESCE(previous.revision,0)+1 THEN
            RAISE EXCEPTION 'Response revision conflict' USING ERRCODE='23514';
        END IF;
        IF category='technical' AND NEW.review_domain IS DISTINCT FROM 'technical'
           OR category='qualification' AND NEW.review_domain IS DISTINCT FROM 'commercial' THEN
            RAISE EXCEPTION 'Fixed review domain cannot change' USING ERRCODE='23514';
        END IF;
        IF previous.id IS NULL THEN
            IF NEW.state<>'draft' THEN RAISE EXCEPTION 'Initial state must be draft' USING ERRCODE='23514'; END IF;
            IF category NOT IN ('technical','qualification') AND NEW.review_domain IS NOT NULL THEN
                RAISE EXCEPTION 'Unclassified initial requirement' USING ERRCODE='23514';
            END IF;
        ELSE
            content_changed := ROW(NEW.response_kind,NEW.response_text,NEW.deviation,NEW.deviation_note,NEW.suggested_disposition,NEW.review_hint)
                IS DISTINCT FROM ROW(previous.response_kind,previous.response_text,previous.deviation,previous.deviation_note,previous.suggested_disposition,previous.review_hint);
            IF NOT ((previous.state='draft' AND NEW.state IN ('draft','pending_review'))
              OR (previous.state='pending_review' AND NEW.state IN ('draft','confirmed','rejected','needs_material'))
              OR (previous.state IN ('rejected','needs_material') AND (NEW.state='draft' OR (NEW.state=previous.state AND decision AND NOT content_changed)))
              OR (previous.state='confirmed' AND NEW.state='draft')) THEN
                RAISE EXCEPTION 'Illegal response state transition' USING ERRCODE='23514';
            END IF;
            IF previous.state IN ('pending_review','confirmed') AND content_changed THEN
                RAISE EXCEPTION 'Protected response content cannot change' USING ERRCODE='23514';
            END IF;
            IF previous.disposition='comply_only' AND
                (content_changed OR NEW.state IS DISTINCT FROM previous.state) THEN
                RAISE EXCEPTION 'Comply-only response content is protected' USING ERRCODE='23514';
            END IF;
            IF NEW.review_domain IS DISTINCT FROM previous.review_domain THEN
                PERFORM public.response_require_human();
                IF previous.review_domain IS NOT NULL OR previous.state<>'draft' OR NEW.state<>'draft' OR NEW.reason IS NULL OR content_changed THEN
                    RAISE EXCEPTION 'Invalid classification' USING ERRCODE='23514';
                END IF;
            END IF;
            IF previous.state='confirmed' OR NEW.state IN ('confirmed','rejected','needs_material') THEN
                PERFORM public.response_require_human();
            END IF;
            IF ((previous.state='pending_review' AND NEW.state IN ('draft','rejected','needs_material'))
                OR previous.state='confirmed') AND NEW.reason IS NULL THEN
                RAISE EXCEPTION 'Review reason required' USING ERRCODE='23514';
            END IF;
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_at);
        IF decision THEN
            IF previous.state='confirmed' OR (previous.state='pending_review' AND NOT
              (NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond'))
              OR (previous.id IS NOT NULL AND previous.state<>NEW.state AND NOT
              (previous.state='pending_review' AND NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond')) THEN
                RAISE EXCEPTION 'Disposition must be a separate review decision' USING ERRCODE='23514';
            END IF;
            PERFORM public.response_require_human();
            IF NEW.disposition IS NULL OR NEW.disposition_at IS NULL
                OR (NEW.state<>'confirmed' AND NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Invalid disposition decision' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.state='confirmed' THEN
            PERFORM public.response_require_human();
            IF btrim(NEW.deviation_note)='满足' THEN
                RAISE EXCEPTION 'Concrete deviation explanation required' USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM public.requirements q WHERE q.id=card.requirement_id
              AND (q.quote ~ '提供.*(证书|检测报告|截图|说明书|证明|复印件)'
               OR q.quote ~* '(certificate|report|screenshot|proof).*(provid|attach)|(provid|attach).*(certificate|report|screenshot|proof)'))
               AND (NOT NEW.reviewed_warning_codes ? 'proof_material_required' OR NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Proof material warning requires human review' USING ERRCODE='23514';
            END IF;
            IF NEW.confirmed_at IS NULL OR previous.disposition='comply_only' THEN
                RAISE EXCEPTION 'Invalid response confirmation' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_revision_quote_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
        DECLARE current_hash text; previous_hash text; previous_id uuid;
        BEGIN
          -- An imported revision keeps the citation baseline it was reviewed against.
          IF public.package_imported((SELECT task_id FROM public.response_cards WHERE id=NEW.card_id)) THEN
            IF NEW.quote_sha256 IS NULL THEN
              SELECT encode(sha256(convert_to(r.quote,'UTF8')),'hex') INTO NEW.quote_sha256
                FROM public.requirements r JOIN public.response_cards c ON c.requirement_id=r.id
                WHERE c.id=NEW.card_id;
            END IF;
            RETURN NEW;
          END IF;
          SELECT encode(sha256(convert_to(r.quote,'UTF8')),'hex') INTO current_hash
            FROM public.requirements r JOIN public.response_cards c
              ON c.requirement_id=r.id
            WHERE c.id=NEW.card_id;
          SELECT v.id,coalesce(v.quote_sha256,
              encode(sha256(convert_to(coalesce(r.model_quote,r.quote),'UTF8')),'hex'))
            INTO previous_id,previous_hash
            FROM public.response_card_revisions v
            JOIN public.response_cards c ON c.id=v.card_id
            JOIN public.requirements r ON r.id=c.requirement_id
            WHERE v.card_id=NEW.card_id AND v.revision=NEW.revision-1;
          NEW.quote_sha256 := coalesce(NEW.quote_sha256,current_hash);
          IF NEW.quote_sha256 IS DISTINCT FROM current_hash AND
             (previous_id IS NULL OR NEW.quote_sha256 IS DISTINCT FROM previous_hash) THEN
            RAISE EXCEPTION 'Invalid response citation baseline' USING ERRCODE='23514';
          END IF;
          IF NEW.state='confirmed' AND (NEW.quote_sha256 IS DISTINCT FROM current_hash
             OR previous_hash IS DISTINCT FROM current_hash) THEN
            RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
          END IF;
          IF previous_id IS NOT NULL AND NEW.disposition='comply_only'
             AND NEW.quote_sha256 IS DISTINCT FROM previous_hash THEN
            PERFORM public.response_require_human();
          END IF;
          RETURN NEW;
        END $function$;

CREATE OR REPLACE FUNCTION public.response_run_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    DECLARE expected_kind text;
    BEGIN
      -- An imported run is bound to its finished generation attempt.
      IF public.package_imported(NEW.task_id) THEN
        expected_kind := CASE TG_TABLE_NAME WHEN 'draft_runs' THEN 'draft' ELSE 'card_generate' END;
        IF NOT EXISTS (SELECT 1 FROM public.jobs e JOIN public.jobs g ON g.task_id=e.task_id
          WHERE e.id=NEW.extraction_job_id AND e.task_id=NEW.task_id
          AND e.kind='extract' AND e.status='succeeded' AND g.id=NEW.generation_job_id
          AND g.document_id=e.document_id AND g.kind=expected_kind
          AND NEW.generation_run_id IS NOT NULL AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
          RAISE EXCEPTION 'Invalid response generation job' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
      expected_kind := CASE TG_TABLE_NAME WHEN 'draft_runs' THEN 'draft' ELSE 'card_generate' END;
      IF NOT EXISTS (SELECT 1 FROM public.jobs e JOIN public.jobs g ON g.task_id=e.task_id
        WHERE e.id=NEW.extraction_job_id AND e.task_id=NEW.task_id
        AND e.kind='extract' AND e.status='succeeded' AND g.id=NEW.generation_job_id
        AND g.task_id=e.task_id AND g.document_id=e.document_id AND g.kind=expected_kind
        AND g.status='running' AND NEW.generation_run_id IS NOT NULL AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Invalid response generation job' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $function$;

CREATE OR REPLACE FUNCTION public.response_evidence_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE card_state text; material jsonb; field_value text;
        source public.evidence_sources%ROWTYPE; asset public.screenshot_assets%ROWTYPE;
        rendition public.screenshot_renditions%ROWTYPE; expected_material text;
        selection_active boolean;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Evidence cannot be deleted' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' THEN
    IF (to_jsonb(NEW)-'confirmed_at'-'quote_check') IS DISTINCT FROM
       (to_jsonb(OLD)-'confirmed_at'-'quote_check')
       OR OLD.confirmed_at IS NOT NULL OR NEW.confirmed_at IS NULL THEN
      RAISE EXCEPTION 'Evidence is immutable after confirmation' USING ERRCODE='42501';
    END IF;
  ELSIF NEW.confirmed_at IS NOT NULL AND NOT public.package_imported(NEW.task_id) THEN
    RAISE EXCEPTION 'New evidence must be unconfirmed' USING ERRCODE='23514';
  END IF;
  -- An imported confirmation was made by a person in the exporting installation.
  IF NEW.confirmed_at IS NOT NULL AND NOT public.package_imported(NEW.task_id) THEN
    SELECT r.state INTO card_state
      FROM public.response_cards c JOIN public.response_card_revisions r
        ON r.id=c.current_revision_id
      WHERE c.id=NEW.card_id;
    PERFORM public.response_require_human();
    IF card_state IS DISTINCT FROM 'pending_review'
       OR public.response_evidence_active(NEW.id) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Invalid evidence confirmation' USING ERRCODE='23514';
    END IF;
  END IF;
  CASE NEW.kind
    WHEN 'product' THEN SELECT data INTO material FROM public.product_revisions
      WHERE id=NEW.product_revision_id;
    WHEN 'feature' THEN SELECT data INTO material FROM public.feature_revisions
      WHERE id=NEW.feature_revision_id;
    WHEN 'certificate' THEN SELECT data INTO material FROM public.certificate_revisions
      WHERE id=NEW.certificate_revision_id;
    WHEN 'org_profile' THEN SELECT data INTO material FROM public.org_profile_revisions
      WHERE id=NEW.profile_revision_id;
    WHEN 'certificate_pdf_page' THEN
      SELECT * INTO source FROM public.evidence_sources
        WHERE id=NEW.evidence_source_id;
      IF source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
        OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
        OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
        OR source.page IS DISTINCT FROM NEW.page
        OR source.preview->>'sha256' IS DISTINCT FROM NEW.source_sha256 THEN
        RAISE EXCEPTION 'Evidence page does not match retained source' USING ERRCODE='23514';
      END IF;
    WHEN 'image_region' THEN
      SELECT * INTO asset FROM public.screenshot_assets
        WHERE task_id=NEW.task_id AND id=NEW.screenshot_asset_id;
      SELECT * INTO rendition FROM public.screenshot_renditions
        WHERE task_id=NEW.task_id AND asset_id=NEW.screenshot_asset_id
          AND id=NEW.screenshot_rendition_id AND image_sha256=NEW.image_sha256;
      IF NEW.material_kind='certificate_image' THEN
        SELECT * INTO source FROM public.evidence_sources
          WHERE id=asset.evidence_source_id;
      END IF;
      expected_material := CASE
        WHEN asset.origin='prototype' THEN 'prototype'
        WHEN asset.source_kind='local_browser' THEN 'browser_screenshot'
        WHEN asset.source_kind='certificate_page' THEN 'certificate_image'
        WHEN asset.source_kind IN ('vendor_web','vendor_pdf') THEN asset.source_kind
        WHEN asset.source_kind='upload' AND asset.image_kind='diagram' THEN 'user_diagram'
        WHEN asset.source_kind='upload' AND asset.image_kind='screenshot' THEN 'user_screenshot'
        ELSE NULL END;
      selection_active := CASE
        WHEN NEW.material_kind IN ('user_screenshot','browser_screenshot','user_diagram','prototype')
          THEN EXISTS(SELECT 1 FROM public.task_features s
            WHERE s.id=NEW.task_feature_id
              AND s.feature_revision_id=NEW.feature_revision_id AND s.active)
        WHEN NEW.material_kind IN ('vendor_web','vendor_pdf')
          THEN EXISTS(SELECT 1 FROM public.task_resources s
            WHERE s.id=NEW.task_resource_id
              AND s.product_revision_id=NEW.product_revision_id AND s.active)
        WHEN NEW.material_kind='certificate_image'
          THEN EXISTS(SELECT 1 FROM public.task_certificates s
            WHERE s.id=NEW.task_certificate_id
              AND s.certificate_revision_id=NEW.certificate_revision_id AND s.active)
        ELSE false END;
      IF asset.id IS NULL OR rendition.id IS NULL OR expected_material IS DISTINCT FROM NEW.material_kind
        OR NEW.source_sha256 IS DISTINCT FROM asset.source_sha256
        OR asset.extraction_job_id IS DISTINCT FROM (
          SELECT c.extraction_job_id FROM public.response_cards c
          WHERE c.id=NEW.card_id AND c.task_id=NEW.task_id)
        OR (NEW.region->>'x')::integer+(NEW.region->>'width')::integer
             > (rendition.mapping->>'content_width')::integer
        OR (NEW.region->>'y')::integer+(NEW.region->>'height')::integer
             > (rendition.mapping->>'content_height')::integer
        OR (NEW.task_feature_id,NEW.feature_revision_id) IS DISTINCT FROM
             (asset.task_feature_id,asset.feature_revision_id)
        OR (NEW.task_resource_id,NEW.product_revision_id) IS DISTINCT FROM
             (asset.task_resource_id,asset.product_revision_id)
        OR (NEW.material_kind='certificate_image'
            AND NEW.evidence_source_id IS DISTINCT FROM asset.evidence_source_id)
        OR (NEW.material_kind='certificate_image' AND (
          source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
          OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
          OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
          OR source.page IS DISTINCT FROM NEW.page))
        -- An imported row may name a selection or asset retired after it was written.
        OR (selection_active IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id))
        OR (NEW.material_kind IN ('user_screenshot','browser_screenshot')
            AND (asset.source->>'environment' IS NULL
              OR asset.source->>'environment' NOT IN ('production','test','development')))
        OR (NEW.material_kind='prototype' AND asset.source_kind<>'prototype_render'
            AND asset.source->>'environment' IS DISTINCT FROM 'prototype')
        OR (EXISTS(SELECT 1 FROM public.screenshot_withdrawals w
          WHERE w.asset_id=NEW.screenshot_asset_id) AND NOT public.package_imported(NEW.task_id))
        OR NOT EXISTS(SELECT 1 FROM public.screenshot_privacy_reviews p
          JOIN public.screenshot_renditions released
            ON released.asset_id=p.asset_id
           AND released.id=p.rendition_id AND released.image_sha256=p.stored_image_sha256
          WHERE p.asset_id=NEW.screenshot_asset_id
            AND p.id=rendition.privacy_review_id) THEN
        RAISE EXCEPTION 'Image evidence does not match retained rendition' USING ERRCODE='23514';
      END IF;
    ELSE RAISE EXCEPTION 'Unknown material kind' USING ERRCODE='23514';
  END CASE;
  IF NEW.kind NOT IN ('certificate_pdf_page','image_region') THEN
    IF NOT (CASE NEW.kind
      WHEN 'product' THEN NEW.field_path IN ('name','vendor','model','model_version','official_url','whitepaper_url')
      WHEN 'feature' THEN NEW.field_path IN ('product_id','name','description','status')
      WHEN 'certificate' THEN NEW.field_path IN ('name','number','kind','valid_from','valid_until')
      WHEN 'org_profile' THEN NEW.field_path IN ('name','registration_details','performance_summary','standard_wording')
      ELSE false END) THEN
      RAISE EXCEPTION 'Evidence field is not referenceable' USING ERRCODE='23514';
    END IF;
    field_value := material->>NEW.field_path;
    IF field_value IS NULL OR position(NEW.quote in field_value)=0 THEN
      RAISE EXCEPTION 'Evidence quote is not exact field text' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
