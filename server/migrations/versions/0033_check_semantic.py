"""Persist verified semantic outcomes without inventing risk findings.

0032 intentionally permits rules-only reports. Extend that boundary in place:
existing citation rows keep their finding target; accepted no-risk conclusions gain
an item target on the same RLS-protected, immutable citation table. Replacing the
two trigger bodies preserves the original parent/current-attempt gates while adding
semantic coverage and supporting-quote checks. Historical records are unchanged.
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(CONSTRAINT_SQL)
    op.execute(GATE_SQL)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain semantic check reports")


CONSTRAINT_SQL = r"""
ALTER TABLE check_runs DROP CONSTRAINT check_run_mode,
  ADD CONSTRAINT check_run_mode CHECK (
    (mode='rules' AND prompt_version IS NULL)
    OR (mode='combined' AND prompt_version IS NOT NULL AND prompt_version ~ '[^[:space:]]'));
ALTER TABLE check_items ADD COLUMN semantic_outcome VARCHAR(20),
  DROP CONSTRAINT check_item_semantic,
  ADD CONSTRAINT check_item_semantic CHECK (
    (semantic_status='not_requested' AND semantic_outcome IS NULL AND semantic_reason_code IS NULL)
    OR (semantic_status='assessed' AND semantic_outcome IS NOT NULL
      AND semantic_outcome IN ('no_risk_found','risk') AND semantic_reason_code IS NULL)
    OR (semantic_status='unassessed' AND semantic_outcome IS NOT NULL AND semantic_outcome='unknown'
      AND semantic_reason_code IS NOT NULL AND semantic_reason_code='semantic_unknown')
    OR (semantic_status='unassessed' AND semantic_outcome IS NULL
      AND semantic_reason_code IS NOT NULL AND semantic_reason_code IN (
        'missing_requirement_id','duplicate_requirement_id','unknown_requirement_id',
        'cross_requirement_citation','ref_not_sent','quote_not_at_position','ambiguous_quote',
        'redacted_input_unassessable','sensitive_model_output','unknown_placeholder',
        'insufficient_citations','invalid_semantic_answer','check_finding_limit','semantic_provider_failed')));
ALTER TABLE check_findings DROP CONSTRAINT check_finding_kind,
  ADD CONSTRAINT check_finding_kind CHECK (
    (method='deterministic' AND code IN ('mandatory_response_missing','negative_deviation','unconfirmed_evidence',
      'certificate_expired','certificate_not_yet_valid','certificate_date_unknown'))
    OR (method='semantic' AND code IN ('semantic_contradiction','insufficient_support','obligation_coverage_uncertain')));
ALTER TABLE check_finding_citations ALTER COLUMN finding_id DROP NOT NULL,
  ADD COLUMN check_item_id UUID,
  ADD CONSTRAINT check_citation_one_target CHECK(num_nonnulls(finding_id,check_item_id)=1),
  ADD CONSTRAINT check_citation_item_binding FOREIGN KEY(org_id,check_item_id,task_id,report_id)
    REFERENCES check_items(org_id,id,task_id,report_id);
CREATE INDEX ix_check_citations_item ON check_finding_citations(org_id,check_item_id)
  WHERE check_item_id IS NOT NULL;
"""

GATE_SQL = r"""
CREATE OR REPLACE FUNCTION check_run_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE j public.jobs%ROWTYPE; d public.draft_runs%ROWTYPE;
BEGIN
  PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
  IF NEW.actor_kind IS DISTINCT FROM 'worker' OR NOT EXISTS(SELECT 1 FROM public.memberships
    WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND role IN ('admin','bidder','technical') AND active)
    OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens
      WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'check:run')) THEN
    RAISE EXCEPTION 'Check worker permission required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
  SELECT * INTO d FROM public.draft_runs WHERE org_id=NEW.org_id AND id=NEW.draft_id;
  IF j.kind IS DISTINCT FROM 'check' OR j.status IS DISTINCT FROM 'running'
    OR j.run_id IS DISTINCT FROM NEW.run_id OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
    OR j.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
    OR j.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
    OR j.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
    OR NEW.input_manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text
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
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id AND kind='extract' AND status='succeeded') THEN
    RAISE EXCEPTION 'Invalid check publication attempt or input' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION check_child_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.check_runs%ROWTYPE; item public.check_items%ROWTYPE;
  response public.response_items%ROWTYPE; req public.requirements%ROWTYPE;
  finding public.check_findings%ROWTYPE; revision public.response_card_revisions%ROWTYPE;
  proof public.evidence%ROWTYPE; raw_text text; report_xid text;
  cert public.certificate_revisions%ROWTYPE; expected_date_status text;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE org_id=NEW.org_id AND id=NEW.report_id;
  SELECT xmin::text INTO report_xid FROM public.check_runs WHERE org_id=NEW.org_id AND id=NEW.report_id;
  IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Historical check report is closed' USING ERRCODE='42501';
  END IF;
  PERFORM public.check_live_attempt(NEW.org_id,NEW.report_id);
  IF TG_TABLE_NAME='check_items' THEN
    IF (r.mode='rules' AND NEW.semantic_status<>'not_requested')
      OR (r.mode='combined' AND NEW.semantic_status='not_requested') THEN
      RAISE EXCEPTION 'Check semantic coverage must match mode' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
    SELECT * INTO req FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
    IF NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
      OR NEW.partition IS DISTINCT FROM (CASE response.kind WHEN 'row' THEN 'response' ELSE response.kind END)
      OR NEW.source IS DISTINCT FROM response.source
      OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,'page',req.page,'location',req.location,'quote',req.quote)
      OR public.response_citation_valid(NEW.org_id,NEW.requirement_id) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Check item source or partition mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificates' THEN
    SELECT * INTO cert FROM public.certificate_revisions WHERE org_id=NEW.org_id AND id=NEW.certificate_revision_id;
    expected_date_status := CASE
      WHEN (cert.data->>'valid_until')::date < NEW.assessment_date THEN 'expired'
      WHEN (cert.data->>'valid_from')::date > NEW.assessment_date THEN 'not_yet_valid'
      WHEN cert.data->>'valid_from' IS NOT NULL AND cert.data->>'valid_until' IS NOT NULL THEN 'valid'
      ELSE 'unknown' END;
    IF NEW.assessment_date IS DISTINCT FROM r.assessment_date OR NEW.date_status IS DISTINCT FROM expected_date_status
      OR NOT EXISTS(SELECT 1 FROM public.task_certificates WHERE org_id=NEW.org_id AND id=NEW.task_certificate_id AND active) THEN
      RAISE EXCEPTION 'Check certificate dates or selection mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificate_items' THEN
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.check_items i
      ON i.org_id=a.org_id AND i.report_id=a.report_id
      JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
      JOIN public.card_evidence_links l ON l.org_id=s.org_id AND l.revision_id=s.card_revision_id
      JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
      WHERE a.org_id=NEW.org_id AND a.id=NEW.certificate_id AND i.id=NEW.check_item_id
        AND a.report_id=NEW.report_id AND s.kind='row' AND e.confirmed_by IS NOT NULL
        AND e.task_certificate_id=a.task_certificate_id AND e.certificate_revision_id=a.certificate_revision_id
        AND public.response_evidence_active(e.org_id,e.id)) THEN
      RAISE EXCEPTION 'Check certificate lacks confirmed requirement binding' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_findings' THEN
    SELECT * INTO item FROM public.check_items WHERE org_id=NEW.org_id AND id=NEW.check_item_id;
    SELECT * INTO revision FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=item.card_revision_id;
    IF NEW.method='semantic' AND (r.mode<>'combined' OR item.semantic_status<>'assessed'
      OR item.semantic_outcome IS DISTINCT FROM 'risk') THEN
      RAISE EXCEPTION 'Semantic finding requires assessed risk' USING ERRCODE='23514';
    END IF;
    IF NEW.source IS DISTINCT FROM item.source OR NEW.review_domain IS DISTINCT FROM revision.review_domain
      OR (SELECT count(*) FROM public.check_findings WHERE org_id=NEW.org_id AND check_item_id=NEW.check_item_id)>=20 THEN
      RAISE EXCEPTION 'Check finding source, domain or count mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_finding_citations' THEN
    SELECT * INTO finding FROM public.check_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id;
    IF num_nonnulls(NEW.finding_id,NEW.check_item_id)<>1 THEN
      RAISE EXCEPTION 'Check citation requires exactly one target' USING ERRCODE='23514';
    END IF;
    SELECT * INTO item FROM public.check_items WHERE org_id=NEW.org_id
      AND id=COALESCE(NEW.check_item_id,finding.check_item_id);
    IF item.id IS NULL OR item.report_id IS DISTINCT FROM NEW.report_id
      OR item.task_id IS DISTINCT FROM NEW.task_id
      OR (NEW.check_item_id IS NOT NULL AND (r.mode<>'combined'
        OR item.semantic_status<>'assessed' OR item.semantic_outcome IS DISTINCT FROM 'no_risk_found')) THEN
      RAISE EXCEPTION 'Check citation target mismatch' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE org_id=NEW.org_id AND id=item.response_item_id;
    IF (SELECT count(*) FROM public.check_finding_citations WHERE org_id=NEW.org_id
      AND ((NEW.finding_id IS NOT NULL AND finding_id=NEW.finding_id)
        OR (NEW.check_item_id IS NOT NULL AND check_item_id=NEW.check_item_id)))>=20 THEN
      RAISE EXCEPTION 'Check citation limit exceeded' USING ERRCODE='23514';
    END IF;
    IF NEW.kind='tender' THEN
      SELECT * INTO req FROM public.requirements WHERE org_id=NEW.org_id AND id=item.requirement_id;
      IF NEW.source IS DISTINCT FROM jsonb_set(item.source,'{quote}',to_jsonb(NEW.quote))
        OR (finding.method='deterministic' AND NEW.quote IS DISTINCT FROM req.quote)
        OR NEW.document_id IS DISTINCT FROM req.document_id OR NEW.chunk_id IS DISTINCT FROM req.chunk_id
        OR public.response_citation_valid(NEW.org_id,req.id) IS DISTINCT FROM true THEN
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
      SELECT * INTO proof FROM public.evidence WHERE org_id=NEW.org_id AND id=NEW.evidence_id;
      IF proof.confirmed_by IS NULL OR proof.kind='image_region'
        OR public.response_evidence_active(NEW.org_id,proof.id) IS DISTINCT FROM true
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=item.card_revision_id AND evidence_id=proof.id)
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
END $$;

CREATE OR REPLACE FUNCTION check_run_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE total integer; actual integer; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
  IF j.status NOT IN ('running','succeeded') OR j.run_id IS DISTINCT FROM NEW.run_id
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() THEN
    RAISE EXCEPTION 'Check attempt lost before publication' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO total FROM public.requirements WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
  SELECT count(*) INTO actual FROM public.check_items WHERE org_id=NEW.org_id AND report_id=NEW.id;
  IF total NOT BETWEEN 1 AND 2000 OR actual<>total
    OR actual<>(SELECT count(*) FROM public.response_items WHERE org_id=NEW.org_id AND draft_id=NEW.draft_id) THEN
    RAISE EXCEPTION 'Check must cover the entire draft exactly once' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i JOIN public.requirements q ON q.org_id=i.org_id AND q.id=i.requirement_id
    WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND
      (i.source IS DISTINCT FROM jsonb_build_object('document_id',q.document_id,'chunk_id',q.chunk_id,'page',q.page,'location',q.location,'quote',q.quote)
       OR public.response_citation_valid(i.org_id,i.requirement_id) IS DISTINCT FROM true
       OR (i.partition IN ('response','comply_only') AND public.response_quote_current(i.org_id,i.card_revision_id) IS DISTINCT FROM true))) THEN
    RAISE EXCEPTION 'Check source changed during publication' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
    LEFT JOIN public.response_cards c ON c.org_id=s.org_id AND c.id=s.card_id
    WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND
      ((s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.org_id=s.org_id AND x.requirement_id=s.requirement_id))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE l.org_id=s.org_id AND l.revision_id=s.card_revision_id AND (e.confirmed_by IS NULL OR public.response_evidence_active(e.org_id,e.id) IS DISTINCT FROM true)))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.response_card_revisions v WHERE v.org_id=s.org_id AND v.id=s.card_revision_id
        AND public.response_generation_materials_active(v.org_id,v.model_job_id) IS DISTINCT FROM true)))) THEN
    RAISE EXCEPTION 'Check draft dependencies changed' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM public.task_certificates WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND active)
    <> (SELECT count(*) FROM public.check_certificates WHERE org_id=NEW.org_id AND report_id=NEW.id)
    OR EXISTS(SELECT 1 FROM public.check_certificates c JOIN public.task_certificates s ON s.org_id=c.org_id AND s.id=c.task_certificate_id
      WHERE c.org_id=NEW.org_id AND c.report_id=NEW.id AND NOT s.active) THEN
    RAISE EXCEPTION 'Check must include every selected certificate' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.org_id=NEW.org_id AND f.report_id=NEW.id AND
    NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.org_id=f.org_id AND c.finding_id=f.id AND c.kind='tender')) THEN
    RAISE EXCEPTION 'Check finding requires a tender citation' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND (
    (NEW.mode='rules' AND i.semantic_status<>'not_requested')
    OR (NEW.mode='combined' AND i.semantic_status='not_requested')
    OR (i.semantic_outcome='risk' AND NOT EXISTS(SELECT 1 FROM public.check_findings f
      WHERE f.org_id=i.org_id AND f.check_item_id=i.id AND f.method='semantic'))
    OR (i.semantic_outcome='no_risk_found' AND (
      NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.org_id=i.org_id AND c.check_item_id=i.id AND c.kind='tender')
      OR NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.org_id=i.org_id AND c.check_item_id=i.id AND c.kind IN ('draft','evidence'))))
    OR ((i.semantic_outcome IS DISTINCT FROM 'risk' OR i.semantic_status<>'assessed')
      AND EXISTS(SELECT 1 FROM public.check_findings f WHERE f.org_id=i.org_id AND f.check_item_id=i.id AND f.method='semantic')))) THEN
    RAISE EXCEPTION 'Check semantic outcome lacks matching support' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.org_id=NEW.org_id AND f.report_id=NEW.id
    AND f.method='semantic' AND f.code='semantic_contradiction'
    AND NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.org_id=f.org_id AND c.finding_id=f.id AND c.kind IN ('draft','evidence'))) THEN
    RAISE EXCEPTION 'Semantic contradiction requires bid support' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Check fixed input changed' USING ERRCODE='23514';
  END IF;
  IF (NEW.summary->>'item_count')::integer IS DISTINCT FROM actual
    OR (NEW.summary->>'finding_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_findings WHERE org_id=NEW.org_id AND report_id=NEW.id)
    OR (NEW.summary->>'unassessed_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_items i
      WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND (i.semantic_status='unassessed' OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.rules) x WHERE x->>'outcome'='unknown')))
    OR NEW.completion IS DISTINCT FROM (CASE WHEN (NEW.summary->>'unassessed_count')::integer>0 THEN 'partial' ELSE 'complete' END) THEN
    RAISE EXCEPTION 'Check summary must match published coverage' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;

"""
