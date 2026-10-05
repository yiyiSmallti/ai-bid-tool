"""Task liability budgets, immutable human revisions and tenant balance notifications.

Old workers must be stopped before this migration: new calls require a durable
quote and an authenticated job actor. Unknown historical liability is retained.
"""

import os
import re

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    currency = os.environ.get("BID_BILLING_CURRENCY", "USD")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise RuntimeError("BID_BILLING_CURRENCY must be a three-letter uppercase currency")
    op.execute(SCHEMA_SQL.replace("__CURRENCY__", currency))
    op.execute(BACKFILL_SQL.replace("__CURRENCY__", currency))
    op.execute(INTEGRITY_SQL)
    op.execute(NOTICES_SQL)


SCHEMA_SQL = r"""
ALTER TABLE public.tasks
  ALTER COLUMN budget_usd TYPE numeric(18,8),
  ADD COLUMN budget_limit numeric(18,8),
  ADD COLUMN budget_currency varchar(3) NOT NULL DEFAULT '__CURRENCY__',
  ADD COLUMN budget_revision integer NOT NULL DEFAULT 1,
  ADD COLUMN budget_state varchar(30) NOT NULL DEFAULT 'active',
  ADD CONSTRAINT task_budget_amount CHECK (budget_limit>=0),
  ADD CONSTRAINT task_budget_identity CHECK (budget_currency ~ '^[A-Z]{3}$' AND budget_revision>=1),
  ADD CONSTRAINT task_budget_state CHECK (budget_state IN ('active','currency_review_required'));
CREATE TABLE public.task_budget_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.orgs(id),
  task_id uuid NOT NULL,
  revision integer NOT NULL CHECK (revision>=1),
  "limit" numeric(18,8) CHECK ("limit">=0),
  currency varchar(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  state varchar(30) NOT NULL CHECK (state IN ('active','currency_review_required')),
  actor_user_id uuid,
  origin varchar(20) NOT NULL CHECK (origin IN ('migration','create','human_update')),
  reason_sha256 varchar(64) CHECK (reason_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(org_id,id), UNIQUE(org_id,task_id,revision),
  FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
  FOREIGN KEY(org_id,actor_user_id) REFERENCES public.memberships(org_id,user_id),
  CHECK (origin<>'human_update' OR (actor_user_id IS NOT NULL AND reason_sha256 IS NOT NULL))
);
CREATE INDEX ix_task_budget_revisions_org_id ON public.task_budget_revisions(org_id);
ALTER TABLE public.vendor_calls
  ADD COLUMN task_id uuid,
  ADD COLUMN capability varchar(20) NOT NULL DEFAULT 'llm',
  ADD COLUMN payer varchar(30) NOT NULL DEFAULT 'org_direct',
  ADD COLUMN budget_revision integer,
  ADD COLUMN reserved_task_amount numeric(18,8),
  ADD COLUMN currency varchar(3) NOT NULL DEFAULT '__CURRENCY__',
  ADD COLUMN price_revision varchar(100) NOT NULL DEFAULT 'legacy_unknown',
  ADD COLUMN request_sha256 varchar(64) NOT NULL DEFAULT repeat('0',64),
  ADD COLUMN quote jsonb NOT NULL DEFAULT '{}',
  ADD CONSTRAINT budget_call_task_fk FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
  ADD CONSTRAINT budget_call_revision_fk FOREIGN KEY(org_id,task_id,budget_revision)
    REFERENCES public.task_budget_revisions(org_id,task_id,revision) DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT budget_call_amount CHECK (reserved_task_amount>=0),
  ADD CONSTRAINT budget_call_metadata CHECK (
    capability IN ('llm','vision','ocr','search','embedding','browser')
    AND payer IN ('org_platform','org_direct','platform_absorbed','local_free')
    AND currency ~ '^[A-Z]{3}$' AND request_sha256 ~ '^[0-9a-f]{64}$'
    AND length(price_revision)>0 AND jsonb_typeof(quote)='object'),
  ADD CONSTRAINT budget_call_task_revision CHECK ((task_id IS NULL)=(budget_revision IS NULL));
CREATE INDEX vendor_calls_task_budget ON public.vendor_calls(org_id,task_id,state);
ALTER TABLE public.usage_records
  ALTER COLUMN usd TYPE numeric(18,8), ALTER COLUMN charge TYPE numeric(18,8),
  ADD COLUMN capability varchar(20) NOT NULL DEFAULT 'llm',
  ADD COLUMN payer varchar(30) NOT NULL DEFAULT 'org_direct',
  ADD COLUMN task_amount numeric(18,8),
  ADD COLUMN billing_currency varchar(3) NOT NULL DEFAULT '__CURRENCY__',
  ADD COLUMN price_revision varchar(100) NOT NULL DEFAULT 'legacy_unknown',
  ADD COLUMN search_requests integer NOT NULL DEFAULT 0,
  ADD CONSTRAINT budget_usage_amount CHECK (task_amount>=0 AND search_requests>=0),
  ADD CONSTRAINT budget_usage_metadata CHECK (
    capability IN ('llm','vision','ocr','search','embedding','browser')
    AND payer IN ('org_platform','org_direct','platform_absorbed','local_free')
    AND billing_currency ~ '^[A-Z]{3}$' AND length(price_revision)>0);
CREATE INDEX usage_records_task_budget ON public.usage_records(org_id,task_id);
ALTER TABLE public.jobs
  ADD COLUMN vendor_cost_history_complete boolean NOT NULL DEFAULT true,
  ADD COLUMN actor_user_id uuid,
  ADD COLUMN actor_token_id uuid,
  ADD COLUMN actor_kind varchar(20),
  ADD COLUMN actor_scopes jsonb NOT NULL DEFAULT '[]',
  ADD CONSTRAINT budget_job_actor_user_fk FOREIGN KEY(org_id,actor_user_id)
    REFERENCES public.memberships(org_id,user_id),
  ADD CONSTRAINT budget_job_actor_token_fk FOREIGN KEY(org_id,actor_token_id)
    REFERENCES public.api_tokens(org_id,id),
  ADD CONSTRAINT budget_job_actor_shape CHECK (
    jsonb_typeof(actor_scopes)='array' AND
    (actor_kind IS NULL OR actor_kind IN ('session','token','agent','worker')) AND
    (actor_token_id IS NULL OR actor_kind<>'session'));
ALTER TABLE public.org_balances
  ADD COLUMN low_balance_threshold numeric(18,8) DEFAULT 0 CHECK (low_balance_threshold>=0),
  ADD COLUMN alert_revision integer NOT NULL DEFAULT 1 CHECK (alert_revision>=1),
  ADD COLUMN low_balance_active boolean NOT NULL DEFAULT false,
  ADD COLUMN alert_cycle integer NOT NULL DEFAULT 0 CHECK (alert_cycle>=0);
CREATE TABLE public.org_balance_notices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.orgs(id) REFERENCES public.org_balances(org_id),
  policy_revision integer NOT NULL CHECK (policy_revision>=1),
  cycle integer NOT NULL CHECK (cycle>=1),
  threshold numeric(18,8) NOT NULL CHECK (threshold>=0),
  currency varchar(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  available_balance numeric(18,8) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(org_id,id), UNIQUE(org_id,policy_revision,cycle)
);
CREATE INDEX ix_org_balance_notices_org_id ON public.org_balance_notices(org_id);
CREATE INDEX org_balance_notices_time ON public.org_balance_notices(org_id,created_at DESC,id DESC);
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_budget_scopes
  CHECK (NOT (scopes ?| ARRAY['task:budget:write','billing:alert:write']));
"""

BACKFILL_SQL = r"""
-- The deployment currency is explicit; historical charges recover their original ledger currency.
UPDATE public.tasks SET budget_limit=budget_usd,
  budget_currency=CASE WHEN budget_usd IS NOT NULL THEN 'USD' ELSE '__CURRENCY__' END,
  budget_state=CASE WHEN budget_usd IS NOT NULL AND '__CURRENCY__'<>'USD'
    THEN 'currency_review_required' ELSE 'active' END;
INSERT INTO public.task_budget_revisions(org_id,task_id,revision,"limit",currency,state,origin)
  SELECT org_id,id,1,budget_limit,budget_currency,budget_state,'migration' FROM public.tasks;
UPDATE public.usage_records u SET
  capability=CASE WHEN image_count>0 THEN 'vision' WHEN ocr_pages>0 OR provider='tesseract' THEN 'ocr' ELSE 'llm' END,
  payer=CASE WHEN platform_model_id IS NOT NULL THEN 'org_platform'
             WHEN provider IN ('tesseract','local-browser','playwright') THEN 'local_free'
             WHEN provider IN ('searxng','perplexity') THEN 'platform_absorbed' ELSE 'org_direct' END,
  task_amount=CASE WHEN platform_model_id IS NOT NULL THEN
      CASE WHEN charge=0 OR EXISTS(SELECT FROM public.balance_entries e
        WHERE e.org_id=u.org_id AND e.usage_record_id=u.id) THEN charge ELSE NULL END
    WHEN provider IN ('tesseract','local-browser','playwright','searxng','perplexity') THEN 0
    WHEN '__CURRENCY__'='USD' THEN usd ELSE NULL END,
  billing_currency=CASE WHEN platform_model_id IS NOT NULL THEN COALESCE(
      (SELECT e.currency FROM public.balance_entries e WHERE e.org_id=u.org_id AND e.usage_record_id=u.id LIMIT 1),
      '__CURRENCY__') ELSE '__CURRENCY__' END;
UPDATE public.vendor_calls c SET task_id=j.task_id,
  budget_revision=CASE WHEN j.task_id IS NULL THEN NULL ELSE 1 END,
  capability=COALESCE((SELECT u.capability FROM public.usage_records u WHERE u.org_id=c.org_id AND u.call_id=c.id LIMIT 1),'llm'),
  payer=COALESCE((SELECT u.payer FROM public.usage_records u WHERE u.org_id=c.org_id AND u.call_id=c.id LIMIT 1),
      CASE WHEN j.provider_identity->>'platform_model_id' IS NOT NULL THEN 'org_platform' ELSE 'org_direct' END),
  currency=COALESCE((SELECT u.billing_currency FROM public.usage_records u WHERE u.org_id=c.org_id AND u.call_id=c.id LIMIT 1),
      (SELECT b.currency FROM public.org_balances b WHERE b.org_id=c.org_id),'__CURRENCY__'),
  reserved_task_amount=CASE WHEN c.state='not_sent' THEN 0
      WHEN j.provider_identity->>'platform_model_id' IS NOT NULL THEN c.reserved_charge ELSE NULL END
  FROM public.jobs j WHERE j.org_id=c.org_id AND j.id=c.job_id;
-- These pre-cutover jobs could dispatch absorbed searches without UsageRecord.
-- Liability remains proven zero, but absent vendor cost must not become USD zero.
UPDATE public.jobs SET vendor_cost_history_complete=false
  WHERE kind IN ('screenshot_search','product_simulation') AND (attempts>0 OR status='succeeded');
-- A completed admission must have an authoritative usage. Inconsistent old ledgers need manual repair.
DO $$ BEGIN
  IF EXISTS(SELECT FROM public.vendor_calls c WHERE c.state='completed' AND
      (SELECT count(*) FROM public.usage_records u WHERE u.org_id=c.org_id AND u.job_id=c.job_id
       AND u.run_id=c.run_id AND u.call_id=c.id)<>1) THEN
    RAISE EXCEPTION 'completed historical calls require exactly one usage; reconcile before migration';
  END IF;
END $$;
"""

INTEGRITY_SQL = r"""
ALTER TABLE public.task_budget_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_budget_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON public.task_budget_revisions
  USING(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid)
  WITH CHECK(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid);
ALTER TABLE public.org_balance_notices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_balance_notices FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON public.org_balance_notices
  USING(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid)
  WITH CHECK(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid);
GRANT SELECT,INSERT ON public.task_budget_revisions TO bid_app;
GRANT SELECT ON public.org_balance_notices TO bid_app;
GRANT UPDATE(low_balance_threshold,alert_revision,low_balance_active,alert_cycle)
  ON public.org_balances TO bid_app;

CREATE FUNCTION public.budget_require_human(p_org uuid,p_roles text[]) RETURNS uuid
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v_actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
  IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
    OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
    OR NOT EXISTS(SELECT FROM public.memberships m JOIN public.users u ON u.id=m.user_id
      JOIN public.orgs o ON o.id=m.org_id
      WHERE m.org_id=p_org AND m.user_id=v_actor AND m.active AND u.active AND o.active
        AND m.role=ANY(p_roles)) THEN
    RAISE EXCEPTION 'human budget permission required' USING ERRCODE='42501'; END IF;
  RETURN v_actor;
END $$;
REVOKE ALL ON FUNCTION public.budget_require_human(uuid,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.budget_require_human(uuid,text[]) TO bid_app;

CREATE FUNCTION public.task_budget_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE spent numeric; held numeric; unknown_count bigint;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.budget_revision<>1 OR NEW.budget_state<>'active' THEN
      RAISE EXCEPTION 'new task requires active budget revision one' USING ERRCODE='23514'; END IF;
    IF NEW.budget_limit IS NOT NULL AND current_user='bid_app' THEN
      PERFORM public.budget_require_human(NEW.org_id,ARRAY['admin','bidder']); END IF;
    RETURN NEW;
  END IF;
  IF (NEW.budget_limit,NEW.budget_currency,NEW.budget_revision,NEW.budget_state)
      IS NOT DISTINCT FROM (OLD.budget_limit,OLD.budget_currency,OLD.budget_revision,OLD.budget_state) THEN
    IF NEW.budget_usd IS DISTINCT FROM OLD.budget_usd THEN
      RAISE EXCEPTION 'legacy budget cannot bypass revision history' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
  PERFORM public.budget_require_human(NEW.org_id,ARRAY['admin','bidder']);
  IF NEW.budget_revision<>OLD.budget_revision+1 OR NEW.budget_state<>'active' THEN
    RAISE EXCEPTION 'budget revision conflict' USING ERRCODE='23514'; END IF;
  IF NEW.budget_limit IS NOT NULL THEN
    SELECT COALESCE(sum(task_amount),0), count(*) FILTER(WHERE task_amount IS NULL
      OR (task_amount>0 AND billing_currency<>NEW.budget_currency)) INTO spent,unknown_count
      FROM public.usage_records WHERE org_id=NEW.org_id AND task_id=NEW.id;
    SELECT COALESCE(sum(reserved_task_amount),0),unknown_count+count(*) FILTER(
      WHERE reserved_task_amount IS NULL OR (reserved_task_amount>0 AND currency<>NEW.budget_currency))
      INTO held,unknown_count FROM public.vendor_calls
      WHERE org_id=NEW.org_id AND task_id=NEW.id AND state IN ('pending','unknown');
    IF unknown_count>0 OR NEW.budget_limit<spent+held THEN
      RAISE EXCEPTION 'budget below known or unresolved exposure' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER task_budget_guard BEFORE INSERT OR UPDATE ON public.tasks
  FOR EACH ROW EXECUTE FUNCTION public.task_budget_guard();

CREATE FUNCTION public.task_budget_initial_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  INSERT INTO public.task_budget_revisions(org_id,task_id,revision,"limit",currency,state,actor_user_id,origin)
    VALUES(NEW.org_id,NEW.id,NEW.budget_revision,NEW.budget_limit,NEW.budget_currency,NEW.budget_state,
      NEW.created_by,'create');
  RETURN NEW;
END $$;
CREATE TRIGGER task_budget_initial_revision AFTER INSERT ON public.tasks
  FOR EACH ROW EXECUTE FUNCTION public.task_budget_initial_revision();

CREATE FUNCTION public.task_budget_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE last_revision integer; actor uuid;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'budget history is append only' USING ERRCODE='42501'; END IF;
  SELECT COALESCE(max(revision),0) INTO last_revision FROM public.task_budget_revisions
    WHERE org_id=NEW.org_id AND task_id=NEW.task_id;
  IF NEW.revision<>last_revision+1 THEN
    RAISE EXCEPTION 'budget history revision conflict' USING ERRCODE='23514'; END IF;
  IF NEW.origin='human_update' THEN
    actor:=public.budget_require_human(NEW.org_id,ARRAY['admin','bidder']);
    IF NEW.actor_user_id IS DISTINCT FROM actor OR NEW.reason_sha256 IS NULL THEN
      RAISE EXCEPTION 'budget history actor mismatch' USING ERRCODE='42501'; END IF;
  ELSIF NEW.origin<>'create' OR pg_trigger_depth()<2 OR NEW.revision<>1 THEN
    RAISE EXCEPTION 'only task insertion creates initial budget history' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER task_budget_revision_guard BEFORE INSERT OR UPDATE OR DELETE ON public.task_budget_revisions
  FOR EACH ROW EXECUTE FUNCTION public.task_budget_revision_guard();

CREATE FUNCTION public.task_budget_consistency() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE t public.tasks; h public.task_budget_revisions;
BEGIN
  IF TG_TABLE_NAME='tasks' THEN
    SELECT * INTO t FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.id;
  ELSE
    SELECT * INTO t FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id;
  END IF;
  SELECT * INTO h FROM public.task_budget_revisions WHERE org_id=t.org_id AND task_id=t.id AND revision=t.budget_revision;
  IF h.id IS NULL OR (h."limit",h.currency,h.state) IS DISTINCT FROM
      (t.budget_limit,t.budget_currency,t.budget_state) OR t.budget_revision IS DISTINCT FROM
      (SELECT max(revision) FROM public.task_budget_revisions WHERE org_id=t.org_id AND task_id=t.id) THEN
    RAISE EXCEPTION 'task and budget history disagree' USING ERRCODE='23514'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER task_budget_consistency AFTER INSERT OR UPDATE ON public.tasks
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.task_budget_consistency();
CREATE CONSTRAINT TRIGGER budget_revision_consistency AFTER INSERT ON public.task_budget_revisions
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.task_budget_consistency();

CREATE FUNCTION public.budget_job_actor_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
        token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
        kind text:=NULLIF(current_setting('app.actor_kind',true),'');
BEGIN
  IF current_user='bid_app' AND NEW.org_id IS DISTINCT FROM
      NULLIF(current_setting('app.current_org',true),'')::uuid THEN
    RAISE EXCEPTION 'tenant context does not permit job' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' AND NEW.vendor_cost_history_complete IS DISTINCT FROM OLD.vendor_cost_history_complete THEN
    RAISE EXCEPTION 'historical vendor cost provenance is immutable' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' OR (OLD.actor_user_id IS NULL AND NEW.status='queued' AND kind IS NOT NULL) THEN
    -- Local system work has no human submitter. Admission still rejects a job
    -- without a saved identity; never populate that identity from a task owner.
    IF kind IS NOT NULL AND NOT (kind IN ('worker','system') AND actor IS NULL) THEN
      IF actor IS NULL OR NOT EXISTS(SELECT FROM public.memberships m JOIN public.users u ON u.id=m.user_id
          WHERE m.org_id=NEW.org_id AND m.user_id=actor AND m.active AND u.active) THEN
        RAISE EXCEPTION 'job actor is not active' USING ERRCODE='42501'; END IF;
      IF NEW.actor_user_id IS NOT NULL AND NEW.actor_user_id IS DISTINCT FROM actor THEN
        RAISE EXCEPTION 'job actor does not match authenticated actor' USING ERRCODE='42501'; END IF;
      NEW.actor_user_id:=actor; NEW.actor_token_id:=token; NEW.actor_kind:=kind;
      NEW.actor_scopes:=COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb,'[]');
    ELSIF current_user='bid_app' AND (NEW.actor_user_id IS NOT NULL
        OR NEW.actor_token_id IS NOT NULL OR NEW.actor_kind IS NOT NULL
        OR NEW.actor_scopes<>'[]'::jsonb OR token IS NOT NULL OR actor IS NOT NULL) THEN
      RAISE EXCEPTION 'authenticated job actor required' USING ERRCODE='42501';
    END IF;
  ELSIF (NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind,NEW.actor_scopes)
    IS DISTINCT FROM (OLD.actor_user_id,OLD.actor_token_id,OLD.actor_kind,OLD.actor_scopes) THEN
    RAISE EXCEPTION 'job actor is immutable' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND (NEW.org_id,NEW.id,NEW.task_id,NEW.document_id,NEW.provider_config_id,NEW.provider_identity)
    IS DISTINCT FROM (OLD.org_id,OLD.id,OLD.task_id,OLD.document_id,OLD.provider_config_id,OLD.provider_identity)
    AND EXISTS(SELECT FROM public.vendor_calls WHERE org_id=OLD.org_id AND job_id=OLD.id) THEN
    RAISE EXCEPTION 'job with admitted calls cannot change input binding' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER budget_job_actor_guard BEFORE INSERT OR UPDATE ON public.jobs
  FOR EACH ROW EXECUTE FUNCTION public.budget_job_actor_guard();

CREATE FUNCTION public.budget_call_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j public.jobs; t public.tasks;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'call history is immutable' USING ERRCODE='42501'; END IF;
  IF TG_OP='INSERT' AND NEW.task_id IS NOT NULL THEN
    SELECT * INTO t FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
    IF t.id IS NULL OR NEW.budget_revision IS DISTINCT FROM t.budget_revision THEN
      RAISE EXCEPTION 'call must use current task budget revision' USING ERRCODE='23514'; END IF;
  END IF;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
  IF j.id IS NULL OR NEW.task_id IS DISTINCT FROM j.task_id THEN
    RAISE EXCEPTION 'call must belong to job task' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' AND (NEW.state<>'pending' OR NEW.charge IS NOT NULL
      OR NEW.price_revision='legacy_unknown' OR NEW.quote='{}'::jsonb
      OR j.actor_user_id IS NULL
      OR (NEW.task_id IS NULL AND j.kind<>'provider_test')) THEN
    RAISE EXCEPTION 'call requires admitted quote and authenticated job' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' AND (NEW.quote->>'capability' IS DISTINCT FROM NEW.capability
    OR NEW.quote->>'payer' IS DISTINCT FROM NEW.payer OR NEW.quote->>'currency' IS DISTINCT FROM NEW.currency
    OR NEW.quote->>'price_revision' IS DISTINCT FROM NEW.price_revision
    OR NEW.quote->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
    OR (NEW.quote->>'reserved_charge')::numeric IS DISTINCT FROM NEW.reserved_charge
    OR (NEW.quote->>'reserved_task_amount')::numeric IS DISTINCT FROM NEW.reserved_task_amount) THEN
    RAISE EXCEPTION 'call quote snapshot mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.payer='org_platform' AND NEW.reserved_task_amount IS DISTINCT FROM NEW.reserved_charge THEN
    RAISE EXCEPTION 'platform task liability equals charge' USING ERRCODE='23514'; END IF;
  IF NEW.payer<>'org_platform' AND NEW.reserved_charge<>0 THEN
    RAISE EXCEPTION 'non-platform calls do not reserve balance' USING ERRCODE='23514'; END IF;
  IF NEW.payer IN ('local_free','platform_absorbed') AND NEW.reserved_task_amount IS DISTINCT FROM 0::numeric THEN
    RAISE EXCEPTION 'free liability must be explicit zero' USING ERRCODE='23514'; END IF;
  IF NEW.state='not_sent' AND (NEW.reserved_charge<>0 OR NEW.reserved_task_amount IS DISTINCT FROM 0::numeric) THEN
    RAISE EXCEPTION 'unsent call cannot retain financial hold' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' THEN
    IF NEW.state='not_sent' AND OLD.state<>'pending' AND OLD.state<>'not_sent' THEN
      RAISE EXCEPTION 'unknown dispatch cannot release its hold' USING ERRCODE='23514'; END IF;
    IF (NEW.org_id,NEW.id,NEW.job_id,NEW.run_id,NEW.task_id,NEW.budget_revision,NEW.capability,
        NEW.payer,NEW.currency,NEW.price_revision,NEW.request_sha256,NEW.quote,NEW.created_at)
      IS DISTINCT FROM (OLD.org_id,OLD.id,OLD.job_id,OLD.run_id,OLD.task_id,OLD.budget_revision,OLD.capability,
        OLD.payer,OLD.currency,OLD.price_revision,OLD.request_sha256,OLD.quote,OLD.created_at)
      OR (OLD.state IN ('completed','not_sent') AND NEW IS DISTINCT FROM OLD)
      OR (NEW.state<>'not_sent' AND (NEW.reserved_charge,NEW.reserved_task_amount)
        IS DISTINCT FROM (OLD.reserved_charge,OLD.reserved_task_amount)) THEN
      RAISE EXCEPTION 'admitted call binding is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER budget_call_guard BEFORE INSERT OR UPDATE OR DELETE ON public.vendor_calls
  FOR EACH ROW EXECUTE FUNCTION public.budget_call_guard();

CREATE FUNCTION public.budget_usage_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c public.vendor_calls;
BEGIN
  IF current_user='bid_app' AND NEW.org_id IS DISTINCT FROM
      NULLIF(current_setting('app.current_org',true),'')::uuid THEN
    RAISE EXCEPTION 'tenant context does not permit usage' USING ERRCODE='42501'; END IF;
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'usage is immutable' USING ERRCODE='42501'; END IF;
  IF NEW.call_id IS NULL THEN RAISE EXCEPTION 'usage requires admitted call' USING ERRCODE='23514'; END IF;
  SELECT * INTO c FROM public.vendor_calls WHERE org_id=NEW.org_id AND job_id=NEW.job_id
    AND run_id=NEW.run_id AND id=NEW.call_id;
  IF c.id IS NULL OR c.state='not_sent' OR (NEW.task_id,NEW.capability,NEW.payer,NEW.billing_currency)
    IS DISTINCT FROM (c.task_id,c.capability,c.payer,c.currency)
    OR (c.price_revision<>'legacy_unknown' AND NEW.price_revision<>c.price_revision) THEN
    RAISE EXCEPTION 'settlement must match admission binding' USING ERRCODE='23514'; END IF;
  IF c.quote<>'{}'::jsonb AND (
    NEW.platform_model_id IS DISTINCT FROM c.quote->>'platform_model_id'
    OR NEW.provider_config_id IS DISTINCT FROM NULLIF(c.quote->>'provider_config_id','')::uuid
    OR NEW.provider IS DISTINCT FROM c.quote->>'provider'
    OR NEW.version IS DISTINCT FROM c.quote->>'version') THEN
    RAISE EXCEPTION 'settlement provider identity differs from quote' USING ERRCODE='23514'; END IF;
  IF NEW.payer='org_platform' AND (NEW.platform_model_id IS NULL OR NEW.charge IS NULL
      OR NEW.task_amount IS DISTINCT FROM NEW.charge) THEN
    RAISE EXCEPTION 'platform usage requires charge liability' USING ERRCODE='23514'; END IF;
  IF NEW.payer<>'org_platform' AND (NEW.platform_model_id IS NOT NULL OR COALESCE(NEW.charge,0)<>0) THEN
    RAISE EXCEPTION 'non-platform usage cannot debit balance' USING ERRCODE='23514'; END IF;
  IF NEW.payer IN ('local_free','platform_absorbed') AND NEW.task_amount IS DISTINCT FROM 0::numeric THEN
    RAISE EXCEPTION 'free usage requires zero liability' USING ERRCODE='23514'; END IF;
  IF NEW.payer='org_direct' AND NEW.task_amount IS NOT NULL AND
      (NEW.billing_currency<>'USD' OR NEW.usd IS NULL OR NEW.task_amount<>NEW.usd) THEN
    RAISE EXCEPTION 'BYOK liability requires unconverted USD price' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER budget_usage_guard BEFORE INSERT OR UPDATE OR DELETE ON public.usage_records
  FOR EACH ROW EXECUTE FUNCTION public.budget_usage_guard();

CREATE FUNCTION public.budget_call_settlement_consistency() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c public.vendor_calls; n bigint; amount numeric;
BEGIN
  IF TG_TABLE_NAME='vendor_calls' THEN
    SELECT * INTO c FROM public.vendor_calls WHERE org_id=NEW.org_id AND id=NEW.id;
  ELSE
    SELECT * INTO c FROM public.vendor_calls WHERE org_id=NEW.org_id AND id=NEW.call_id;
  END IF;
  SELECT count(*),COALESCE(sum(charge),0) INTO n,amount FROM public.usage_records
    WHERE org_id=c.org_id AND job_id=c.job_id AND run_id=c.run_id AND call_id=c.id;
  IF (c.state='completed' AND (n<>1 OR c.charge<>amount)) OR (c.state<>'completed' AND n<>0) THEN
    RAISE EXCEPTION 'completed call requires exactly one matching settlement' USING ERRCODE='23514'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER budget_call_settlement_consistency AFTER INSERT OR UPDATE ON public.vendor_calls
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.budget_call_settlement_consistency();
CREATE CONSTRAINT TRIGGER budget_usage_settlement_consistency AFTER INSERT ON public.usage_records
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.budget_call_settlement_consistency();
"""

NOTICES_SQL = r"""
-- Deferred triggers run under the committing tenant role after platform functions return.
-- Platform function roles gain no new budget or notification access.
GRANT INSERT ON public.org_balance_notices TO bid_app;

CREATE FUNCTION public.balance_notice_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  IF TG_OP<>'INSERT' OR pg_trigger_depth()<2 THEN
    RAISE EXCEPTION 'balance notices are system append-only events' USING ERRCODE='42501'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER balance_notice_guard BEFORE INSERT OR UPDATE OR DELETE ON public.org_balance_notices
  FOR EACH ROW EXECUTE FUNCTION public.balance_notice_guard();

CREATE FUNCTION public.balance_alert_update() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE held numeric; available numeric; is_low boolean;
BEGIN
  IF TG_OP='UPDATE' AND (NEW.low_balance_threshold,NEW.alert_revision)
    IS DISTINCT FROM (OLD.low_balance_threshold,OLD.alert_revision) THEN
    PERFORM public.budget_require_human(NEW.org_id,ARRAY['admin']);
    IF NEW.alert_revision<>OLD.alert_revision+1 THEN
      RAISE EXCEPTION 'alert revision conflict' USING ERRCODE='23514'; END IF;
  END IF;
  IF pg_trigger_depth()<2 THEN
    IF (NEW.low_balance_active,NEW.alert_cycle) IS DISTINCT FROM (OLD.low_balance_active,OLD.alert_cycle) THEN
      RAISE EXCEPTION 'balance alert state is system maintained' USING ERRCODE='42501'; END IF;
    RETURN NEW;
  END IF;
  SELECT COALESCE(sum(reserved_charge),0) INTO held FROM public.vendor_calls
    WHERE org_id=NEW.org_id AND state IN ('pending','unknown');
  available:=NEW.balance-held;
  is_low:=NEW.low_balance_threshold IS NOT NULL AND available<=NEW.low_balance_threshold;
  NEW.low_balance_active:=is_low;
  NEW.alert_cycle:=OLD.alert_cycle;
  IF is_low AND NOT OLD.low_balance_active THEN
    NEW.alert_cycle:=OLD.alert_cycle+1;
    INSERT INTO public.org_balance_notices(org_id,policy_revision,cycle,threshold,currency,available_balance)
      VALUES(NEW.org_id,NEW.alert_revision,NEW.alert_cycle,NEW.low_balance_threshold,NEW.currency,available);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER balance_alert_update BEFORE UPDATE ON public.org_balances
  FOR EACH ROW EXECUTE FUNCTION public.balance_alert_update();

CREATE FUNCTION public.balance_alert_refresh() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE previous_org text:=COALESCE(current_setting('app.current_org',true),'');
BEGIN
  -- Deferred triggers read final net exposure, not transient hold-release/debit ordering.
  -- The event's immutable org_id restores context after a platform function has returned.
  PERFORM set_config('app.current_org',NEW.org_id::text,true);
  UPDATE public.org_balances SET updated_at=clock_timestamp() WHERE org_id=NEW.org_id;
  PERFORM set_config('app.current_org',previous_org,true);
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER balance_alert_initial AFTER INSERT ON public.org_balances
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.balance_alert_refresh();
CREATE CONSTRAINT TRIGGER balance_alert_final AFTER UPDATE ON public.org_balances
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (pg_trigger_depth()=0)
  EXECUTE FUNCTION public.balance_alert_refresh();
CREATE CONSTRAINT TRIGGER balance_alert_reservation AFTER INSERT OR UPDATE ON public.vendor_calls
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.balance_alert_refresh();
REVOKE ALL ON FUNCTION public.balance_alert_update(),public.balance_alert_refresh(),public.balance_notice_guard() FROM PUBLIC;
-- Trigger functions execute only through their attached table triggers.
-- Evaluate existing persisted balances once; reads and preflight never create notifications.
UPDATE public.org_balances SET updated_at=updated_at;
"""


def downgrade():
    raise RuntimeError(
        "Retain task budgets, usage liability, unresolved holds and audit history; rollback is application-only"
    )
