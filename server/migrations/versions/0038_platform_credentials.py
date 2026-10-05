"""Bound platform credentials, separate runtime roles and owner-only secret rewrapping.

The migration deliberately leaves legacy catalog references unvalidated. Import checks
identity and endpoint before cutover; new catalog writes must bind an active credential.
No root encryption key or historical credential value is stored in PostgreSQL.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

MANAGE = "platform_credential_manage(text, text, jsonb)"
READERS = (
    "platform_credential_readiness(jsonb)",
    "platform_credential_resolve_catalog(text, bigint)",
    "platform_credential_resolve_service(text, uuid)",
    "platform_credential_resolve_probe(uuid)",
    "platform_credential_resolve_operator_check(uuid, bigint, text)",
)
INTERNAL = (
    "platform_credential_view(uuid)",
    "platform_credential_validate(jsonb)",
    "platform_credential_cipher(uuid, boolean)",
    "platform_credential_audit(text, text, uuid, text, jsonb)",
    "platform_credential_catalog_gate()",
)


def upgrade():
    op.execute(r"""
        -- Preserve an unsent admission without widening the runtime DELETE grant.
        ALTER TABLE public.vendor_calls DROP CONSTRAINT vendor_calls_state_check;
        ALTER TABLE public.vendor_calls ADD CONSTRAINT vendor_calls_state_check
          CHECK (state IN ('pending','completed','unknown','not_sent'));
        ALTER TABLE public.vendor_calls ADD CONSTRAINT vendor_calls_not_sent_no_charge
          CHECK (state<>'not_sent' OR (reserved_charge=0 AND charge IS NULL));
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='bid_platform_credentials_fn') THEN
            CREATE ROLE bid_platform_credentials_fn NOLOGIN;
          END IF;
          IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='bid_platform_app') THEN
            CREATE ROLE bid_platform_app LOGIN;
          END IF;
          IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='bid_credential_reader') THEN
            CREATE ROLE bid_credential_reader LOGIN;
          END IF;
        END $$;
        ALTER ROLE bid_platform_credentials_fn NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
        ALTER ROLE bid_platform_app LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
        ALTER ROLE bid_credential_reader LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
        REVOKE bid_platform_credentials_fn FROM bid_app, bid_platform_fn, bid_platform_app, bid_credential_reader;
        REVOKE bid_platform_app, bid_credential_reader FROM bid_app, bid_platform_fn;
        REVOKE CREATE ON SCHEMA public FROM PUBLIC, bid_platform_credentials_fn, bid_platform_app, bid_credential_reader;
        GRANT USAGE ON SCHEMA public TO bid_platform_credentials_fn, bid_platform_app, bid_credential_reader;
        -- Preexisting role memberships would silently enlarge the boundary; fail closed.
        DO $$ BEGIN
          IF EXISTS (
            SELECT FROM pg_catalog.pg_auth_members m
            JOIN pg_catalog.pg_roles r ON r.oid=m.member OR r.oid=m.roleid
            WHERE r.rolname IN ('bid_platform_credentials_fn','bid_platform_app','bid_credential_reader')
          ) THEN RAISE EXCEPTION 'credential roles must have no inherited memberships'; END IF;
        END $$;
        CREATE TABLE public.platform_credentials (
          id uuid PRIMARY KEY,
          name varchar(40) NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9_]{1,40}$'),
          purpose varchar(30) NOT NULL CHECK (purpose IN ('catalog_llm','standalone_llm','vendor_search')),
          provider varchar(20) NOT NULL CHECK (provider IN ('anthropic','openai','perplexity')),
          endpoint varchar(300) NOT NULL CHECK (
            endpoint ~ '^https://(\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*)(/[^[:space:]?#%\\]*)?$'
            AND endpoint !~ '/$' AND endpoint !~ '[[:cntrl:]]'),
          encrypted_key text,
          envelope_version integer NOT NULL DEFAULT 1 CHECK (envelope_version=1),
          fingerprint varchar(23) NOT NULL CHECK (fingerprint ~ '^sha256:[a-f0-9]{16}$'),
          last_four varchar(4) NOT NULL CHECK (last_four ~ '^[!-~]{4}$'),
          state varchar(10) NOT NULL DEFAULT 'disabled' CHECK (state IN ('active','disabled','removed')),
          revision bigint NOT NULL DEFAULT 1 CHECK (revision>=1),
          secret_version bigint NOT NULL DEFAULT 1 CHECK (secret_version>=1 AND secret_version<=revision),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by varchar(254) NOT NULL,
          CONSTRAINT credential_cipher_state CHECK (
            (state='removed' AND encrypted_key IS NULL)
            OR (state<>'removed' AND encrypted_key IS NOT NULL
              AND length(encrypted_key) BETWEEN 100 AND 16384)),
          CONSTRAINT credential_provider_purpose CHECK (
            (purpose='vendor_search' AND provider='perplexity' AND endpoint='https://api.perplexity.ai')
            OR (purpose<>'vendor_search' AND provider IN ('anthropic','openai')))
        );
        CREATE UNIQUE INDEX platform_credentials_one_service ON public.platform_credentials(purpose)
          WHERE purpose<>'catalog_llm' AND state<>'removed';
        REVOKE ALL ON public.platform_credentials FROM PUBLIC, bid_app, bid_platform_fn,
          bid_platform_app, bid_credential_reader;
        GRANT SELECT, INSERT, UPDATE ON public.platform_credentials TO bid_platform_credentials_fn;
        REVOKE ALL ON public.platform_models,public.platform_audit_logs FROM bid_platform_credentials_fn;
        GRANT SELECT (id,credential,provider,base_url,revision,enabled,is_default)
          ON public.platform_models TO bid_platform_credentials_fn;
        GRANT INSERT ON public.platform_audit_logs TO bid_platform_credentials_fn;
        GRANT SELECT (id,created_at,actor_email,action,object_id,outcome,details)
          ON public.platform_audit_logs TO bid_platform_credentials_fn;
        ALTER TABLE public.platform_models ADD CONSTRAINT platform_model_credential_fk
          FOREIGN KEY (credential) REFERENCES public.platform_credentials(name) NOT VALID;
        CREATE INDEX platform_audit_credential_probe ON public.platform_audit_logs
          (object_id,created_at DESC) WHERE action='credential.probe_start';
        CREATE UNIQUE INDEX platform_audit_probe_finish ON public.platform_audit_logs
          ((details->>'probe_id')) WHERE action='credential.probe_finish';

        CREATE FUNCTION public.platform_credential_row_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='42501'; END IF;
          IF TG_OP='UPDATE' AND (
            NEW.id IS DISTINCT FROM OLD.id OR NEW.name IS DISTINCT FROM OLD.name
            OR NEW.purpose IS DISTINCT FROM OLD.purpose OR NEW.provider IS DISTINCT FROM OLD.provider
            OR NEW.endpoint IS DISTINCT FROM OLD.endpoint OR NEW.created_at IS DISTINCT FROM OLD.created_at
            OR NEW.envelope_version IS DISTINCT FROM OLD.envelope_version OR OLD.state='removed'
          ) THEN RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER platform_credential_row_gate BEFORE UPDATE OR DELETE
          ON public.platform_credentials FOR EACH ROW EXECUTE FUNCTION public.platform_credential_row_gate();

        CREATE FUNCTION public.platform_credential_audit_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'platform audit is append only' USING ERRCODE='42501';
          END IF;
          IF NEW.action LIKE 'credential.%' AND current_user<>'bid_platform_credentials_fn'
            AND current_user<>pg_catalog.pg_get_userbyid((
              SELECT relowner FROM pg_catalog.pg_class WHERE oid='public.platform_credentials'::regclass))
          THEN RAISE EXCEPTION 'credential audit requires trusted connection' USING ERRCODE='42501'; END IF;
          IF NEW.action LIKE 'credential.%' AND (
            NEW.action NOT IN ('credential.create','credential.replace','credential.set_active',
              'credential.remove','credential.import','credential.read','credential.probe_start',
              'credential.probe_finish','credential.rewrap')
            OR NEW.details - ARRAY['credential_id','credential_name','old_revision','new_revision',
              'secret_version','old_state','new_state','reason','probe_id','outcome','error_code',
              'checked','rewritten'] <> '{}'::jsonb
          ) THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER platform_credential_audit_gate BEFORE INSERT OR UPDATE OR DELETE
          ON public.platform_audit_logs FOR EACH ROW EXECUTE FUNCTION public.platform_credential_audit_gate();

        CREATE FUNCTION public.platform_credential_audit(
          p_action text,p_actor text,p_id uuid,p_outcome text,p_details jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details)
            VALUES(gen_random_uuid(),p_actor,p_action,p_id::text,p_outcome,jsonb_strip_nulls(p_details));
        EXCEPTION WHEN OTHERS THEN
          RAISE EXCEPTION 'credential_audit_unavailable' USING ERRCODE='P0001';
        END $$;

        CREATE FUNCTION public.platform_credential_view(p_id uuid) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
          SELECT jsonb_build_object('id',c.id,'name',c.name,'purpose',c.purpose,'provider',c.provider,
            'endpoint',c.endpoint,'fingerprint',c.fingerprint,'last_four',c.last_four,
            'state',c.state,'revision',c.revision,'secret_version',c.secret_version,
            'created_at',c.created_at,'updated_at',c.updated_at,'updated_by',c.updated_by,
            'consumers', CASE WHEN c.purpose='catalog_llm' THEN COALESCE((
              SELECT jsonb_agg(jsonb_build_object('kind','catalog_model','model_id',m.id,
                'model_revision',m.revision,'enabled',m.enabled,'default',m.is_default) ORDER BY m.id)
              FROM public.platform_models m WHERE m.credential=c.name),'[]'::jsonb)
              ELSE jsonb_build_array(jsonb_build_object('kind','service','service',c.purpose,
                'selected',c.state<>'removed')) END)
          FROM public.platform_credentials c WHERE c.id=p_id
        $$;

        CREATE FUNCTION public.platform_credential_validate(p jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF NOT COALESCE(
            (p->>'id')::uuid IS NOT NULL AND p->>'name' ~ '^[a-z0-9_]{1,40}$'
            AND p->>'purpose' IN ('catalog_llm','standalone_llm','vendor_search')
            AND p->>'provider' IN ('anthropic','openai','perplexity')
            AND length(p->>'endpoint') BETWEEN 8 AND 300
            AND p->>'endpoint' ~ '^https://(\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*)(/[^[:space:]?#%\\]*)?$'
            AND p->>'endpoint' !~ '/$' AND p->>'endpoint' !~ '[[:cntrl:]]'
            AND length(p->>'encrypted_key') BETWEEN 100 AND 16384
            AND p->>'fingerprint' ~ '^sha256:[a-f0-9]{16}$'
            AND p->>'last_four' ~ '^[!-~]{4}$'
            AND (NOT p ? 'active' OR jsonb_typeof(p->'active')='boolean')
            AND ((p->>'purpose'='vendor_search' AND p->>'provider'='perplexity'
              AND p->>'endpoint'='https://api.perplexity.ai')
              OR (p->>'purpose'<>'vendor_search' AND p->>'provider' IN ('anthropic','openai'))),false)
          THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
        END $$;

        CREATE FUNCTION public.platform_credential_cipher(p_id uuid,p_allow_disabled boolean) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.state='removed' THEN RAISE EXCEPTION 'credential_removed' USING ERRCODE='P0001'; END IF;
          IF c.state='disabled' AND NOT p_allow_disabled THEN
            RAISE EXCEPTION 'credential_disabled' USING ERRCODE='P0001'; END IF;
          RETURN to_jsonb(c);
        END $$;

        CREATE FUNCTION public.platform_credential_catalog_gate() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials; endpoint text;
        BEGIN
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
        CREATE TRIGGER platform_credential_catalog_gate BEFORE INSERT OR UPDATE
          ON public.platform_models FOR EACH ROW EXECUTE FUNCTION public.platform_credential_catalog_gate();

        CREATE FUNCTION public.platform_credential_resolve_catalog(p_model text,p_revision bigint) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE m record; c public.platform_credentials; endpoint text;
        BEGIN
          SELECT id,credential,provider,base_url,revision,enabled INTO m
            FROM public.platform_models WHERE id=p_model;
          IF m.id IS NULL OR NOT m.enabled OR m.revision IS DISTINCT FROM p_revision THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          endpoint := rtrim(COALESCE(NULLIF(m.base_url,''), CASE m.provider
            WHEN 'anthropic' THEN 'https://api.anthropic.com'
            WHEN 'openai' THEN 'https://api.openai.com/v1' END),'/');
          SELECT * INTO c FROM public.platform_credentials WHERE name=m.credential;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.purpose<>'catalog_llm' OR c.provider<>m.provider OR c.endpoint<>endpoint THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,false);
        END $$;

        CREATE FUNCTION public.platform_credential_resolve_service(p_service text,p_id uuid) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF p_service NOT IN ('vendor_search','standalone_llm') OR p_service IS NULL THEN
            RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'credential_missing' USING ERRCODE='P0001'; END IF;
          IF c.purpose<>p_service THEN
            RAISE EXCEPTION 'credential_reference_mismatch' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,false);
        END $$;

        CREATE FUNCTION public.platform_credential_resolve_operator_check(
          p_id uuid,p_revision bigint,p_purpose text) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE c public.platform_credentials;
        BEGIN
          IF p_purpose IS NULL OR p_purpose NOT IN ('import','activate') THEN
            RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=p_id;
          IF c.id IS NULL THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
          IF c.revision IS DISTINCT FROM p_revision THEN
            RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,true);
        END $$;

        CREATE FUNCTION public.platform_credential_readiness(p_target jsonb) RETURNS jsonb
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
          ELSIF p_target->>'kind'='service' AND p_target->>'service' IN ('vendor_search','standalone_llm') THEN
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
            'state',c.state,'configured',COALESCE(c.state='active',false));
        END $$;

        CREATE FUNCTION public.platform_credential_resolve_probe(p_probe uuid) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE a record; c public.platform_credentials;
        BEGIN
          SELECT object_id,details,created_at INTO a FROM public.platform_audit_logs
            WHERE action='credential.probe_start' AND details->>'probe_id'=p_probe::text
              AND outcome='success';
          IF a.object_id IS NULL OR a.created_at<clock_timestamp()-interval '30 seconds'
            OR EXISTS(SELECT FROM public.platform_audit_logs
              WHERE action='credential.probe_finish' AND details->>'probe_id'=p_probe::text) THEN
            RAISE EXCEPTION 'credential_probe_interrupted' USING ERRCODE='P0001'; END IF;
          SELECT * INTO c FROM public.platform_credentials WHERE id=a.object_id::uuid;
          IF c.revision IS DISTINCT FROM (a.details->>'new_revision')::bigint THEN
            RAISE EXCEPTION 'revision_conflict' USING ERRCODE='P0001'; END IF;
          RETURN public.platform_credential_cipher(c.id,true);
        END $$;
    """)
    op.execute(MANAGEMENT_SQL)
    op.execute(MAINTENANCE_SQL)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
              AND r.rolname IN ('bid_platform_credentials_fn','bid_platform_app','bid_credential_reader')
              AND NOT (r.rolname='bid_platform_credentials_fn'
                AND c.relname IN ('platform_credentials','platform_models','platform_audit_logs'))
              AND (pg_catalog.has_any_column_privilege(r.oid,c.oid,'SELECT, INSERT, UPDATE, REFERENCES')
                OR pg_catalog.has_table_privilege(r.oid,c.oid,'DELETE, TRUNCATE, TRIGGER'))
          ) THEN RAISE EXCEPTION 'credential roles have unexpected table privileges'; END IF;
          IF pg_catalog.has_table_privilege('bid_platform_credentials_fn','public.platform_credentials','DELETE, TRUNCATE, TRIGGER')
            OR pg_catalog.has_any_column_privilege('bid_platform_credentials_fn','public.platform_models','INSERT, UPDATE, REFERENCES')
            OR pg_catalog.has_table_privilege('bid_platform_credentials_fn','public.platform_models','DELETE, TRUNCATE, TRIGGER')
            OR pg_catalog.has_any_column_privilege('bid_platform_credentials_fn','public.platform_audit_logs','UPDATE, REFERENCES')
            OR pg_catalog.has_table_privilege('bid_platform_credentials_fn','public.platform_audit_logs','DELETE, TRUNCATE, TRIGGER')
            OR EXISTS(SELECT FROM pg_catalog.pg_attribute a
              WHERE a.attrelid='public.platform_models'::regclass AND a.attnum>0 AND NOT a.attisdropped
                AND a.attname NOT IN ('id','credential','provider','base_url','revision','enabled','is_default')
                AND pg_catalog.has_column_privilege('bid_platform_credentials_fn',a.attrelid,a.attnum,'SELECT'))
          THEN RAISE EXCEPTION 'credential function owner has excess column privileges'; END IF;
        END $$
    """)
    # Helpers have no client grants. The owner is separate from the tenant platform role.
    op.execute("GRANT CREATE ON SCHEMA public TO bid_platform_credentials_fn")
    for signature in (MANAGE, *READERS, *INTERNAL):
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO bid_platform_credentials_fn")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, bid_app, bid_platform_fn, bid_platform_app, bid_credential_reader"
        )
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_platform_credentials_fn")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{MANAGE} TO bid_platform_app")
    for signature in READERS:
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO bid_credential_reader")
    for signature in (
        "platform_credential_row_gate()",
        "platform_credential_audit_gate()",
        "platform_credential_rewrap(uuid, text, text, text)",
        "provider_credential_rewrap(uuid, uuid, text, text)",
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, bid_app, bid_platform_fn, bid_platform_app, bid_credential_reader, bid_platform_credentials_fn"
        )


MANAGEMENT_SQL = r"""
CREATE FUNCTION public.platform_credential_manage(p_action text,p_actor text,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  c public.platform_credentials; prior public.platform_credentials;
  v_id uuid; v_probe uuid; v_reason text; v_state text; v_action text;
  v_limit integer; v_items jsonb := '[]'; v_entry jsonb; v_created integer := 0;
  v_skipped integer := 0; v_dry boolean := false; v_audit record;
  v_details jsonb; v_outcome text; v_duration bigint; v_next text;
BEGIN
  IF p_actor IS NULL OR length(p_actor)>254 OR p_actor !~ '^[^@[:space:]]+@[^@[:space:]]+$'
    OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF p_action='list' THEN
    v_limit := COALESCE((p_body->>'limit')::integer,100);
    IF v_limit NOT BETWEEN 1 AND 100 OR (p_body->>'state' IS NOT NULL AND
      p_body->>'state' NOT IN ('active','disabled','removed')) OR (p_body->>'purpose' IS NOT NULL AND
      p_body->>'purpose' NOT IN ('catalog_llm','standalone_llm','vendor_search')) THEN
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


MAINTENANCE_SQL = r"""
CREATE FUNCTION public.platform_credential_rewrap(p_id uuid,p_old text,p_new text,p_actor text) RETURNS boolean
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE c public.platform_credentials;
BEGIN
  IF current_user<>pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
    WHERE oid='public.platform_credentials'::regclass)) THEN
    RAISE EXCEPTION 'owner maintenance required' USING ERRCODE='42501'; END IF;
  IF p_new IS NULL OR length(p_new) NOT BETWEEN 100 AND 16384 OR p_actor IS NULL
    OR p_actor !~ '^[^@[:space:]]+@[^@[:space:]]+$' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  SELECT * INTO c FROM public.platform_credentials WHERE id=p_id FOR UPDATE;
  IF c.id IS NULL OR c.state='removed' OR c.encrypted_key IS DISTINCT FROM p_old THEN RETURN false; END IF;
  IF p_old=p_new THEN RETURN true; END IF;
  UPDATE public.platform_credentials SET encrypted_key=p_new WHERE id=p_id;
  INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details)
    VALUES(gen_random_uuid(),p_actor,'credential.rewrap',p_id::text,'success',
      jsonb_build_object('credential_id',p_id,'new_revision',c.revision,
        'secret_version',c.secret_version,'checked',1,'rewritten',1));
  RETURN true;
END $$;

CREATE FUNCTION public.provider_credential_rewrap(p_org uuid,p_id uuid,p_old text,p_new text) RETURNS boolean
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE changed bigint; previous_setting text;
BEGIN
  IF current_user<>pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
    WHERE oid='public.provider_configs'::regclass))
    OR p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid THEN
    RAISE EXCEPTION 'owner maintenance required' USING ERRCODE='42501'; END IF;
  IF p_new IS NULL OR length(p_new) NOT BETWEEN 100 AND 16384 THEN
    RAISE EXCEPTION 'invalid credential rewrap' USING ERRCODE='23514'; END IF;
  previous_setting := current_setting('app.provider_rewrap',true);
  PERFORM set_config('app.provider_rewrap','on',true);
  UPDATE public.provider_configs SET encrypted_key=p_new
    WHERE org_id=p_org AND id=p_id AND source='org' AND encrypted_key=p_old;
  GET DIAGNOSTICS changed=ROW_COUNT;
  PERFORM set_config('app.provider_rewrap',COALESCE(previous_setting,''),true);
  RETURN changed=1;
END $$;

CREATE OR REPLACE FUNCTION public.provider_revision_gate() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE previous integer;
BEGIN
  IF TG_OP='UPDATE' AND current_user=pg_catalog.pg_get_userbyid((
    SELECT relowner FROM pg_catalog.pg_class WHERE oid='public.provider_configs'::regclass))
    AND current_setting('app.provider_rewrap',true)='on'
    AND NEW.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
    AND NEW.source='org' AND NEW.encrypted_key IS NOT NULL
    AND (to_jsonb(NEW)-'encrypted_key')=(to_jsonb(OLD)-'encrypted_key') THEN
    RETURN NEW;
  END IF;
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'Provider revisions are immutable' USING ERRCODE='42501'; END IF;
  PERFORM public.provider_require_admin(NEW.org_id,NEW.updated_by);
  PERFORM pg_advisory_xact_lock(hashtextextended('provider:'||NEW.org_id::text,0));
  SELECT COALESCE(max(revision),0) INTO previous FROM public.provider_configs
    WHERE org_id=NEW.org_id AND capability=NEW.capability;
  IF NEW.revision<>previous+1 THEN
    RAISE EXCEPTION 'Provider revision conflict' USING ERRCODE='23514'; END IF;
  IF NEW.source='platform' AND NOT EXISTS(SELECT FROM public.platform_models
    WHERE id=NEW.platform_model_id AND capability=NEW.capability AND enabled) THEN
    RAISE EXCEPTION 'Unavailable platform model' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
"""


def downgrade():
    raise RuntimeError(
        "Retain credential tombstones and audit history; rollback requires a recovery plan"
    )
