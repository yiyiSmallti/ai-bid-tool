"""Platform operator console: org status, model catalog, audit and aggregate-only access."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "platform_org_summaries()",
    "platform_usage_summary(date, date)",
    "platform_create_org(text, text, text)",
    "platform_set_org_active(uuid, boolean)",
)


def upgrade():
    op.execute("ALTER TABLE orgs ADD COLUMN active boolean NOT NULL DEFAULT true")
    op.execute(
        """
        ALTER TABLE usage_records
          ADD COLUMN input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
          ADD COLUMN output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
          ADD COLUMN platform_model_id varchar(40),
          ADD COLUMN charge_usd numeric(16, 8) CHECK (charge_usd >= 0)
        """
    )

    # Global tables: the two approved exceptions to the org_id rule besides users.
    op.execute(
        """
        CREATE TABLE platform_models (
          id varchar(40) PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
          capability varchar(40) NOT NULL CHECK (capability IN ('llm_extract')),
          provider varchar(20) NOT NULL CHECK (provider IN ('anthropic', 'openai')),
          model varchar(100) NOT NULL CHECK (length(btrim(model)) > 0),
          base_url varchar(300),
          credential varchar(40) NOT NULL CHECK (credential ~ '^[a-z0-9_]{1,40}$'),
          vendor_input_usd_per_mtok numeric(12, 6) NOT NULL CHECK (vendor_input_usd_per_mtok >= 0),
          vendor_output_usd_per_mtok numeric(12, 6) NOT NULL CHECK (vendor_output_usd_per_mtok >= 0),
          sale_input_usd_per_mtok numeric(12, 6) NOT NULL CHECK (sale_input_usd_per_mtok >= 0),
          sale_output_usd_per_mtok numeric(12, 6) NOT NULL CHECK (sale_output_usd_per_mtok >= 0),
          is_default boolean NOT NULL DEFAULT false,
          enabled boolean NOT NULL DEFAULT true,
          revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by varchar(254) NOT NULL,
          CONSTRAINT platform_default_enabled CHECK (NOT is_default OR enabled)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX platform_models_one_default ON platform_models (capability) WHERE is_default"
    )
    # Models are disabled, never deleted, so usage records keep a valid reference.
    op.execute("GRANT SELECT, INSERT, UPDATE ON platform_models TO bid_app")
    op.execute(
        """
        CREATE TABLE platform_audit_logs (
          id uuid PRIMARY KEY,
          created_at timestamptz NOT NULL DEFAULT now(),
          actor_email varchar(254) NOT NULL,
          action varchar(60) NOT NULL,
          object_id varchar(100),
          outcome varchar(20) NOT NULL CHECK (outcome IN ('success', 'denied', 'failed')),
          details jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object')
        )
        """
    )
    op.execute(
        "CREATE INDEX platform_audit_actor ON platform_audit_logs (actor_email, action, created_at DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON platform_audit_logs TO bid_app")

    # Cross-org reads go through a NOLOGIN, non-BYPASSRLS role that only owns the
    # functions below; bid_app may execute them but cannot query other orgs itself.
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bid_platform_fn') THEN
            CREATE ROLE bid_platform_fn NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO bid_platform_fn")
    op.execute("GRANT SELECT ON orgs, memberships, usage_records, users TO bid_platform_fn")
    op.execute("GRANT INSERT ON orgs, memberships, users TO bid_platform_fn")
    op.execute("GRANT UPDATE (active) ON orgs TO bid_platform_fn")
    for table in ("orgs", "memberships", "usage_records"):
        op.execute(
            f"CREATE POLICY platform_read ON {table} FOR SELECT TO bid_platform_fn USING (true)"
        )

    op.execute(
        """
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
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_usage_summary(from_month date, to_month date)
        RETURNS TABLE (org_id uuid, month date, billing text, provider text, model text,
                       calls bigint, tokens bigint, input_tokens bigint, output_tokens bigint,
                       ocr_pages bigint, vendor_usd numeric, unpriced_calls bigint,
                       charge_usd numeric)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT r.org_id,
                 date_trunc('month', r.created_at AT TIME ZONE 'UTC')::date,
                 CASE WHEN r.platform_model_id IS NULL THEN 'unbilled' ELSE 'platform' END,
                 r.provider::text, r.model::text, count(*),
                 sum(r.tokens), sum(r.input_tokens), sum(r.output_tokens), sum(r.ocr_pages),
                 coalesce(sum(r.usd), 0), count(*) FILTER (WHERE r.usd IS NULL),
                 coalesce(sum(r.charge_usd), 0)
          FROM usage_records r
          WHERE NOT r.test_only
            AND r.created_at >= (date_trunc('month', from_month)::timestamp AT TIME ZONE 'UTC')
            AND r.created_at < ((date_trunc('month', to_month) + interval '1 month')::timestamp
                                AT TIME ZONE 'UTC')
          GROUP BY 1, 2, 3, 4, 5
          ORDER BY 2, 1, 3, 4, 5
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_create_org(p_name text, p_admin_email text, p_password_hash text)
        RETURNS TABLE (new_org_id uuid, admin_user_id uuid, user_created boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_org uuid := gen_random_uuid();
          v_email text := lower(btrim(p_admin_email));
          v_user uuid;
          v_created boolean := false;
        BEGIN
          IF length(btrim(p_name)) = 0 OR length(p_name) > 200 OR v_email !~ '^[^@\\s]+@[^@\\s]+$' THEN
            RAISE EXCEPTION 'invalid organization input' USING ERRCODE = 'check_violation';
          END IF;
          SELECT u.id INTO v_user FROM users u WHERE u.email = v_email;
          IF v_user IS NULL THEN
            v_user := gen_random_uuid();
            INSERT INTO users (id, email, password_hash, active) VALUES (v_user, v_email, p_password_hash, true);
            v_created := true;
          END IF;
          -- Writes stay under the ordinary tenant policy of the new org.
          PERFORM set_config('app.current_org', v_org::text, true);
          INSERT INTO orgs (id, org_id, name, active) VALUES (v_org, v_org, btrim(p_name), true);
          INSERT INTO memberships (id, org_id, user_id, role, active)
            VALUES (gen_random_uuid(), v_org, v_user, 'admin', true);
          PERFORM set_config('app.current_org', '', true);
          RETURN QUERY SELECT v_org, v_user, v_created;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION platform_set_org_active(p_org uuid, p_active boolean)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_count integer;
        BEGIN
          PERFORM set_config('app.current_org', p_org::text, true);
          UPDATE orgs SET active = p_active WHERE id = p_org;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          PERFORM set_config('app.current_org', '', true);
          RETURN v_count = 1;
        END
        $$
        """
    )
    # A new owner needs CREATE on the schema only while ownership is transferred.
    op.execute("GRANT CREATE ON SCHEMA public TO bid_platform_fn")
    for function in FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO bid_platform_fn")
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO bid_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_platform_fn")


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain platform audit history and usage columns"
    )
