"""Package imports and the scoped authority their job writes under (ADR 0014).

A `package_imports` row binds one import to its job, the importing member and
the task it creates. `package_import_task()` returns that task only inside the
import job's own execution: the transaction names the import, the job runs
with the bound run and an unexpired lease, and the import is `importing`.
Everywhere else it returns NULL, so the guards below behave as before.

The shared actor helpers admit the import's task without a lease or a human
actor; the checks that follow them in each guard still run.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
CREATE TABLE public.package_imports (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL UNIQUE REFERENCES public.jobs(id) ON DELETE CASCADE,
    member_id uuid REFERENCES public.members(id),
    package_sha256 character(64) NOT NULL,
    task_id uuid,
    state character varying(12) NOT NULL DEFAULT 'queued',
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT package_import_state CHECK (
        (state)::text = ANY (ARRAY['queued', 'importing', 'succeeded', 'failed'])),
    CONSTRAINT package_import_hash CHECK (package_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT package_import_task_bound CHECK (
        task_id IS NOT NULL OR (state)::text = ANY (ARRAY['queued', 'failed']))
);
-- A repeat of a package by the same member returns the first import, unless it failed.
CREATE UNIQUE INDEX package_import_once ON public.package_imports
    USING btree (package_sha256, member_id) NULLS NOT DISTINCT
    WHERE ((state)::text <> 'failed'::text);
GRANT SELECT,INSERT,UPDATE ON TABLE public.package_imports TO bid_app;

CREATE FUNCTION public.package_import_task() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
SELECT i.task_id FROM public.package_imports i JOIN public.jobs j ON j.id = i.job_id
WHERE i.id = nullif(current_setting('app.package_import_id', true), '')::uuid
  AND i.state = 'importing'
  AND current_setting('app.actor_kind', true) = 'worker'
  AND j.id = nullif(current_setting('app.execution_job_id', true), '')::uuid
  AND j.run_id = nullif(current_setting('app.execution_run_id', true), '')::uuid
  AND j.kind = 'package_import' AND j.status = 'running'
  AND j.lease_until > clock_timestamp()
$$;

-- True for a row of the task the running import creates.
CREATE FUNCTION public.package_imported(p_task uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT p_task IS NOT NULL AND p_task IS NOT DISTINCT FROM public.package_import_task() $$;

CREATE OR REPLACE FUNCTION public.task_write_authority(p_task uuid, p_scope text, p_domain text, p_decision boolean, p_human boolean)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE kind text:=current_setting('app.actor_kind',true);
        token uuid:=nullif(current_setting('app.actor_token_id',true),'')::uuid;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'task is not active' USING ERRCODE='42501'; END IF;
 IF public.package_imported(p_task) THEN RETURN; END IF;
 IF kind IS NULL OR kind NOT IN ('session','token','agent','worker','system')
   OR (kind='token' AND token IS NULL)
   OR (token IS NOT NULL AND NOT EXISTS(SELECT FROM public.api_tokens
       WHERE id=token AND NOT revoked AND expires_at>clock_timestamp()))
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? p_scope THEN
   RAISE EXCEPTION 'task mutation authority required' USING ERRCODE='42501'; END IF;
 IF p_human AND NOT public.actor_is_human() THEN
   RAISE EXCEPTION 'human task decision required' USING ERRCODE='42501'; END IF;
END $function$;

-- Imported rows keep the actor kind they were written with; their tokens are cleared.
CREATE OR REPLACE FUNCTION public.response_check_actor(p_token uuid, p_kind text)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
    BEGIN
        IF p_token IS NULL AND public.package_import_task() IS NOT NULL THEN RETURN; END IF;
        IF p_kind IS DISTINCT FROM current_setting('app.actor_kind',true)
            OR p_token IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid THEN
            RAISE EXCEPTION 'Actor context mismatch' USING ERRCODE='42501';
        END IF;
        IF (p_kind='session' AND p_token IS NOT NULL) OR (p_kind='token' AND p_token IS NULL)
          OR (p_token IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.api_tokens t
             WHERE t.id=p_token
             AND NOT t.revoked AND t.expires_at > CURRENT_TIMESTAMP)) THEN
            RAISE EXCEPTION 'Actor token mismatch' USING ERRCODE='42501';
        END IF;
    END $function$;

-- Library rows an import creates have no task; task rows are bound below.
CREATE OR REPLACE FUNCTION public.attachment_human()
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF public.package_import_task() IS NOT NULL THEN RETURN; END IF;
 IF NOT public.actor_is_human() THEN
  RAISE EXCEPTION 'Human attachment authority required' USING ERRCODE='42501'; END IF;
END $function$;

CREATE OR REPLACE FUNCTION public.attachment_task_writer(p_task uuid)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE imported uuid:=public.package_import_task();
BEGIN
 IF imported IS NOT NULL AND p_task IS DISTINCT FROM imported THEN
  RAISE EXCEPTION 'Active task contributor required' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human();
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Active task contributor required' USING ERRCODE='42501'; END IF;
END $function$;

CREATE OR REPLACE FUNCTION public.requirement_human_authority(p_task uuid, p_scope text)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF p_scope IS NULL OR p_scope NOT IN ('req:confirm','req:manual') OR (NOT public.actor_is_human()
    AND p_task IS DISTINCT FROM public.package_import_task()) THEN
  RAISE EXCEPTION 'requirement human role required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_task,p_scope,NULL,false,true);
END $function$;

CREATE OR REPLACE FUNCTION public.requirement_seed_authority(p_task uuid, p_job uuid, p_manual boolean)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE j public.jobs;
BEGIN
 IF p_manual THEN PERFORM public.requirement_human_authority(p_task,'req:manual'); RETURN; END IF;
 IF public.actor_is_human() THEN
 PERFORM public.requirement_human_authority(p_task,'req:confirm'); RETURN; END IF;
 SELECT * INTO j FROM public.jobs WHERE id=p_job AND task_id=p_task AND kind='extract';
 IF public.package_imported(p_task) THEN
  IF j.id IS NULL OR j.status <> 'succeeded' THEN
   RAISE EXCEPTION 'extract publication fence required' USING ERRCODE='42501'; END IF;
  RETURN;
 END IF;
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM nullif(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM nullif(current_setting('app.execution_run_id',true),'')::uuid
 OR j.run_id IS NULL OR j.status NOT IN ('running','succeeded')
 THEN RAISE EXCEPTION 'extract publication fence required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_task,'req:extract',NULL,false,false);
END $function$;

-- An imported job keeps its recorded actor kind, without a token or scopes.
CREATE OR REPLACE FUNCTION public.job_actor_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
        kind text:=NULLIF(current_setting('app.actor_kind',true),'');
BEGIN
  IF TG_OP='INSERT' AND public.package_imported(NEW.task_id) THEN
    IF NEW.actor_token_id IS NOT NULL OR NEW.status NOT IN ('succeeded','failed','cancelled') THEN
      RAISE EXCEPTION 'imported job must be final and tokenless' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.actor_scopes) IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'imported job scopes must be a list' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
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
END $function$;

CREATE OR REPLACE FUNCTION public.agent_origin_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
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
 -- An imported job keeps the origin it was exported with; agent origins never travel.
 IF TG_TABLE_NAME='jobs' AND TG_OP='INSERT' THEN
   IF public.package_imported((to_jsonb(NEW)->>'task_id')::uuid) THEN
     IF NEW.initiated_by NOT IN ('human','legacy_unknown') OR NEW.agent_principal_id IS NOT NULL
         OR NEW.agent_session_id IS NOT NULL OR NEW.agent_step_id IS NOT NULL THEN
       RAISE EXCEPTION 'imported job origin must be human or unknown' USING ERRCODE='23514'; END IF;
     RETURN NEW;
   END IF;
 END IF;
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
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package imports have no earlier representation")
