-- FUNCTION: actor_is_human()
CREATE FUNCTION public.actor_is_human() RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
    -- The installation's one human acts through a console session or the access
    -- key; agents and API tokens never satisfy this.
    SELECT current_setting('app.actor_kind', true) IS NOT DISTINCT FROM 'session'
       AND nullif(current_setting('app.actor_token_id', true), '') IS NULL
       AND nullif(current_setting('app.agent_session_id', true), '') IS NULL
$$;

-- FUNCTION: agent_history_guard()
CREATE FUNCTION public.agent_history_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE s public.agent_sessions; j public.jobs; kind text:=current_setting('app.actor_kind',true);
 principal uuid:=NULLIF(current_setting('app.agent_principal_id',true),'')::uuid;
 execution_job uuid:=NULLIF(current_setting('app.execution_job_id',true),'')::uuid;
 execution_run uuid:=NULLIF(current_setting('app.execution_run_id',true),'')::uuid;
 delegated boolean; live_execution boolean;
 sid uuid; enc text; col text;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent history cannot be deleted' USING ERRCODE='42501'; END IF;
 -- The existing encryption rotation command uses the migration owner. Only the
 -- ciphertext columns may change on that path; no app-set flag grants an exception.
 IF TG_OP='UPDATE' AND current_user<>'bid_app' AND
   (to_jsonb(NEW)-ARRAY['content_enc','arguments_enc','result_enc','question_enc','receipt_enc','start_receipt_enc','cancel_receipt_enc'])
   IS NOT DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['content_enc','arguments_enc','result_enc','question_enc','receipt_enc','start_receipt_enc','cancel_receipt_enc']) THEN
   RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='agent_sessions' THEN sid:=NEW.id; ELSE sid:=NEW.session_id; END IF;
 SELECT * INTO s FROM public.agent_sessions WHERE id=sid;
 IF TG_TABLE_NAME='agent_sessions' AND TG_OP='INSERT' THEN s:=NEW; END IF;
 IF s.id IS NULL THEN RAISE EXCEPTION 'agent session is unavailable' USING ERRCODE='23514'; END IF;
 IF NOT public.actor_is_human() AND NOT COALESCE((
    kind IN ('agent','worker') AND principal=s.principal_id
    AND NULLIF(current_setting('app.agent_session_id',true),'')::uuid=s.id),false) THEN
   RAISE EXCEPTION 'agent history actor mismatch' USING ERRCODE='42501'; END IF;
 IF kind IN ('agent','worker') THEN
   SELECT EXISTS(SELECT FROM public.agent_principals p
      WHERE p.id=s.principal_id AND p.revoked_at IS NULL
        AND p.authority_expires_at>clock_timestamp() AND s.expires_at>clock_timestamp())
      AND public.agent_task_authority(s.task_id) INTO delegated;
   -- Expired delegation or a missing task blocks publication/dispatch while
   -- recovery may still retain a pause or terminal receipt without paid work.
   IF NOT delegated AND NOT (
       (TG_TABLE_NAME='agent_sessions' AND TG_OP='UPDATE' AND to_jsonb(NEW)->>'state' IN ('paused','failed','partial','cancelled'))
       OR TG_TABLE_NAME='agent_pauses'
       OR (TG_TABLE_NAME='agent_steps' AND TG_OP='UPDATE' AND to_jsonb(NEW)->>'state' IN ('failed','uncertain'))
       OR (TG_TABLE_NAME='agent_messages' AND to_jsonb(NEW)->>'role'='system')) THEN
     RAISE EXCEPTION 'agent delegation is no longer active' USING ERRCODE='42501'; END IF;
   IF TG_TABLE_NAME IN ('agent_messages','agent_pauses','agent_job_links') THEN
     SELECT * INTO j FROM public.jobs WHERE id=execution_job;
     live_execution:=j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
       AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
       AND (s.current_job_id,s.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id);
     IF NOT COALESCE(live_execution,false) AND NOT (
       execution_job IS NULL AND execution_run IS NULL
       AND NOT EXISTS(SELECT FROM public.jobs WHERE id=s.current_job_id
         AND status='running' AND lease_until>clock_timestamp())
       AND (TG_TABLE_NAME='agent_pauses'
         OR (TG_TABLE_NAME='agent_messages' AND to_jsonb(NEW)->>'role'='system')
         OR (TG_TABLE_NAME='agent_job_links' AND to_jsonb(NEW)->>'role'='controller'))) THEN
       RAISE EXCEPTION 'stale agent history execution' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 FOREACH col IN ARRAY ARRAY['content_enc','arguments_enc','result_enc','question_enc','receipt_enc','start_receipt_enc','cancel_receipt_enc'] LOOP
   enc:=to_jsonb(NEW)->>col;
   IF enc IS NOT NULL AND (length(enc)<80 OR enc !~ '^gAAAA') THEN
     RAISE EXCEPTION 'agent content must be encrypted' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF TG_TABLE_NAME='agent_sessions' THEN
   IF TG_OP='INSERT' THEN
     IF NEW.state<>'queued' OR NEW.revision<>1 OR NEW.steps_used<>0 OR NEW.vendor_calls_used<>0 OR NEW.active_seconds_used<>0 THEN
       RAISE EXCEPTION 'new agent session requires initial checkpoint' USING ERRCODE='23514'; END IF;
     IF NOT public.actor_is_human()
       OR NOT COALESCE(public.agent_task_authority(NEW.task_id),false)
       OR NOT EXISTS(
       SELECT FROM public.jobs x WHERE x.id=NEW.extraction_job_id
         AND x.task_id=NEW.task_id AND x.document_id=NEW.document_id AND x.kind='extract' AND x.status='succeeded') THEN
       RAISE EXCEPTION 'agent requires human and successful extraction' USING ERRCODE='42501'; END IF;
   ELSE
     IF kind='session' AND NEW.state NOT IN ('cancelled','failed','partial','paused')
       AND NOT COALESCE(public.agent_task_authority(s.task_id),false) THEN
       RAISE EXCEPTION 'agent task authority is no longer active' USING ERRCODE='42501'; END IF;
     IF kind IN ('agent','worker') THEN
       SELECT * INTO j FROM public.jobs WHERE id=execution_job;
       live_execution:=j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
         AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
         AND ((OLD.current_job_id,OLD.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id)
           OR (OLD.state='queued' AND NEW.state='running'
             AND (NEW.current_job_id,NEW.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id)));
       IF NOT COALESCE(live_execution,false) AND NOT (
         execution_job IS NULL AND execution_run IS NULL
         AND NOT EXISTS(SELECT FROM public.jobs WHERE id=OLD.current_job_id
           AND status='running' AND lease_until>clock_timestamp())
         AND (NEW.state IN ('paused','failed','partial','cancelled') OR
           (OLD.state IN ('queued','running','waiting_job') AND NEW.state='queued'))) THEN
         RAISE EXCEPTION 'stale agent session execution' USING ERRCODE='23514'; END IF;
       IF OLD.state='paused' AND NEW.state='queued' THEN
         RAISE EXCEPTION 'only a human may resume an agent' USING ERRCODE='42501'; END IF;
     END IF;
     IF (NEW.id,NEW.task_id,NEW.document_id,NEW.extraction_job_id,NEW.principal_id,
       NEW.limits,NEW.model_snapshot,NEW.tool_schema_sha256,NEW.model_sha256,
       NEW.input_sha256,NEW.expires_at,NEW.start_idempotency_key,NEW.start_request_sha256,NEW.created_at)
       IS DISTINCT FROM (OLD.id,OLD.task_id,OLD.document_id,OLD.extraction_job_id,OLD.principal_id,
       OLD.limits,OLD.model_snapshot,OLD.tool_schema_sha256,OLD.model_sha256,
       OLD.input_sha256,OLD.expires_at,OLD.start_idempotency_key,OLD.start_request_sha256,OLD.created_at)
       OR NEW.revision<OLD.revision OR NEW.steps_used<OLD.steps_used OR NEW.vendor_calls_used<OLD.vendor_calls_used
       OR NEW.active_seconds_used<OLD.active_seconds_used
       OR (OLD.state IN ('completed','partial','failed','cancelled') AND NEW IS DISTINCT FROM OLD)
       OR (OLD.start_receipt_enc IS NOT NULL AND NEW.start_receipt_enc IS DISTINCT FROM OLD.start_receipt_enc)
       OR (OLD.cancel_idempotency_key IS NOT NULL AND
           (NEW.cancel_idempotency_key,NEW.cancel_request_sha256,NEW.cancel_receipt_enc) IS DISTINCT FROM
           (OLD.cancel_idempotency_key,OLD.cancel_request_sha256,OLD.cancel_receipt_enc)) THEN
       RAISE EXCEPTION 'agent session binding and terminal state are immutable' USING ERRCODE='23514'; END IF;
   END IF;
 ELSIF TG_TABLE_NAME='agent_messages' THEN
   IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'agent messages are append only' USING ERRCODE='42501'; END IF;
   IF (NEW.role='human' AND (NOT public.actor_is_human()
         OR NOT COALESCE(public.agent_task_authority(s.task_id),false)))
      OR (NEW.role<>'human' AND kind NOT IN ('agent','worker')) THEN
      RAISE EXCEPTION 'message role does not match actor' USING ERRCODE='42501'; END IF;
 ELSIF TG_TABLE_NAME='agent_steps' THEN
   IF kind NOT IN ('agent','worker') THEN RAISE EXCEPTION 'step requires agent execution' USING ERRCODE='42501'; END IF;
   SELECT * INTO j FROM public.jobs WHERE id=NEW.last_transition_job_id;
   IF j.id IS NULL OR j.run_id IS DISTINCT FROM NEW.last_transition_run_id OR j.status<>'running'
       OR j.id IS DISTINCT FROM execution_job OR j.run_id IS DISTINCT FROM execution_run
       OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
       OR s.current_job_id IS DISTINCT FROM j.id OR s.current_run_id IS DISTINCT FROM j.run_id THEN
     RAISE EXCEPTION 'stale agent execution' USING ERRCODE='23514'; END IF;
   IF TG_OP='INSERT' AND (NEW.created_by_job_id,NEW.created_by_run_id) IS DISTINCT FROM
       (NEW.last_transition_job_id,NEW.last_transition_run_id) THEN
     RAISE EXCEPTION 'step creator must be current execution' USING ERRCODE='23514'; END IF;
   IF TG_OP='UPDATE' AND (
      (NEW.id,NEW.session_id,NEW.task_id,NEW.document_id,NEW.ordinal,NEW.kind,NEW.command,
       NEW.created_by_job_id,NEW.created_by_run_id,NEW.invocation_id,NEW.arguments_enc,NEW.arguments_sha256,
       NEW.input_refs,NEW.schema_sha256,NEW.created_at) IS DISTINCT FROM
      (OLD.id,OLD.session_id,OLD.task_id,OLD.document_id,OLD.ordinal,OLD.kind,OLD.command,
       OLD.created_by_job_id,OLD.created_by_run_id,OLD.invocation_id,OLD.arguments_enc,OLD.arguments_sha256,
       OLD.input_refs,OLD.schema_sha256,OLD.created_at)
      OR OLD.state IN ('completed','failed') OR NEW.revision<>OLD.revision+1) THEN
     RAISE EXCEPTION 'step provenance and completed output are immutable' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='agent_pauses' THEN
   IF TG_OP='UPDATE' AND (
      (to_jsonb(NEW)-ARRAY['status','resolved_at','resume_idempotency_key','request_sha256','receipt_enc'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','resolved_at','resume_idempotency_key','request_sha256','receipt_enc'])
      OR OLD.status<>'pending') THEN
     RAISE EXCEPTION 'pause input and resolution are immutable' USING ERRCODE='23514'; END IF;
   IF NEW.status='resolved' AND (NOT public.actor_is_human()
      OR NOT COALESCE(public.agent_task_authority(s.task_id),false)
      OR NEW.resolved_at IS NULL) THEN
     RAISE EXCEPTION 'pause resolution requires its human owner' USING ERRCODE='42501'; END IF;
   IF TG_OP='INSERT' AND (NEW.status<>'pending' OR NEW.resolved_at IS NOT NULL) THEN
     RAISE EXCEPTION 'new pause must be pending' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='agent_job_links' THEN
   IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'job ownership is immutable' USING ERRCODE='42501'; END IF;
   IF kind='session' AND NOT public.agent_task_authority(s.task_id) THEN
     RAISE EXCEPTION 'agent task authority is no longer active' USING ERRCODE='42501'; END IF;
   SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
   IF NEW.owned AND (j.id IS NULL OR j.initiated_by IS DISTINCT FROM 'builtin_agent'
      OR j.agent_session_id IS DISTINCT FROM s.id) THEN
     RAISE EXCEPTION 'owned job requires bound agent provenance' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: agent_origin_guard()
CREATE FUNCTION public.agent_origin_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE kind text:=NULLIF(current_setting('app.actor_kind',true),'');
 token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
 principal uuid:=NULLIF(current_setting('app.agent_principal_id',true),'')::uuid;
 sid uuid:=NULLIF(current_setting('app.agent_session_id',true),'')::uuid;
 step uuid:=NULLIF(current_setting('app.agent_step_id',true),'')::uuid;
 execution_job uuid:=NULLIF(current_setting('app.execution_job_id',true),'')::uuid;
 execution_run uuid:=NULLIF(current_setting('app.execution_run_id',true),'')::uuid;
 j public.jobs;
 s public.agent_sessions;
BEGIN
 IF TG_OP='DELETE' OR (TG_TABLE_NAME='audit_logs' AND TG_OP='UPDATE') THEN
   RAISE EXCEPTION 'audit and job provenance cannot be deleted or rewritten' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
   IF (NEW.initiated_by,NEW.agent_principal_id,NEW.agent_session_id,
       NEW.agent_step_id,NEW.invocation_id,NEW.command)
      IS DISTINCT FROM (OLD.initiated_by,OLD.agent_principal_id,OLD.agent_session_id,
       OLD.agent_step_id,OLD.invocation_id,OLD.command) THEN
      RAISE EXCEPTION 'job origin is immutable' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;


 IF TG_TABLE_NAME='audit_logs' THEN
  IF NEW.action='annotation.invalidate' THEN
   IF pg_trigger_depth()<2 OR NOT public.annotation_invalidation_audit_valid(NEW.object_id,NEW.details) THEN
    RAISE EXCEPTION 'Annotation invalidation audit requires a stale immutable binding' USING ERRCODE='42501'; END IF;
   NEW.actor_token_id:=NULL; NEW.actor_kind:='system'; NEW.initiated_by:='legacy_unknown';
   NEW.agent_principal_id:=NULL; NEW.agent_session_id:=NULL;
   NEW.agent_step_id:=NULL; NEW.job_id:=NULL; NEW.run_id:=NULL; NEW.invocation_id:=NULL; NEW.command:=NULL;
   RETURN NEW;
  END IF;
  IF NEW.action IN ('requirement.invalidated','requirement.repair_citation') THEN
   IF pg_trigger_depth()<2 OR NOT EXISTS(SELECT 1 FROM public.requirement_review_events e
    WHERE e.requirement_id=NEW.object_id
    -- xmin is a subxid inside SAVEPOINT, while pg_current_xact_id() reports the
    -- top-level xid. PostgreSQL owns an ExclusiveLock for the writing xid;
    -- require this backend's exact transaction-ID lock, not a caller-set marker,
    -- an advisory lock or a shared lock on another transaction.
    AND EXISTS(SELECT 1 FROM pg_catalog.pg_locks writing_xact
      WHERE writing_xact.locktype='transactionid' AND writing_xact.transactionid=e.xmin
       AND writing_xact.pid=pg_catalog.pg_backend_pid() AND writing_xact.granted
       AND writing_xact.mode='ExclusiveLock')
    AND e.action IN ('invalidate','source_repair') AND e.id=(NEW.details->>'event_id')::uuid
    AND NEW.details=jsonb_build_object('task_id',e.task_id,'extraction_job_id',e.extraction_job_id,
      'requirement_id',e.requirement_id,'event_id',e.id,'revision',e.revision,'actor_kind','system')) THEN
    RAISE EXCEPTION 'requirement invalidation audit requires immutable event' USING ERRCODE='42501'; END IF;
   NEW.actor_kind:='system'; NEW.initiated_by:='legacy_unknown'; NEW.actor_token_id:=NULL;
   NEW.agent_principal_id:=NULL; NEW.agent_session_id:=NULL;
   NEW.agent_step_id:=NULL; NEW.job_id:=NULL; NEW.run_id:=NULL; NEW.invocation_id:=NULL; NEW.command:=NULL;
   RETURN NEW;
  END IF;
  NEW.actor_kind:=COALESCE(kind,'legacy_unknown');
 END IF;

 -- A human controller targets the saved session in its row. Human requests
 -- keep app.agent_session_id unset so they still pass the human gate.
 IF TG_TABLE_NAME='jobs' THEN
   IF public.actor_is_human() AND NEW.kind='agent' THEN
     sid:=NEW.agent_session_id; principal:=NEW.agent_principal_id;
   END IF;
 END IF;
 IF kind='token' OR token IS NOT NULL THEN
   IF NEW.agent_principal_id IS NOT NULL OR NEW.agent_session_id IS NOT NULL OR
      NEW.actor_token_id IS DISTINCT FROM token
      OR NOT EXISTS(SELECT FROM public.api_tokens WHERE id=token AND NOT revoked AND expires_at>clock_timestamp()) THEN
     RAISE EXCEPTION 'token origin must match authentication' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:='external_agent';
 ELSIF principal IS NOT NULL THEN
   IF kind NOT IN ('agent','worker','session') OR (kind='session' AND NOT public.actor_is_human()) OR sid IS NULL OR
      NEW.actor_token_id IS NOT NULL OR
      (NEW.agent_principal_id IS NOT NULL AND NEW.agent_principal_id<>principal) OR
      (NEW.agent_session_id IS NOT NULL AND NEW.agent_session_id<>sid) OR
      (NEW.agent_step_id IS NOT NULL AND NEW.agent_step_id IS DISTINCT FROM step) THEN
     RAISE EXCEPTION 'agent origin must match execution context' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:='builtin_agent';
   NEW.agent_principal_id:=principal; NEW.agent_session_id:=sid; NEW.agent_step_id:=step;
   IF TG_TABLE_NAME='jobs' THEN
     SELECT * INTO s FROM public.agent_sessions WHERE id=sid;
     -- Human resume creates its controller before resolving the immutable pause
     -- receipt. Only a human may enqueue from paused; workers still cannot.
     IF s.id IS NULL OR NOT (s.state IN ('queued','running','waiting_job')
         OR (kind='session' AND NEW.kind='agent' AND s.state='paused'
           AND public.actor_is_human()))
       OR s.expires_at<=clock_timestamp()
       OR NOT COALESCE(public.agent_task_authority(s.task_id),false)
       OR NOT EXISTS(SELECT FROM public.agent_principals p
          WHERE p.id=principal AND p.id=s.principal_id
            AND p.revoked_at IS NULL AND p.authority_expires_at>clock_timestamp()) THEN
       RAISE EXCEPTION 'agent dispatch requires active delegation' USING ERRCODE='42501'; END IF;
     IF kind IN ('agent','worker') THEN
       SELECT * INTO j FROM public.jobs WHERE id=execution_job;
       IF NOT COALESCE(j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
         AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
         AND (s.current_job_id,s.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id),false)
       AND NOT (NEW.kind='agent' AND execution_job IS NULL AND execution_run IS NULL
         AND NOT EXISTS(SELECT FROM public.jobs WHERE id=s.current_job_id
           AND status='running' AND lease_until>clock_timestamp())) THEN
         RAISE EXCEPTION 'stale agent job dispatch' USING ERRCODE='23514'; END IF;
     END IF;
   END IF;
 ELSIF TG_TABLE_NAME='audit_logs' AND kind='worker' AND (to_jsonb(NEW)->>'job_id') IS NOT NULL THEN
   SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
   IF j.id IS NULL THEN RAISE EXCEPTION 'audit job is unavailable' USING ERRCODE='23514'; END IF;
   NEW.initiated_by:=j.initiated_by;
   NEW.agent_principal_id:=j.agent_principal_id; NEW.agent_session_id:=j.agent_session_id;
   NEW.agent_step_id:=j.agent_step_id; NEW.invocation_id:=j.invocation_id;
   NEW.actor_token_id:=j.actor_token_id;
 ELSE
   IF (kind='session' AND NOT public.actor_is_human())
       OR NEW.initiated_by IN ('builtin_agent','external_agent') OR NEW.agent_principal_id IS NOT NULL
       OR NEW.agent_session_id IS NOT NULL OR NEW.agent_step_id IS NOT NULL THEN
     RAISE EXCEPTION 'authenticated origin is required' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:=CASE WHEN kind='session' THEN 'human' ELSE 'legacy_unknown' END;
 END IF;
 IF NEW.invocation_id IS NULL THEN
   NEW.invocation_id:=NULLIF(current_setting('app.invocation_id',true),'')::uuid;
 END IF;
 IF NEW.command IS NULL THEN NEW.command:=NULLIF(current_setting('app.command',true),''); END IF;
 RETURN NEW;
END $$;

-- FUNCTION: agent_principal_guard()
CREATE FUNCTION public.agent_principal_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent authority history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF NOT public.actor_is_human() THEN
   RAISE EXCEPTION 'agent authority requires its human owner' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' AND (NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb ? 'agent:run',false)
    OR NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb @> NEW.initial_grants,false)) THEN
   RAISE EXCEPTION 'principal grants must come from its authenticated owner' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT FROM jsonb_array_elements_text(NEW.scopes) AS grants(scope) WHERE scope NOT IN
   ('task:read','job:read','card:read','card:generate','draft:read','draft:run','resource:read',
    'certificate:read','certificate:file:read','profile:read','evidence:source:read')) THEN
   RAISE EXCEPTION 'agent scope forbidden' USING ERRCODE='42501'; END IF;
 IF NEW.authority_expires_at>clock_timestamp()+interval '8 hours' THEN
   RAISE EXCEPTION 'agent authority exceeds delegation window' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.initial_grants,NEW.created_at)
    IS DISTINCT FROM (OLD.id,OLD.initial_grants,OLD.created_at)
    OR NOT OLD.scopes @> NEW.scopes OR (OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)) THEN
   RAISE EXCEPTION 'agent authority cannot expand or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: agent_task_authority(uuid)
CREATE FUNCTION public.agent_task_authority(p_task uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 RETURN FOUND;
END $$;

-- FUNCTION: append_task_event(uuid, text, jsonb, uuid)
CREATE FUNCTION public.append_task_event(p_task uuid, p_kind text, p_payload jsonb, p_source uuid DEFAULT NULL::uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE n bigint; floor_seq bigint; prune_to bigint;
BEGIN
 IF p_kind NOT IN ('board_changed','job_progress')
   OR p_payload->>'type' IS DISTINCT FROM p_kind OR octet_length(p_payload::text)>1800 THEN
  RAISE EXCEPTION 'Invalid task event metadata' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' AND (p_payload - ARRAY['type','extraction_job_id','requirement_ids','card_ids','invalidate_all']) <> '{}'::jsonb
 OR p_kind='job_progress' AND (p_payload - ARRAY['type','job_id','state','run_id','attempts','progress']) <> '{}'::jsonb
 THEN
  RAISE EXCEPTION 'Unexpected event fields' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' THEN
  IF jsonb_typeof(p_payload->'invalidate_all') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(p_payload->'requirement_ids') IS DISTINCT FROM 'array'
    OR jsonb_typeof(p_payload->'card_ids') IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_payload->'requirement_ids')+jsonb_array_length(p_payload->'card_ids') >100
    OR (p_payload->>'invalidate_all')::boolean IS DISTINCT FROM
       (jsonb_array_length(p_payload->'requirement_ids')+jsonb_array_length(p_payload->'card_ids')=0) THEN
   RAISE EXCEPTION 'Invalid board metadata' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_payload->'requirement_ids'))
   OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements_text(p_payload->'card_ids')) THEN
   RAISE EXCEPTION 'Duplicate event IDs' USING ERRCODE='23514';
  END IF;
  PERFORM value::uuid FROM jsonb_array_elements_text(p_payload->'requirement_ids');
  PERFORM value::uuid FROM jsonb_array_elements_text(p_payload->'card_ids');
  PERFORM (p_payload->>'extraction_job_id')::uuid;
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
 END IF;
 IF p_source IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs
  WHERE task_id=p_task AND id=p_source) THEN
  RAISE EXCEPTION 'Invalid task event source' USING ERRCODE='23514';
 END IF;
 IF p_kind='job_progress' AND NOT EXISTS(SELECT 1 FROM public.jobs
  WHERE task_id=p_task AND id=(p_payload->>'job_id')::uuid) THEN
  RAISE EXCEPTION 'Invalid event job binding' USING ERRCODE='23514';
 END IF;
 IF p_kind='board_changed' THEN
  IF p_payload->>'extraction_job_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs
   WHERE task_id=p_task AND kind='extract' AND id=(p_payload->>'extraction_job_id')::uuid) THEN
   RAISE EXCEPTION 'Invalid event extraction binding' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_payload->'requirement_ids') r(value)
    WHERE NOT EXISTS(SELECT 1 FROM public.requirements q WHERE q.task_id=p_task AND q.id=r.value::uuid
     AND (p_payload->>'extraction_job_id' IS NULL OR q.job_id=(p_payload->>'extraction_job_id')::uuid)))
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_payload->'card_ids') r(value)
    WHERE NOT EXISTS(SELECT 1 FROM public.response_cards c WHERE c.task_id=p_task AND c.id=r.value::uuid
     AND (p_payload->>'extraction_job_id' IS NULL OR c.extraction_job_id=(p_payload->>'extraction_job_id')::uuid)))
   THEN
   RAISE EXCEPTION 'Invalid board event parent binding' USING ERRCODE='23514';
  END IF;
 END IF;
 INSERT INTO public.task_event_heads(id,task_id,last_seq,retained_floor_seq,created_at)
 VALUES(gen_random_uuid(),p_task,0,0,clock_timestamp()) ON CONFLICT(task_id) DO NOTHING;
 UPDATE public.task_event_heads SET last_seq=last_seq+1
 WHERE task_id=p_task RETURNING last_seq,retained_floor_seq INTO n,floor_seq;
 INSERT INTO public.task_events(id,task_id,seq,event_kind,payload,source_id,created_at)
 VALUES(gen_random_uuid(),p_task,n,p_kind,p_payload,p_source,clock_timestamp());
 SELECT coalesce(max(seq),0) INTO prune_to FROM public.task_events
 WHERE task_id=p_task
 AND (seq<=n-50000 OR created_at<clock_timestamp()-interval '7 days');
 IF prune_to>floor_seq THEN
  DELETE FROM public.task_events WHERE task_id=p_task AND seq<=prune_to;
  UPDATE public.task_event_heads SET retained_floor_seq=prune_to WHERE task_id=p_task;
 END IF;
END $_$;

-- FUNCTION: certificate_file_new_revision_only()
CREATE FUNCTION public.certificate_file_new_revision_only() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE revision_xmin xid;
        BEGIN
            SELECT xmin INTO revision_xmin
            FROM public.certificate_revisions
            WHERE certificate_id = NEW.certificate_id
                AND id = NEW.certificate_revision_id;
            IF FOUND AND revision_xmin <> pg_current_xact_id()::xid THEN
                RAISE EXCEPTION 'File requires a newly created revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

-- FUNCTION: certificate_part_new_file_only()
CREATE FUNCTION public.certificate_part_new_file_only() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE file_xmin xid;
        BEGIN
            SELECT xmin INTO file_xmin
            FROM public.certificate_files
            WHERE id = NEW.certificate_file_id;
            IF FOUND AND file_xmin <> pg_current_xact_id()::xid THEN
                RAISE EXCEPTION 'Part requires a newly created certificate file'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

-- FUNCTION: job_actor_guard()
CREATE FUNCTION public.job_actor_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
        kind text:=NULLIF(current_setting('app.actor_kind',true),'');
BEGIN
  IF TG_OP='INSERT' OR (OLD.actor_kind IS NULL AND NEW.status='queued' AND kind IS NOT NULL) THEN
    -- Workers and system jobs retain their own kind without a human identity.
    IF kind IS NOT NULL THEN
      IF kind NOT IN ('session','token','agent','worker','system') OR
          (kind='session' AND NOT public.actor_is_human()) OR
          (kind='token' AND token IS NULL) OR
          (token IS NOT NULL AND NOT EXISTS(SELECT FROM public.api_tokens
            WHERE id=token AND NOT revoked AND expires_at>clock_timestamp())) OR
          (kind='agent' AND (
            NULLIF(current_setting('app.agent_principal_id',true),'') IS NULL OR
            NULLIF(current_setting('app.agent_session_id',true),'') IS NULL)) THEN
        RAISE EXCEPTION 'job actor is not active' USING ERRCODE='42501'; END IF;
      IF (NEW.actor_kind IS NOT NULL AND NEW.actor_kind IS DISTINCT FROM kind)
          OR (NEW.actor_token_id IS NOT NULL AND NEW.actor_token_id IS DISTINCT FROM token) THEN
        RAISE EXCEPTION 'job actor does not match authenticated actor' USING ERRCODE='42501'; END IF;
      NEW.actor_token_id:=token; NEW.actor_kind:=kind;
      NEW.actor_scopes:=COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb,'[]');
    ELSIF current_user='bid_app' AND (NEW.actor_token_id IS NOT NULL OR NEW.actor_kind IS NOT NULL
        OR NEW.actor_scopes<>'[]'::jsonb OR token IS NOT NULL) THEN
      RAISE EXCEPTION 'authenticated job actor required' USING ERRCODE='42501';
    END IF;
  ELSIF (NEW.actor_token_id,NEW.actor_kind,NEW.actor_scopes)
    IS DISTINCT FROM (OLD.actor_token_id,OLD.actor_kind,OLD.actor_scopes) THEN
    RAISE EXCEPTION 'job actor is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND (NEW.id,NEW.task_id,NEW.document_id,NEW.provider_config_id,NEW.provider_identity)
    IS DISTINCT FROM (OLD.id,OLD.task_id,OLD.document_id,OLD.provider_config_id,OLD.provider_identity)
    AND EXISTS(SELECT FROM public.vendor_calls WHERE job_id=OLD.id) THEN
    RAISE EXCEPTION 'job with admitted calls cannot change input binding' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;

-- FUNCTION: management_bidder_human(text)
CREATE FUNCTION public.management_bidder_human(p_kind text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE scopes jsonb;
BEGIN
 scopes:=coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb;
 IF p_kind NOT IN ('certificate','profile')
   OR NOT public.actor_is_human()
   OR NOT (scopes ? (p_kind || ':write')) OR NOT (scopes ? (p_kind || ':lifecycle')) THEN
  RAISE EXCEPTION 'human library maintenance required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: management_casefold(text)
CREATE FUNCTION public.management_casefold(p_text text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $_$
 SELECT coalesce(string_agg(coalesce($fold${"A":"a","B":"b","C":"c","D":"d","E":"e","F":"f","G":"g","H":"h","I":"i","J":"j","K":"k","L":"l","M":"m","N":"n","O":"o","P":"p","Q":"q","R":"r","S":"s","T":"t","U":"u","V":"v","W":"w","X":"x","Y":"y","Z":"z","\u00b5":"\u03bc","\u00c0":"\u00e0","\u00c1":"\u00e1","\u00c2":"\u00e2","\u00c3":"\u00e3","\u00c4":"\u00e4","\u00c5":"\u00e5","\u00c6":"\u00e6","\u00c7":"\u00e7","\u00c8":"\u00e8","\u00c9":"\u00e9","\u00ca":"\u00ea","\u00cb":"\u00eb","\u00cc":"\u00ec","\u00cd":"\u00ed","\u00ce":"\u00ee","\u00cf":"\u00ef","\u00d0":"\u00f0","\u00d1":"\u00f1","\u00d2":"\u00f2","\u00d3":"\u00f3","\u00d4":"\u00f4","\u00d5":"\u00f5","\u00d6":"\u00f6","\u00d8":"\u00f8","\u00d9":"\u00f9","\u00da":"\u00fa","\u00db":"\u00fb","\u00dc":"\u00fc","\u00dd":"\u00fd","\u00de":"\u00fe","\u00df":"ss","\u0100":"\u0101","\u0102":"\u0103","\u0104":"\u0105","\u0106":"\u0107","\u0108":"\u0109","\u010a":"\u010b","\u010c":"\u010d","\u010e":"\u010f","\u0110":"\u0111","\u0112":"\u0113","\u0114":"\u0115","\u0116":"\u0117","\u0118":"\u0119","\u011a":"\u011b","\u011c":"\u011d","\u011e":"\u011f","\u0120":"\u0121","\u0122":"\u0123","\u0124":"\u0125","\u0126":"\u0127","\u0128":"\u0129","\u012a":"\u012b","\u012c":"\u012d","\u012e":"\u012f","\u0130":"i\u0307","\u0132":"\u0133","\u0134":"\u0135","\u0136":"\u0137","\u0139":"\u013a","\u013b":"\u013c","\u013d":"\u013e","\u013f":"\u0140","\u0141":"\u0142","\u0143":"\u0144","\u0145":"\u0146","\u0147":"\u0148","\u0149":"\u02bcn","\u014a":"\u014b","\u014c":"\u014d","\u014e":"\u014f","\u0150":"\u0151","\u0152":"\u0153","\u0154":"\u0155","\u0156":"\u0157","\u0158":"\u0159","\u015a":"\u015b","\u015c":"\u015d","\u015e":"\u015f","\u0160":"\u0161","\u0162":"\u0163","\u0164":"\u0165","\u0166":"\u0167","\u0168":"\u0169","\u016a":"\u016b","\u016c":"\u016d","\u016e":"\u016f","\u0170":"\u0171","\u0172":"\u0173","\u0174":"\u0175","\u0176":"\u0177","\u0178":"\u00ff","\u0179":"\u017a","\u017b":"\u017c","\u017d":"\u017e","\u017f":"s","\u0181":"\u0253","\u0182":"\u0183","\u0184":"\u0185","\u0186":"\u0254","\u0187":"\u0188","\u0189":"\u0256","\u018a":"\u0257","\u018b":"\u018c","\u018e":"\u01dd","\u018f":"\u0259","\u0190":"\u025b","\u0191":"\u0192","\u0193":"\u0260","\u0194":"\u0263","\u0196":"\u0269","\u0197":"\u0268","\u0198":"\u0199","\u019c":"\u026f","\u019d":"\u0272","\u019f":"\u0275","\u01a0":"\u01a1","\u01a2":"\u01a3","\u01a4":"\u01a5","\u01a6":"\u0280","\u01a7":"\u01a8","\u01a9":"\u0283","\u01ac":"\u01ad","\u01ae":"\u0288","\u01af":"\u01b0","\u01b1":"\u028a","\u01b2":"\u028b","\u01b3":"\u01b4","\u01b5":"\u01b6","\u01b7":"\u0292","\u01b8":"\u01b9","\u01bc":"\u01bd","\u01c4":"\u01c6","\u01c5":"\u01c6","\u01c7":"\u01c9","\u01c8":"\u01c9","\u01ca":"\u01cc","\u01cb":"\u01cc","\u01cd":"\u01ce","\u01cf":"\u01d0","\u01d1":"\u01d2","\u01d3":"\u01d4","\u01d5":"\u01d6","\u01d7":"\u01d8","\u01d9":"\u01da","\u01db":"\u01dc","\u01de":"\u01df","\u01e0":"\u01e1","\u01e2":"\u01e3","\u01e4":"\u01e5","\u01e6":"\u01e7","\u01e8":"\u01e9","\u01ea":"\u01eb","\u01ec":"\u01ed","\u01ee":"\u01ef","\u01f0":"j\u030c","\u01f1":"\u01f3","\u01f2":"\u01f3","\u01f4":"\u01f5","\u01f6":"\u0195","\u01f7":"\u01bf","\u01f8":"\u01f9","\u01fa":"\u01fb","\u01fc":"\u01fd","\u01fe":"\u01ff","\u0200":"\u0201","\u0202":"\u0203","\u0204":"\u0205","\u0206":"\u0207","\u0208":"\u0209","\u020a":"\u020b","\u020c":"\u020d","\u020e":"\u020f","\u0210":"\u0211","\u0212":"\u0213","\u0214":"\u0215","\u0216":"\u0217","\u0218":"\u0219","\u021a":"\u021b","\u021c":"\u021d","\u021e":"\u021f","\u0220":"\u019e","\u0222":"\u0223","\u0224":"\u0225","\u0226":"\u0227","\u0228":"\u0229","\u022a":"\u022b","\u022c":"\u022d","\u022e":"\u022f","\u0230":"\u0231","\u0232":"\u0233","\u023a":"\u2c65","\u023b":"\u023c","\u023d":"\u019a","\u023e":"\u2c66","\u0241":"\u0242","\u0243":"\u0180","\u0244":"\u0289","\u0245":"\u028c","\u0246":"\u0247","\u0248":"\u0249","\u024a":"\u024b","\u024c":"\u024d","\u024e":"\u024f","\u0345":"\u03b9","\u0370":"\u0371","\u0372":"\u0373","\u0376":"\u0377","\u037f":"\u03f3","\u0386":"\u03ac","\u0388":"\u03ad","\u0389":"\u03ae","\u038a":"\u03af","\u038c":"\u03cc","\u038e":"\u03cd","\u038f":"\u03ce","\u0390":"\u03b9\u0308\u0301","\u0391":"\u03b1","\u0392":"\u03b2","\u0393":"\u03b3","\u0394":"\u03b4","\u0395":"\u03b5","\u0396":"\u03b6","\u0397":"\u03b7","\u0398":"\u03b8","\u0399":"\u03b9","\u039a":"\u03ba","\u039b":"\u03bb","\u039c":"\u03bc","\u039d":"\u03bd","\u039e":"\u03be","\u039f":"\u03bf","\u03a0":"\u03c0","\u03a1":"\u03c1","\u03a3":"\u03c3","\u03a4":"\u03c4","\u03a5":"\u03c5","\u03a6":"\u03c6","\u03a7":"\u03c7","\u03a8":"\u03c8","\u03a9":"\u03c9","\u03aa":"\u03ca","\u03ab":"\u03cb","\u03b0":"\u03c5\u0308\u0301","\u03c2":"\u03c3","\u03cf":"\u03d7","\u03d0":"\u03b2","\u03d1":"\u03b8","\u03d5":"\u03c6","\u03d6":"\u03c0","\u03d8":"\u03d9","\u03da":"\u03db","\u03dc":"\u03dd","\u03de":"\u03df","\u03e0":"\u03e1","\u03e2":"\u03e3","\u03e4":"\u03e5","\u03e6":"\u03e7","\u03e8":"\u03e9","\u03ea":"\u03eb","\u03ec":"\u03ed","\u03ee":"\u03ef","\u03f0":"\u03ba","\u03f1":"\u03c1","\u03f4":"\u03b8","\u03f5":"\u03b5","\u03f7":"\u03f8","\u03f9":"\u03f2","\u03fa":"\u03fb","\u03fd":"\u037b","\u03fe":"\u037c","\u03ff":"\u037d","\u0400":"\u0450","\u0401":"\u0451","\u0402":"\u0452","\u0403":"\u0453","\u0404":"\u0454","\u0405":"\u0455","\u0406":"\u0456","\u0407":"\u0457","\u0408":"\u0458","\u0409":"\u0459","\u040a":"\u045a","\u040b":"\u045b","\u040c":"\u045c","\u040d":"\u045d","\u040e":"\u045e","\u040f":"\u045f","\u0410":"\u0430","\u0411":"\u0431","\u0412":"\u0432","\u0413":"\u0433","\u0414":"\u0434","\u0415":"\u0435","\u0416":"\u0436","\u0417":"\u0437","\u0418":"\u0438","\u0419":"\u0439","\u041a":"\u043a","\u041b":"\u043b","\u041c":"\u043c","\u041d":"\u043d","\u041e":"\u043e","\u041f":"\u043f","\u0420":"\u0440","\u0421":"\u0441","\u0422":"\u0442","\u0423":"\u0443","\u0424":"\u0444","\u0425":"\u0445","\u0426":"\u0446","\u0427":"\u0447","\u0428":"\u0448","\u0429":"\u0449","\u042a":"\u044a","\u042b":"\u044b","\u042c":"\u044c","\u042d":"\u044d","\u042e":"\u044e","\u042f":"\u044f","\u0460":"\u0461","\u0462":"\u0463","\u0464":"\u0465","\u0466":"\u0467","\u0468":"\u0469","\u046a":"\u046b","\u046c":"\u046d","\u046e":"\u046f","\u0470":"\u0471","\u0472":"\u0473","\u0474":"\u0475","\u0476":"\u0477","\u0478":"\u0479","\u047a":"\u047b","\u047c":"\u047d","\u047e":"\u047f","\u0480":"\u0481","\u048a":"\u048b","\u048c":"\u048d","\u048e":"\u048f","\u0490":"\u0491","\u0492":"\u0493","\u0494":"\u0495","\u0496":"\u0497","\u0498":"\u0499","\u049a":"\u049b","\u049c":"\u049d","\u049e":"\u049f","\u04a0":"\u04a1","\u04a2":"\u04a3","\u04a4":"\u04a5","\u04a6":"\u04a7","\u04a8":"\u04a9","\u04aa":"\u04ab","\u04ac":"\u04ad","\u04ae":"\u04af","\u04b0":"\u04b1","\u04b2":"\u04b3","\u04b4":"\u04b5","\u04b6":"\u04b7","\u04b8":"\u04b9","\u04ba":"\u04bb","\u04bc":"\u04bd","\u04be":"\u04bf","\u04c0":"\u04cf","\u04c1":"\u04c2","\u04c3":"\u04c4","\u04c5":"\u04c6","\u04c7":"\u04c8","\u04c9":"\u04ca","\u04cb":"\u04cc","\u04cd":"\u04ce","\u04d0":"\u04d1","\u04d2":"\u04d3","\u04d4":"\u04d5","\u04d6":"\u04d7","\u04d8":"\u04d9","\u04da":"\u04db","\u04dc":"\u04dd","\u04de":"\u04df","\u04e0":"\u04e1","\u04e2":"\u04e3","\u04e4":"\u04e5","\u04e6":"\u04e7","\u04e8":"\u04e9","\u04ea":"\u04eb","\u04ec":"\u04ed","\u04ee":"\u04ef","\u04f0":"\u04f1","\u04f2":"\u04f3","\u04f4":"\u04f5","\u04f6":"\u04f7","\u04f8":"\u04f9","\u04fa":"\u04fb","\u04fc":"\u04fd","\u04fe":"\u04ff","\u0500":"\u0501","\u0502":"\u0503","\u0504":"\u0505","\u0506":"\u0507","\u0508":"\u0509","\u050a":"\u050b","\u050c":"\u050d","\u050e":"\u050f","\u0510":"\u0511","\u0512":"\u0513","\u0514":"\u0515","\u0516":"\u0517","\u0518":"\u0519","\u051a":"\u051b","\u051c":"\u051d","\u051e":"\u051f","\u0520":"\u0521","\u0522":"\u0523","\u0524":"\u0525","\u0526":"\u0527","\u0528":"\u0529","\u052a":"\u052b","\u052c":"\u052d","\u052e":"\u052f","\u0531":"\u0561","\u0532":"\u0562","\u0533":"\u0563","\u0534":"\u0564","\u0535":"\u0565","\u0536":"\u0566","\u0537":"\u0567","\u0538":"\u0568","\u0539":"\u0569","\u053a":"\u056a","\u053b":"\u056b","\u053c":"\u056c","\u053d":"\u056d","\u053e":"\u056e","\u053f":"\u056f","\u0540":"\u0570","\u0541":"\u0571","\u0542":"\u0572","\u0543":"\u0573","\u0544":"\u0574","\u0545":"\u0575","\u0546":"\u0576","\u0547":"\u0577","\u0548":"\u0578","\u0549":"\u0579","\u054a":"\u057a","\u054b":"\u057b","\u054c":"\u057c","\u054d":"\u057d","\u054e":"\u057e","\u054f":"\u057f","\u0550":"\u0580","\u0551":"\u0581","\u0552":"\u0582","\u0553":"\u0583","\u0554":"\u0584","\u0555":"\u0585","\u0556":"\u0586","\u0587":"\u0565\u0582","\u10a0":"\u2d00","\u10a1":"\u2d01","\u10a2":"\u2d02","\u10a3":"\u2d03","\u10a4":"\u2d04","\u10a5":"\u2d05","\u10a6":"\u2d06","\u10a7":"\u2d07","\u10a8":"\u2d08","\u10a9":"\u2d09","\u10aa":"\u2d0a","\u10ab":"\u2d0b","\u10ac":"\u2d0c","\u10ad":"\u2d0d","\u10ae":"\u2d0e","\u10af":"\u2d0f","\u10b0":"\u2d10","\u10b1":"\u2d11","\u10b2":"\u2d12","\u10b3":"\u2d13","\u10b4":"\u2d14","\u10b5":"\u2d15","\u10b6":"\u2d16","\u10b7":"\u2d17","\u10b8":"\u2d18","\u10b9":"\u2d19","\u10ba":"\u2d1a","\u10bb":"\u2d1b","\u10bc":"\u2d1c","\u10bd":"\u2d1d","\u10be":"\u2d1e","\u10bf":"\u2d1f","\u10c0":"\u2d20","\u10c1":"\u2d21","\u10c2":"\u2d22","\u10c3":"\u2d23","\u10c4":"\u2d24","\u10c5":"\u2d25","\u10c7":"\u2d27","\u10cd":"\u2d2d","\u13f8":"\u13f0","\u13f9":"\u13f1","\u13fa":"\u13f2","\u13fb":"\u13f3","\u13fc":"\u13f4","\u13fd":"\u13f5","\u1c80":"\u0432","\u1c81":"\u0434","\u1c82":"\u043e","\u1c83":"\u0441","\u1c84":"\u0442","\u1c85":"\u0442","\u1c86":"\u044a","\u1c87":"\u0463","\u1c88":"\ua64b","\u1c90":"\u10d0","\u1c91":"\u10d1","\u1c92":"\u10d2","\u1c93":"\u10d3","\u1c94":"\u10d4","\u1c95":"\u10d5","\u1c96":"\u10d6","\u1c97":"\u10d7","\u1c98":"\u10d8","\u1c99":"\u10d9","\u1c9a":"\u10da","\u1c9b":"\u10db","\u1c9c":"\u10dc","\u1c9d":"\u10dd","\u1c9e":"\u10de","\u1c9f":"\u10df","\u1ca0":"\u10e0","\u1ca1":"\u10e1","\u1ca2":"\u10e2","\u1ca3":"\u10e3","\u1ca4":"\u10e4","\u1ca5":"\u10e5","\u1ca6":"\u10e6","\u1ca7":"\u10e7","\u1ca8":"\u10e8","\u1ca9":"\u10e9","\u1caa":"\u10ea","\u1cab":"\u10eb","\u1cac":"\u10ec","\u1cad":"\u10ed","\u1cae":"\u10ee","\u1caf":"\u10ef","\u1cb0":"\u10f0","\u1cb1":"\u10f1","\u1cb2":"\u10f2","\u1cb3":"\u10f3","\u1cb4":"\u10f4","\u1cb5":"\u10f5","\u1cb6":"\u10f6","\u1cb7":"\u10f7","\u1cb8":"\u10f8","\u1cb9":"\u10f9","\u1cba":"\u10fa","\u1cbd":"\u10fd","\u1cbe":"\u10fe","\u1cbf":"\u10ff","\u1e00":"\u1e01","\u1e02":"\u1e03","\u1e04":"\u1e05","\u1e06":"\u1e07","\u1e08":"\u1e09","\u1e0a":"\u1e0b","\u1e0c":"\u1e0d","\u1e0e":"\u1e0f","\u1e10":"\u1e11","\u1e12":"\u1e13","\u1e14":"\u1e15","\u1e16":"\u1e17","\u1e18":"\u1e19","\u1e1a":"\u1e1b","\u1e1c":"\u1e1d","\u1e1e":"\u1e1f","\u1e20":"\u1e21","\u1e22":"\u1e23","\u1e24":"\u1e25","\u1e26":"\u1e27","\u1e28":"\u1e29","\u1e2a":"\u1e2b","\u1e2c":"\u1e2d","\u1e2e":"\u1e2f","\u1e30":"\u1e31","\u1e32":"\u1e33","\u1e34":"\u1e35","\u1e36":"\u1e37","\u1e38":"\u1e39","\u1e3a":"\u1e3b","\u1e3c":"\u1e3d","\u1e3e":"\u1e3f","\u1e40":"\u1e41","\u1e42":"\u1e43","\u1e44":"\u1e45","\u1e46":"\u1e47","\u1e48":"\u1e49","\u1e4a":"\u1e4b","\u1e4c":"\u1e4d","\u1e4e":"\u1e4f","\u1e50":"\u1e51","\u1e52":"\u1e53","\u1e54":"\u1e55","\u1e56":"\u1e57","\u1e58":"\u1e59","\u1e5a":"\u1e5b","\u1e5c":"\u1e5d","\u1e5e":"\u1e5f","\u1e60":"\u1e61","\u1e62":"\u1e63","\u1e64":"\u1e65","\u1e66":"\u1e67","\u1e68":"\u1e69","\u1e6a":"\u1e6b","\u1e6c":"\u1e6d","\u1e6e":"\u1e6f","\u1e70":"\u1e71","\u1e72":"\u1e73","\u1e74":"\u1e75","\u1e76":"\u1e77","\u1e78":"\u1e79","\u1e7a":"\u1e7b","\u1e7c":"\u1e7d","\u1e7e":"\u1e7f","\u1e80":"\u1e81","\u1e82":"\u1e83","\u1e84":"\u1e85","\u1e86":"\u1e87","\u1e88":"\u1e89","\u1e8a":"\u1e8b","\u1e8c":"\u1e8d","\u1e8e":"\u1e8f","\u1e90":"\u1e91","\u1e92":"\u1e93","\u1e94":"\u1e95","\u1e96":"h\u0331","\u1e97":"t\u0308","\u1e98":"w\u030a","\u1e99":"y\u030a","\u1e9a":"a\u02be","\u1e9b":"\u1e61","\u1e9e":"ss","\u1ea0":"\u1ea1","\u1ea2":"\u1ea3","\u1ea4":"\u1ea5","\u1ea6":"\u1ea7","\u1ea8":"\u1ea9","\u1eaa":"\u1eab","\u1eac":"\u1ead","\u1eae":"\u1eaf","\u1eb0":"\u1eb1","\u1eb2":"\u1eb3","\u1eb4":"\u1eb5","\u1eb6":"\u1eb7","\u1eb8":"\u1eb9","\u1eba":"\u1ebb","\u1ebc":"\u1ebd","\u1ebe":"\u1ebf","\u1ec0":"\u1ec1","\u1ec2":"\u1ec3","\u1ec4":"\u1ec5","\u1ec6":"\u1ec7","\u1ec8":"\u1ec9","\u1eca":"\u1ecb","\u1ecc":"\u1ecd","\u1ece":"\u1ecf","\u1ed0":"\u1ed1","\u1ed2":"\u1ed3","\u1ed4":"\u1ed5","\u1ed6":"\u1ed7","\u1ed8":"\u1ed9","\u1eda":"\u1edb","\u1edc":"\u1edd","\u1ede":"\u1edf","\u1ee0":"\u1ee1","\u1ee2":"\u1ee3","\u1ee4":"\u1ee5","\u1ee6":"\u1ee7","\u1ee8":"\u1ee9","\u1eea":"\u1eeb","\u1eec":"\u1eed","\u1eee":"\u1eef","\u1ef0":"\u1ef1","\u1ef2":"\u1ef3","\u1ef4":"\u1ef5","\u1ef6":"\u1ef7","\u1ef8":"\u1ef9","\u1efa":"\u1efb","\u1efc":"\u1efd","\u1efe":"\u1eff","\u1f08":"\u1f00","\u1f09":"\u1f01","\u1f0a":"\u1f02","\u1f0b":"\u1f03","\u1f0c":"\u1f04","\u1f0d":"\u1f05","\u1f0e":"\u1f06","\u1f0f":"\u1f07","\u1f18":"\u1f10","\u1f19":"\u1f11","\u1f1a":"\u1f12","\u1f1b":"\u1f13","\u1f1c":"\u1f14","\u1f1d":"\u1f15","\u1f28":"\u1f20","\u1f29":"\u1f21","\u1f2a":"\u1f22","\u1f2b":"\u1f23","\u1f2c":"\u1f24","\u1f2d":"\u1f25","\u1f2e":"\u1f26","\u1f2f":"\u1f27","\u1f38":"\u1f30","\u1f39":"\u1f31","\u1f3a":"\u1f32","\u1f3b":"\u1f33","\u1f3c":"\u1f34","\u1f3d":"\u1f35","\u1f3e":"\u1f36","\u1f3f":"\u1f37","\u1f48":"\u1f40","\u1f49":"\u1f41","\u1f4a":"\u1f42","\u1f4b":"\u1f43","\u1f4c":"\u1f44","\u1f4d":"\u1f45","\u1f50":"\u03c5\u0313","\u1f52":"\u03c5\u0313\u0300","\u1f54":"\u03c5\u0313\u0301","\u1f56":"\u03c5\u0313\u0342","\u1f59":"\u1f51","\u1f5b":"\u1f53","\u1f5d":"\u1f55","\u1f5f":"\u1f57","\u1f68":"\u1f60","\u1f69":"\u1f61","\u1f6a":"\u1f62","\u1f6b":"\u1f63","\u1f6c":"\u1f64","\u1f6d":"\u1f65","\u1f6e":"\u1f66","\u1f6f":"\u1f67","\u1f80":"\u1f00\u03b9","\u1f81":"\u1f01\u03b9","\u1f82":"\u1f02\u03b9","\u1f83":"\u1f03\u03b9","\u1f84":"\u1f04\u03b9","\u1f85":"\u1f05\u03b9","\u1f86":"\u1f06\u03b9","\u1f87":"\u1f07\u03b9","\u1f88":"\u1f00\u03b9","\u1f89":"\u1f01\u03b9","\u1f8a":"\u1f02\u03b9","\u1f8b":"\u1f03\u03b9","\u1f8c":"\u1f04\u03b9","\u1f8d":"\u1f05\u03b9","\u1f8e":"\u1f06\u03b9","\u1f8f":"\u1f07\u03b9","\u1f90":"\u1f20\u03b9","\u1f91":"\u1f21\u03b9","\u1f92":"\u1f22\u03b9","\u1f93":"\u1f23\u03b9","\u1f94":"\u1f24\u03b9","\u1f95":"\u1f25\u03b9","\u1f96":"\u1f26\u03b9","\u1f97":"\u1f27\u03b9","\u1f98":"\u1f20\u03b9","\u1f99":"\u1f21\u03b9","\u1f9a":"\u1f22\u03b9","\u1f9b":"\u1f23\u03b9","\u1f9c":"\u1f24\u03b9","\u1f9d":"\u1f25\u03b9","\u1f9e":"\u1f26\u03b9","\u1f9f":"\u1f27\u03b9","\u1fa0":"\u1f60\u03b9","\u1fa1":"\u1f61\u03b9","\u1fa2":"\u1f62\u03b9","\u1fa3":"\u1f63\u03b9","\u1fa4":"\u1f64\u03b9","\u1fa5":"\u1f65\u03b9","\u1fa6":"\u1f66\u03b9","\u1fa7":"\u1f67\u03b9","\u1fa8":"\u1f60\u03b9","\u1fa9":"\u1f61\u03b9","\u1faa":"\u1f62\u03b9","\u1fab":"\u1f63\u03b9","\u1fac":"\u1f64\u03b9","\u1fad":"\u1f65\u03b9","\u1fae":"\u1f66\u03b9","\u1faf":"\u1f67\u03b9","\u1fb2":"\u1f70\u03b9","\u1fb3":"\u03b1\u03b9","\u1fb4":"\u03ac\u03b9","\u1fb6":"\u03b1\u0342","\u1fb7":"\u03b1\u0342\u03b9","\u1fb8":"\u1fb0","\u1fb9":"\u1fb1","\u1fba":"\u1f70","\u1fbb":"\u1f71","\u1fbc":"\u03b1\u03b9","\u1fbe":"\u03b9","\u1fc2":"\u1f74\u03b9","\u1fc3":"\u03b7\u03b9","\u1fc4":"\u03ae\u03b9","\u1fc6":"\u03b7\u0342","\u1fc7":"\u03b7\u0342\u03b9","\u1fc8":"\u1f72","\u1fc9":"\u1f73","\u1fca":"\u1f74","\u1fcb":"\u1f75","\u1fcc":"\u03b7\u03b9","\u1fd2":"\u03b9\u0308\u0300","\u1fd3":"\u03b9\u0308\u0301","\u1fd6":"\u03b9\u0342","\u1fd7":"\u03b9\u0308\u0342","\u1fd8":"\u1fd0","\u1fd9":"\u1fd1","\u1fda":"\u1f76","\u1fdb":"\u1f77","\u1fe2":"\u03c5\u0308\u0300","\u1fe3":"\u03c5\u0308\u0301","\u1fe4":"\u03c1\u0313","\u1fe6":"\u03c5\u0342","\u1fe7":"\u03c5\u0308\u0342","\u1fe8":"\u1fe0","\u1fe9":"\u1fe1","\u1fea":"\u1f7a","\u1feb":"\u1f7b","\u1fec":"\u1fe5","\u1ff2":"\u1f7c\u03b9","\u1ff3":"\u03c9\u03b9","\u1ff4":"\u03ce\u03b9","\u1ff6":"\u03c9\u0342","\u1ff7":"\u03c9\u0342\u03b9","\u1ff8":"\u1f78","\u1ff9":"\u1f79","\u1ffa":"\u1f7c","\u1ffb":"\u1f7d","\u1ffc":"\u03c9\u03b9","\u2126":"\u03c9","\u212a":"k","\u212b":"\u00e5","\u2132":"\u214e","\u2160":"\u2170","\u2161":"\u2171","\u2162":"\u2172","\u2163":"\u2173","\u2164":"\u2174","\u2165":"\u2175","\u2166":"\u2176","\u2167":"\u2177","\u2168":"\u2178","\u2169":"\u2179","\u216a":"\u217a","\u216b":"\u217b","\u216c":"\u217c","\u216d":"\u217d","\u216e":"\u217e","\u216f":"\u217f","\u2183":"\u2184","\u24b6":"\u24d0","\u24b7":"\u24d1","\u24b8":"\u24d2","\u24b9":"\u24d3","\u24ba":"\u24d4","\u24bb":"\u24d5","\u24bc":"\u24d6","\u24bd":"\u24d7","\u24be":"\u24d8","\u24bf":"\u24d9","\u24c0":"\u24da","\u24c1":"\u24db","\u24c2":"\u24dc","\u24c3":"\u24dd","\u24c4":"\u24de","\u24c5":"\u24df","\u24c6":"\u24e0","\u24c7":"\u24e1","\u24c8":"\u24e2","\u24c9":"\u24e3","\u24ca":"\u24e4","\u24cb":"\u24e5","\u24cc":"\u24e6","\u24cd":"\u24e7","\u24ce":"\u24e8","\u24cf":"\u24e9","\u2c00":"\u2c30","\u2c01":"\u2c31","\u2c02":"\u2c32","\u2c03":"\u2c33","\u2c04":"\u2c34","\u2c05":"\u2c35","\u2c06":"\u2c36","\u2c07":"\u2c37","\u2c08":"\u2c38","\u2c09":"\u2c39","\u2c0a":"\u2c3a","\u2c0b":"\u2c3b","\u2c0c":"\u2c3c","\u2c0d":"\u2c3d","\u2c0e":"\u2c3e","\u2c0f":"\u2c3f","\u2c10":"\u2c40","\u2c11":"\u2c41","\u2c12":"\u2c42","\u2c13":"\u2c43","\u2c14":"\u2c44","\u2c15":"\u2c45","\u2c16":"\u2c46","\u2c17":"\u2c47","\u2c18":"\u2c48","\u2c19":"\u2c49","\u2c1a":"\u2c4a","\u2c1b":"\u2c4b","\u2c1c":"\u2c4c","\u2c1d":"\u2c4d","\u2c1e":"\u2c4e","\u2c1f":"\u2c4f","\u2c20":"\u2c50","\u2c21":"\u2c51","\u2c22":"\u2c52","\u2c23":"\u2c53","\u2c24":"\u2c54","\u2c25":"\u2c55","\u2c26":"\u2c56","\u2c27":"\u2c57","\u2c28":"\u2c58","\u2c29":"\u2c59","\u2c2a":"\u2c5a","\u2c2b":"\u2c5b","\u2c2c":"\u2c5c","\u2c2d":"\u2c5d","\u2c2e":"\u2c5e","\u2c2f":"\u2c5f","\u2c60":"\u2c61","\u2c62":"\u026b","\u2c63":"\u1d7d","\u2c64":"\u027d","\u2c67":"\u2c68","\u2c69":"\u2c6a","\u2c6b":"\u2c6c","\u2c6d":"\u0251","\u2c6e":"\u0271","\u2c6f":"\u0250","\u2c70":"\u0252","\u2c72":"\u2c73","\u2c75":"\u2c76","\u2c7e":"\u023f","\u2c7f":"\u0240","\u2c80":"\u2c81","\u2c82":"\u2c83","\u2c84":"\u2c85","\u2c86":"\u2c87","\u2c88":"\u2c89","\u2c8a":"\u2c8b","\u2c8c":"\u2c8d","\u2c8e":"\u2c8f","\u2c90":"\u2c91","\u2c92":"\u2c93","\u2c94":"\u2c95","\u2c96":"\u2c97","\u2c98":"\u2c99","\u2c9a":"\u2c9b","\u2c9c":"\u2c9d","\u2c9e":"\u2c9f","\u2ca0":"\u2ca1","\u2ca2":"\u2ca3","\u2ca4":"\u2ca5","\u2ca6":"\u2ca7","\u2ca8":"\u2ca9","\u2caa":"\u2cab","\u2cac":"\u2cad","\u2cae":"\u2caf","\u2cb0":"\u2cb1","\u2cb2":"\u2cb3","\u2cb4":"\u2cb5","\u2cb6":"\u2cb7","\u2cb8":"\u2cb9","\u2cba":"\u2cbb","\u2cbc":"\u2cbd","\u2cbe":"\u2cbf","\u2cc0":"\u2cc1","\u2cc2":"\u2cc3","\u2cc4":"\u2cc5","\u2cc6":"\u2cc7","\u2cc8":"\u2cc9","\u2cca":"\u2ccb","\u2ccc":"\u2ccd","\u2cce":"\u2ccf","\u2cd0":"\u2cd1","\u2cd2":"\u2cd3","\u2cd4":"\u2cd5","\u2cd6":"\u2cd7","\u2cd8":"\u2cd9","\u2cda":"\u2cdb","\u2cdc":"\u2cdd","\u2cde":"\u2cdf","\u2ce0":"\u2ce1","\u2ce2":"\u2ce3","\u2ceb":"\u2cec","\u2ced":"\u2cee","\u2cf2":"\u2cf3","\ua640":"\ua641","\ua642":"\ua643","\ua644":"\ua645","\ua646":"\ua647","\ua648":"\ua649","\ua64a":"\ua64b","\ua64c":"\ua64d","\ua64e":"\ua64f","\ua650":"\ua651","\ua652":"\ua653","\ua654":"\ua655","\ua656":"\ua657","\ua658":"\ua659","\ua65a":"\ua65b","\ua65c":"\ua65d","\ua65e":"\ua65f","\ua660":"\ua661","\ua662":"\ua663","\ua664":"\ua665","\ua666":"\ua667","\ua668":"\ua669","\ua66a":"\ua66b","\ua66c":"\ua66d","\ua680":"\ua681","\ua682":"\ua683","\ua684":"\ua685","\ua686":"\ua687","\ua688":"\ua689","\ua68a":"\ua68b","\ua68c":"\ua68d","\ua68e":"\ua68f","\ua690":"\ua691","\ua692":"\ua693","\ua694":"\ua695","\ua696":"\ua697","\ua698":"\ua699","\ua69a":"\ua69b","\ua722":"\ua723","\ua724":"\ua725","\ua726":"\ua727","\ua728":"\ua729","\ua72a":"\ua72b","\ua72c":"\ua72d","\ua72e":"\ua72f","\ua732":"\ua733","\ua734":"\ua735","\ua736":"\ua737","\ua738":"\ua739","\ua73a":"\ua73b","\ua73c":"\ua73d","\ua73e":"\ua73f","\ua740":"\ua741","\ua742":"\ua743","\ua744":"\ua745","\ua746":"\ua747","\ua748":"\ua749","\ua74a":"\ua74b","\ua74c":"\ua74d","\ua74e":"\ua74f","\ua750":"\ua751","\ua752":"\ua753","\ua754":"\ua755","\ua756":"\ua757","\ua758":"\ua759","\ua75a":"\ua75b","\ua75c":"\ua75d","\ua75e":"\ua75f","\ua760":"\ua761","\ua762":"\ua763","\ua764":"\ua765","\ua766":"\ua767","\ua768":"\ua769","\ua76a":"\ua76b","\ua76c":"\ua76d","\ua76e":"\ua76f","\ua779":"\ua77a","\ua77b":"\ua77c","\ua77d":"\u1d79","\ua77e":"\ua77f","\ua780":"\ua781","\ua782":"\ua783","\ua784":"\ua785","\ua786":"\ua787","\ua78b":"\ua78c","\ua78d":"\u0265","\ua790":"\ua791","\ua792":"\ua793","\ua796":"\ua797","\ua798":"\ua799","\ua79a":"\ua79b","\ua79c":"\ua79d","\ua79e":"\ua79f","\ua7a0":"\ua7a1","\ua7a2":"\ua7a3","\ua7a4":"\ua7a5","\ua7a6":"\ua7a7","\ua7a8":"\ua7a9","\ua7aa":"\u0266","\ua7ab":"\u025c","\ua7ac":"\u0261","\ua7ad":"\u026c","\ua7ae":"\u026a","\ua7b0":"\u029e","\ua7b1":"\u0287","\ua7b2":"\u029d","\ua7b3":"\uab53","\ua7b4":"\ua7b5","\ua7b6":"\ua7b7","\ua7b8":"\ua7b9","\ua7ba":"\ua7bb","\ua7bc":"\ua7bd","\ua7be":"\ua7bf","\ua7c0":"\ua7c1","\ua7c2":"\ua7c3","\ua7c4":"\ua794","\ua7c5":"\u0282","\ua7c6":"\u1d8e","\ua7c7":"\ua7c8","\ua7c9":"\ua7ca","\ua7d0":"\ua7d1","\ua7d6":"\ua7d7","\ua7d8":"\ua7d9","\ua7f5":"\ua7f6","\uab70":"\u13a0","\uab71":"\u13a1","\uab72":"\u13a2","\uab73":"\u13a3","\uab74":"\u13a4","\uab75":"\u13a5","\uab76":"\u13a6","\uab77":"\u13a7","\uab78":"\u13a8","\uab79":"\u13a9","\uab7a":"\u13aa","\uab7b":"\u13ab","\uab7c":"\u13ac","\uab7d":"\u13ad","\uab7e":"\u13ae","\uab7f":"\u13af","\uab80":"\u13b0","\uab81":"\u13b1","\uab82":"\u13b2","\uab83":"\u13b3","\uab84":"\u13b4","\uab85":"\u13b5","\uab86":"\u13b6","\uab87":"\u13b7","\uab88":"\u13b8","\uab89":"\u13b9","\uab8a":"\u13ba","\uab8b":"\u13bb","\uab8c":"\u13bc","\uab8d":"\u13bd","\uab8e":"\u13be","\uab8f":"\u13bf","\uab90":"\u13c0","\uab91":"\u13c1","\uab92":"\u13c2","\uab93":"\u13c3","\uab94":"\u13c4","\uab95":"\u13c5","\uab96":"\u13c6","\uab97":"\u13c7","\uab98":"\u13c8","\uab99":"\u13c9","\uab9a":"\u13ca","\uab9b":"\u13cb","\uab9c":"\u13cc","\uab9d":"\u13cd","\uab9e":"\u13ce","\uab9f":"\u13cf","\uaba0":"\u13d0","\uaba1":"\u13d1","\uaba2":"\u13d2","\uaba3":"\u13d3","\uaba4":"\u13d4","\uaba5":"\u13d5","\uaba6":"\u13d6","\uaba7":"\u13d7","\uaba8":"\u13d8","\uaba9":"\u13d9","\uabaa":"\u13da","\uabab":"\u13db","\uabac":"\u13dc","\uabad":"\u13dd","\uabae":"\u13de","\uabaf":"\u13df","\uabb0":"\u13e0","\uabb1":"\u13e1","\uabb2":"\u13e2","\uabb3":"\u13e3","\uabb4":"\u13e4","\uabb5":"\u13e5","\uabb6":"\u13e6","\uabb7":"\u13e7","\uabb8":"\u13e8","\uabb9":"\u13e9","\uabba":"\u13ea","\uabbb":"\u13eb","\uabbc":"\u13ec","\uabbd":"\u13ed","\uabbe":"\u13ee","\uabbf":"\u13ef","\ufb00":"ff","\ufb01":"fi","\ufb02":"fl","\ufb03":"ffi","\ufb04":"ffl","\ufb05":"st","\ufb06":"st","\ufb13":"\u0574\u0576","\ufb14":"\u0574\u0565","\ufb15":"\u0574\u056b","\ufb16":"\u057e\u0576","\ufb17":"\u0574\u056d","\uff21":"\uff41","\uff22":"\uff42","\uff23":"\uff43","\uff24":"\uff44","\uff25":"\uff45","\uff26":"\uff46","\uff27":"\uff47","\uff28":"\uff48","\uff29":"\uff49","\uff2a":"\uff4a","\uff2b":"\uff4b","\uff2c":"\uff4c","\uff2d":"\uff4d","\uff2e":"\uff4e","\uff2f":"\uff4f","\uff30":"\uff50","\uff31":"\uff51","\uff32":"\uff52","\uff33":"\uff53","\uff34":"\uff54","\uff35":"\uff55","\uff36":"\uff56","\uff37":"\uff57","\uff38":"\uff58","\uff39":"\uff59","\uff3a":"\uff5a","\ud801\udc00":"\ud801\udc28","\ud801\udc01":"\ud801\udc29","\ud801\udc02":"\ud801\udc2a","\ud801\udc03":"\ud801\udc2b","\ud801\udc04":"\ud801\udc2c","\ud801\udc05":"\ud801\udc2d","\ud801\udc06":"\ud801\udc2e","\ud801\udc07":"\ud801\udc2f","\ud801\udc08":"\ud801\udc30","\ud801\udc09":"\ud801\udc31","\ud801\udc0a":"\ud801\udc32","\ud801\udc0b":"\ud801\udc33","\ud801\udc0c":"\ud801\udc34","\ud801\udc0d":"\ud801\udc35","\ud801\udc0e":"\ud801\udc36","\ud801\udc0f":"\ud801\udc37","\ud801\udc10":"\ud801\udc38","\ud801\udc11":"\ud801\udc39","\ud801\udc12":"\ud801\udc3a","\ud801\udc13":"\ud801\udc3b","\ud801\udc14":"\ud801\udc3c","\ud801\udc15":"\ud801\udc3d","\ud801\udc16":"\ud801\udc3e","\ud801\udc17":"\ud801\udc3f","\ud801\udc18":"\ud801\udc40","\ud801\udc19":"\ud801\udc41","\ud801\udc1a":"\ud801\udc42","\ud801\udc1b":"\ud801\udc43","\ud801\udc1c":"\ud801\udc44","\ud801\udc1d":"\ud801\udc45","\ud801\udc1e":"\ud801\udc46","\ud801\udc1f":"\ud801\udc47","\ud801\udc20":"\ud801\udc48","\ud801\udc21":"\ud801\udc49","\ud801\udc22":"\ud801\udc4a","\ud801\udc23":"\ud801\udc4b","\ud801\udc24":"\ud801\udc4c","\ud801\udc25":"\ud801\udc4d","\ud801\udc26":"\ud801\udc4e","\ud801\udc27":"\ud801\udc4f","\ud801\udcb0":"\ud801\udcd8","\ud801\udcb1":"\ud801\udcd9","\ud801\udcb2":"\ud801\udcda","\ud801\udcb3":"\ud801\udcdb","\ud801\udcb4":"\ud801\udcdc","\ud801\udcb5":"\ud801\udcdd","\ud801\udcb6":"\ud801\udcde","\ud801\udcb7":"\ud801\udcdf","\ud801\udcb8":"\ud801\udce0","\ud801\udcb9":"\ud801\udce1","\ud801\udcba":"\ud801\udce2","\ud801\udcbb":"\ud801\udce3","\ud801\udcbc":"\ud801\udce4","\ud801\udcbd":"\ud801\udce5","\ud801\udcbe":"\ud801\udce6","\ud801\udcbf":"\ud801\udce7","\ud801\udcc0":"\ud801\udce8","\ud801\udcc1":"\ud801\udce9","\ud801\udcc2":"\ud801\udcea","\ud801\udcc3":"\ud801\udceb","\ud801\udcc4":"\ud801\udcec","\ud801\udcc5":"\ud801\udced","\ud801\udcc6":"\ud801\udcee","\ud801\udcc7":"\ud801\udcef","\ud801\udcc8":"\ud801\udcf0","\ud801\udcc9":"\ud801\udcf1","\ud801\udcca":"\ud801\udcf2","\ud801\udccb":"\ud801\udcf3","\ud801\udccc":"\ud801\udcf4","\ud801\udccd":"\ud801\udcf5","\ud801\udcce":"\ud801\udcf6","\ud801\udccf":"\ud801\udcf7","\ud801\udcd0":"\ud801\udcf8","\ud801\udcd1":"\ud801\udcf9","\ud801\udcd2":"\ud801\udcfa","\ud801\udcd3":"\ud801\udcfb","\ud801\udd70":"\ud801\udd97","\ud801\udd71":"\ud801\udd98","\ud801\udd72":"\ud801\udd99","\ud801\udd73":"\ud801\udd9a","\ud801\udd74":"\ud801\udd9b","\ud801\udd75":"\ud801\udd9c","\ud801\udd76":"\ud801\udd9d","\ud801\udd77":"\ud801\udd9e","\ud801\udd78":"\ud801\udd9f","\ud801\udd79":"\ud801\udda0","\ud801\udd7a":"\ud801\udda1","\ud801\udd7c":"\ud801\udda3","\ud801\udd7d":"\ud801\udda4","\ud801\udd7e":"\ud801\udda5","\ud801\udd7f":"\ud801\udda6","\ud801\udd80":"\ud801\udda7","\ud801\udd81":"\ud801\udda8","\ud801\udd82":"\ud801\udda9","\ud801\udd83":"\ud801\uddaa","\ud801\udd84":"\ud801\uddab","\ud801\udd85":"\ud801\uddac","\ud801\udd86":"\ud801\uddad","\ud801\udd87":"\ud801\uddae","\ud801\udd88":"\ud801\uddaf","\ud801\udd89":"\ud801\uddb0","\ud801\udd8a":"\ud801\uddb1","\ud801\udd8c":"\ud801\uddb3","\ud801\udd8d":"\ud801\uddb4","\ud801\udd8e":"\ud801\uddb5","\ud801\udd8f":"\ud801\uddb6","\ud801\udd90":"\ud801\uddb7","\ud801\udd91":"\ud801\uddb8","\ud801\udd92":"\ud801\uddb9","\ud801\udd94":"\ud801\uddbb","\ud801\udd95":"\ud801\uddbc","\ud803\udc80":"\ud803\udcc0","\ud803\udc81":"\ud803\udcc1","\ud803\udc82":"\ud803\udcc2","\ud803\udc83":"\ud803\udcc3","\ud803\udc84":"\ud803\udcc4","\ud803\udc85":"\ud803\udcc5","\ud803\udc86":"\ud803\udcc6","\ud803\udc87":"\ud803\udcc7","\ud803\udc88":"\ud803\udcc8","\ud803\udc89":"\ud803\udcc9","\ud803\udc8a":"\ud803\udcca","\ud803\udc8b":"\ud803\udccb","\ud803\udc8c":"\ud803\udccc","\ud803\udc8d":"\ud803\udccd","\ud803\udc8e":"\ud803\udcce","\ud803\udc8f":"\ud803\udccf","\ud803\udc90":"\ud803\udcd0","\ud803\udc91":"\ud803\udcd1","\ud803\udc92":"\ud803\udcd2","\ud803\udc93":"\ud803\udcd3","\ud803\udc94":"\ud803\udcd4","\ud803\udc95":"\ud803\udcd5","\ud803\udc96":"\ud803\udcd6","\ud803\udc97":"\ud803\udcd7","\ud803\udc98":"\ud803\udcd8","\ud803\udc99":"\ud803\udcd9","\ud803\udc9a":"\ud803\udcda","\ud803\udc9b":"\ud803\udcdb","\ud803\udc9c":"\ud803\udcdc","\ud803\udc9d":"\ud803\udcdd","\ud803\udc9e":"\ud803\udcde","\ud803\udc9f":"\ud803\udcdf","\ud803\udca0":"\ud803\udce0","\ud803\udca1":"\ud803\udce1","\ud803\udca2":"\ud803\udce2","\ud803\udca3":"\ud803\udce3","\ud803\udca4":"\ud803\udce4","\ud803\udca5":"\ud803\udce5","\ud803\udca6":"\ud803\udce6","\ud803\udca7":"\ud803\udce7","\ud803\udca8":"\ud803\udce8","\ud803\udca9":"\ud803\udce9","\ud803\udcaa":"\ud803\udcea","\ud803\udcab":"\ud803\udceb","\ud803\udcac":"\ud803\udcec","\ud803\udcad":"\ud803\udced","\ud803\udcae":"\ud803\udcee","\ud803\udcaf":"\ud803\udcef","\ud803\udcb0":"\ud803\udcf0","\ud803\udcb1":"\ud803\udcf1","\ud803\udcb2":"\ud803\udcf2","\ud806\udca0":"\ud806\udcc0","\ud806\udca1":"\ud806\udcc1","\ud806\udca2":"\ud806\udcc2","\ud806\udca3":"\ud806\udcc3","\ud806\udca4":"\ud806\udcc4","\ud806\udca5":"\ud806\udcc5","\ud806\udca6":"\ud806\udcc6","\ud806\udca7":"\ud806\udcc7","\ud806\udca8":"\ud806\udcc8","\ud806\udca9":"\ud806\udcc9","\ud806\udcaa":"\ud806\udcca","\ud806\udcab":"\ud806\udccb","\ud806\udcac":"\ud806\udccc","\ud806\udcad":"\ud806\udccd","\ud806\udcae":"\ud806\udcce","\ud806\udcaf":"\ud806\udccf","\ud806\udcb0":"\ud806\udcd0","\ud806\udcb1":"\ud806\udcd1","\ud806\udcb2":"\ud806\udcd2","\ud806\udcb3":"\ud806\udcd3","\ud806\udcb4":"\ud806\udcd4","\ud806\udcb5":"\ud806\udcd5","\ud806\udcb6":"\ud806\udcd6","\ud806\udcb7":"\ud806\udcd7","\ud806\udcb8":"\ud806\udcd8","\ud806\udcb9":"\ud806\udcd9","\ud806\udcba":"\ud806\udcda","\ud806\udcbb":"\ud806\udcdb","\ud806\udcbc":"\ud806\udcdc","\ud806\udcbd":"\ud806\udcdd","\ud806\udcbe":"\ud806\udcde","\ud806\udcbf":"\ud806\udcdf","\ud81b\ude40":"\ud81b\ude60","\ud81b\ude41":"\ud81b\ude61","\ud81b\ude42":"\ud81b\ude62","\ud81b\ude43":"\ud81b\ude63","\ud81b\ude44":"\ud81b\ude64","\ud81b\ude45":"\ud81b\ude65","\ud81b\ude46":"\ud81b\ude66","\ud81b\ude47":"\ud81b\ude67","\ud81b\ude48":"\ud81b\ude68","\ud81b\ude49":"\ud81b\ude69","\ud81b\ude4a":"\ud81b\ude6a","\ud81b\ude4b":"\ud81b\ude6b","\ud81b\ude4c":"\ud81b\ude6c","\ud81b\ude4d":"\ud81b\ude6d","\ud81b\ude4e":"\ud81b\ude6e","\ud81b\ude4f":"\ud81b\ude6f","\ud81b\ude50":"\ud81b\ude70","\ud81b\ude51":"\ud81b\ude71","\ud81b\ude52":"\ud81b\ude72","\ud81b\ude53":"\ud81b\ude73","\ud81b\ude54":"\ud81b\ude74","\ud81b\ude55":"\ud81b\ude75","\ud81b\ude56":"\ud81b\ude76","\ud81b\ude57":"\ud81b\ude77","\ud81b\ude58":"\ud81b\ude78","\ud81b\ude59":"\ud81b\ude79","\ud81b\ude5a":"\ud81b\ude7a","\ud81b\ude5b":"\ud81b\ude7b","\ud81b\ude5c":"\ud81b\ude7c","\ud81b\ude5d":"\ud81b\ude7d","\ud81b\ude5e":"\ud81b\ude7e","\ud81b\ude5f":"\ud81b\ude7f","\ud83a\udd00":"\ud83a\udd22","\ud83a\udd01":"\ud83a\udd23","\ud83a\udd02":"\ud83a\udd24","\ud83a\udd03":"\ud83a\udd25","\ud83a\udd04":"\ud83a\udd26","\ud83a\udd05":"\ud83a\udd27","\ud83a\udd06":"\ud83a\udd28","\ud83a\udd07":"\ud83a\udd29","\ud83a\udd08":"\ud83a\udd2a","\ud83a\udd09":"\ud83a\udd2b","\ud83a\udd0a":"\ud83a\udd2c","\ud83a\udd0b":"\ud83a\udd2d","\ud83a\udd0c":"\ud83a\udd2e","\ud83a\udd0d":"\ud83a\udd2f","\ud83a\udd0e":"\ud83a\udd30","\ud83a\udd0f":"\ud83a\udd31","\ud83a\udd10":"\ud83a\udd32","\ud83a\udd11":"\ud83a\udd33","\ud83a\udd12":"\ud83a\udd34","\ud83a\udd13":"\ud83a\udd35","\ud83a\udd14":"\ud83a\udd36","\ud83a\udd15":"\ud83a\udd37","\ud83a\udd16":"\ud83a\udd38","\ud83a\udd17":"\ud83a\udd39","\ud83a\udd18":"\ud83a\udd3a","\ud83a\udd19":"\ud83a\udd3b","\ud83a\udd1a":"\ud83a\udd3c","\ud83a\udd1b":"\ud83a\udd3d","\ud83a\udd1c":"\ud83a\udd3e","\ud83a\udd1d":"\ud83a\udd3f","\ud83a\udd1e":"\ud83a\udd40","\ud83a\udd1f":"\ud83a\udd41","\ud83a\udd20":"\ud83a\udd42","\ud83a\udd21":"\ud83a\udd43"}$fold$::jsonb->>character,character),'' ORDER BY position),'')
 FROM regexp_split_to_table(p_text,'') WITH ORDINALITY AS characters(character,position)
$_$;

-- FUNCTION: management_certificate_revision_search()
CREATE FUNCTION public.management_certificate_revision_search() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.certificates p SET search_vector=public.management_certificate_vector(NEW.data)
  WHERE p.id=NEW.certificate_id AND p.current_revision=NEW.revision;
 RETURN NEW;
END $$;

-- FUNCTION: management_certificate_root_guard()
CREATE FUNCTION public.management_certificate_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new certificate requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'certificate identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human('certificate');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.certificate_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'certificate lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_certificate_vector(r.data) INTO v FROM public.certificate_revisions r
  WHERE r.certificate_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- FUNCTION: management_certificate_selection_guard()
CREATE FUNCTION public.management_certificate_selection_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:certificate',NULL,false,false);
 SELECT p.lifecycle_state INTO root_state FROM public.certificates p
 WHERE p.id=NEW.certificate_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive certificate cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_certificate_vector(jsonb)
CREATE FUNCTION public.management_certificate_vector(p_data jsonb) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','') || ' ' || coalesce(p_data->>'number','')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_confidential_field_guard()
CREATE FUNCTION public.management_confidential_field_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF (NEW.id,NEW.key,NEW.kind,NEW.scope,NEW.created_at)
    IS DISTINCT FROM
    (OLD.id,OLD.key,OLD.kind,OLD.scope,OLD.created_at)
    OR NEW.revision<>OLD.revision+1 THEN
  RAISE EXCEPTION 'confidential field identity is fixed and metadata revision must advance'
   USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_confidential_tokens_guard()
CREATE FUNCTION public.management_confidential_tokens_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='UPDATE' OR (TG_OP='INSERT' AND pg_trigger_depth()<>2) THEN
  RAISE EXCEPTION 'confidential search tokens are maintained by field triggers'
   USING ERRCODE='23514';
 END IF;
 -- Depth alone is forgeable through a caller-owned temporary trigger. Validate
 -- every change against the real parent, locked against concurrent label edits.
 IF TG_OP='INSERT' THEN
  SELECT search_vector INTO v FROM public.confidential_fields
   WHERE id=NEW.field_id FOR SHARE;
  IF v IS NULL OR NOT(NEW.token=ANY(tsvector_to_array(v))) THEN
   RAISE EXCEPTION 'confidential search token must match field metadata'
    USING ERRCODE='23514';
  END IF;
 ELSE
  SELECT search_vector INTO v FROM public.confidential_fields
   WHERE id=OLD.field_id FOR SHARE;
  -- FK cascades can dispatch the queued AFTER DELETE after their trigger
  -- frame has unwound. A missing parent, not a fixed nesting depth, proves
  -- this cleanup is legitimate.
  IF NOT FOUND THEN
   RETURN NULL;
  END IF;
  IF pg_trigger_depth()<>2 OR v IS NULL OR OLD.token=ANY(tsvector_to_array(v)) THEN
   RAISE EXCEPTION 'current confidential search token must be retained'
    USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NULL;
END $$;

-- FUNCTION: management_confidential_tokens_refresh()
CREATE FUNCTION public.management_confidential_tokens_refresh() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_RELID<>'public.confidential_fields'::regclass THEN
  RAISE EXCEPTION 'confidential search refresh requires its field table'
   USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND NEW.search_vector IS NOT DISTINCT FROM OLD.search_vector THEN
  RETURN NULL;
 END IF;
 DELETE FROM public.confidential_field_search_tokens
  WHERE field_id=NEW.id
    AND NOT(token=ANY(tsvector_to_array(NEW.search_vector)));
 INSERT INTO public.confidential_field_search_tokens(field_id,token)
  SELECT NEW.id,unnest(tsvector_to_array(NEW.search_vector))
  ON CONFLICT(token,field_id) DO NOTHING;
 RETURN NULL;
END $$;

-- FUNCTION: management_confidential_vector(text, text)
CREATE FUNCTION public.management_confidential_vector(p_key text, p_label text) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(p_key||' '||p_label), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_feature_head_guard()
CREATE FUNCTION public.management_feature_head_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE parent uuid; state text;
BEGIN
 IF NEW.current_revision=OLD.current_revision THEN RETURN NEW; END IF;
 SELECT r.product_id INTO parent FROM public.feature_revisions r
 WHERE r.feature_id=NEW.id AND r.revision=NEW.current_revision;
 -- The deferred composite FK owns an absent revision; do not replace its error.
 IF parent IS NULL THEN RETURN NEW; END IF;
 SELECT p.lifecycle_state INTO state FROM public.products p
 WHERE p.id=parent FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive product cannot receive a feature association' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_feature_lock(uuid, uuid, boolean)
CREATE FUNCTION public.management_feature_lock(p_feature uuid, p_parent uuid, p_write boolean) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE head integer; actual integer; parent uuid; item record;
BEGIN
 SELECT f.current_revision,r.product_id INTO head,parent FROM public.features f
 LEFT JOIN public.feature_revisions r ON r.feature_id=f.id AND r.revision=f.current_revision
 WHERE f.id=p_feature;
 FOR item IN SELECT ids.id,ids.kind FROM (
   SELECT p_feature AS id,'feature'::text AS kind
   UNION SELECT parent,'product' WHERE parent IS NOT NULL
   UNION SELECT p_parent,'product' WHERE p_parent IS NOT NULL
 ) ids ORDER BY ids.id,ids.kind LOOP
  IF item.kind='feature' THEN
   IF p_write THEN
    PERFORM 1 FROM public.features f WHERE f.id=item.id FOR NO KEY UPDATE;
   ELSE
    PERFORM 1 FROM public.features f WHERE f.id=item.id FOR SHARE;
   END IF;
  ELSE
   PERFORM 1 FROM public.products p WHERE p.id=item.id FOR SHARE;
  END IF;
 END LOOP;
 SELECT f.current_revision INTO actual FROM public.features f WHERE f.id=p_feature;
 IF actual IS DISTINCT FROM head THEN
  RAISE EXCEPTION 'feature association changed; repeat transaction' USING ERRCODE='40001'; END IF;
END $$;

-- FUNCTION: management_feature_revision_guard()
CREATE FUNCTION public.management_feature_revision_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE parent_state text;
BEGIN
 PERFORM public.management_feature_lock(NEW.feature_id,NEW.product_id,true);
 SELECT p.lifecycle_state INTO parent_state FROM public.products p
 WHERE p.id=NEW.product_id;
 IF parent_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive product cannot receive a feature association' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_feature_revision_search()
CREATE FUNCTION public.management_feature_revision_search() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.features f SET search_vector=public.management_feature_vector(NEW.data)
  WHERE f.id=NEW.feature_id AND f.current_revision=NEW.revision;
 RETURN NEW;
END $$;

-- FUNCTION: management_feature_root_guard()
CREATE FUNCTION public.management_feature_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new feature requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'feature identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.feature_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'feature lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_feature_vector(r.data) INTO v FROM public.feature_revisions r
  WHERE r.feature_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- FUNCTION: management_feature_selection_guard()
CREATE FUNCTION public.management_feature_selection_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE parent uuid; feature_state text; parent_state text; current_parent_state text;
BEGIN
 -- Branch TG_OP explicitly: OLD has no row on INSERT.
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:resource',NULL,false,false);
 SELECT r.product_id INTO parent FROM public.feature_revisions r
 WHERE r.feature_id=NEW.feature_id AND r.id=NEW.feature_revision_id;
 PERFORM public.management_feature_lock(NEW.feature_id,parent,false);
 SELECT f.lifecycle_state,p.lifecycle_state INTO feature_state,current_parent_state
 FROM public.features f JOIN public.feature_revisions r
 ON r.feature_id=f.id AND r.revision=f.current_revision
 JOIN public.products p ON p.id=r.product_id
 WHERE f.id=NEW.feature_id;
 SELECT p.lifecycle_state INTO parent_state FROM public.products p WHERE p.id=parent;
 IF feature_state IS DISTINCT FROM 'active' OR parent_state IS DISTINCT FROM 'active'
    OR current_parent_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive feature or parent cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_feature_task_lock()
CREATE FUNCTION public.management_feature_task_lock() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE row_task uuid;
BEGIN
 IF TG_OP='DELETE' THEN row_task:=OLD.task_id;
 ELSE row_task:=NEW.task_id; END IF;
 PERFORM 1 FROM public.tasks t WHERE t.id=row_task FOR UPDATE;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;

-- FUNCTION: management_feature_vector(jsonb)
CREATE FUNCTION public.management_feature_vector(p_data jsonb) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_lifecycle_apply()
CREATE FUNCTION public.management_lifecycle_apply() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW.product_id IS NOT NULL THEN
  UPDATE public.products p SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE p.id=NEW.product_id;
 ELSIF NEW.feature_id IS NOT NULL THEN
  UPDATE public.features f SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE f.id=NEW.feature_id;
 ELSIF NEW.certificate_id IS NOT NULL THEN
  UPDATE public.certificates c SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE c.id=NEW.certificate_id;
 ELSIF NEW.template_id IS NOT NULL THEN
  UPDATE public.templates t SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE t.id=NEW.template_id;
 ELSE
  UPDATE public.org_profiles p SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE p.id=NEW.profile_id;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_lifecycle_audit_guard()
CREATE FUNCTION public.management_lifecycle_audit_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root_id uuid; resource_kind text;
BEGIN
 IF NEW.product_id IS NOT NULL THEN root_id:=NEW.product_id; resource_kind:='product';
 ELSIF NEW.feature_id IS NOT NULL THEN root_id:=NEW.feature_id; resource_kind:='feature';
 ELSIF NEW.certificate_id IS NOT NULL THEN root_id:=NEW.certificate_id; resource_kind:='certificate';
 ELSIF NEW.template_id IS NOT NULL THEN root_id:=NEW.template_id; resource_kind:='template';
 ELSE root_id:=NEW.profile_id; resource_kind:='profile'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.object_id=root_id
   AND a.action='resource.' || resource_kind || '.' || (CASE WHEN NEW.after_state='inactive' THEN 'deactivate' ELSE 'restore' END)
   AND a.actor_token_id IS NULL AND a.actor_kind='session'
   AND a.details->>'event_id'=NEW.id::text
   AND a.details->>'old_lifecycle_revision'=(NEW.revision-1)::text
   AND a.details->>'new_lifecycle_revision'=NEW.revision::text
   AND a.details->>'resource_revision'=NEW.resource_revision::text
   AND a.details->>'before'=NEW.before_state AND a.details->>'after'=NEW.after_state
   AND a.details->>'reason_code'=NEW.reason_code) THEN
  RAISE EXCEPTION 'lifecycle transition requires atomic audit' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: management_lifecycle_guard()
CREATE FUNCTION public.management_lifecycle_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root_id uuid; root_sequence integer; root_revision integer; root_state text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'lifecycle history is immutable' USING ERRCODE='42501'; END IF;
 IF NEW.template_id IS NOT NULL THEN
  PERFORM public.management_template_human();
 ELSIF NEW.certificate_id IS NOT NULL THEN
  PERFORM public.management_bidder_human('certificate');
 ELSIF NEW.profile_id IS NOT NULL THEN
  PERFORM public.management_bidder_human('profile');
 ELSE PERFORM public.management_product_human(); END IF;
 IF NEW.product_id IS NOT NULL THEN
  SELECT p.id,p.lifecycle_revision,p.current_revision,p.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.products p
  WHERE p.id=NEW.product_id FOR UPDATE;
 ELSIF NEW.feature_id IS NOT NULL THEN
  SELECT f.id,f.lifecycle_revision,f.current_revision,f.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.features f
  WHERE f.id=NEW.feature_id FOR UPDATE;
 ELSIF NEW.certificate_id IS NOT NULL THEN
  SELECT c.id,c.lifecycle_revision,c.current_revision,c.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.certificates c
  WHERE c.id=NEW.certificate_id FOR UPDATE;
 ELSIF NEW.template_id IS NOT NULL THEN
  SELECT t.id,t.lifecycle_revision,t.current_revision,t.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.templates t
  WHERE t.id=NEW.template_id FOR UPDATE;
 ELSE
  SELECT p.id,p.lifecycle_revision,p.current_revision,p.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.org_profiles p
  WHERE p.id=NEW.profile_id FOR UPDATE;
 END IF;
 IF root_id IS NULL OR NEW.revision<>root_sequence+1 OR NEW.resource_revision<>root_revision
    OR NEW.before_state<>root_state OR NEW.after_state=NEW.before_state THEN
  RAISE EXCEPTION 'lifecycle event must extend current root' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_memory_initial_projection()
CREATE FUNCTION public.management_memory_initial_projection() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.memories m SET
  search_vector=public.management_memory_vector(NEW.normalized_text,NEW.content,NEW.normalized_tags),
  search_tags=NEW.normalized_tags,search_kind=NEW.content->>'kind',
  search_status=NEW.status,search_expires_at=NEW.expires_at
 WHERE m.id=NEW.memory_id
  AND m.current_revision_id=NEW.id AND m.revision=NEW.revision
  AND ROW(m.search_vector,m.search_tags,m.search_kind,m.search_status,m.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(NEW.normalized_text,NEW.content,NEW.normalized_tags),
    NEW.normalized_tags,NEW.content->>'kind',NEW.status::text,NEW.expires_at);
 RETURN NEW;
END $$;

-- FUNCTION: management_memory_vector(text, jsonb, text[])
CREATE FUNCTION public.management_memory_vector(p_text text, p_content jsonb, p_tags text[]) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig, regexp_replace(
   public.management_casefold(coalesce(p_text,'') || ' ' || coalesce(p_content->>'conflict_key','')
     || ' ' || coalesce(array_to_string(p_tags,' '),'')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_product_human()
CREATE FUNCTION public.management_product_human() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT public.actor_is_human()
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? 'resource:write' THEN
   RAISE EXCEPTION 'human resource maintenance required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: management_product_revision_search()
CREATE FUNCTION public.management_product_revision_search() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.products SET search_vector=public.management_product_vector(NEW.data)
  WHERE id=NEW.product_id AND current_revision=NEW.revision;
 RETURN NEW;
END $$;

-- FUNCTION: management_product_root_guard()
CREATE FUNCTION public.management_product_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new product requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'product identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.product_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'product lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_product_vector(r.data) INTO v FROM public.product_revisions r
  WHERE r.product_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- FUNCTION: management_product_selection_guard()
CREATE FUNCTION public.management_product_selection_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE s text;
BEGIN
 IF NEW.active AND (TG_OP='INSERT' OR NOT OLD.active) THEN
  PERFORM public.task_write_authority(NEW.task_id,'task:resource',NULL,false,false);
  SELECT lifecycle_state INTO s FROM public.products WHERE id=NEW.product_id FOR SHARE;
  IF s IS DISTINCT FROM 'active' THEN
   RAISE EXCEPTION 'inactive product cannot receive a new pin' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_product_vector(jsonb)
CREATE FUNCTION public.management_product_vector(p_data jsonb) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','') || ' ' || coalesce(p_data->>'vendor','') || ' ' || coalesce(p_data->>'model','')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_profile_revision_search()
CREATE FUNCTION public.management_profile_revision_search() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.org_profiles p SET search_vector=public.management_profile_vector(NEW.data)
  WHERE p.id=NEW.profile_id AND p.current_revision=NEW.revision;
 RETURN NEW;
END $$;

-- FUNCTION: management_profile_root_guard()
CREATE FUNCTION public.management_profile_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new profile requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'profile identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human('profile');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.profile_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'profile lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_profile_vector(r.data) INTO v FROM public.org_profile_revisions r
  WHERE r.profile_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- FUNCTION: management_profile_selection_guard()
CREATE FUNCTION public.management_profile_selection_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:profile',NULL,false,false);
 SELECT p.lifecycle_state INTO root_state FROM public.org_profiles p
 WHERE p.id=NEW.profile_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive profile cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_profile_vector(jsonb)
CREATE FUNCTION public.management_profile_vector(p_data jsonb) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: management_template_human()
CREATE FUNCTION public.management_template_human() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT public.actor_is_human()
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? 'template:write' THEN
  RAISE EXCEPTION 'human template maintenance required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: management_template_revision_search()
CREATE FUNCTION public.management_template_revision_search() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 UPDATE public.templates f SET search_vector=public.management_template_vector(NEW.data)
  WHERE f.id=NEW.template_id AND f.current_revision=NEW.revision;
 RETURN NEW;
END $$;

-- FUNCTION: management_template_root_guard()
CREATE FUNCTION public.management_template_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new template requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'template identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_template_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.template_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'template lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_template_vector(r.data) INTO v FROM public.template_revisions r
  WHERE r.template_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;

-- FUNCTION: management_template_selection_guard()
CREATE FUNCTION public.management_template_selection_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.active THEN RETURN NEW; END IF;
  -- Retained template pins need this rule even after the root is restored.
  -- The AFTER trigger preserves FK/CHECK rejection precedence.
  RAISE EXCEPTION 'Historical selections cannot be reactivated; create a new selection' USING ERRCODE='42501';
 END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:template',NULL,false,false);
 SELECT t.lifecycle_state INTO state FROM public.templates t
 WHERE t.id=NEW.template_id FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive template cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: management_template_vector(jsonb)
CREATE FUNCTION public.management_template_vector(p_data jsonb) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;

-- FUNCTION: memory_call_gate()
CREATE FUNCTION public.memory_call_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE job_row public.jobs%ROWTYPE; retrieval_row public.memory_retrievals%ROWTYPE;
 ref jsonb; requirement_value jsonb; usage_row public.usage_records%ROWTYPE; submitted jsonb;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory call lineage cannot be deleted' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
 submitted:=job_row.result->'submission';
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['state','usage_record_id']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','usage_record_id'])
   OR OLD.state='completed' OR NEW.state NOT IN ('completed','unknown') THEN
   RAISE EXCEPTION 'Memory call inputs cannot change after admission' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.vendor_calls WHERE id=NEW.call_id AND job_id=NEW.job_id AND run_id=NEW.run_id AND state=NEW.state) THEN
   RAISE EXCEPTION 'Memory settlement must match vendor outcome' USING ERRCODE='23514'; END IF;
  IF NEW.usage_record_id IS NOT NULL THEN
   SELECT * INTO usage_row FROM public.usage_records WHERE id=NEW.usage_record_id;
   IF usage_row.id IS NULL OR ROW(usage_row.job_id,usage_row.run_id,usage_row.call_id) IS DISTINCT FROM ROW(NEW.job_id,NEW.run_id,NEW.call_id) THEN
    RAISE EXCEPTION 'Memory call usage binding mismatch' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 PERFORM public.response_check_actor((submitted->>'actor_token_id')::uuid,'worker');
 SELECT * INTO retrieval_row FROM public.memory_retrievals WHERE id=NEW.retrieval_id;
 IF job_row.kind IS DISTINCT FROM 'card_generate' OR job_row.status IS DISTINCT FROM 'running' OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR NEW.state<>'admitted' OR NEW.usage_record_id IS NOT NULL
  OR retrieval_row.task_id IS DISTINCT FROM NEW.task_id
  OR retrieval_row.actor_token_id IS DISTINCT FROM (submitted->>'actor_token_id')::uuid
  OR NEW.retrieval_id IS DISTINCT FROM (submitted->>'memory_retrieval_id')::uuid
  OR NEW.prompt_version IS DISTINCT FROM submitted->'input_manifest'->>'prompt_version'
  OR NEW.manifest_sha256 IS DISTINCT FROM submitted->'input_manifest'->'memory'->>'manifest_sha256'
  OR NEW.memories IS DISTINCT FROM submitted->'input_manifest'->'memory'->'memories'
  OR NEW.manifest_sha256 IS DISTINCT FROM retrieval_row.data->>'manifest_sha256'
  OR public.memory_manifest_current(submitted->'input_manifest'->'memory') IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Memory call requires the live attempt and current input' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.memories)<>(SELECT count(*) FROM public.memory_retrieval_items WHERE retrieval_id=NEW.retrieval_id AND selected)
  OR (SELECT count(DISTINCT value->>'revision_id') FROM jsonb_array_elements(NEW.memories))<>jsonb_array_length(NEW.memories)
  OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.requirement_ids))<>jsonb_array_length(NEW.requirement_ids) THEN
  RAISE EXCEPTION 'Memory call input list must be exact and unique' USING ERRCODE='23514'; END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(NEW.memories) LOOP
  IF NOT EXISTS(SELECT 1 FROM public.memory_retrieval_items WHERE retrieval_id=NEW.retrieval_id AND selected
   AND memory_id=(ref->>'memory_id')::uuid AND memory_revision_id=(ref->>'revision_id')::uuid
   AND revision=(ref->>'revision')::integer AND content_sha256=ref->>'content_sha256' AND sent_sha256=ref->>'sent_sha256') THEN
   RAISE EXCEPTION 'Memory call contains an unselected revision' USING ERRCODE='23514'; END IF;
 END LOOP;
 FOR requirement_value IN SELECT value FROM jsonb_array_elements(NEW.requirement_ids) LOOP
  IF NOT EXISTS(SELECT 1 FROM public.requirements WHERE id=(requirement_value#>>'{}')::uuid AND task_id=NEW.task_id)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(submitted->'input_manifest'->'requirements') value
    WHERE coalesce(value->>'id',value->>'requirement_id')=requirement_value#>>'{}') THEN
   RAISE EXCEPTION 'Memory call requirement is outside its fixed generation input' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;

-- FUNCTION: memory_card_confirmation_gate()
CREATE FUNCTION public.memory_card_confirmation_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE prior public.response_card_revisions%ROWTYPE;
BEGIN
 SELECT * INTO prior FROM public.response_card_revisions WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
 IF NEW.state='confirmed' AND prior.state IS DISTINCT FROM 'confirmed' AND NEW.model_job_id IS NOT NULL
  AND public.memory_generation_current(NEW.model_job_id) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Memory input changed; edit or regenerate before confirmation' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: memory_epoch_gate()
CREATE FUNCTION public.memory_epoch_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory epochs cannot be removed' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (NEW.scope IS DISTINCT FROM OLD.scope
  OR NEW.epoch<OLD.epoch) THEN RAISE EXCEPTION 'Memory epoch cannot move backwards or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: memory_eval_gate()
CREATE FUNCTION public.memory_eval_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE event_row public.memory_feedback_events%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory evaluation history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
  SELECT * INTO event_row FROM public.memory_feedback_events WHERE id=NEW.feedback_event_id;
  PERFORM public.response_check_actor(NULL,'session');
  IF event_row.id IS NULL OR NOT public.actor_is_human()
   OR ROW(NEW.task_id,NEW.card_id,NEW.before_revision_id,NEW.after_revision_id,NEW.label,NEW.sanitized_sha256)
   IS DISTINCT FROM ROW(event_row.task_id,event_row.card_id,event_row.before_revision_id,event_row.after_revision_id,event_row.kind,event_row.sanitized_sha256)
   OR NEW.review_state<>'unreviewed' OR NEW.review_revision<>1 OR NEW.review_reason_sha256 IS NOT NULL THEN
   RAISE EXCEPTION 'Evaluation sample must retain the source feedback' USING ERRCODE='23514'; END IF;
 ELSE
  PERFORM public.response_require_human();
  IF (to_jsonb(NEW)-ARRAY['review_state','review_revision','reviewed_at','review_reason_sha256'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['review_state','review_revision','reviewed_at','review_reason_sha256'])
   OR NEW.review_revision<>OLD.review_revision+1
   OR NEW.review_state NOT IN ('accepted','excluded') OR NEW.reviewed_at IS NULL
   OR NEW.review_reason_sha256 IS NULL OR NEW.review_reason_sha256 !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'Evaluation review must be an exact human revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $_$;

-- FUNCTION: memory_feedback_gate()
CREATE FUNCTION public.memory_feedback_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE before_row public.response_card_revisions%ROWTYPE; after_row public.response_card_revisions%ROWTYPE;
 card_row public.response_cards%ROWTYPE; after_xid text; job_row public.jobs%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NULL,'session');
 SELECT * INTO before_row FROM public.response_card_revisions WHERE id=NEW.before_revision_id;
 SELECT * INTO after_row FROM public.response_card_revisions WHERE id=NEW.after_revision_id;
 SELECT xmin::text INTO after_xid FROM public.response_card_revisions WHERE id=NEW.after_revision_id;
 SELECT * INTO card_row FROM public.response_cards WHERE id=NEW.card_id;
 SELECT * INTO job_row FROM public.jobs WHERE id=card_row.extraction_job_id;
 IF before_row.id IS NULL OR after_row.id IS NULL OR before_row.model_job_id IS NULL
  OR NEW.model_job_id IS DISTINCT FROM before_row.model_job_id
  OR after_row.revision<>before_row.revision+1 OR after_row.actor_kind<>'session'
  OR after_row.actor_token_id IS NOT NULL OR NOT public.actor_is_human()
  OR after_xid IS DISTINCT FROM pg_current_xact_id()::text
  OR card_row.current_revision_id IS DISTINCT FROM after_row.id
  OR NEW.document_id IS DISTINCT FROM job_row.document_id
  OR NEW.review_domain IS DISTINCT FROM after_row.review_domain
  OR (NEW.kind='card_rejected' AND after_row.state<>'rejected')
  OR (NEW.kind='card_confirmed' AND (after_row.state<>'confirmed' OR after_row.confirmed_at IS NULL))
  OR (NEW.kind='card_edited' AND ROW(before_row.response_text,before_row.deviation,before_row.deviation_note)
     IS NOT DISTINCT FROM ROW(after_row.response_text,after_row.deviation,after_row.deviation_note)) THEN
  RAISE EXCEPTION 'Feedback must belong to an adjacent human model-card decision' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: memory_generation_current(uuid)
CREATE FUNCTION public.memory_generation_current(p_job uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT p_job IS NULL OR EXISTS(SELECT 1 FROM public.card_generation_runs
 WHERE generation_job_id=p_job AND public.memory_manifest_current(input_manifest->'memory'))
$$;

-- FUNCTION: memory_immutable_gate()
CREATE FUNCTION public.memory_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 RAISE EXCEPTION 'Memory history is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: memory_manifest_current(jsonb)
CREATE FUNCTION public.memory_manifest_current(p_manifest jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE epoch_value jsonb; memory_value jsonb;
BEGIN
 IF p_manifest IS NULL THEN RETURN true; END IF;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope='library' FOR SHARE;
 IF jsonb_typeof(p_manifest) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_manifest->'epochs') IS DISTINCT FROM 'array'
  OR p_manifest->'scopes' IS DISTINCT FROM '["library"]'::jsonb
  OR p_manifest->>'retrieval_version' IS DISTINCT FROM 'keyword-v1'
  OR p_manifest->>'priority_version' IS DISTINCT FROM 'memory-priority-v1'
  OR p_manifest->>'sanitizer_version' IS DISTINCT FROM 'memory-sanitize-v1:bid-redaction-v3'
  OR jsonb_typeof(p_manifest->'memories') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_manifest->'epochs')<>1
  OR ((p_manifest->>'valid_until')::timestamptz IS NOT NULL AND (p_manifest->>'valid_until')::timestamptz<=clock_timestamp()) THEN RETURN false; END IF;
 FOR epoch_value IN SELECT value FROM jsonb_array_elements(p_manifest->'epochs') LOOP
  IF epoch_value->>'scope' IS DISTINCT FROM 'library'
   OR (epoch_value->>'epoch')::bigint IS DISTINCT FROM coalesce((SELECT epoch FROM public.memory_scope_epochs WHERE scope='library'),0) THEN RETURN false; END IF;
 END LOOP;
 FOR memory_value IN SELECT value FROM jsonb_array_elements(p_manifest->'memories') LOOP
  IF NOT EXISTS(SELECT 1 FROM public.memories m JOIN public.memory_revisions r ON r.id=m.current_revision_id
    WHERE m.id=(memory_value->>'memory_id')::uuid AND m.scope='library' AND m.deleted_at IS NULL
     AND r.id=(memory_value->>'revision_id')::uuid AND r.revision=(memory_value->>'revision')::integer AND r.status='active'
     AND r.content_sha256=memory_value->>'content_sha256' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())) THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

-- FUNCTION: memory_parent_gate()
CREATE FUNCTION public.memory_parent_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE current_row public.memory_revisions%ROWTYPE; previous_row public.memory_revisions%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory deletion must preserve history' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND
  ROW(NEW.search_vector,NEW.search_tags,NEW.search_kind,NEW.search_status,NEW.search_expires_at)
  IS DISTINCT FROM ROW(OLD.search_vector,OLD.search_tags,OLD.search_kind,OLD.search_status,OLD.search_expires_at) AND
  (to_jsonb(NEW)-ARRAY['search_vector','search_tags','search_kind','search_status','search_expires_at']) IS NOT DISTINCT FROM
  (to_jsonb(OLD)-ARRAY['search_vector','search_tags','search_kind','search_status','search_expires_at']) THEN
  SELECT * INTO current_row FROM public.memory_revisions
   WHERE memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  IF current_row.id IS NULL OR
   ROW(NEW.search_vector,NEW.search_tags,NEW.search_kind,NEW.search_status,NEW.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(current_row.normalized_text,current_row.content,current_row.normalized_tags),
     current_row.normalized_tags,current_row.content->>'kind',current_row.status::text,current_row.expires_at) THEN
   RAISE EXCEPTION 'Memory search must match the exact current revision' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 INSERT INTO public.memory_scope_epochs(scope,epoch) VALUES('library',0) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope='library' FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF NEW.revision<>1 OR NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Initial memory must be a candidate' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.deleted_at IS NOT NULL OR NEW.revision<>OLD.revision+1
   OR (to_jsonb(NEW)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) THEN
   RAISE EXCEPTION 'Memory owner and history cannot change' USING ERRCODE='23514'; END IF;
  SELECT * INTO current_row FROM public.memory_revisions WHERE memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  SELECT * INTO previous_row FROM public.memory_revisions WHERE id=OLD.current_revision_id;
  IF current_row.id IS NULL OR ((current_row.decision IS NOT DISTINCT FROM 'delete') IS DISTINCT FROM (NEW.deleted_at IS NOT NULL)) THEN
   RAISE EXCEPTION 'Memory pointer or tombstone mismatch' USING ERRCODE='23514'; END IF;
  IF current_row.status='active' OR previous_row.status='active' THEN
   UPDATE public.memory_scope_epochs SET epoch=epoch+1 WHERE scope='library';
  END IF;
 END IF;
 -- A real revision can leave the search projection unchanged. Do not issue a
 -- nested no-op UPDATE: it must remain forbidden by the durable-history gate.
 UPDATE public.memories m SET
  search_vector=public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
  search_tags=r.normalized_tags, search_kind=r.content->>'kind',
  search_status=r.status, search_expires_at=r.expires_at
 FROM public.memory_revisions r
 WHERE m.id=NEW.id AND r.memory_id=m.id AND r.id=m.current_revision_id AND r.revision=m.revision
  AND ROW(m.search_vector,m.search_tags,m.search_kind,m.search_status,m.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
    r.normalized_tags,r.content->>'kind',r.status::text,r.expires_at);
 RETURN NEW;
END $$;

-- FUNCTION: memory_retrieval_gate()
CREATE FUNCTION public.memory_retrieval_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE actor_kind text;
BEGIN
 actor_kind:=current_setting('app.actor_kind',true);
 PERFORM public.response_check_actor(NEW.actor_token_id,actor_kind);
 IF actor_kind NOT IN ('session','token','worker')
  OR NEW.data->>'retrieval_id' IS DISTINCT FROM NEW.id::text
  OR NEW.data->'preview' IS DISTINCT FROM 'false'::jsonb
  OR NEW.data->'currently_valid' IS DISTINCT FROM 'true'::jsonb
  OR NEW.data->'scopes' IS DISTINCT FROM '["library"]'::jsonb THEN
  RAISE EXCEPTION 'Memory retrieval requires an authenticated org binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: memory_retrieval_item_gate()
CREATE FUNCTION public.memory_retrieval_item_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE retrieval_row public.memory_retrievals%ROWTYPE; retrieval_xid text; revision_row public.memory_revisions%ROWTYPE;
BEGIN
 SELECT * INTO retrieval_row FROM public.memory_retrievals WHERE id=NEW.retrieval_id;
 SELECT xmin::text INTO retrieval_xid FROM public.memory_retrievals WHERE id=NEW.retrieval_id;
 SELECT * INTO revision_row FROM public.memory_revisions WHERE id=NEW.memory_revision_id;
 IF retrieval_xid IS DISTINCT FROM pg_current_xact_id()::text OR revision_row.status IS DISTINCT FROM 'active'
  OR revision_row.content_sha256 IS DISTINCT FROM NEW.content_sha256
  OR NEW.sent_sha256 IS DISTINCT FROM encode(sha256(convert_to(revision_row.content->>'text','UTF8')),'hex')
  OR NOT EXISTS(SELECT 1 FROM public.memories WHERE id=NEW.memory_id AND current_revision_id=NEW.memory_revision_id AND deleted_at IS NULL)
  OR (revision_row.expires_at IS NOT NULL AND revision_row.expires_at<=clock_timestamp()) THEN
  RAISE EXCEPTION 'Memory retrieval items must bind current active revisions atomically' USING ERRCODE='23514'; END IF;
 PERFORM public.response_check_actor(retrieval_row.actor_token_id,current_setting('app.actor_kind',true));
 RETURN NEW;
END $$;

-- FUNCTION: memory_revision_complete()
CREATE FUNCTION public.memory_revision_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.memories WHERE id=NEW.memory_id AND current_revision_id=NEW.id AND revision=NEW.revision) THEN
  RAISE EXCEPTION 'New memory revision must become current atomically' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: memory_revision_gate()
CREATE FUNCTION public.memory_revision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE parent_row public.memories%ROWTYPE; prior public.memory_revisions%ROWTYPE;
 job_row public.jobs%ROWTYPE; event_row public.memory_feedback_events%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 IF NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(NEW.content),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Memory content hash mismatch' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope='library' FOR UPDATE;
 SELECT * INTO parent_row FROM public.memories WHERE id=NEW.memory_id FOR UPDATE;
 SELECT * INTO prior FROM public.memory_revisions WHERE memory_id=NEW.memory_id ORDER BY revision DESC LIMIT 1;
 IF parent_row.id IS NULL OR parent_row.deleted_at IS NOT NULL
  OR NEW.revision<>coalesce(prior.revision,0)+1
  OR (prior.id IS NOT NULL AND parent_row.current_revision_id IS DISTINCT FROM prior.id)
  OR (NEW.expires_at IS NOT NULL AND NEW.status IN ('candidate','active') AND NEW.expires_at<=clock_timestamp()) THEN
  RAISE EXCEPTION 'Stale memory revision or expiry' USING ERRCODE='23514'; END IF;
 IF (NEW.source->>'task_id')::uuid IS DISTINCT FROM NEW.task_id
  OR (NEW.source->>'card_id')::uuid IS DISTINCT FROM NEW.card_id
  OR (NEW.source->>'card_revision_id')::uuid IS DISTINCT FROM NEW.card_revision_id
  OR (NEW.source->>'feedback_event_id')::uuid IS DISTINCT FROM NEW.feedback_event_id
  OR (NEW.source->>'proposal_job_id')::uuid IS DISTINCT FROM NEW.proposal_job_id
  OR (NEW.source->>'proposal_run_id')::uuid IS DISTINCT FROM NEW.proposal_run_id
  OR NEW.source->>'platform_release_id' IS NOT NULL
  OR (prior.id IS NOT NULL AND NEW.source IS DISTINCT FROM prior.source)
  OR NEW.feedback_event_id IS DISTINCT FROM parent_row.source_feedback_event_id THEN
  RAISE EXCEPTION 'Memory provenance mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.decision IS NULL THEN
  IF NEW.status<>'candidate' OR (NEW.actor_kind='session' AND NOT public.actor_is_human())
   OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE id=NEW.actor_token_id AND scopes ? 'memory:write')) THEN
   RAISE EXCEPTION 'Memory proposal requires write permission' USING ERRCODE='42501'; END IF;
  IF prior.id IS NOT NULL AND NOT public.actor_is_human()
   AND (prior.status<>'candidate' OR NEW.actor_token_id IS DISTINCT FROM prior.actor_token_id
    OR EXISTS(SELECT 1 FROM public.memory_revisions WHERE memory_id=NEW.memory_id AND status='active')) THEN
   RAISE EXCEPTION 'Only an admin may change reviewed memory or another candidate' USING ERRCODE='42501'; END IF;
 ELSE
  PERFORM public.response_require_human();
  IF prior.id IS NULL OR NEW.content IS DISTINCT FROM prior.content
   OR NEW.content_sha256 IS DISTINCT FROM prior.content_sha256 OR NEW.expires_at IS DISTINCT FROM prior.expires_at
   OR NEW.normalized_text IS DISTINCT FROM prior.normalized_text OR NEW.normalized_tags IS DISTINCT FROM prior.normalized_tags
   OR (NEW.decision IN ('approve','reject') AND prior.status<>'candidate')
   OR (NEW.decision='disable' AND prior.status NOT IN ('candidate','active'))
   OR (NEW.decision='approve' AND (NEW.status<>'active' OR NEW.confirmed_at IS NULL))
   OR (NEW.decision<>'approve' AND NEW.status<>'disabled') THEN
   RAISE EXCEPTION 'Memory decision must bind the exact current candidate' USING ERRCODE='23514'; END IF;
  IF NEW.decision='approve' AND EXISTS(SELECT 1 FROM public.memories m JOIN public.memory_revisions r ON r.id=m.current_revision_id
   WHERE m.id<>NEW.memory_id AND m.scope=parent_row.scope AND m.deleted_at IS NULL AND r.status='active'
    AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp()) AND r.content->>'kind'=NEW.content->>'kind'
    AND r.content->>'conflict_key'=NEW.content->>'conflict_key') THEN
   RAISE EXCEPTION 'An active memory already owns this conflict key' USING ERRCODE='23505'; END IF;
 END IF;
 IF prior.id IS NULL THEN
  IF NEW.status<>'candidate' OR NEW.decision IS NOT NULL THEN RAISE EXCEPTION 'Memory starts as candidate' USING ERRCODE='23514'; END IF;
  IF NEW.source->>'origin'='system' THEN
   SELECT * INTO event_row FROM public.memory_feedback_events WHERE id=NEW.feedback_event_id;
   SELECT * INTO job_row FROM public.jobs WHERE id=NEW.proposal_job_id FOR UPDATE;
   IF NEW.actor_kind<>'worker' OR NEW.card_id IS NULL OR event_row.id IS NULL OR event_row.kind='card_confirmed'
    OR ROW(event_row.task_id,event_row.card_id,event_row.after_revision_id) IS DISTINCT FROM ROW(NEW.task_id,NEW.card_id,NEW.card_revision_id)
    OR job_row.kind IS DISTINCT FROM 'memory_candidate' OR job_row.status IS DISTINCT FROM 'running'
    OR job_row.task_id IS DISTINCT FROM NEW.task_id OR job_row.run_id IS DISTINCT FROM NEW.proposal_run_id
    OR job_row.document_id IS DISTINCT FROM event_row.document_id
    OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
    OR (job_row.result->'submission'->>'actor_token_id')::uuid IS DISTINCT FROM NEW.actor_token_id
    OR NOT coalesce(job_row.result->'submission'->'event_ids' ? NEW.feedback_event_id::text,false)
    OR job_row.result->'submission'->>'generator_version' IS DISTINCT FROM parent_row.generator_version THEN
    RAISE EXCEPTION 'Memory candidate requires its live authorized worker attempt' USING ERRCODE='42501'; END IF;
  ELSIF NEW.actor_kind NOT IN ('session','token') OR NEW.feedback_event_id IS NOT NULL OR NEW.proposal_job_id IS NOT NULL THEN
   RAISE EXCEPTION 'Human provenance cannot claim a system producer' USING ERRCODE='42501'; END IF;
 ELSIF NEW.actor_kind NOT IN ('session','token') THEN
  RAISE EXCEPTION 'Only a member may revise an existing memory' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: platform_clef_manage(text, jsonb)
CREATE FUNCTION public.platform_clef_manage(p_action text, p_body jsonb) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE v_prior jsonb; v_new jsonb; v_revision bigint;
  v_workers record; v_gateway record; v_credential record;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_body) IS DISTINCT FROM 'object'
    OR p_action IS NULL OR p_action NOT IN ('set','check') THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform-clef-config',0));
  SELECT settings INTO v_prior FROM public.clef_settings WHERE id;
  IF v_prior->>'revision' IS DISTINCT FROM p_body->>'expected_revision' THEN
    RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001';
  END IF;
  v_revision := COALESCE((v_prior->>'revision')::bigint,0)+1;
  IF p_action='set' THEN
    IF p_body-ARRAY['expected_revision','account_id','gateway_id','enabled',
        'workers_credential_id','gateway_credential_id'] <> '{}'::jsonb
      OR NOT COALESCE(p_body->>'account_id' ~ '^[a-f0-9]{32}$'
        AND p_body->>'gateway_id' ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
        AND jsonb_typeof(p_body->'enabled')='boolean'
        AND p_body->>'workers_credential_id'<>p_body->>'gateway_credential_id',false) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    SELECT id,revision INTO v_workers FROM public.platform_credentials
      WHERE id=(p_body->>'workers_credential_id')::uuid AND purpose='clef_workers_ai'
        AND provider='cloudflare' AND endpoint='https://gateway.ai.cloudflare.com' AND state<>'removed';
    SELECT id,revision INTO v_gateway FROM public.platform_credentials
      WHERE id=(p_body->>'gateway_credential_id')::uuid AND purpose='clef_gateway'
        AND provider='cloudflare' AND endpoint='https://api.cloudflare.com/client/v4' AND state<>'removed';
    IF v_workers.id IS NULL OR v_gateway.id IS NULL THEN
      RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001';
    END IF;
    v_new := (p_body-'expected_revision') || jsonb_build_object(
      'expected_revision',NULL,'revision',v_revision,
      'workers_credential_revision',v_workers.revision,'gateway_credential_revision',v_gateway.revision,
      'gateway_check',NULL,'updated_at',clock_timestamp());
  ELSE
    IF v_prior IS NULL OR p_body-ARRAY['expected_revision','gateway_check','check_error'] <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    -- A credential change between HTTP check and durable recording rejects the receipt.
    FOR v_credential IN SELECT id,revision FROM public.platform_credentials
      WHERE id IN ((v_prior->>'workers_credential_id')::uuid,(v_prior->>'gateway_credential_id')::uuid)
    LOOP
      IF v_credential.revision IS DISTINCT FROM
        (CASE WHEN v_credential.id=(v_prior->>'workers_credential_id')::uuid
          THEN (v_prior->>'workers_credential_revision')::bigint
          ELSE (v_prior->>'gateway_credential_revision')::bigint END) THEN
        RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001';
      END IF;
    END LOOP;
    IF p_body->'gateway_check' <> 'null'::jsonb AND NOT COALESCE(
      p_body->'gateway_check'->>'gateway_id'=v_prior->>'gateway_id'
      AND p_body->'gateway_check'->'authentication'='true'::jsonb
      AND p_body->'gateway_check'->'collect_logs'='false'::jsonb
      AND p_body->'gateway_check'->'logpush'='false'::jsonb
      AND p_body->'gateway_check'->'cache_ttl'='0'::jsonb
      AND p_body->'gateway_check'->'gateway_retries'='false'::jsonb
      AND (p_body->'gateway_check'->>'rate_limit_requests')::bigint>0
      AND (p_body->'gateway_check'->>'rate_limit_seconds')::bigint>0
      AND (p_body->'gateway_check'->>'checked_at')::timestamptz<=clock_timestamp(),false) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    v_new := v_prior || jsonb_build_object('revision',v_revision,
      'gateway_check',p_body->'gateway_check','updated_at',clock_timestamp());
  END IF;
  INSERT INTO public.clef_settings(id,settings,revision,updated_at)
  VALUES(true,v_new,v_revision,clock_timestamp())
  ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings,revision=EXCLUDED.revision,
    updated_at=EXCLUDED.updated_at;
  RETURN v_new;
END $_$;

-- FUNCTION: platform_clef_read()
CREATE FUNCTION public.platform_clef_read() RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
  SELECT settings FROM public.clef_settings WHERE id
$$;

-- FUNCTION: platform_clef_row_gate()
CREATE FUNCTION public.platform_clef_row_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Clef configuration requires platform connection' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END $$;

-- FUNCTION: platform_credential_cipher(uuid, boolean)
CREATE FUNCTION public.platform_credential_cipher(p_id uuid, p_allow_disabled boolean) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.state='removed' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='P0001'; END IF;
          IF c.state='disabled' AND NOT p_allow_disabled THEN
            RAISE EXCEPTION 'credential_disabled' USING ERRCODE='P0001'; END IF;
          RETURN to_jsonb(c);
        END $$;

-- FUNCTION: platform_credential_manage(text, jsonb)
CREATE FUNCTION public.platform_credential_manage(p_action text, p_body jsonb) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE
  c public.platform_credentials;
  v_id uuid; v_reason text; v_state text; v_action text;
  v_limit integer; v_items jsonb := '[]'; v_entry jsonb; v_created integer := 0;
  v_skipped integer := 0; v_dry boolean := false;
  v_next text; v_probe uuid; v_receipt public.credential_probes; v_outcome text; v_duration bigint;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
  IF jsonb_typeof(p_body) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF p_action='list' THEN
    v_limit := COALESCE((p_body->>'limit')::integer,100);
    IF v_limit NOT BETWEEN 1 AND 100 OR (p_body->>'state' IS NOT NULL AND
      p_body->>'state' NOT IN ('active','disabled','removed')) OR (p_body->>'purpose' IS NOT NULL AND
      p_body->>'purpose' NOT IN ('standalone_llm','vendor_search','clef_workers_ai','clef_gateway')) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(public.platform_credential_view(s.id) ORDER BY s.name),'[]') INTO v_items
      FROM (SELECT id,name FROM public.platform_credentials
        WHERE (p_body->>'state' IS NULL OR state=p_body->>'state')
          AND (p_body->>'purpose' IS NULL OR purpose=p_body->>'purpose')
          AND (p_body->>'after_name' IS NULL OR name>p_body->>'after_name')
        ORDER BY name LIMIT v_limit) s;
    IF jsonb_array_length(v_items)=v_limit AND EXISTS(SELECT FROM public.platform_credentials
      WHERE name>v_items->(v_limit-1)->>'name'
        AND (p_body->>'state' IS NULL OR state=p_body->>'state')
        AND (p_body->>'purpose' IS NULL OR purpose=p_body->>'purpose')) THEN
      v_next := v_items->(v_limit-1)->>'name'; END IF;
    RETURN jsonb_build_object('items',v_items,'next_after_name',v_next);
  ELSIF p_action='show' THEN
    v_id := (p_body->>'id')::uuid;
    IF NOT EXISTS(SELECT FROM public.platform_credentials WHERE id=v_id) THEN
      RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
    RETURN jsonb_build_object('credential',public.platform_credential_view(v_id));
  ELSIF p_action='import' THEN
    IF jsonb_typeof(p_body->'entries') IS DISTINCT FROM 'array'
      OR jsonb_array_length(p_body->'entries') NOT BETWEEN 1 AND 100 THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    v_dry := COALESCE((p_body->>'dry_run')::boolean,false);
    IF COALESCE((p_body->>'preflight')::boolean,false) THEN
      FOR v_entry IN SELECT value FROM jsonb_array_elements(p_body->'entries') LOOP
        SELECT * INTO c FROM public.platform_credentials WHERE name=v_entry->>'name';
        v_items := v_items || jsonb_build_array(jsonb_build_object('name',v_entry->>'name',
          'credential',public.platform_credential_view(c.id)));
      END LOOP;
      RETURN jsonb_build_object('items',v_items);
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('platform-credential-import',0));
    IF (SELECT count(*)<>count(DISTINCT value->>'name') FROM jsonb_array_elements(p_body->'entries')) THEN
      RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT value->>'purpose' FROM jsonb_array_elements(p_body->'entries')
      GROUP BY value->>'purpose' HAVING count(*)>1) THEN
      RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
    FOR v_entry IN SELECT value FROM jsonb_array_elements(p_body->'entries') LOOP
      SELECT * INTO c FROM public.platform_credentials WHERE name=v_entry->>'name' FOR UPDATE;
      IF c.id IS NOT NULL THEN
        IF c.state='removed' OR c.id IS DISTINCT FROM (v_entry->>'id')::uuid
          OR c.revision IS DISTINCT FROM COALESCE((v_entry->>'checked_revision')::bigint,
                                                 (v_entry->>'expected_revision')::bigint)
          OR NOT COALESCE((v_entry->>'same_secret')::boolean,(v_entry->>'same_value')::boolean,false)
          OR c.purpose IS DISTINCT FROM v_entry->>'purpose' OR c.provider IS DISTINCT FROM v_entry->>'provider'
          OR c.endpoint IS DISTINCT FROM v_entry->>'endpoint'
          OR (c.state='active') IS DISTINCT FROM COALESCE((v_entry->>'active')::boolean,false) THEN
          RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
        v_skipped := v_skipped+1;
        v_action := CASE WHEN v_dry THEN 'would_skip' ELSE 'skipped' END;
      ELSE
        IF v_dry THEN
          PERFORM public.platform_credential_validate(v_entry);
          IF EXISTS(SELECT FROM public.platform_credentials
            WHERE purpose=v_entry->>'purpose' AND state<>'removed') THEN
            RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
          v_action := 'would_create';
        ELSE
          PERFORM public.platform_credential_manage('create',v_entry||jsonb_build_object('reason','migration'));
          v_action := 'created';
        END IF;
        v_created := v_created+1;
      END IF;
      v_items := v_items || jsonb_build_array(jsonb_build_object('name',v_entry->>'name',
        'action',v_action,'credential_id',CASE WHEN v_dry AND c.id IS NULL THEN NULL
          ELSE (v_entry->>'id')::uuid END));
    END LOOP;
    RETURN jsonb_build_object('items',v_items,'dry_run',v_dry,'created',CASE WHEN v_dry THEN 0 ELSE v_created END,
      'skipped',v_skipped,'would_create',CASE WHEN v_dry THEN v_created ELSE 0 END);
  ELSIF p_action='create' THEN
    PERFORM public.platform_credential_validate(p_body);
    PERFORM pg_advisory_xact_lock(hashtextextended('platform-credential-import',0));
    v_id := (p_body->>'id')::uuid;
    v_reason := COALESCE(p_body->>'reason','setup');
    IF v_reason NOT IN ('setup','scheduled_rotation','vendor_revoked','incident','retired','migration') THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT FROM public.platform_credentials WHERE name=p_body->>'name') THEN
      RAISE EXCEPTION 'credential_name_conflict' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT FROM public.platform_credentials
      WHERE purpose=p_body->>'purpose' AND state<>'removed') THEN
      RAISE EXCEPTION 'credential_purpose_conflict' USING ERRCODE='P0001'; END IF;
    INSERT INTO public.platform_credentials(id,name,purpose,provider,endpoint,encrypted_key,
      fingerprint,last_four,state) VALUES(v_id,p_body->>'name',p_body->>'purpose',
      p_body->>'provider',p_body->>'endpoint',p_body->>'encrypted_key',p_body->>'fingerprint',
      p_body->>'last_four',CASE WHEN COALESCE((p_body->>'active')::boolean,false)
        THEN 'active' ELSE 'disabled' END) RETURNING * INTO c;
    RETURN jsonb_build_object('credential',public.platform_credential_view(c.id));
  ELSIF p_action='probe_finish' THEN
    v_probe := (p_body->>'probe_id')::uuid;
    PERFORM pg_advisory_xact_lock(hashtextextended('credential-probe:'||v_probe::text,0));
    SELECT * INTO v_receipt FROM public.credential_probes WHERE id=v_probe FOR UPDATE;
    IF v_receipt.id IS NULL OR v_receipt.finished_at IS NOT NULL THEN
      RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
    v_outcome := p_body->>'outcome';
    v_duration := (p_body->>'duration_ms')::bigint;
    IF v_outcome IS NULL OR v_outcome NOT IN ('passed','auth_failed','unsupported','timeout',
      'rate_limited','unavailable','interrupted') OR v_duration IS NULL OR v_duration<0
      OR COALESCE((p_body->>'retry_after_seconds')::integer,0) NOT BETWEEN 0 AND 3600 THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    IF v_receipt.started_at<clock_timestamp()-interval '30 seconds' THEN
      RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
    UPDATE public.credential_probes SET finished_at=clock_timestamp(),outcome=v_outcome WHERE id=v_probe;
    RETURN jsonb_build_object('probe',jsonb_build_object('probe_id',v_probe,
      'credential_id',v_receipt.credential_id,'tested_revision',v_receipt.tested_revision,
      'secret_version',v_receipt.secret_version,'outcome',v_outcome,
      'duration_ms',v_duration,'checked_at',clock_timestamp(),'proves','authentication_only',
      'retry_after_seconds',(p_body->>'retry_after_seconds')::integer));
  ELSIF p_action NOT IN ('replace','set_active','remove','probe_begin') OR p_action IS NULL THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  v_id := (p_body->>'id')::uuid;
  SELECT * INTO c FROM public.platform_credentials WHERE id=v_id FOR UPDATE;
  IF c.id IS NULL THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  IF c.revision IS DISTINCT FROM (p_body->>'expected_revision')::bigint THEN
    RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
  IF c.state='removed' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='P0001'; END IF;
  IF p_action='probe_begin' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('credential-probe-installation',0));
    IF (SELECT count(*) FROM public.credential_probes
      WHERE started_at>clock_timestamp()-interval '1 minute')>=10
      OR (SELECT count(*) FROM public.credential_probes WHERE credential_id=c.id
      AND started_at>clock_timestamp()-interval '1 minute')>=5 THEN
      RAISE EXCEPTION 'credential_probe_rate_limited' USING ERRCODE='P0001'; END IF;
    DELETE FROM public.credential_probes WHERE started_at<clock_timestamp()-interval '1 day';
    v_probe := gen_random_uuid();
    INSERT INTO public.credential_probes(id,credential_id,tested_revision,secret_version)
      VALUES(v_probe,c.id,c.revision,c.secret_version);
    RETURN jsonb_build_object('probe_id',v_probe,'credential_id',c.id,
      'tested_revision',c.revision,'secret_version',c.secret_version);
  END IF;
  v_reason := p_body->>'reason';
  IF v_reason IS NULL OR v_reason NOT IN ('setup','scheduled_rotation','vendor_revoked','incident','retired','migration')
    OR (p_action='remove' AND v_reason NOT IN ('vendor_revoked','incident','retired')) THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF p_action='replace' THEN
    UPDATE public.platform_credentials SET encrypted_key=p_body->>'encrypted_key',
      fingerprint=p_body->>'fingerprint',last_four=p_body->>'last_four',
      revision=revision+1,secret_version=secret_version+1,updated_at=clock_timestamp()
      WHERE id=v_id RETURNING * INTO c;
  ELSIF p_action='set_active' THEN
    IF jsonb_typeof(p_body->'active') IS DISTINCT FROM 'boolean' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    v_state := CASE WHEN (p_body->>'active')::boolean THEN 'active' ELSE 'disabled' END;
    IF v_state='active' AND c.revision IS DISTINCT FROM (p_body->>'checked_revision')::bigint THEN
      RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
    IF c.state<>v_state THEN
      UPDATE public.platform_credentials SET state=v_state,revision=revision+1,
        updated_at=clock_timestamp() WHERE id=v_id RETURNING * INTO c;
    END IF;
  ELSE
    UPDATE public.platform_credentials SET state='removed',encrypted_key=NULL,revision=revision+1,
      updated_at=clock_timestamp() WHERE id=v_id RETURNING * INTO c;
  END IF;
  RETURN jsonb_build_object('credential',public.platform_credential_view(c.id));
EXCEPTION WHEN invalid_text_representation OR not_null_violation OR check_violation
  OR string_data_right_truncation OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
WHEN unique_violation THEN
  RAISE EXCEPTION 'credential_name_conflict' USING ERRCODE='P0001';
END $_$;

-- FUNCTION: platform_credential_readiness(jsonb)
CREATE FUNCTION public.platform_credential_readiness(p_target jsonb) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF p_target->>'kind'='service' AND p_target->>'service' IN ('vendor_search','standalone_llm','clef_workers_ai','clef_gateway') THEN
            IF p_target->>'credential_id' IS NOT NULL THEN
              SELECT * INTO c FROM public.platform_credentials WHERE id=(p_target->>'credential_id')::uuid;
              IF c.id IS NOT NULL AND c.purpose<>p_target->>'service' THEN
                RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
            ELSE
              SELECT * INTO c FROM public.platform_credentials
                WHERE purpose=p_target->>'service' AND state<>'removed';
            END IF;
          ELSE RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          RETURN jsonb_build_object('credential_id',c.id,'provider',c.provider,'endpoint',c.endpoint,
            'revision',c.revision,'state',c.state,'configured',COALESCE(c.state='active',false));
        END $$;

-- FUNCTION: platform_credential_resolve_human(uuid, bigint, text)
CREATE FUNCTION public.platform_credential_resolve_human(p_id uuid, p_revision bigint, p_purpose text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF NOT public.actor_is_human() THEN
            RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
          IF p_purpose IS NULL OR p_purpose NOT IN ('import','activate') THEN
            RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
          IF c.revision IS DISTINCT FROM p_revision THEN
            RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,true);
        END $$;

-- FUNCTION: platform_credential_resolve_probe(uuid)
CREATE FUNCTION public.platform_credential_resolve_probe(p_probe uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.credential_probes; c public.platform_credentials;
BEGIN
  SELECT * INTO a FROM public.credential_probes WHERE id=p_probe;
  IF a.id IS NULL OR a.started_at<clock_timestamp()-interval '30 seconds' OR a.finished_at IS NOT NULL THEN
    RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
  SELECT * INTO c FROM public.platform_credentials WHERE id=a.credential_id;
  IF c.revision IS DISTINCT FROM a.tested_revision THEN
    RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
  RETURN public.platform_credential_cipher(c.id,true);
END $$;

-- FUNCTION: platform_credential_resolve_service(text, uuid)
CREATE FUNCTION public.platform_credential_resolve_service(p_service text, p_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF p_service NOT IN ('vendor_search','standalone_llm','clef_workers_ai','clef_gateway') OR p_service IS NULL THEN
            RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.purpose<>p_service THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,false);
        END $$;

-- FUNCTION: platform_credential_rewrap(uuid, text, text)
CREATE FUNCTION public.platform_credential_rewrap(p_id uuid, p_old text, p_new text) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE c public.platform_credentials;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
  IF current_user<>pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
    WHERE oid='public.platform_credentials'::regclass)) THEN
    RAISE EXCEPTION 'owner maintenance required' USING ERRCODE='42501'; END IF;
  IF p_new IS NULL OR length(p_new) NOT BETWEEN 100 AND 16384 THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  SELECT * INTO c FROM public.platform_credentials WHERE id=p_id FOR UPDATE;
  IF c.id IS NULL OR c.state='removed' OR c.encrypted_key IS DISTINCT FROM p_old THEN RETURN false; END IF;
  IF p_old=p_new THEN RETURN true; END IF;
  UPDATE public.platform_credentials SET encrypted_key=p_new WHERE id=p_id;
  RETURN true;
END $_$;

-- FUNCTION: platform_credential_row_gate()
CREATE FUNCTION public.platform_credential_row_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
        BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='42501'; END IF;
          IF TG_OP='UPDATE' AND (
            NEW.id IS DISTINCT FROM OLD.id OR NEW.name IS DISTINCT FROM OLD.name
            OR NEW.purpose IS DISTINCT FROM OLD.purpose OR NEW.provider IS DISTINCT FROM OLD.provider
            OR NEW.endpoint IS DISTINCT FROM OLD.endpoint OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NEW.envelope_version IS DISTINCT FROM OLD.envelope_version OR OLD.state='removed'
          ) THEN RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$;

-- FUNCTION: platform_credential_validate(jsonb)
CREATE FUNCTION public.platform_credential_validate(p jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
        BEGIN
          IF NOT COALESCE(
            (p->>'id')::uuid IS NOT NULL AND p->>'name' ~ '^[a-z0-9_]{1,40}$'
            AND p->>'purpose' IN ('standalone_llm','vendor_search','clef_workers_ai','clef_gateway')
            AND p->>'provider' IN ('anthropic','openai','perplexity','cloudflare')
            AND length(p->>'endpoint') BETWEEN 8 AND 300
            AND p->>'endpoint' ~ '^https://(\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*)(/[^[:space:]?#%\\]*)?$'
            AND p->>'endpoint' !~ '/$' AND p->>'endpoint' !~ '[[:cntrl:]]'
            AND length(p->>'encrypted_key') BETWEEN 100 AND 16384
            AND p->>'fingerprint' ~ '^sha256:[a-f0-9]{16}$'
            AND p->>'last_four' ~ '^[!-~]{4}$'
            AND (NOT p ? 'active' OR jsonb_typeof(p->'active')='boolean')
            AND ((p->>'purpose'='vendor_search' AND p->>'provider'='perplexity'
              AND p->>'endpoint'='https://api.perplexity.ai')
              OR (p->>'purpose'='standalone_llm' AND p->>'provider' IN ('anthropic','openai'))
              OR (p->>'purpose'='clef_workers_ai' AND p->>'provider'='cloudflare'
                AND p->>'endpoint'='https://gateway.ai.cloudflare.com')
              OR (p->>'purpose'='clef_gateway' AND p->>'provider'='cloudflare'
                AND p->>'endpoint'='https://api.cloudflare.com/client/v4')),false)
          THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
        END $_$;

-- FUNCTION: platform_credential_view(uuid)
CREATE FUNCTION public.platform_credential_view(p_id uuid) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
          SELECT jsonb_build_object('id',c.id,'name',c.name,'purpose',c.purpose,'provider',c.provider,
            'endpoint',c.endpoint,'fingerprint',c.fingerprint,'last_four',c.last_four,
            'state',c.state,'revision',c.revision,'secret_version',c.secret_version,
            'created_at',c.created_at,'updated_at',c.updated_at,
            'consumers', jsonb_build_array(jsonb_build_object('kind','service','service',c.purpose,
                'selected',c.state<>'removed')))
          FROM public.platform_credentials c WHERE c.id=p_id
        $$;

-- FUNCTION: platform_trust_anchor_add(text, bytea, text, text, text, timestamp with time zone, timestamp with time zone)
CREATE FUNCTION public.platform_trust_anchor_add(p_label text, p_der bytea, p_fingerprint text, p_subject text, p_issuer text, p_before timestamp with time zone, p_after timestamp with time zone) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.platform_trust_anchors%ROWTYPE; inserted boolean:=false;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
 IF p_fingerprint<>encode(sha256(p_der),'hex') THEN
  RAISE EXCEPTION 'Invalid trust anchor identity' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(862062001);
 IF (SELECT count(*) FROM public.platform_trust_anchors)>=128 AND
 NOT EXISTS(SELECT 1 FROM public.platform_trust_anchors WHERE fingerprint_sha256=p_fingerprint) THEN
  RAISE EXCEPTION 'Trust anchor capacity exceeded' USING ERRCODE='23514', CONSTRAINT='platform_trust_anchor_capacity'; END IF;
 INSERT INTO public.platform_trust_anchors(id,label,certificate_der,fingerprint_sha256,subject,issuer,
 not_before,not_after,created_at,enabled,revision)
 VALUES(gen_random_uuid(),p_label,p_der,p_fingerprint,p_subject,p_issuer,p_before,p_after,clock_timestamp(),true,1)
 ON CONFLICT(fingerprint_sha256) DO NOTHING RETURNING * INTO a;
 inserted:=FOUND;
 IF NOT inserted THEN
  SELECT * INTO STRICT a FROM public.platform_trust_anchors WHERE fingerprint_sha256=p_fingerprint;
 END IF;
 RETURN (to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca',true,'created',inserted);
END $$;

-- FUNCTION: platform_trust_anchor_disable(uuid)
CREATE FUNCTION public.platform_trust_anchor_disable(p_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.platform_trust_anchors%ROWTYPE;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Platform actor required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(862062001);
 SELECT * INTO a FROM public.platform_trust_anchors WHERE id=p_id FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF a.enabled THEN
  UPDATE public.platform_trust_anchors SET enabled=false,revision=revision+1,
  disabled_at=clock_timestamp() WHERE id=p_id RETURNING * INTO a;
 END IF;
 RETURN (to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca', true);
END $$;

-- FUNCTION: platform_trust_anchor_list()
CREATE FUNCTION public.platform_trust_anchor_list() RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT COALESCE(jsonb_agg((to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca', true)
 ORDER BY a.created_at,a.id),'[]'::jsonb) FROM public.platform_trust_anchors a
$$;

-- FUNCTION: platform_trust_anchor_snapshot()
CREATE FUNCTION public.platform_trust_anchor_snapshot() RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
 WITH snapshot AS (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('id',a.id,'fingerprint_sha256',a.fingerprint_sha256,
 'certificate_der_base64',replace(encode(a.certificate_der,'base64'),E'\n',''),
 'revision',a.revision) ORDER BY a.fingerprint_sha256),'[]'::jsonb) AS anchors
 FROM public.platform_trust_anchors a WHERE a.enabled)
 SELECT jsonb_build_object('sha256',encode(sha256(convert_to(anchors::text,'UTF8')),'hex'),'anchors',anchors)
 FROM snapshot
$$;

-- FUNCTION: produce_task_events()
CREATE FUNCTION public.produce_task_events() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE changed_task record; j jsonb; q text; total integer; payload jsonb; source uuid;
BEGIN
 IF TG_OP='DELETE' THEN q='SELECT to_jsonb(t) AS value FROM old_rows t';
 ELSE q='SELECT to_jsonb(t) AS value FROM new_rows t'; END IF;
 IF TG_TABLE_NAME='jobs' AND TG_OP='UPDATE' THEN
  q='SELECT to_jsonb(n) AS value FROM new_rows n JOIN old_rows o ON n.id=o.id WHERE (n.status,n.run_id,n.attempts,n.result,n.error,n.cache_key) IS DISTINCT FROM (o.status,o.run_id,o.attempts,o.result,o.error,o.cache_key)';
 END IF;
 -- Resource revisions fan out through the pinned task selections. Transition rows
 -- are statement-local, and all affected heads are locked in stable task order.
 IF TG_ARGV[0]='library' THEN
  q='SELECT DISTINCT s.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.'
    ||quote_ident(TG_ARGV[1])||' s ON s.'
    ||quote_ident(TG_ARGV[2])||'=(x.value->>'||quote_literal(TG_ARGV[3])||')::uuid ORDER BY s.task_id';
 ELSIF TG_ARGV[0]='derived_job' THEN
  q='SELECT DISTINCT p.task_id,jsonb_build_object(''source_id'',p.'||quote_ident(TG_ARGV[3])||') AS value FROM ('||q||') x JOIN public.'||quote_ident(TG_ARGV[1])||' p ON p.id=(x.value->>'||quote_literal(TG_ARGV[2])||')::uuid ORDER BY p.task_id';
 ELSIF TG_ARGV[0]='card_revision' THEN
  q='SELECT DISTINCT c.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.response_cards c ON c.id=(x.value->>''card_id'')::uuid ORDER BY c.task_id';
 ELSIF TG_ARGV[0]='card_link' THEN
  q='SELECT DISTINCT c.task_id,NULL::jsonb AS value FROM ('||q||') x JOIN public.response_card_revisions v ON v.id=(x.value->>''revision_id'')::uuid JOIN public.response_cards c ON c.id=v.card_id ORDER BY c.task_id';
 ELSIF TG_TABLE_NAME='exports' THEN
  q='SELECT DISTINCT r.task_id,jsonb_build_object(''source_id'',r.render_job_id) AS value FROM ('||q||') x JOIN public.export_runs r ON r.id=(x.value->>''run_id'')::uuid ORDER BY r.task_id';
 ELSIF TG_TABLE_NAME='jobs' THEN
  q='SELECT (value->>''task_id'')::uuid task_id,value FROM ('||q||') x WHERE value->>''task_id'' IS NOT NULL ORDER BY task_id,value->>''id''';
 ELSE
  q='SELECT DISTINCT (value->>'||quote_literal(TG_ARGV[0])||')::uuid task_id,jsonb_build_object(''source_id'',value->>'||quote_literal(coalesce(TG_ARGV[1],''))||') AS value FROM ('||q||') x WHERE value->>'||quote_literal(TG_ARGV[0])||' IS NOT NULL ORDER BY task_id';
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
  ELSE
   payload=jsonb_build_object('type','board_changed','invalidate_all',true,'requirement_ids','[]'::jsonb,'card_ids','[]'::jsonb);
  END IF;
  PERFORM public.append_task_event(changed_task.task_id,payload->>'type',payload,source);
 END LOOP;
 RETURN NULL;
END $_$;

-- FUNCTION: provider_credential_rewrap(uuid, text, text)
CREATE FUNCTION public.provider_credential_rewrap(p_id uuid, p_old text, p_new text) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE changed bigint; previous_setting text;
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human actor required' USING ERRCODE='42501'; END IF;
  IF current_user<>pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
    WHERE oid='public.provider_configs'::regclass)) THEN
    RAISE EXCEPTION 'owner maintenance required' USING ERRCODE='42501'; END IF;
  IF p_new IS NULL OR length(p_new) NOT BETWEEN 100 AND 16384 THEN
    RAISE EXCEPTION 'invalid credential rewrap' USING ERRCODE='23514'; END IF;
  previous_setting := current_setting('app.provider_rewrap',true);
  PERFORM set_config('app.provider_rewrap','on',true);
  UPDATE public.provider_configs SET encrypted_key=p_new
    WHERE id=p_id AND encrypted_key=p_old;
  GET DIAGNOSTICS changed=ROW_COUNT;
  PERFORM set_config('app.provider_rewrap',COALESCE(previous_setting,''),true);
  RETURN changed=1;
END $$;

-- FUNCTION: provider_job_gate()
CREATE FUNCTION public.provider_job_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF TG_OP='UPDATE' AND (NEW.provider_config_id IS DISTINCT FROM OLD.provider_config_id
                OR NEW.provider_identity IS DISTINCT FROM OLD.provider_identity
                OR NEW.kind IS DISTINCT FROM OLD.kind) THEN
                RAISE EXCEPTION 'Job model identity is immutable' USING ERRCODE='42501';
            END IF;
            IF TG_OP='INSERT' AND NEW.kind='provider_test' THEN
                PERFORM public.provider_require_admin();
            END IF;
            RETURN NEW;
        END $$;

-- FUNCTION: provider_require_admin()
CREATE FUNCTION public.provider_require_admin() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Provider configuration requires a human org admin' USING ERRCODE='42501';
  END IF;
END $$;

-- FUNCTION: provider_revision_gate()
CREATE FUNCTION public.provider_revision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE previous integer;
BEGIN
  IF TG_OP='UPDATE' AND current_user=pg_catalog.pg_get_userbyid((
    SELECT relowner FROM pg_catalog.pg_class WHERE oid='public.provider_configs'::regclass))
    AND current_setting('app.provider_rewrap',true)='on'
    AND public.actor_is_human() AND NEW.encrypted_key IS NOT NULL
    AND (to_jsonb(NEW)-'encrypted_key')=(to_jsonb(OLD)-'encrypted_key') THEN
    RETURN NEW;
  END IF;
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'Provider revisions are immutable' USING ERRCODE='42501'; END IF;
  PERFORM public.provider_require_admin();
  PERFORM pg_advisory_xact_lock(hashtextextended('provider',0));
  SELECT COALESCE(max(revision),0) INTO previous FROM public.provider_configs
    WHERE capability=NEW.capability AND id<>NEW.id;
  IF NEW.revision<>previous+1 THEN
    RAISE EXCEPTION 'Provider revision conflict' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;

-- FUNCTION: provider_usage_gate()
CREATE FUNCTION public.provider_usage_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE cfg public.provider_configs; parent public.jobs;
        BEGIN
            IF NEW.job_id IS NOT NULL THEN
                SELECT * INTO parent FROM public.jobs WHERE id=NEW.job_id;
                IF NEW.task_id IS DISTINCT FROM parent.task_id
                    OR (NEW.provider_config_id IS DISTINCT FROM parent.provider_config_id
                        AND NOT (parent.kind = 'bid_review' AND NEW.provider_config_id IS NULL
                                 AND NEW.provider = 'cloudflare' AND NEW.model = '@cf/cloudflare/clef')) THEN
                    RAISE EXCEPTION 'Usage model or task differs from job' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.task_id IS NULL AND (parent.id IS NULL OR parent.kind <> 'provider_test') THEN
                RAISE EXCEPTION 'Taskless usage requires a provider test' USING ERRCODE='23514';
            END IF;
            IF NEW.provider_config_id IS NOT NULL THEN
                SELECT * INTO cfg FROM public.provider_configs
                    WHERE id=NEW.provider_config_id;
                IF cfg.id IS NULL THEN
                    RAISE EXCEPTION 'Usage model source differs from configuration' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END $$;

-- FUNCTION: task_archived_guard()
CREATE FUNCTION public.task_archived_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;

-- FUNCTION: task_events_immutable()
CREATE FUNCTION public.task_events_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' AND current_user=pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
   WHERE oid='public.task_event_heads'::regclass)) AND EXISTS(
   SELECT 1 FROM public.task_event_heads h WHERE h.task_id=OLD.task_id
    AND (OLD.seq<=h.last_seq-50000 OR OLD.created_at<clock_timestamp()-interval '7 days')) THEN
  RETURN OLD;
 END IF;
 RAISE EXCEPTION 'task events are immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: task_human_content_guard()
CREATE FUNCTION public.task_human_content_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE tid uuid; domain_value text; scope_value text; decision boolean:=true; human_only boolean:=true;
        previous public.response_card_revisions;
BEGIN
 IF TG_TABLE_NAME='response_card_revisions' THEN
   SELECT task_id INTO tid FROM public.response_cards WHERE id=NEW.card_id;
   SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
   decision:=NEW.state IN ('confirmed','rejected','needs_material') OR (previous.state='confirmed' AND NEW.state='draft')
     OR (NEW.disposition_at IS NOT NULL AND (NEW.disposition,NEW.disposition_at) IS DISTINCT FROM (previous.disposition,previous.disposition_at));
   decision:=coalesce(decision,false);

   domain_value:=CASE WHEN decision THEN NEW.review_domain ELSE NULL END;
   IF NEW.state='confirmed' THEN
    PERFORM public.task_write_authority(tid,'evidence:confirm',NULL,true,true);
   ELSIF NEW.state=previous.state AND NEW.state IN ('draft','rejected','needs_material')
    AND NEW.disposition IS NOT NULL AND (NEW.disposition,NEW.disposition_at)
     IS DISTINCT FROM (previous.disposition,previous.disposition_at) THEN
    PERFORM public.task_write_authority(tid,'evidence:confirm',NULL,true,true);
    decision:=true;
   END IF;

   scope_value:=CASE WHEN decision THEN 'evidence:confirm' WHEN NEW.actor_kind='worker' THEN 'card:generate' ELSE 'card:write' END;
   human_only:=decision;
 ELSIF TG_TABLE_NAME='evidence' THEN
   IF NEW.confirmed_at IS NULL OR (TG_OP='UPDATE' AND NEW.confirmed_at IS NOT DISTINCT FROM OLD.confirmed_at) THEN RETURN NEW; END IF;
   tid:=NEW.task_id;
   SELECT v.review_domain INTO domain_value FROM public.response_cards c JOIN public.response_card_revisions v
     ON v.id=c.current_revision_id WHERE c.id=NEW.card_id;

   PERFORM public.task_write_authority(tid,'evidence:confirm',NULL,true,true);
   scope_value:='evidence:confirm';
 ELSIF TG_TABLE_NAME='check_decisions' THEN

   tid:=NEW.task_id; scope_value:='check:decide';
   SELECT review_domain INTO domain_value FROM public.check_findings WHERE id=NEW.finding_id;
 ELSIF TG_TABLE_NAME='prototype_evidence_decisions' THEN
   tid:=NEW.task_id; scope_value:='evidence:confirm';
   SELECT review_domain INTO domain_value FROM public.response_card_revisions WHERE id=NEW.card_revision_id;
 ELSIF TG_TABLE_NAME='prototype_decision_batches' THEN
   tid:=NEW.task_id; scope_value:='evidence:confirm';
 ELSE
   tid:=NEW.task_id; scope_value:='score:rubric:review';
 END IF;
 PERFORM public.task_write_authority(tid,scope_value,domain_value,decision,human_only);
 RETURN NEW;
END $$;

-- FUNCTION: task_job_lifecycle_guard()
CREATE FUNCTION public.task_job_lifecycle_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 RETURN NEW;
END $$;

-- FUNCTION: task_write_authority(uuid, text, text, boolean, boolean)
CREATE FUNCTION public.task_write_authority(p_task uuid, p_scope text, p_domain text, p_decision boolean, p_human boolean) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE kind text:=current_setting('app.actor_kind',true);
        token uuid:=nullif(current_setting('app.actor_token_id',true),'')::uuid;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'task is not active' USING ERRCODE='42501'; END IF;
 IF kind IS NULL OR kind NOT IN ('session','token','agent','worker','system')
   OR (kind='token' AND token IS NULL)
   OR (token IS NOT NULL AND NOT EXISTS(SELECT FROM public.api_tokens
       WHERE id=token AND NOT revoked AND expires_at>clock_timestamp()))
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? p_scope THEN
   RAISE EXCEPTION 'task mutation authority required' USING ERRCODE='42501'; END IF;
 IF p_human AND NOT public.actor_is_human() THEN
   RAISE EXCEPTION 'human task decision required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: usage_admission_guard()
CREATE FUNCTION public.usage_admission_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE c public.vendor_calls;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'usage is immutable' USING ERRCODE='42501'; END IF;
  IF NEW.call_id IS NULL THEN RAISE EXCEPTION 'usage requires admitted call' USING ERRCODE='23514'; END IF;
  SELECT * INTO c FROM public.vendor_calls WHERE job_id=NEW.job_id
    AND run_id=NEW.run_id AND id=NEW.call_id;
  IF c.id IS NULL OR c.state='not_sent' OR (NEW.task_id,NEW.capability)
    IS DISTINCT FROM (c.task_id,c.capability) THEN
    RAISE EXCEPTION 'usage must match admission binding' USING ERRCODE='23514'; END IF;
  IF c.provider_identity<>'{}'::jsonb AND (
    NEW.provider_config_id IS DISTINCT FROM NULLIF(c.provider_identity->>'provider_config_id','')::uuid
    OR NEW.provider IS DISTINCT FROM c.provider_identity->>'provider'
    OR NEW.version IS DISTINCT FROM c.provider_identity->>'version') THEN
    RAISE EXCEPTION 'usage provider identity differs from admission' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;

-- FUNCTION: vendor_call_guard()
CREATE FUNCTION public.vendor_call_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE j public.jobs;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'call history is immutable' USING ERRCODE='42501'; END IF;
  SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
  IF j.id IS NULL OR NEW.task_id IS DISTINCT FROM j.task_id THEN
    RAISE EXCEPTION 'call must belong to job task' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'pending'
      OR (NEW.task_id IS NULL AND j.kind<>'provider_test') THEN
      RAISE EXCEPTION 'call requires authenticated job admission' USING ERRCODE='23514'; END IF;
    IF j.status<>'running' OR NEW.run_id IS DISTINCT FROM j.run_id
      OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() THEN
      RAISE EXCEPTION 'call requires live job execution lease' USING ERRCODE='23514'; END IF;
    IF NEW.provider_identity->>'capability' IS DISTINCT FROM NEW.capability
      OR NEW.provider_identity->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
      OR COALESCE(NEW.provider_identity->>'provider','')=''
      OR COALESCE(NEW.provider_identity->>'version','')='' THEN
      RAISE EXCEPTION 'call provider snapshot mismatch' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT FROM public.vendor_calls prior_call WHERE prior_call.job_id=NEW.job_id AND prior_call.request_sha256=NEW.request_sha256
      AND ((j.agent_session_id IS NOT NULL OR NEW.capability='browser')
           AND prior_call.state IN ('pending','unknown')
        OR j.agent_session_id IS NULL AND NEW.capability<>'browser'
           AND prior_call.state='pending' AND prior_call.run_id<>NEW.run_id)) THEN
      RAISE EXCEPTION 'unresolved call cannot be dispatched again' USING ERRCODE='23514'; END IF;
  ELSE
    IF NEW.state='not_sent' AND OLD.state NOT IN ('pending','not_sent') THEN
      RAISE EXCEPTION 'unknown dispatch cannot become unsent' USING ERRCODE='23514'; END IF;
    IF (NEW.id,NEW.job_id,NEW.run_id,NEW.task_id,NEW.capability,
        NEW.request_sha256,NEW.provider_identity,NEW.created_at)
      IS DISTINCT FROM (OLD.id,OLD.job_id,OLD.run_id,OLD.task_id,OLD.capability,
        OLD.request_sha256,OLD.provider_identity,OLD.created_at)
      OR (OLD.state IN ('completed','not_sent') AND NEW IS DISTINCT FROM OLD)
      OR (OLD.state='unknown' AND NEW.state='pending') THEN
      RAISE EXCEPTION 'admitted call binding is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: vendor_call_usage_consistency()
CREATE FUNCTION public.vendor_call_usage_consistency() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE c public.vendor_calls; n bigint;
BEGIN
  IF TG_TABLE_NAME='vendor_calls' THEN
    SELECT * INTO c FROM public.vendor_calls WHERE id=NEW.id;
  ELSE
    SELECT * INTO c FROM public.vendor_calls WHERE id=NEW.call_id;
  END IF;
  SELECT count(*) INTO n FROM public.usage_records
    WHERE job_id=c.job_id AND run_id=c.run_id AND call_id=c.id;
  IF (c.state='completed' AND n<>1) OR (c.state<>'completed' AND n<>0) THEN
    RAISE EXCEPTION 'completed call requires exactly one matching usage' USING ERRCODE='23514'; END IF;
  RETURN NULL;
END $$;
