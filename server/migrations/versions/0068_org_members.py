"""Org member invitations, revision-checked changes and human-only administration."""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE public.memberships
          ADD COLUMN revision integer NOT NULL DEFAULT 1,
          ADD COLUMN created_by uuid REFERENCES public.users(id),
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
          ADD CONSTRAINT membership_revision CHECK (revision >= 1);
        UPDATE public.memberships SET updated_at = created_at;
        ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_member_scope
          CHECK (NOT (scopes ? 'member:manage'));
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION public.org_add_member(p_org uuid, p_actor uuid, p_email text, p_role text)
        RETURNS TABLE(outcome text, user_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          v_user uuid;
          v_email text := lower(btrim(p_email));
        BEGIN
          -- The owner has platform SELECT policies, so every lookup explicitly scopes the org.
          IF p_org IS DISTINCT FROM nullif(current_setting('app.current_org', true), '')::uuid
             OR p_actor IS DISTINCT FROM nullif(current_setting('app.actor_user_id', true), '')::uuid
             OR current_setting('app.actor_kind', true) IS DISTINCT FROM 'session'
             OR coalesce(current_setting('app.actor_token_id', true), '') <> '' THEN
            RETURN QUERY SELECT 'forbidden'::text, NULL::uuid;
            RETURN;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(p_org::text, 680068));
          IF NOT EXISTS(SELECT 1 FROM public.memberships m
              JOIN public.users u ON u.id = m.user_id JOIN public.orgs o ON o.id = m.org_id
              WHERE m.org_id = p_org AND m.user_id = p_actor
                AND m.active AND m.role = 'admin' AND u.active AND o.active) THEN
            RETURN QUERY SELECT 'forbidden'::text, NULL::uuid;
            RETURN;
          END IF;
          IF v_email IS NULL OR length(v_email) > 254 OR v_email !~ '^[^@\s]+@[^@\s]+$'
             OR p_role IS NULL OR p_role NOT IN ('admin', 'bidder', 'technical', 'viewer') THEN
            RETURN QUERY SELECT 'invalid_input'::text, NULL::uuid;
            RETURN;
          END IF;
          SELECT u.id INTO v_user FROM public.users u WHERE u.email = v_email;
          IF v_user IS NULL THEN
            -- The unique email winner is reused; no UPDATE privilege on users is needed.
            INSERT INTO public.users(id, email, password_hash, active)
              VALUES (gen_random_uuid(), v_email, '!setup', true)
              ON CONFLICT (email) DO NOTHING RETURNING id INTO v_user;
            IF v_user IS NULL THEN
              SELECT u.id INTO STRICT v_user FROM public.users u WHERE u.email = v_email;
            END IF;
          END IF;
          IF EXISTS(SELECT 1 FROM public.memberships m
              WHERE m.org_id = p_org AND m.user_id = v_user) THEN
            RETURN QUERY SELECT 'member_exists'::text, NULL::uuid;
            RETURN;
          END IF;
          -- Do not change current_org: INSERT is subject to the ordinary tenant RLS policy.
          INSERT INTO public.memberships(id, org_id, user_id, role, active, created_by)
            VALUES (gen_random_uuid(), p_org, v_user, p_role, true, p_actor);
          RETURN QUERY SELECT 'added'::text, v_user;
        END $$;
        """
    )
    op.execute(
        """
        GRANT CREATE ON SCHEMA public TO bid_platform_fn;
        ALTER FUNCTION public.org_add_member(uuid, uuid, text, text) OWNER TO bid_platform_fn;
        REVOKE ALL ON FUNCTION public.org_add_member(uuid, uuid, text, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.org_add_member(uuid, uuid, text, text) TO bid_app;
        REVOKE CREATE ON SCHEMA public FROM bid_platform_fn;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.org_member_row_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE
          v_actor uuid;
          v_expected text;
        BEGIN
          -- Migration/administrative repairs and platform provisioning retain their authority.
          IF current_user <> 'bid_app' THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'memberships must be retained and created through org_add_member'
              USING ERRCODE = 'check_violation';
          END IF;
          v_actor := nullif(current_setting('app.actor_user_id', true), '')::uuid;
          v_expected := current_setting('app.member_expected_revision', true);
          IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.user_id IS DISTINCT FROM OLD.user_id
             OR NEW.id IS DISTINCT FROM OLD.id OR NEW.created_by IS DISTINCT FROM OLD.created_by
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR current_setting('app.actor_kind', true) IS DISTINCT FROM 'session'
             OR coalesce(current_setting('app.actor_token_id', true), '') <> ''
             OR NOT EXISTS(SELECT 1 FROM public.memberships m
               JOIN public.users u ON u.id = m.user_id JOIN public.orgs o ON o.id = m.org_id
               WHERE m.org_id = OLD.org_id AND m.user_id = v_actor AND m.active
                 AND m.role = 'admin' AND u.active AND o.active) THEN
            RAISE EXCEPTION 'human org administrator required' USING ERRCODE = 'check_violation';
          END IF;
          IF v_expected IS DISTINCT FROM OLD.revision::text OR NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'member revision conflict' USING ERRCODE = 'check_violation';
          END IF;
          -- HTTP acquires this same lock before selecting/checking the target revision.
          PERFORM pg_advisory_xact_lock(hashtextextended(OLD.org_id::text, 680068));
          IF OLD.active AND OLD.role = 'admin' AND (NOT NEW.active OR NEW.role <> 'admin')
             AND NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
               WHERE m.org_id = OLD.org_id AND m.user_id <> OLD.user_id
                 AND m.active AND m.role = 'admin' AND u.active) THEN
            RAISE EXCEPTION 'last active admin required' USING ERRCODE = 'check_violation';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION public.org_member_row_guard() FROM PUBLIC;
        CREATE TRIGGER org_member_row_guard BEFORE INSERT OR UPDATE OR DELETE ON public.memberships
          FOR EACH ROW EXECUTE FUNCTION public.org_member_row_guard();
        """
    )


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain membership revisions, attribution and audit history"
    )
