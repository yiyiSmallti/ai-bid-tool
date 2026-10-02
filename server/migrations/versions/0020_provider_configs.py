"""Immutable org model revisions and usage attribution."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE provider_configs (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES orgs(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            capability varchar(40) NOT NULL,
            revision integer NOT NULL,
            source varchar(10) NOT NULL,
            platform_model_id varchar(40) REFERENCES platform_models(id),
            data jsonb NOT NULL,
            encrypted_key text,
            key_last4 varchar(4),
            updated_by uuid NOT NULL,
            UNIQUE (org_id, id),
            UNIQUE (org_id, capability, revision),
            FOREIGN KEY (org_id, updated_by) REFERENCES memberships(org_id, user_id),
            CONSTRAINT provider_revision_capability CHECK (revision > 0 AND capability = 'llm_extract'),
            CONSTRAINT provider_config_object CHECK (jsonb_typeof(data) = 'object'),
            CONSTRAINT provider_config_source CHECK (
                (source = 'org' AND platform_model_id IS NULL AND encrypted_key IS NOT NULL
                    AND length(encrypted_key) > 0 AND key_last4 IS NOT NULL AND length(key_last4) = 4
                    AND data->>'provider' IN ('anthropic', 'openai')
                    AND length(data->>'model') BETWEEN 1 AND 100
                    AND data->>'json_mode' IN ('json_schema', 'json_object')) IS TRUE
                OR (source = 'platform' AND platform_model_id IS NOT NULL
                    AND encrypted_key IS NULL AND key_last4 IS NULL))
        );
        CREATE INDEX ix_provider_configs_org_id ON provider_configs(org_id);
        ALTER TABLE provider_configs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE provider_configs FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON provider_configs
            USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
            WITH CHECK (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);
        GRANT SELECT, INSERT ON provider_configs TO bid_app;

        CREATE FUNCTION provider_require_admin(p_org uuid, p_user uuid) RETURNS void
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org', true), '')::uuid
                OR current_setting('app.actor_kind', true) IS DISTINCT FROM 'session'
                OR NULLIF(current_setting('app.actor_token_id', true), '') IS NOT NULL
                OR p_user IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
                OR NOT EXISTS (
                    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
                    JOIN public.orgs o ON o.id=m.org_id
                    WHERE m.org_id=p_org AND m.user_id=p_user AND m.active AND u.active
                        AND o.active AND m.role='admin') THEN
                RAISE EXCEPTION 'Provider configuration requires a human org admin' USING ERRCODE='42501';
            END IF;
        END $$;
        CREATE FUNCTION provider_revision_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE previous integer;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Provider revisions are immutable' USING ERRCODE='42501';
            END IF;
            PERFORM public.provider_require_admin(NEW.org_id, NEW.updated_by);
            PERFORM pg_advisory_xact_lock(hashtextextended('provider:' || NEW.org_id::text, 0));
            SELECT coalesce(max(revision), 0) INTO previous FROM public.provider_configs
                WHERE org_id=NEW.org_id AND capability=NEW.capability;
            IF NEW.revision <> previous + 1 THEN
                RAISE EXCEPTION 'Provider revision conflict' USING ERRCODE='23514';
            END IF;
            IF NEW.source='platform' AND NOT EXISTS (
                SELECT 1 FROM public.platform_models WHERE id=NEW.platform_model_id
                    AND capability=NEW.capability AND enabled) THEN
                RAISE EXCEPTION 'Unavailable platform model' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_revision_gate BEFORE INSERT OR UPDATE OR DELETE ON provider_configs
            FOR EACH ROW EXECUTE FUNCTION provider_revision_gate();

        ALTER TABLE api_tokens ADD CONSTRAINT token_no_provider_write
            CHECK (NOT (scopes ? 'provider:write'));
        ALTER TABLE jobs
            ALTER COLUMN task_id DROP NOT NULL,
            ALTER COLUMN document_id DROP NOT NULL,
            ADD COLUMN provider_config_id uuid,
            ADD COLUMN provider_identity jsonb,
            ADD CONSTRAINT job_provider_config_fk FOREIGN KEY (org_id, provider_config_id)
                REFERENCES provider_configs(org_id, id),
            ADD CONSTRAINT job_document_binding CHECK (
                (kind='provider_test' AND task_id IS NULL AND document_id IS NULL)
                OR (kind<>'provider_test' AND task_id IS NOT NULL AND document_id IS NOT NULL));
        ALTER TABLE usage_records
            ALTER COLUMN task_id DROP NOT NULL,
            ADD COLUMN provider_config_id uuid,
            ADD CONSTRAINT usage_provider_config_fk FOREIGN KEY (org_id, provider_config_id)
                REFERENCES provider_configs(org_id, id);
        CREATE INDEX usage_provider_month ON usage_records(org_id, provider_config_id, created_at);
        CREATE FUNCTION provider_job_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP='UPDATE' AND (NEW.provider_config_id IS DISTINCT FROM OLD.provider_config_id
                OR NEW.provider_identity IS DISTINCT FROM OLD.provider_identity
                OR NEW.kind IS DISTINCT FROM OLD.kind) THEN
                RAISE EXCEPTION 'Job model identity is immutable' USING ERRCODE='42501';
            END IF;
            IF TG_OP='INSERT' AND NEW.kind='provider_test' THEN
                PERFORM public.provider_require_admin(NEW.org_id,
                    NULLIF(NEW.result->'submission'->>'actor_user_id','')::uuid);
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_job_gate BEFORE INSERT OR UPDATE ON jobs
            FOR EACH ROW EXECUTE FUNCTION provider_job_gate();
        CREATE FUNCTION provider_usage_gate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE cfg public.provider_configs; parent public.jobs;
        BEGIN
            IF NEW.job_id IS NOT NULL THEN
                SELECT * INTO parent FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
                IF NEW.provider_config_id IS DISTINCT FROM parent.provider_config_id
                    OR NEW.task_id IS DISTINCT FROM parent.task_id THEN
                    RAISE EXCEPTION 'Usage model or task differs from job' USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.task_id IS NULL AND (parent.id IS NULL OR parent.kind <> 'provider_test') THEN
                RAISE EXCEPTION 'Taskless usage requires a provider test' USING ERRCODE='23514';
            END IF;
            IF NEW.provider_config_id IS NOT NULL THEN
                SELECT * INTO cfg FROM public.provider_configs
                    WHERE org_id=NEW.org_id AND id=NEW.provider_config_id;
                IF cfg.id IS NULL OR (cfg.source='org' AND (NEW.platform_model_id IS NOT NULL
                    OR NEW.charge IS DISTINCT FROM 0::numeric))
                    OR (cfg.source='platform' AND NEW.platform_model_id IS DISTINCT FROM cfg.platform_model_id) THEN
                    RAISE EXCEPTION 'Usage payer differs from configuration' USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER provider_usage_gate BEFORE INSERT OR UPDATE ON usage_records
            FOR EACH ROW EXECUTE FUNCTION provider_usage_gate();
    """)
    # Only existing usage metadata is exposed; the function role gets no config/key access.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_usage_summary(from_month date, to_month date)
        RETURNS TABLE (org_id uuid, month date, billing text, provider text, model text,
                       calls bigint, tokens bigint, input_tokens bigint, output_tokens bigint,
                       ocr_pages bigint, vendor_usd numeric, unpriced_calls bigint,
                       charge numeric)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT r.org_id, date_trunc('month', r.created_at AT TIME ZONE 'UTC')::date,
                 CASE WHEN r.platform_model_id IS NOT NULL THEN 'platform'
                      WHEN r.provider_config_id IS NOT NULL THEN 'org' ELSE 'unbilled' END,
                 r.provider::text, r.model::text, count(*),
                 sum(r.tokens), sum(r.input_tokens), sum(r.output_tokens), sum(r.ocr_pages),
                 coalesce(sum(r.usd), 0), count(*) FILTER (WHERE r.usd IS NULL),
                 coalesce(sum(r.charge), 0)
          FROM public.usage_records r
          WHERE NOT r.test_only
            AND r.created_at >= (date_trunc('month', from_month)::timestamp AT TIME ZONE 'UTC')
            AND r.created_at < ((date_trunc('month', to_month) + interval '1 month')::timestamp AT TIME ZONE 'UTC')
          GROUP BY 1, 2, 3, 4, 5 ORDER BY 2, 1, 3, 4, 5
        $$
    """)


def downgrade():
    raise RuntimeError(
        "Retain provider revisions, credentials and accounting; rollback is application-only"
    )
