"""Tenant memory storage, immutable lineage and human/worker gates."""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

TABLES = (
    "memory_scope_epochs",
    "memory_feedback_events",
    "memories",
    "memory_revisions",
    "memory_eval_samples",
    "memory_retrievals",
    "memory_retrieval_items",
    "memory_call_inputs",
)
POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def upgrade():
    op.execute(
        "ALTER TABLE usage_records ADD CONSTRAINT memory_usage_call_binding UNIQUE(org_id,id,job_id,run_id,call_id)"
    )
    op.execute(TABLE_SQL)
    op.execute(CONSTRAINT_SQL)
    op.execute(GATE_SQL)
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({POLICY}) WITH CHECK ({POLICY})'
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
    for table in (
        "memory_revisions",
        "memory_feedback_events",
        "memory_retrievals",
        "memory_retrieval_items",
    ):
        op.execute(
            f'CREATE TRIGGER memory_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION memory_immutable_gate()'
        )
    op.execute(
        "GRANT UPDATE ON memories,memory_scope_epochs,memory_eval_samples,memory_call_inputs TO bid_app"
    )
    op.execute(TRIGGER_SQL)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain memory and model-call history")


TABLE_SQL = r"""

CREATE TABLE memory_scope_epochs (
	org_id UUID NOT NULL, 
	scope VARCHAR(20) NOT NULL, 
	owner_id UUID NOT NULL, 
	epoch BIGINT NOT NULL, 
	PRIMARY KEY (org_id, scope, owner_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_feedback_events (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	model_job_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	before_revision_id UUID NOT NULL, 
	after_revision_id UUID NOT NULL, 
	actor_user_id UUID NOT NULL, 
	actor_kind VARCHAR(20) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	review_domain VARCHAR(20), 
	encrypted_summary TEXT NOT NULL, 
	sanitized_sha256 VARCHAR(64) NOT NULL, 
	sanitizer_version VARCHAR(100) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, card_id, after_revision_id, kind), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	FOREIGN KEY(org_id, model_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, card_id, task_id) REFERENCES response_cards (org_id, id, task_id), 
	FOREIGN KEY(org_id, card_id, before_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, after_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memories (
	scope VARCHAR(20) NOT NULL, 
	user_id UUID, 
	task_id UUID, 
	current_revision_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	source_feedback_event_id UUID, 
	generator_version VARCHAR(100), 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, source_feedback_event_id, generator_version), 
	FOREIGN KEY(org_id, user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, source_feedback_event_id) REFERENCES memory_feedback_events (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_revisions (
	memory_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	content JSONB NOT NULL, 
	normalized_text TEXT NOT NULL, 
	normalized_tags TEXT[] NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	source JSONB NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	created_by UUID NOT NULL, 
	actor_kind VARCHAR(20) NOT NULL, 
	actor_token_id UUID, 
	confirmed_by UUID, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	decision VARCHAR(20), 
	decision_reason_sha256 VARCHAR(64), 
	expires_at TIMESTAMP WITH TIME ZONE, 
	task_id UUID, 
	card_id UUID, 
	card_revision_id UUID, 
	feedback_event_id UUID, 
	proposal_job_id UUID, 
	proposal_run_id UUID, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, memory_id, revision), 
	UNIQUE (org_id, memory_id, id), 
	UNIQUE (org_id, memory_id, id, revision), 
	FOREIGN KEY(org_id, memory_id) REFERENCES memories (org_id, id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, confirmed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, feedback_event_id) REFERENCES memory_feedback_events (org_id, id), 
	FOREIGN KEY(org_id, proposal_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, card_id, task_id) REFERENCES response_cards (org_id, id, task_id), 
	FOREIGN KEY(org_id, card_id, card_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_eval_samples (
	task_id UUID NOT NULL, 
	feedback_event_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	before_revision_id UUID NOT NULL, 
	after_revision_id UUID NOT NULL, 
	label VARCHAR(30) NOT NULL, 
	actor_user_id UUID NOT NULL, 
	generator_version VARCHAR(100) NOT NULL, 
	sanitized_sha256 VARCHAR(64) NOT NULL, 
	encrypted_summary TEXT NOT NULL, 
	review_state VARCHAR(20) NOT NULL, 
	review_revision INTEGER NOT NULL, 
	reviewed_by UUID, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	review_reason_sha256 VARCHAR(64), 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, feedback_event_id, generator_version), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, feedback_event_id) REFERENCES memory_feedback_events (org_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, reviewed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, card_id, task_id) REFERENCES response_cards (org_id, id, task_id), 
	FOREIGN KEY(org_id, card_id, before_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, after_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_retrievals (
	user_id UUID NOT NULL, 
	task_id UUID, 
	actor_token_id UUID, 
	data JSONB NOT NULL, 
	encrypted_query TEXT NOT NULL, 
	encrypted_snapshot TEXT NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	FOREIGN KEY(org_id, user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_retrieval_items (
	retrieval_id UUID NOT NULL, 
	memory_id UUID NOT NULL, 
	memory_revision_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	sent_sha256 VARCHAR(64) NOT NULL, 
	rank INTEGER NOT NULL, 
	selected BOOLEAN NOT NULL, 
	omission_reason VARCHAR(40), 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, retrieval_id, memory_revision_id), 
	FOREIGN KEY(org_id, retrieval_id) REFERENCES memory_retrievals (org_id, id), 
	FOREIGN KEY(org_id, memory_id, memory_revision_id, revision) REFERENCES memory_revisions (org_id, memory_id, id, revision), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE memory_call_inputs (
	task_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	call_id UUID NOT NULL, 
	retrieval_id UUID NOT NULL, 
	requirement_ids JSONB NOT NULL, 
	memories JSONB NOT NULL, 
	manifest_sha256 VARCHAR(64) NOT NULL, 
	prompt_sha256 VARCHAR(64) NOT NULL, 
	prompt_version VARCHAR(100) NOT NULL, 
	encrypted_prompt TEXT NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	usage_record_id UUID, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, job_id, run_id, call_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, retrieval_id) REFERENCES memory_retrievals (org_id, id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, job_id, run_id, call_id) REFERENCES vendor_calls (org_id, job_id, run_id, id), 
	FOREIGN KEY(org_id, usage_record_id, job_id, run_id, call_id) REFERENCES usage_records (org_id, id, job_id, run_id, call_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
ALTER TABLE memories ADD CONSTRAINT memory_current_revision FOREIGN KEY(org_id, id, current_revision_id, revision) REFERENCES memory_revisions (org_id, memory_id, id, revision) DEFERRABLE INITIALLY DEFERRED;
"""

CONSTRAINT_SQL = r"""
ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_memory_scopes CHECK (NOT(scopes ?| ARRAY['memory:approve','memory:manage','memory:eval:read','memory:eval:review']));
ALTER TABLE memories ADD CHECK(scope='org' AND user_id IS NULL AND task_id IS NULL), ADD CHECK(revision>=1),
 ADD CHECK((source_feedback_event_id IS NULL)=(generator_version IS NULL));
ALTER TABLE memory_scope_epochs ADD CHECK(scope='org' AND owner_id=org_id AND epoch>=0);
ALTER TABLE memory_revisions ADD CHECK(revision>=1), ADD CHECK(status IN ('candidate','active','disabled')),
 ADD CHECK(actor_kind IN ('session','token','worker','agent')),
 ADD CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 ADD CHECK(decision_reason_sha256 IS NULL OR decision_reason_sha256 ~ '^[0-9a-f]{64}$'),
 ADD CHECK(decision IS NULL OR decision IN ('approve','reject','disable','delete')),
 ADD CHECK((confirmed_by IS NULL)=(confirmed_at IS NULL)),
 ADD CHECK((status='active')=(confirmed_by IS NOT NULL)),
 ADD CHECK(status<>'active' OR (actor_kind='session' AND actor_token_id IS NULL AND decision='approve')),
 ADD CHECK(status<>'candidate' OR decision IS NULL),
 ADD CHECK((decision IS NULL)=(decision_reason_sha256 IS NULL)),
 ADD CHECK(jsonb_typeof(content)='object' AND coalesce(content->>'kind' IN ('rule','preference'),false)
  AND coalesce(content->>'conflict_key' ~ '^[a-z0-9][a-z0-9_.-]{0,99}$',false)
  AND coalesce(length(content->>'text') BETWEEN 1 AND 2000,false)
  AND coalesce(content->>'text' ~ '[^[:space:]]',false)
  AND coalesce(jsonb_typeof(content->'tags')='array',false) AND jsonb_array_length(content->'tags')<=20),
 ADD CHECK(jsonb_typeof(source)='object' AND coalesce(source->>'origin' IN ('human','system'),false)),
 ADD CHECK((card_id IS NULL)=(card_revision_id IS NULL)), ADD CHECK(card_id IS NULL OR task_id IS NOT NULL),
 ADD CHECK((proposal_job_id IS NULL)=(proposal_run_id IS NULL));
ALTER TABLE memory_feedback_events ADD CHECK(actor_kind='session'),
 ADD CHECK(kind IN ('card_rejected','card_edited','card_confirmed')), ADD CHECK(sanitized_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE memory_eval_samples ADD CHECK(review_state IN ('unreviewed','accepted','excluded')),
 ADD CHECK(review_revision>=1), ADD CHECK((reviewed_by IS NULL)=(reviewed_at IS NULL)),
 ADD CHECK((review_state='unreviewed')=(reviewed_by IS NULL)), ADD CHECK(sanitized_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE memory_retrievals ADD CHECK(jsonb_typeof(data)='object');
ALTER TABLE memory_retrieval_items ADD CHECK(rank>=1 AND revision>=1),
 ADD CHECK(selected=(omission_reason IS NULL)),
 ADD CHECK(omission_reason IS NULL OR omission_reason IN ('shadowed','same_priority_conflict','context_limit','top_k_limit')),
 ADD CHECK(content_sha256 ~ '^[0-9a-f]{64}$' AND sent_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE memory_call_inputs ADD CHECK(state IN ('admitted','completed','unknown')),
 ADD CHECK((state='completed')=(usage_record_id IS NOT NULL)),
 ADD CHECK(jsonb_typeof(requirement_ids)='array' AND jsonb_array_length(requirement_ids)>0),
 ADD CHECK(jsonb_typeof(memories)='array' AND jsonb_array_length(memories)<=50),
 ADD CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$' AND prompt_sha256 ~ '^[0-9a-f]{64}$');
CREATE INDEX memory_scope_lookup ON memories(org_id,scope);
CREATE INDEX memory_revision_search ON memory_revisions(org_id,status,memory_id);
CREATE INDEX memory_revision_tags ON memory_revisions USING gin(normalized_tags);
CREATE INDEX memory_feedback_task ON memory_feedback_events(org_id,task_id,created_at,id);
CREATE INDEX memory_eval_task ON memory_eval_samples(org_id,task_id,created_at,id);
CREATE INDEX memory_calls_job ON memory_call_inputs(org_id,job_id);
"""

GATE_SQL = r"""
CREATE FUNCTION memory_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 RAISE EXCEPTION 'Memory history is immutable' USING ERRCODE='42501';
END $$;

CREATE FUNCTION memory_epoch_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory epochs cannot be removed' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (ROW(NEW.org_id,NEW.scope,NEW.owner_id) IS DISTINCT FROM ROW(OLD.org_id,OLD.scope,OLD.owner_id)
  OR NEW.epoch<OLD.epoch) THEN RAISE EXCEPTION 'Memory epoch cannot move backwards or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION memory_parent_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE current_row public.memory_revisions%ROWTYPE; previous_row public.memory_revisions%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory deletion must preserve history' USING ERRCODE='42501'; END IF;
 INSERT INTO public.memory_scope_epochs(org_id,scope,owner_id,epoch) VALUES(NEW.org_id,'org',NEW.org_id,0) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM public.memory_scope_epochs WHERE org_id=NEW.org_id AND scope='org' AND owner_id=NEW.org_id FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF NEW.revision<>1 OR NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Initial memory must be a candidate' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.deleted_at IS NOT NULL OR NEW.revision<>OLD.revision+1
   OR (to_jsonb(NEW)-ARRAY['revision','current_revision_id','deleted_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','current_revision_id','deleted_at']) THEN
   RAISE EXCEPTION 'Memory owner and history cannot change' USING ERRCODE='23514'; END IF;
  SELECT * INTO current_row FROM public.memory_revisions WHERE org_id=NEW.org_id AND memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  SELECT * INTO previous_row FROM public.memory_revisions WHERE org_id=OLD.org_id AND id=OLD.current_revision_id;
  IF current_row.id IS NULL OR ((current_row.decision IS NOT DISTINCT FROM 'delete') IS DISTINCT FROM (NEW.deleted_at IS NOT NULL)) THEN
   RAISE EXCEPTION 'Memory pointer or tombstone mismatch' USING ERRCODE='23514'; END IF;
  IF current_row.status='active' OR previous_row.status='active' THEN
   UPDATE public.memory_scope_epochs SET epoch=epoch+1 WHERE org_id=NEW.org_id AND scope='org' AND owner_id=NEW.org_id;
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION memory_revision_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE parent_row public.memories%ROWTYPE; prior public.memory_revisions%ROWTYPE;
 job_row public.jobs%ROWTYPE; event_row public.memory_feedback_events%ROWTYPE; human uuid;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.created_by,NEW.actor_token_id,NEW.actor_kind);
 IF NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(NEW.content),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Memory content hash mismatch' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.memory_scope_epochs WHERE org_id=NEW.org_id AND scope='org' AND owner_id=NEW.org_id FOR UPDATE;
 SELECT * INTO parent_row FROM public.memories WHERE org_id=NEW.org_id AND id=NEW.memory_id FOR UPDATE;
 SELECT * INTO prior FROM public.memory_revisions WHERE org_id=NEW.org_id AND memory_id=NEW.memory_id ORDER BY revision DESC LIMIT 1;
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
  IF NEW.status<>'candidate' OR NOT EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.created_by AND active AND role IN ('admin','bidder','technical'))
   OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'memory:write')) THEN
   RAISE EXCEPTION 'Memory proposal requires write permission' USING ERRCODE='42501'; END IF;
  IF prior.id IS NOT NULL AND NOT(NEW.actor_kind='session' AND EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.created_by AND active AND role='admin'))
   AND (prior.status<>'candidate' OR NEW.created_by IS DISTINCT FROM prior.created_by OR NEW.actor_token_id IS DISTINCT FROM prior.actor_token_id
    OR EXISTS(SELECT 1 FROM public.memory_revisions WHERE org_id=NEW.org_id AND memory_id=NEW.memory_id AND status='active')) THEN
   RAISE EXCEPTION 'Only an admin may change reviewed memory or another candidate' USING ERRCODE='42501'; END IF;
 ELSE
  human:=public.response_require_human(NEW.org_id,'admin');
  IF human IS DISTINCT FROM NEW.created_by OR prior.id IS NULL OR NEW.content IS DISTINCT FROM prior.content
   OR NEW.content_sha256 IS DISTINCT FROM prior.content_sha256 OR NEW.expires_at IS DISTINCT FROM prior.expires_at
   OR NEW.normalized_text IS DISTINCT FROM prior.normalized_text OR NEW.normalized_tags IS DISTINCT FROM prior.normalized_tags
   OR (NEW.decision IN ('approve','reject') AND prior.status<>'candidate')
   OR (NEW.decision='disable' AND prior.status NOT IN ('candidate','active'))
   OR (NEW.decision='approve' AND (NEW.status<>'active' OR NEW.confirmed_by IS DISTINCT FROM human))
   OR (NEW.decision<>'approve' AND NEW.status<>'disabled') THEN
   RAISE EXCEPTION 'Memory decision must bind the exact current candidate' USING ERRCODE='23514'; END IF;
  IF NEW.decision='approve' AND EXISTS(SELECT 1 FROM public.memories m JOIN public.memory_revisions r ON r.org_id=m.org_id AND r.id=m.current_revision_id
   WHERE m.org_id=NEW.org_id AND m.id<>NEW.memory_id AND m.scope=parent_row.scope AND m.deleted_at IS NULL AND r.status='active'
    AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp()) AND r.content->>'kind'=NEW.content->>'kind'
    AND r.content->>'conflict_key'=NEW.content->>'conflict_key') THEN
   RAISE EXCEPTION 'An active memory already owns this conflict key' USING ERRCODE='23505'; END IF;
 END IF;
 IF prior.id IS NULL THEN
  IF NEW.status<>'candidate' OR NEW.decision IS NOT NULL THEN RAISE EXCEPTION 'Memory starts as candidate' USING ERRCODE='23514'; END IF;
  IF NEW.source->>'origin'='system' THEN
   SELECT * INTO event_row FROM public.memory_feedback_events WHERE org_id=NEW.org_id AND id=NEW.feedback_event_id;
   SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.proposal_job_id FOR UPDATE;
   IF NEW.actor_kind<>'worker' OR NEW.card_id IS NULL OR event_row.id IS NULL OR event_row.kind='card_confirmed'
    OR ROW(event_row.task_id,event_row.card_id,event_row.after_revision_id) IS DISTINCT FROM ROW(NEW.task_id,NEW.card_id,NEW.card_revision_id)
    OR job_row.kind IS DISTINCT FROM 'memory_candidate' OR job_row.status IS DISTINCT FROM 'running'
    OR job_row.task_id IS DISTINCT FROM NEW.task_id OR job_row.run_id IS DISTINCT FROM NEW.proposal_run_id
    OR job_row.document_id IS DISTINCT FROM event_row.document_id
    OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
    OR job_row.result->'submission'->>'actor_user_id' IS DISTINCT FROM NEW.created_by::text
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

CREATE FUNCTION memory_revision_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.memories WHERE org_id=NEW.org_id AND id=NEW.memory_id AND current_revision_id=NEW.id AND revision=NEW.revision) THEN
  RAISE EXCEPTION 'New memory revision must become current atomically' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION memory_feedback_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE before_row public.response_card_revisions%ROWTYPE; after_row public.response_card_revisions%ROWTYPE;
 card_row public.response_cards%ROWTYPE; after_xid text; job_row public.jobs%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NULL,'session');
 SELECT * INTO before_row FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.before_revision_id;
 SELECT * INTO after_row FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.after_revision_id;
 SELECT xmin::text INTO after_xid FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.after_revision_id;
 SELECT * INTO card_row FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=card_row.extraction_job_id;
 IF before_row.id IS NULL OR after_row.id IS NULL OR before_row.model_job_id IS NULL
  OR NEW.model_job_id IS DISTINCT FROM before_row.model_job_id
  OR after_row.revision<>before_row.revision+1 OR after_row.actor_kind<>'session'
  OR after_row.actor_user_id IS DISTINCT FROM NEW.actor_user_id OR after_row.actor_token_id IS NOT NULL
  OR after_xid IS DISTINCT FROM pg_current_xact_id()::text
  OR card_row.current_revision_id IS DISTINCT FROM after_row.id
  OR NEW.document_id IS DISTINCT FROM job_row.document_id
  OR NEW.review_domain IS DISTINCT FROM after_row.review_domain
  OR (NEW.kind='card_rejected' AND after_row.state<>'rejected')
  OR (NEW.kind='card_confirmed' AND (after_row.state<>'confirmed' OR after_row.confirmed_by IS DISTINCT FROM NEW.actor_user_id))
  OR (NEW.kind='card_edited' AND ROW(before_row.response_text,before_row.deviation,before_row.deviation_note)
     IS NOT DISTINCT FROM ROW(after_row.response_text,after_row.deviation,after_row.deviation_note)) THEN
  RAISE EXCEPTION 'Feedback must belong to an adjacent human model-card decision' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION memory_eval_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE event_row public.memory_feedback_events%ROWTYPE; human uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory evaluation history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
  SELECT * INTO event_row FROM public.memory_feedback_events WHERE org_id=NEW.org_id AND id=NEW.feedback_event_id;
  PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NULL,'session');
  IF event_row.id IS NULL OR ROW(NEW.task_id,NEW.card_id,NEW.before_revision_id,NEW.after_revision_id,NEW.label,NEW.actor_user_id,NEW.sanitized_sha256)
   IS DISTINCT FROM ROW(event_row.task_id,event_row.card_id,event_row.before_revision_id,event_row.after_revision_id,event_row.kind,event_row.actor_user_id,event_row.sanitized_sha256)
   OR NEW.review_state<>'unreviewed' OR NEW.review_revision<>1 OR NEW.review_reason_sha256 IS NOT NULL THEN
   RAISE EXCEPTION 'Evaluation sample must retain the source feedback' USING ERRCODE='23514'; END IF;
 ELSE
  human:=public.response_require_human(NEW.org_id,'admin');
  IF (to_jsonb(NEW)-ARRAY['review_state','review_revision','reviewed_by','reviewed_at','review_reason_sha256'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['review_state','review_revision','reviewed_by','reviewed_at','review_reason_sha256'])
   OR NEW.review_revision<>OLD.review_revision+1 OR NEW.reviewed_by IS DISTINCT FROM human
   OR NEW.review_state NOT IN ('accepted','excluded') OR NEW.reviewed_at IS NULL
   OR NEW.review_reason_sha256 IS NULL OR NEW.review_reason_sha256 !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'Evaluation review must be an exact human revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION memory_manifest_current(p_org uuid,p_manifest jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE epoch_value jsonb; memory_value jsonb;
BEGIN
 IF p_manifest IS NULL THEN RETURN true; END IF;
 PERFORM 1 FROM public.memory_scope_epochs WHERE org_id=p_org AND scope='org' AND owner_id=p_org FOR SHARE;
 IF jsonb_typeof(p_manifest) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_manifest->'epochs') IS DISTINCT FROM 'array'
  OR p_manifest->'scopes' IS DISTINCT FROM '["org"]'::jsonb
  OR p_manifest->>'retrieval_version' IS DISTINCT FROM 'keyword-v1'
  OR p_manifest->>'priority_version' IS DISTINCT FROM 'memory-priority-v1'
  OR p_manifest->>'sanitizer_version' IS DISTINCT FROM 'memory-sanitize-v1:bid-redaction-v3'
  OR jsonb_typeof(p_manifest->'memories') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_manifest->'epochs')<>1
  OR ((p_manifest->>'valid_until')::timestamptz IS NOT NULL AND (p_manifest->>'valid_until')::timestamptz<=clock_timestamp()) THEN RETURN false; END IF;
 FOR epoch_value IN SELECT value FROM jsonb_array_elements(p_manifest->'epochs') LOOP
  IF epoch_value->>'org_id' IS DISTINCT FROM p_org::text OR epoch_value->>'scope' IS DISTINCT FROM 'org'
   OR epoch_value->>'owner_id' IS DISTINCT FROM p_org::text
   OR (epoch_value->>'epoch')::bigint IS DISTINCT FROM coalesce((SELECT epoch FROM public.memory_scope_epochs WHERE org_id=p_org AND scope='org' AND owner_id=p_org),0) THEN RETURN false; END IF;
 END LOOP;
 FOR memory_value IN SELECT value FROM jsonb_array_elements(p_manifest->'memories') LOOP
  IF NOT EXISTS(SELECT 1 FROM public.memories m JOIN public.memory_revisions r ON r.org_id=m.org_id AND r.id=m.current_revision_id
    WHERE m.org_id=p_org AND m.id=(memory_value->>'memory_id')::uuid AND m.scope='org' AND m.deleted_at IS NULL
     AND r.id=(memory_value->>'revision_id')::uuid AND r.revision=(memory_value->>'revision')::integer AND r.status='active'
     AND r.content_sha256=memory_value->>'content_sha256' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())) THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION memory_generation_current(p_org uuid,p_job uuid) RETURNS boolean
LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT p_job IS NULL OR EXISTS(SELECT 1 FROM public.card_generation_runs
 WHERE org_id=p_org AND generation_job_id=p_job AND public.memory_manifest_current(p_org,input_manifest->'memory'))
$$;

CREATE FUNCTION memory_card_confirmation_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE prior public.response_card_revisions%ROWTYPE;
BEGIN
 SELECT * INTO prior FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id AND revision=NEW.revision-1;
 IF NEW.state='confirmed' AND prior.state IS DISTINCT FROM 'confirmed' AND NEW.model_job_id IS NOT NULL
  AND public.memory_generation_current(NEW.org_id,NEW.model_job_id) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Memory input changed; edit or regenerate before confirmation' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION memory_retrieval_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE actor_kind text;
BEGIN
 actor_kind:=current_setting('app.actor_kind',true);
 PERFORM public.response_check_actor(NEW.org_id,NEW.user_id,NEW.actor_token_id,actor_kind);
 IF actor_kind NOT IN ('session','token','worker') OR NEW.data->>'org_id' IS DISTINCT FROM NEW.org_id::text
  OR NEW.data->>'retrieval_id' IS DISTINCT FROM NEW.id::text
  OR NEW.data->'preview' IS DISTINCT FROM 'false'::jsonb
  OR NEW.data->'currently_valid' IS DISTINCT FROM 'true'::jsonb
  OR NEW.data->'scopes' IS DISTINCT FROM '["org"]'::jsonb THEN
  RAISE EXCEPTION 'Memory retrieval requires an authenticated org binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION memory_retrieval_item_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE retrieval_row public.memory_retrievals%ROWTYPE; retrieval_xid text; revision_row public.memory_revisions%ROWTYPE;
BEGIN
 SELECT * INTO retrieval_row FROM public.memory_retrievals WHERE org_id=NEW.org_id AND id=NEW.retrieval_id;
 SELECT xmin::text INTO retrieval_xid FROM public.memory_retrievals WHERE org_id=NEW.org_id AND id=NEW.retrieval_id;
 SELECT * INTO revision_row FROM public.memory_revisions WHERE org_id=NEW.org_id AND id=NEW.memory_revision_id;
 IF retrieval_xid IS DISTINCT FROM pg_current_xact_id()::text OR revision_row.status IS DISTINCT FROM 'active'
  OR revision_row.content_sha256 IS DISTINCT FROM NEW.content_sha256
  OR NEW.sent_sha256 IS DISTINCT FROM encode(sha256(convert_to(revision_row.content->>'text','UTF8')),'hex')
  OR NOT EXISTS(SELECT 1 FROM public.memories WHERE org_id=NEW.org_id AND id=NEW.memory_id AND current_revision_id=NEW.memory_revision_id AND deleted_at IS NULL)
  OR (revision_row.expires_at IS NOT NULL AND revision_row.expires_at<=clock_timestamp()) THEN
  RAISE EXCEPTION 'Memory retrieval items must bind current active revisions atomically' USING ERRCODE='23514'; END IF;
 PERFORM public.response_check_actor(NEW.org_id,retrieval_row.user_id,retrieval_row.actor_token_id,current_setting('app.actor_kind',true));
 RETURN NEW;
END $$;

CREATE FUNCTION memory_call_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; retrieval_row public.memory_retrievals%ROWTYPE;
 ref jsonb; requirement_value jsonb; usage_row public.usage_records%ROWTYPE; submitted jsonb;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory call lineage cannot be deleted' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
 submitted:=job_row.result->'submission';
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['state','usage_record_id']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','usage_record_id'])
   OR OLD.state='completed' OR NEW.state NOT IN ('completed','unknown') THEN
   RAISE EXCEPTION 'Memory call inputs cannot change after admission' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.vendor_calls WHERE org_id=NEW.org_id AND id=NEW.call_id AND job_id=NEW.job_id AND run_id=NEW.run_id AND state=NEW.state) THEN
   RAISE EXCEPTION 'Memory settlement must match vendor outcome' USING ERRCODE='23514'; END IF;
  IF NEW.usage_record_id IS NOT NULL THEN
   SELECT * INTO usage_row FROM public.usage_records WHERE org_id=NEW.org_id AND id=NEW.usage_record_id;
   IF usage_row.id IS NULL OR ROW(usage_row.job_id,usage_row.run_id,usage_row.call_id) IS DISTINCT FROM ROW(NEW.job_id,NEW.run_id,NEW.call_id) THEN
    RAISE EXCEPTION 'Memory call usage binding mismatch' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 PERFORM public.response_check_actor(NEW.org_id,(submitted->>'actor_user_id')::uuid,(submitted->>'actor_token_id')::uuid,'worker');
 SELECT * INTO retrieval_row FROM public.memory_retrievals WHERE org_id=NEW.org_id AND id=NEW.retrieval_id;
 IF job_row.kind IS DISTINCT FROM 'card_generate' OR job_row.status IS DISTINCT FROM 'running' OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR NEW.state<>'admitted' OR NEW.usage_record_id IS NOT NULL
  OR retrieval_row.task_id IS DISTINCT FROM NEW.task_id
  OR retrieval_row.user_id IS DISTINCT FROM (submitted->>'actor_user_id')::uuid
  OR retrieval_row.actor_token_id IS DISTINCT FROM (submitted->>'actor_token_id')::uuid
  OR NEW.retrieval_id IS DISTINCT FROM (submitted->>'memory_retrieval_id')::uuid
  OR NEW.prompt_version IS DISTINCT FROM submitted->'input_manifest'->>'prompt_version'
  OR NEW.manifest_sha256 IS DISTINCT FROM submitted->'input_manifest'->'memory'->>'manifest_sha256'
  OR NEW.memories IS DISTINCT FROM submitted->'input_manifest'->'memory'->'memories'
  OR NEW.manifest_sha256 IS DISTINCT FROM retrieval_row.data->>'manifest_sha256'
  OR public.memory_manifest_current(NEW.org_id,submitted->'input_manifest'->'memory') IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Memory call requires the live attempt and current input' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.memories)<>(SELECT count(*) FROM public.memory_retrieval_items WHERE org_id=NEW.org_id AND retrieval_id=NEW.retrieval_id AND selected)
  OR (SELECT count(DISTINCT value->>'revision_id') FROM jsonb_array_elements(NEW.memories))<>jsonb_array_length(NEW.memories)
  OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.requirement_ids))<>jsonb_array_length(NEW.requirement_ids) THEN
  RAISE EXCEPTION 'Memory call input list must be exact and unique' USING ERRCODE='23514'; END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(NEW.memories) LOOP
  IF NOT EXISTS(SELECT 1 FROM public.memory_retrieval_items WHERE org_id=NEW.org_id AND retrieval_id=NEW.retrieval_id AND selected
   AND memory_id=(ref->>'memory_id')::uuid AND memory_revision_id=(ref->>'revision_id')::uuid
   AND revision=(ref->>'revision')::integer AND content_sha256=ref->>'content_sha256' AND sent_sha256=ref->>'sent_sha256') THEN
   RAISE EXCEPTION 'Memory call contains an unselected revision' USING ERRCODE='23514'; END IF;
 END LOOP;
 FOR requirement_value IN SELECT value FROM jsonb_array_elements(NEW.requirement_ids) LOOP
  IF NOT EXISTS(SELECT 1 FROM public.requirements WHERE org_id=NEW.org_id AND id=(requirement_value#>>'{}')::uuid AND task_id=NEW.task_id)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(submitted->'input_manifest'->'requirements') value
    WHERE coalesce(value->>'id',value->>'requirement_id')=requirement_value#>>'{}') THEN
   RAISE EXCEPTION 'Memory call requirement is outside its fixed generation input' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
"""

TRIGGER_SQL = r"""
CREATE TRIGGER memory_parent_gate BEFORE INSERT OR UPDATE OR DELETE ON memories FOR EACH ROW EXECUTE FUNCTION memory_parent_gate();
CREATE TRIGGER memory_epoch_gate BEFORE UPDATE OR DELETE ON memory_scope_epochs FOR EACH ROW EXECUTE FUNCTION memory_epoch_gate();
CREATE TRIGGER memory_revision_gate BEFORE INSERT ON memory_revisions FOR EACH ROW EXECUTE FUNCTION memory_revision_gate();
CREATE CONSTRAINT TRIGGER memory_revision_complete AFTER INSERT ON memory_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory_revision_complete();
CREATE TRIGGER memory_feedback_gate BEFORE INSERT ON memory_feedback_events FOR EACH ROW EXECUTE FUNCTION memory_feedback_gate();
CREATE TRIGGER memory_eval_gate BEFORE INSERT OR UPDATE OR DELETE ON memory_eval_samples FOR EACH ROW EXECUTE FUNCTION memory_eval_gate();
CREATE TRIGGER memory_retrieval_gate BEFORE INSERT ON memory_retrievals FOR EACH ROW EXECUTE FUNCTION memory_retrieval_gate();
CREATE TRIGGER memory_retrieval_item_gate BEFORE INSERT ON memory_retrieval_items FOR EACH ROW EXECUTE FUNCTION memory_retrieval_item_gate();
CREATE TRIGGER memory_call_gate BEFORE INSERT OR UPDATE OR DELETE ON memory_call_inputs FOR EACH ROW EXECUTE FUNCTION memory_call_gate();
CREATE CONSTRAINT TRIGGER memory_card_confirmation_gate AFTER INSERT ON response_card_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memory_card_confirmation_gate();
"""
