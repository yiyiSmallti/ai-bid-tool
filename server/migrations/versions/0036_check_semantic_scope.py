"""Keep semantic coverage within confirmed response partitions.

Replace the two publication gates with their existing complete checks, changing
only the mode/partition/status combination. Gap and comply-only rows retain local
rule findings, with no semantic outcome, reason or support. Historical reports
remain immutable and retain their recorded coverage.
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(GATE_SQL)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain check semantic scope reports")


GATE_SQL = r"""
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
      OR (r.mode='combined' AND ((NEW.partition='response' AND NEW.semantic_status='not_requested')
        OR (NEW.partition<>'response' AND NEW.semantic_status<>'not_requested'))) THEN
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
    OR (NEW.mode='combined' AND ((i.partition='response' AND i.semantic_status='not_requested')
      OR (i.partition<>'response' AND i.semantic_status<>'not_requested')))
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
