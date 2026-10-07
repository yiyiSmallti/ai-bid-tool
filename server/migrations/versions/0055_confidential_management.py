"""Bound confidential metadata/current/history reads without copying secret values."""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)


def downgrade():
    raise RuntimeError("Preserve confidential fields and value history; repair forward")


SCHEMA_SQL = r"""
CREATE FUNCTION public.management_confidential_vector(p_key text,p_label text)
 RETURNS tsvector LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(p_key||' '||p_label), '[^[:alnum:]]+', ' ', 'g'))
$$;
ALTER TABLE public.confidential_fields ADD COLUMN search_vector tsvector
 GENERATED ALWAYS AS (public.management_confidential_vector(key,label)) STORED;
CREATE INDEX management_confidential_fields_browse
 ON public.confidential_fields(org_id,key,id);
CREATE INDEX management_confidential_fields_active_browse
 ON public.confidential_fields(org_id,archived,key,id);
CREATE INDEX management_confidential_fields_org_browse
 ON public.confidential_fields(org_id,scope,archived,key,id);
CREATE INDEX management_confidential_fields_prefix
 ON public.confidential_fields USING gin(search_vector);
CREATE INDEX management_confidential_org_history
 ON public.confidential_values(org_id,field_id,version DESC,id DESC)
 INCLUDE (created_by,created_at,tail) WHERE task_id IS NULL;
CREATE INDEX management_confidential_task_history
 ON public.confidential_values(org_id,field_id,task_id,version DESC,id DESC)
 INCLUDE (created_by,created_at,tail) WHERE task_id IS NOT NULL;

CREATE FUNCTION public.management_confidential_field_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.org_id,NEW.id,NEW.key,NEW.kind,NEW.scope,NEW.created_by,NEW.created_at)
    IS DISTINCT FROM
    (OLD.org_id,OLD.id,OLD.key,OLD.kind,OLD.scope,OLD.created_by,OLD.created_at)
    OR NEW.revision<>OLD.revision+1 THEN
  RAISE EXCEPTION 'confidential field identity is fixed and metadata revision must advance'
   USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
-- AFTER preserves RLS, composite FK and CHECK rejection before business guards.
CREATE TRIGGER zz_management_confidential_field_guard AFTER UPDATE
 ON public.confidential_fields FOR EACH ROW
 EXECUTE FUNCTION public.management_confidential_field_guard();
"""
