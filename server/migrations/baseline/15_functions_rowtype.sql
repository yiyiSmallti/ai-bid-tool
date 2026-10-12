-- FUNCTION: rubric_section_sources(public.score_rubric_sections)
CREATE FUNCTION public.rubric_section_sources(section_row public.score_rubric_sections) RETURNS jsonb
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce((section_row).sources,jsonb_build_array(jsonb_build_object(
  'requirement_id',(section_row).requirement_id,'source',(section_row).source,'quote',(section_row).source->>'quote')));
$$;
