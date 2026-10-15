"""Gateway members, sessions, sign-in attempts and actor attribution.

The tables stay empty in a personal database and the new columns stay null.
Every table whose insert trigger checks for the human actor, directly or
through a helper such as `actor_is_human()`, gains `actor_member_id`; one
trigger fills it from the transaction-local `app.actor_member_id` and, on a
gateway, rejects a human write without a member. The same query enumerates
the tables in `server/tests/test_members.py`, so a later decision table that
misses the column fails there.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

DECISION_TABLES = r"""
WITH RECURSIVE human(oid, proname) AS (
  SELECT p.oid, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.proname <> 'actor_member_attribution'
    AND (p.proname='actor_is_human'
      OR (p.prosrc LIKE '%app.actor_kind%' AND p.prosrc LIKE '%''session''%'))
  UNION
  SELECT p.oid, p.proname FROM human h, pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public' AND p.prosrc ~ ('\m' || h.proname || '\s*\(')
)
SELECT DISTINCT c.relname::text AS tbl
FROM pg_trigger t JOIN human h ON h.oid=t.tgfoid JOIN pg_class c ON c.oid=t.tgrelid
WHERE NOT t.tgisinternal AND (t.tgtype & 4) <> 0
"""


def upgrade():
    op.execute(
        """
CREATE TABLE public.members (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    login character varying(64) NOT NULL,
    display_name character varying(60) NOT NULL,
    role character varying(10) NOT NULL,
    password_hash text NOT NULL,
    must_change_password boolean NOT NULL DEFAULT true,
    status character varying(10) NOT NULL DEFAULT 'active',
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    disabled_at timestamp with time zone,
    password_changed_at timestamp with time zone,
    CONSTRAINT members_pkey PRIMARY KEY (id),
    CONSTRAINT member_login CHECK (((login)::text ~ '^[a-z0-9._-]{3,64}$'::text)),
    CONSTRAINT member_display_name CHECK ((length(btrim((display_name)::text)) >= 1)),
    CONSTRAINT member_role CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'member'::character varying])::text[]))),
    CONSTRAINT member_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'disabled'::character varying])::text[])) AND (((status)::text = 'disabled'::text) = (disabled_at IS NOT NULL))),
    CONSTRAINT member_password_hash CHECK ((password_hash ~ '^pbkdf2\\$[0-9]+\\$[^$]+\\$[^$]+$'::text))
);
CREATE UNIQUE INDEX member_login_unique ON public.members USING btree (lower((login)::text));

CREATE FUNCTION public.member_last_admin() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Members are disabled, not deleted' USING ERRCODE='42501';
  END IF;
  IF OLD.role='admin' AND OLD.status='active'
     AND NOT (NEW.role='admin' AND NEW.status='active') THEN
    -- Serializes concurrent demotions so two admins cannot remove each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('members.active_admins',0));
    IF NOT EXISTS (
      SELECT 1 FROM public.members
      WHERE id<>OLD.id AND role='admin' AND status='active'
    ) THEN
      RAISE EXCEPTION 'last_admin: at least one active administrator is required'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER member_last_admin BEFORE UPDATE OR DELETE ON public.members
    FOR EACH ROW EXECUTE FUNCTION public.member_last_admin();

CREATE TABLE public.member_grants (
    member_id uuid NOT NULL REFERENCES public.members(id),
    "grant" character varying(30) NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT member_grants_pkey PRIMARY KEY (member_id, "grant"),
    CONSTRAINT member_grant CHECK ((("grant")::text = ANY ((ARRAY['library:edit'::character varying, 'memory:edit'::character varying, 'confidential:reveal'::character varying])::text[])))
);

CREATE TABLE public.member_sessions (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    member_id uuid NOT NULL REFERENCES public.members(id),
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL DEFAULT now(),
    revoked_at timestamp with time zone,
    client_label character varying(100),
    CONSTRAINT member_sessions_pkey PRIMARY KEY (id),
    CONSTRAINT member_session_window CHECK ((expires_at > created_at))
);
CREATE INDEX member_session_member ON public.member_sessions USING btree (member_id) WHERE (revoked_at IS NULL);

CREATE TABLE public.login_attempts (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    login_digest character varying(64) NOT NULL,
    source_digest character varying(64),
    outcome character varying(10) NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT login_attempts_pkey PRIMARY KEY (id),
    CONSTRAINT login_attempt_outcome CHECK (((outcome)::text = ANY ((ARRAY['failure'::character varying, 'success'::character varying])::text[])))
);
CREATE INDEX login_attempt_login ON public.login_attempts USING btree (login_digest, created_at) WHERE ((outcome)::text = 'failure'::text);
CREATE INDEX login_attempt_source ON public.login_attempts USING btree (source_digest, created_at) WHERE ((outcome)::text = 'failure'::text);

CREATE FUNCTION public.actor_member() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$ SELECT nullif(current_setting('app.actor_member_id', true), '')::uuid $$;

-- TG_ARGV[0]: the column to fill; TG_ARGV[1] = 'required' makes a member
-- mandatory on a gateway for every actor kind, not only human sessions.
CREATE FUNCTION public.actor_member_attribution() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE member uuid := public.actor_member();
BEGIN
  IF member IS NULL
     AND (current_setting('app.actor_kind', true) IS NOT DISTINCT FROM 'session'
          OR TG_ARGV[1] IS NOT DISTINCT FROM 'required')
     AND EXISTS (SELECT 1 FROM public.installation WHERE mode='enterprise') THEN
    RAISE EXCEPTION 'A gateway write requires the acting member' USING ERRCODE='42501';
  END IF;
  NEW := jsonb_populate_record(NEW, jsonb_build_object(TG_ARGV[0], member));
  RETURN NEW;
END $$;

ALTER TABLE public.api_tokens ADD COLUMN member_id uuid REFERENCES public.members(id);
ALTER TABLE public.agent_principals ADD COLUMN member_id uuid REFERENCES public.members(id);
ALTER TABLE public.vendor_calls ADD COLUMN member_id uuid;
ALTER TABLE public.usage_records ADD COLUMN member_id uuid;
CREATE TRIGGER actor_member_attribution BEFORE INSERT ON public.api_tokens
    FOR EACH ROW EXECUTE FUNCTION public.actor_member_attribution('member_id', 'required');
CREATE TRIGGER actor_member_attribution BEFORE INSERT ON public.agent_principals
    FOR EACH ROW EXECUTE FUNCTION public.actor_member_attribution('member_id', 'required');
CREATE TRIGGER actor_member_attribution BEFORE INSERT ON public.vendor_calls
    FOR EACH ROW EXECUTE FUNCTION public.actor_member_attribution('member_id');
CREATE TRIGGER actor_member_attribution BEFORE INSERT ON public.usage_records
    FOR EACH ROW EXECUTE FUNCTION public.actor_member_attribution('member_id');

GRANT SELECT,INSERT,UPDATE ON TABLE public.members TO bid_app;
GRANT SELECT,INSERT,DELETE ON TABLE public.member_grants TO bid_app;
GRANT SELECT,INSERT,UPDATE ON TABLE public.member_sessions TO bid_app;
GRANT SELECT,INSERT ON TABLE public.login_attempts TO bid_app;
"""
    )
    op.execute(
        f"""
DO $$
DECLARE name text;
BEGIN
  FOR name IN {DECISION_TABLES} LOOP
    -- These name their member `member_id` and already carry the trigger.
    CONTINUE WHEN name IN ('agent_principals', 'api_tokens');
    EXECUTE format('ALTER TABLE public.%I ADD COLUMN actor_member_id uuid', name);
    EXECUTE format(
      'CREATE TRIGGER actor_member_attribution BEFORE INSERT ON public.%I '
      'FOR EACH ROW EXECUTE FUNCTION public.actor_member_attribution(%L)',
      name, 'actor_member_id');
  END LOOP;
END $$;
"""
    )


def downgrade():
    raise NotImplementedError("Member attribution has no earlier representation")
