"""Require independent human review of tender interpretations before acceptance."""

import json
import os
from uuid import uuid4

from alembic import op
from sqlalchemy import text

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(HASH_SQL)
    backfill()
    op.execute(GUARD_SQL)
    op.execute(INVALIDATION_SQL)
    op.execute(CONSUMER_SQL)
    op.execute(AUDIT_SQL)
    op.execute(GRANTS_SQL)


def downgrade():
    raise RuntimeError("Preserve review history and acceptance gates; restore a gate-aware release")


SCHEMA_SQL = r"""
CREATE TABLE public.requirement_review_sets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, extraction_job_id uuid NOT NULL, document_id uuid NOT NULL,
 origin varchar(20) NOT NULL CHECK(origin IN ('model','manual')),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>=1),
 membership_sha256 varchar(64) NOT NULL, confirmation_sha256 varchar(64) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(org_id,id),
 UNIQUE(org_id,extraction_job_id), UNIQUE(org_id,task_id,extraction_job_id),
 UNIQUE(org_id,id,task_id,extraction_job_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,task_id,document_id,extraction_job_id) REFERENCES public.jobs(org_id,task_id,document_id,id)
);
CREATE TABLE public.requirement_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, extraction_job_id uuid NOT NULL, requirement_id uuid NOT NULL,
 origin varchar(30) NOT NULL CHECK(origin IN ('extracted','legacy','manual_missing','manual_rejected')),
 revision integer NOT NULL CHECK(revision>=1), current_event_id uuid NOT NULL,
 state varchar(30) NOT NULL CHECK(state IN ('unconfirmed','legacy_unconfirmed','confirmed','invalidated')),
 review_hash varchar(64) NOT NULL, source_pin jsonb, snapshot_ciphertext text NOT NULL CHECK(length(snapshot_ciphertext)>=80 AND snapshot_ciphertext~'^gAAAA'),
 confirmed_by_user_id uuid, confirmed_at timestamptz,
 rejected_job_id uuid, rejected_index integer CHECK(rejected_index>=0), rejected_summary_sha256 varchar(64),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(org_id,id), UNIQUE(org_id,requirement_id),
 UNIQUE(org_id,id,task_id,extraction_job_id,requirement_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,requirement_id,task_id,extraction_job_id) REFERENCES public.requirements(org_id,id,task_id,job_id),
 FOREIGN KEY(org_id,task_id,extraction_job_id) REFERENCES public.requirement_review_sets(org_id,task_id,extraction_job_id),
 FOREIGN KEY(org_id,confirmed_by_user_id) REFERENCES public.memberships(org_id,user_id),
 FOREIGN KEY(org_id,rejected_job_id) REFERENCES public.jobs(org_id,id),
 CHECK((state='confirmed' AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL AND source_pin IS NOT NULL)
   OR (state<>'confirmed' AND confirmed_by_user_id IS NULL AND confirmed_at IS NULL)),
 CHECK((rejected_job_id IS NULL AND rejected_index IS NULL AND rejected_summary_sha256 IS NULL)
   OR (rejected_job_id IS NOT NULL AND rejected_index IS NOT NULL AND rejected_summary_sha256 IS NOT NULL))
);
CREATE TABLE public.requirement_review_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, extraction_job_id uuid NOT NULL, requirement_id uuid NOT NULL, review_id uuid NOT NULL,
 revision integer NOT NULL CHECK(revision>=1), action varchar(30) NOT NULL
 CHECK(action IN ('seed','manual_add','confirm','reopen','invalidate','source_repair')),
 state_after varchar(30) NOT NULL CHECK(state_after IN ('unconfirmed','legacy_unconfirmed','confirmed','invalidated')),
 review_hash varchar(64) NOT NULL, snapshot_sha256 varchar(64) NOT NULL, snapshot_ciphertext text NOT NULL CHECK(length(snapshot_ciphertext)>=80 AND snapshot_ciphertext~'^gAAAA'),
 reason_ciphertext text, reason_sha256 varchar(64), source_pin jsonb,
 actor_kind varchar(20) NOT NULL CHECK(actor_kind IN ('session','worker','migration')),
 actor_user_id uuid, request_id uuid, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(org_id,id),
 UNIQUE(org_id,review_id,revision), UNIQUE(org_id,review_id,id,revision),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,actor_user_id) REFERENCES public.memberships(org_id,user_id),
 FOREIGN KEY(org_id,review_id,task_id,extraction_job_id,requirement_id)
 REFERENCES public.requirement_reviews(org_id,id,task_id,extraction_job_id,requirement_id) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE public.requirement_reviews ADD CONSTRAINT requirement_current_event
 FOREIGN KEY(org_id,id,current_event_id,revision) REFERENCES public.requirement_review_events(org_id,review_id,id,revision)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.requirement_review_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id), task_id uuid NOT NULL,
 actor_user_id uuid NOT NULL, request_id uuid NOT NULL, action varchar(30) NOT NULL,
 request_sha256 varchar(64) NOT NULL, receipt_ciphertext text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(org_id,id), UNIQUE(org_id,actor_user_id,request_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,actor_user_id) REFERENCES public.memberships(org_id,user_id)
);
CREATE INDEX requirement_reviews_scope ON public.requirement_reviews(org_id,task_id,extraction_job_id,state);
CREATE INDEX requirement_reviews_rejected ON public.requirement_reviews(org_id,rejected_job_id,rejected_index);
CREATE INDEX requirement_review_events_scope ON public.requirement_review_events(org_id,requirement_id,revision);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['requirement_review_sets','requirement_reviews','requirement_review_events','requirement_review_requests'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY tenant_isolation ON public.%I USING(org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid) WITH CHECK(org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid)',tab);
 END LOOP;
END $$;
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_requirement_scopes
 CHECK(NOT (scopes ?| ARRAY['req:confirm','req:manual']));
"""

HASH_SQL = r"""
CREATE FUNCTION public.requirement_canonical(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text;
BEGIN
 CASE jsonb_typeof(value)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||public.requirement_canonical(val),',' ORDER BY key COLLATE "C"),'')||'}'
   INTO result FROM jsonb_each(value) AS x(key,val);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(public.requirement_canonical(val),',' ORDER BY ord),'')||']'
   INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS x(val,ord);
 WHEN 'number' THEN result:=regexp_replace(regexp_replace(value::text,'(\.[0-9]*?)0+$','\1'),'\.$','');
 ELSE result:=value::text;
 END CASE;
 RETURN result;
END $$;
CREATE FUNCTION public.requirement_digest(value jsonb) RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(public.requirement_canonical(value),'UTF8')),'hex')
$$;
CREATE FUNCTION public.requirement_content(p_org uuid,p_req uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('category',r.category,'starred',r.starred,'text',r.text,'condition',r.condition,
 'source',jsonb_build_object('document_id',r.document_id,'chunk_id',r.chunk_id,'page',r.page,'location',r.location,'quote',r.quote))
 FROM public.requirements r WHERE r.org_id=p_org AND r.id=p_req
$$;
CREATE FUNCTION public.requirement_hash(p_org uuid,p_req uuid,p_pin jsonb) RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT public.requirement_digest(jsonb_build_object('version','requirement-review-v1','org_id',r.org_id,
 'task_id',r.task_id,'extraction_job_id',r.job_id,'requirement_id',r.id,'content',public.requirement_content(p_org,p_req),
 'source_binding_sha256',p_pin->>'binding_sha256')) FROM public.requirements r WHERE r.org_id=p_org AND r.id=p_req
$$;
CREATE FUNCTION public.requirement_pin_valid(p_org uuid,p_req uuid,p_pin jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE r public.requirements; c public.chunks; d public.documents; source_text text; block jsonb;
BEGIN
 SELECT * INTO r FROM public.requirements WHERE org_id=p_org AND id=p_req;
 SELECT * INTO c FROM public.chunks WHERE org_id=p_org AND id=r.chunk_id AND task_id=r.task_id AND document_id=r.document_id;
 SELECT * INTO d FROM public.documents WHERE org_id=p_org AND id=r.document_id AND task_id=r.task_id;
 IF r.id IS NULL OR c.id IS NULL OR d.id IS NULL OR NOT c.citation_verified OR p_pin IS NULL
   OR p_pin->>'verifier_version' IS DISTINCT FROM 'requirement-source-v1'
   OR p_pin->'source' IS DISTINCT FROM public.requirement_content(p_org,p_req)->'source'
   OR p_pin->>'document_sha256' IS DISTINCT FROM d.sha256
   OR p_pin->>'chunk_sha256' IS DISTINCT FROM public.requirement_digest(jsonb_build_object('document_id',c.document_id,
      'text',c.text,'blocks',c.blocks,'seq',c.seq,'page',c.page,'citation_verified',c.citation_verified)) THEN RETURN false; END IF;
 IF r.location IS NOT NULL THEN
  SELECT b INTO block FROM jsonb_array_elements(c.blocks) b WHERE b->>'block_id'=r.location->>'block_id';
  IF block IS NULL OR (block-'text') IS DISTINCT FROM r.location THEN RETURN false; END IF;
  source_text:=block->>'text';
 ELSE
  IF c.blocks IS NOT NULL OR r.page IS DISTINCT FROM c.page THEN RETURN false; END IF;
  source_text:=c.text;
 END IF;
 RETURN coalesce(p_pin->>'binding_sha256'=public.requirement_digest(p_pin-'binding_sha256')
 AND p_pin->>'location_sha256'=encode(sha256(convert_to(source_text,'UTF8')),'hex')
 AND p_pin->>'quote_sha256'=encode(sha256(convert_to(r.quote,'UTF8')),'hex')
 AND (p_pin->>'start')::int>=0 AND (p_pin->>'end')::int-(p_pin->>'start')::int=length(r.quote)
 AND substring(source_text FROM (p_pin->>'start')::int+1 FOR length(r.quote))=r.quote,false);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;
CREATE FUNCTION public.requirement_review_current(p_org uuid,p_req uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.requirement_reviews v WHERE v.org_id=p_org AND v.requirement_id=p_req
 AND v.state='confirmed' AND v.confirmed_by_user_id IS NOT NULL AND v.confirmed_at IS NOT NULL
 AND public.requirement_pin_valid(p_org,p_req,v.source_pin)
 AND v.review_hash=public.requirement_hash(p_org,p_req,v.source_pin))
$$;
CREATE FUNCTION public.requirement_review_state(p_org uuid,p_req uuid) RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT CASE WHEN state='confirmed' AND NOT public.requirement_review_current(p_org,p_req)
 THEN 'invalidated' ELSE state END FROM public.requirement_reviews WHERE org_id=p_org AND requirement_id=p_req),'legacy_unconfirmed')
$$;
"""


def backfill():
    """An empty installation needs no data key; legacy content never enters plaintext history."""
    connection = op.get_bind()
    from app.core.security import Secrets

    crypto = None
    org_ids = (
        connection.execute(text("SELECT id FROM public.platform_org_summaries() ORDER BY id"))
        .scalars()
        .all()
    )
    for org_id in org_ids:
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_id)}
        )
        rows = (
            connection.execute(
                text(
                    "SELECT r.*,public.requirement_content(r.org_id,r.id) AS content FROM public.requirements r "
                    "JOIN public.jobs j ON j.org_id=r.org_id AND j.id=r.job_id "
                    "WHERE r.org_id=:org AND j.kind='extract' AND j.status='succeeded' ORDER BY r.job_id,r.id"
                ),
                {"org": org_id},
            )
            .mappings()
            .all()
        )
        if not rows:
            continue
        if crypto is None:
            key = os.environ.get("BID_ENCRYPTION_KEY")
            if not key:
                raise RuntimeError("0046 legacy review backfill requires BID_ENCRYPTION_KEY")
            crypto = Secrets(key)
        for row in rows:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"), {"org": str(row["org_id"])}
            )
            connection.execute(
                text(
                    "INSERT INTO public.requirement_review_sets(org_id,task_id,extraction_job_id,document_id,origin,membership_sha256,confirmation_sha256) VALUES(:org,:task,:job,:doc,'model',public.requirement_digest('[]'),public.requirement_digest('[]')) ON CONFLICT(org_id,extraction_job_id) DO NOTHING"
                ),
                {
                    "org": row["org_id"],
                    "task": row["task_id"],
                    "job": row["job_id"],
                    "doc": row["document_id"],
                },
            )
            rid, eid = uuid4(), uuid4()
            encrypted = crypto.encrypt(
                json.dumps(
                    {
                        "org_id": str(row["org_id"]),
                        "record_id": str(rid),
                        "payload": row["content"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            values = {
                "id": rid,
                "event": eid,
                "org": row["org_id"],
                "task": row["task_id"],
                "job": row["job_id"],
                "req": row["id"],
                "cipher": encrypted,
            }
            connection.execute(
                text(
                    "INSERT INTO public.requirement_reviews(id,org_id,task_id,extraction_job_id,requirement_id,origin,revision,current_event_id,state,review_hash,snapshot_ciphertext) VALUES(:id,:org,:task,:job,:req,'legacy',1,:event,'legacy_unconfirmed',public.requirement_hash(:org,:req,NULL),:cipher)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO public.requirement_review_events(id,org_id,task_id,extraction_job_id,requirement_id,review_id,revision,action,state_after,review_hash,snapshot_sha256,snapshot_ciphertext,actor_kind) VALUES(:event,:org,:task,:job,:req,:id,1,'seed','legacy_unconfirmed',public.requirement_hash(:org,:req,NULL),public.requirement_digest(public.requirement_content(:org,:req)),:cipher,'migration')"
                ),
                values,
            )
        connection.execute(
            text(
                "UPDATE public.requirement_review_sets s SET "
                "membership_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(r.requirement_id ORDER BY r.requirement_id),'[]')) "
                "FROM public.requirement_reviews r WHERE r.org_id=s.org_id AND r.extraction_job_id=s.extraction_job_id), "
                "confirmation_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(jsonb_build_array(r.requirement_id,r.revision,r.review_hash,r.state) ORDER BY r.requirement_id),'[]')) "
                "FROM public.requirement_reviews r WHERE r.org_id=s.org_id AND r.extraction_job_id=s.extraction_job_id) WHERE s.org_id=:org"
            ),
            {"org": org_id},
        )
    connection.execute(text("SELECT set_config('app.current_org','',true)"))


GUARD_SQL = r"""
CREATE FUNCTION public.requirement_human_authority(p_org uuid,p_task uuid,p_scope text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF p_scope IS NULL OR p_scope NOT IN ('req:confirm','req:manual') OR NOT EXISTS(SELECT 1 FROM public.memberships m
  WHERE m.org_id=p_org AND m.user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid
   AND m.active AND m.role IN ('admin','bidder','technical')) THEN
  RAISE EXCEPTION 'requirement human role required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_org,p_task,p_scope,NULL,false,true);
END $$;
CREATE FUNCTION public.requirement_seed_authority(p_org uuid,p_task uuid,p_job uuid,p_manual boolean) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j public.jobs; uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF p_manual THEN PERFORM public.requirement_human_authority(p_org,p_task,'req:manual'); RETURN; END IF;
 IF current_setting('app.actor_kind',true)='session' AND coalesce(current_setting('app.actor_token_id',true),'')='' THEN
 PERFORM public.requirement_human_authority(p_org,p_task,'req:confirm'); RETURN; END IF;
 SELECT * INTO j FROM public.jobs WHERE org_id=p_org AND id=p_job AND task_id=p_task AND kind='extract';
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM nullif(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM nullif(current_setting('app.execution_run_id',true),'')::uuid
 OR j.run_id IS NULL OR j.status NOT IN ('running','succeeded')
 OR j.actor_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'extract publication fence required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_org,p_task,'req:extract',NULL,false,false);
END $$;
CREATE FUNCTION public.requirement_review_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid; r public.requirements; j public.jobs;
 e public.requirement_review_events; previous public.requirement_review_events;
BEGIN
 IF TG_OP='UPDATE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
 AND (to_jsonb(NEW)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext'])=(to_jsonb(OLD)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext']) THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'requirement review history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='requirement_review_sets' THEN
  IF TG_OP='INSERT' THEN
   SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id;
   IF j.kind IS DISTINCT FROM 'extract' OR j.status IS DISTINCT FROM 'succeeded' OR j.task_id IS DISTINCT FROM NEW.task_id OR j.document_id IS DISTINCT FROM NEW.document_id
   OR (NEW.origin='manual') IS DISTINCT FROM (coalesce(j.result->>'origin','model')='manual') THEN RAISE EXCEPTION 'invalid review extraction scope' USING ERRCODE='23514'; END IF;
   PERFORM public.requirement_seed_authority(NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.origin='manual');
  ELSIF pg_trigger_depth()<2 OR ROW(NEW.id,NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.document_id,NEW.origin)
   IS DISTINCT FROM ROW(OLD.id,OLD.org_id,OLD.task_id,OLD.extraction_job_id,OLD.document_id,OLD.origin) THEN
   RAISE EXCEPTION 'review set updates are producer-owned' USING ERRCODE='42501';
  END IF;
 ELSIF TG_TABLE_NAME='requirement_review_requests' THEN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable review receipt' USING ERRCODE='42501'; END IF;
  PERFORM public.requirement_human_authority(NEW.org_id,NEW.task_id,CASE WHEN NEW.action='manual_add' THEN 'req:manual' ELSE 'req:confirm' END);
  IF NEW.actor_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'receipt actor mismatch' USING ERRCODE='42501'; END IF;
 ELSIF TG_TABLE_NAME='requirement_review_events' THEN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable review event' USING ERRCODE='42501'; END IF;
  IF NEW.action IN ('confirm','reopen','manual_add') THEN
   PERFORM public.requirement_human_authority(NEW.org_id,NEW.task_id,CASE WHEN NEW.action='manual_add' THEN 'req:manual' ELSE 'req:confirm' END);
   IF NEW.actor_kind<>'session' OR NEW.actor_user_id IS DISTINCT FROM uid OR NEW.request_id IS NULL
    OR NEW.reason_sha256 IS NULL OR NEW.reason_ciphertext IS NULL THEN RAISE EXCEPTION 'human event binding required' USING ERRCODE='42501'; END IF;
  ELSIF NEW.action='seed' THEN
   PERFORM public.requirement_seed_authority(NEW.org_id,NEW.task_id,NEW.extraction_job_id,false);
   IF NOT ((NEW.actor_kind='worker' AND NEW.actor_user_id IS NULL) OR
 (NEW.actor_kind='session' AND NEW.actor_user_id=uid AND NEW.state_after='legacy_unconfirmed' AND EXISTS(
 SELECT 1 FROM public.requirement_reviews v WHERE v.org_id=NEW.org_id AND v.id=NEW.review_id AND v.origin='legacy')))
 THEN RAISE EXCEPTION 'invalid seed actor' USING ERRCODE='42501'; END IF;
  ELSIF pg_trigger_depth()<2 THEN RAISE EXCEPTION 'invalidation requires source producer' USING ERRCODE='42501'; END IF;
  IF NEW.action NOT IN ('invalidate','source_repair') AND NEW.snapshot_sha256 IS DISTINCT FROM public.requirement_digest(public.requirement_content(NEW.org_id,NEW.requirement_id)) THEN
   RAISE EXCEPTION 'review snapshot content differs' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='requirement_reviews' THEN
  SELECT * INTO r FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id;
  IF r.document_id IS DISTINCT FROM j.document_id OR r.task_id IS DISTINCT FROM j.task_id OR r.job_id IS DISTINCT FROM j.id THEN
   RAISE EXCEPTION 'requirement extraction document binding differs' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' THEN
   PERFORM public.requirement_seed_authority(NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.origin LIKE 'manual_%');
   IF (current_setting('app.actor_kind',true)='session' AND NEW.origin='extracted') OR
 (NEW.origin='legacy' AND NEW.state<>'legacy_unconfirmed') OR NEW.state NOT IN ('unconfirmed','legacy_unconfirmed') OR NEW.revision<>1 THEN RAISE EXCEPTION 'seed cannot approve' USING ERRCODE='42501'; END IF;
  ELSE
   IF ROW(NEW.id,NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id,NEW.origin)
    IS DISTINCT FROM ROW(OLD.id,OLD.org_id,OLD.task_id,OLD.extraction_job_id,OLD.requirement_id,OLD.origin) THEN
    RAISE EXCEPTION 'review identity is immutable' USING ERRCODE='42501'; END IF;
   IF pg_trigger_depth()<2 THEN
    PERFORM public.requirement_human_authority(NEW.org_id,NEW.task_id,'req:confirm');
    IF NEW.revision<>OLD.revision+1 OR (NEW.state='confirmed' AND OLD.state='confirmed') OR
      (NEW.state='unconfirmed' AND OLD.state<>'confirmed') OR NEW.state NOT IN ('confirmed','unconfirmed') THEN
      RAISE EXCEPTION 'invalid review transition' USING ERRCODE='23514'; END IF;
    IF NEW.review_hash IS DISTINCT FROM public.requirement_hash(NEW.org_id,NEW.requirement_id,NEW.source_pin) THEN
      RAISE EXCEPTION 'review hash mismatch' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
  IF NEW.source_pin IS NOT NULL AND (TG_OP='INSERT' OR pg_trigger_depth()<2) AND (NOT public.requirement_pin_valid(NEW.org_id,NEW.requirement_id,NEW.source_pin)
 OR NEW.source_pin IS DISTINCT FROM public.requirement_current_pin(NEW.org_id,NEW.requirement_id)) THEN
   RAISE EXCEPTION 'unverified review source pin' USING ERRCODE='23514'; END IF;
  IF NEW.state='confirmed' AND NEW.confirmed_by_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'confirmer mismatch' USING ERRCODE='42501'; END IF;
  IF NEW.rejected_job_id IS NOT NULL THEN
   SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.rejected_job_id;
   IF j.task_id IS DISTINCT FROM r.task_id OR j.document_id IS DISTINCT FROM r.document_id OR j.kind<>'extract'
    OR public.requirement_digest(j.result->'rejected'->NEW.rejected_index) IS DISTINCT FROM NEW.rejected_summary_sha256 THEN
    RAISE EXCEPTION 'rejected reference changed' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.requirement_archived_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
 AND (to_jsonb(NEW)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext'])=(to_jsonb(OLD)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext']) THEN RETURN NEW; END IF;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM public.task_workflows WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND state='archived') THEN
 RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.requirement_event_pointer() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v public.requirement_reviews; e public.requirement_review_events;
BEGIN
 SELECT * INTO v FROM public.requirement_reviews WHERE org_id=NEW.org_id AND id=NEW.id;
 SELECT * INTO e FROM public.requirement_review_events WHERE org_id=v.org_id AND id=v.current_event_id;
 IF e.review_id IS DISTINCT FROM v.id OR e.revision IS DISTINCT FROM v.revision OR e.state_after IS DISTINCT FROM v.state
 OR (e.action IN ('confirm','reopen','manual_add','seed') AND e.review_hash IS DISTINCT FROM v.review_hash)
 OR (v.state='confirmed' AND (e.action<>'confirm' OR e.actor_user_id IS DISTINCT FROM v.confirmed_by_user_id)) THEN
 RAISE EXCEPTION 'review event pointer mismatch' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER requirement_event_pointer AFTER INSERT OR UPDATE ON public.requirement_reviews
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.requirement_event_pointer();
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['requirement_review_sets','requirement_reviews','requirement_review_events','requirement_review_requests'] LOOP
  EXECUTE format('CREATE TRIGGER requirement_review_guard BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.requirement_review_guard()',tab);
  EXECUTE format('CREATE TRIGGER task_archived_write BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.requirement_archived_guard()',tab);
 END LOOP;
END $$;
"""

INVALIDATION_SQL = r"""
CREATE FUNCTION public.requirement_review_changed() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE previous_org text:=current_setting('app.current_org',true); derive_org boolean;
BEGIN
 IF TG_OP='UPDATE' AND (to_jsonb(NEW)-'snapshot_ciphertext')=(to_jsonb(OLD)-'snapshot_ciphertext') THEN RETURN NULL; END IF;
 -- Match the existing tenant statement producers. append_task_event runs as its
 -- restricted function owner, so derive before entering it, never inside it.
 SELECT nullif(previous_org,'') IS NULL AND (r.rolsuper OR r.rolbypassrls)
  INTO derive_org FROM pg_catalog.pg_roles r WHERE r.rolname=current_user;
 IF derive_org THEN PERFORM set_config('app.current_org',NEW.org_id::text,true); END IF;
 UPDATE public.requirement_review_sets s SET revision=revision+CASE WHEN TG_OP='INSERT' AND NEW.origin='legacy' THEN 0 ELSE 1 END,
 membership_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(r.id ORDER BY r.id),'[]')) FROM public.requirements r WHERE r.org_id=s.org_id AND r.job_id=s.extraction_job_id),
 confirmation_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(jsonb_build_array(v.requirement_id,v.revision,v.review_hash,v.state) ORDER BY v.requirement_id),'[]')) FROM public.requirement_reviews v WHERE v.org_id=s.org_id AND v.extraction_job_id=s.extraction_job_id)
 WHERE s.org_id=NEW.org_id AND s.extraction_job_id=NEW.extraction_job_id;
 PERFORM public.append_task_event(NEW.org_id,NEW.task_id,'board_changed',jsonb_build_object('type','board_changed',
 'extraction_job_id',NEW.extraction_job_id,'requirement_ids',jsonb_build_array(NEW.requirement_id),'card_ids','[]'::jsonb,'invalidate_all',false),NULL);
 IF derive_org THEN PERFORM set_config('app.current_org',coalesce(previous_org,''),true); END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER zz_requirement_review_changed AFTER INSERT OR UPDATE ON public.requirement_reviews
 FOR EACH ROW EXECUTE FUNCTION public.requirement_review_changed();
CREATE FUNCTION public.requirement_source_invalidate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v public.requirement_reviews; prior public.requirement_review_events; eid uuid; next_state text; next_pin jsonb;
 previous_org text:=current_setting('app.current_org',true); derive_org boolean;
BEGIN
 IF TG_TABLE_NAME='requirements' THEN
 IF ROW(NEW.text,NEW.category,NEW.starred,NEW.condition,NEW.document_id,NEW.chunk_id,NEW.page,NEW.location,NEW.quote)
 IS NOT DISTINCT FROM ROW(OLD.text,OLD.category,OLD.starred,OLD.condition,OLD.document_id,OLD.chunk_id,OLD.page,OLD.location,OLD.quote) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='chunks' THEN
 IF ROW(NEW.text,NEW.blocks,NEW.seq,NEW.page,NEW.citation_verified,NEW.document_id)
 IS NOT DISTINCT FROM ROW(OLD.text,OLD.blocks,OLD.seq,OLD.page,OLD.citation_verified,OLD.document_id) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='documents' THEN
 IF NEW.sha256 IS NOT DISTINCT FROM OLD.sha256 THEN RETURN NEW; END IF;
 END IF;
 -- Maintenance connections may omit tenant context, but runtime roles cannot.
 -- Keep it installed through review/event/audit producers, then restore it so
 -- the source table's own statement producer can derive the same org normally.
 SELECT nullif(previous_org,'') IS NULL AND (r.rolsuper OR r.rolbypassrls)
  INTO derive_org FROM pg_catalog.pg_roles r WHERE r.rolname=current_user;
 IF derive_org THEN PERFORM set_config('app.current_org',NEW.org_id::text,true); END IF;
 FOR v IN SELECT rv.* FROM public.requirement_reviews rv JOIN public.requirements r ON r.org_id=rv.org_id AND r.id=rv.requirement_id
 WHERE r.org_id=NEW.org_id AND (CASE TG_TABLE_NAME WHEN 'requirements' THEN r.id=NEW.id WHEN 'chunks' THEN r.chunk_id=NEW.id ELSE r.document_id=NEW.id END)
 ORDER BY rv.task_id,rv.extraction_job_id,rv.requirement_id FOR UPDATE OF rv LOOP
  PERFORM 1 FROM public.tasks WHERE org_id=v.org_id AND id=v.task_id FOR UPDATE;
  PERFORM 1 FROM public.requirement_review_sets WHERE org_id=v.org_id AND extraction_job_id=v.extraction_job_id FOR UPDATE;
  SELECT * INTO prior FROM public.requirement_review_events WHERE org_id=v.org_id AND id=v.current_event_id;
  eid:=gen_random_uuid(); next_state:=CASE WHEN v.state IN ('confirmed','invalidated') THEN 'invalidated' ELSE v.state END;
  next_pin:=public.requirement_current_pin(v.org_id,v.requirement_id);
  UPDATE public.requirement_reviews SET revision=v.revision+1,current_event_id=eid,state=next_state,
   review_hash=public.requirement_hash(v.org_id,v.requirement_id,next_pin),source_pin=next_pin,
   confirmed_by_user_id=NULL,confirmed_at=NULL WHERE org_id=v.org_id AND id=v.id;
  INSERT INTO public.requirement_review_events(id,org_id,task_id,extraction_job_id,requirement_id,review_id,revision,
   action,state_after,review_hash,snapshot_sha256,snapshot_ciphertext,source_pin,actor_kind)
  VALUES(eid,v.org_id,v.task_id,v.extraction_job_id,v.requirement_id,v.id,v.revision+1,
   CASE WHEN TG_TABLE_NAME='requirements' AND to_jsonb(NEW)->>'quote' IS DISTINCT FROM to_jsonb(OLD)->>'quote' THEN 'source_repair' ELSE 'invalidate' END,
   next_state,prior.review_hash,prior.snapshot_sha256,prior.snapshot_ciphertext,prior.source_pin,'worker');
 END LOOP;
 IF derive_org THEN PERFORM set_config('app.current_org',coalesce(previous_org,''),true); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_source_invalidate AFTER UPDATE ON public.requirements FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();
CREATE TRIGGER requirement_chunk_invalidate AFTER UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();
CREATE TRIGGER requirement_document_invalidate AFTER UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();
CREATE FUNCTION public.requirement_source_lock() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 RETURN NEW;
END $$;
CREATE TRIGGER aa_requirement_source_lock BEFORE UPDATE ON public.requirements FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();
CREATE TRIGGER aa_requirement_chunk_lock BEFORE UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();
CREATE TRIGGER aa_requirement_document_lock BEFORE UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();
CREATE FUNCTION public.requirement_manual_job_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.result->>'origin'='manual' AND NEW IS DISTINCT FROM OLD THEN
 RAISE EXCEPTION 'manual extraction is an immutable terminal receipt' USING ERRCODE='42501'; END IF;
 IF NEW.kind='extract' AND NEW.result->>'origin'='manual' THEN
 PERFORM public.requirement_human_authority(NEW.org_id,NEW.task_id,'req:manual');
 IF NEW.status<>'succeeded' OR NEW.attempts<>0 OR NEW.provider_config_id IS NOT NULL OR NEW.provider_identity IS NOT NULL
 OR NEW.reasoning IS NOT NULL OR NEW.run_id IS NOT NULL OR NEW.lease_until IS NOT NULL OR NEW.queue_id IS NOT NULL
 OR NEW.finished_at IS NULL THEN RAISE EXCEPTION 'invalid manual extraction receipt' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_manual_job_guard BEFORE INSERT OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.requirement_manual_job_guard();
"""

CONSUMER_SQL = r"""
CREATE FUNCTION public.requirement_response_gate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rid uuid; previous public.response_card_revisions;
BEGIN
 SELECT * INTO previous FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id AND revision=NEW.revision-1;
 SELECT requirement_id INTO rid FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
 IF ((NEW.state='confirmed' AND previous.state IS DISTINCT FROM 'confirmed') OR (NEW.disposition='comply_only' AND previous.disposition IS DISTINCT FROM 'comply_only')) AND NOT public.requirement_review_current(NEW.org_id,rid) THEN
 RAISE EXCEPTION 'requirement_unconfirmed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_response_gate BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.requirement_response_gate();
CREATE FUNCTION public.requirement_rubric_confirmed(p_org uuid,p_rubric uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.score_rubric_sets r WHERE r.org_id=p_org AND r.id=p_rubric
 AND jsonb_typeof(r.input_manifest->'requirements')='array' AND NOT EXISTS(
 SELECT 1 FROM jsonb_array_elements(r.input_manifest->'requirements') e
 WHERE NOT public.requirement_review_current(p_org,(e->>'requirement_id')::uuid)))
$$;
DO $$ DECLARE definition text; patched text; BEGIN
 SELECT pg_get_functiondef('public.response_item_gate()'::regprocedure) INTO definition;
 patched:=replace(definition,'citation_ok boolean;', 'requirement_ok boolean; requirement_state text; citation_ok boolean;');
 patched:=replace(patched,'citation_ok := public.response_citation_valid',E'requirement_ok := public.requirement_review_current(NEW.org_id,NEW.requirement_id);\n requirement_state := public.requirement_review_state(NEW.org_id,NEW.requirement_id);\n IF NEW.kind IN (''row'',''comply_only'') AND NOT requirement_ok THEN RAISE EXCEPTION ''requirement_unconfirmed'' USING ERRCODE=''23514''; END IF;\n citation_ok := public.response_citation_valid');
 patched:=replace(patched,'IF cosign_ok AND citation_ok AND quote_current AND (revision.disposition=', 'IF requirement_ok AND cosign_ok AND citation_ok AND quote_current AND (revision.disposition=');
 patched:=replace(patched,'IF NEW.card_id IS NULL THEN expected_reasons :=',E'IF NOT requirement_ok THEN expected_reasons := jsonb_build_array(CASE WHEN requirement_state=''invalidated'' THEN ''requirement_invalidated'' ELSE ''requirement_unconfirmed'' END); END IF;\n IF NEW.card_id IS NULL THEN expected_reasons :=');
 IF patched=definition THEN RAISE EXCEPTION 'response_item_gate integration anchor missing'; END IF;
 EXECUTE patched;
END $$;
"""

CONSUMER_SQL += r"""
CREATE FUNCTION public.requirement_current_pin(p_org uuid,p_req uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path=pg_catalog AS $$
DECLARE r public.requirements; c public.chunks; d public.documents; body jsonb; located text; literal text;
 existing_pin jsonb;
 offset_value integer:=1; found integer; first_pos integer; boundary_pos integer; matches integer:=0; boundaries integer:=0;
 separators constant text:=U&'\FF1B;\FF0C,\3002\FF1A:\3001\FF01!\FF1F?\FF08\FF09()\3010\3011[]\300A\300B\201C\201D\2018\2019\0022\0027\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000';
BEGIN
 SELECT source_pin INTO existing_pin FROM public.requirement_reviews WHERE org_id=p_org AND requirement_id=p_req;
 IF existing_pin IS NOT NULL AND public.requirement_pin_valid(p_org,p_req,existing_pin) THEN RETURN existing_pin; END IF;
 SELECT * INTO r FROM public.requirements WHERE org_id=p_org AND id=p_req;
 SELECT * INTO c FROM public.chunks WHERE org_id=p_org AND id=r.chunk_id AND task_id=r.task_id AND document_id=r.document_id;
 SELECT * INTO d FROM public.documents WHERE org_id=p_org AND id=r.document_id AND task_id=r.task_id;
 IF c.id IS NULL OR d.id IS NULL OR NOT c.citation_verified THEN RETURN NULL; END IF;
 IF r.location IS NOT NULL THEN SELECT b->>'text' INTO located FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location;
 ELSIF c.blocks IS NULL AND c.page=r.page THEN located:=c.text; END IF;
 IF located IS NULL OR public.response_locate_quote(located,r.quote) IS DISTINCT FROM r.quote THEN RETURN NULL; END IF;
 LOOP
  found:=strpos(substring(located FROM offset_value),r.quote);
  EXIT WHEN found=0;
  found:=found+offset_value-1; matches:=matches+1; first_pos:=coalesce(first_pos,found);
  IF (found=1 OR strpos(separators,substring(located FROM found-1 FOR 1))>0)
   AND (found+length(r.quote)>length(located) OR strpos(separators,substring(located FROM found+length(r.quote) FOR 1))>0)
  THEN boundaries:=boundaries+1; boundary_pos:=found; END IF;
  offset_value:=found+1;
 END LOOP;
 IF matches<>1 THEN IF boundaries<>1 THEN RETURN NULL; END IF; first_pos:=boundary_pos; END IF;
 body:=jsonb_build_object('source',public.requirement_content(p_org,p_req)->'source','document_sha256',d.sha256,
 'chunk_sha256',public.requirement_digest(jsonb_build_object('document_id',c.document_id,'text',c.text,'blocks',c.blocks,'seq',c.seq,'page',c.page,'citation_verified',c.citation_verified)),
 'location_sha256',encode(sha256(convert_to(located,'UTF8')),'hex'),'quote_sha256',encode(sha256(convert_to(r.quote,'UTF8')),'hex'),
 'start',first_pos-1,'end',first_pos-1+length(r.quote),'verifier_version','requirement-source-v1');
 RETURN body||jsonb_build_object('binding_sha256',public.requirement_digest(body));
END $$;
CREATE FUNCTION public.requirement_review_fields(p_org uuid,p_req uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('policy_version','requirement-review-v1','revision',coalesce(v.revision,1),
 'review_hash',public.requirement_hash(p_org,p_req,public.requirement_current_pin(p_org,p_req)),
 'state',public.requirement_review_state(p_org,p_req)) FROM public.requirements r LEFT JOIN public.requirement_reviews v
 ON v.org_id=r.org_id AND v.requirement_id=r.id WHERE r.org_id=p_org AND r.id=p_req
$$;
CREATE FUNCTION public.requirement_draft_current(p_org uuid,p_draft uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.draft_runs d WHERE d.org_id=p_org AND d.id=p_draft
 AND jsonb_typeof(d.input_manifest->'requirements')='array'
 AND ARRAY(SELECT r.id FROM public.requirements r WHERE r.org_id=p_org AND r.job_id=d.extraction_job_id ORDER BY r.id)
 = ARRAY(SELECT (fixed->>'requirement_id')::uuid FROM jsonb_array_elements(d.input_manifest->'requirements') fixed ORDER BY (fixed->>'requirement_id')::uuid)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(d.input_manifest->'requirements') fixed
 WHERE fixed->'requirement_review' IS DISTINCT FROM public.requirement_review_fields(p_org,(fixed->>'requirement_id')::uuid)))
$$;
CREATE FUNCTION public.requirement_draft_gate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NOT public.requirement_draft_current(NEW.org_id,NEW.id) THEN RAISE EXCEPTION 'requirement review manifest is stale' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER requirement_draft_gate AFTER INSERT ON public.draft_runs DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION public.requirement_draft_gate();
CREATE FUNCTION public.requirement_rubric_preparation_current(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.score_rubric_sets s WHERE s.org_id=p_org AND s.id=p_rubric
 AND s.input_manifest->'requirement_preparation'=jsonb_build_object('policy_version','requirement-review-v1',
 'entries',(SELECT coalesce(jsonb_agg(jsonb_build_object('requirement_id',r.id,
 'review_hash',public.requirement_hash(p_org,r.id,public.requirement_current_pin(p_org,r.id))) ORDER BY r.id),'[]')
 FROM public.requirements r WHERE r.org_id=p_org AND r.task_id=s.task_id AND r.job_id=s.extraction_job_id AND r.category='scoring')))
$$;
ALTER FUNCTION public.rubric_current_inputs(uuid,uuid) RENAME TO requirement_prior_rubric_current_inputs;
CREATE FUNCTION public.rubric_current_inputs(p_org uuid,p_rubric uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT public.requirement_rubric_preparation_current(p_org,p_rubric) AND public.requirement_prior_rubric_current_inputs(p_org,p_rubric)
$$;
ALTER FUNCTION public.rubric_complete(uuid,uuid) RENAME TO requirement_prior_rubric_complete;
CREATE FUNCTION public.rubric_complete(p_org uuid,p_rubric uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT public.requirement_rubric_preparation_current(p_org,p_rubric) AND public.requirement_rubric_confirmed(p_org,p_rubric) AND public.requirement_prior_rubric_complete(p_org,p_rubric)
$$;
ALTER FUNCTION public.score_inputs_current(uuid,uuid,uuid,integer) RENAME TO requirement_prior_score_inputs_current;
CREATE FUNCTION public.score_inputs_current(p_org uuid,p_draft uuid,p_rubric uuid,p_revision integer) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT public.requirement_draft_current(p_org,p_draft) AND public.requirement_rubric_preparation_current(p_org,p_rubric) AND public.requirement_rubric_confirmed(p_org,p_rubric)
 AND public.requirement_prior_score_inputs_current(p_org,p_draft,p_rubric,p_revision)
$$;
ALTER FUNCTION public.check_current_inputs(uuid,uuid) RENAME TO requirement_prior_check_current_inputs;
CREATE FUNCTION public.check_current_inputs(p_org uuid,p_report uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT public.requirement_draft_current(p_org,c.draft_id) AND public.requirement_prior_check_current_inputs(p_org,p_report)
 FROM public.check_runs c WHERE c.org_id=p_org AND c.id=p_report
$$;
REVOKE ALL ON FUNCTION public.requirement_current_pin(uuid,uuid),public.requirement_review_fields(uuid,uuid),
 public.requirement_rubric_preparation_current(uuid,uuid),public.rubric_current_inputs(uuid,uuid),
 public.requirement_draft_current(uuid,uuid),public.requirement_draft_gate(),public.rubric_complete(uuid,uuid),
 public.score_inputs_current(uuid,uuid,uuid,integer),public.check_current_inputs(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.requirement_current_pin(uuid,uuid),public.requirement_review_fields(uuid,uuid),
 public.requirement_rubric_preparation_current(uuid,uuid),public.rubric_current_inputs(uuid,uuid),
 public.requirement_draft_current(uuid,uuid),public.rubric_complete(uuid,uuid),
 public.score_inputs_current(uuid,uuid,uuid,integer),public.check_current_inputs(uuid,uuid) TO bid_app;

"""

GRANTS_SQL = r"""
GRANT SELECT,INSERT,UPDATE ON public.requirement_review_sets,public.requirement_reviews TO bid_app;
GRANT SELECT,INSERT ON public.requirement_review_events,public.requirement_review_requests TO bid_app;
REVOKE ALL ON FUNCTION public.requirement_canonical(jsonb),public.requirement_digest(jsonb),
 public.requirement_content(uuid,uuid),public.requirement_hash(uuid,uuid,jsonb),public.requirement_pin_valid(uuid,uuid,jsonb),
 public.requirement_review_current(uuid,uuid),public.requirement_review_state(uuid,uuid),
 public.requirement_human_authority(uuid,uuid,text),
 public.requirement_seed_authority(uuid,uuid,uuid,boolean),public.requirement_review_guard(),
 public.requirement_event_pointer(),public.requirement_archived_guard(),public.requirement_review_changed(),public.requirement_source_invalidate(),
 public.requirement_source_lock(),public.requirement_manual_job_guard(),public.requirement_response_gate(),
 public.requirement_rubric_confirmed(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.requirement_canonical(jsonb),public.requirement_digest(jsonb),
 public.requirement_content(uuid,uuid),public.requirement_hash(uuid,uuid,jsonb),public.requirement_pin_valid(uuid,uuid,jsonb),
 public.requirement_review_current(uuid,uuid),public.requirement_review_state(uuid,uuid),
 public.requirement_human_authority(uuid,uuid,text),
 public.requirement_seed_authority(uuid,uuid,uuid,boolean),public.requirement_rubric_confirmed(uuid,uuid) TO bid_app;
"""


AUDIT_SQL = r"""
ALTER TABLE public.audit_logs DROP CONSTRAINT audit_system_cosign_invalidation_actor;
ALTER TABLE public.audit_logs ADD CONSTRAINT audit_system_cosign_invalidation_actor CHECK(actor_user_id IS NOT NULL OR coalesce(
 (action='task.review_round_invalidated' AND actor_kind='system' AND details->>'actor_kind'='system'
 AND details->>'invalidation_id' IS NOT NULL AND details->>'round_id' IS NOT NULL AND details->>'source_id' IS NOT NULL AND details->>'cause' IS NOT NULL)
 OR (action IN ('requirement.invalidated','requirement.repair_citation') AND actor_kind='system'
 AND details->>'actor_kind'='system' AND details->>'event_id' IS NOT NULL AND details->>'requirement_id' IS NOT NULL),false));
DO $$ DECLARE definition text; patched text; BEGIN
 SELECT pg_get_functiondef('public.agent_origin_guard()'::regprocedure) INTO definition;
 patched:=replace(definition,E' IF TG_TABLE_NAME=''audit_logs'' THEN\n', $patch$
 IF TG_TABLE_NAME='audit_logs' THEN
  IF NEW.action IN ('requirement.invalidated','requirement.repair_citation') AND NEW.actor_user_id IS NULL THEN
   IF pg_trigger_depth()<2 OR NOT EXISTS(SELECT 1 FROM public.requirement_review_events e
    WHERE e.org_id=NEW.org_id AND e.requirement_id=NEW.object_id AND e.xmin=pg_current_xact_id()::xid
    AND e.action IN ('invalidate','source_repair') AND e.id=(NEW.details->>'event_id')::uuid
    AND NEW.details=jsonb_build_object('task_id',e.task_id,'extraction_job_id',e.extraction_job_id,
      'requirement_id',e.requirement_id,'event_id',e.id,'revision',e.revision,'actor_kind','system')) THEN
    RAISE EXCEPTION 'requirement invalidation audit requires immutable event' USING ERRCODE='42501'; END IF;
   NEW.actor_kind:='system'; NEW.initiated_by:='legacy_unknown'; NEW.actor_token_id:=NULL;
   NEW.on_behalf_of_user_id:=NULL; NEW.agent_principal_id:=NULL; NEW.agent_session_id:=NULL;
   NEW.agent_step_id:=NULL; NEW.job_id:=NULL; NEW.run_id:=NULL; NEW.invocation_id:=NULL; NEW.command:=NULL;
   RETURN NEW;
  END IF;
$patch$);
 IF patched=definition THEN RAISE EXCEPTION 'agent audit integration anchor missing'; END IF;
 EXECUTE patched;
END $$;
CREATE FUNCTION public.requirement_invalidation_audit() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.action IN ('invalidate','source_repair') THEN
 INSERT INTO public.audit_logs(id,org_id,actor_user_id,actor_token_id,action,object_id,details)
 VALUES(gen_random_uuid(),NEW.org_id,NULL,NULL,
 CASE WHEN NEW.action='source_repair' THEN 'requirement.repair_citation' ELSE 'requirement.invalidated' END,
 NEW.requirement_id,jsonb_build_object('task_id',NEW.task_id,'extraction_job_id',NEW.extraction_job_id,
 'requirement_id',NEW.requirement_id,'event_id',NEW.id,'revision',NEW.revision,'actor_kind','system'));
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER aa_requirement_invalidation_audit AFTER INSERT ON public.requirement_review_events
 FOR EACH ROW EXECUTE FUNCTION public.requirement_invalidation_audit();
REVOKE ALL ON FUNCTION public.requirement_invalidation_audit() FROM PUBLIC;

"""
