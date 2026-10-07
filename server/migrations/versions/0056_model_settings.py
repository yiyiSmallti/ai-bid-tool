"""Bounded extraction-model metadata reads and constraint-first revision guards."""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(INDEX_SQL)
    op.execute(GUARD_SQL)


def downgrade():
    raise RuntimeError("Retain immutable provider configuration history and repair forward")


INDEX_SQL = r"""
CREATE INDEX management_provider_history
 ON public.provider_configs(org_id,capability,revision DESC,id DESC);
CREATE INDEX management_provider_audit_author
 ON public.audit_logs(org_id,object_id) WHERE action='provider.set';
-- Match the catalog query's BooleanTest exactly. A bare `enabled` predicate
-- is not the same planner expression as `enabled IS TRUE` for partial-index use.
CREATE INDEX management_provider_catalog
 ON public.platform_models(id COLLATE "C") WHERE enabled IS TRUE AND capability='llm_extract';
"""

GUARD_SQL = r"""
-- Preserve the owner-only ciphertext rewrap exception from ADR 0006. INSERT
-- business guards run AFTER genuine RLS, composite-FK and CHECK rejection.
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
    WHERE org_id=NEW.org_id AND capability=NEW.capability AND id<>NEW.id;
  IF NEW.revision<>previous+1 THEN
    RAISE EXCEPTION 'Provider revision conflict' USING ERRCODE='23514'; END IF;
  IF NEW.source='platform' AND NOT EXISTS(SELECT FROM public.platform_models
    WHERE id=NEW.platform_model_id AND capability=NEW.capability AND enabled) THEN
    RAISE EXCEPTION 'Unavailable platform model' USING ERRCODE='23514'; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER provider_revision_gate ON public.provider_configs;
CREATE TRIGGER provider_revision_gate AFTER INSERT OR UPDATE OR DELETE ON public.provider_configs
 FOR EACH ROW EXECUTE FUNCTION public.provider_revision_gate();
"""
