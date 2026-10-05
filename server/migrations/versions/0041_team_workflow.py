"""Explicit task membership, lifecycle and durable event ordering.

Existing tasks require the reviewed offline import command before cutover. The
migration never guesses an owner or opens every task to every organization user.
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(GUARD_SQL)
    op.execute(ARCHIVE_SQL)
    op.execute(HUMAN_CONTENT_SQL)
    from app.services.task_event_sql import install

    install(op)


SCHEMA_SQL = r"""
CREATE TABLE task_members (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, user_id uuid NOT NULL, role text NOT NULL,
 review_domains jsonb NOT NULL DEFAULT '[]', active boolean NOT NULL DEFAULT true,
 revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
 workflow_revision integer NOT NULL DEFAULT 1 CHECK(workflow_revision>0),
 changed_by_user_id uuid NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(), last_reason_ciphertext text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,user_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,user_id) REFERENCES memberships(org_id,user_id),
 FOREIGN KEY(org_id,changed_by_user_id) REFERENCES memberships(org_id,user_id),
 CHECK(role IN ('owner','contributor','reviewer','observer')),
 CHECK(jsonb_typeof(review_domains)='array' AND review_domains <@ '["commercial","technical"]'::jsonb
   AND jsonb_array_length(review_domains)<=1),
 CHECK(role<>'reviewer' OR jsonb_array_length(review_domains)>0),
 CHECK(role<>'observer' OR review_domains='[]'::jsonb)
);
CREATE UNIQUE INDEX task_member_one_owner ON task_members(org_id,task_id) WHERE role='owner' AND active;
CREATE INDEX task_member_user_tasks ON task_members(org_id,user_id,task_id);
CREATE TABLE task_workflows (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id),
 task_id uuid NOT NULL, owner_user_id uuid NOT NULL, state text NOT NULL DEFAULT 'active', last_reason_ciphertext text,
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0), access_epoch integer NOT NULL DEFAULT 1 CHECK(access_epoch>0),
 co_sign_starred boolean NOT NULL DEFAULT false CHECK(NOT co_sign_starred),
 rule_revision integer NOT NULL DEFAULT 1 CHECK(rule_revision=1),
 archived_at timestamptz, archived_by_user_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 FOREIGN KEY(org_id,owner_user_id) REFERENCES memberships(org_id,user_id),
 FOREIGN KEY(org_id,archived_by_user_id) REFERENCES memberships(org_id,user_id),
 CONSTRAINT task_workflow_owner_member FOREIGN KEY(org_id,task_id,owner_user_id)
   REFERENCES task_members(org_id,task_id,user_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(state IN ('active','archived')),
 CHECK((state='archived')=(archived_at IS NOT NULL) AND (state='archived')=(archived_by_user_id IS NOT NULL))
);
CREATE TABLE task_event_heads (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id), task_id uuid NOT NULL,
 last_seq bigint NOT NULL DEFAULT 0 CHECK(last_seq>=0),
 retained_floor_seq bigint NOT NULL DEFAULT 0 CHECK(retained_floor_seq>=0 AND retained_floor_seq<=last_seq),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(org_id,id), UNIQUE(org_id,task_id),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id)
);
CREATE TABLE task_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES orgs(id), task_id uuid NOT NULL,
 seq bigint NOT NULL CHECK(seq>0), event_kind text NOT NULL,
 payload jsonb NOT NULL, source_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,seq),
 FOREIGN KEY(org_id,task_id) REFERENCES tasks(org_id,id),
 CHECK(event_kind IN ('board_changed','job_progress','access_changed')),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=3072 AND payload->>'type'=event_kind)
);
CREATE INDEX task_jobs_lifecycle ON jobs(org_id,task_id,status,id);
ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_workflow_scopes
 CHECK(NOT(scopes ?| ARRAY['task:members:write','task:archive','card:assign','card:comment','task:review-policy','card:cosign']));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['task_workflows','task_members','task_event_heads','task_events'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (org_id = nullif(current_setting(''app.current_org'',true),'''')::uuid) WITH CHECK (org_id = nullif(current_setting(''app.current_org'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO bid_app',t);
 END LOOP;
END $$;
"""

GUARD_SQL = r"""
CREATE FUNCTION task_workflow_human(p_org uuid,p_task uuid,p_scope text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid; r text;
BEGIN
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
   OR coalesce(current_setting('app.actor_token_id',true),'')<>'' THEN
   RAISE EXCEPTION 'human task management required' USING ERRCODE='42501'; END IF;
 SELECT m.role INTO r FROM public.memberships m JOIN public.users u ON u.id=m.user_id
   JOIN public.orgs o ON o.id=m.org_id
   WHERE m.org_id=p_org AND m.user_id=uid AND m.active AND u.active AND o.active;
 IF r IS NULL OR r NOT IN ('admin','bidder') OR NOT coalesce(current_setting('app.actor_scopes',true),'[]')::jsonb ? p_scope
   OR (r<>'admin' AND NOT EXISTS(SELECT 1 FROM public.task_workflows
      WHERE org_id=p_org AND task_id=p_task AND owner_user_id=uid)) THEN
   RAISE EXCEPTION 'task management denied' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION task_workflow_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE w public.task_workflows; r text; uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'task authorization history is retained' USING ERRCODE='42501'; END IF;
 -- Offline imports use the migration connection, never a forgeable runtime SET flag.
 IF current_user <> 'bid_app' THEN RETURN NEW; END IF;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 SELECT * INTO w FROM public.task_workflows WHERE org_id=NEW.org_id AND task_id=NEW.task_id FOR UPDATE;
 IF TG_OP='UPDATE' AND (NEW.org_id,NEW.task_id,NEW.id,NEW.created_at) IS DISTINCT FROM
   (OLD.org_id,OLD.task_id,OLD.id,OLD.created_at) THEN
   RAISE EXCEPTION 'immutable authorization identity' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='task_members' THEN
   NEW.workflow_revision=coalesce(w.revision+1,1);
   IF TG_OP='INSERT' AND NEW.revision<>1 THEN RAISE EXCEPTION 'initial member revision' USING ERRCODE='23514'; END IF;
   IF TG_OP='UPDATE' AND NEW.user_id<>OLD.user_id THEN RAISE EXCEPTION 'immutable member' USING ERRCODE='23514'; END IF;
   IF w.id IS NULL THEN
     IF NEW.role<>'owner' OR NEW.user_id<>uid OR NOT EXISTS(SELECT 1 FROM public.tasks
       WHERE org_id=NEW.org_id AND id=NEW.task_id AND created_by=uid)
       OR NOT coalesce(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'task:create' THEN
       RAISE EXCEPTION 'only creator may seed owner' USING ERRCODE='42501'; END IF;
   ELSE
     PERFORM public.task_workflow_human(NEW.org_id,NEW.task_id,'task:members:write');
     IF w.state<>'active' THEN RAISE EXCEPTION 'task archived' USING ERRCODE='23514'; END IF;
   END IF;
   SELECT m.role INTO r FROM public.memberships m JOIN public.users u ON u.id=m.user_id
     WHERE m.org_id=NEW.org_id AND m.user_id=NEW.user_id AND m.active AND u.active;
   IF NEW.active AND (r IS NULL OR (NEW.role='owner' AND r NOT IN ('admin','bidder'))
     OR (NEW.review_domains<>'[]'::jsonb AND NEW.review_domains<>(CASE r WHEN 'bidder' THEN '["commercial"]'::jsonb WHEN 'technical' THEN '["technical"]'::jsonb ELSE '[]'::jsonb END))) THEN
     RAISE EXCEPTION 'invalid member authority' USING ERRCODE='23514'; END IF;
   IF NEW.changed_by_user_id<>uid THEN RAISE EXCEPTION 'invalid actor' USING ERRCODE='42501'; END IF;
   IF TG_OP='UPDATE' AND NEW.revision<>OLD.revision+1 THEN RAISE EXCEPTION 'member revision' USING ERRCODE='23514'; END IF;
 ELSE
   IF TG_OP='INSERT' THEN
     IF NEW.state<>'active' OR NEW.revision<>1 OR NEW.access_epoch<>1 THEN
       RAISE EXCEPTION 'initial workflow must be active revision one' USING ERRCODE='23514'; END IF;
     IF NEW.owner_user_id<>uid OR NOT EXISTS(SELECT 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id AND created_by=uid)
       OR NOT coalesce(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'task:create' THEN
       RAISE EXCEPTION 'only creator may seed workflow' USING ERRCODE='42501'; END IF;
   ELSE
     PERFORM public.task_workflow_human(NEW.org_id,NEW.task_id,CASE WHEN NEW.state<>OLD.state THEN 'task:archive' ELSE 'task:members:write' END);
     IF OLD.state='archived' AND NEW.state<>'active' THEN RAISE EXCEPTION 'task archived' USING ERRCODE='23514'; END IF;
     IF NEW.revision<>OLD.revision+1 OR NEW.access_epoch<>OLD.access_epoch+1 THEN
       RAISE EXCEPTION 'workflow revision' USING ERRCODE='23514'; END IF;
     IF NEW.state='archived' AND (NEW.archived_by_user_id<>uid OR EXISTS(SELECT 1 FROM public.jobs WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND status IN ('queued','running'))
       OR EXISTS(SELECT 1 FROM public.vendor_calls c JOIN public.jobs j ON j.org_id=c.org_id AND j.id=c.job_id
          WHERE c.org_id=NEW.org_id AND j.task_id=NEW.task_id AND c.state IN ('pending','unknown'))) THEN
       RAISE EXCEPTION 'task busy or invalid archive actor' USING ERRCODE='23514'; END IF;
   END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task_members_guard BEFORE INSERT OR UPDATE OR DELETE ON task_members FOR EACH ROW EXECUTE FUNCTION task_workflow_guard();
CREATE TRIGGER task_workflows_guard BEFORE INSERT OR UPDATE OR DELETE ON task_workflows FOR EACH ROW EXECUTE FUNCTION task_workflow_guard();
CREATE FUNCTION task_owner_consistent() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE w public.task_workflows; n integer;
BEGIN
 SELECT * INTO w FROM public.task_workflows WHERE org_id=NEW.org_id AND task_id=NEW.task_id;
 SELECT count(*) INTO n FROM public.task_members WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND active AND role='owner';
 IF w.id IS NULL OR n<>1 OR NOT EXISTS(SELECT 1 FROM public.task_members WHERE org_id=w.org_id AND task_id=w.task_id AND user_id=w.owner_user_id AND active AND role='owner') THEN
   RAISE EXCEPTION 'exactly one matching task owner required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='task_members' THEN
   IF w.revision<NEW.workflow_revision THEN
     RAISE EXCEPTION 'member change requires workflow access revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER task_owner_workflow AFTER INSERT OR UPDATE ON task_workflows DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION task_owner_consistent();
CREATE CONSTRAINT TRIGGER task_owner_members AFTER INSERT OR UPDATE ON task_members DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION task_owner_consistent();
CREATE FUNCTION task_events_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'task events are immutable' USING ERRCODE='42501'; END $$;
CREATE TRIGGER task_events_immutable BEFORE UPDATE OR DELETE ON task_events FOR EACH ROW EXECUTE FUNCTION task_events_immutable();
REVOKE ALL ON FUNCTION task_workflow_human(uuid,uuid,text),task_workflow_guard(),task_owner_consistent(),task_events_immutable() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION task_workflow_human(uuid,uuid,text) TO bid_app;
"""


ARCHIVE_SQL = r"""
CREATE FUNCTION task_archived_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE oid uuid; tid uuid; state text;
BEGIN
 IF TG_TABLE_NAME='tasks' THEN
  IF TG_OP='INSERT' THEN RETURN NEW; END IF;
  oid=OLD.org_id; tid=OLD.id;
 ELSIF TG_OP='DELETE' THEN oid=OLD.org_id; tid=OLD.task_id;
 ELSE oid=NEW.org_id; tid=NEW.task_id; END IF;
 IF tid IS NULL THEN IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END IF;
 PERFORM 1 FROM public.tasks WHERE org_id=oid AND id=tid FOR UPDATE;
 SELECT w.state INTO state FROM public.task_workflows w WHERE w.org_id=oid AND w.task_id=tid;
 IF state='archived' THEN RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
REVOKE ALL ON FUNCTION task_archived_guard() FROM PUBLIC;
CREATE FUNCTION task_job_lifecycle_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE state text;
BEGIN
 IF NEW.task_id IS NULL THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='vendor_calls' AND TG_OP<>'INSERT' THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='jobs' THEN
   IF TG_OP='UPDATE' THEN
     IF NEW.status IN ('failed','cancelled') OR NEW.status=OLD.status THEN RETURN NEW; END IF;
   END IF;
 END IF;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 SELECT w.state INTO state FROM public.task_workflows w WHERE w.org_id=NEW.org_id AND w.task_id=NEW.task_id;
 IF state='archived' THEN RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task_job_lifecycle BEFORE INSERT OR UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION task_job_lifecycle_guard();
CREATE TRIGGER task_vendor_lifecycle BEFORE INSERT ON vendor_calls FOR EACH ROW EXECUTE FUNCTION task_job_lifecycle_guard();
REVOKE ALL ON FUNCTION task_job_lifecycle_guard() FROM PUBLIC;

DO $$ DECLARE t text; BEGIN
 FOR t IN SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='task_id'
   AND table_name NOT IN ('task_workflows','task_members','task_event_heads','task_events','jobs','usage_records','vendor_calls','balance_entries','audit_logs') LOOP
   EXECUTE format('CREATE TRIGGER task_archived_write BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION task_archived_guard()',t);
 END LOOP;
 CREATE TRIGGER task_archived_write BEFORE UPDATE OR DELETE ON public.tasks FOR EACH ROW EXECUTE FUNCTION task_archived_guard();
END $$;
"""


def downgrade():
    raise RuntimeError(
        "Retain membership, archive and event history; disable writes and repair forward"
    )


# Preserve each existing content/human gate and add the task membership ceiling
# before it; no trigger replaces evidence, citation, domain or billing validation.
HUMAN_CONTENT_SQL = r"""
CREATE FUNCTION task_write_authority(p_org uuid,p_task uuid,p_scope text,p_domain text,p_decision boolean,p_human boolean)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid;
        kind text:=current_setting('app.actor_kind',true); m public.task_members; org_role text; lifecycle text;
BEGIN
 PERFORM 1 FROM public.tasks WHERE org_id=p_org AND id=p_task FOR UPDATE;
 SELECT state INTO lifecycle FROM public.task_workflows WHERE org_id=p_org AND task_id=p_task FOR UPDATE;
 IF lifecycle IS DISTINCT FROM 'active' THEN RAISE EXCEPTION 'task is not active' USING ERRCODE='42501'; END IF;
 SELECT tm.* INTO m FROM public.task_members tm JOIN public.memberships om ON om.org_id=tm.org_id AND om.user_id=tm.user_id
   JOIN public.users u ON u.id=tm.user_id JOIN public.orgs o ON o.id=tm.org_id
   WHERE tm.org_id=p_org AND tm.task_id=p_task AND tm.user_id=uid AND tm.active AND om.active AND u.active AND o.active;
 SELECT role INTO org_role FROM public.memberships WHERE org_id=p_org AND user_id=uid AND active;
 IF m.id IS NULL OR m.role='observer' OR (m.role='reviewer' AND NOT p_decision)
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? p_scope THEN
   RAISE EXCEPTION 'task mutation authority required' USING ERRCODE='42501'; END IF;
 IF p_human AND (kind IS DISTINCT FROM 'session' OR coalesce(current_setting('app.actor_token_id',true),'')<>'') THEN
   RAISE EXCEPTION 'human task decision required' USING ERRCODE='42501'; END IF;
 IF p_domain IS NOT NULL AND (NOT m.review_domains ? p_domain
   OR org_role IS DISTINCT FROM (CASE p_domain WHEN 'commercial' THEN 'bidder' WHEN 'technical' THEN 'technical' END)) THEN
   RAISE EXCEPTION 'task review domain not granted' USING ERRCODE='42501'; END IF;
END $$;
REVOKE ALL ON FUNCTION task_write_authority(uuid,uuid,text,text,boolean,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION task_write_authority(uuid,uuid,text,text,boolean,boolean) TO bid_app;

CREATE FUNCTION task_human_content_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE tid uuid; domain_value text; scope_value text; decision boolean:=true; human_only boolean:=true;
        previous public.response_card_revisions; actor_role text;
BEGIN
 SELECT role INTO actor_role FROM public.memberships WHERE org_id=NEW.org_id
   AND user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid AND active;
 IF TG_TABLE_NAME='response_card_revisions' THEN
   SELECT task_id INTO tid FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
   SELECT * INTO previous FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
   decision:=NEW.state IN ('confirmed','rejected','needs_material') OR (previous.state='confirmed' AND NEW.state='draft')
     OR (NEW.disposition_by IS NOT NULL AND (NEW.disposition,NEW.disposition_by) IS DISTINCT FROM (previous.disposition,previous.disposition_by));
   decision:=coalesce(decision,false);
   domain_value:=CASE WHEN decision THEN NEW.review_domain ELSE NULL END;
   scope_value:=CASE WHEN decision THEN 'evidence:confirm' WHEN NEW.actor_kind='worker' THEN 'card:generate' ELSE 'card:write' END;
   human_only:=decision;
 ELSIF TG_TABLE_NAME='evidence' THEN
   IF NEW.confirmed_by IS NULL OR NEW.confirmed_by IS NOT DISTINCT FROM OLD.confirmed_by THEN RETURN NEW; END IF;
   tid:=NEW.task_id;
   SELECT v.review_domain INTO domain_value FROM public.response_cards c JOIN public.response_card_revisions v
     ON v.org_id=c.org_id AND v.id=c.current_revision_id WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
   scope_value:='evidence:confirm';
 ELSIF TG_TABLE_NAME='check_decisions' THEN
   tid:=NEW.task_id; scope_value:='check:decide';
   SELECT review_domain INTO domain_value FROM public.check_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id;
 ELSIF TG_TABLE_NAME='prototype_evidence_decisions' THEN
   tid:=NEW.task_id; scope_value:='evidence:confirm';
   SELECT review_domain INTO domain_value FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id;
 ELSIF TG_TABLE_NAME='prototype_decision_batches' THEN
   tid:=NEW.task_id; scope_value:='evidence:confirm';
   domain_value:=CASE actor_role WHEN 'bidder' THEN 'commercial' WHEN 'technical' THEN 'technical' END;
 ELSE
   tid:=NEW.task_id; scope_value:='score:rubric:review';
   IF TG_TABLE_NAME<>'score_rubric_classifications' THEN
     domain_value:=CASE actor_role WHEN 'bidder' THEN 'commercial' WHEN 'technical' THEN 'technical' END;
   END IF;
 END IF;
 PERFORM public.task_write_authority(NEW.org_id,tid,scope_value,domain_value,decision,human_only);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION task_human_content_guard() FROM PUBLIC;
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON response_card_revisions FOR EACH ROW EXECUTE FUNCTION task_human_content_guard();
CREATE TRIGGER aaa_task_human_authority BEFORE UPDATE ON evidence FOR EACH ROW EXECUTE FUNCTION task_human_content_guard();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['check_decisions','prototype_decision_batches','prototype_evidence_decisions',
   'score_rubric_decisions','score_rubric_classifications','score_rubric_coverage_decisions','score_rubric_revision_events'] LOOP
  EXECUTE format('CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION task_human_content_guard()',t);
 END LOOP;
END $$;
"""
