"""Human requirement assignment and immutable encrypted card discussion."""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(GUARDS_SQL)
    from app.services.task_event_sql import install_collaboration

    install_collaboration(op)


SCHEMA_SQL = r"""
CREATE TABLE requirement_workflows (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, extraction_job_id uuid NOT NULL, requirement_id uuid NOT NULL,
 assignee_user_id uuid, assignment_revision integer NOT NULL DEFAULT 0 CHECK(assignment_revision>=0),
 changed_by_user_id uuid, changed_at timestamptz, last_reason_ciphertext text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,extraction_job_id,requirement_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,requirement_id,task_id,extraction_job_id) REFERENCES requirements(org_id,id,task_id,job_id),
 FOREIGN KEY(org_id,task_id,assignee_user_id) REFERENCES task_members(org_id,task_id,user_id),
 FOREIGN KEY(org_id,assignee_user_id) REFERENCES memberships(org_id,user_id),
 FOREIGN KEY(org_id,changed_by_user_id) REFERENCES memberships(org_id,user_id),
 CHECK((assignment_revision=0 AND assignee_user_id IS NULL AND changed_by_user_id IS NULL
        AND changed_at IS NULL AND last_reason_ciphertext IS NULL)
    OR (assignment_revision>0 AND changed_by_user_id IS NOT NULL AND changed_at IS NOT NULL
        AND last_reason_ciphertext IS NOT NULL AND length(last_reason_ciphertext)>0))
);
CREATE INDEX requirement_workflow_assignee ON requirement_workflows(org_id,task_id,assignee_user_id);
CREATE TABLE card_comment_threads (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, created_card_revision_id uuid NOT NULL,
 revision integer NOT NULL DEFAULT 1 CHECK(revision=1), created_by_user_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,card_id,id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,card_id,task_id) REFERENCES response_cards(org_id,id,task_id),
 FOREIGN KEY(org_id,card_id,created_card_revision_id) REFERENCES response_card_revisions(org_id,card_id,id),
 FOREIGN KEY(org_id,task_id,created_by_user_id) REFERENCES task_members(org_id,task_id,user_id),
 FOREIGN KEY(org_id,created_by_user_id) REFERENCES memberships(org_id,user_id)
);
CREATE INDEX card_comment_thread_page ON card_comment_threads(org_id,task_id,card_id,created_at,id);
CREATE TABLE card_comments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, thread_id uuid NOT NULL, author_user_id uuid NOT NULL,
 body_ciphertext text NOT NULL CHECK(length(body_ciphertext)>0 AND octet_length(body_ciphertext)<=24000),
 body_sha256 varchar(64) NOT NULL CHECK(body_sha256~'^[0-9a-f]{64}$'),
 request_sha256 varchar(64) NOT NULL CHECK(request_sha256~'^[0-9a-f]{64}$'),
 client_request_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,card_id,thread_id,id),
 UNIQUE(org_id,task_id,author_user_id,client_request_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,author_user_id) REFERENCES memberships(org_id,user_id),
 FOREIGN KEY(org_id,task_id,card_id,thread_id) REFERENCES card_comment_threads(org_id,task_id,card_id,id),
 FOREIGN KEY(org_id,task_id,author_user_id) REFERENCES task_members(org_id,task_id,user_id)
);
CREATE INDEX card_comment_page ON card_comments(org_id,card_id,created_at,id);
CREATE INDEX card_comment_thread_messages ON card_comments(org_id,task_id,card_id,thread_id,created_at,id);
CREATE TABLE card_comment_mentions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, thread_id uuid NOT NULL, comment_id uuid NOT NULL,
 user_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,comment_id,user_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,user_id) REFERENCES memberships(org_id,user_id),
 FOREIGN KEY(org_id,task_id,card_id,thread_id,comment_id) REFERENCES card_comments(org_id,task_id,card_id,thread_id,id),
 FOREIGN KEY(org_id,task_id,user_id) REFERENCES task_members(org_id,task_id,user_id)
);
CREATE INDEX card_comment_mention_recipient ON card_comment_mentions(org_id,user_id,comment_id);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['requirement_workflows','card_comment_threads','card_comments','card_comment_mentions'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON public.%I USING (org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid) WITH CHECK (org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO bid_app',t);
 END LOOP;
END $$;
GRANT UPDATE ON public.requirement_workflows TO bid_app;
"""

GUARDS_SQL = r"""
CREATE FUNCTION public.task_collaboration_human(p_org uuid,p_task uuid,p_scope text) RETURNS uuid
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid; lifecycle text;
BEGIN
 IF p_org IS DISTINCT FROM nullif(current_setting('app.current_org',true),'')::uuid
   OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
   OR coalesce(current_setting('app.actor_token_id',true),'')<>'' THEN
  RAISE EXCEPTION 'human task collaboration required' USING ERRCODE='42501';
 END IF;
 -- Serialize with assignment/member/handover/archive operations before checking authority.
 PERFORM 1 FROM public.tasks WHERE org_id=p_org AND id=p_task FOR UPDATE;
 SELECT state INTO lifecycle FROM public.task_workflows WHERE org_id=p_org AND task_id=p_task FOR UPDATE;
 IF lifecycle IS DISTINCT FROM 'active' THEN RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 IF NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ?& ARRAY[p_scope,'task:read']
   OR (p_scope='card:comment' AND NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? 'card:read') THEN
  RAISE EXCEPTION 'task collaboration scope required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
   JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=uid AND m.active AND u.active AND o.active) THEN
  RAISE EXCEPTION 'active human membership required' USING ERRCODE='42501'; END IF;
 IF p_scope='card:assign' THEN
  PERFORM public.task_workflow_human(p_org,p_task,p_scope);
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.task_members m WHERE m.org_id=p_org AND m.task_id=p_task AND m.user_id=uid AND m.active) THEN
   RAISE EXCEPTION 'current task membership required' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN uid;
END $$;

CREATE FUNCTION public.requirement_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'assignment history is retained' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
   AND (to_jsonb(NEW)-'last_reason_ciphertext') IS NOT DISTINCT FROM (to_jsonb(OLD)-'last_reason_ciphertext') THEN
  RETURN NEW;
 END IF;
 uid=public.task_collaboration_human(NEW.org_id,NEW.task_id,'card:assign');
 IF TG_OP='INSERT' THEN
  IF NEW.assignment_revision<>1 THEN RAISE EXCEPTION 'initial assignment revision' USING ERRCODE='23514'; END IF;
 ELSE
  IF (NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id,NEW.id,NEW.created_at) IS DISTINCT FROM
    (OLD.org_id,OLD.task_id,OLD.extraction_job_id,OLD.requirement_id,OLD.id,OLD.created_at) THEN
   RAISE EXCEPTION 'immutable assignment identity' USING ERRCODE='23514'; END IF;
  IF NEW.assignment_revision<>OLD.assignment_revision+1 THEN RAISE EXCEPTION 'assignment revision conflict' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.changed_by_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'invalid assignment actor' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.requirements r JOIN public.jobs j ON j.org_id=r.org_id AND j.id=r.job_id
  WHERE r.org_id=NEW.org_id AND r.task_id=NEW.task_id AND r.id=NEW.requirement_id AND r.job_id=NEW.extraction_job_id
   AND j.task_id=NEW.task_id AND j.document_id=r.document_id
   AND j.kind='extract' AND j.status='succeeded') THEN
  RAISE EXCEPTION 'saved extraction requirement required' USING ERRCODE='23514'; END IF;
 IF NEW.assignee_user_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM public.task_members tm JOIN public.memberships m ON m.org_id=tm.org_id AND m.user_id=tm.user_id
    JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
    WHERE tm.org_id=NEW.org_id AND tm.task_id=NEW.task_id AND tm.user_id=NEW.assignee_user_id
      AND tm.active AND tm.role IN ('owner','contributor') AND m.active AND u.active AND o.active
      AND m.role IN ('admin','bidder','technical')) THEN
  RAISE EXCEPTION 'active editable assignee required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_assignment_guard BEFORE INSERT OR UPDATE OR DELETE ON public.requirement_workflows
 FOR EACH ROW EXECUTE FUNCTION public.requirement_assignment_guard();

CREATE FUNCTION public.card_discussion_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid; parent_author uuid;
BEGIN
 IF TG_TABLE_NAME='card_comments' AND TG_OP='UPDATE'
   AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
   AND (to_jsonb(NEW)-'body_ciphertext') IS NOT DISTINCT FROM (to_jsonb(OLD)-'body_ciphertext') THEN
  RETURN NEW;
 END IF;
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'card discussion is immutable' USING ERRCODE='42501'; END IF;
 uid=public.task_collaboration_human(NEW.org_id,NEW.task_id,'card:comment');
 IF TG_TABLE_NAME='card_comment_threads' THEN
  IF NEW.created_by_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'invalid discussion actor' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.response_cards c WHERE c.org_id=NEW.org_id AND c.task_id=NEW.task_id
   AND c.id=NEW.card_id AND c.current_revision_id=NEW.created_card_revision_id) THEN
   RAISE EXCEPTION 'current card revision required' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='card_comments' THEN
  IF NEW.author_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'invalid discussion actor' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.card_comment_threads t WHERE t.org_id=NEW.org_id AND t.task_id=NEW.task_id
   AND t.card_id=NEW.card_id AND t.id=NEW.thread_id) THEN
   RAISE EXCEPTION 'discussion parent mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT author_user_id INTO parent_author FROM public.card_comments c WHERE c.org_id=NEW.org_id AND c.task_id=NEW.task_id
   AND c.card_id=NEW.card_id AND c.thread_id=NEW.thread_id AND c.id=NEW.comment_id;
  IF parent_author IS DISTINCT FROM uid THEN RAISE EXCEPTION 'mention must belong to actor comment' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.task_members tm JOIN public.memberships m ON m.org_id=tm.org_id AND m.user_id=tm.user_id
    JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
    WHERE tm.org_id=NEW.org_id AND tm.task_id=NEW.task_id AND tm.user_id=NEW.user_id AND tm.active AND m.active AND u.active AND o.active) THEN
   RAISE EXCEPTION 'current mention recipient required' USING ERRCODE='23514'; END IF;
  IF (SELECT count(*) FROM public.card_comment_mentions WHERE org_id=NEW.org_id AND comment_id=NEW.comment_id)>=20 THEN
   RAISE EXCEPTION 'mention limit exceeded' USING ERRCODE='23514'; END IF;
  -- Mentions form part of the immutable request. A later transaction cannot append
  -- recipients to a prior comment, bypassing its request hash and replay receipt.
  IF NOT EXISTS(SELECT 1 FROM public.card_comments c WHERE c.org_id=NEW.org_id AND c.id=NEW.comment_id
    AND c.xmin=pg_current_xact_id()::xid) THEN
   RAISE EXCEPTION 'mentions must be inserted with comment' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['card_comment_threads','card_comments','card_comment_mentions'] LOOP
  EXECUTE format('CREATE TRIGGER card_discussion_guard BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.card_discussion_guard()',t);
 END LOOP;
END $$;

CREATE FUNCTION public.card_thread_first_message() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.card_comments c WHERE c.org_id=NEW.org_id AND c.task_id=NEW.task_id
  AND c.card_id=NEW.card_id AND c.thread_id=NEW.id AND c.author_user_id=NEW.created_by_user_id) THEN
  RAISE EXCEPTION 'thread requires first message from creator' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER card_thread_first_message AFTER INSERT ON public.card_comment_threads
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.card_thread_first_message();

CREATE FUNCTION public.task_member_assignment_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 -- No guard on Membership/User/Org: external revocation must never be blocked.
 -- Lock the same task row as assignment before examining outstanding work.
 PERFORM 1 FROM public.tasks WHERE org_id=OLD.org_id AND id=OLD.task_id FOR UPDATE;
 IF (NOT NEW.active OR NEW.role NOT IN ('owner','contributor')) AND EXISTS(
   SELECT 1 FROM public.requirement_workflows w WHERE w.org_id=OLD.org_id AND w.task_id=OLD.task_id AND w.assignee_user_id=OLD.user_id) THEN
  RAISE EXCEPTION 'member_has_assignments' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER aaa_task_member_assignment BEFORE UPDATE ON public.task_members
 FOR EACH ROW EXECUTE FUNCTION public.task_member_assignment_guard();
REVOKE ALL ON FUNCTION public.task_collaboration_human(uuid,uuid,text),public.requirement_assignment_guard(),
 public.card_discussion_guard(),public.card_thread_first_message(),public.task_member_assignment_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.task_collaboration_human(uuid,uuid,text) TO bid_app;
"""


def downgrade():
    raise RuntimeError(
        "Retain assignment and discussion history; disable writes and repair forward"
    )
