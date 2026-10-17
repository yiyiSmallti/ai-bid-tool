"""Agents may hold `solution:draft` and `solution:read` (whole-bid plan, requirement 7).

Editing and confirming a chapter stay human-only; only the two scopes an agent
may be granted join the principal guard's list.
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.agent_principal_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent authority history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF NOT public.actor_is_human() THEN
   RAISE EXCEPTION 'agent authority requires its human owner' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' AND (NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb ? 'agent:run',false)
    OR NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb @> NEW.initial_grants,false)) THEN
   RAISE EXCEPTION 'principal grants must come from its authenticated owner' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT FROM jsonb_array_elements_text(NEW.scopes) AS grants(scope) WHERE scope NOT IN
   ('task:read','job:read','card:read','card:generate','draft:read','draft:run','resource:read',
    'certificate:read','certificate:file:read','profile:read','evidence:source:read','solution:draft','solution:read')) THEN
   RAISE EXCEPTION 'agent scope forbidden' USING ERRCODE='42501'; END IF;
 IF NEW.authority_expires_at>clock_timestamp()+interval '8 hours' THEN
   RAISE EXCEPTION 'agent authority exceeds delegation window' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.initial_grants,NEW.created_at)
    IS DISTINCT FROM (OLD.id,OLD.initial_grants,OLD.created_at)
    OR NOT OLD.scopes @> NEW.scopes OR (OLD.revoked_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)) THEN
   RAISE EXCEPTION 'agent authority cannot expand or change owner' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
"""
    )


def downgrade():
    raise NotImplementedError("Agent solution scopes have no earlier representation")
