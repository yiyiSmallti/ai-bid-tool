"""Durable tenant agent delegation, encrypted checkpoints and immutable provenance."""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(PARENTS_SQL)
    op.execute(SCHEMA_SQL)
    op.execute(ORIGIN_SQL)
    op.execute(GUARDS_SQL)


PARENTS_SQL = r"""
ALTER TABLE public.memberships ADD CONSTRAINT agent_membership_owner UNIQUE(org_id,id,user_id);
ALTER TABLE public.documents ADD CONSTRAINT agent_document_task UNIQUE(org_id,task_id,id);
ALTER TABLE public.jobs ADD CONSTRAINT agent_job_document UNIQUE(org_id,task_id,document_id,id);
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_agent_scopes
  CHECK (NOT (scopes ?| ARRAY['agent:read','agent:run','agent:cancel']));
"""

SCHEMA_SQL = r"""
CREATE TABLE agent_principals (
	user_id UUID NOT NULL, 
	membership_id UUID NOT NULL, 
	initial_grants JSONB NOT NULL, 
	scopes JSONB NOT NULL, 
	authority_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, user_id), 
	CONSTRAINT agent_principal_scopes CHECK (jsonb_typeof(initial_grants)='array' AND jsonb_typeof(scopes)='array' AND initial_grants @> scopes)
);
CREATE TABLE agent_sessions (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	principal_id UUID NOT NULL, 
	owner_user_id UUID NOT NULL, 
	state VARCHAR(20) DEFAULT 'queued' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	limits JSONB NOT NULL, 
	model_snapshot JSONB DEFAULT '{}' NOT NULL, 
	steps_used INTEGER DEFAULT '0' NOT NULL, 
	active_seconds_used INTEGER DEFAULT '0' NOT NULL, 
	vendor_calls_used INTEGER DEFAULT '0' NOT NULL, 
	active_since TIMESTAMP WITH TIME ZONE, 
	current_job_id UUID, 
	current_run_id UUID, 
	pause_id UUID, 
	tool_schema_sha256 VARCHAR(64) NOT NULL, 
	model_sha256 VARCHAR(64) NOT NULL, 
	input_sha256 VARCHAR(64) NOT NULL, 
	start_idempotency_key UUID NOT NULL, 
	start_request_sha256 VARCHAR(64) NOT NULL, 
	start_receipt_enc TEXT, 
	cancel_idempotency_key UUID, 
	cancel_request_sha256 VARCHAR(64), 
	cancel_receipt_enc TEXT, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id, document_id), 
	UNIQUE (org_id, id, principal_id, owner_user_id), 
	UNIQUE (org_id, task_id, owner_user_id, start_idempotency_key), 
	CONSTRAINT agent_cancel_receipt CHECK ((cancel_idempotency_key IS NULL AND cancel_request_sha256 IS NULL AND cancel_receipt_enc IS NULL) OR (cancel_idempotency_key IS NOT NULL AND cancel_request_sha256 IS NOT NULL AND cancel_receipt_enc IS NOT NULL AND state='cancelled')),
	CONSTRAINT agent_session_state CHECK (state IN ('queued','running','waiting_job','paused','completed','partial','failed','cancelled')), 
	CONSTRAINT agent_session_counters CHECK (revision>=1 AND steps_used>=0 AND active_seconds_used>=0 AND vendor_calls_used>=0)
);
CREATE TABLE agent_steps (
	session_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	ordinal INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	state VARCHAR(20) DEFAULT 'planned' NOT NULL, 
	command VARCHAR(100), 
	created_by_job_id UUID NOT NULL, 
	created_by_run_id UUID NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	last_transition_job_id UUID NOT NULL, 
	last_transition_run_id UUID NOT NULL, 
	invocation_id UUID, 
	arguments_enc TEXT, 
	arguments_sha256 VARCHAR(64), 
	input_refs JSONB DEFAULT '[]' NOT NULL, 
	schema_sha256 VARCHAR(64) NOT NULL, 
	child_job_id UUID, 
	result_enc TEXT, 
	result_sha256 VARCHAR(64), 
	exit_code INTEGER, 
	error_code VARCHAR(80), 
	usage_ids JSONB DEFAULT '[]' NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, session_id, id), 
	UNIQUE (org_id, session_id, ordinal), 
	UNIQUE (org_id, invocation_id), 
	CONSTRAINT agent_step_state CHECK (ordinal>=1 AND revision>=1 AND kind IN ('decision','tool') AND state IN ('planned','submitted','waiting_job','completed','failed','uncertain')), 
	CONSTRAINT agent_step_exit CHECK (exit_code IN (0,2,3,4,5))
);
CREATE TABLE agent_messages (
	session_id UUID NOT NULL, 
	ordinal INTEGER NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	author_user_id UUID, 
	step_id UUID, 
	content_enc TEXT NOT NULL, 
	content TEXT NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	idempotency_key UUID, 
	request_sha256 VARCHAR(64), 
	receipt_enc TEXT, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, session_id, ordinal), 
	UNIQUE (org_id, session_id, idempotency_key), 
	CONSTRAINT agent_message_receipt CHECK ((idempotency_key IS NULL AND request_sha256 IS NULL AND receipt_enc IS NULL) OR (idempotency_key IS NOT NULL AND request_sha256 IS NOT NULL AND receipt_enc IS NOT NULL AND role='human')),
	CONSTRAINT agent_message_shape CHECK (ordinal>=1 AND role IN ('human','assistant','tool','system') AND length(content)<=8000)
);
CREATE TABLE agent_pauses (
	session_id UUID NOT NULL, 
	step_id UUID, 
	kind VARCHAR(20) NOT NULL, 
	status VARCHAR(20) DEFAULT 'pending' NOT NULL, 
	question TEXT NOT NULL, 
	question_enc TEXT NOT NULL, 
	action VARCHAR(30), 
	resource_ids JSONB DEFAULT '[]' NOT NULL, 
	budget_ref JSONB, 
	input_refs JSONB DEFAULT '[]' NOT NULL, 
	input_sha256 VARCHAR(64) NOT NULL, 
	resolved_by UUID, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	resume_idempotency_key UUID, 
	request_sha256 VARCHAR(64), 
	receipt_enc TEXT, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, session_id, id), 
	UNIQUE (org_id, session_id, resume_idempotency_key), 
	CONSTRAINT agent_pause_receipt CHECK ((resume_idempotency_key IS NULL AND request_sha256 IS NULL AND receipt_enc IS NULL) OR (resume_idempotency_key IS NOT NULL AND request_sha256 IS NOT NULL AND receipt_enc IS NOT NULL AND status='resolved')),
	CONSTRAINT agent_pause_shape CHECK (kind IN ('budget','human_action','authority','recovery') AND status IN ('pending','resolved','cancelled','expired') AND length(question) BETWEEN 1 AND 2000), 
	CONSTRAINT agent_pause_budget CHECK ((kind='budget')=(budget_ref IS NOT NULL))
);
CREATE TABLE agent_job_links (
	session_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	step_id UUID, 
	owned BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, session_id, job_id), 
	CONSTRAINT agent_link_role CHECK (role IN ('controller','tool') AND (role<>'tool' OR step_id IS NOT NULL))
);
ALTER TABLE agent_principals ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
ALTER TABLE agent_principals ADD FOREIGN KEY(org_id, membership_id, user_id) REFERENCES memberships (org_id, id, user_id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX ix_agent_principals_org_id ON agent_principals (org_id);
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, id, pause_id) REFERENCES agent_pauses (org_id, session_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, principal_id, owner_user_id) REFERENCES agent_principals (org_id, id, user_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, task_id, document_id, current_job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, task_id, document_id, extraction_job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD FOREIGN KEY(org_id, task_id, document_id) REFERENCES documents (org_id, task_id, id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX ix_agent_sessions_org_id ON agent_sessions (org_id);
ALTER TABLE agent_steps ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
ALTER TABLE agent_steps ADD FOREIGN KEY(org_id, task_id, document_id, created_by_job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_steps ADD FOREIGN KEY(org_id, task_id, document_id, child_job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_steps ADD FOREIGN KEY(org_id, session_id, task_id, document_id) REFERENCES agent_sessions (org_id, id, task_id, document_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_steps ADD FOREIGN KEY(org_id, task_id, document_id, last_transition_job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX ix_agent_steps_org_id ON agent_steps (org_id);
ALTER TABLE agent_messages ADD FOREIGN KEY(org_id, session_id) REFERENCES agent_sessions (org_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_messages ADD FOREIGN KEY(org_id, author_user_id) REFERENCES memberships (org_id, user_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_messages ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
ALTER TABLE agent_messages ADD FOREIGN KEY(org_id, session_id, step_id) REFERENCES agent_steps (org_id, session_id, id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX ix_agent_messages_org_id ON agent_messages (org_id);
ALTER TABLE agent_pauses ADD FOREIGN KEY(org_id, session_id, step_id) REFERENCES agent_steps (org_id, session_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_pauses ADD FOREIGN KEY(org_id, session_id) REFERENCES agent_sessions (org_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_pauses ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
ALTER TABLE agent_pauses ADD FOREIGN KEY(org_id, resolved_by) REFERENCES memberships (org_id, user_id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX agent_one_pending_pause ON agent_pauses (org_id, session_id) WHERE status='pending';
CREATE INDEX ix_agent_pauses_org_id ON agent_pauses (org_id);
ALTER TABLE agent_job_links ADD FOREIGN KEY(org_id, task_id, document_id, job_id) REFERENCES jobs (org_id, task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_job_links ADD FOREIGN KEY(org_id, session_id, task_id, document_id) REFERENCES agent_sessions (org_id, id, task_id, document_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_job_links ADD FOREIGN KEY(org_id, session_id, step_id) REFERENCES agent_steps (org_id, session_id, id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_job_links ADD FOREIGN KEY(org_id) REFERENCES orgs (id);
CREATE INDEX ix_agent_job_links_org_id ON agent_job_links (org_id);
CREATE UNIQUE INDEX agent_owned_job ON agent_job_links (org_id, job_id) WHERE owned;
ALTER TABLE public.agent_principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_principals FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_principals
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_principals TO bid_app;
ALTER TABLE public.agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_sessions
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_sessions TO bid_app;
ALTER TABLE public.agent_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_steps
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_steps TO bid_app;
ALTER TABLE public.agent_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_messages
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_messages TO bid_app;
ALTER TABLE public.agent_pauses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_pauses FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_pauses
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_pauses TO bid_app;
ALTER TABLE public.agent_job_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_job_links FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.agent_job_links
 USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON public.agent_job_links TO bid_app;
"""

GUARDS_SQL = r"""
CREATE FUNCTION public.agent_owner(p_org uuid,p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(current_setting('app.actor_kind',true)='session'
  AND NULLIF(current_setting('app.actor_token_id',true),'') IS NULL
  AND NULLIF(current_setting('app.actor_user_id',true),'')::uuid=p_user
  AND EXISTS(SELECT FROM public.memberships m JOIN public.users u ON u.id=m.user_id
    JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=p_user
      AND m.active AND u.active AND o.active),false)
$$;
REVOKE ALL ON FUNCTION public.agent_owner(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_owner(uuid,uuid) TO bid_app;

CREATE FUNCTION public.agent_principal_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent authority history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF NOT public.agent_owner(NEW.org_id,NEW.user_id) THEN
   RAISE EXCEPTION 'agent authority requires its human owner' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' AND (NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb ? 'agent:run',false)
    OR NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb @> NEW.initial_grants,false)) THEN
   RAISE EXCEPTION 'principal grants must come from its authenticated owner' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT FROM public.memberships WHERE org_id=NEW.org_id AND id=NEW.membership_id
    AND user_id=NEW.user_id AND active) THEN
   RAISE EXCEPTION 'principal membership mismatch' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM jsonb_array_elements_text(NEW.scopes) AS grants(scope) WHERE scope NOT IN
   ('task:read','job:read','card:read','card:generate','draft:read','draft:run','resource:read',
    'certificate:read','certificate:file:read','profile:read','evidence:source:read')) THEN
   RAISE EXCEPTION 'agent scope forbidden' USING ERRCODE='42501'; END IF;
 IF NEW.authority_expires_at>clock_timestamp()+interval '8 hours' THEN
   RAISE EXCEPTION 'agent authority exceeds delegation window' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.org_id,NEW.id,NEW.user_id,NEW.membership_id,NEW.initial_grants,NEW.created_at)
    IS DISTINCT FROM (OLD.org_id,OLD.id,OLD.user_id,OLD.membership_id,OLD.initial_grants,OLD.created_at)
    OR NOT OLD.scopes @> NEW.scopes OR (OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)) THEN
   RAISE EXCEPTION 'agent authority cannot expand or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_principal_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_principals
 FOR EACH ROW EXECUTE FUNCTION public.agent_principal_guard();

CREATE FUNCTION public.agent_history_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s public.agent_sessions; j public.jobs; kind text:=current_setting('app.actor_kind',true);
 actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
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
 SELECT * INTO s FROM public.agent_sessions WHERE org_id=NEW.org_id AND id=sid;
 IF TG_TABLE_NAME='agent_sessions' AND TG_OP='INSERT' THEN s:=NEW; END IF;
 IF s.id IS NULL THEN RAISE EXCEPTION 'agent session is unavailable' USING ERRCODE='23514'; END IF;
 IF NOT public.agent_owner(s.org_id,s.owner_user_id) AND NOT COALESCE((
    kind IN ('agent','worker') AND principal=s.principal_id AND actor=s.owner_user_id
    AND NULLIF(current_setting('app.agent_session_id',true),'')::uuid=s.id),false) THEN
   RAISE EXCEPTION 'agent history actor mismatch' USING ERRCODE='42501'; END IF;
 IF kind IN ('agent','worker') THEN
   SELECT EXISTS(SELECT FROM public.agent_principals p
      JOIN public.memberships m ON (m.org_id,m.id,m.user_id)=(p.org_id,p.membership_id,p.user_id)
      JOIN public.users u ON u.id=p.user_id JOIN public.orgs o ON o.id=p.org_id
      WHERE p.org_id=s.org_id AND p.id=s.principal_id AND p.revoked_at IS NULL
        AND p.authority_expires_at>clock_timestamp() AND s.expires_at>clock_timestamp()
        AND m.active AND u.active AND o.active) INTO delegated;
   -- Expiry blocks publication/dispatch, while leaving recovery able to retain
   -- an authority pause or terminal receipt without another paid operation.
   IF NOT delegated AND NOT (
       (TG_TABLE_NAME='agent_sessions' AND TG_OP='UPDATE' AND to_jsonb(NEW)->>'state' IN ('paused','failed','partial','cancelled'))
       OR TG_TABLE_NAME='agent_pauses'
       OR (TG_TABLE_NAME='agent_steps' AND TG_OP='UPDATE' AND to_jsonb(NEW)->>'state' IN ('failed','uncertain'))
       OR (TG_TABLE_NAME='agent_messages' AND to_jsonb(NEW)->>'role'='system')) THEN
     RAISE EXCEPTION 'agent delegation is no longer active' USING ERRCODE='42501'; END IF;
   IF TG_TABLE_NAME IN ('agent_messages','agent_pauses','agent_job_links') THEN
     SELECT * INTO j FROM public.jobs WHERE org_id=s.org_id AND id=execution_job;
     live_execution:=j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
       AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
       AND (s.current_job_id,s.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id);
     IF NOT COALESCE(live_execution,false) AND NOT (
       execution_job IS NULL AND execution_run IS NULL
       AND NOT EXISTS(SELECT FROM public.jobs WHERE org_id=s.org_id AND id=s.current_job_id
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
     IF NOT public.agent_owner(NEW.org_id,NEW.owner_user_id) OR NOT EXISTS(
       SELECT FROM public.jobs x WHERE x.org_id=NEW.org_id AND x.id=NEW.extraction_job_id
         AND x.task_id=NEW.task_id AND x.document_id=NEW.document_id AND x.kind='extract' AND x.status='succeeded') THEN
       RAISE EXCEPTION 'agent requires human and successful extraction' USING ERRCODE='42501'; END IF;
   ELSE
     IF kind IN ('agent','worker') THEN
       SELECT * INTO j FROM public.jobs WHERE org_id=s.org_id AND id=execution_job;
       live_execution:=j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
         AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
         AND ((OLD.current_job_id,OLD.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id)
           OR (OLD.state='queued' AND NEW.state='running'
             AND (NEW.current_job_id,NEW.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id)));
       IF NOT COALESCE(live_execution,false) AND NOT (
         execution_job IS NULL AND execution_run IS NULL
         AND NOT EXISTS(SELECT FROM public.jobs WHERE org_id=s.org_id AND id=OLD.current_job_id
           AND status='running' AND lease_until>clock_timestamp())
         AND (NEW.state IN ('paused','failed','partial','cancelled') OR
           (OLD.state IN ('queued','running','waiting_job') AND NEW.state='queued'))) THEN
         RAISE EXCEPTION 'stale agent session execution' USING ERRCODE='23514'; END IF;
       IF OLD.state='paused' AND NEW.state='queued' THEN
         RAISE EXCEPTION 'only a human may resume an agent' USING ERRCODE='42501'; END IF;
     END IF;
     IF (NEW.org_id,NEW.id,NEW.task_id,NEW.document_id,NEW.extraction_job_id,NEW.principal_id,
       NEW.owner_user_id,NEW.limits,NEW.model_snapshot,NEW.tool_schema_sha256,NEW.model_sha256,
       NEW.input_sha256,NEW.expires_at,NEW.start_idempotency_key,NEW.start_request_sha256,NEW.created_at)
       IS DISTINCT FROM (OLD.org_id,OLD.id,OLD.task_id,OLD.document_id,OLD.extraction_job_id,OLD.principal_id,
       OLD.owner_user_id,OLD.limits,OLD.model_snapshot,OLD.tool_schema_sha256,OLD.model_sha256,
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
   IF (NEW.role='human' AND (NOT public.agent_owner(s.org_id,s.owner_user_id)
         OR NEW.author_user_id IS DISTINCT FROM s.owner_user_id))
      OR (NEW.role<>'human' AND (kind NOT IN ('agent','worker') OR NEW.author_user_id IS NOT NULL)) THEN
      RAISE EXCEPTION 'message role does not match actor' USING ERRCODE='42501'; END IF;
 ELSIF TG_TABLE_NAME='agent_steps' THEN
   IF kind NOT IN ('agent','worker') THEN RAISE EXCEPTION 'step requires agent execution' USING ERRCODE='42501'; END IF;
   SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.last_transition_job_id;
   IF j.id IS NULL OR j.run_id IS DISTINCT FROM NEW.last_transition_run_id OR j.status<>'running'
       OR j.id IS DISTINCT FROM execution_job OR j.run_id IS DISTINCT FROM execution_run
       OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
       OR s.current_job_id IS DISTINCT FROM j.id OR s.current_run_id IS DISTINCT FROM j.run_id THEN
     RAISE EXCEPTION 'stale agent execution' USING ERRCODE='23514'; END IF;
   IF TG_OP='INSERT' AND (NEW.created_by_job_id,NEW.created_by_run_id) IS DISTINCT FROM
       (NEW.last_transition_job_id,NEW.last_transition_run_id) THEN
     RAISE EXCEPTION 'step creator must be current execution' USING ERRCODE='23514'; END IF;
   IF TG_OP='UPDATE' AND (
      (NEW.org_id,NEW.id,NEW.session_id,NEW.task_id,NEW.document_id,NEW.ordinal,NEW.kind,NEW.command,
       NEW.created_by_job_id,NEW.created_by_run_id,NEW.invocation_id,NEW.arguments_enc,NEW.arguments_sha256,
       NEW.input_refs,NEW.schema_sha256,NEW.created_at) IS DISTINCT FROM
      (OLD.org_id,OLD.id,OLD.session_id,OLD.task_id,OLD.document_id,OLD.ordinal,OLD.kind,OLD.command,
       OLD.created_by_job_id,OLD.created_by_run_id,OLD.invocation_id,OLD.arguments_enc,OLD.arguments_sha256,
       OLD.input_refs,OLD.schema_sha256,OLD.created_at)
      OR OLD.state IN ('completed','failed') OR NEW.revision<>OLD.revision+1) THEN
     RAISE EXCEPTION 'step provenance and completed output are immutable' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='agent_pauses' THEN
   IF TG_OP='UPDATE' AND (
      (to_jsonb(NEW)-ARRAY['status','resolved_by','resolved_at','resume_idempotency_key','request_sha256','receipt_enc'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','resolved_by','resolved_at','resume_idempotency_key','request_sha256','receipt_enc'])
      OR OLD.status<>'pending') THEN
     RAISE EXCEPTION 'pause input and resolution are immutable' USING ERRCODE='23514'; END IF;
   IF NEW.status='resolved' AND (NOT public.agent_owner(s.org_id,s.owner_user_id)
      OR NEW.resolved_by IS DISTINCT FROM s.owner_user_id OR NEW.resolved_at IS NULL) THEN
     RAISE EXCEPTION 'pause resolution requires its human owner' USING ERRCODE='42501'; END IF;
   IF TG_OP='INSERT' AND (NEW.status<>'pending' OR NEW.resolved_by IS NOT NULL OR NEW.resolved_at IS NOT NULL) THEN
     RAISE EXCEPTION 'new pause must be pending' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='agent_job_links' THEN
   IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'job ownership is immutable' USING ERRCODE='42501'; END IF;
   SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
   IF NEW.owned AND (j.id IS NULL OR j.initiated_by IS DISTINCT FROM 'builtin_agent'
      OR j.agent_session_id IS DISTINCT FROM s.id) THEN
     RAISE EXCEPTION 'owned job requires bound agent provenance' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_session_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_sessions FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();
CREATE TRIGGER agent_message_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_messages FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();
CREATE TRIGGER agent_step_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_steps FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();
CREATE TRIGGER agent_pause_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_pauses FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();
CREATE TRIGGER agent_link_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_job_links FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();
REVOKE ALL ON FUNCTION public.agent_history_guard(),public.agent_principal_guard() FROM PUBLIC;
"""

ORIGIN_SQL = r"""
ALTER TABLE public.audit_logs ADD COLUMN actor_kind varchar(20), ADD COLUMN job_id uuid, ADD COLUMN run_id uuid;
"""
for _table in ("jobs", "audit_logs"):
    ORIGIN_SQL += f"""
ALTER TABLE public.{_table}
 ADD COLUMN initiated_by varchar(30),
 ADD COLUMN on_behalf_of_user_id uuid,
 ADD COLUMN agent_principal_id uuid,
 ADD COLUMN agent_session_id uuid,
 ADD COLUMN agent_step_id uuid,
 ADD COLUMN invocation_id uuid,
 ADD COLUMN command varchar(100),
 ADD CONSTRAINT {_table}_agent_origin_owner_fk FOREIGN KEY(org_id,on_behalf_of_user_id)
   REFERENCES public.memberships(org_id,user_id) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT {_table}_agent_origin_principal_fk FOREIGN KEY(org_id,agent_principal_id,on_behalf_of_user_id)
   REFERENCES public.agent_principals(org_id,id,user_id) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT {_table}_agent_origin_session_fk FOREIGN KEY(org_id,agent_session_id,agent_principal_id,on_behalf_of_user_id)
   REFERENCES public.agent_sessions(org_id,id,principal_id,owner_user_id) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT {_table}_agent_origin_step_fk FOREIGN KEY(org_id,agent_session_id,agent_step_id)
   REFERENCES public.agent_steps(org_id,session_id,id) DEFERRABLE INITIALLY DEFERRED,
 ADD CONSTRAINT {_table}_agent_origin_shape CHECK (
   initiated_by IS NULL OR initiated_by IN ('human','legacy_unknown','builtin_agent','external_agent')),
 ADD CONSTRAINT {_table}_agent_origin_binding CHECK (
   (initiated_by='builtin_agent' AND agent_principal_id IS NOT NULL AND agent_session_id IS NOT NULL
     AND on_behalf_of_user_id IS NOT NULL AND actor_token_id IS NULL)
   OR (initiated_by='external_agent' AND actor_token_id IS NOT NULL AND on_behalf_of_user_id IS NOT NULL
     AND agent_principal_id IS NULL AND agent_session_id IS NULL AND agent_step_id IS NULL)
   OR (COALESCE(initiated_by,'legacy_unknown') IN ('human','legacy_unknown')
     AND agent_principal_id IS NULL AND agent_session_id IS NULL AND agent_step_id IS NULL));
CREATE INDEX {_table}_agent_session ON public.{_table}(org_id,agent_session_id);
UPDATE public.{_table} SET initiated_by=CASE WHEN actor_token_id IS NOT NULL THEN 'external_agent'
  ELSE 'legacy_unknown' END,
  on_behalf_of_user_id=CASE WHEN actor_token_id IS NOT NULL THEN actor_user_id ELSE NULL END;
"""
ORIGIN_SQL += r"""
ALTER TABLE public.jobs ADD CONSTRAINT agent_job_session UNIQUE(org_id,agent_session_id,id);
ALTER TABLE public.jobs ADD CONSTRAINT job_agent_task_fk FOREIGN KEY(org_id,agent_session_id,task_id,document_id)
 REFERENCES public.agent_sessions(org_id,id,task_id,document_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.audit_logs ADD CONSTRAINT audit_agent_job_fk FOREIGN KEY(org_id,job_id)
 REFERENCES public.jobs(org_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE public.audit_logs ADD CONSTRAINT audit_agent_job_session_fk FOREIGN KEY(org_id,agent_session_id,job_id)
 REFERENCES public.jobs(org_id,agent_session_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION public.agent_origin_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE kind text:=NULLIF(current_setting('app.actor_kind',true),'');
 token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
 actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
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
   IF (NEW.initiated_by,NEW.on_behalf_of_user_id,NEW.agent_principal_id,NEW.agent_session_id,
       NEW.agent_step_id,NEW.invocation_id,NEW.command)
      IS DISTINCT FROM (OLD.initiated_by,OLD.on_behalf_of_user_id,OLD.agent_principal_id,OLD.agent_session_id,
       OLD.agent_step_id,OLD.invocation_id,OLD.command) THEN
      RAISE EXCEPTION 'job origin is immutable' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='audit_logs' THEN NEW.actor_kind:=COALESCE(kind,'legacy_unknown'); END IF;
 IF kind='token' OR token IS NOT NULL THEN
   IF NEW.agent_principal_id IS NOT NULL OR NEW.agent_session_id IS NOT NULL OR
      NEW.actor_token_id IS DISTINCT FROM token OR NEW.actor_user_id IS DISTINCT FROM actor THEN
     RAISE EXCEPTION 'token origin must match authentication' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:='external_agent'; NEW.on_behalf_of_user_id:=actor;
 ELSIF principal IS NOT NULL THEN
   IF kind NOT IN ('agent','worker','session') OR actor IS NULL OR sid IS NULL OR
      (NEW.actor_user_id IS NOT NULL AND NEW.actor_user_id IS DISTINCT FROM actor) OR
      NEW.actor_token_id IS NOT NULL OR
      (NEW.agent_principal_id IS NOT NULL AND NEW.agent_principal_id<>principal) OR
      (NEW.agent_session_id IS NOT NULL AND NEW.agent_session_id<>sid) OR
      (NEW.agent_step_id IS NOT NULL AND NEW.agent_step_id IS DISTINCT FROM step) THEN
     RAISE EXCEPTION 'agent origin must match execution context' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:='builtin_agent'; NEW.on_behalf_of_user_id:=actor;
   NEW.agent_principal_id:=principal; NEW.agent_session_id:=sid; NEW.agent_step_id:=step;
   IF TG_TABLE_NAME='jobs' AND kind IN ('agent','worker') THEN
     SELECT * INTO s FROM public.agent_sessions WHERE org_id=NEW.org_id AND id=sid;
     IF s.id IS NULL OR s.state NOT IN ('queued','running','waiting_job') OR s.expires_at<=clock_timestamp()
       OR NOT EXISTS(SELECT FROM public.agent_principals p
          JOIN public.memberships m ON (m.org_id,m.id,m.user_id)=(p.org_id,p.membership_id,p.user_id)
          JOIN public.users u ON u.id=p.user_id JOIN public.orgs o ON o.id=p.org_id
          WHERE p.org_id=NEW.org_id AND p.id=principal AND p.id=s.principal_id
            AND p.user_id=actor AND p.revoked_at IS NULL AND p.authority_expires_at>clock_timestamp()
            AND m.active AND u.active AND o.active) THEN
       RAISE EXCEPTION 'agent dispatch requires active delegation' USING ERRCODE='42501'; END IF;
     SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=execution_job;
     IF NOT COALESCE(j.id IS NOT NULL AND j.kind='agent' AND j.agent_session_id=s.id
         AND j.run_id=execution_run AND j.status='running' AND j.lease_until>clock_timestamp()
         AND (s.current_job_id,s.current_run_id) IS NOT DISTINCT FROM (j.id,j.run_id),false)
       AND NOT (NEW.kind='agent' AND execution_job IS NULL AND execution_run IS NULL
         AND NOT EXISTS(SELECT FROM public.jobs WHERE org_id=s.org_id AND id=s.current_job_id
           AND status='running' AND lease_until>clock_timestamp())) THEN
       RAISE EXCEPTION 'stale agent job dispatch' USING ERRCODE='23514'; END IF;
   END IF;
 ELSIF TG_TABLE_NAME='audit_logs' AND kind='worker' AND (to_jsonb(NEW)->>'job_id') IS NOT NULL THEN
   SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
   IF j.id IS NULL THEN RAISE EXCEPTION 'audit job is unavailable' USING ERRCODE='23514'; END IF;
   NEW.initiated_by:=j.initiated_by; NEW.on_behalf_of_user_id:=j.on_behalf_of_user_id;
   NEW.agent_principal_id:=j.agent_principal_id; NEW.agent_session_id:=j.agent_session_id;
   NEW.agent_step_id:=j.agent_step_id; NEW.invocation_id:=j.invocation_id;
   NEW.actor_token_id:=j.actor_token_id;
 ELSE
   IF NEW.initiated_by IN ('builtin_agent','external_agent') OR NEW.agent_principal_id IS NOT NULL
       OR NEW.agent_session_id IS NOT NULL OR NEW.agent_step_id IS NOT NULL THEN
     RAISE EXCEPTION 'authenticated origin is required' USING ERRCODE='42501'; END IF;
   NEW.initiated_by:=CASE WHEN kind='session' THEN 'human' ELSE 'legacy_unknown' END;
   IF kind='session' THEN NEW.on_behalf_of_user_id:=actor; END IF;
 END IF;
 IF NEW.invocation_id IS NULL THEN
   NEW.invocation_id:=NULLIF(current_setting('app.invocation_id',true),'')::uuid;
 END IF;
 IF NEW.command IS NULL THEN NEW.command:=NULLIF(current_setting('app.command',true),''); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER z_agent_job_origin BEFORE INSERT OR UPDATE OR DELETE ON public.jobs
 FOR EACH ROW EXECUTE FUNCTION public.agent_origin_guard();
CREATE TRIGGER agent_audit_origin BEFORE INSERT OR UPDATE OR DELETE ON public.audit_logs
 FOR EACH ROW EXECUTE FUNCTION public.agent_origin_guard();
REVOKE ALL ON FUNCTION public.agent_origin_guard() FROM PUBLIC;
"""


def downgrade():
    raise RuntimeError(
        "Agent history and billing provenance must be retained; rollback is application-only"
    )
