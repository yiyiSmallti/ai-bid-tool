"""Clef platform configuration, credential purposes and tenant presence boundaries."""

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade():
    platform_upgrade()
    presence_upgrade()


def platform_upgrade():
    op.execute(r"""
      ALTER TABLE public.platform_credentials DROP CONSTRAINT platform_credentials_purpose_check;
      ALTER TABLE public.platform_credentials DROP CONSTRAINT platform_credentials_provider_check;
      ALTER TABLE public.platform_credentials DROP CONSTRAINT credential_provider_purpose;
      ALTER TABLE public.platform_credentials ADD CONSTRAINT platform_credentials_purpose_check
        CHECK (purpose IN ('catalog_llm','standalone_llm','vendor_search','clef_workers_ai','clef_gateway'));
      ALTER TABLE public.platform_credentials ADD CONSTRAINT platform_credentials_provider_check
        CHECK (provider IN ('anthropic','openai','perplexity','cloudflare'));
      ALTER TABLE public.platform_credentials ADD CONSTRAINT credential_provider_purpose CHECK (
        (purpose='vendor_search' AND provider='perplexity' AND endpoint='https://api.perplexity.ai')
        OR (purpose IN ('catalog_llm','standalone_llm') AND provider IN ('anthropic','openai'))
        OR (purpose='clef_workers_ai' AND provider='cloudflare' AND endpoint='https://gateway.ai.cloudflare.com')
        OR (purpose='clef_gateway' AND provider='cloudflare' AND endpoint='https://api.cloudflare.com/client/v4'));
      ALTER TABLE public.platform_models ADD COLUMN clef_settings jsonb;
      ALTER TABLE public.platform_models DROP CONSTRAINT platform_models_capability_check;
      ALTER TABLE public.platform_models DROP CONSTRAINT platform_models_provider_check;
      ALTER TABLE public.platform_models ADD CONSTRAINT platform_models_capability_check
        CHECK (capability IN ('llm_extract','vision'));
      ALTER TABLE public.platform_models ADD CONSTRAINT platform_models_provider_check
        CHECK (provider IN ('anthropic','openai','cloudflare'));
      ALTER TABLE public.platform_models ADD CONSTRAINT platform_clef_catalog_shape CHECK (
        (id='bid-review-clef' AND capability='vision' AND provider='cloudflare'
          AND model='@cf/cloudflare/clef' AND NOT is_default AND clef_settings IS NOT NULL
          AND jsonb_typeof(clef_settings)='object')
        OR (id<>'bid-review-clef' AND capability='llm_extract' AND provider IN ('anthropic','openai')
          AND clef_settings IS NULL));
      CREATE ROLE bid_clef_config_fn NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
      GRANT USAGE ON SCHEMA public TO bid_clef_config_fn;
      GRANT SELECT,INSERT,UPDATE ON public.platform_models TO bid_clef_config_fn;
      GRANT SELECT(id,name,purpose,provider,endpoint,state,revision) ON public.platform_credentials TO bid_clef_config_fn;
      GRANT INSERT ON public.platform_audit_logs TO bid_clef_config_fn;
      CREATE FUNCTION public.platform_clef_row_gate() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      BEGIN
        IF (NEW.id='bid-review-clef' OR (TG_OP='UPDATE' AND OLD.id='bid-review-clef'))
          AND current_user<>'bid_clef_config_fn' THEN
          RAISE EXCEPTION 'Clef configuration requires platform connection' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER platform_clef_row_gate BEFORE INSERT OR UPDATE ON public.platform_models
        FOR EACH ROW EXECUTE FUNCTION public.platform_clef_row_gate();
    """)
    op.execute(CREDENTIAL_FUNCTIONS)
    op.execute(CREDENTIAL_MANAGEMENT)
    op.execute(CONFIG_FUNCTIONS)
    op.execute("GRANT CREATE ON SCHEMA public TO bid_clef_config_fn")
    for signature in ("platform_clef_read()", "platform_clef_manage(text,text,jsonb)"):
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO bid_clef_config_fn")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,bid_app,bid_platform_app,bid_credential_reader"
        )
    op.execute("GRANT EXECUTE ON FUNCTION public.platform_clef_read() TO bid_app,bid_platform_app")
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.platform_clef_manage(text,text,jsonb) TO bid_platform_app"
    )
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_clef_config_fn")


def downgrade():
    raise RuntimeError("Clef evidence and pinned pricing require an explicit archival migration")


CREDENTIAL_FUNCTIONS = r"""
        CREATE OR REPLACE FUNCTION public.platform_credential_validate(p jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF NOT COALESCE(
            (p->>'id')::uuid IS NOT NULL AND p->>'name' ~ '^[a-z0-9_]{1,40}$'
            AND p->>'purpose' IN ('catalog_llm','standalone_llm','vendor_search','clef_workers_ai','clef_gateway')
            AND p->>'provider' IN ('anthropic','openai','perplexity','cloudflare')
            AND length(p->>'endpoint') BETWEEN 8 AND 300
            AND p->>'endpoint' ~ '^https://(\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*)(/[^[\:space:]?#%\\]*)?$'
            AND p->>'endpoint' !~ '/$' AND p->>'endpoint' !~ '[[\:cntrl:]]'
            AND length(p->>'encrypted_key') BETWEEN 100 AND 16384
            AND p->>'fingerprint' ~ '^sha256:[a-f0-9]{16}$'
            AND p->>'last_four' ~ '^[!-~]{4}$'
            AND (NOT p ? 'active' OR jsonb_typeof(p->'active')='boolean')
            AND ((p->>'purpose'='vendor_search' AND p->>'provider'='perplexity'
              AND p->>'endpoint'='https://api.perplexity.ai')
              OR (p->>'purpose' IN ('catalog_llm','standalone_llm') AND p->>'provider' IN ('anthropic','openai'))
              OR (p->>'purpose'='clef_workers_ai' AND p->>'provider'='cloudflare'
                AND p->>'endpoint'='https://gateway.ai.cloudflare.com')
              OR (p->>'purpose'='clef_gateway' AND p->>'provider'='cloudflare'
                AND p->>'endpoint'='https://api.cloudflare.com/client/v4')),false)
          THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
        END $$;
        CREATE OR REPLACE FUNCTION public.platform_credential_resolve_service(p_service text,p_id uuid) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF p_service NOT IN ('vendor_search','standalone_llm','clef_workers_ai','clef_gateway') OR p_service IS NULL THEN
            RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.purpose<>p_service THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,false);
        END $$;
        CREATE OR REPLACE FUNCTION public.platform_credential_readiness(p_target jsonb) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials; m record; endpoint text;
        BEGIN
          IF p_target->>'kind'='catalog_model' THEN
            SELECT id,credential,provider,base_url,revision,enabled INTO m FROM public.platform_models
              WHERE id=p_target->>'model_id';
            IF m.id IS NULL OR m.revision IS DISTINCT FROM (p_target->>'expected_model_revision')::bigint THEN
              RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
            SELECT * INTO c FROM public.platform_credentials WHERE name=m.credential;
            endpoint := rtrim(COALESCE(NULLIF(m.base_url,''), CASE m.provider
              WHEN 'anthropic' THEN 'https://api.anthropic.com'
              WHEN 'openai' THEN 'https://api.openai.com/v1' END),'/');
            IF c.id IS NOT NULL AND (c.purpose<>'catalog_llm' OR c.provider<>m.provider OR c.endpoint<>endpoint) THEN
              RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          ELSIF p_target->>'kind'='service' AND p_target->>'service' IN ('vendor_search','standalone_llm','clef_workers_ai','clef_gateway') THEN
            IF p_target->>'credential_id' IS NOT NULL THEN
              SELECT * INTO c FROM public.platform_credentials WHERE id=(p_target->>'credential_id')::uuid;
              IF c.id IS NOT NULL AND c.purpose<>p_target->>'service' THEN
                RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
            ELSE
              SELECT * INTO c FROM public.platform_credentials
                WHERE purpose=p_target->>'service' AND state<>'removed';
            END IF;
          ELSE RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          RETURN jsonb_build_object('credential_id',c.id,'provider',c.provider,'endpoint',c.endpoint,
            'revision',c.revision,'state',c.state,'configured',COALESCE(c.state='active',false));
        END $$;
        CREATE OR REPLACE FUNCTION public.platform_credential_catalog_gate() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials; endpoint text;
        BEGIN
          IF NEW.id='bid-review-clef' THEN
            SELECT * INTO c FROM public.platform_credentials WHERE name=NEW.credential;
            IF c.id IS NULL OR c.purpose<>'clef_workers_ai' OR c.provider<>'cloudflare'
              OR c.endpoint<>'https://gateway.ai.cloudflare.com' THEN
              RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END IF;
          -- Disabling a broken consumer is always possible; no binding can change here.
          IF TG_OP='UPDATE' AND NOT NEW.enabled AND OLD.enabled
            AND (to_jsonb(NEW)-ARRAY['enabled','is_default','revision','updated_at','updated_by'])
              =(to_jsonb(OLD)-ARRAY['enabled','is_default','revision','updated_at','updated_by']) THEN
            RETURN NEW;
          END IF;
          endpoint := rtrim(COALESCE(NULLIF(NEW.base_url,''), CASE NEW.provider
            WHEN 'anthropic' THEN 'https://api.anthropic.com'
            WHEN 'openai' THEN 'https://api.openai.com/v1' END),'/');
          SELECT * INTO c FROM public.platform_credentials WHERE name=NEW.credential FOR SHARE;
          IF c.id IS NULL OR c.purpose<>'catalog_llm' OR c.provider<>NEW.provider
             OR c.endpoint<>endpoint OR c.state<>'active' THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
"""

CREDENTIAL_MANAGEMENT = r"""
CREATE OR REPLACE FUNCTION public.platform_credential_manage(p_action text,p_actor text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  c public.platform_credentials; prior public.platform_credentials;
  v_id uuid; v_probe uuid; v_reason text; v_state text; v_action text;
  v_limit integer; v_items jsonb := '[]'; v_entry jsonb; v_created integer := 0;
  v_skipped integer := 0; v_dry boolean := false; v_audit record;
  v_details jsonb; v_outcome text; v_duration bigint; v_next text;
BEGIN
  IF p_actor IS NULL OR length(p_actor)>254 OR p_actor !~ '^[^@[\:space:]]+@[^@[\:space:]]+$'
    OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF p_action='list' THEN
    v_limit := COALESCE((p_body->>'limit')::integer,100);
    IF v_limit NOT BETWEEN 1 AND 100 OR (p_body->>'state' IS NOT NULL AND
      p_body->>'state' NOT IN ('active','disabled','removed')) OR (p_body->>'purpose' IS NOT NULL AND
      p_body->>'purpose' NOT IN ('catalog_llm','standalone_llm','vendor_search','clef_workers_ai','clef_gateway')) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(public.platform_credential_view(s.id) ORDER BY s.name),'[]') INTO v_items
      FROM (SELECT id,name FROM public.platform_credentials
        WHERE (p_body->>'state' IS NULL OR state=p_body->>'state')
          AND (p_body->>'purpose' IS NULL OR purpose=p_body->>'purpose')
          AND (p_body->>'after_name' IS NULL OR name>p_body->>'after_name')
        ORDER BY name LIMIT v_limit) s;
    IF jsonb_array_length(v_items)=v_limit AND EXISTS(SELECT FROM public.platform_credentials
      WHERE name>v_items->(v_limit-1)->>'name'
        AND (p_body->>'state' IS NULL OR state=p_body->>'state')
        AND (p_body->>'purpose' IS NULL OR purpose=p_body->>'purpose')) THEN
      v_next := v_items->(v_limit-1)->>'name'; END IF;
    PERFORM public.platform_credential_audit('credential.read',p_actor,NULL,'success','{}');
    RETURN jsonb_build_object('items',v_items,'next_after_name',v_next);
  ELSIF p_action='show' THEN
    v_id := (p_body->>'id')::uuid;
    IF NOT EXISTS(SELECT FROM public.platform_credentials WHERE id=v_id) THEN
      RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
    PERFORM public.platform_credential_audit('credential.read',p_actor,v_id,'success',
      jsonb_build_object('credential_id',v_id));
    RETURN jsonb_build_object('credential',public.platform_credential_view(v_id));
  ELSIF p_action='audit' THEN
    -- Rejections are committed by the caller in a separate transaction. Never accept arbitrary details.
    IF p_body->>'action' IS NULL OR p_body->>'outcome' IS NULL OR p_body->>'action' NOT IN ('create','replace','set_active','remove','import','read','probe_start','probe_finish')
      OR p_body->>'outcome' NOT IN ('denied','failed') OR p_body->>'error_code' IS NULL
      OR p_body->>'error_code' NOT IN ('invalid_input','invalid_session','not_found','revision_conflict',
        'credential_name_conflict','credential_purpose_conflict','credential_import_conflict',
        'credential_reference_mismatch','credential_missing','credential_disabled','credential_removed',
        'credential_unreadable','credential_backend_unavailable','credential_audit_unavailable',
        'credential_env_forbidden','credential_probe_auth_failed','credential_probe_unsupported',
        'credential_probe_timeout','credential_probe_rate_limited','credential_probe_unavailable',
        'credential_probe_interrupted','credential_probe_endpoint_rejected') THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    SELECT id INTO v_id FROM public.platform_credentials WHERE id=(p_body->>'id')::uuid;
    PERFORM public.platform_credential_audit('credential.'||(p_body->>'action'),p_actor,v_id,
      p_body->>'outcome',jsonb_build_object('credential_id',v_id,'error_code',p_body->>'error_code'));
    RETURN '{}'::jsonb;
  ELSIF p_action='import' THEN
    IF jsonb_typeof(p_body->'entries') IS DISTINCT FROM 'array'
      OR jsonb_array_length(p_body->'entries') NOT BETWEEN 1 AND 100 THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    v_dry := COALESCE((p_body->>'dry_run')::boolean,false);
    IF COALESCE((p_body->>'preflight')::boolean,false) THEN
      FOR v_entry IN SELECT value FROM jsonb_array_elements(p_body->'entries') LOOP
        SELECT * INTO c FROM public.platform_credentials WHERE name=v_entry->>'name';
        v_items := v_items || jsonb_build_array(jsonb_build_object('name',v_entry->>'name',
          'credential',public.platform_credential_view(c.id)));
      END LOOP;
      RETURN jsonb_build_object('items',v_items);
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('platform-credential-import',0));
    IF (SELECT count(*)<>count(DISTINCT value->>'name') FROM jsonb_array_elements(p_body->'entries')) THEN
      RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT value->>'purpose' FROM jsonb_array_elements(p_body->'entries')
      WHERE value->>'purpose'<>'catalog_llm' GROUP BY value->>'purpose' HAVING count(*)>1) THEN
      RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
    FOR v_entry IN SELECT value FROM jsonb_array_elements(p_body->'entries') LOOP
      SELECT * INTO c FROM public.platform_credentials WHERE name=v_entry->>'name' FOR UPDATE;
      IF EXISTS(SELECT FROM public.platform_models m WHERE m.credential=v_entry->>'name' AND (
        v_entry->>'purpose'<>'catalog_llm' OR m.provider<>v_entry->>'provider'
        OR rtrim(COALESCE(NULLIF(m.base_url,''),CASE m.provider
          WHEN 'anthropic' THEN 'https://api.anthropic.com'
          WHEN 'openai' THEN 'https://api.openai.com/v1' END),'/')<>v_entry->>'endpoint')) THEN
        RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
      IF c.id IS NOT NULL THEN
        IF c.state='removed' OR c.id IS DISTINCT FROM (v_entry->>'id')::uuid
          OR c.revision IS DISTINCT FROM COALESCE((v_entry->>'checked_revision')::bigint,
                                                 (v_entry->>'expected_revision')::bigint)
          OR NOT COALESCE((v_entry->>'same_secret')::boolean,(v_entry->>'same_value')::boolean,false)
          OR c.purpose IS DISTINCT FROM v_entry->>'purpose' OR c.provider IS DISTINCT FROM v_entry->>'provider'
          OR c.endpoint IS DISTINCT FROM v_entry->>'endpoint'
          OR (c.state='active') IS DISTINCT FROM COALESCE((v_entry->>'active')::boolean,false) THEN
          RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
        v_skipped := v_skipped+1;
        v_action := CASE WHEN v_dry THEN 'would_skip' ELSE 'skipped' END;
        IF NOT v_dry THEN PERFORM public.platform_credential_audit('credential.import',p_actor,c.id,
          'success',jsonb_build_object('credential_id',c.id,'credential_name',c.name,
            'new_revision',c.revision,'reason','migration')); END IF;
      ELSE
        IF v_dry THEN
          PERFORM public.platform_credential_validate(v_entry);
          IF v_entry->>'purpose'<>'catalog_llm' AND EXISTS(SELECT FROM public.platform_credentials
            WHERE purpose=v_entry->>'purpose' AND state<>'removed') THEN
            RAISE EXCEPTION 'credential_import_conflict' USING ERRCODE='P0001'; END IF;
          v_action := 'would_create';
        ELSE
          v_details := public.platform_credential_manage('create',p_actor,v_entry||'{"reason":"migration"}');
          v_action := 'created';
          PERFORM public.platform_credential_audit('credential.import',p_actor,(v_entry->>'id')::uuid,
            'success',jsonb_build_object('credential_id',v_entry->>'id','credential_name',v_entry->>'name',
              'new_revision',1,'reason','migration'));
        END IF;
        v_created := v_created+1;
      END IF;
      v_items := v_items || jsonb_build_array(jsonb_build_object('name',v_entry->>'name',
        'action',v_action,'credential_id',CASE WHEN v_dry AND c.id IS NULL THEN NULL
          ELSE (v_entry->>'id')::uuid END));
    END LOOP;
    RETURN jsonb_build_object('items',v_items,'dry_run',v_dry,'created',CASE WHEN v_dry THEN 0 ELSE v_created END,
      'skipped',v_skipped,'would_create',CASE WHEN v_dry THEN v_created ELSE 0 END);
  ELSIF p_action='create' THEN
    PERFORM public.platform_credential_validate(p_body);
    PERFORM pg_advisory_xact_lock(hashtextextended('platform-credential-import',0));
    v_id := (p_body->>'id')::uuid;
    v_reason := COALESCE(p_body->>'reason','setup');
    IF v_reason NOT IN ('setup','scheduled_rotation','vendor_revoked','incident','retired','migration') THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT FROM public.platform_credentials WHERE name=p_body->>'name') THEN
      RAISE EXCEPTION 'credential_name_conflict' USING ERRCODE='P0001'; END IF;
    IF p_body->>'purpose'<>'catalog_llm' AND EXISTS(SELECT FROM public.platform_credentials
      WHERE purpose=p_body->>'purpose' AND state<>'removed') THEN
      RAISE EXCEPTION 'credential_purpose_conflict' USING ERRCODE='P0001'; END IF;
    INSERT INTO public.platform_credentials(id,name,purpose,provider,endpoint,encrypted_key,
      fingerprint,last_four,state,updated_by) VALUES(v_id,p_body->>'name',p_body->>'purpose',
      p_body->>'provider',p_body->>'endpoint',p_body->>'encrypted_key',p_body->>'fingerprint',
      p_body->>'last_four',CASE WHEN COALESCE((p_body->>'active')::boolean,false)
        THEN 'active' ELSE 'disabled' END,p_actor) RETURNING * INTO c;
    PERFORM public.platform_credential_audit('credential.create',p_actor,c.id,'success',
      jsonb_build_object('credential_id',c.id,'credential_name',c.name,'new_revision',1,
        'secret_version',1,'new_state',c.state,'reason',v_reason));
    RETURN jsonb_build_object('credential',public.platform_credential_view(c.id));
  ELSIF p_action='probe_finish' THEN
    v_probe := (p_body->>'probe_id')::uuid;
    PERFORM pg_advisory_xact_lock(hashtextextended('credential-probe:'||v_probe::text,0));
    SELECT object_id,details,created_at,actor_email INTO v_audit FROM public.platform_audit_logs
      WHERE action='credential.probe_start' AND outcome='success' AND details->>'probe_id'=v_probe::text;
    IF v_audit.object_id IS NULL OR v_audit.actor_email<>p_actor OR EXISTS(SELECT FROM public.platform_audit_logs
      WHERE action='credential.probe_finish' AND details->>'probe_id'=v_probe::text) THEN
      RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
    v_outcome := p_body->>'outcome';
    v_duration := (p_body->>'duration_ms')::bigint;
    IF v_outcome IS NULL OR v_outcome NOT IN ('passed','auth_failed','unsupported','timeout',
      'rate_limited','unavailable','interrupted') OR v_duration IS NULL OR v_duration<0
      OR COALESCE((p_body->>'retry_after_seconds')::integer,0) NOT BETWEEN 0 AND 3600 THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    IF v_audit.created_at<clock_timestamp()-interval '30 seconds' THEN
      RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
    v_id := v_audit.object_id::uuid;
    v_details := v_audit.details || jsonb_build_object('outcome',v_outcome);
    PERFORM public.platform_credential_audit('credential.probe_finish',p_actor,v_id,
      CASE WHEN v_outcome='passed' THEN 'success' ELSE 'failed' END,v_details);
    RETURN jsonb_build_object('probe',jsonb_build_object('probe_id',v_probe,'credential_id',v_id,
      'tested_revision',(v_details->>'new_revision')::bigint,
      'secret_version',(v_details->>'secret_version')::bigint,'outcome',v_outcome,
      'duration_ms',v_duration,'checked_at',clock_timestamp(),'proves','authentication_only',
      'retry_after_seconds',(p_body->>'retry_after_seconds')::integer));
  ELSIF p_action NOT IN ('replace','set_active','remove','probe_begin') OR p_action IS NULL THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
  END IF;
  v_id := (p_body->>'id')::uuid;
  SELECT * INTO c FROM public.platform_credentials WHERE id=v_id FOR UPDATE;
  IF c.id IS NULL THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  IF c.revision IS DISTINCT FROM (p_body->>'expected_revision')::bigint THEN
    RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
  IF c.state='removed' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='P0001'; END IF;
  prior := c;
  IF p_action='probe_begin' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('credential-probe-actor:'||p_actor,0));
    IF (SELECT count(*) FROM public.platform_audit_logs WHERE action='credential.probe_start'
      AND outcome='success' AND actor_email=p_actor AND created_at>clock_timestamp()-interval '1 minute')>=10
      OR (SELECT count(*) FROM public.platform_audit_logs WHERE action='credential.probe_start'
      AND outcome='success' AND object_id=c.id::text AND created_at>clock_timestamp()-interval '1 minute')>=5 THEN
      RAISE EXCEPTION 'credential_probe_rate_limited' USING ERRCODE='P0001'; END IF;
    v_probe := gen_random_uuid();
    PERFORM public.platform_credential_audit('credential.probe_start',p_actor,c.id,'success',
      jsonb_build_object('credential_id',c.id,'new_revision',c.revision,
        'secret_version',c.secret_version,'probe_id',v_probe));
    RETURN jsonb_build_object('probe_id',v_probe,'credential_id',c.id,
      'tested_revision',c.revision,'secret_version',c.secret_version);
  END IF;
  v_reason := p_body->>'reason';
  IF v_reason IS NULL OR v_reason NOT IN ('setup','scheduled_rotation','vendor_revoked','incident','retired','migration')
    OR (p_action='remove' AND v_reason NOT IN ('vendor_revoked','incident','retired')) THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF p_action='replace' THEN
    UPDATE public.platform_credentials SET encrypted_key=p_body->>'encrypted_key',
      fingerprint=p_body->>'fingerprint',last_four=p_body->>'last_four',
      revision=revision+1,secret_version=secret_version+1,updated_at=clock_timestamp(),updated_by=p_actor
      WHERE id=v_id RETURNING * INTO c;
  ELSIF p_action='set_active' THEN
    IF jsonb_typeof(p_body->'active') IS DISTINCT FROM 'boolean' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    v_state := CASE WHEN (p_body->>'active')::boolean THEN 'active' ELSE 'disabled' END;
    IF v_state='active' AND c.revision IS DISTINCT FROM (p_body->>'checked_revision')::bigint THEN
      RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
    IF c.state<>v_state THEN
      UPDATE public.platform_credentials SET state=v_state,revision=revision+1,
        updated_at=clock_timestamp(),updated_by=p_actor WHERE id=v_id RETURNING * INTO c;
    END IF;
  ELSE
    UPDATE public.platform_credentials SET state='removed',encrypted_key=NULL,revision=revision+1,
      updated_at=clock_timestamp(),updated_by=p_actor WHERE id=v_id RETURNING * INTO c;
  END IF;
  PERFORM public.platform_credential_audit('credential.'||p_action,p_actor,c.id,'success',
    jsonb_build_object('credential_id',c.id,'credential_name',c.name,'old_revision',prior.revision,
      'new_revision',c.revision,'secret_version',c.secret_version,'old_state',prior.state,
      'new_state',c.state,'reason',v_reason));
  RETURN jsonb_build_object('credential',public.platform_credential_view(c.id));
EXCEPTION WHEN invalid_text_representation OR not_null_violation OR check_violation
  OR string_data_right_truncation OR numeric_value_out_of_range THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
WHEN unique_violation THEN
  RAISE EXCEPTION 'credential_name_conflict' USING ERRCODE='P0001';
END $$;
"""

CONFIG_FUNCTIONS = r"""
CREATE FUNCTION public.platform_clef_read() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
  SELECT clef_settings FROM public.platform_models WHERE id='bid-review-clef'
$$;

CREATE FUNCTION public.platform_clef_manage(p_action text,p_actor text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_prior jsonb; v_new jsonb; v_revision bigint; v_name text;
  v_workers record; v_gateway record; v_credential record;
BEGIN
  IF p_actor IS NULL OR p_actor !~ '^[^@[\:space:]]+@[^@[\:space:]]+$'
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
    IF p_body-ARRAY['expected_revision','account_id','gateway_id','enabled','price_revision',
        'fixed_sale_price','currency','workers_credential_id','gateway_credential_id'] <> '{}'::jsonb
      OR NOT COALESCE(p_body->>'account_id' ~ '^[a-f0-9]{32}$'
        AND p_body->>'gateway_id' ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
        AND jsonb_typeof(p_body->'enabled')='boolean'
        AND (p_body->>'price_revision')::bigint>=1
        AND (p_body->>'fixed_sale_price')::numeric>=0
        AND (p_body->>'fixed_sale_price')::numeric<=9999999999.99999999
        AND scale((p_body->>'fixed_sale_price')::numeric)<=8
        AND (p_body->>'currency') ~ '^[A-Z]{3}$'
        AND p_body->>'workers_credential_id'<>p_body->>'gateway_credential_id',false) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    IF v_prior IS NOT NULL AND ((p_body->>'price_revision')::bigint<(v_prior->>'price_revision')::bigint
      OR ((p_body->>'price_revision')::bigint=(v_prior->>'price_revision')::bigint
        AND ((p_body->>'fixed_sale_price')::numeric IS DISTINCT FROM (v_prior->>'fixed_sale_price')::numeric
          OR p_body->>'currency' IS DISTINCT FROM v_prior->>'currency'))) THEN
      RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001';
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
      AND p_body->'gateway_check'->>'workers_ai_billing_mode'='unified'
      AND (p_body->'gateway_check'->>'checked_at')::timestamptz<=clock_timestamp(),false) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
    v_new := v_prior || jsonb_build_object('revision',v_revision,
      'gateway_check',p_body->'gateway_check','updated_at',clock_timestamp());
  END IF;
  INSERT INTO public.platform_models(id,capability,provider,model,base_url,credential,
    vendor_input_usd_per_mtok,vendor_output_usd_per_mtok,sale_input_per_mtok,sale_output_per_mtok,
    enabled,is_default,revision,updated_by,clef_settings)
  VALUES('bid-review-clef','vision','cloudflare','@cf/cloudflare/clef','https://gateway.ai.cloudflare.com',
    v_name,0,0,0,0,(v_new->>'enabled')::boolean,false,v_revision,p_actor,v_new)
  ON CONFLICT(id) DO UPDATE SET credential=EXCLUDED.credential,enabled=EXCLUDED.enabled,
    revision=EXCLUDED.revision,updated_by=p_actor,updated_at=clock_timestamp(),clef_settings=v_new;
  INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details)
  VALUES(gen_random_uuid(),p_actor,'platform.clef.'||p_action,'bid-review-clef',
    (CASE WHEN p_action='check' AND p_body->'gateway_check'='null'::jsonb THEN 'failed' ELSE 'success' END),
    jsonb_strip_nulls(jsonb_build_object('revision',v_revision,'price_revision',v_new->'price_revision',
      'enabled',v_new->'enabled','error_code',p_body->'check_error')));
  RETURN v_new;
END $$;
"""


PRESENCE_TABLES = (
    "bid_presence_preparations",
    "bid_presence_images",
    "bid_presence_authorizations",
    "bid_presence_authorized_images",
)

PRESENCE_SQL = (
    r"""
CREATE TABLE bid_presence_preparations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	source_review_id UUID NOT NULL,
	created_by UUID NOT NULL,
	manifest_sha256 VARCHAR(64) NOT NULL,
	expected_page_ids JSONB NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, source_review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, task_id, submission_id, id),
	UNIQUE (org_id, submission_id, manifest_sha256),
	CHECK (jsonb_typeof(expected_page_ids)='array' AND jsonb_array_length(expected_page_ids) BETWEEN 1 AND 40),
	CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$' AND length(details_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE bid_presence_images (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	presence_preparation_id UUID NOT NULL,
	page_id UUID NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	size_bytes INTEGER NOT NULL,
	width_px INTEGER NOT NULL,
	height_px INTEGER NOT NULL,
	blur JSONB NOT NULL,
	privacy_receipt_sha256 VARCHAR(64) NOT NULL,
	storage_key TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, presence_preparation_id) REFERENCES bid_presence_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	UNIQUE (org_id, presence_preparation_id, page_id),
	UNIQUE (org_id, task_id, submission_id, presence_preparation_id, id),
	CHECK (sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$' AND privacy_receipt_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (size_bytes BETWEEN 1 AND 4194304 AND width_px BETWEEN 1 AND 8192 AND height_px BETWEEN 1 AND 8192 AND width_px*height_px<=16000000),
	CHECK (storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text || '/presence/' || presence_preparation_id::text || '/' || sha256 || '.jpg'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE bid_presence_authorizations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	presence_preparation_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	prior_authorization_id UUID,
	request_id UUID NOT NULL,
	authorized_by UUID NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	manifest_sha256 VARCHAR(64) NOT NULL,
	image_ids JSONB NOT NULL,
	allow_external BOOLEAN NOT NULL,
	reason_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id) REFERENCES bid_submissions (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, presence_preparation_id) REFERENCES bid_presence_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, prior_authorization_id) REFERENCES bid_presence_authorizations (org_id, task_id, id),
	FOREIGN KEY(org_id, authorized_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, submission_id, revision),
	UNIQUE (org_id, authorized_by, request_id),
	UNIQUE (org_id, task_id, submission_id, presence_preparation_id, id),
	CHECK (jsonb_typeof(image_ids)='array' AND jsonb_array_length(image_ids) BETWEEN 1 AND 40),
	CHECK (revision>0 AND payload_hash ~ '^[0-9a-f]{64}$' AND manifest_sha256 ~ '^[0-9a-f]{64}$' AND length(reason_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE bid_presence_authorized_images (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	presence_preparation_id UUID NOT NULL,
	authorization_id UUID NOT NULL,
	image_id UUID NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, presence_preparation_id, authorization_id) REFERENCES bid_presence_authorizations (org_id, task_id, submission_id, presence_preparation_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, presence_preparation_id, image_id) REFERENCES bid_presence_images (org_id, task_id, submission_id, presence_preparation_id, id),
	UNIQUE (org_id, authorization_id, image_id),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
)

PRESENCE_GUARDS = r"""
CREATE FUNCTION public.bid_presence_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND TG_TABLE_NAME IN ('bid_presence_preparations','bid_presence_authorizations')
 AND (to_jsonb(NEW)-'details_encrypted'-'reason_encrypted')=(to_jsonb(OLD)-'details_encrypted'-'reason_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Presence images and authorization history are immutable' USING ERRCODE='23514';
END $$;
CREATE FUNCTION public.bid_presence_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE prepared public.bid_presence_preparations%ROWTYPE;
 granted public.bid_presence_authorizations%ROWTYPE;
 source_page public.bid_document_pages%ROWTYPE;
 prior_grant public.bid_presence_authorizations%ROWTYPE;
 human_id uuid; permission_name text := 'bid-review' || chr(58) || 'outbound' || chr(58) || 'authorize';
BEGIN
 IF TG_TABLE_NAME='bid_presence_preparations' THEN prepared := NEW; human_id := NEW.created_by;
 ELSIF TG_TABLE_NAME='bid_presence_authorizations' THEN
  granted := NEW; human_id := NEW.authorized_by;
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE org_id=NEW.org_id AND id=NEW.presence_preparation_id;
 ELSIF TG_TABLE_NAME='bid_presence_images' THEN
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE org_id=NEW.org_id AND id=NEW.presence_preparation_id;
  human_id := prepared.created_by;
 ELSE
  SELECT * INTO granted FROM public.bid_presence_authorizations WHERE org_id=NEW.org_id AND id=NEW.authorization_id;
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE org_id=NEW.org_id AND id=NEW.presence_preparation_id;
  human_id := granted.authorized_by;
 END IF;
 IF prepared.id IS NULL OR human_id IS NULL
 OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NULLIF(current_setting('app.actor_user_id',true),'')::uuid IS DISTINCT FROM human_id
 OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? permission_name)
 OR NOT public.bid_review_actor_live(NEW.org_id,NEW.task_id,human_id)
 OR NOT EXISTS(SELECT 1 FROM public.task_members WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND user_id=human_id AND active AND role='owner')
 OR NOT EXISTS(SELECT 1 FROM public.bid_review_publications WHERE org_id=NEW.org_id AND review_id=prepared.source_review_id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id AND preparation_id=prepared.preparation_id)
 THEN RAISE EXCEPTION 'Presence disclosure requires current human task owner' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_presence_images' THEN
  SELECT * INTO source_page FROM public.bid_document_pages WHERE org_id=NEW.org_id AND id=NEW.page_id;
  IF NOT (prepared.expected_page_ids ? NEW.page_id::text)
  OR source_page.role IS DISTINCT FROM 'bid' OR source_page.preparation_id IS DISTINCT FROM prepared.preparation_id
  OR source_page.image->>'sha256' IS DISTINCT FROM NEW.source_sha256
  OR NOT EXISTS(SELECT 1 FROM public.bid_review_required_locations loc
    JOIN public.bid_review_signing_requirements req ON req.org_id=loc.org_id AND req.id=loc.requirement_id
    WHERE loc.org_id=NEW.org_id AND loc.review_id=prepared.source_review_id AND loc.page_id=NEW.page_id AND req.applicability='applies')
  OR NOT EXISTS(SELECT 1 FROM public.bid_redacted_pages redacted WHERE redacted.org_id=NEW.org_id AND redacted.page_id=NEW.page_id AND redacted.classification='non_price' AND NOT redacted.price_page)
  THEN RAISE EXCEPTION 'Presence image requires non-price confirmed required page' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_presence_authorizations' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text || '/' || NEW.submission_id::text || '/presence',0));
  SELECT * INTO prior_grant FROM public.bid_presence_authorizations WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>COALESCE(prior_grant.revision,0)+1
  OR NEW.prior_authorization_id IS DISTINCT FROM prior_grant.id
  OR NEW.manifest_sha256 IS DISTINCT FROM prepared.manifest_sha256
  OR (NOT NEW.allow_external AND (prior_grant.id IS NULL OR prior_grant.presence_preparation_id IS DISTINCT FROM NEW.presence_preparation_id))
  THEN RAISE EXCEPTION 'Presence grant predecessor or manifest mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_presence_authorized_images' THEN
  IF NOT (granted.image_ids ? NEW.image_id::text) THEN
   RAISE EXCEPTION 'Presence image was not in the fixed human grant' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bid_presence_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE actual_ids jsonb; expected_ids jsonb; parent_id uuid;
BEGIN
 IF TG_TABLE_NAME IN ('bid_presence_preparations','bid_presence_images') THEN
  IF TG_TABLE_NAME='bid_presence_preparations' THEN parent_id := NEW.id; ELSE parent_id := NEW.presence_preparation_id; END IF;
  SELECT expected_page_ids INTO expected_ids FROM public.bid_presence_preparations WHERE org_id=NEW.org_id AND id=parent_id;
  SELECT COALESCE(jsonb_agg(page_id::text ORDER BY page_id::text),'[]'::jsonb) INTO actual_ids FROM public.bid_presence_images WHERE org_id=NEW.org_id AND presence_preparation_id=parent_id;
 ELSE
  IF TG_TABLE_NAME='bid_presence_authorizations' THEN parent_id := NEW.id; ELSE parent_id := NEW.authorization_id; END IF;
  SELECT image_ids INTO expected_ids FROM public.bid_presence_authorizations WHERE org_id=NEW.org_id AND id=parent_id;
  SELECT COALESCE(jsonb_agg(image_id::text ORDER BY image_id::text),'[]'::jsonb) INTO actual_ids FROM public.bid_presence_authorized_images WHERE org_id=NEW.org_id AND authorization_id=parent_id;
 END IF;
 IF actual_ids IS DISTINCT FROM expected_ids OR jsonb_array_length(actual_ids) NOT BETWEEN 1 AND 40 THEN
  RAISE EXCEPTION 'Presence scope must exactly match its fixed inventory' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
"""


def presence_upgrade():
    op.execute(
        "ALTER TABLE usage_records ADD COLUMN gateway_request_id VARCHAR(128), ADD COLUMN gateway_trace_id VARCHAR(128)"
    )
    op.execute(
        "ALTER TABLE usage_records ADD CONSTRAINT usage_gateway_telemetry CHECK ((gateway_request_id IS NULL OR gateway_request_id ~ '^[A-Za-z0-9_-]{1,128}$') AND (gateway_trace_id IS NULL OR gateway_trace_id ~ '^[A-Za-z0-9_-]{1,128}$'))"
    )
    op.execute(
        "ALTER TABLE bid_review_required_locations DROP CONSTRAINT bid_review_required_locations_check"
    )
    op.execute(
        "ALTER TABLE bid_review_required_locations ADD CONSTRAINT bid_review_required_locations_check CHECK (ordinal BETWEEN 1 AND 10000 AND status IN ('unresolved','triage_present','triage_absent','triage_uncertain'))"
    )
    for statement in PRESENCE_SQL:
        op.execute(statement)
    for table in PRESENCE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy = "org_id = NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO bid_app")
        op.execute(f"CREATE INDEX ix_{table}_org_id ON {table}(org_id)")
    op.execute(PRESENCE_GUARDS)
    for table in PRESENCE_TABLES:
        op.execute(
            f"CREATE TRIGGER bid_presence_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.bid_presence_immutable()"
        )
        op.execute(
            f"CREATE TRIGGER bid_presence_insert_guard BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.bid_presence_insert_guard()"
        )
    for table in PRESENCE_TABLES:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER bid_presence_complete AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_presence_complete()"
        )
