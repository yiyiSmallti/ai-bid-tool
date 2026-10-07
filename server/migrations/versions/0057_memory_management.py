"""Indexes for bounded memory management and recorded feedback job recovery."""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(PROJECTION_SQL)


def downgrade():
    raise RuntimeError("Preserve memory history and repair forward")


SCHEMA_SQL = r"""
CREATE FUNCTION public.management_memory_vector(p_text text, p_content jsonb, p_tags text[])
RETURNS tsvector LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig, regexp_replace(
   public.management_casefold(coalesce(p_text,'') || ' ' || coalesce(p_content->>'conflict_key','')
     || ' ' || coalesce(array_to_string(p_tags,' '),'')), '[^[:alnum:]]+', ' ', 'g'))
$$;
ALTER TABLE public.memories
 ADD COLUMN search_vector tsvector NOT NULL DEFAULT ''::tsvector,
 ADD COLUMN search_tags text[] NOT NULL DEFAULT '{}',
 ADD COLUMN search_kind text,
 ADD COLUMN search_status text,
 ADD COLUMN search_expires_at timestamptz;
CREATE INDEX management_memory_browse ON public.memories(org_id,created_at DESC,id DESC);
CREATE INDEX management_memory_history ON public.memory_revisions(org_id,memory_id,revision DESC,id DESC);
CREATE INDEX management_memory_active_history ON public.memory_revisions(org_id,memory_id)
 WHERE status='active';
CREATE INDEX management_memory_prefix ON public.memories USING gin(search_vector);
CREATE INDEX management_memory_tags ON public.memories USING gin(search_tags);
CREATE INDEX management_memory_filter ON public.memories(org_id,search_status,search_kind,search_expires_at,created_at DESC,id DESC);
ALTER TABLE public.audit_logs ADD COLUMN memory_revision_id_text text
 GENERATED ALWAYS AS (details->>'revision_id') STORED;
CREATE INDEX management_memory_audit_author ON public.audit_logs(org_id,memory_revision_id_text,object_id)
 WHERE action IN ('memory.create','memory.update','memory.approve','memory.reject','memory.disable','memory.delete','memory.candidate.publish');
CREATE INDEX management_memory_feedback_browse ON public.memory_feedback_events(org_id,task_id,created_at DESC,id DESC);
CREATE INDEX management_memory_candidate_events ON public.jobs USING gin((result->'submission'->'event_ids'))
 WHERE kind='memory_candidate';
CREATE INDEX management_memory_candidate_task ON public.jobs(org_id,task_id,created_at DESC,id DESC)
 WHERE kind='memory_candidate';
REVOKE ALL ON FUNCTION public.management_memory_vector(text,jsonb,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_memory_vector(text,jsonb,text[]) TO bid_app;
"""


# Replaces only the memory root guard, preserving its original owner, tombstone,
# sequential revision and epoch behavior; search fields are never approval state.
PROJECTION_SQL = r"""
CREATE OR REPLACE FUNCTION public.memory_parent_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE current_row public.memory_revisions%ROWTYPE; previous_row public.memory_revisions%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Memory deletion must preserve history' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND
  ROW(NEW.search_vector,NEW.search_tags,NEW.search_kind,NEW.search_status,NEW.search_expires_at)
  IS DISTINCT FROM ROW(OLD.search_vector,OLD.search_tags,OLD.search_kind,OLD.search_status,OLD.search_expires_at) AND
  (to_jsonb(NEW)-ARRAY['search_vector','search_tags','search_kind','search_status','search_expires_at']) IS NOT DISTINCT FROM
  (to_jsonb(OLD)-ARRAY['search_vector','search_tags','search_kind','search_status','search_expires_at']) THEN
  SELECT * INTO current_row FROM public.memory_revisions
   WHERE org_id=NEW.org_id AND memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  IF current_row.id IS NULL OR
   ROW(NEW.search_vector,NEW.search_tags,NEW.search_kind,NEW.search_status,NEW.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(current_row.normalized_text,current_row.content,current_row.normalized_tags),
     current_row.normalized_tags,current_row.content->>'kind',current_row.status::text,current_row.expires_at) THEN
   RAISE EXCEPTION 'Memory search must match the exact current revision' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 INSERT INTO public.memory_scope_epochs(org_id,scope,owner_id,epoch) VALUES(NEW.org_id,'org',NEW.org_id,0) ON CONFLICT DO NOTHING;
 PERFORM 1 FROM public.memory_scope_epochs WHERE org_id=NEW.org_id AND scope='org' AND owner_id=NEW.org_id FOR UPDATE;
 IF TG_OP='INSERT' THEN
  IF NEW.revision<>1 OR NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Initial memory must be a candidate' USING ERRCODE='23514'; END IF;
 ELSE
  IF OLD.deleted_at IS NOT NULL OR NEW.revision<>OLD.revision+1
   OR (to_jsonb(NEW)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['revision','current_revision_id','deleted_at','search_vector','search_tags','search_kind','search_status','search_expires_at']) THEN
   RAISE EXCEPTION 'Memory owner and history cannot change' USING ERRCODE='23514'; END IF;
  SELECT * INTO current_row FROM public.memory_revisions WHERE org_id=NEW.org_id AND memory_id=NEW.id AND id=NEW.current_revision_id AND revision=NEW.revision;
  SELECT * INTO previous_row FROM public.memory_revisions WHERE org_id=OLD.org_id AND id=OLD.current_revision_id;
  IF current_row.id IS NULL OR ((current_row.decision IS NOT DISTINCT FROM 'delete') IS DISTINCT FROM (NEW.deleted_at IS NOT NULL)) THEN
   RAISE EXCEPTION 'Memory pointer or tombstone mismatch' USING ERRCODE='23514'; END IF;
  IF current_row.status='active' OR previous_row.status='active' THEN
   UPDATE public.memory_scope_epochs SET epoch=epoch+1 WHERE org_id=NEW.org_id AND scope='org' AND owner_id=NEW.org_id;
  END IF;
 END IF;
 -- A real revision can leave the search projection unchanged. Do not issue a
 -- nested no-op UPDATE: it must remain forbidden by the durable-history gate.
 UPDATE public.memories m SET
  search_vector=public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
  search_tags=r.normalized_tags, search_kind=r.content->>'kind',
  search_status=r.status, search_expires_at=r.expires_at
 FROM public.memory_revisions r
 WHERE m.org_id=NEW.org_id AND m.id=NEW.id AND r.org_id=m.org_id
  AND r.memory_id=m.id AND r.id=m.current_revision_id AND r.revision=m.revision
  AND ROW(m.search_vector,m.search_tags,m.search_kind,m.search_status,m.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
    r.normalized_tags,r.content->>'kind',r.status::text,r.expires_at);
 RETURN NEW;
END $$;


DROP TRIGGER memory_parent_gate ON public.memories;
CREATE TRIGGER memory_parent_gate AFTER INSERT OR UPDATE OR DELETE ON public.memories
 FOR EACH ROW EXECUTE FUNCTION public.memory_parent_gate();
CREATE FUNCTION public.management_memory_initial_projection() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.memories m SET
  search_vector=public.management_memory_vector(NEW.normalized_text,NEW.content,NEW.normalized_tags),
  search_tags=NEW.normalized_tags,search_kind=NEW.content->>'kind',
  search_status=NEW.status,search_expires_at=NEW.expires_at
 WHERE m.org_id=NEW.org_id AND m.id=NEW.memory_id
  AND m.current_revision_id=NEW.id AND m.revision=NEW.revision
  AND ROW(m.search_vector,m.search_tags,m.search_kind,m.search_status,m.search_expires_at)
   IS DISTINCT FROM ROW(public.management_memory_vector(NEW.normalized_text,NEW.content,NEW.normalized_tags),
    NEW.normalized_tags,NEW.content->>'kind',NEW.status::text,NEW.expires_at);
 RETURN NEW;
END $$;
CREATE TRIGGER zz_management_memory_initial_projection AFTER INSERT ON public.memory_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_memory_initial_projection();
UPDATE public.memories m SET
 search_vector=public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
 search_tags=r.normalized_tags,search_kind=r.content->>'kind',
 search_status=r.status,search_expires_at=r.expires_at
FROM public.memory_revisions r
WHERE r.org_id=m.org_id AND r.memory_id=m.id AND r.id=m.current_revision_id AND r.revision=m.revision
 AND ROW(m.search_vector,m.search_tags,m.search_kind,m.search_status,m.search_expires_at)
  IS DISTINCT FROM ROW(public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags),
   r.normalized_tags,r.content->>'kind',r.status::text,r.expires_at);
REVOKE ALL ON FUNCTION public.management_memory_initial_projection() FROM PUBLIC;
"""
