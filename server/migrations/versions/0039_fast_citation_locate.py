"""Speed up exact citation location and deduplicate report publication verification."""

import unicodedata

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    # Match 0019's frozen Python combining-character set for the fallback and
    # the fast path's normalization-unit boundary checks.
    combining = "".join(chr(code) for code in range(0x110000) if unicodedata.combining(chr(code)))
    # A few CCC=0 characters normalize to leading combining marks. Whole-source
    # NFKC can then cross a boundary retained by normalized_spans (e.g. Tibetan
    # vowel signs followed by accents). Keep all marked sources on the oracle path.
    leading_marks = "".join(
        chr(code)
        for code in range(0x110000)
        if unicodedata.combining(unicodedata.normalize("NFKC", chr(code))[0])
    )
    op.execute(
        LOCATE_SQL.replace("__COMBINING__", combining.replace("'", "''")).replace(
            "__UNSAFE__", "".join(sorted(set(combining + leading_marks))).replace("'", "''")
        )
    )
    op.execute(HELPER_SQL)
    op.execute(RUBRIC_GATE_SQL)
    op.execute(CHECK_GATE_SQL)
    op.execute(SCORE_GATE_SQL)


def downgrade():
    raise RuntimeError("Retain citation verification semantics; rollback is application-only")


LOCATE_SQL = r"""
CREATE OR REPLACE FUNCTION response_locate_quote(source_text text, quote text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
DECLARE
  combining_chars constant text := '__COMBINING__';
  separators constant text := U&'\FF1B;\FF0C,\3002\FF1A:\3001\FF01!\FF1F?\FF08\FF09()\3010\3011[]\300A\300B\201C\201D\2018\2019\0022\0027\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000';
  needle text := public.response_normalize_quote(quote);
  haystack text := '';
  starts integer[] := '{}'; ends integer[] := '{}';
  source_length integer := length(source_text);
  unit_start integer := 1; idx integer; part_index integer;
  previous text; character text; part text;
  search_from integer := 1; relative_match integer; match_start integer;
  span_start integer; span_end integer; candidate text;
  matches integer := 0; bounded_matches integer := 0;
  chosen text; bounded_chosen text;
  exact_start integer; exact_end integer; prefix text; next_character text;
BEGIN
  IF length(needle)=0 OR source_length=0 THEN RETURN NULL; END IF;
  -- Without combining input or leading normalized marks, unit boundaries
  -- compose exactly as whole-source NFKC. Marked input retains the old algorithm.
  IF source_text !~ '[__UNSAFE__]' THEN
    -- A unique normalized occurrence with an exact original spelling needs no
    -- offset arrays. Check both normalization-unit edges, not segment boundaries:
    -- a sole occurrence is valid even inside a word, but never inside a combining
    -- or Hangul unit. Nonempty edge characters exclude discarded whitespace units.
    haystack := public.response_normalize_quote(source_text);
    match_start := position(needle in haystack);
    IF match_start=0 THEN RETURN NULL; END IF;
    IF position(needle in substring(haystack from match_start+1))=0 THEN
      exact_start := position(quote in source_text);
      IF exact_start>0
        AND public.response_normalize_quote(left(quote,1))<>''
        AND public.response_normalize_quote(right(quote,1))<>''
        AND position(left(quote,1) in combining_chars)=0 THEN
        exact_end := exact_start+length(quote)-1;
        prefix := left(source_text,exact_start-1);
        next_character := substring(source_text from exact_end+1 for 1);
        IF (exact_start=1 OR
            normalize(prefix || left(quote,1),NFKC)=
              normalize(prefix,NFKC) || normalize(left(quote,1),NFKC))
          AND (exact_end=source_length OR
            (position(next_character in combining_chars)=0 AND
             normalize(prefix || quote || next_character,NFKC)=
               normalize(prefix || quote,NFKC) || normalize(next_character,NFKC))) THEN
          RETURN quote;
        END IF;
      END IF;
    END IF;
  END IF;
  haystack := '';
  -- Mirror normalized_spans: retain whole combining/composition units,
  -- including Hangul and compatibility expansions, with original offsets.
  FOR idx IN 2..source_length+1 LOOP
    previous := substring(source_text from unit_start for idx-unit_start);
    character := substring(source_text from idx for 1);
    IF idx<=source_length AND
      (position(character in combining_chars)>0 OR
       normalize(previous || character,NFKC) <>
         normalize(previous,NFKC) || normalize(character,NFKC)) THEN
      CONTINUE;
    END IF;
    part := public.response_normalize_quote(previous);
    haystack := haystack || part;
    FOR part_index IN 1..length(part) LOOP
      starts := array_append(starts,unit_start);
      ends := array_append(ends,idx-1);
    END LOOP;
    unit_start := idx;
  END LOOP;
  LOOP
    relative_match := position(needle in substring(haystack from search_from));
    EXIT WHEN relative_match=0;
    match_start := search_from+relative_match-1;
    span_start := starts[match_start];
    span_end := ends[match_start+length(needle)-1];
    candidate := substring(source_text from span_start for span_end-span_start+1);
    -- An inner match in a compatibility expansion is not an original span.
    IF public.response_normalize_quote(candidate)=needle THEN
      matches := matches+1;
      chosen := candidate;
      IF (span_start=1 OR
            position(substring(source_text from span_start-1 for 1) in separators)>0)
        AND (span_end=source_length OR
            position(substring(source_text from span_end+1 for 1) in separators)>0) THEN
        bounded_matches := bounded_matches+1;
        bounded_chosen := candidate;
      END IF;
    END IF;
    search_from := match_start+1;
  END LOOP;
  IF matches=1 THEN RETURN chosen; END IF;
  IF matches>1 AND bounded_matches=1 THEN RETURN bounded_chosen; END IF;
  RETURN NULL;
END $$;
"""


HELPER_SQL = r"""
CREATE FUNCTION public.response_requirement_citations_valid(p_org uuid,p_requirements uuid[])
RETURNS TABLE(requirement_id uuid,is_valid boolean)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
  WITH requested AS MATERIALIZED (
    SELECT DISTINCT unnest(coalesce(p_requirements,ARRAY[]::uuid[])) AS requirement_id
  ), resolved AS MATERIALIZED (
    SELECT requested.requirement_id,r.quote COLLATE "C" AS quote,
      coalesce(r.id IS NOT NULL AND c.id IS NOT NULL
        AND c.task_id=r.task_id AND c.document_id=r.document_id
        AND c.citation_verified,false) AS metadata_valid,
      CASE WHEN r.page IS NOT NULL AND r.page=c.page AND r.location IS NULL
          AND c.blocks IS NULL THEN c.text
        WHEN r.page IS NULL AND c.page IS NULL AND r.location IS NOT NULL THEN
          (SELECT b->>'text' FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location)
      END COLLATE "C" AS source_text
    FROM requested
    LEFT JOIN public.requirements r ON r.org_id=p_org AND r.id=requested.requirement_id
    LEFT JOIN public.chunks c ON c.org_id=r.org_id AND c.id=r.chunk_id
  ), sources AS MATERIALIZED (
    SELECT DISTINCT source_text,quote FROM resolved
    WHERE metadata_valid AND source_text IS NOT NULL AND quote IS NOT NULL
  ), verified AS MATERIALIZED (
    SELECT source_text,quote,
      coalesce(length(quote)>0 AND position(quote in source_text)>0
        AND public.response_locate_quote(source_text,quote) IS NOT NULL,false) AS is_valid
    FROM sources
  )
  SELECT resolved.requirement_id,
    coalesce(resolved.metadata_valid AND verified.is_valid,false)
  FROM resolved LEFT JOIN verified
    ON verified.source_text=resolved.source_text AND verified.quote=resolved.quote
$$;

CREATE FUNCTION public.citation_publication_lock_key(p_org uuid,p_report uuid,p_kind text)
RETURNS bigint
LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT hashtextextended('bid.citation_publication:'||p_kind||':'||p_org::text||':'||p_report::text,0)
$$;

CREATE FUNCTION public.citation_publication_is_sealed(p_org uuid,p_report uuid,p_kind text)
RETURNS boolean
LANGUAGE sql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT EXISTS(
    SELECT 1 FROM pg_locks
    WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted AND objsubid=1
      AND classid=((public.citation_publication_lock_key(p_org,p_report,p_kind)>>32)&4294967295)::oid
      AND objid=(public.citation_publication_lock_key(p_org,p_report,p_kind)&4294967295)::oid
  )
$$;

CREATE FUNCTION public.citation_seal_publication(p_org uuid,p_report uuid,p_kind text)
RETURNS void
LANGUAGE sql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  -- A seal only rejects further child rows. Acquiring it directly cannot claim
  -- that verification succeeded, and transaction locks cannot be unlocked.
  SELECT pg_advisory_xact_lock(public.citation_publication_lock_key(p_org,p_report,p_kind))
$$;
"""


RUBRIC_GATE_SQL = r"""
-- Shape helpers do not attest citations. Public validators and publication
-- gates combine these checks with independently resolved citation results.
CREATE OR REPLACE FUNCTION public.rubric_input_shapes_current(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE fixed jsonb; set_row public.score_rubric_sets%ROWTYPE; req_row public.requirements%ROWTYPE; chunk_row public.chunks%ROWTYPE; source_value jsonb;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF NOT FOUND OR jsonb_typeof(set_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(set_row.input_manifest->'requirements') NOT BETWEEN 1 AND 2000
  OR (SELECT count(DISTINCT entry->>'requirement_id') FROM jsonb_array_elements(set_row.input_manifest->'requirements') entry) IS DISTINCT FROM jsonb_array_length(set_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=p_org AND id=set_row.extraction_job_id AND task_id=set_row.task_id AND document_id=set_row.document_id AND kind='extract' AND status='succeeded')
  OR set_row.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE org_id=p_org AND id=set_row.document_id AND task_id=set_row.task_id)
  OR jsonb_array_length(set_row.input_manifest->'requirements') IS DISTINCT FROM (SELECT count(*) FROM public.requirements WHERE org_id=p_org AND task_id=set_row.task_id AND job_id=set_row.extraction_job_id AND category='scoring') THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(set_row.input_manifest->'requirements') LOOP
  SELECT * INTO req_row FROM public.requirements WHERE org_id=p_org AND id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO chunk_row FROM public.chunks WHERE org_id=p_org AND id=(fixed->>'chunk_id')::uuid;
  source_value:=jsonb_build_object('document_id',req_row.document_id,'chunk_id',req_row.chunk_id,'page',req_row.page,'location',req_row.location,'quote',req_row.quote);
  IF req_row.id IS NULL OR chunk_row.id IS NULL OR req_row.task_id IS DISTINCT FROM set_row.task_id OR req_row.job_id IS DISTINCT FROM set_row.extraction_job_id
   OR req_row.category IS DISTINCT FROM 'scoring' OR req_row.document_id IS DISTINCT FROM set_row.document_id OR req_row.chunk_id IS DISTINCT FROM chunk_row.id
   OR chunk_row.task_id IS DISTINCT FROM set_row.task_id OR chunk_row.document_id IS DISTINCT FROM set_row.document_id
   OR fixed->>'source_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(source_value),'UTF8')),'hex')
   OR fixed->>'requirement_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',req_row.text,'category',req_row.category,'starred',req_row.starred)),'UTF8')),'hex')
   OR fixed->>'chunk_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',chunk_row.text,'blocks',chunk_row.blocks,'page',chunk_row.page,'citation_verified',chunk_row.citation_verified)),'UTF8')),'hex') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.rubric_shape_complete(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; section_row public.score_rubric_sections%ROWTYPE;
 low_value numeric; high_value numeric; total_low numeric:=0; total_high numeric:=0; weight_total numeric; child_count integer;
 all_bounds boolean:=true; section_bounds boolean;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF NOT FOUND OR NOT public.rubric_range_valid(set_row.overall_score_range) OR set_row.normalization_errors<>'[]'::jsonb THEN RETURN false; END IF;
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

CREATE OR REPLACE FUNCTION public.rubric_current_inputs(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE required_ids uuid[];
BEGIN
  IF public.rubric_input_shapes_current(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  SELECT ARRAY(SELECT (entry->>'requirement_id')::uuid
    FROM jsonb_array_elements(r.input_manifest->'requirements') entry)
    INTO required_ids FROM public.score_rubric_sets r WHERE r.org_id=p_org AND r.id=p_rubric;
  IF required_ids IS NULL THEN RETURN false; END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(p_org,required_ids) verified
    WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

CREATE OR REPLACE FUNCTION public.rubric_complete(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE required_ids uuid[];
BEGIN
  IF public.rubric_shape_complete(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  SELECT ARRAY(SELECT (entry->>'requirement_id')::uuid
    FROM jsonb_array_elements(r.input_manifest->'requirements') entry)
    INTO required_ids FROM public.score_rubric_sets r WHERE r.org_id=p_org AND r.id=p_rubric;
  IF required_ids IS NULL THEN RETURN false; END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(p_org,required_ids) verified
    WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

CREATE OR REPLACE FUNCTION public.rubric_publication_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE own_domain text; old_section public.score_rubric_sections%ROWTYPE; new_section public.score_rubric_sections%ROWTYPE;
 old_item public.score_rubric_items%ROWTYPE; new_item public.score_rubric_items%ROWTYPE; domain_value text; old_section_key text; new_section_key text;
 prior_set public.score_rubric_sets%ROWTYPE; owning_job public.jobs%ROWTYPE;
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
  FOR old_section IN SELECT * FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id LOOP
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id AND section_id=old_section.id ORDER BY revision DESC LIMIT 1;
   IF domain_value IS DISTINCT FROM own_domain THEN
    SELECT * INTO new_section FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND key=old_section.key;
    IF NOT FOUND OR (to_jsonb(old_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid']) IS DISTINCT FROM (to_jsonb(new_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid']) THEN
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


CHECK_GATE_SQL = r"""
CREATE OR REPLACE FUNCTION public.check_current_inputs(p_org uuid,p_report uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
-- Subquery aliases deliberately reuse the record names; they always mean the table.
#variable_conflict use_column
DECLARE r public.check_runs%ROWTYPE; t public.tasks%ROWTYPE; i public.check_items%ROWTYPE;
  s public.response_items%ROWTYPE; q public.requirements%ROWTYPE; c public.response_cards%ROWTYPE;
  v public.response_card_revisions%ROWTYPE; e public.evidence%ROWTYPE;
  cf public.confidential_fields%ROWTYPE; fixed jsonb; dep jsonb; cert jsonb; field jsonb;
  value_id uuid; expected_count integer; actual_count integer; citation_results jsonb;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE org_id=p_org AND id=p_report;
  IF r.id IS NULL THEN RETURN false; END IF;
  SELECT * INTO t FROM public.tasks WHERE org_id=p_org AND id=r.task_id;
  IF (r.input_manifest->>'model_redaction_enabled')::boolean IS DISTINCT FROM t.model_redaction_enabled
    OR (r.input_manifest->>'model_redaction_revision')::integer IS DISTINCT FROM t.model_redaction_revision
    OR r.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE org_id=p_org AND id=r.document_id AND task_id=r.task_id)
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=p_org AND id=r.extraction_job_id
      AND task_id=r.task_id AND document_id=r.document_id AND kind='extract' AND status='succeeded') THEN RETURN false; END IF;
  IF jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.check_items WHERE org_id=p_org AND report_id=p_report)
    OR jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.requirements WHERE org_id=p_org AND task_id=r.task_id AND job_id=r.extraction_job_id) THEN RETURN false; END IF;
  IF (SELECT count(DISTINCT x->>'requirement_id') FROM jsonb_array_elements(r.input_manifest->'items') x)
    <>jsonb_array_length(r.input_manifest->'items') THEN RETURN false; END IF;
  SELECT jsonb_object_agg(verified.requirement_id::text,verified.is_valid) INTO citation_results
    FROM public.response_requirement_citations_valid(p_org,
      ARRAY(SELECT requirement_id FROM public.check_items WHERE org_id=p_org AND report_id=p_report)) verified;
  FOR fixed IN SELECT value FROM jsonb_array_elements(r.input_manifest->'items') LOOP
    SELECT * INTO i FROM public.check_items WHERE org_id=p_org AND report_id=p_report AND requirement_id=(fixed->>'requirement_id')::uuid;
    SELECT * INTO s FROM public.response_items WHERE org_id=p_org AND id=i.response_item_id;
    SELECT * INTO q FROM public.requirements WHERE org_id=p_org AND id=i.requirement_id;
    SELECT * INTO c FROM public.response_cards WHERE org_id=p_org AND id=s.card_id;
    SELECT * INTO v FROM public.response_card_revisions WHERE org_id=p_org AND id=s.card_revision_id;
    IF i.id IS NULL OR s.id IS NULL OR q.id IS NULL
      OR s.draft_id IS DISTINCT FROM r.draft_id OR q.job_id IS DISTINCT FROM r.extraction_job_id
      OR q.task_id IS DISTINCT FROM r.task_id OR q.document_id IS DISTINCT FROM r.document_id
      OR (fixed->>'response_item_id')::uuid IS DISTINCT FROM s.id
      OR (fixed->>'card_id')::uuid IS DISTINCT FROM s.card_id
      OR (fixed->>'card_revision_id')::uuid IS DISTINCT FROM s.card_revision_id
      OR (fixed->>'document_id')::uuid IS DISTINCT FROM q.document_id
      OR (fixed->>'chunk_id')::uuid IS DISTINCT FROM q.chunk_id
      OR fixed->>'partition' IS DISTINCT FROM i.partition
      OR fixed->>'review_domain' IS DISTINCT FROM v.review_domain
      OR fixed->'gap_reasons' IS DISTINCT FROM s.gap_reasons
      OR s.category IS DISTINCT FROM q.category OR s.starred IS DISTINCT FROM q.starred
      OR i.source IS DISTINCT FROM s.source
      OR i.source IS DISTINCT FROM jsonb_build_object('document_id',q.document_id,'chunk_id',q.chunk_id,'page',q.page,'location',q.location,'quote',q.quote)
      OR coalesce((citation_results->>q.id::text)::boolean,false) IS DISTINCT FROM true
      OR (s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE org_id=p_org AND requirement_id=q.id))
      OR (i.partition IN ('response','comply_only') AND public.response_quote_current(p_org,s.card_revision_id) IS DISTINCT FROM true)
      OR (i.partition='response' AND (v.state IS DISTINCT FROM 'confirmed' OR v.confirmed_by IS NULL))
      OR (i.partition='comply_only' AND v.disposition IS DISTINCT FROM 'comply_only') THEN RETURN false; END IF;
    IF jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=s.card_revision_id)
      OR (SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(fixed->'evidence') x)<>jsonb_array_length(fixed->'evidence') THEN RETURN false; END IF;
    FOR dep IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
      SELECT * INTO e FROM public.evidence WHERE org_id=p_org AND id=(dep->>'id')::uuid;
      IF e.id IS NULL OR e.task_id IS DISTINCT FROM r.task_id OR e.card_id IS DISTINCT FROM s.card_id
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=s.card_revision_id AND evidence_id=e.id)
        OR (dep->>'confirmed_by')::uuid IS DISTINCT FROM e.confirmed_by
        OR (dep->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(p_org,e.id)
        OR (i.partition='response' AND (e.confirmed_by IS NULL OR public.response_evidence_active(p_org,e.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
    END LOOP;
    IF i.partition<>'comply_only' AND v.model_job_id IS NOT NULL AND public.response_generation_materials_active(p_org,v.model_job_id) IS DISTINCT FROM true
      AND NOT (i.partition='gap' AND fixed->'gap_reasons' ? 'stale_material') THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'certificates');
  IF expected_count<>(SELECT count(*) FROM public.task_certificates WHERE org_id=p_org AND task_id=r.task_id AND active)
    OR expected_count<>(SELECT count(*) FROM public.check_certificates WHERE org_id=p_org AND report_id=p_report)
    OR expected_count<>(SELECT count(DISTINCT x->>'task_certificate_id') FROM jsonb_array_elements(r.input_manifest->'certificates') x) THEN RETURN false; END IF;
  FOR cert IN SELECT value FROM jsonb_array_elements(r.input_manifest->'certificates') LOOP
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.task_certificates b ON b.org_id=a.org_id AND b.id=a.task_certificate_id
      JOIN public.certificate_revisions v ON v.org_id=b.org_id AND v.id=b.certificate_revision_id
      WHERE a.org_id=p_org AND a.report_id=p_report AND a.task_certificate_id=(cert->>'task_certificate_id')::uuid
        AND a.certificate_revision_id=(cert->>'certificate_revision_id')::uuid AND b.task_id=r.task_id AND b.active
        AND a.date_status=cert->>'date_status' AND a.assessment_date=r.assessment_date
        AND v.data->>'valid_from' IS NOT DISTINCT FROM cert->>'valid_from'
        AND v.data->>'valid_until' IS NOT DISTINCT FROM cert->>'valid_until') THEN RETURN false; END IF;
    SELECT count(*) INTO actual_count FROM public.check_certificate_items a JOIN public.check_certificates b
      ON b.org_id=a.org_id AND b.id=a.certificate_id WHERE a.org_id=p_org AND a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid;
    IF jsonb_typeof(cert->'requirement_ids') IS DISTINCT FROM 'array'
      OR actual_count<>(SELECT count(DISTINCT i.id) FROM public.check_items i
        JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
        JOIN public.card_evidence_links l ON l.org_id=s.org_id AND l.revision_id=s.card_revision_id
        JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE i.org_id=p_org AND i.report_id=p_report AND s.kind='row' AND e.confirmed_by IS NOT NULL
          AND e.task_certificate_id=(cert->>'task_certificate_id')::uuid
          AND e.certificate_revision_id=(cert->>'certificate_revision_id')::uuid)
      OR actual_count<>jsonb_array_length(cert->'requirement_ids')
      OR actual_count<>(SELECT count(DISTINCT x) FROM jsonb_array_elements_text(cert->'requirement_ids') x)
      OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(cert->'requirement_ids') req WHERE NOT EXISTS(
        SELECT 1 FROM public.check_certificate_items a JOIN public.check_certificates b ON b.org_id=a.org_id AND b.id=a.certificate_id
        JOIN public.check_items i ON i.org_id=a.org_id AND i.id=a.check_item_id
        WHERE a.org_id=p_org AND a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid AND i.requirement_id=req::uuid)) THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'confidential');
  IF expected_count<>(SELECT count(*) FROM public.confidential_fields WHERE org_id=p_org AND NOT archived)
    OR expected_count<>(SELECT count(DISTINCT x->>'field_id') FROM jsonb_array_elements(r.input_manifest->'confidential') x) THEN RETURN false; END IF;
  FOR field IN SELECT value FROM jsonb_array_elements(r.input_manifest->'confidential') LOOP
    SELECT * INTO cf FROM public.confidential_fields WHERE org_id=p_org AND id=(field->>'field_id')::uuid;
    SELECT id INTO value_id FROM public.confidential_values WHERE org_id=p_org AND field_id=cf.id
      AND task_id IS NOT DISTINCT FROM CASE cf.scope WHEN 'task' THEN r.task_id ELSE NULL END ORDER BY version DESC LIMIT 1;
    IF cf.id IS NULL OR cf.archived OR cf.revision IS DISTINCT FROM (field->>'field_revision')::integer
      OR value_id IS DISTINCT FROM (field->>'value_id')::uuid THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.check_child_gate() RETURNS trigger
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
  IF public.citation_publication_is_sealed(NEW.org_id,NEW.report_id,'check') THEN
    RAISE EXCEPTION 'Completed check publication is closed' USING ERRCODE='42501';
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
      OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,'page',req.page,'location',req.location,'quote',req.quote) THEN
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

CREATE OR REPLACE FUNCTION public.check_run_complete() RETURNS trigger
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
  PERFORM public.citation_seal_publication(NEW.org_id,NEW.id,'check');
  RETURN NULL;
END $$;
"""


SCORE_GATE_SQL = r"""
CREATE OR REPLACE FUNCTION public.score_inputs_current(p_org uuid,p_draft uuid,p_rubric uuid,p_revision integer) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
 response_row public.response_items%ROWTYPE; requirement_row public.requirements%ROWTYPE;
 card_row record; revision_row record;
 evidence_row public.evidence%ROWTYPE; fixed jsonb; dependency jsonb; source_value jsonb;
 citation_results jsonb; required_ids uuid[];
BEGIN
 SELECT * INTO draft_row FROM public.draft_runs WHERE org_id=p_org AND id=p_draft;
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF draft_row.id IS NULL OR rubric_row.id IS NULL
  OR draft_row.task_id IS DISTINCT FROM rubric_row.task_id
  OR draft_row.extraction_job_id IS DISTINCT FROM rubric_row.extraction_job_id
  OR p_revision IS DISTINCT FROM public.rubric_revision(p_org,p_rubric)
  OR public.rubric_shape_complete(p_org,p_rubric) IS DISTINCT FROM true
  OR (SELECT action FROM public.score_rubric_decisions WHERE org_id=p_org AND rubric_id=p_rubric
      AND section_id IS NULL AND item_id IS NULL ORDER BY revision DESC LIMIT 1) IS DISTINCT FROM 'confirm'
  OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=p_org AND prior_rubric_id=p_rubric)
  OR jsonb_typeof(draft_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF (SELECT count(*) FROM public.response_items WHERE org_id=p_org AND draft_id=p_draft)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint
  OR (SELECT count(*) FROM public.requirements WHERE org_id=p_org AND job_id=draft_row.extraction_job_id)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 SELECT ARRAY(
   SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(draft_row.input_manifest->'requirements') entry
   UNION
   SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(rubric_row.input_manifest->'requirements') entry)
   INTO required_ids;
 SELECT jsonb_object_agg(verified.requirement_id::text,verified.is_valid) INTO citation_results
   FROM public.response_requirement_citations_valid(p_org,required_ids) verified;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(rubric_row.input_manifest->'requirements') entry
   WHERE coalesce((citation_results->>(entry->>'requirement_id'))::boolean,false) IS DISTINCT FROM true) THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(draft_row.input_manifest->'requirements') LOOP
  SELECT * INTO response_row FROM public.response_items WHERE org_id=p_org AND draft_id=p_draft AND requirement_id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO requirement_row FROM public.requirements WHERE org_id=p_org AND id=response_row.requirement_id;
  SELECT id,revision INTO card_row FROM public.response_cards WHERE org_id=p_org AND id=response_row.card_id;
  SELECT id,revision,state,confirmed_by,disposition,model_job_id INTO revision_row FROM public.response_card_revisions WHERE org_id=p_org AND id=response_row.card_revision_id;
  source_value:=jsonb_build_object('document_id',requirement_row.document_id,'chunk_id',requirement_row.chunk_id,
    'page',requirement_row.page,'location',requirement_row.location,'quote',requirement_row.quote);
  IF response_row.id IS NULL OR requirement_row.id IS NULL
   OR requirement_row.task_id IS DISTINCT FROM draft_row.task_id OR requirement_row.document_id IS DISTINCT FROM rubric_row.document_id
   OR requirement_row.job_id IS DISTINCT FROM draft_row.extraction_job_id
   OR response_row.source IS DISTINCT FROM source_value
   OR fixed->>'source_hash' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(source_value),'UTF8')),'hex')
   OR fixed->>'category' IS DISTINCT FROM requirement_row.category OR (fixed->>'starred')::boolean IS DISTINCT FROM requirement_row.starred
   OR fixed->>'kind' IS DISTINCT FROM response_row.kind
   OR (fixed->>'card_revision_id')::uuid IS DISTINCT FROM response_row.card_revision_id
   OR (response_row.card_id IS NOT NULL AND card_row.revision IS DISTINCT FROM revision_row.revision)
   OR (response_row.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE org_id=p_org AND requirement_id=requirement_row.id))
   OR (coalesce((citation_results->>requirement_row.id::text)::boolean,false) IS DISTINCT FROM true AND NOT (response_row.kind='gap' AND response_row.gap_reasons ? 'invalid_citation'))
   OR (response_row.kind IN ('row','comply_only') AND public.response_quote_current(p_org,revision_row.id) IS DISTINCT FROM true)
   OR (response_row.kind='row' AND (revision_row.state IS DISTINCT FROM 'confirmed' OR revision_row.confirmed_by IS NULL))
   OR (response_row.kind='comply_only' AND revision_row.disposition IS DISTINCT FROM 'comply_only')
   OR jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=revision_row.id) THEN RETURN false; END IF;
  FOR dependency IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
   SELECT * INTO evidence_row FROM public.evidence WHERE org_id=p_org AND id=(dependency->>'id')::uuid;
   IF evidence_row.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=revision_row.id AND evidence_id=evidence_row.id)
    OR (dependency->>'confirmed_by')::uuid IS DISTINCT FROM evidence_row.confirmed_by
    OR (dependency->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(p_org,evidence_row.id)
    OR (response_row.kind='row' AND (evidence_row.confirmed_by IS NULL OR public.response_evidence_active(p_org,evidence_row.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
  END LOOP;
  IF revision_row.model_job_id IS NOT NULL AND response_row.kind<>'comply_only'
   AND public.response_generation_materials_active(p_org,revision_row.model_job_id) IS DISTINCT FROM true
   AND NOT(response_row.kind='gap' AND response_row.gap_reasons ? 'stale_material') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.score_report_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
 IF NEW.actor_kind IS DISTINCT FROM 'worker' OR NOT EXISTS(SELECT 1 FROM public.memberships
  WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active AND role IN ('admin','bidder','technical'))
  OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'score:run')) THEN
  RAISE EXCEPTION 'Score worker permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR SHARE;
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id FOR SHARE;
 SELECT * INTO draft_row FROM public.draft_runs WHERE org_id=NEW.org_id AND id=NEW.draft_id;
 IF job_row.kind IS DISTINCT FROM 'score' OR job_row.status IS DISTINCT FROM 'running'
  OR job_row.run_id IS DISTINCT FROM NEW.run_id OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
  OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
  OR job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
  OR NEW.input_manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text
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
END $$;

CREATE OR REPLACE FUNCTION public.score_child_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE report_row public.score_reports%ROWTYPE; job_row public.jobs%ROWTYPE;
 item_row public.score_report_items%ROWTYPE; rubric_item public.score_rubric_items%ROWTYPE;
 response_row public.response_items%ROWTYPE; raw_text text; report_xid text;
BEGIN
 SELECT * INTO report_row FROM public.score_reports WHERE org_id=NEW.org_id AND id=NEW.report_id;
 SELECT xmin::text INTO report_xid FROM public.score_reports WHERE org_id=NEW.org_id AND id=NEW.report_id;
 IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Historical score report is closed' USING ERRCODE='42501'; END IF;
 IF public.citation_publication_is_sealed(NEW.org_id,NEW.report_id,'score') THEN
  RAISE EXCEPTION 'Completed score publication is closed' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=report_row.job_id FOR UPDATE;
 PERFORM public.response_check_actor(NEW.org_id,report_row.actor_user_id,report_row.actor_token_id,'worker');
 IF job_row.status IS DISTINCT FROM 'running' OR job_row.run_id IS DISTINCT FROM report_row.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'Score attempt is not live' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_report_items' THEN
  SELECT * INTO rubric_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=NEW.rubric_item_id;
  SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.anchor_response_item_id;
  IF NEW.anchor_partition IS DISTINCT FROM (CASE response_row.kind WHEN 'row' THEN 'response' ELSE response_row.kind END)
   OR NEW.score_range IS DISTINCT FROM rubric_item.score_range
   OR (NEW.outcome='assessed' AND rubric_item.assessment_mode<>'model_assessable') THEN
   RAISE EXCEPTION 'Score item fixed rubric or anchor mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO item_row FROM public.score_report_items WHERE org_id=NEW.org_id AND id=NEW.score_item_id;
  IF item_row.report_id IS DISTINCT FROM NEW.report_id OR item_row.task_id IS DISTINCT FROM NEW.task_id THEN
   RAISE EXCEPTION 'Score child report mismatch' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='score_report_item_responses' THEN
   SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
   IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row'
    OR response_row.card_revision_id IS DISTINCT FROM NEW.card_revision_id
    OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id AND state='confirmed' AND confirmed_by IS NOT NULL) THEN
    RAISE EXCEPTION 'Score support requires a fixed confirmed response' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.kind='tender' THEN
    SELECT * INTO rubric_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=item_row.rubric_item_id;
    IF NEW.source IS DISTINCT FROM jsonb_set(rubric_item.source,'{quote}',to_jsonb(NEW.quote))
     OR NEW.document_id IS DISTINCT FROM (rubric_item.source->>'document_id')::uuid
     OR NEW.chunk_id IS DISTINCT FROM (rubric_item.source->>'chunk_id')::uuid THEN
     RAISE EXCEPTION 'Score tender citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=rubric_item.source->>'quote';
   ELSE
    SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
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
END $$;

CREATE OR REPLACE FUNCTION public.score_publication_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; assessed integer; unassessable integer; section_value jsonb;
BEGIN
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
 IF job_row.status NOT IN ('running','succeeded') OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR public.score_inputs_current(NEW.org_id,NEW.draft_id,NEW.rubric_id,NEW.rubric_revision) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Score attempt or inputs lost before publication' USING ERRCODE='23514'; END IF;
 SELECT count(*) FILTER(WHERE outcome='assessed'),count(*) FILTER(WHERE outcome='unassessable') INTO assessed,unassessable
  FROM public.score_report_items WHERE org_id=NEW.org_id AND report_id=NEW.id;
 IF assessed+unassessable NOT BETWEEN 1 AND 2000 OR assessed+unassessable<>(SELECT count(*) FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id)
  OR (NEW.summary->>'assessed_items')::integer IS DISTINCT FROM assessed
  OR (NEW.summary->>'unassessable_items')::integer IS DISTINCT FROM unassessable
  OR (unassessable>0 AND NEW.completion<>'partial')
  OR NEW.summary->>'total_status' IS NULL OR NEW.summary->>'total_status' NOT IN ('estimated','range_only','unavailable')
  OR (NEW.completion='complete' AND NEW.summary->>'total_status'<>'estimated')
  OR ((NEW.summary->>'total_status'='estimated') IS DISTINCT FROM (NEW.summary->>'estimated_total' IS NOT NULL))
  OR ((unassessable>0 OR NEW.completion<>'complete') AND NEW.summary->>'estimated_total' IS NOT NULL)
  OR (NEW.summary->>'total_status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id AND overall_aggregation IN ('formula','non_additive'))) THEN
  RAISE EXCEPTION 'Score summary must match published coverage' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_report_items item WHERE item.org_id=NEW.org_id AND item.report_id=NEW.id AND item.outcome='assessed'
  AND (NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=item.org_id AND cite.score_item_id=item.id AND cite.kind='tender')
   OR NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=item.org_id AND cite.score_item_id=item.id AND cite.kind='draft')))
  OR EXISTS(SELECT 1 FROM public.score_report_item_responses support WHERE support.org_id=NEW.org_id AND support.report_id=NEW.id
   AND NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=support.org_id AND cite.score_item_id=support.score_item_id AND cite.response_item_id=support.response_item_id AND cite.kind='draft')) THEN
  RAISE EXCEPTION 'Score assessed items require tender and actual supporting draft citations' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.sections)<>(SELECT count(*) FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id)
  OR (SELECT count(DISTINCT entry->>'section_key') FROM jsonb_array_elements(NEW.sections) entry)<>jsonb_array_length(NEW.sections) THEN
  RAISE EXCEPTION 'Score report must include each rubric section' USING ERRCODE='23514'; END IF;
 FOR section_value IN SELECT value FROM jsonb_array_elements(NEW.sections) LOOP
  SELECT count(*) FILTER(WHERE item.outcome='assessed'),count(*) FILTER(WHERE item.outcome='unassessable') INTO assessed,unassessable
   FROM public.score_report_items item JOIN public.score_rubric_sections section ON section.org_id=item.org_id AND section.id=item.section_id
   WHERE item.org_id=NEW.org_id AND item.report_id=NEW.id AND section.key=section_value->>'section_key';
  IF assessed+unassessable=0 OR (section_value->>'assessed_items')::integer IS DISTINCT FROM assessed
   OR (section_value->>'unassessable_items')::integer IS DISTINCT FROM unassessable
   OR ((section_value->>'status'='estimated') IS DISTINCT FROM (section_value->>'estimated_score' IS NOT NULL))
   OR section_value->>'status' IS NULL OR section_value->>'status' NOT IN ('estimated','range_only','unavailable')
   OR (unassessable>0 AND section_value->>'estimated_score' IS NOT NULL)
   OR (section_value->>'status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND key=section_value->>'section_key' AND aggregation IN ('formula','non_additive'))) THEN
   RAISE EXCEPTION 'Score section summary must match published coverage' USING ERRCODE='23514'; END IF;
 END LOOP;
 PERFORM public.citation_seal_publication(NEW.org_id,NEW.id,'score');
 RETURN NEW;
END $$;
"""
