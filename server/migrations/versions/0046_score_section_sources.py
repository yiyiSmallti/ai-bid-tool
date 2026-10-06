"""Preserve ordered verified section citations without rewriting old snapshots."""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SOURCES_SQL)
    op.execute(INPUTS_SQL)
    op.execute(COMPLETE_SQL)
    op.execute(PUBLICATION_SQL)


def downgrade():
    raise RuntimeError("Retain verified section source history; repair forward")


SOURCES_SQL = r"""
ALTER TABLE public.score_rubric_sections ADD COLUMN sources jsonb;
ALTER TABLE public.score_rubric_sections ADD CONSTRAINT rubric_section_sources_shape
 CHECK(sources IS NULL OR (jsonb_typeof(sources)='array' AND jsonb_array_length(sources)>0));

CREATE FUNCTION public.rubric_section_sources(section_row public.score_rubric_sections) RETURNS jsonb
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT coalesce((section_row).sources,jsonb_build_array(jsonb_build_object(
  'requirement_id',(section_row).requirement_id,'source',(section_row).source,'quote',(section_row).source->>'quote')));
$$;

CREATE FUNCTION public.rubric_section_source_bindings_valid(p_org uuid,p_rubric uuid,p_sources jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; req public.requirements%ROWTYPE;
 entry jsonb; expected_source jsonb;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF set_row.id IS NULL OR jsonb_typeof(p_sources) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(p_sources)=0 THEN RETURN false; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p_sources) LOOP
  IF jsonb_typeof(entry) IS DISTINCT FROM 'object'
   OR NOT entry ?& ARRAY['requirement_id','source','quote']
   OR entry-ARRAY['requirement_id','source','quote'] <> '{}'::jsonb
   OR jsonb_typeof(entry->'requirement_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(entry->'quote') IS DISTINCT FROM 'string'
   OR entry->>'quote' !~ '[^[:space:]]' THEN RETURN false; END IF;
  SELECT * INTO req FROM public.requirements WHERE org_id=p_org AND id=(entry->>'requirement_id')::uuid;
  IF req.id IS NULL OR req.task_id IS DISTINCT FROM set_row.task_id
   OR req.document_id IS DISTINCT FROM set_row.document_id OR req.job_id IS DISTINCT FROM set_row.extraction_job_id
   OR req.category IS DISTINCT FROM 'scoring'
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(set_row.input_manifest->'requirements') fixed
    WHERE fixed->>'requirement_id'=req.id::text) THEN RETURN false; END IF;
  expected_source:=jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,
   'page',req.page,'location',req.location,'quote',req.quote);
  IF entry->'source' IS DISTINCT FROM expected_source
   OR position(entry->>'quote' in req.quote)=0
   OR position(entry->>'quote' in substring(req.quote from position(entry->>'quote' in req.quote)+1))>0
   THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN invalid_text_representation THEN RETURN false;
END $$;

CREATE FUNCTION public.rubric_section_sources_valid(p_org uuid,p_rubric uuid,p_sources jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF public.rubric_section_source_bindings_valid(p_org,p_rubric,p_sources) IS DISTINCT FROM true THEN RETURN false; END IF;
 -- New citations still require normalized span verification. Publication checks
 -- their immutable bindings, then verifies all pinned Sources in one shared batch.
 IF EXISTS(WITH citations AS MATERIALIZED (
   SELECT DISTINCT req.quote COLLATE "C" AS source_text,(entry->>'quote') COLLATE "C" AS quote
   FROM jsonb_array_elements(p_sources) entry
   JOIN public.requirements req ON req.org_id=p_org AND req.id=(entry->>'requirement_id')::uuid
  ) SELECT 1 FROM citations WHERE public.response_locate_quote(citations.source_text,citations.quote)
   IS DISTINCT FROM citations.quote) THEN RETURN false; END IF;
 RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(p_org,
  ARRAY(SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(p_sources) entry)) verified
  WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

CREATE FUNCTION public.rubric_section_bindings_current(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric)
 AND NOT EXISTS(SELECT 1 FROM public.score_rubric_sections section
  WHERE section.org_id=p_org AND section.rubric_id=p_rubric
   AND (section.requirement_id::text IS DISTINCT FROM public.rubric_section_sources(section)->0->>'requirement_id'
    OR section.source IS DISTINCT FROM public.rubric_section_sources(section)->0->'source'
    OR public.rubric_section_source_bindings_valid(p_org,p_rubric,public.rubric_section_sources(section)) IS DISTINCT FROM true));
$$;

CREATE FUNCTION public.rubric_section_sources_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 -- NULL is retained only for pre-migration rows. All new snapshots are explicit.
 IF NEW.sources IS NULL
  OR NEW.sources->0->>'requirement_id' IS DISTINCT FROM NEW.requirement_id::text
  OR NEW.sources->0->'source' IS DISTINCT FROM NEW.source
  OR public.rubric_section_sources_valid(NEW.org_id,NEW.rubric_id,NEW.sources) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Invalid verified section sources' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER rubric_section_sources_gate BEFORE INSERT ON public.score_rubric_sections
 FOR EACH ROW EXECUTE FUNCTION public.rubric_section_sources_gate();
REVOKE ALL ON FUNCTION public.rubric_section_sources(public.score_rubric_sections),
 public.rubric_section_source_bindings_valid(uuid,uuid,jsonb),
 public.rubric_section_sources_valid(uuid,uuid,jsonb),public.rubric_section_bindings_current(uuid,uuid),
 public.rubric_section_sources_gate() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.rubric_section_sources(public.score_rubric_sections),
 public.rubric_section_source_bindings_valid(uuid,uuid,jsonb),
 public.rubric_section_sources_valid(uuid,uuid,jsonb),public.rubric_section_bindings_current(uuid,uuid) TO bid_app;
"""


INPUTS_SQL = r"""
CREATE OR REPLACE FUNCTION public.rubric_current_inputs(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE required_ids uuid[];
BEGIN
  IF public.rubric_input_shapes_current(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  SELECT ARRAY(SELECT (entry->>'requirement_id')::uuid
    FROM jsonb_array_elements(r.input_manifest->'requirements') entry)
    INTO required_ids FROM public.score_rubric_sets r WHERE r.org_id=p_org AND r.id=p_rubric;
  IF required_ids IS NULL THEN RETURN false; END IF;
  IF public.rubric_section_bindings_current(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(p_org,required_ids) verified
    WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

"""


PUBLICATION_SQL = r"""
CREATE OR REPLACE FUNCTION public.rubric_publication_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE own_domain text; old_section public.score_rubric_sections%ROWTYPE; new_section public.score_rubric_sections%ROWTYPE;
 old_item public.score_rubric_items%ROWTYPE; new_item public.score_rubric_items%ROWTYPE; domain_value text; old_section_key text; new_section_key text;
 prior_set public.score_rubric_sets%ROWTYPE; owning_job public.jobs%ROWTYPE; citation jsonb;
BEGIN
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO owning_job FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
  IF owning_job.status NOT IN ('running','succeeded') OR owning_job.run_id IS DISTINCT FROM NEW.run_id OR owning_job.lease_until IS NULL OR owning_job.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'Rubric owning attempt lost before publication' USING ERRCODE='23514'; END IF;
 END IF;
 IF public.rubric_current_inputs(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric fixed inputs changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_shape_complete(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric lost completeness before publication' USING ERRCODE='23514'; END IF;

 -- current_inputs verified every scoring requirement in the fixed manifest.
 -- Coverage must refer to that exact set and retain its current source binding.
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
  LEFT JOIN public.requirements req ON req.org_id=coverage.org_id AND req.id=coverage.requirement_id
  WHERE coverage.org_id=NEW.org_id AND coverage.rubric_id=NEW.id AND (
   req.id IS NULL OR req.task_id IS DISTINCT FROM NEW.task_id OR req.document_id IS DISTINCT FROM NEW.document_id
   OR req.job_id IS DISTINCT FROM NEW.extraction_job_id OR req.category IS DISTINCT FROM 'scoring'
   OR coverage.source->>'document_id' IS DISTINCT FROM req.document_id::text
   OR coverage.source->>'chunk_id' IS DISTINCT FROM req.chunk_id::text
   OR coverage.source->>'quote' IS DISTINCT FROM req.quote OR coverage.source->>'page' IS DISTINCT FROM req.page::text
   OR coalesce(coverage.source->'location','null'::jsonb) IS DISTINCT FROM coalesce(req.location,'null'::jsonb)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.input_manifest->'requirements') entry
     WHERE (entry->>'requirement_id')::uuid=req.id))) THEN
  RAISE EXCEPTION 'Rubric source changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.requirements req WHERE req.org_id=NEW.org_id AND req.job_id=NEW.extraction_job_id AND req.category='scoring' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage c WHERE c.org_id=NEW.org_id AND c.rubric_id=NEW.id AND c.requirement_id=req.id))
 OR (NEW.prior_rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_revision_events WHERE org_id=NEW.org_id AND rubric_id=NEW.id)) THEN
 RAISE EXCEPTION 'Rubric publication lacks complete coverage or revision history' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NOT NULL THEN
  SELECT (CASE role WHEN 'bidder' THEN 'commercial' WHEN 'technical' THEN 'technical' END) INTO own_domain FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active;
  SELECT * INTO prior_set FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.prior_rubric_id;
  IF own_domain='technical' AND ROW(NEW.overall_aggregation,NEW.overall_rule_text,NEW.overall_score_range,NEW.overall_cap) IS DISTINCT FROM ROW(prior_set.overall_aggregation,prior_set.overall_rule_text,prior_set.overall_score_range,prior_set.overall_cap) THEN
   RAISE EXCEPTION 'Overall rule revision requires bidder' USING ERRCODE='42501'; END IF;
  -- A human may select a prior verified quotation or the pinned full Source,
  -- but cannot turn arbitrary substring input into new citation provenance.
  FOR new_section IN SELECT * FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.id LOOP
   FOR citation IN SELECT value FROM jsonb_array_elements(new_section.sources) LOOP
    IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections previous,
      LATERAL jsonb_array_elements(public.rubric_section_sources(previous)) verified
      WHERE previous.org_id=NEW.org_id AND previous.rubric_id=NEW.prior_rubric_id AND verified.value=citation)
     AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
      WHERE coverage.org_id=NEW.org_id AND coverage.rubric_id=NEW.prior_rubric_id
       AND coverage.requirement_id::text=citation->>'requirement_id' AND coverage.source=citation->'source'
       AND coverage.source->>'quote'=citation->>'quote') THEN
     RAISE EXCEPTION 'Revision cannot invent section citations' USING ERRCODE='23514';
    END IF;
   END LOOP;
  END LOOP;
  FOR old_section IN SELECT * FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id LOOP
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id AND section_id=old_section.id ORDER BY revision DESC LIMIT 1;
   IF domain_value IS DISTINCT FROM own_domain THEN
    SELECT * INTO new_section FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND key=old_section.key;
    IF NOT FOUND OR (to_jsonb(old_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid','sources']) IS DISTINCT FROM (to_jsonb(new_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid','sources'])
     OR public.rubric_section_sources(old_section) IS DISTINCT FROM public.rubric_section_sources(new_section) THEN
     RAISE EXCEPTION 'Cannot revise another review domain section' USING ERRCODE='42501'; END IF;
   END IF;
  END LOOP;
  FOR old_item IN SELECT * FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id LOOP
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id AND item_id=old_item.id ORDER BY revision DESC LIMIT 1;
   IF domain_value IS DISTINCT FROM own_domain THEN
    SELECT * INTO new_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND key=old_item.key;
    IF NOT FOUND OR (to_jsonb(old_item)-ARRAY['id','rubric_id','section_id','created_at','fingerprint','citation_valid']) IS DISTINCT FROM (to_jsonb(new_item)-ARRAY['id','rubric_id','section_id','created_at','fingerprint','citation_valid']) THEN
     RAISE EXCEPTION 'Cannot revise another review domain item' USING ERRCODE='42501'; END IF;
    SELECT key INTO old_section_key FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND id=old_item.section_id;
    SELECT key INTO new_section_key FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND id=new_item.section_id;
    IF old_section_key IS DISTINCT FROM new_section_key THEN RAISE EXCEPTION 'Cannot move another review domain item' USING ERRCODE='42501'; END IF;
   END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
"""


COMPLETE_SQL = r"""
CREATE OR REPLACE FUNCTION public.rubric_shape_complete(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; section_row public.score_rubric_sections%ROWTYPE;
 low_value numeric; high_value numeric; total_low numeric:=0; total_high numeric:=0; weight_total numeric; child_count integer;
 all_bounds boolean:=true; section_bounds boolean;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF NOT FOUND OR NOT public.rubric_range_valid(set_row.overall_score_range) OR set_row.normalization_errors<>'[]'::jsonb THEN RETURN false; END IF;
 IF (set_row.overall_aggregation='capped_sum')<>(set_row.overall_cap IS NOT NULL)
  OR set_row.overall_cap<0 THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_sections s WHERE s.org_id=p_org AND s.rubric_id=p_rubric
  AND NOT public.rubric_subject_values_valid(p_org,p_rubric,s.id,NULL))
 OR EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric
  AND NOT public.rubric_subject_values_valid(p_org,p_rubric,NULL,i.id)) THEN RETURN false; END IF;
 IF public.rubric_input_shapes_current(p_org,p_rubric) IS DISTINCT FROM true
  OR public.rubric_section_bindings_current(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric)
 OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY section_id,"order" HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY "order" HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric
  GROUP BY normalize(regexp_replace(btrim(rule_text),'\s+',' ','g'),NFKC),assessment_mode,
   (score_range->>'minimum')::numeric,(score_range->>'maximum')::numeric,weight,
   normalize(regexp_replace(btrim(ambiguity_reason),'\s+',' ','g'),NFKC),source HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric
  AND (NOT i.citation_valid OR NOT public.rubric_subject_confirmed(p_org,p_rubric,NULL,i.id)
   OR NOT public.rubric_range_valid(i.score_range) OR (i.assessment_mode='model_assessable' AND i.score_range IS NULL)
   OR (i.assessment_mode<>'model_assessable' AND NOT coalesce(i.ambiguity_reason ~ '[^[:space:]]',false)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage c LEFT JOIN LATERAL (
   SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.org_id=p_org AND d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   WHERE c.org_id=p_org AND c.rubric_id=p_rubric AND (latest.id IS NULL OR latest.action='reopen'
    OR (latest.action='mapped' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage_items ci WHERE ci.org_id=p_org AND ci.coverage_decision_id=latest.id))
    OR (latest.action='duplicate' AND NOT public.rubric_canonical_mapped(p_org,p_rubric,latest.canonical_requirement_id)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric AND 1<>(
  SELECT count(*) FROM public.score_rubric_coverage c JOIN LATERAL (SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.org_id=p_org AND d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   JOIN public.score_rubric_coverage_items ci ON ci.org_id=p_org AND ci.coverage_decision_id=latest.id
   WHERE c.org_id=p_org AND c.rubric_id=p_rubric AND latest.action='mapped' AND ci.rubric_item_id=i.id AND c.requirement_id=i.requirement_id)) THEN RETURN false; END IF;
 FOR section_row IN SELECT * FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric LOOP
  IF NOT section_row.citation_valid OR NOT public.rubric_subject_confirmed(p_org,p_rubric,section_row.id,NULL) OR NOT public.rubric_range_valid(section_row.score_range) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric AND section_id=section_row.id)
   OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
  IF section_row.aggregation IN ('formula','non_additive') THEN
   IF NOT coalesce(section_row.aggregation_rule_text ~ '[^[:space:]]',false) OR NOT coalesce(section_row.ambiguity_reason ~ '[^[:space:]]',false) THEN RETURN false; END IF;
   section_bounds:=section_row.score_range IS NOT NULL;
   low_value:=(section_row.score_range->>'minimum')::numeric;
   high_value:=(section_row.score_range->>'maximum')::numeric;
  ELSE
   SELECT count(*),bool_and(score_range IS NOT NULL),sum(weight),
    sum((score_range->>'minimum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END)),
    sum((score_range->>'maximum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END))
    INTO child_count,section_bounds,weight_total,low_value,high_value FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric AND section_id=section_row.id;
   IF child_count=0 OR (section_row.aggregation='weighted_sum' AND (weight_total IS DISTINCT FROM 1 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NULL)))
    OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
   IF section_row.aggregation='capped_sum' THEN low_value:=least(low_value,section_row.cap);high_value:=least(high_value,section_row.cap); END IF;
   IF section_bounds AND section_row.score_range IS NOT NULL AND ((section_row.score_range->>'minimum')::numeric<>round(low_value,8) OR (section_row.score_range->>'maximum')::numeric<>round(high_value,8)) THEN RETURN false; END IF;
  END IF;
  IF NOT section_row.included_in_overall_total AND section_row.weight IS NOT NULL THEN RETURN false; END IF;
  IF section_row.included_in_overall_total THEN
   IF set_row.overall_aggregation='weighted_sum' AND section_row.weight IS NULL OR set_row.overall_aggregation<>'weighted_sum' AND section_row.weight IS NOT NULL THEN RETURN false; END IF;
   all_bounds:=all_bounds AND section_bounds;
   total_low:=total_low+low_value*(CASE WHEN set_row.overall_aggregation='weighted_sum' THEN section_row.weight ELSE 1 END);
   total_high:=total_high+high_value*(CASE WHEN set_row.overall_aggregation='weighted_sum' THEN section_row.weight ELSE 1 END);
  END IF;
 END LOOP;
 IF set_row.overall_aggregation='weighted_sum' AND (SELECT sum(weight) FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric AND included_in_overall_total) IS DISTINCT FROM 1 THEN RETURN false; END IF;
 IF set_row.overall_aggregation IN ('formula','non_additive') THEN RETURN coalesce(set_row.overall_rule_text ~ '[^[:space:]]',false); END IF;
 IF set_row.overall_aggregation='capped_sum' THEN total_low:=least(total_low,set_row.overall_cap);total_high:=least(total_high,set_row.overall_cap); END IF;
 RETURN NOT all_bounds OR set_row.overall_score_range IS NULL OR ((set_row.overall_score_range->>'minimum')::numeric=round(total_low,8) AND (set_row.overall_score_range->>'maximum')::numeric=round(total_high,8));
END $$;

"""
