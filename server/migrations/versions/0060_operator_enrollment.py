"""Global operator TOTP factors through a dedicated restricted function owner."""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "platform_operator_factor(text)",
    "platform_operator_status(text[])",
    "platform_operator_lock(text)",
    "platform_operator_enroll(text, text, bigint, text, integer, text, text, bigint)",
)


def upgrade():
    op.execute(
        """
        CREATE TABLE public.platform_operator_factors (
          email varchar(254) PRIMARY KEY CHECK (email = lower(btrim(email))),
          secret_ciphertext text NOT NULL CHECK (length(secret_ciphertext) > 0),
          key_version integer NOT NULL CHECK (key_version >= 1),
          generation bigint NOT NULL CHECK (generation >= 1),
          enrolled_at timestamptz NOT NULL,
          enrolled_by varchar(254) NOT NULL CHECK (length(btrim(enrolled_by)) > 0)
        );
        REVOKE ALL ON TABLE public.platform_operator_factors FROM PUBLIC, bid_app;
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bid_operator_enrollment_fn') THEN
            CREATE ROLE bid_operator_enrollment_fn NOLOGIN NOSUPERUSER NOBYPASSRLS
              NOINHERIT NOCREATEDB NOCREATEROLE;
          END IF;
        END $$;
        ALTER ROLE bid_operator_enrollment_fn NOLOGIN NOSUPERUSER NOBYPASSRLS
          NOINHERIT NOCREATEDB NOCREATEROLE;
        GRANT USAGE ON SCHEMA public TO bid_operator_enrollment_fn;
        GRANT SELECT, INSERT ON public.platform_operator_factors
          TO bid_operator_enrollment_fn;
        GRANT UPDATE (secret_ciphertext, key_version, generation, enrolled_at, enrolled_by)
          ON public.platform_operator_factors TO bid_operator_enrollment_fn;
        GRANT SELECT (id, email, password_hash, active) ON public.users
          TO bid_operator_enrollment_fn;
        GRANT INSERT (id, email, password_hash, active) ON public.users
          TO bid_operator_enrollment_fn;
        GRANT UPDATE (password_hash) ON public.users TO bid_operator_enrollment_fn;
        GRANT INSERT (id, actor_email, action, object_id, outcome, details, created_at)
          ON public.platform_audit_logs TO bid_operator_enrollment_fn;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.platform_operator_factor(p_email text)
        RETURNS TABLE(email text, secret_ciphertext text, key_version integer,
          generation bigint, enrolled_at timestamptz, enrolled_by text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT f.email::text, f.secret_ciphertext, f.key_version, f.generation,
            f.enrolled_at, f.enrolled_by::text
          FROM public.platform_operator_factors f WHERE f.email = p_email
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.platform_operator_status(p_emails text[])
        RETURNS TABLE(email text, has_account boolean, generation bigint,
          enrolled_at timestamptz, enrolled_by text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT requested.email, EXISTS(SELECT 1 FROM public.users u
              WHERE u.email = requested.email), f.generation, f.enrolled_at, f.enrolled_by::text
          FROM (SELECT DISTINCT unnest(p_emails) AS email) requested
          LEFT JOIN public.platform_operator_factors f ON f.email = requested.email
          ORDER BY requested.email
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.platform_operator_lock(p_email text)
        RETURNS TABLE(account_exists boolean, active boolean, password_hash text, generation bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_user uuid;
          v_active boolean;
          v_hash text;
          v_generation bigint;
        BEGIN
          -- The identity lock also serializes enrollments before either row exists.
          PERFORM pg_advisory_xact_lock(hashtextextended(p_email, 600060));
          SELECT u.id, u.active, u.password_hash INTO v_user, v_active, v_hash
            FROM public.users u WHERE u.email = p_email FOR UPDATE;
          SELECT f.generation INTO v_generation FROM public.platform_operator_factors f
            WHERE f.email = p_email FOR UPDATE;
          RETURN QUERY SELECT v_user IS NOT NULL, v_active, v_hash, v_generation;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.platform_operator_enroll(
          p_email text, p_expected_hash text, p_expected_generation bigint,
          p_secret_ciphertext text, p_key_version integer, p_new_password_hash text,
          p_enrolled_by text, p_totp_counter bigint)
        RETURNS TABLE(outcome text, account_created boolean, password_set boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_state record;
          v_user uuid;
          v_created boolean := false;
          v_password_set boolean := false;
        BEGIN
          SELECT * INTO v_state FROM public.platform_operator_lock(p_email);
          IF v_state.password_hash IS DISTINCT FROM p_expected_hash
             OR v_state.generation IS DISTINCT FROM p_expected_generation
             OR (v_state.account_exists AND NOT v_state.active)
             OR (v_state.account_exists AND v_state.password_hash <> '!setup'
                 AND p_new_password_hash IS NOT NULL)
             OR ((NOT v_state.account_exists OR v_state.password_hash = '!setup')
                 AND p_new_password_hash IS NULL) THEN
            RETURN QUERY SELECT 'invalid_state'::text, false, false;
            RETURN;
          END IF;
          IF NOT v_state.account_exists THEN
            INSERT INTO public.users(id, email, password_hash, active)
              VALUES (gen_random_uuid(), p_email, p_new_password_hash, true)
              ON CONFLICT (email) DO NOTHING RETURNING id INTO v_user;
            -- Another identity-creation path does not use the enrollment lock.
            -- Its winner must never have its password replaced by this link.
            IF v_user IS NULL THEN
              PERFORM u.id FROM public.users u WHERE u.email = p_email FOR UPDATE;
              RETURN QUERY SELECT 'invalid_state'::text, false, false;
              RETURN;
            END IF;
            v_created := true;
            v_password_set := true;
          ELSE
            SELECT u.id INTO STRICT v_user FROM public.users u WHERE u.email = p_email;
            IF p_new_password_hash IS NOT NULL THEN
              UPDATE public.users u SET password_hash = p_new_password_hash WHERE u.id = v_user;
              v_password_set := true;
            END IF;
          END IF;
          INSERT INTO public.platform_operator_factors AS factors(
            email, secret_ciphertext, key_version, generation, enrolled_at, enrolled_by)
            VALUES (p_email, p_secret_ciphertext, p_key_version, 1,
              clock_timestamp(), p_enrolled_by)
            ON CONFLICT (email) DO UPDATE SET
              secret_ciphertext = EXCLUDED.secret_ciphertext,
              key_version = EXCLUDED.key_version,
              generation = factors.generation + 1,
              enrolled_at = EXCLUDED.enrolled_at,
              enrolled_by = EXCLUDED.enrolled_by;
          INSERT INTO public.platform_audit_logs(
            id, actor_email, action, object_id, outcome, details, created_at)
            VALUES (gen_random_uuid(), p_email, 'platform.operator.enroll', v_user::text,
              'success', jsonb_build_object('email', p_email, 'enrolled_by', p_enrolled_by,
                'totp_counter', p_totp_counter), clock_timestamp());
          RETURN QUERY SELECT 'enrolled'::text, v_created, v_password_set;
        END $$
        """
    )
    # CREATE is needed only while transferring ownership, as in 0010 and 0059.
    op.execute("GRANT CREATE ON SCHEMA public TO bid_operator_enrollment_fn")
    for function in FUNCTIONS:
        op.execute(f"ALTER FUNCTION public.{function} OWNER TO bid_operator_enrollment_fn")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function} TO bid_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_operator_enrollment_fn")


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain operator factors, identities and enrollment audit"
    )
