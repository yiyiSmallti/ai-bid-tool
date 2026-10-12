"""Remove product billing while retaining tenant usage and durable call admission.

Stop API and workers and retain a pre-migration backup before applying this
irreversible schema change. Historical audit rows are deliberately untouched.
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(REMOVE_SQL)
    op.execute(ADMISSION_SQL)
    # Driver SQL preserves POSIX character classes without treating their colons as binds.
    op.get_bind().exec_driver_sql(PLATFORM_SQL)


REMOVE_SQL = r"""
-- Deferred foreign-key events queued by the row rewrites below would make the
-- later ALTER TABLE on the same tables fail with "pending trigger events".
SET CONSTRAINTS ALL IMMEDIATE;
DROP TRIGGER task_budget_guard ON public.tasks;
DROP TRIGGER task_budget_initial_revision ON public.tasks;
DROP TRIGGER task_budget_consistency ON public.tasks;
DROP TRIGGER task_budget_revision_guard ON public.task_budget_revisions;
DROP TRIGGER budget_revision_consistency ON public.task_budget_revisions;
DROP TRIGGER budget_job_actor_guard ON public.jobs;
DROP TRIGGER budget_call_guard ON public.vendor_calls;
DROP TRIGGER budget_call_settlement_consistency ON public.vendor_calls;
DROP TRIGGER budget_usage_guard ON public.usage_records;
DROP TRIGGER budget_usage_settlement_consistency ON public.usage_records;
DROP TRIGGER balance_notice_guard ON public.org_balance_notices;
DROP TRIGGER balance_alert_update ON public.org_balances;
DROP TRIGGER balance_alert_initial ON public.org_balances;
DROP TRIGGER balance_alert_final ON public.org_balances;
DROP TRIGGER balance_alert_reservation ON public.vendor_calls;
DROP TRIGGER platform_cards_final ON public.platform_cards;
DROP FUNCTION public.budget_require_human(uuid,text[]);
DROP FUNCTION public.task_budget_guard();
DROP FUNCTION public.task_budget_initial_revision();
DROP FUNCTION public.task_budget_revision_guard();
DROP FUNCTION public.task_budget_consistency();
DROP FUNCTION public.budget_job_actor_guard();
DROP FUNCTION public.budget_call_guard();
DROP FUNCTION public.budget_usage_guard();
DROP FUNCTION public.budget_call_settlement_consistency();
DROP FUNCTION public.balance_notice_guard();
DROP FUNCTION public.balance_alert_update();
DROP FUNCTION public.balance_alert_refresh();
DROP FUNCTION public.platform_cards_final_status();
DROP FUNCTION public.redeem_card(text,uuid,uuid,text);
DROP FUNCTION public.platform_adjust_balance(uuid,text,numeric,text,text,text);
DROP FUNCTION public.platform_org_summaries();
DROP FUNCTION public.platform_usage_summary(date,date);
ALTER TABLE public.vendor_calls DROP CONSTRAINT budget_call_revision_fk;
-- Policies, grants, indexes and table-owned constraints disappear with these tables.
DROP TABLE public.org_balance_notices;
DROP TABLE public.balance_entries;
DROP TABLE public.org_balances;
DROP TABLE public.platform_cards;
DROP TABLE public.task_budget_revisions;
ALTER TABLE public.tasks
  DROP COLUMN budget_usd, DROP COLUMN budget_limit, DROP COLUMN budget_currency,
  DROP COLUMN budget_revision, DROP COLUMN budget_state;
ALTER TABLE public.usage_records
  DROP COLUMN usd, DROP COLUMN charge, DROP COLUMN task_amount, DROP COLUMN payer,
  DROP COLUMN billing_currency, DROP COLUMN price_revision, DROP COLUMN image_price_revision,
  ADD COLUMN browser_units integer NOT NULL DEFAULT 0 CHECK(browser_units>=0),
  ADD CONSTRAINT usage_search_requests_nonnegative CHECK(search_requests>=0),
  ADD CONSTRAINT usage_image_manifest CHECK(
    (image_count=0 AND image_input_sha256 IS NULL) OR
    (image_count>0 AND image_input_sha256 IS NOT NULL AND image_input_sha256 ~ '^[0-9a-f]{64}$')),
  ADD CONSTRAINT usage_capability CHECK(capability IN ('llm','vision','ocr','search','embedding','browser'));
ALTER INDEX public.usage_records_task_budget RENAME TO usage_records_task;
ALTER TABLE public.vendor_calls
  DROP COLUMN budget_revision, DROP COLUMN reserved_task_amount, DROP COLUMN currency,
  DROP COLUMN price_revision, DROP COLUMN payer, DROP COLUMN reserved_charge, DROP COLUMN charge;
ALTER TABLE public.vendor_calls RENAME COLUMN quote TO provider_identity;
ALTER TABLE public.vendor_calls RENAME CONSTRAINT budget_call_task_fk TO vendor_call_task_fk;
ALTER INDEX public.vendor_calls_task_budget RENAME TO vendor_calls_task;
ALTER TABLE public.vendor_calls ADD CONSTRAINT vendor_call_metadata CHECK(
  capability IN ('llm','vision','ocr','search','embedding','browser')
  AND request_sha256 ~ '^[0-9a-f]{64}$' AND jsonb_typeof(provider_identity)='object');
ALTER TABLE public.jobs DROP COLUMN vendor_cost_history_complete;
ALTER TABLE public.jobs RENAME CONSTRAINT budget_job_actor_user_fk TO job_actor_user_fk;
ALTER TABLE public.jobs RENAME CONSTRAINT budget_job_actor_token_fk TO job_actor_token_fk;
ALTER TABLE public.jobs RENAME CONSTRAINT budget_job_actor_shape TO job_actor_shape;
ALTER TABLE public.platform_models
  DROP COLUMN vendor_input_usd_per_mtok, DROP COLUMN vendor_output_usd_per_mtok,
  DROP COLUMN sale_input_per_mtok, DROP COLUMN sale_output_per_mtok;
ALTER TABLE public.api_tokens DROP CONSTRAINT token_forbidden_budget_scopes;
ALTER TABLE public.api_tokens DROP CONSTRAINT token_forbidden_scopes;
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_scopes
  CHECK(NOT(scopes ? 'evidence:confirm') AND NOT(scopes ? 'export'));
UPDATE public.api_tokens SET scopes=scopes-ARRAY['billing:read','billing:redeem','billing:alert:write','task:budget:write'];

-- Temporarily detach only the affected row guards while transforming existing
-- operational state. No audit table, audit trigger or RLS policy is changed.
ALTER TABLE public.jobs DISABLE TRIGGER USER;
ALTER TABLE public.agent_sessions DISABLE TRIGGER USER;
ALTER TABLE public.agent_pauses DISABLE TRIGGER USER;
ALTER TABLE public.agent_principals DISABLE TRIGGER USER;
ALTER TABLE public.provider_configs DISABLE TRIGGER provider_revision_gate;
ALTER TABLE public.platform_models DISABLE TRIGGER platform_clef_row_gate;
ALTER TABLE public.platform_models DISABLE TRIGGER platform_credential_catalog_gate;
ALTER TABLE public.agent_pauses DROP CONSTRAINT agent_pause_shape;
ALTER TABLE public.agent_pauses DROP COLUMN budget_ref;
UPDATE public.agent_pauses SET kind='recovery' WHERE kind='budget';
ALTER TABLE public.agent_pauses ADD CONSTRAINT agent_pause_shape CHECK(
  kind IN ('human_action','authority','recovery')
  AND status IN ('pending','resolved','cancelled','expired')
  AND length(question) BETWEEN 1 AND 2000);
UPDATE public.agent_sessions SET limits=limits-ARRAY['max_vendor_usd','max_platform_charge','billing_currency'];
UPDATE public.agent_principals SET
  scopes=scopes-ARRAY['billing:read','billing:redeem','billing:alert:write','task:budget:write'],
  initial_grants=initial_grants-ARRAY['billing:read','billing:redeem','billing:alert:write','task:budget:write'];
UPDATE public.jobs SET actor_scopes=actor_scopes-ARRAY['billing:read','billing:redeem','billing:alert:write','task:budget:write'];
UPDATE public.platform_models SET clef_settings=clef_settings-ARRAY['price_revision','fixed_sale_price','currency']
  WHERE clef_settings IS NOT NULL;
CREATE FUNCTION public.remove_billing_snapshot(value jsonb) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE cleaned jsonb;
BEGIN
 IF jsonb_typeof(value)='object' THEN
   SELECT COALESCE(jsonb_object_agg(item.key,public.remove_billing_snapshot(item.value)),'{}'::jsonb)
     INTO cleaned FROM jsonb_each(value) item
     WHERE item.key<>ALL(ARRAY['usd','charge','task_amount','payer','billing_currency','currency',
       'price_revision','image_price_revision','reserved_charge','reserved_task_amount',
       'vendor_usd_upper_bound','task_amount_upper_bound','platform_charge_upper_bound',
       'sale_input_per_mtok','sale_output_per_mtok','vendor_input_usd_per_mtok','vendor_output_usd_per_mtok',
       'input_usd_per_mtok','output_usd_per_mtok','llm_input_usd_per_mtok','llm_output_usd_per_mtok',
       'fixed_sale_price','max_vendor_usd','max_platform_charge','budget_revision',
       'workers_ai_billing_mode','sale','pricing','unit_price','price']);
   RETURN cleaned;
 ELSIF jsonb_typeof(value)='array' THEN
   SELECT COALESCE(jsonb_agg(public.remove_billing_snapshot(item.value) ORDER BY item.ordinality),'[]'::jsonb)
     INTO cleaned FROM jsonb_array_elements(value) WITH ORDINALITY item(value,ordinality);
   RETURN cleaned;
 END IF;
 RETURN value;
END $$;
-- Rewrite only provider metadata; preserve revision ids, actor metadata and encrypted keys.
UPDATE public.provider_configs SET data=public.remove_billing_snapshot(data);
UPDATE public.vendor_calls SET provider_identity=public.remove_billing_snapshot(provider_identity);
UPDATE public.jobs SET provider_identity=public.remove_billing_snapshot(provider_identity)
  WHERE provider_identity IS NOT NULL;
-- These retired operational envelopes are separate from the hash-bound
-- submission inputs and published reports, which must remain intact.
UPDATE public.jobs SET result=result-ARRAY['budget','budget_preflight']
  WHERE result ?| ARRAY['budget','budget_preflight'];
-- Only the envelope's cost object is an operational usage snapshot. Rebuild it
-- from durable metering rows; never rewrite business finding basis or audit rows.
WITH recorded AS (
  SELECT org_id,job_id,count(*) AS calls,COALESCE(sum(tokens),0) AS tokens,
    COALESCE(sum(ocr_pages),0) AS pages,COALESCE(sum(search_requests),0) AS searches,
    COALESCE(sum(browser_units),0) AS browser_units
  FROM public.usage_records GROUP BY org_id,job_id
), unresolved AS (
  SELECT org_id,job_id,count(*) AS calls FROM public.vendor_calls
  WHERE state IN ('pending','unknown') GROUP BY org_id,job_id
), projected AS (
  SELECT j.org_id,j.id,jsonb_build_object(
    'calls',COALESCE(r.calls,0),'llm_tokens',COALESCE(r.tokens,0),
    'ocr_pages',COALESCE(r.pages,0),'search_requests',COALESCE(r.searches,0),
    'browser_units',COALESCE(r.browser_units,0),'unresolved_calls',COALESCE(u.calls,0)) AS cost
  FROM public.jobs j
  LEFT JOIN recorded r ON r.org_id=j.org_id AND r.job_id=j.id
  LEFT JOIN unresolved u ON u.org_id=j.org_id AND u.job_id=j.id
  WHERE jsonb_typeof(j.result->'cost')='object'
)
UPDATE public.jobs j SET result=jsonb_set(j.result,'{cost}',p.cost)
  FROM projected p WHERE p.org_id=j.org_id AND p.id=j.id;
UPDATE public.agent_sessions SET model_snapshot=public.remove_billing_snapshot(model_snapshot);
UPDATE public.platform_models SET clef_settings=public.remove_billing_snapshot(clef_settings)
  WHERE clef_settings IS NOT NULL;
DROP FUNCTION public.remove_billing_snapshot(jsonb);
ALTER TABLE public.jobs ENABLE TRIGGER USER;
ALTER TABLE public.agent_sessions ENABLE TRIGGER USER;
ALTER TABLE public.agent_pauses ENABLE TRIGGER USER;
ALTER TABLE public.agent_principals ENABLE TRIGGER USER;
ALTER TABLE public.provider_configs ENABLE TRIGGER provider_revision_gate;
ALTER TABLE public.platform_models ENABLE TRIGGER platform_clef_row_gate;
ALTER TABLE public.platform_models ENABLE TRIGGER platform_credential_catalog_gate;
"""

ADMISSION_SQL = r"""
CREATE FUNCTION public.job_actor_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE actor uuid:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
        token uuid:=NULLIF(current_setting('app.actor_token_id',true),'')::uuid;
        kind text:=NULLIF(current_setting('app.actor_kind',true),'');
BEGIN
  IF current_user='bid_app' AND NEW.org_id IS DISTINCT FROM
      NULLIF(current_setting('app.current_org',true),'')::uuid THEN
    RAISE EXCEPTION 'tenant context does not permit job' USING ERRCODE='42501'; END IF;
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
CREATE TRIGGER job_actor_guard BEFORE INSERT OR UPDATE ON public.jobs
  FOR EACH ROW EXECUTE FUNCTION public.job_actor_guard();

CREATE FUNCTION public.vendor_call_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE j public.jobs;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'call history is immutable' USING ERRCODE='42501'; END IF;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
  IF j.id IS NULL OR NEW.task_id IS DISTINCT FROM j.task_id THEN
    RAISE EXCEPTION 'call must belong to job task' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'pending' OR j.actor_user_id IS NULL
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
    IF EXISTS(SELECT FROM public.vendor_calls prior_call WHERE prior_call.org_id=NEW.org_id
      AND prior_call.job_id=NEW.job_id AND prior_call.request_sha256=NEW.request_sha256
      AND ((j.agent_session_id IS NOT NULL OR NEW.capability='browser')
           AND prior_call.state IN ('pending','unknown')
        OR j.agent_session_id IS NULL AND NEW.capability<>'browser'
           AND prior_call.state='pending' AND prior_call.run_id<>NEW.run_id)) THEN
      RAISE EXCEPTION 'unresolved call cannot be dispatched again' USING ERRCODE='23514'; END IF;
  ELSE
    IF NEW.state='not_sent' AND OLD.state NOT IN ('pending','not_sent') THEN
      RAISE EXCEPTION 'unknown dispatch cannot become unsent' USING ERRCODE='23514'; END IF;
    IF (NEW.org_id,NEW.id,NEW.job_id,NEW.run_id,NEW.task_id,NEW.capability,
        NEW.request_sha256,NEW.provider_identity,NEW.created_at)
      IS DISTINCT FROM (OLD.org_id,OLD.id,OLD.job_id,OLD.run_id,OLD.task_id,OLD.capability,
        OLD.request_sha256,OLD.provider_identity,OLD.created_at)
      OR (OLD.state IN ('completed','not_sent') AND NEW IS DISTINCT FROM OLD)
      OR (OLD.state='unknown' AND NEW.state='pending') THEN
      RAISE EXCEPTION 'admitted call binding is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vendor_call_guard BEFORE INSERT OR UPDATE OR DELETE ON public.vendor_calls
  FOR EACH ROW EXECUTE FUNCTION public.vendor_call_guard();

CREATE FUNCTION public.usage_admission_guard() RETURNS trigger
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
  IF c.id IS NULL OR c.state='not_sent' OR (NEW.task_id,NEW.capability)
    IS DISTINCT FROM (c.task_id,c.capability) THEN
    RAISE EXCEPTION 'usage must match admission binding' USING ERRCODE='23514'; END IF;
  IF c.provider_identity<>'{}'::jsonb AND (
    NEW.platform_model_id IS DISTINCT FROM c.provider_identity->>'platform_model_id'
    OR NEW.provider_config_id IS DISTINCT FROM NULLIF(c.provider_identity->>'provider_config_id','')::uuid
    OR NEW.provider IS DISTINCT FROM c.provider_identity->>'provider'
    OR NEW.version IS DISTINCT FROM c.provider_identity->>'version') THEN
    RAISE EXCEPTION 'usage provider identity differs from admission' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
-- Same-event triggers run in name order; authority and history checks precede provider validation.
CREATE TRIGGER admission_usage_guard BEFORE INSERT OR UPDATE OR DELETE ON public.usage_records
  FOR EACH ROW EXECUTE FUNCTION public.usage_admission_guard();

CREATE FUNCTION public.vendor_call_usage_consistency() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c public.vendor_calls; n bigint;
BEGIN
  IF TG_TABLE_NAME='vendor_calls' THEN
    SELECT * INTO c FROM public.vendor_calls WHERE org_id=NEW.org_id AND id=NEW.id;
  ELSE
    SELECT * INTO c FROM public.vendor_calls WHERE org_id=NEW.org_id AND id=NEW.call_id;
  END IF;
  SELECT count(*) INTO n FROM public.usage_records
    WHERE org_id=c.org_id AND job_id=c.job_id AND run_id=c.run_id AND call_id=c.id;
  IF (c.state='completed' AND n<>1) OR (c.state<>'completed' AND n<>0) THEN
    RAISE EXCEPTION 'completed call requires exactly one matching usage' USING ERRCODE='23514'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER vendor_call_usage_consistency AFTER INSERT OR UPDATE ON public.vendor_calls
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.vendor_call_usage_consistency();
CREATE CONSTRAINT TRIGGER usage_call_consistency AFTER INSERT ON public.usage_records
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.vendor_call_usage_consistency();
REVOKE ALL ON FUNCTION public.job_actor_guard(),public.vendor_call_guard(),
  public.usage_admission_guard(),public.vendor_call_usage_consistency() FROM PUBLIC;
CREATE OR REPLACE FUNCTION provider_usage_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE cfg public.provider_configs; parent public.jobs;
        BEGIN
            IF NEW.job_id IS NOT NULL THEN
                SELECT * INTO parent FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
                IF NEW.task_id IS DISTINCT FROM parent.task_id
                    OR (NEW.provider_config_id IS DISTINCT FROM parent.provider_config_id
                        AND NOT (parent.kind = 'bid_review' AND NEW.provider_config_id IS NULL
                                 AND NEW.platform_model_id = 'bid-review-clef')) THEN
                    RAISE EXCEPTION 'Usage model or task differs from job' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.task_id IS NULL AND (parent.id IS NULL OR parent.kind <> 'provider_test') THEN
                RAISE EXCEPTION 'Taskless usage requires a provider test' USING ERRCODE='23514';
            END IF;
            IF NEW.provider_config_id IS NOT NULL THEN
                SELECT * INTO cfg FROM public.provider_configs
                    WHERE org_id=NEW.org_id AND id=NEW.provider_config_id;
                IF cfg.id IS NULL OR (cfg.source='org' AND NEW.platform_model_id IS NOT NULL)
                    OR (cfg.source='platform' AND NEW.platform_model_id IS DISTINCT FROM cfg.platform_model_id) THEN
                    RAISE EXCEPTION 'Usage model source differs from configuration' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
"""

PLATFORM_SQL = r"""
CREATE FUNCTION platform_org_summaries()
        RETURNS TABLE (id uuid, name text, active boolean, created_at timestamptz,
                       member_count bigint, admin_emails text[])
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT o.id, o.name::text, o.active, o.created_at,
                 count(m.id) FILTER (WHERE m.active),
                 coalesce(array_agg(u.email::text ORDER BY u.email)
                          FILTER (WHERE m.active AND m.role = 'admin'), '{}')
          FROM orgs o
          LEFT JOIN memberships m ON m.org_id = o.id
          LEFT JOIN users u ON u.id = m.user_id
          GROUP BY o.id
          ORDER BY o.created_at, o.id
        $$;
CREATE FUNCTION platform_usage_summary(from_month date, to_month date)
        RETURNS TABLE (org_id uuid, month date, source text, provider text, model text,
                       calls bigint, tokens bigint, input_tokens bigint, output_tokens bigint,
                       ocr_pages bigint, search_requests bigint, browser_units bigint)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT r.org_id, date_trunc('month', r.created_at AT TIME ZONE 'UTC')::date,
                 CASE WHEN r.platform_model_id IS NOT NULL THEN 'platform'
                      WHEN r.provider_config_id IS NOT NULL THEN 'org' ELSE 'local' END,
                 r.provider::text, r.model::text, count(*),
                 sum(r.tokens), sum(r.input_tokens), sum(r.output_tokens), sum(r.ocr_pages),
                 sum(r.search_requests), sum(r.browser_units)
          FROM public.usage_records r
          WHERE NOT r.test_only
            AND r.created_at >= (date_trunc('month', from_month)::timestamp AT TIME ZONE 'UTC')
            AND r.created_at < ((date_trunc('month', to_month) + interval '1 month')::timestamp AT TIME ZONE 'UTC')
          GROUP BY 1, 2, 3, 4, 5 ORDER BY 2, 1, 3, 4, 5
        $$;
CREATE OR REPLACE FUNCTION public.platform_clef_manage(p_action text,p_actor text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_prior jsonb; v_new jsonb; v_revision bigint; v_name text;
  v_workers record; v_gateway record; v_credential record;
BEGIN
  IF p_actor IS NULL OR p_actor !~ '^[^@[:space:]]+@[^@[:space:]]+$'
    OR length(p_actor)>254 OR jsonb_typeof(p_body) IS DISTINCT FROM 'object'
    OR p_action IS NULL OR p_action NOT IN ('set','check') THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('platform-clef-config',0));
  SELECT clef_settings INTO v_prior FROM public.platform_models WHERE id='bid-review-clef';
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
    SELECT id,name,revision INTO v_workers FROM public.platform_credentials
      WHERE id=(p_body->>'workers_credential_id')::uuid AND purpose='clef_workers_ai'
        AND provider='cloudflare' AND endpoint='https://gateway.ai.cloudflare.com' AND state<>'removed';
    SELECT id,revision INTO v_gateway FROM public.platform_credentials
      WHERE id=(p_body->>'gateway_credential_id')::uuid AND purpose='clef_gateway'
        AND provider='cloudflare' AND endpoint='https://api.cloudflare.com/client/v4' AND state<>'removed';
    IF v_workers.id IS NULL OR v_gateway.id IS NULL THEN
      RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001';
    END IF;
    v_name := v_workers.name;
    v_new := (p_body-'expected_revision') || jsonb_build_object(
      'expected_revision',NULL,'platform_model_id','bid-review-clef','revision',v_revision,
      'workers_credential_revision',v_workers.revision,'gateway_credential_revision',v_gateway.revision,
      'gateway_check',NULL,'updated_at',clock_timestamp());
  ELSE
    IF v_prior IS NULL OR p_body-ARRAY['expected_revision','gateway_check','check_error'] <> '{}'::jsonb THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    SELECT name INTO v_name FROM public.platform_credentials
      WHERE id=(v_prior->>'workers_credential_id')::uuid;
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
  INSERT INTO public.platform_models(id,capability,provider,model,base_url,credential,
    enabled,is_default,revision,updated_by,clef_settings)
  VALUES('bid-review-clef','vision','cloudflare','@cf/cloudflare/clef','https://gateway.ai.cloudflare.com',
    v_name,(v_new->>'enabled')::boolean,false,v_revision,p_actor,v_new)
  ON CONFLICT(id) DO UPDATE SET credential=EXCLUDED.credential,enabled=EXCLUDED.enabled,
    revision=EXCLUDED.revision,updated_by=p_actor,updated_at=clock_timestamp(),clef_settings=v_new;
  INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details)
  VALUES(gen_random_uuid(),p_actor,'platform.clef.'||p_action,'bid-review-clef',
    (CASE WHEN p_action='check' AND p_body->'gateway_check'='null'::jsonb THEN 'failed' ELSE 'success' END),
    jsonb_strip_nulls(jsonb_build_object('revision',v_revision,
      'enabled',v_new->'enabled','error_code',p_body->'check_error')));
  RETURN v_new;
END $$;
GRANT CREATE ON SCHEMA public TO bid_platform_fn;
ALTER FUNCTION public.platform_org_summaries() OWNER TO bid_platform_fn;
ALTER FUNCTION public.platform_usage_summary(date,date) OWNER TO bid_platform_fn;
REVOKE ALL ON FUNCTION public.platform_org_summaries(),public.platform_usage_summary(date,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_org_summaries(),public.platform_usage_summary(date,date) TO bid_app;
REVOKE CREATE ON SCHEMA public FROM bid_platform_fn;
"""


def downgrade():
    raise RuntimeError("Data-preserving rollback required; restore the pre-0069 backup")
