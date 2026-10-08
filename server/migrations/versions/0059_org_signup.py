"""Global pre-org applications through narrowly scoped platform-owner functions."""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

FUNCTIONS = (
    "org_application_submit(text, text, text, text, text, text, text)",
    "org_application_list(text, integer, timestamptz)",
    "org_application_approve(text, uuid, text, boolean)",
    "org_application_reject(text, uuid, text)",
    "org_application_expire()",
)

DECISION_COLUMNS = """outcome text, application_id uuid, status text, org_id uuid,
    admin_user_id uuid, user_created boolean, attached_existing_user boolean"""


def upgrade():
    op.execute(
        """
        CREATE TABLE public.org_applications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          status varchar(10) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
          org_name varchar(200) NOT NULL CHECK (length(btrim(org_name)) > 0),
          contact_name varchar(100) NOT NULL CHECK (length(btrim(contact_name)) > 0),
          email varchar(254) NOT NULL CHECK (email = lower(btrim(email))),
          phone varchar(40),
          note varchar(500),
          password_hash text,
          source_digest varchar(64) NOT NULL CHECK (source_digest ~ '^[0-9a-f]{64}$'),
          decided_at timestamptz,
          decided_by varchar(254),
          decision_reason varchar(500),
          org_id uuid REFERENCES public.orgs(id),
          admin_user_id uuid REFERENCES public.users(id),
          user_created boolean,
          attached_existing_user boolean,
          CONSTRAINT org_application_expiry CHECK (expires_at > created_at),
          CONSTRAINT org_application_password CHECK (
            (status = 'pending' AND password_hash IS NOT NULL)
            OR (status <> 'pending' AND password_hash IS NULL)),
          CONSTRAINT org_application_org CHECK ((status = 'approved') = (org_id IS NOT NULL)),
          CONSTRAINT org_application_decision CHECK (
            (status IN ('pending', 'expired') AND decided_at IS NULL AND decided_by IS NULL
              AND decision_reason IS NULL AND admin_user_id IS NULL AND user_created IS NULL
              AND attached_existing_user IS NULL)
            OR (status = 'approved' AND decided_at IS NOT NULL
              AND length(btrim(coalesce(decided_by, ''))) > 0 AND decision_reason IS NULL
              AND admin_user_id IS NOT NULL AND user_created IS NOT NULL
              AND attached_existing_user IS NOT NULL AND user_created <> attached_existing_user)
            OR (status = 'rejected' AND decided_at IS NOT NULL
              AND length(btrim(coalesce(decided_by, ''))) > 0
              AND length(btrim(coalesce(decision_reason, ''))) > 0
              AND admin_user_id IS NULL AND user_created IS NULL
              AND attached_existing_user IS NULL))
        );
        CREATE UNIQUE INDEX org_application_pending_email ON public.org_applications(email)
          WHERE status = 'pending';
        CREATE INDEX org_application_source_time ON public.org_applications(source_digest, created_at);
        CREATE INDEX org_application_status_time ON public.org_applications(status, created_at DESC);
        REVOKE ALL ON TABLE public.org_applications FROM PUBLIC, bid_app;
        GRANT SELECT, INSERT, UPDATE ON public.org_applications TO bid_platform_fn;
        GRANT INSERT ON public.platform_audit_logs TO bid_platform_fn;
        """
    )
    # Keep one org/membership creation path. A user registered after a caller's
    # pre-check wins the unique email without ever having its password replaced.
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.platform_create_org(
          p_name text, p_admin_email text, p_password_hash text)
        RETURNS TABLE (new_org_id uuid, admin_user_id uuid, user_created boolean)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_org uuid := gen_random_uuid();
          v_email text := lower(btrim(p_admin_email));
          v_user uuid;
          v_created boolean := false;
          v_previous text := coalesce(current_setting('app.current_org', true), '');
        BEGIN
          IF length(btrim(p_name)) = 0 OR length(p_name) > 200
             OR v_email !~ '^[^@\s]+@[^@\s]+$' THEN
            RAISE EXCEPTION 'invalid organization input' USING ERRCODE = 'check_violation';
          END IF;
          SELECT u.id INTO v_user FROM public.users u WHERE u.email = v_email;
          IF v_user IS NULL THEN
            INSERT INTO public.users (id, email, password_hash, active)
              VALUES (gen_random_uuid(), v_email, p_password_hash, true)
              ON CONFLICT (email) DO NOTHING RETURNING id INTO v_user;
            v_created := v_user IS NOT NULL;
            IF v_user IS NULL THEN
              SELECT u.id INTO STRICT v_user FROM public.users u WHERE u.email = v_email;
            END IF;
          END IF;
          PERFORM set_config('app.current_org', v_org::text, true);
          INSERT INTO public.orgs (id, org_id, name, active)
            VALUES (v_org, v_org, btrim(p_name), true);
          INSERT INTO public.memberships (id, org_id, user_id, role, active)
            VALUES (gen_random_uuid(), v_org, v_user, 'admin', true);
          PERFORM set_config('app.current_org', v_previous, true);
          RETURN QUERY SELECT v_org, v_user, v_created;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.org_application_submit(
          p_org_name text, p_contact_name text, p_email text, p_phone text,
          p_note text, p_password_hash text, p_source_digest text)
        RETURNS TABLE(outcome text, application_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_id uuid;
          v_now timestamptz;
        BEGIN
          -- A short shared lock serializes both replicas' admission checks and inserts.
          -- NULL hash is a read-only preflight before bounded CPU admission.
          PERFORM pg_advisory_xact_lock(590059);
          v_now := clock_timestamp();
          IF (SELECT count(*) FROM public.org_applications a
              WHERE a.source_digest = p_source_digest
                AND a.created_at > v_now - interval '24 hours') >= 5 THEN
            RETURN QUERY SELECT 'too_many_attempts'::text, NULL::uuid;
            RETURN;
          END IF;
          IF (SELECT count(*) FROM public.org_applications a
              WHERE a.status = 'pending' AND a.expires_at > v_now) >= 200 THEN
            RETURN QUERY SELECT 'signup_busy'::text, NULL::uuid;
            RETURN;
          END IF;
          IF p_password_hash IS NULL THEN
            RETURN QUERY SELECT 'ready'::text, NULL::uuid;
            RETURN;
          END IF;
          IF EXISTS (SELECT 1 FROM public.org_applications a
              WHERE a.email = p_email AND a.status = 'pending' AND a.expires_at > v_now) THEN
            -- The duplicate performs no write, including audit, and exposes no ID.
            RETURN QUERY SELECT 'duplicate'::text, NULL::uuid;
            RETURN;
          END IF;
          UPDATE public.org_applications a SET status = 'expired', password_hash = NULL
            WHERE a.email = p_email AND a.status = 'pending' AND a.expires_at <= v_now;
          INSERT INTO public.org_applications(
            org_name, contact_name, email, phone, note, password_hash, source_digest,
            created_at, expires_at)
            VALUES (p_org_name, p_contact_name, p_email, p_phone, p_note, p_password_hash,
              p_source_digest, v_now, v_now + interval '30 days') RETURNING id INTO v_id;
          INSERT INTO public.platform_audit_logs(id, actor_email, action, object_id, outcome, details)
            VALUES (gen_random_uuid(), 'org_signup', 'org_application.submit', v_id::text,
              'success', jsonb_build_object('application_id', v_id, 'source_digest', p_source_digest));
          RETURN QUERY SELECT 'submitted'::text, v_id;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.org_application_list(p_status text, p_limit integer, p_before timestamptz)
        RETURNS TABLE(id uuid, status text, org_name text, contact_name text, email text,
          phone text, note text, created_at timestamptz, expires_at timestamptz,
          existing_user boolean, source_submissions_24h bigint, decided_at timestamptz,
          decided_by text, decision_reason text, org_id uuid, admin_user_id uuid,
          user_created boolean, attached_existing_user boolean)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
          SELECT a.id,
            CASE WHEN a.status = 'pending' AND a.expires_at <= now()
              THEN 'expired' ELSE a.status::text END,
            a.org_name::text, a.contact_name::text, a.email::text, a.phone::text, a.note::text,
            a.created_at, a.expires_at,
            EXISTS(SELECT 1 FROM public.users u WHERE u.email = a.email),
            greatest(1, (SELECT count(*) FROM public.org_applications s
              WHERE s.source_digest = a.source_digest AND s.created_at > now() - interval '24 hours')),
            a.decided_at, a.decided_by::text, a.decision_reason::text, a.org_id, a.admin_user_id,
            a.user_created, a.attached_existing_user
          FROM public.org_applications a
          WHERE (p_status IS NULL OR p_status = CASE
            WHEN a.status = 'pending' AND a.expires_at <= now() THEN 'expired' ELSE a.status::text END)
            AND (p_before IS NULL OR a.created_at < p_before)
          ORDER BY a.created_at DESC, a.id DESC
          LIMIT least(200, greatest(1, coalesce(p_limit, 50)))
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.org_application_approve(
          p_actor text, p_application uuid, p_org_name text, p_attach boolean)
        RETURNS TABLE({DECISION_COLUMNS})
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_application public.org_applications%ROWTYPE;
          v_org uuid;
          v_user uuid;
          v_created boolean;
        BEGIN
          SELECT * INTO v_application FROM public.org_applications a
            WHERE a.id = p_application FOR UPDATE;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'not_found'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END IF;
          IF v_application.status <> 'pending' OR v_application.expires_at <= clock_timestamp() THEN
            RETURN QUERY SELECT 'application_not_pending'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END IF;
          IF NOT coalesce(p_attach, false)
             AND EXISTS(SELECT 1 FROM public.users u WHERE u.email = v_application.email) THEN
            RETURN QUERY SELECT 'existing_user_requires_attach'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END IF;
          BEGIN
            SELECT c.new_org_id, c.admin_user_id, c.user_created INTO v_org, v_user, v_created
              FROM public.platform_create_org(coalesce(p_org_name, v_application.org_name),
                v_application.email, v_application.password_hash) c;
            IF NOT v_created AND NOT coalesce(p_attach, false) THEN
              -- User creation can race the pre-check. Roll back only this nested
              -- org-creation call, retaining the pending row and original hash.
              RAISE EXCEPTION 'existing user requires attachment' USING ERRCODE = 'P5901';
            END IF;
          EXCEPTION WHEN SQLSTATE 'P5901' THEN
            RETURN QUERY SELECT 'existing_user_requires_attach'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END;
          UPDATE public.org_applications a SET status = 'approved', password_hash = NULL,
            decided_at = clock_timestamp(), decided_by = p_actor, org_id = v_org,
            admin_user_id = v_user, user_created = v_created, attached_existing_user = NOT v_created
            WHERE a.id = p_application;
          INSERT INTO public.platform_audit_logs(id, actor_email, action, object_id, outcome, details)
            VALUES (gen_random_uuid(), p_actor, 'platform.org_application.approve',
              p_application::text, 'success', jsonb_build_object('application_id', p_application,
                'org_id', v_org, 'attached_existing_user', NOT v_created));
          RETURN QUERY SELECT 'approved'::text, p_application, 'approved'::text,
            v_org, v_user, v_created, NOT v_created;
        END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.org_application_reject(p_actor text, p_application uuid, p_reason text)
        RETURNS TABLE({DECISION_COLUMNS})
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_application public.org_applications%ROWTYPE;
        BEGIN
          SELECT * INTO v_application FROM public.org_applications a
            WHERE a.id = p_application FOR UPDATE;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'not_found'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END IF;
          IF v_application.status <> 'pending' OR v_application.expires_at <= clock_timestamp() THEN
            RETURN QUERY SELECT 'application_not_pending'::text, p_application, NULL::text,
              NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
            RETURN;
          END IF;
          UPDATE public.org_applications a SET status = 'rejected', password_hash = NULL,
            decided_at = clock_timestamp(), decided_by = p_actor, decision_reason = btrim(p_reason)
            WHERE a.id = p_application;
          INSERT INTO public.platform_audit_logs(id, actor_email, action, object_id, outcome, details)
            VALUES (gen_random_uuid(), p_actor, 'platform.org_application.reject',
              p_application::text, 'success', jsonb_build_object('application_id', p_application,
                'org_id', NULL, 'attached_existing_user', NULL));
          RETURN QUERY SELECT 'rejected'::text, p_application, 'rejected'::text,
            NULL::uuid, NULL::uuid, NULL::boolean, NULL::boolean;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.org_application_expire() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE v_count integer;
        BEGIN
          UPDATE public.org_applications a SET status = 'expired', password_hash = NULL
            WHERE a.status = 'pending' AND a.expires_at <= clock_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count;
        END $$
        """
    )
    op.execute("GRANT CREATE ON SCHEMA public TO bid_platform_fn")
    for function in (*FUNCTIONS, "platform_create_org(text, text, text)"):
        op.execute(f"ALTER FUNCTION public.{function} OWNER TO bid_platform_fn")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function} TO bid_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_platform_fn")


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain org applications and decision audit"
    )
