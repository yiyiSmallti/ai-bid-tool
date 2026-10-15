"""Private memory: one member's own memory beside the public library.

A private memory names its owner in `owner_member_id`; library memory has
none. Each owner has an epoch row of their own, so a change to private memory
invalidates only that member's fixed drafting inputs. Only the owner writes or
reviews it, checked at the database as well as in the service through
`app.actor_member_id`; a drafting manifest that read private memory records
the owner in its private epoch.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
ALTER TABLE public.memories
    ADD COLUMN owner_member_id uuid REFERENCES public.members(id);
ALTER TABLE public.memories DROP CONSTRAINT memories_check;
ALTER TABLE public.memories ADD CONSTRAINT memories_check CHECK (((task_id IS NULL) AND ((((scope)::text = 'library'::text) AND (owner_member_id IS NULL)) OR (((scope)::text = 'private'::text) AND (owner_member_id IS NOT NULL)))));
CREATE INDEX memory_private_owner ON public.memories USING btree (owner_member_id) WHERE (owner_member_id IS NOT NULL);

ALTER TABLE public.memory_scope_epochs
    ADD COLUMN owner_member_id uuid REFERENCES public.members(id);
ALTER TABLE public.memory_scope_epochs DROP CONSTRAINT memory_scope_epochs_pkey;
ALTER TABLE public.memory_scope_epochs DROP CONSTRAINT memory_scope_epochs_check;
ALTER TABLE public.memory_scope_epochs ADD CONSTRAINT memory_scope_epochs_key UNIQUE NULLS NOT DISTINCT (scope, owner_member_id);
ALTER TABLE public.memory_scope_epochs ADD CONSTRAINT memory_scope_epochs_check CHECK (((epoch >= 0) AND ((((scope)::text = 'library'::text) AND (owner_member_id IS NULL)) OR (((scope)::text = 'private'::text) AND (owner_member_id IS NOT NULL)))));

CREATE OR REPLACE FUNCTION public.memory_epoch_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory epochs cannot be removed' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (NEW.scope IS DISTINCT FROM OLD.scope
  OR NEW.owner_member_id IS DISTINCT FROM OLD.owner_member_id
  OR NEW.epoch<OLD.epoch) THEN RAISE EXCEPTION 'Memory epoch cannot move backwards or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.memory_manifest_current(p_manifest jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE epoch_value jsonb; memory_value jsonb; owner uuid;
BEGIN
 IF p_manifest IS NULL THEN RETURN true; END IF;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope='library' AND owner_member_id IS NULL FOR SHARE;
 IF jsonb_typeof(p_manifest) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_manifest->'epochs') IS DISTINCT FROM 'array'
  OR NOT coalesce(p_manifest->'scopes' IN ('["library"]'::jsonb,'["library","private"]'::jsonb),false)
  OR p_manifest->>'retrieval_version' IS DISTINCT FROM 'keyword-v1'
  OR p_manifest->>'priority_version' IS DISTINCT FROM 'memory-priority-v1'
  OR p_manifest->>'sanitizer_version' IS DISTINCT FROM 'memory-sanitize-v1:bid-redaction-v3'
  OR jsonb_typeof(p_manifest->'memories') IS DISTINCT FROM 'array'
  OR p_manifest->'scopes' IS DISTINCT FROM (SELECT jsonb_agg(value->'scope' ORDER BY ordinality) FROM jsonb_array_elements(p_manifest->'epochs') WITH ORDINALITY)
  OR ((p_manifest->>'valid_until')::timestamptz IS NOT NULL AND (p_manifest->>'valid_until')::timestamptz<=clock_timestamp()) THEN RETURN false; END IF;
 -- Private memory carries its owner in its epoch; library memory has none.
 SELECT (value->>'owner_member_id')::uuid INTO owner FROM jsonb_array_elements(p_manifest->'epochs') value WHERE value->>'scope'='private';
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope='private' AND owner_member_id=owner FOR SHARE;
 FOR epoch_value IN SELECT value FROM jsonb_array_elements(p_manifest->'epochs') LOOP
  IF (epoch_value->>'scope'='private') IS DISTINCT FROM (epoch_value ? 'owner_member_id')
   OR (epoch_value->>'scope'='private' AND owner IS NULL)
   OR (epoch_value->>'epoch')::bigint IS DISTINCT FROM coalesce((SELECT epoch FROM public.memory_scope_epochs WHERE scope=epoch_value->>'scope' AND owner_member_id IS NOT DISTINCT FROM (epoch_value->>'owner_member_id')::uuid),0) THEN RETURN false; END IF;
 END LOOP;
 FOR memory_value IN SELECT value FROM jsonb_array_elements(p_manifest->'memories') LOOP
  IF NOT EXISTS(SELECT 1 FROM public.memories m JOIN public.memory_revisions r ON r.id=m.current_revision_id
    WHERE m.id=(memory_value->>'memory_id')::uuid AND (m.scope='library' OR (m.scope='private' AND m.owner_member_id=owner)) AND m.deleted_at IS NULL
     AND r.id=(memory_value->>'revision_id')::uuid AND r.revision=(memory_value->>'revision')::integer AND r.status='active'
     AND r.content_sha256=memory_value->>'content_sha256' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())) THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION public.memory_parent_gate() RETURNS trigger
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
 INSERT INTO public.memory_scope_epochs(scope,owner_member_id,epoch) VALUES(NEW.scope,NEW.owner_member_id,0) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope=NEW.scope AND owner_member_id IS NOT DISTINCT FROM NEW.owner_member_id FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF NEW.revision<>1 OR NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Initial memory must be a candidate' USING ERRCODE='23514'; END IF;
  IF NEW.scope='private' AND NEW.owner_member_id IS DISTINCT FROM nullif(current_setting('app.actor_member_id',true),'')::uuid THEN
   RAISE EXCEPTION 'Private memory belongs to the acting member' USING ERRCODE='42501'; END IF;
 ELSE
  IF OLD.deleted_at IS NOT NULL OR NEW.revision<>OLD.revision+1
   OR (to_jsonb(NEW)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) THEN
   RAISE EXCEPTION 'Memory owner and history cannot change' USING ERRCODE='23514'; END IF;
  SELECT * INTO current_row FROM public.memory_revisions WHERE memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  SELECT * INTO previous_row FROM public.memory_revisions WHERE id=OLD.current_revision_id;
  IF current_row.id IS NULL OR ((current_row.decision IS NOT DISTINCT FROM 'delete') IS DISTINCT FROM (NEW.deleted_at IS NOT NULL)) THEN
   RAISE EXCEPTION 'Memory pointer or tombstone mismatch' USING ERRCODE='23514'; END IF;
  IF current_row.status='active' OR previous_row.status='active' THEN
   UPDATE public.memory_scope_epochs SET epoch=epoch+1 WHERE scope=NEW.scope AND owner_member_id IS NOT DISTINCT FROM NEW.owner_member_id;
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

CREATE OR REPLACE FUNCTION public.memory_retrieval_gate() RETURNS trigger
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
  OR NOT coalesce(NEW.data->'scopes' IN ('["library"]'::jsonb,'["library","private"]'::jsonb),false) THEN
  RAISE EXCEPTION 'Memory retrieval requires an authenticated org binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.memory_revision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE parent_row public.memories%ROWTYPE; prior public.memory_revisions%ROWTYPE;
 job_row public.jobs%ROWTYPE; event_row public.memory_feedback_events%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 IF NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(NEW.content),'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Memory content hash mismatch' USING ERRCODE='23514'; END IF;
 -- The scope and owner never change, so read them before taking the epoch lock.
 SELECT * INTO parent_row FROM public.memories WHERE id=NEW.memory_id;
 PERFORM 1 FROM public.memory_scope_epochs WHERE scope=parent_row.scope AND owner_member_id IS NOT DISTINCT FROM parent_row.owner_member_id FOR UPDATE;
 SELECT * INTO parent_row FROM public.memories WHERE id=NEW.memory_id FOR UPDATE;
 IF parent_row.scope='private' AND parent_row.owner_member_id IS DISTINCT FROM nullif(current_setting('app.actor_member_id',true),'')::uuid THEN
  RAISE EXCEPTION 'Private memory belongs to the acting member' USING ERRCODE='42501'; END IF;
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
   WHERE m.id<>NEW.memory_id AND m.scope=parent_row.scope
    AND m.owner_member_id IS NOT DISTINCT FROM parent_row.owner_member_id AND m.deleted_at IS NULL AND r.status='active'
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
"""
    )


def downgrade():
    raise NotImplementedError("Private memory has no earlier representation")
