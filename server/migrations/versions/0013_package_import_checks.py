"""Import branches for check reports and decisions (ADR 0014).

An imported report keeps its job, run, draft and citation bindings and its
coverage; the live attempt, the currency of its inputs against later edits and
the human actor belong to the installation that wrote it.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.check_child_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE r public.check_runs%ROWTYPE; item public.check_items%ROWTYPE;
  response public.response_items%ROWTYPE; req public.requirements%ROWTYPE;
  finding public.check_findings%ROWTYPE; revision public.response_card_revisions%ROWTYPE;
  proof public.evidence%ROWTYPE; raw_text text; report_xid text;
  cert public.certificate_revisions%ROWTYPE; expected_date_status text;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE id=NEW.report_id;
  SELECT xmin::text INTO report_xid FROM public.check_runs WHERE id=NEW.report_id;
  IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Historical check report is closed' USING ERRCODE='42501';
  END IF;
  IF public.citation_publication_is_sealed(NEW.report_id,'check') THEN
    RAISE EXCEPTION 'Completed check publication is closed' USING ERRCODE='42501';
  END IF;
  -- An imported report finished in the exporting installation; its bindings are still checked.
  IF NOT public.package_imported(r.task_id) THEN PERFORM public.check_live_attempt(NEW.report_id); END IF;
  IF TG_TABLE_NAME='check_items' THEN
    IF (r.mode='rules' AND NEW.semantic_status<>'not_requested')
      OR (r.mode='combined' AND ((NEW.partition='response' AND NEW.semantic_status='not_requested')
        OR (NEW.partition<>'response' AND NEW.semantic_status<>'not_requested'))) THEN
      RAISE EXCEPTION 'Check semantic coverage must match mode' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE id=NEW.response_item_id;
    SELECT * INTO req FROM public.requirements WHERE id=NEW.requirement_id;
    IF NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
      OR NEW.partition IS DISTINCT FROM (CASE response.kind WHEN 'row' THEN 'response' ELSE response.kind END)
      OR NEW.source IS DISTINCT FROM response.source
      OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,'page',req.page,'location',req.location,'quote',req.quote) THEN
      RAISE EXCEPTION 'Check item source or partition mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificates' THEN
    SELECT * INTO cert FROM public.certificate_revisions WHERE id=NEW.certificate_revision_id;
    expected_date_status := CASE
      WHEN (cert.data->>'valid_until')::date < NEW.assessment_date THEN 'expired'
      WHEN (cert.data->>'valid_from')::date > NEW.assessment_date THEN 'not_yet_valid'
      WHEN cert.data->>'valid_from' IS NOT NULL AND cert.data->>'valid_until' IS NOT NULL THEN 'valid'
      ELSE 'unknown' END;
    IF NEW.assessment_date IS DISTINCT FROM r.assessment_date OR NEW.date_status IS DISTINCT FROM expected_date_status
      OR NOT EXISTS(SELECT 1 FROM public.task_certificates WHERE id=NEW.task_certificate_id AND (active OR public.package_imported(r.task_id))) THEN
      RAISE EXCEPTION 'Check certificate dates or selection mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificate_items' THEN
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.check_items i
      ON i.report_id=a.report_id
      JOIN public.response_items s ON s.id=i.response_item_id
      JOIN public.card_evidence_links l ON l.revision_id=s.card_revision_id
      JOIN public.evidence e ON e.id=l.evidence_id
      WHERE a.id=NEW.certificate_id AND i.id=NEW.check_item_id
        AND a.report_id=NEW.report_id AND s.kind='row' AND e.confirmed_at IS NOT NULL
        AND e.task_certificate_id=a.task_certificate_id AND e.certificate_revision_id=a.certificate_revision_id
        AND (public.response_evidence_active(e.id) OR public.package_imported(r.task_id))) THEN
      RAISE EXCEPTION 'Check certificate lacks confirmed requirement binding' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_findings' THEN
    SELECT * INTO item FROM public.check_items WHERE id=NEW.check_item_id;
    SELECT * INTO revision FROM public.response_card_revisions WHERE id=item.card_revision_id;
    IF NEW.method='semantic' AND (r.mode<>'combined' OR item.semantic_status<>'assessed'
      OR item.semantic_outcome IS DISTINCT FROM 'risk') THEN
      RAISE EXCEPTION 'Semantic finding requires assessed risk' USING ERRCODE='23514';
    END IF;
    IF NEW.source IS DISTINCT FROM item.source OR NEW.review_domain IS DISTINCT FROM revision.review_domain
      OR (SELECT count(*) FROM public.check_findings WHERE check_item_id=NEW.check_item_id)>=20 THEN
      RAISE EXCEPTION 'Check finding source, domain or count mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_finding_citations' THEN
    SELECT * INTO finding FROM public.check_findings WHERE id=NEW.finding_id;
    IF num_nonnulls(NEW.finding_id,NEW.check_item_id)<>1 THEN
      RAISE EXCEPTION 'Check citation requires exactly one target' USING ERRCODE='23514';
    END IF;
    SELECT * INTO item FROM public.check_items WHERE id=COALESCE(NEW.check_item_id,finding.check_item_id);
    IF item.id IS NULL OR item.report_id IS DISTINCT FROM NEW.report_id
      OR item.task_id IS DISTINCT FROM NEW.task_id
      OR (NEW.check_item_id IS NOT NULL AND (r.mode<>'combined'
        OR item.semantic_status<>'assessed' OR item.semantic_outcome IS DISTINCT FROM 'no_risk_found')) THEN
      RAISE EXCEPTION 'Check citation target mismatch' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE id=item.response_item_id;
    IF (SELECT count(*) FROM public.check_finding_citations WHERE ((NEW.finding_id IS NOT NULL AND finding_id=NEW.finding_id)
        OR (NEW.check_item_id IS NOT NULL AND check_item_id=NEW.check_item_id)))>=20 THEN
      RAISE EXCEPTION 'Check citation limit exceeded' USING ERRCODE='23514';
    END IF;
    IF NEW.kind='tender' THEN
      SELECT * INTO req FROM public.requirements WHERE id=item.requirement_id;
      IF NEW.source IS DISTINCT FROM jsonb_set(item.source,'{quote}',to_jsonb(NEW.quote))
        OR (finding.method='deterministic' AND NEW.quote IS DISTINCT FROM req.quote)
        OR NEW.document_id IS DISTINCT FROM req.document_id OR NEW.chunk_id IS DISTINCT FROM req.chunk_id THEN
        RAISE EXCEPTION 'Check tender citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := req.quote;
    ELSIF NEW.kind='draft' THEN
      IF NEW.response_item_id IS DISTINCT FROM response.id OR NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
        OR NEW.draft_id IS DISTINCT FROM r.draft_id OR response.kind IS DISTINCT FROM 'row' THEN
        RAISE EXCEPTION 'Check response citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := CASE NEW.field WHEN 'response_text' THEN response.response_text WHEN 'deviation_note' THEN response.deviation_note END;
    ELSIF NEW.kind='evidence' THEN
      SELECT * INTO proof FROM public.evidence WHERE id=NEW.evidence_id;
      IF proof.confirmed_at IS NULL OR proof.kind='image_region'
        OR (public.response_evidence_active(proof.id) IS DISTINCT FROM true AND NOT public.package_imported(r.task_id))
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE revision_id=item.card_revision_id AND evidence_id=proof.id)
        OR response.kind IS DISTINCT FROM 'row' THEN
        RAISE EXCEPTION 'Check evidence citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := proof.quote;
    END IF;
    IF raw_text IS NULL OR position(NEW.quote in raw_text)=0
      OR position(NEW.quote in substring(raw_text from position(NEW.quote in raw_text)+1))>0 THEN
      RAISE EXCEPTION 'Check citation must be a unique exact span' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.check_decision_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE f public.check_findings%ROWTYPE; r public.check_runs%ROWTYPE; previous public.check_decisions%ROWTYPE;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE id=NEW.report_id;
  PERFORM 1 FROM public.tasks WHERE id=r.task_id FOR SHARE;
  PERFORM pg_advisory_xact_lock(hashtextextended('check.finding:'||NEW.finding_id::text,0));
  SELECT * INTO f FROM public.check_findings WHERE id=NEW.finding_id;
  PERFORM public.response_check_actor(NULL,'session');
  IF NEW.actor_kind IS DISTINCT FROM 'session' OR (NOT public.actor_is_human() AND NOT public.package_imported(r.task_id)) THEN
    RAISE EXCEPTION 'Check decision requires the responsible human' USING ERRCODE='42501';
  END IF;
  SELECT * INTO previous FROM public.check_decisions WHERE finding_id=NEW.finding_id ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>coalesce(previous.revision,1)+1
    OR NEW.action IS DISTINCT FROM (CASE WHEN previous.action='dismiss' THEN 'reopen' ELSE 'dismiss' END)
    OR NEW.expected_input_hash IS DISTINCT FROM r.input_hash THEN
    RAISE EXCEPTION 'Check decision revision or input conflict' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.report_id) IS DISTINCT FROM true AND NOT public.package_imported(r.task_id) THEN
    RAISE EXCEPTION 'Stale check report cannot receive decisions' USING ERRCODE='23514';
  END IF;
  IF NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'Check decision reason hash mismatch' USING ERRCODE='23514';
  END IF;
  IF NOT public.package_imported(r.task_id) THEN NEW.decided_at := clock_timestamp(); END IF;
  RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.check_run_complete()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE total integer; actual integer; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
  IF j.run_id IS DISTINCT FROM NEW.run_id OR (NOT public.package_imported(NEW.task_id) AND (j.status NOT IN ('running','succeeded')
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp())) THEN
    RAISE EXCEPTION 'Check attempt lost before publication' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO total FROM public.requirements WHERE task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
  SELECT count(*) INTO actual FROM public.check_items WHERE report_id=NEW.id;
  IF total NOT BETWEEN 1 AND 2000 OR actual<>total
    OR actual<>(SELECT count(*) FROM public.response_items WHERE draft_id=NEW.draft_id) THEN
    RAISE EXCEPTION 'Check must cover the entire draft exactly once' USING ERRCODE='23514';
  END IF;
  -- Currency against later edits applies to new reports; an imported report is historical.
  IF NOT public.package_imported(NEW.task_id) AND EXISTS(SELECT 1 FROM public.check_items i JOIN public.response_items s ON s.id=i.response_item_id
    LEFT JOIN public.response_cards c ON c.id=s.card_id
    WHERE i.report_id=NEW.id AND
      ((s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.requirement_id=s.requirement_id))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e ON e.id=l.evidence_id
        WHERE l.revision_id=s.card_revision_id AND (e.confirmed_at IS NULL OR public.response_evidence_active(e.id) IS DISTINCT FROM true)))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.response_card_revisions v WHERE v.id=s.card_revision_id
        AND public.response_generation_materials_active(v.model_job_id) IS DISTINCT FROM true)))) THEN
    RAISE EXCEPTION 'Check draft dependencies changed' USING ERRCODE='23514';
  END IF;
  IF NOT public.package_imported(NEW.task_id) AND (SELECT count(*) FROM public.task_certificates WHERE task_id=NEW.task_id AND active)
    <> (SELECT count(*) FROM public.check_certificates WHERE report_id=NEW.id)
    OR EXISTS(SELECT 1 FROM public.check_certificates c JOIN public.task_certificates s ON s.id=c.task_certificate_id
      WHERE c.report_id=NEW.id AND NOT s.active) THEN
    RAISE EXCEPTION 'Check must include every selected certificate' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.report_id=NEW.id AND
    NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.finding_id=f.id AND c.kind='tender')) THEN
    RAISE EXCEPTION 'Check finding requires a tender citation' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i WHERE i.report_id=NEW.id AND (
    (NEW.mode='rules' AND i.semantic_status<>'not_requested')
    OR (NEW.mode='combined' AND ((i.partition='response' AND i.semantic_status='not_requested')
      OR (i.partition<>'response' AND i.semantic_status<>'not_requested')))
    OR (i.semantic_outcome='risk' AND NOT EXISTS(SELECT 1 FROM public.check_findings f
      WHERE f.check_item_id=i.id AND f.method='semantic'))
    OR (i.semantic_outcome='no_risk_found' AND (
      NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.check_item_id=i.id AND c.kind='tender')
      OR NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.check_item_id=i.id AND c.kind IN ('draft','evidence'))))
    OR ((i.semantic_outcome IS DISTINCT FROM 'risk' OR i.semantic_status<>'assessed')
      AND EXISTS(SELECT 1 FROM public.check_findings f WHERE f.check_item_id=i.id AND f.method='semantic')))) THEN
    RAISE EXCEPTION 'Check semantic outcome lacks matching support' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.report_id=NEW.id
    AND f.method='semantic' AND f.code='semantic_contradiction'
    AND NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.finding_id=f.id AND c.kind IN ('draft','evidence'))) THEN
    RAISE EXCEPTION 'Semantic contradiction requires bid support' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.id) IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id) THEN
    RAISE EXCEPTION 'Check fixed input changed' USING ERRCODE='23514';
  END IF;
  IF (NEW.summary->>'item_count')::integer IS DISTINCT FROM actual
    OR (NEW.summary->>'finding_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_findings WHERE report_id=NEW.id)
    OR (NEW.summary->>'unassessed_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_items i
      WHERE i.report_id=NEW.id AND (i.semantic_status='unassessed' OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.rules) x WHERE x->>'outcome'='unknown')))
    OR NEW.completion IS DISTINCT FROM (CASE WHEN (NEW.summary->>'unassessed_count')::integer>0 THEN 'partial' ELSE 'complete' END) THEN
    RAISE EXCEPTION 'Check summary must match published coverage' USING ERRCODE='23514';
  END IF;
  PERFORM public.citation_seal_publication(NEW.id,'check');
  RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION public.check_run_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE j public.jobs%ROWTYPE; d public.draft_runs%ROWTYPE;
BEGIN
  PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
  IF NEW.actor_kind IS DISTINCT FROM 'worker'
    OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens
      WHERE id=NEW.actor_token_id AND scopes ? 'check:run')) THEN
    RAISE EXCEPTION 'Check worker permission required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
  SELECT * INTO d FROM public.draft_runs WHERE id=NEW.draft_id;
  IF j.kind IS DISTINCT FROM 'check' OR j.run_id IS DISTINCT FROM NEW.run_id
    OR (NOT public.package_imported(NEW.task_id) AND (j.status IS DISTINCT FROM 'running'
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()))
    OR j.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
    OR j.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
    OR (j.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input AND NOT public.package_imported(NEW.task_id))

    OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
    OR NEW.input_manifest->>'draft_id' IS DISTINCT FROM NEW.draft_id::text
    OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text
    OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text
    OR NEW.input_manifest->>'assessment_date' IS DISTINCT FROM to_char(NEW.assessment_date,'YYYY-MM-DD')
    OR NEW.input_manifest->>'draft_input_hash' IS DISTINCT FROM NEW.draft_input_hash
    OR NEW.input_manifest->>'mode' IS DISTINCT FROM NEW.mode
    OR NEW.input_manifest->>'prompt_version' IS DISTINCT FROM NEW.prompt_version
    OR (NEW.mode='combined' AND NEW.input_manifest->'model_redaction_enabled' IS DISTINCT FROM 'true'::jsonb)
    OR NEW.input_manifest->>'rule_version' IS DISTINCT FROM NEW.rule_version
    OR NEW.input_manifest->>'schema_version' IS DISTINCT FROM NEW.schema_version
    OR jsonb_typeof(NEW.input_manifest->'items') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'certificates') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'confidential') IS DISTINCT FROM 'array'
    OR d.input_hash IS DISTINCT FROM NEW.draft_input_hash
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=NEW.extraction_job_id AND kind='extract' AND status='succeeded') THEN
    RAISE EXCEPTION 'Invalid check publication attempt or input' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
