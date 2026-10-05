"""Migration-owned SQL for same-transaction statement-level event producers.

Statements aggregate transition rows before taking event-head locks, avoiding one
frame per published requirement and ordering multi-task head locks by UUID.
"""

APPEND_SQL = r"""
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='bid_task_event_writer') THEN
  CREATE ROLE bid_task_event_writer NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='bid_task_event_writer' AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolinherit)) THEN
  RAISE EXCEPTION 'Unsafe task event owner role';
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO bid_task_event_writer;
GRANT SELECT,INSERT,UPDATE ON public.task_event_heads TO bid_task_event_writer;
GRANT SELECT,INSERT,DELETE ON public.task_events TO bid_task_event_writer;
GRANT SELECT(id,org_id,task_id,kind) ON public.jobs TO bid_task_event_writer;
GRANT SELECT(id,org_id,task_id,job_id) ON public.requirements TO bid_task_event_writer;
GRANT SELECT(id,org_id,task_id,extraction_job_id) ON public.response_cards TO bid_task_event_writer;
REVOKE INSERT,UPDATE,DELETE ON public.task_events FROM bid_app;
REVOKE UPDATE,DELETE ON public.task_event_heads FROM bid_app;
CREATE OR REPLACE FUNCTION public.task_events_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' AND current_user='bid_task_event_writer' AND EXISTS(
   SELECT 1 FROM public.task_event_heads h WHERE h.org_id=OLD.org_id AND h.task_id=OLD.task_id
    AND (OLD.seq<=h.last_seq-50000 OR OLD.created_at<clock_timestamp()-interval '7 days')) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'task events are immutable' USING ERRCODE='42501';
END $$;

CREATE OR REPLACE FUNCTION public.append_task_event(
 p_org uuid, p_task uuid, p_kind text, p_payload jsonb, p_source uuid DEFAULT NULL
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE n bigint; floor_seq bigint; prune_to bigint;
BEGIN
 IF p_org IS DISTINCT FROM nullif(current_setting('app.current_org', true),'')::uuid THEN
  RAISE EXCEPTION 'Task event org mismatch' USING ERRCODE='42501';
 END IF;
 IF p_kind NOT IN ('board_changed','job_progress','access_changed')
   OR p_payload->>'type' IS DISTINCT FROM p_kind OR octet_length(p_payload::text)>1800 THEN
  RAISE EXCEPTION 'Invalid task event metadata' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' AND (p_payload - ARRAY['type','extraction_job_id','requirement_ids','card_ids','thread_id','comment_id','invalidate_all']) <> '{}'::jsonb
 OR p_kind='job_progress' AND (p_payload - ARRAY['type','job_id','state','run_id','attempts','progress']) <> '{}'::jsonb
 OR p_kind='access_changed' AND (p_payload - ARRAY['type','state','access_epoch']) <> '{}'::jsonb THEN
  RAISE EXCEPTION 'Unexpected event fields' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' THEN
  IF jsonb_typeof(p_payload->'invalidate_all') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(p_payload->'requirement_ids') IS DISTINCT FROM 'array'
    OR jsonb_typeof(p_payload->'card_ids') IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_payload->'requirement_ids')+jsonb_array_length(p_payload->'card_ids')
       +(CASE WHEN p_payload->>'thread_id' IS NULL THEN 0 ELSE 1 END)
       +(CASE WHEN p_payload->>'comment_id' IS NULL THEN 0 ELSE 1 END)>100
    OR (p_payload->>'invalidate_all')::boolean IS DISTINCT FROM
       (jsonb_array_length(p_payload->'requirement_ids')+jsonb_array_length(p_payload->'card_ids')=0
        AND coalesce(p_payload->>'thread_id',p_payload->>'comment_id') IS NULL) THEN
   RAISE EXCEPTION 'Invalid board metadata' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_payload->'requirement_ids'))
   OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_payload->'card_ids')) THEN
   RAISE EXCEPTION 'Duplicate event IDs' USING ERRCODE='23514';
  END IF;
  PERFORM value::uuid FROM jsonb_array_elements_text(p_payload->'requirement_ids');
  PERFORM value::uuid FROM jsonb_array_elements_text(p_payload->'card_ids');
  PERFORM (p_payload->>'extraction_job_id')::uuid,(p_payload->>'thread_id')::uuid,(p_payload->>'comment_id')::uuid;
 ELSIF p_kind='job_progress' THEN
  IF p_payload->>'state' IS NULL OR p_payload->>'state' NOT IN ('queued','running','succeeded','failed','cancelled')
    OR jsonb_typeof(p_payload->'attempts') IS DISTINCT FROM 'number'
    OR coalesce(p_payload->>'attempts','') !~ '^[0-9]{1,9}$'
    OR p_payload->>'job_id' IS NULL THEN
   RAISE EXCEPTION 'Invalid job metadata' USING ERRCODE='23514';
  END IF;
  PERFORM (p_payload->>'job_id')::uuid,(p_payload->>'run_id')::uuid;
  IF p_payload->'progress' IS NOT NULL AND p_payload->'progress'<>'null'::jsonb THEN
   IF jsonb_typeof(p_payload->'progress') IS DISTINCT FROM 'object'
     OR (p_payload->'progress'-ARRAY['completed','total'])<>'{}'::jsonb
     OR jsonb_typeof(p_payload#>'{progress,completed}') IS DISTINCT FROM 'number'
     OR coalesce(p_payload#>>'{progress,completed}','') !~ '^[0-9]{1,9}$'
     OR ((p_payload#>>'{progress,total}') IS NOT NULL AND (
       jsonb_typeof(p_payload#>'{progress,total}') IS DISTINCT FROM 'number'
       OR (p_payload#>>'{progress,total}') !~ '^[0-9]{1,9}$'
       OR (p_payload#>>'{progress,total}')::bigint<(p_payload#>>'{progress,completed}')::bigint)) THEN
    RAISE EXCEPTION 'Invalid progress units' USING ERRCODE='23514';
   END IF;
  END IF;
 ELSIF p_kind='access_changed' THEN
  IF p_payload->>'state' IS NULL OR p_payload->>'state' NOT IN ('active','archived')
    OR jsonb_typeof(p_payload->'access_epoch') IS DISTINCT FROM 'number'
    OR coalesce(p_payload->>'access_epoch','') !~ '^[1-9][0-9]{0,8}$' THEN
   RAISE EXCEPTION 'Invalid access metadata' USING ERRCODE='23514';
  END IF;
 END IF;
 IF p_source IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs
  WHERE org_id=p_org AND task_id=p_task AND id=p_source) THEN
  RAISE EXCEPTION 'Invalid task event source' USING ERRCODE='23514';
 END IF;
 IF p_kind='job_progress' AND NOT EXISTS(SELECT 1 FROM public.jobs
  WHERE org_id=p_org AND task_id=p_task AND id=(p_payload->>'job_id')::uuid) THEN
  RAISE EXCEPTION 'Invalid event job binding' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' THEN
  IF p_payload->>'extraction_job_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs
   WHERE org_id=p_org AND task_id=p_task AND kind='extract' AND id=(p_payload->>'extraction_job_id')::uuid) THEN
   RAISE EXCEPTION 'Invalid event extraction binding' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_payload->'requirement_ids') r(value)
    WHERE NOT EXISTS(SELECT 1 FROM public.requirements q WHERE q.org_id=p_org AND q.task_id=p_task AND q.id=r.value::uuid
     AND (p_payload->>'extraction_job_id' IS NULL OR q.job_id=(p_payload->>'extraction_job_id')::uuid)))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_payload->'card_ids') r(value)
    WHERE NOT EXISTS(SELECT 1 FROM public.response_cards c WHERE c.org_id=p_org AND c.task_id=p_task AND c.id=r.value::uuid
     AND (p_payload->>'extraction_job_id' IS NULL OR c.extraction_job_id=(p_payload->>'extraction_job_id')::uuid)))
   OR p_payload->>'thread_id' IS NOT NULL OR p_payload->>'comment_id' IS NOT NULL THEN
   RAISE EXCEPTION 'Invalid board event parent binding' USING ERRCODE='23514';
  END IF;
 END IF;
 INSERT INTO public.task_event_heads(id,org_id,task_id,last_seq,retained_floor_seq,created_at)
 VALUES(gen_random_uuid(),p_org,p_task,0,0,clock_timestamp()) ON CONFLICT(org_id,task_id) DO NOTHING;
 UPDATE public.task_event_heads SET last_seq=last_seq+1
 WHERE org_id=p_org AND task_id=p_task RETURNING last_seq,retained_floor_seq INTO n,floor_seq;
 INSERT INTO public.task_events(id,org_id,task_id,seq,event_kind,payload,source_id,created_at)
 VALUES(gen_random_uuid(),p_org,p_task,n,p_kind,p_payload,p_source,clock_timestamp());
 SELECT coalesce(max(seq),0) INTO prune_to FROM public.task_events
 WHERE org_id=p_org AND task_id=p_task
 AND (seq<=n-50000 OR created_at<clock_timestamp()-interval '7 days');
 IF prune_to>floor_seq THEN
  DELETE FROM public.task_events WHERE org_id=p_org AND task_id=p_task AND seq<=prune_to;
  UPDATE public.task_event_heads SET retained_floor_seq=prune_to WHERE org_id=p_org AND task_id=p_task;
 END IF;
END $$;
GRANT CREATE ON SCHEMA public TO bid_task_event_writer;
ALTER FUNCTION public.append_task_event(uuid,uuid,text,jsonb,uuid) OWNER TO bid_task_event_writer;
REVOKE CREATE ON SCHEMA public FROM bid_task_event_writer;
REVOKE ALL ON FUNCTION public.append_task_event(uuid,uuid,text,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.append_task_event(uuid,uuid,text,jsonb,uuid) TO bid_app;
"""

PRODUCER_SQL = r"""
CREATE OR REPLACE FUNCTION public.produce_task_events() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE changed_task record; j jsonb; q text; total integer; payload jsonb; source uuid;
        previous_org text:=current_setting('app.current_org',true); derive_org boolean;
BEGIN
 -- This trigger runs as the statement's effective role. Check that role here:
 -- append_task_event is SECURITY DEFINER, where current_user is always its owner.
 -- Only DB administrators already able to cross RLS may supply missing context
 -- from transition rows; runtime requests/workers must provide their own org.
 SELECT nullif(previous_org,'') IS NULL AND (r.rolsuper OR r.rolbypassrls)
 INTO derive_org FROM pg_catalog.pg_roles r WHERE r.rolname=current_user;
 IF TG_OP='DELETE' THEN q='SELECT to_jsonb(t) AS value FROM old_rows t';
 ELSE q='SELECT to_jsonb(t) AS value FROM new_rows t'; END IF;
 IF TG_TABLE_NAME='jobs' AND TG_OP='UPDATE' THEN
  q='SELECT to_jsonb(n) AS value FROM new_rows n JOIN old_rows o ON n.id=o.id WHERE (n.status,n.run_id,n.attempts,n.result,n.error,n.cache_key) IS DISTINCT FROM (o.status,o.run_id,o.attempts,o.result,o.error,o.cache_key)';
 END IF;
 -- Resource revisions fan out through the pinned task selections. Transition rows
 -- are statement-local, and all affected heads are locked in stable task order.
 IF TG_ARGV[0]='library' THEN
  q='SELECT DISTINCT s.org_id,s.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.'
    ||quote_ident(TG_ARGV[1])||' s ON s.org_id=(x.value->>''org_id'')::uuid AND s.'
    ||quote_ident(TG_ARGV[2])||'=(x.value->>'||quote_literal(TG_ARGV[3])||')::uuid ORDER BY s.task_id';
 ELSIF TG_ARGV[0]='derived_job' THEN
  q='SELECT DISTINCT p.org_id,p.task_id,jsonb_build_object(''source_id'',p.'||quote_ident(TG_ARGV[3])||') AS value FROM ('||q||') x JOIN public.'||quote_ident(TG_ARGV[1])||' p ON p.org_id=(x.value->>''org_id'')::uuid AND p.id=(x.value->>'||quote_literal(TG_ARGV[2])||')::uuid ORDER BY p.task_id';
 ELSIF TG_ARGV[0]='card_revision' THEN
  q='SELECT DISTINCT c.org_id,c.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.response_cards c ON c.org_id=(x.value->>''org_id'')::uuid AND c.id=(x.value->>''card_id'')::uuid ORDER BY c.task_id';
 ELSIF TG_ARGV[0]='card_link' THEN
  q='SELECT DISTINCT c.org_id,c.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.response_card_revisions v ON v.org_id=(x.value->>''org_id'')::uuid AND v.id=(x.value->>''revision_id'')::uuid JOIN public.response_cards c ON c.org_id=v.org_id AND c.id=v.card_id ORDER BY c.task_id';
 ELSIF TG_TABLE_NAME='exports' THEN
  q='SELECT DISTINCT r.org_id,r.task_id,jsonb_build_object(''source_id'',r.render_job_id) AS value FROM ('||q||') x JOIN public.export_runs r ON r.org_id=(x.value->>''org_id'')::uuid AND r.id=(x.value->>''run_id'')::uuid ORDER BY r.task_id';
 ELSIF TG_TABLE_NAME='jobs' THEN
  q='SELECT (value->>''org_id'')::uuid org_id,(value->>''task_id'')::uuid task_id,value FROM ('||q||') x WHERE value->>''task_id'' IS NOT NULL ORDER BY task_id,value->>''id''';
 ELSIF TG_TABLE_NAME='task_workflows' THEN
  q='SELECT (value->>''org_id'')::uuid org_id,(value->>''task_id'')::uuid task_id,value FROM ('||q||') x ORDER BY task_id';
 ELSE
  q='SELECT DISTINCT (value->>''org_id'')::uuid org_id,(value->>'||quote_literal(TG_ARGV[0])||')::uuid task_id,jsonb_build_object(''source_id'',value->>'||quote_literal(coalesce(TG_ARGV[1],''))||') AS value FROM ('||q||') x WHERE value->>'||quote_literal(TG_ARGV[0])||' IS NOT NULL ORDER BY task_id';
 END IF;
 EXECUTE 'SELECT count(DISTINCT task_id) FROM ('||q||') affected' INTO total;
 IF total>100 THEN RAISE EXCEPTION 'affected_task_limit' USING ERRCODE='54000'; END IF;
 FOR changed_task IN EXECUTE q LOOP
  source=(changed_task.value->>'source_id')::uuid;
  IF TG_TABLE_NAME='jobs' THEN
   j=changed_task.value;
   payload=jsonb_build_object('type','job_progress','job_id',j->>'id','state',j->>'status',
     'run_id',j->'run_id','attempts',j->'attempts','progress',NULL);
   -- Only persisted integer units are progress; never infer percentages from state.
   IF jsonb_typeof(j#>'{result,progress}')='object'
      AND (j#>>'{result,progress,completed}')~'^[0-9]{1,9}$'
      AND ((j#>'{result,progress,total}')='null'::jsonb OR j#>'{result,progress,total}' IS NULL
        OR ((j#>>'{result,progress,total}')~'^[0-9]{1,9}$'
           AND (j#>>'{result,progress,total}')::bigint >= (j#>>'{result,progress,completed}')::bigint)) THEN
     payload=jsonb_set(payload,'{progress}',jsonb_build_object('completed',(j#>>'{result,progress,completed}')::bigint,'total',j#>'{result,progress,total}'));
   END IF;
   source=(j->>'id')::uuid;
  ELSIF TG_TABLE_NAME='task_workflows' THEN
   payload=jsonb_build_object('type','access_changed','state',changed_task.value->>'state','access_epoch',changed_task.value->'access_epoch');
  ELSE
   payload=jsonb_build_object('type','board_changed','invalidate_all',true,'requirement_ids','[]'::jsonb,'card_ids','[]'::jsonb);
  END IF;
  IF derive_org THEN
   PERFORM set_config('app.current_org',changed_task.org_id::text,true);
  END IF;
  -- An append failure aborts the statement, rolling back its local GUC change
  -- along with the business rows and event head; successful rows restore it here.
  PERFORM public.append_task_event(changed_task.org_id,changed_task.task_id,payload->>'type',payload,source);
  IF derive_org THEN
   PERFORM set_config('app.current_org',coalesce(previous_org,''),true);
  END IF;
 END LOOP;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.produce_task_events() FROM PUBLIC;
"""


PRODUCER_ARGUMENTS = {
    "card_evidence_links": ("card_link",),
    "card_generation_runs": ("task_id", "generation_job_id"),
    "certificate_revisions": ("library", "task_certificates", "certificate_id", "certificate_id"),
    "certificates": ("library", "task_certificates", "certificate_id", "id"),
    "check_certificate_items": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_certificates": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_decisions": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_finding_citations": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_findings": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_items": ("derived_job", "check_runs", "report_id", "job_id"),
    "check_runs": ("task_id", "job_id"),
    "chunks": ("task_id", ""),
    "confidential_values": ("task_id", ""),
    "documents": ("task_id", ""),
    "draft_runs": ("task_id", "generation_job_id"),
    "evidence": ("task_id", ""),
    "evidence_sources": ("task_id", ""),
    "export_run_items": ("derived_job", "export_runs", "run_id", "render_job_id"),
    "export_run_evidence": ("derived_job", "export_runs", "run_id", "render_job_id"),
    "export_render_candidates": ("derived_job", "export_runs", "run_id", "render_job_id"),
    "export_runs": ("task_id", "render_job_id"),
    "exports": ("task_id", ""),
    "feature_revisions": ("library", "task_features", "feature_id", "feature_id"),
    "features": ("library", "task_features", "feature_id", "id"),
    "jobs": ("task_id", ""),
    "memories": ("task_id", ""),
    "memory_call_inputs": ("task_id", "job_id"),
    "memory_eval_samples": ("task_id", ""),
    "memory_feedback_events": ("task_id", ""),
    "memory_retrievals": ("task_id", ""),
    "memory_revisions": ("task_id", ""),
    "org_profile_revisions": ("library", "task_org_profiles", "profile_id", "profile_id"),
    "org_profiles": ("library", "task_org_profiles", "profile_id", "id"),
    "product_revisions": ("library", "task_resources", "product_id", "product_id"),
    "products": ("library", "task_resources", "product_id", "id"),
    "prototype_decision_batches": ("task_id", ""),
    "prototype_evidence_decisions": ("task_id", ""),
    "requirements": ("task_id", "job_id"),
    "response_card_revisions": ("card_revision",),
    "response_cards": ("task_id", ""),
    "sandbox_artifacts": ("derived_job", "sandbox_runs", "sandbox_run_id", "job_id"),
    "sandbox_attempts": ("task_id", "job_id"),
    "sandbox_fetch_receipts": ("derived_job", "sandbox_runs", "sandbox_run_id", "job_id"),
    "sandbox_inputs": ("task_id", "generation_job_id"),
    "sandbox_runs": ("task_id", "job_id"),
    "score_item_citations": ("derived_job", "score_reports", "report_id", "job_id"),
    "score_report_item_responses": ("derived_job", "score_reports", "report_id", "job_id"),
    "score_report_items": ("derived_job", "score_reports", "report_id", "job_id"),
    "score_reports": ("task_id", "job_id"),
    "score_rubric_classifications": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_coverage": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_coverage_decisions": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_coverage_items": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_decisions": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_items": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_revision_events": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_sections": ("derived_job", "score_rubric_sets", "rubric_id", "job_id"),
    "score_rubric_sets": ("task_id", "job_id"),
    "screenshot_analysis_inputs": (
        "derived_job",
        "screenshot_analysis_runs",
        "analysis_run_id",
        "job_id",
    ),
    "screenshot_analysis_runs": ("task_id", "job_id"),
    "screenshot_assets": ("task_id", ""),
    "screenshot_privacy_reviews": ("task_id", ""),
    "screenshot_prototype_runs": ("task_id", "generation_job_id"),
    "screenshot_renditions": ("task_id", "generation_job_id"),
    "screenshot_search_candidates": (
        "derived_job",
        "screenshot_search_runs",
        "search_run_id",
        "job_id",
    ),
    "screenshot_search_runs": ("task_id", "job_id"),
    "screenshot_suggestions": (
        "derived_job",
        "screenshot_analysis_runs",
        "analysis_run_id",
        "job_id",
    ),
    "screenshot_vendor_archives": ("derived_job", "sandbox_runs", "sandbox_run_id", "job_id"),
    "screenshot_withdrawals": ("task_id", ""),
    "task_budget_revisions": ("task_id", ""),
    "task_certificates": ("task_id", ""),
    "task_features": ("task_id", ""),
    "task_org_profiles": ("task_id", ""),
    "task_resources": ("task_id", ""),
    "task_templates": ("task_id", ""),
    "task_workflows": ("task_id", ""),
    "tasks": ("id",),
    "template_revisions": ("library", "task_templates", "template_id", "template_id"),
    "templates": ("library", "task_templates", "template_id", "id"),
    "usage_records": ("task_id", "job_id"),
    "vendor_calls": ("task_id", "job_id"),
}


def install(op):
    op.execute(APPEND_SQL)
    op.execute(PRODUCER_SQL)
    for name, args in PRODUCER_ARGUMENTS.items():
        params = ",".join("'" + arg + "'" for arg in args)
        for operation in ("INSERT", "UPDATE", "DELETE"):
            if name == "tasks" and operation == "INSERT":
                continue
            # Append-only entity tables reject forbidden UPDATE/DELETE themselves.
            transition = (
                "OLD TABLE AS old_rows"
                if operation == "DELETE"
                else "OLD TABLE AS old_rows NEW TABLE AS new_rows"
                if operation == "UPDATE"
                else "NEW TABLE AS new_rows"
            )
            op.execute(
                f"CREATE TRIGGER task_event_{operation.lower()} AFTER {operation} ON public.{name} REFERENCING {transition} FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events({params})"
            )
