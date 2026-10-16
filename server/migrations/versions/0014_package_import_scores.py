"""Import branches for scoring rubrics and score reports (ADR 0014).

An imported rubric or report keeps its job, run, input and citation bindings
and its review order; the live attempt, the currency of its inputs and the
human actor belong to the installation that wrote it.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.rubric_coverage_item_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE decision_row public.score_rubric_coverage_decisions%ROWTYPE; decision_xmin text;
BEGIN
 SELECT * INTO decision_row FROM public.score_rubric_coverage_decisions WHERE id=NEW.coverage_decision_id;
 SELECT xmin::text INTO decision_xmin FROM public.score_rubric_coverage_decisions WHERE id=NEW.coverage_decision_id;
 PERFORM public.response_check_actor(NULL,'session');
 IF NOT public.package_imported(NEW.task_id) THEN PERFORM public.response_require_human(); END IF;
 IF decision_row.action<>'mapped' OR decision_xmin IS DISTINCT FROM pg_current_xact_id()::text OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE id=NEW.rubric_item_id AND requirement_id=decision_row.requirement_id) THEN
 RAISE EXCEPTION 'Mapped coverage must bind its fixed requirement items' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.rubric_publication_complete()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE new_section public.score_rubric_sections%ROWTYPE;
 owning_job public.jobs%ROWTYPE; citation jsonb;
BEGIN
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO owning_job FROM public.jobs WHERE id=NEW.job_id;
  IF owning_job.run_id IS DISTINCT FROM NEW.run_id OR (NOT public.package_imported(NEW.task_id) AND (owning_job.status NOT IN ('running','succeeded') OR owning_job.lease_until IS NULL OR owning_job.lease_until<=clock_timestamp())) THEN
   RAISE EXCEPTION 'Rubric owning attempt lost before publication' USING ERRCODE='23514'; END IF;
 END IF;
 IF public.rubric_current_inputs(NEW.id) IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id) THEN RAISE EXCEPTION 'Rubric fixed inputs changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_shape_complete(NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric lost completeness before publication' USING ERRCODE='23514'; END IF;

 -- current_inputs verified every scoring requirement in the fixed manifest.
 -- Coverage must refer to that exact set and retain its current source binding.
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
  LEFT JOIN public.requirements req ON req.id=coverage.requirement_id
  WHERE coverage.rubric_id=NEW.id AND (
   req.id IS NULL OR req.task_id IS DISTINCT FROM NEW.task_id OR req.document_id IS DISTINCT FROM NEW.document_id
   OR req.job_id IS DISTINCT FROM NEW.extraction_job_id OR req.category IS DISTINCT FROM 'scoring'
   OR coverage.source->>'document_id' IS DISTINCT FROM req.document_id::text
   OR coverage.source->>'chunk_id' IS DISTINCT FROM req.chunk_id::text
   OR coverage.source->>'quote' IS DISTINCT FROM req.quote OR coverage.source->>'page' IS DISTINCT FROM req.page::text
   OR coalesce(coverage.source->'location','null'::jsonb) IS DISTINCT FROM coalesce(req.location,'null'::jsonb)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.input_manifest->'requirements') entry
     WHERE (entry->>'requirement_id')::uuid=req.id))) THEN
  RAISE EXCEPTION 'Rubric source changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.requirements req WHERE req.job_id=NEW.extraction_job_id AND req.category='scoring' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage c WHERE c.rubric_id=NEW.id AND c.requirement_id=req.id))
 OR (NEW.prior_rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_revision_events WHERE rubric_id=NEW.id)) THEN
 RAISE EXCEPTION 'Rubric publication lacks complete coverage or revision history' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NOT NULL THEN
  -- A human may select a prior verified quotation or the pinned full Source,
  -- but cannot turn arbitrary substring input into new citation provenance.
  FOR new_section IN SELECT * FROM public.score_rubric_sections WHERE rubric_id=NEW.id LOOP
   FOR citation IN SELECT value FROM jsonb_array_elements(new_section.sources) LOOP
    IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections previous,
      LATERAL jsonb_array_elements(public.rubric_section_sources(previous)) verified
      WHERE previous.rubric_id=NEW.prior_rubric_id AND verified.value=citation)
     AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
      WHERE coverage.rubric_id=NEW.prior_rubric_id
       AND coverage.requirement_id::text=citation->>'requirement_id' AND coverage.source=citation->'source'
       AND coverage.source->>'quote'=citation->>'quote') THEN
     RAISE EXCEPTION 'Revision cannot invent section citations' USING ERRCODE='23514';
    END IF;
   END LOOP;
  END LOOP;

 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.rubric_review_complete()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF public.rubric_current_inputs(NEW.rubric_id) IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id) THEN RAISE EXCEPTION 'Rubric inputs changed during review' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' AND ((to_jsonb(NEW)->>'action'='mapped') IS DISTINCT FROM EXISTS(SELECT 1 FROM public.score_rubric_coverage_items WHERE coverage_decision_id=NEW.id)) THEN
  RAISE EXCEPTION 'Mapped coverage requires normalized item links' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_complete(NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Confirmed rubric must remain complete through commit' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.rubric_review_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE set_row public.score_rubric_sets%ROWTYPE; prior_revision integer; subject_section uuid; subject_item uuid; is_set boolean; previous_action text; previous_revision integer;
BEGIN
 PERFORM public.response_check_actor(NULL,'session');
 PERFORM pg_advisory_xact_lock(hashtextextended('rubric.set:'||NEW.rubric_id::text,0));
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 IF public.rubric_current_inputs(NEW.rubric_id) IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id) THEN RAISE EXCEPTION 'Rubric fixed inputs changed' USING ERRCODE='23514'; END IF;
 IF NEW.actor_kind<>'session' OR (NOT public.actor_is_human() AND NOT public.package_imported(NEW.task_id)) OR NEW.expected_input_hash IS DISTINCT FROM set_row.input_hash OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Invalid rubric human review context' USING ERRCODE='42501'; END IF;
 -- Imported decisions arrive table by table, so the order across subjects is the exporter's.
 IF NOT public.package_imported(NEW.task_id) AND (NEW.set_revision<>public.rubric_revision(NEW.rubric_id)+1
 OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE prior_rubric_id=NEW.rubric_id)) THEN
  RAISE EXCEPTION 'Rubric revision conflict' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' THEN
  IF NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE id=NEW.coverage_id AND requirement_id=NEW.requirement_id) THEN RAISE EXCEPTION 'Coverage requires bidder and fixed requirement' USING ERRCODE='42501'; END IF;
  SELECT coalesce(max(revision),1) INTO prior_revision FROM public.score_rubric_coverage_decisions WHERE coverage_id=NEW.coverage_id;
  IF NEW.canonical_requirement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE rubric_id=NEW.rubric_id AND requirement_id=NEW.canonical_requirement_id) THEN RAISE EXCEPTION 'Invalid canonical requirement' USING ERRCODE='23514'; END IF;
 ELSE
  subject_section:=NEW.section_id;subject_item:=NEW.item_id;
  is_set:=subject_section IS NULL AND subject_item IS NULL;
  prior_revision:=public.rubric_revision(NEW.rubric_id,subject_section,subject_item);
  IF TG_TABLE_NAME='score_rubric_decisions' THEN
   IF is_set THEN
   IF NEW.action NOT IN ('confirm','reopen') THEN RAISE EXCEPTION 'Set review requires bidder' USING ERRCODE='42501'; END IF;
   IF NEW.action='confirm' AND NOT public.rubric_complete(NEW.rubric_id) THEN RAISE EXCEPTION 'Rubric completeness gate failed' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.action='confirm' AND ((subject_section IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE id=subject_section AND citation_valid))
    OR (subject_item IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE id=subject_item AND citation_valid))) THEN
    RAISE EXCEPTION 'Unresolved citation cannot be confirmed' USING ERRCODE='23514'; END IF;
  END IF;
   SELECT action,revision INTO previous_action,previous_revision FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id
    AND section_id IS NOT DISTINCT FROM subject_section AND item_id IS NOT DISTINCT FROM subject_item ORDER BY revision DESC LIMIT 1;
   IF NOT is_set AND previous_revision IS DISTINCT FROM prior_revision THEN previous_action:=NULL; END IF;
   IF (NEW.action='reopen' AND coalesce(previous_action,'reopen')='reopen') OR (NEW.action<>'reopen' AND coalesce(previous_action,'reopen')<>'reopen') THEN
    RAISE EXCEPTION 'Invalid rubric review transition' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 IF NEW.revision<>prior_revision+1 THEN RAISE EXCEPTION 'Subject revision conflict' USING ERRCODE='23514'; END IF;
 IF NOT public.package_imported(NEW.task_id) AND EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND NOT (TG_TABLE_NAME='score_rubric_decisions' AND subject_section IS NULL AND subject_item IS NULL AND to_jsonb(NEW)->>'action'='reopen') THEN
  RAISE EXCEPTION 'Reopen set before changing human review' USING ERRCODE='23514'; END IF;
 IF NOT public.package_imported(NEW.task_id) THEN NEW.decided_at:=clock_timestamp(); END IF; RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.rubric_revision_event_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE new_set public.score_rubric_sets%ROWTYPE; old_set public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NULL,'session');
 SELECT * INTO new_set FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 SELECT * INTO old_set FROM public.score_rubric_sets WHERE id=NEW.prior_rubric_id;
 IF NEW.actor_kind<>'session' OR (NOT public.actor_is_human() AND NOT public.package_imported(NEW.task_id)) OR NEW.prior_rubric_id IS DISTINCT FROM new_set.prior_rubric_id
 OR NEW.snapshot_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.replacement_snapshot::text,'UTF8')),'hex')
 OR NEW.replacement_snapshot->>'expected_input_hash' IS DISTINCT FROM NEW.expected_input_hash
 OR (NEW.replacement_snapshot->>'expected_revision')::integer IS DISTINCT FROM NEW.expected_revision
 OR NEW.version<>new_set.version OR NEW.input_hash IS DISTINCT FROM new_set.input_hash OR NEW.expected_input_hash IS DISTINCT FROM old_set.input_hash
 OR (NEW.expected_revision<>public.rubric_revision(NEW.prior_rubric_id) AND NOT public.package_imported(NEW.task_id)) OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
 RAISE EXCEPTION 'Invalid rubric revision history' USING ERRCODE='23514'; END IF;
 IF NOT public.package_imported(NEW.task_id) THEN NEW.revised_at:=clock_timestamp(); END IF; RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.rubric_set_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE job_row public.jobs%ROWTYPE; prior_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=NEW.extraction_job_id AND task_id=NEW.task_id AND document_id=NEW.document_id AND kind='extract' AND status='succeeded') THEN
  RAISE EXCEPTION 'Rubric requires successful fixed extraction' USING ERRCODE='23514'; END IF;
 IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM public.score_rubric_sets WHERE extraction_job_id=NEW.extraction_job_id) THEN
  RAISE EXCEPTION 'Rubric version conflict' USING ERRCODE='23514'; END IF;
 IF NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
  OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
  RAISE EXCEPTION 'Rubric input binding mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
  -- An imported rubric keeps its generation binding; its attempt finished in the exporting installation.
  IF NEW.actor_kind<>'worker' OR job_row.kind IS DISTINCT FROM 'score_rubric'
   OR (NOT public.package_imported(NEW.task_id) AND (job_row.status IS DISTINCT FROM 'running' OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()))
   OR job_row.run_id IS DISTINCT FROM NEW.run_id
   OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
   OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
   OR (job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input AND NOT public.package_imported(NEW.task_id))
   OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE id=NEW.actor_token_id AND scopes ? 'score:rubric:generate')) THEN
   RAISE EXCEPTION 'Rubric publication requires owning live worker attempt' USING ERRCODE='42501'; END IF;
 ELSE
  PERFORM pg_advisory_xact_lock(hashtextextended('rubric.set:'||NEW.prior_rubric_id::text,0));
  SELECT * INTO prior_row FROM public.score_rubric_sets WHERE id=NEW.prior_rubric_id;
  IF NEW.actor_kind<>'session' OR (NOT public.actor_is_human() AND NOT public.package_imported(NEW.task_id))
   OR NEW.input_hash IS DISTINCT FROM prior_row.input_hash OR NEW.input_manifest IS DISTINCT FROM prior_row.input_manifest
   OR NEW.encrypted_input IS DISTINCT FROM prior_row.encrypted_input OR NEW.extraction_job_id IS DISTINCT FROM prior_row.extraction_job_id
   OR NEW.job_id IS DISTINCT FROM prior_row.job_id OR NEW.run_id IS DISTINCT FROM prior_row.run_id
   OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE prior_rubric_id=NEW.prior_rubric_id) THEN
   RAISE EXCEPTION 'Invalid human rubric revision' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.score_child_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE report_row public.score_reports%ROWTYPE; job_row public.jobs%ROWTYPE;
 item_row public.score_report_items%ROWTYPE; rubric_item public.score_rubric_items%ROWTYPE;
 response_row public.response_items%ROWTYPE; raw_text text; report_xid text;
BEGIN
 SELECT * INTO report_row FROM public.score_reports WHERE id=NEW.report_id;
 SELECT xmin::text INTO report_xid FROM public.score_reports WHERE id=NEW.report_id;
 IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Historical score report is closed' USING ERRCODE='42501'; END IF;
 IF public.citation_publication_is_sealed(NEW.report_id,'score') THEN
  RAISE EXCEPTION 'Completed score publication is closed' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=report_row.job_id FOR UPDATE;
 PERFORM public.response_check_actor(report_row.actor_token_id,'worker');
 IF job_row.run_id IS DISTINCT FROM report_row.run_id
  OR (NOT public.package_imported(report_row.task_id) AND (job_row.status IS DISTINCT FROM 'running'
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp())) THEN
  RAISE EXCEPTION 'Score attempt is not live' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_report_items' THEN
  SELECT * INTO rubric_item FROM public.score_rubric_items WHERE id=NEW.rubric_item_id;
  SELECT * INTO response_row FROM public.response_items WHERE id=NEW.anchor_response_item_id;
  IF NEW.anchor_partition IS DISTINCT FROM (CASE response_row.kind WHEN 'row' THEN 'response' ELSE response_row.kind END)
   OR NEW.score_range IS DISTINCT FROM rubric_item.score_range
   OR (NEW.outcome='assessed' AND rubric_item.assessment_mode<>'model_assessable') THEN
   RAISE EXCEPTION 'Score item fixed rubric or anchor mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO item_row FROM public.score_report_items WHERE id=NEW.score_item_id;
  IF item_row.report_id IS DISTINCT FROM NEW.report_id OR item_row.task_id IS DISTINCT FROM NEW.task_id THEN
   RAISE EXCEPTION 'Score child report mismatch' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='score_report_item_responses' THEN
   SELECT * INTO response_row FROM public.response_items WHERE id=NEW.response_item_id;
   IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row'
    OR response_row.card_revision_id IS DISTINCT FROM NEW.card_revision_id
    OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions WHERE id=NEW.card_revision_id AND state='confirmed' AND confirmed_at IS NOT NULL) THEN
    RAISE EXCEPTION 'Score support requires a fixed confirmed response' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.kind='tender' THEN
    SELECT * INTO rubric_item FROM public.score_rubric_items WHERE id=item_row.rubric_item_id;
    IF NEW.source IS DISTINCT FROM jsonb_set(rubric_item.source,'{quote}',to_jsonb(NEW.quote))
     OR NEW.document_id IS DISTINCT FROM (rubric_item.source->>'document_id')::uuid
     OR NEW.chunk_id IS DISTINCT FROM (rubric_item.source->>'chunk_id')::uuid THEN
     RAISE EXCEPTION 'Score tender citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=rubric_item.source->>'quote';
   ELSE
    SELECT * INTO response_row FROM public.response_items WHERE id=NEW.response_item_id;
    IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row' OR NEW.draft_id IS DISTINCT FROM report_row.draft_id
     OR NEW.card_revision_id IS DISTINCT FROM response_row.card_revision_id THEN
     RAISE EXCEPTION 'Score draft citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=(CASE NEW.field WHEN 'response_text' THEN response_row.response_text WHEN 'deviation_note' THEN response_row.deviation_note END);
   END IF;
   IF raw_text IS NULL OR position(NEW.quote in raw_text)=0
    OR position(NEW.quote in substring(raw_text from position(NEW.quote in raw_text)+1))>0 THEN
    RAISE EXCEPTION 'Score citation must be a unique exact span' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.score_publication_complete()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE job_row public.jobs%ROWTYPE; assessed integer; unassessable integer; section_value jsonb;
BEGIN
 SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id;
 IF job_row.run_id IS DISTINCT FROM NEW.run_id
  OR (NOT public.package_imported(NEW.task_id) AND (job_row.status NOT IN ('running','succeeded')
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()))
  OR (public.score_inputs_current(NEW.draft_id,NEW.rubric_id,NEW.rubric_revision) IS DISTINCT FROM true AND NOT public.package_imported(NEW.task_id)) THEN
  RAISE EXCEPTION 'Score attempt or inputs lost before publication' USING ERRCODE='23514'; END IF;
 SELECT count(*) FILTER(WHERE outcome='assessed'),count(*) FILTER(WHERE outcome='unassessable') INTO assessed,unassessable
  FROM public.score_report_items WHERE report_id=NEW.id;
 IF assessed+unassessable NOT BETWEEN 1 AND 2000 OR assessed+unassessable<>(SELECT count(*) FROM public.score_rubric_items WHERE rubric_id=NEW.rubric_id)
  OR (NEW.summary->>'assessed_items')::integer IS DISTINCT FROM assessed
  OR (NEW.summary->>'unassessable_items')::integer IS DISTINCT FROM unassessable
  OR (unassessable>0 AND NEW.completion<>'partial')
  OR NEW.summary->>'total_status' IS NULL OR NEW.summary->>'total_status' NOT IN ('estimated','range_only','unavailable')
  OR (NEW.completion='complete' AND NEW.summary->>'total_status'<>'estimated')
  OR ((NEW.summary->>'total_status'='estimated') IS DISTINCT FROM (NEW.summary->>'estimated_total' IS NOT NULL))
  OR ((unassessable>0 OR NEW.completion<>'complete') AND NEW.summary->>'estimated_total' IS NOT NULL)
  OR (NEW.summary->>'total_status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE id=NEW.rubric_id AND overall_aggregation IN ('formula','non_additive'))) THEN
  RAISE EXCEPTION 'Score summary must match published coverage' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_report_items item WHERE item.report_id=NEW.id AND item.outcome='assessed'
  AND (NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=item.id AND cite.kind='tender')
   OR NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=item.id AND cite.kind='draft')))
  OR EXISTS(SELECT 1 FROM public.score_report_item_responses support WHERE support.report_id=NEW.id
   AND NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=support.score_item_id AND cite.response_item_id=support.response_item_id AND cite.kind='draft')) THEN
  RAISE EXCEPTION 'Score assessed items require tender and actual supporting draft citations' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.sections)<>(SELECT count(*) FROM public.score_rubric_sections WHERE rubric_id=NEW.rubric_id)
  OR (SELECT count(DISTINCT entry->>'section_key') FROM jsonb_array_elements(NEW.sections) entry)<>jsonb_array_length(NEW.sections) THEN
  RAISE EXCEPTION 'Score report must include each rubric section' USING ERRCODE='23514'; END IF;
 FOR section_value IN SELECT value FROM jsonb_array_elements(NEW.sections) LOOP
  SELECT count(*) FILTER(WHERE item.outcome='assessed'),count(*) FILTER(WHERE item.outcome='unassessable') INTO assessed,unassessable
   FROM public.score_report_items item JOIN public.score_rubric_sections section ON section.id=item.section_id
   WHERE item.report_id=NEW.id AND section.key=section_value->>'section_key';
  IF assessed+unassessable=0 OR (section_value->>'assessed_items')::integer IS DISTINCT FROM assessed
   OR (section_value->>'unassessable_items')::integer IS DISTINCT FROM unassessable
   OR ((section_value->>'status'='estimated') IS DISTINCT FROM (section_value->>'estimated_score' IS NOT NULL))
   OR section_value->>'status' IS NULL OR section_value->>'status' NOT IN ('estimated','range_only','unavailable')
   OR (unassessable>0 AND section_value->>'estimated_score' IS NOT NULL)
   OR (section_value->>'status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE rubric_id=NEW.rubric_id AND key=section_value->>'section_key' AND aggregation IN ('formula','non_additive'))) THEN
   RAISE EXCEPTION 'Score section summary must match published coverage' USING ERRCODE='23514'; END IF;
 END LOOP;
 PERFORM public.citation_seal_publication(NEW.id,'score');
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.score_report_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE job_row public.jobs%ROWTYPE; draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 IF NEW.actor_kind IS DISTINCT FROM 'worker'
  OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE id=NEW.actor_token_id AND scopes ? 'score:run')) THEN
  RAISE EXCEPTION 'Score worker permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR SHARE;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('rubric.set:'||NEW.rubric_id::text,0));
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 SELECT * INTO draft_row FROM public.draft_runs WHERE id=NEW.draft_id;
 IF job_row.kind IS DISTINCT FROM 'score' OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR (NOT public.package_imported(NEW.task_id) AND (job_row.status IS DISTINCT FROM 'running'
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()))
  OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
  OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
  OR (job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input AND NOT public.package_imported(NEW.task_id))

  OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
  OR NEW.input_manifest->>'draft_id' IS DISTINCT FROM NEW.draft_id::text
  OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text
  OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text
  OR NEW.input_manifest->>'draft_input_hash' IS DISTINCT FROM NEW.draft_input_hash
  OR NEW.input_manifest->>'rubric_id' IS DISTINCT FROM NEW.rubric_id::text
  OR (NEW.input_manifest->>'rubric_version')::integer IS DISTINCT FROM NEW.rubric_version
  OR (NEW.input_manifest->>'rubric_revision')::integer IS DISTINCT FROM NEW.rubric_revision
  OR NEW.input_manifest->>'rubric_input_hash' IS DISTINCT FROM NEW.rubric_input_hash
  OR NEW.input_manifest->>'assessment_date' IS DISTINCT FROM to_char(NEW.assessment_date,'YYYY-MM-DD')
  OR NEW.input_manifest->>'prompt_version' IS DISTINCT FROM NEW.prompt_version
  OR NEW.input_manifest->>'schema_version' IS DISTINCT FROM NEW.schema_version
  OR NEW.input_manifest->>'scoring_rule_version' IS DISTINCT FROM NEW.scoring_rule_version
  OR NEW.input_manifest->'model_redaction_enabled' IS DISTINCT FROM 'true'::jsonb
  OR draft_row.input_hash IS DISTINCT FROM NEW.draft_input_hash
  OR rubric_row.input_hash IS DISTINCT FROM NEW.rubric_input_hash OR rubric_row.version IS DISTINCT FROM NEW.rubric_version THEN
  RAISE EXCEPTION 'Invalid score publication attempt or fixed input' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
