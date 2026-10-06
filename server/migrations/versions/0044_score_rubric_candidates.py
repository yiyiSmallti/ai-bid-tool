"""Retain numeric normalization failures in immutable rubric candidates.

Storage verifies numeric representation; human subject/set confirmation retains
all semantic invariants independently of the stored normalization error list.
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(CANDIDATE_STORAGE_SQL)
    op.execute(CONFIRMATION_SQL)
    op.execute(COMPLETE_SQL)


CANDIDATE_STORAGE_SQL = r"""
-- 0034 assigned these CHECK names automatically. Identify the affected checks
-- by their numeric columns, leaving every other constraint and trigger intact.
DO $$ DECLARE constraint_row record; BEGIN
 FOR constraint_row IN
  SELECT c.conrelid::regclass AS table_name,c.conname
  FROM pg_constraint c
  WHERE c.contype='c' AND c.conrelid IN (
   'public.score_rubric_sets'::regclass,'public.score_rubric_sections'::regclass,
   'public.score_rubric_items'::regclass)
  AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.conrelid
   AND a.attnum=ANY(c.conkey)
   AND a.attname IN ('overall_cap','cap','weight','overall_score_range','score_range'))
 LOOP
  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',constraint_row.table_name,constraint_row.conname);
 END LOOP;
END $$;

CREATE FUNCTION public.rubric_candidate_range_valid(bounds jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE minimum_value numeric; maximum_value numeric;
BEGIN
 IF bounds IS NULL THEN RETURN true; END IF;
 IF jsonb_typeof(bounds) IS DISTINCT FROM 'object'
  OR (bounds-'minimum'-'maximum')<>'{}'::jsonb
  OR coalesce(jsonb_typeof(bounds->'minimum'),'null') NOT IN ('number','string')
  OR coalesce(jsonb_typeof(bounds->'maximum'),'null') NOT IN ('number','string') THEN RETURN false; END IF;
 BEGIN
  minimum_value:=(bounds->>'minimum')::numeric;
  maximum_value:=(bounds->>'maximum')::numeric;
 EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
 END;
 RETURN minimum_value NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND maximum_value NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND abs(minimum_value)<=9999999999.99999999 AND abs(maximum_value)<=9999999999.99999999
  AND scale(trim_scale(minimum_value))<=8 AND scale(trim_scale(maximum_value))<=8;
END $$;

ALTER TABLE public.score_rubric_sets
 ADD CONSTRAINT rubric_candidate_overall_cap_finite CHECK(overall_cap NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 ADD CONSTRAINT rubric_candidate_overall_range CHECK(public.rubric_candidate_range_valid(overall_score_range));
ALTER TABLE public.score_rubric_sections
 ADD CONSTRAINT rubric_candidate_section_cap_finite CHECK(cap NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 ADD CONSTRAINT rubric_candidate_section_weight_finite CHECK(weight NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 ADD CONSTRAINT rubric_candidate_section_range CHECK(public.rubric_candidate_range_valid(score_range));
ALTER TABLE public.score_rubric_items
 ADD CONSTRAINT rubric_candidate_item_weight_finite CHECK(weight NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 ADD CONSTRAINT rubric_candidate_item_range CHECK(public.rubric_candidate_range_valid(score_range));
REVOKE ALL ON FUNCTION public.rubric_candidate_range_valid(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.rubric_candidate_range_valid(jsonb) TO bid_app;
"""

CONFIRMATION_SQL = r"""
CREATE FUNCTION public.rubric_subject_values_valid(p_org uuid,p_rubric uuid,p_section uuid,p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT coalesce(CASE WHEN p_section IS NOT NULL AND p_item IS NULL THEN (
  SELECT public.rubric_range_valid(s.score_range)
   AND (s.weight IS NULL OR (s.weight>0 AND s.weight<=1))
   AND (s.aggregation='capped_sum')=(s.cap IS NOT NULL) AND (s.cap IS NULL OR s.cap>=0)
   AND (s.aggregation NOT IN ('formula','non_additive') OR (
    coalesce(s.aggregation_rule_text ~ '[^[:space:]]',false)
    AND coalesce(s.ambiguity_reason ~ '[^[:space:]]',false)))
  FROM public.score_rubric_sections s WHERE s.org_id=p_org AND s.rubric_id=p_rubric AND s.id=p_section
 ) WHEN p_item IS NOT NULL AND p_section IS NULL THEN (
  SELECT public.rubric_range_valid(i.score_range)
   AND (i.weight IS NULL OR (i.weight>0 AND i.weight<=1))
   AND (i.assessment_mode<>'model_assessable' OR i.score_range IS NOT NULL)
   AND (i.assessment_mode='model_assessable' OR coalesce(i.ambiguity_reason ~ '[^[:space:]]',false))
  FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric AND i.id=p_item
 ) ELSE false END,false)
$$;

CREATE FUNCTION public.rubric_subject_values_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.action='confirm' AND (NEW.section_id IS NOT NULL OR NEW.item_id IS NOT NULL)
  AND NOT public.rubric_subject_values_valid(NEW.org_id,NEW.rubric_id,NEW.section_id,NEW.item_id) THEN
  RAISE EXCEPTION 'Invalid rubric subject cannot be confirmed' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
-- The existing rubric_review_gate runs first and retains actor, source, domain,
-- revision and transition checks. Numeric checks never replace those guards.
CREATE TRIGGER rubric_subject_values_gate BEFORE INSERT ON public.score_rubric_decisions
 FOR EACH ROW EXECUTE FUNCTION public.rubric_subject_values_gate();
REVOKE ALL ON FUNCTION public.rubric_subject_values_valid(uuid,uuid,uuid,uuid),
 public.rubric_subject_values_gate() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.rubric_subject_values_valid(uuid,uuid,uuid,uuid) TO bid_app;
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
 IF public.rubric_input_shapes_current(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
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


def downgrade():
    raise RuntimeError(
        "Retain invalid candidate history; correct through human revisions and repair forward"
    )
