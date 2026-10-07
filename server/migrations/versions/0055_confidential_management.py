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
-- @@ uses a non-leakproof function in PostgreSQL 16, so FORCE RLS prevents
-- using its GIN condition ahead of tenant filtering. Index scalar lexemes
-- instead: text >= / < are leakproof and C collation gives literal ranges.
CREATE TABLE public.confidential_field_search_tokens (
 org_id uuid NOT NULL REFERENCES public.orgs(id),
 token text COLLATE "C" NOT NULL,
 field_id uuid NOT NULL,
 CONSTRAINT management_confidential_token_prefix PRIMARY KEY(org_id,token,field_id),
 CONSTRAINT management_confidential_token_field FOREIGN KEY(org_id,field_id)
   REFERENCES public.confidential_fields(org_id,id) ON DELETE CASCADE
);
CREATE INDEX management_confidential_token_owner
 ON public.confidential_field_search_tokens(org_id,field_id,token);
ALTER TABLE public.confidential_field_search_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.confidential_field_search_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON public.confidential_field_search_tokens
 USING(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid)
 WITH CHECK(org_id=NULLIF(current_setting('app.current_org',true),'')::uuid);
-- Backfill only the existing nonsecret metadata lexemes. Reuse the exact
-- generated vector so PostgreSQL simple-tokenizer semantics stay unchanged.
INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token)
 SELECT f.org_id,f.id,t.token FROM public.confidential_fields f
 CROSS JOIN LATERAL unnest(tsvector_to_array(f.search_vector)) AS t(token);

CREATE FUNCTION public.management_confidential_tokens_refresh() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_RELID<>'public.confidential_fields'::regclass THEN
  RAISE EXCEPTION 'confidential search refresh requires its field table'
   USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND NEW.search_vector IS NOT DISTINCT FROM OLD.search_vector THEN
  RETURN NULL;
 END IF;
 DELETE FROM public.confidential_field_search_tokens
  WHERE org_id=NEW.org_id AND field_id=NEW.id
    AND NOT(token=ANY(tsvector_to_array(NEW.search_vector)));
 INSERT INTO public.confidential_field_search_tokens(org_id,field_id,token)
  SELECT NEW.org_id,NEW.id,unnest(tsvector_to_array(NEW.search_vector))
  ON CONFLICT(org_id,token,field_id) DO NOTHING;
 RETURN NULL;
END $$;
CREATE TRIGGER zzz_management_confidential_tokens_refresh AFTER INSERT OR UPDATE OF label
 ON public.confidential_fields FOR EACH ROW
 EXECUTE FUNCTION public.management_confidential_tokens_refresh();

CREATE FUNCTION public.management_confidential_tokens_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 IF TG_OP='UPDATE' OR (TG_OP='INSERT' AND pg_trigger_depth()<>2) THEN
  RAISE EXCEPTION 'confidential search tokens are maintained by field triggers'
   USING ERRCODE='23514';
 END IF;
 -- Depth alone is forgeable through a caller-owned temporary trigger. Validate
 -- every change against the real parent, locked against concurrent label edits.
 -- Invoker rights retain RLS; there is no privileged function owner.
 IF TG_OP='INSERT' THEN
  SELECT search_vector INTO v FROM public.confidential_fields
   WHERE org_id=NEW.org_id AND id=NEW.field_id FOR SHARE;
  IF v IS NULL OR NOT(NEW.token=ANY(tsvector_to_array(v))) THEN
   RAISE EXCEPTION 'confidential search token must match field metadata'
    USING ERRCODE='23514';
  END IF;
 ELSE
  SELECT search_vector INTO v FROM public.confidential_fields
   WHERE org_id=OLD.org_id AND id=OLD.field_id FOR SHARE;
  -- FK cascades can dispatch the queued AFTER DELETE after their trigger
  -- frame has unwound. A missing parent, not a fixed nesting depth, proves
  -- this cleanup is legitimate. Both tables have the same tenant policy.
  IF NOT FOUND THEN
   RETURN NULL;
  END IF;
  IF pg_trigger_depth()<>2 OR v IS NULL OR OLD.token=ANY(tsvector_to_array(v)) THEN
   RAISE EXCEPTION 'current confidential search token must be retained'
    USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER zz_management_confidential_tokens_guard AFTER INSERT OR UPDATE OR DELETE
 ON public.confidential_field_search_tokens FOR EACH ROW
 EXECUTE FUNCTION public.management_confidential_tokens_guard();
REVOKE ALL ON FUNCTION public.management_confidential_tokens_refresh(),
 public.management_confidential_tokens_guard() FROM PUBLIC;
GRANT SELECT,INSERT,DELETE ON public.confidential_field_search_tokens TO bid_app;
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
