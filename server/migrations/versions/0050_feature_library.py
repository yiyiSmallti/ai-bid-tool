"""Feature library lifecycle, parent guards and retained shared lifecycle events."""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(GUARD_SQL)
    op.execute(GRANTS_SQL)


def downgrade():
    raise RuntimeError("Preserve feature lifecycle history and repair forward")


SCHEMA_SQL = r"""
ALTER TABLE public.features
 ADD COLUMN lifecycle_state varchar(8) NOT NULL DEFAULT 'active',
 ADD COLUMN lifecycle_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN search_vector tsvector NOT NULL DEFAULT ''::tsvector,
 ADD CONSTRAINT feature_lifecycle_valid CHECK(lifecycle_state IN ('active','inactive')
   AND lifecycle_revision>=0 AND (lifecycle_revision>0 OR lifecycle_state='active'));
CREATE FUNCTION public.management_feature_vector(p_data jsonb) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;
UPDATE public.features f SET search_vector=public.management_feature_vector(r.data)
 FROM public.feature_revisions r
 WHERE r.org_id=f.org_id AND r.feature_id=f.id AND r.revision=f.current_revision;
CREATE INDEX management_features_browse ON public.features(org_id,created_at DESC,id DESC);
CREATE INDEX management_features_state_browse ON public.features(org_id,lifecycle_state,created_at DESC,id DESC);
CREATE INDEX management_features_prefix ON public.features USING gin(search_vector);
CREATE INDEX management_feature_history ON public.feature_revisions(org_id,feature_id,revision DESC,id DESC);
CREATE INDEX management_feature_parent ON public.feature_revisions(org_id,product_id,feature_id,revision);
CREATE INDEX management_feature_status ON public.feature_revisions(org_id,(data->>'status'),feature_id,revision);
CREATE INDEX management_feature_audit_author ON public.audit_logs(org_id,(details->>'new_revision_id'),object_id)
 WHERE action IN ('resource.feature.create','resource.feature.update');
CREATE INDEX management_feature_lifecycle_audit ON public.audit_logs(org_id,object_id,(details->>'event_id'))
 WHERE action IN ('resource.feature.deactivate','resource.feature.restore');
ALTER TABLE public.resource_lifecycle_events
 ALTER COLUMN product_id DROP NOT NULL,
 ADD COLUMN feature_id uuid,
 DROP CONSTRAINT resource_lifecycle_events_org_id_product_id_revision_key,
 ADD CONSTRAINT lifecycle_one_root CHECK(num_nonnulls(product_id,feature_id)=1),
 ADD CONSTRAINT lifecycle_feature_root FOREIGN KEY(org_id,feature_id) REFERENCES public.features(org_id,id),
 ADD CONSTRAINT lifecycle_feature_revision FOREIGN KEY(org_id,feature_id,resource_revision)
   REFERENCES public.feature_revisions(org_id,feature_id,revision);
CREATE UNIQUE INDEX lifecycle_product_sequence ON public.resource_lifecycle_events(org_id,product_id,revision)
 WHERE product_id IS NOT NULL;
CREATE UNIQUE INDEX lifecycle_feature_sequence ON public.resource_lifecycle_events(org_id,feature_id,revision)
 WHERE feature_id IS NOT NULL;
CREATE INDEX management_feature_lifecycle_history
 ON public.resource_lifecycle_events(org_id,feature_id,revision DESC,id DESC) WHERE feature_id IS NOT NULL;
-- Existing policies, FORCE RLS, actor and product composite FKs remain in place.
"""

GUARD_SQL = r"""
CREATE FUNCTION public.management_feature_root_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 IF pg_catalog.row_security_active(TG_RELID)
    AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new feature requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'feature identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human(NEW.org_id,nullif(current_setting('app.actor_user_id',true),'')::uuid);
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.org_id=NEW.org_id
      AND e.feature_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid)
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'feature lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_feature_vector(r.data) INTO v FROM public.feature_revisions r
  WHERE r.org_id=NEW.org_id AND r.feature_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;
CREATE TRIGGER management_feature_root_guard BEFORE INSERT OR UPDATE ON public.features
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_root_guard();

-- The caller locks tasks first. Resolve the current association before taking
-- roots in stable UUID order and fail a concurrent reassociation after locking;
-- never acquire an unseen new parent out of order while holding the feature.
CREATE FUNCTION public.management_feature_lock(p_org uuid,p_feature uuid,p_parent uuid,p_write boolean)
 RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE head integer; actual integer; parent uuid; item record;
BEGIN
 SELECT f.current_revision,r.product_id INTO head,parent FROM public.features f
 LEFT JOIN public.feature_revisions r ON r.org_id=f.org_id AND r.feature_id=f.id AND r.revision=f.current_revision
 WHERE f.org_id=p_org AND f.id=p_feature;
 FOR item IN SELECT ids.id,ids.kind FROM (
   SELECT p_feature AS id,'feature'::text AS kind
   UNION SELECT parent,'product' WHERE parent IS NOT NULL
   UNION SELECT p_parent,'product' WHERE p_parent IS NOT NULL
 ) ids ORDER BY ids.id,ids.kind LOOP
  IF item.kind='feature' THEN
   IF p_write THEN
    PERFORM 1 FROM public.features f WHERE f.org_id=p_org AND f.id=item.id FOR NO KEY UPDATE;
   ELSE
    PERFORM 1 FROM public.features f WHERE f.org_id=p_org AND f.id=item.id FOR SHARE;
   END IF;
  ELSE
   PERFORM 1 FROM public.products p WHERE p.org_id=p_org AND p.id=item.id FOR SHARE;
  END IF;
 END LOOP;
 SELECT f.current_revision INTO actual FROM public.features f WHERE f.org_id=p_org AND f.id=p_feature;
 IF actual IS DISTINCT FROM head THEN
  RAISE EXCEPTION 'feature association changed; repeat transaction' USING ERRCODE='40001'; END IF;
END $$;

CREATE FUNCTION public.management_feature_revision_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE parent_state text;
BEGIN
 PERFORM public.management_feature_lock(NEW.org_id,NEW.feature_id,NEW.product_id,true);
 SELECT p.lifecycle_state INTO parent_state FROM public.products p
 WHERE p.org_id=NEW.org_id AND p.id=NEW.product_id;
 IF parent_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive product cannot receive a feature association' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_feature_revision_guard AFTER INSERT ON public.feature_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_revision_guard();
CREATE FUNCTION public.management_feature_revision_search() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.features f SET search_vector=public.management_feature_vector(NEW.data)
  WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id AND f.current_revision=NEW.revision;
 RETURN NEW;
END $$;
CREATE TRIGGER management_feature_revision_search AFTER INSERT ON public.feature_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_revision_search();
CREATE FUNCTION public.management_feature_head_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE parent uuid; state text;
BEGIN
 IF NEW.current_revision=OLD.current_revision THEN RETURN NEW; END IF;
 SELECT r.product_id INTO parent FROM public.feature_revisions r
 WHERE r.org_id=NEW.org_id AND r.feature_id=NEW.id AND r.revision=NEW.current_revision;
 -- The deferred composite FK owns an absent revision; do not replace its error.
 IF parent IS NULL THEN RETURN NEW; END IF;
 SELECT p.lifecycle_state INTO state FROM public.products p
 WHERE p.org_id=NEW.org_id AND p.id=parent FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive product cannot receive a feature association' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_feature_head_guard AFTER UPDATE ON public.features
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_head_guard();

-- Both arms run AFTER immediate RI_ConstraintTrigger checks: forged relationships
-- are rejected by their real FK, then valid rows always undergo human authority.
CREATE OR REPLACE FUNCTION public.management_lifecycle_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_id uuid; root_sequence integer; root_revision integer; root_state text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'lifecycle history is immutable' USING ERRCODE='42501'; END IF;
 PERFORM public.management_product_human(NEW.org_id,NEW.actor_user_id);
 IF NEW.product_id IS NOT NULL THEN
  SELECT p.id,p.lifecycle_revision,p.current_revision,p.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.products p
  WHERE p.org_id=NEW.org_id AND p.id=NEW.product_id FOR UPDATE;
 ELSE
  SELECT f.id,f.lifecycle_revision,f.current_revision,f.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.features f
  WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id FOR UPDATE;
 END IF;
 IF root_id IS NULL OR NEW.revision<>root_sequence+1 OR NEW.resource_revision<>root_revision
    OR NEW.before_state<>root_state OR NEW.after_state=NEW.before_state THEN
  RAISE EXCEPTION 'lifecycle event must extend current root' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER management_lifecycle_guard ON public.resource_lifecycle_events;
CREATE TRIGGER management_lifecycle_guard AFTER INSERT OR UPDATE OR DELETE ON public.resource_lifecycle_events
 FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_guard();
CREATE OR REPLACE FUNCTION public.management_lifecycle_apply() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.product_id IS NOT NULL THEN
  UPDATE public.products p SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE p.org_id=NEW.org_id AND p.id=NEW.product_id;
 ELSE
  UPDATE public.features f SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER management_lifecycle_apply ON public.resource_lifecycle_events;
CREATE TRIGGER management_lifecycle_z_apply AFTER INSERT ON public.resource_lifecycle_events
 FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_apply();
CREATE OR REPLACE FUNCTION public.management_lifecycle_audit_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_id uuid; resource_kind text;
BEGIN
 IF NEW.product_id IS NOT NULL THEN root_id:=NEW.product_id; resource_kind:='product';
 ELSE root_id:=NEW.feature_id; resource_kind:='feature'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.org_id=NEW.org_id AND a.object_id=root_id
   AND a.action='resource.' || resource_kind || '.' || CASE WHEN NEW.after_state='inactive' THEN 'deactivate' ELSE 'restore' END
   AND a.actor_user_id=NEW.actor_user_id AND a.actor_token_id IS NULL AND a.actor_kind='session'
   AND a.details->>'event_id'=NEW.id::text
   AND a.details->>'old_lifecycle_revision'=(NEW.revision-1)::text
   AND a.details->>'new_lifecycle_revision'=NEW.revision::text
   AND a.details->>'resource_revision'=NEW.resource_revision::text
   AND a.details->>'before'=NEW.before_state AND a.details->>'after'=NEW.after_state
   AND a.details->>'reason_code'=NEW.reason_code) THEN
  RAISE EXCEPTION 'lifecycle transition requires atomic audit' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
-- Preserve task-first serialization without emitting an archive/business error
-- before a forged selection has been rejected by RLS and composite FKs.
CREATE FUNCTION public.management_feature_task_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE row_org uuid; row_task uuid;
BEGIN
 IF TG_OP='DELETE' THEN row_org:=OLD.org_id; row_task:=OLD.task_id;
 ELSE row_org:=NEW.org_id; row_task:=NEW.task_id; END IF;
 IF NOT pg_catalog.row_security_active(TG_RELID)
    OR row_org=nullif(current_setting('app.current_org',true),'')::uuid THEN
  PERFORM 1 FROM public.tasks t WHERE t.org_id=row_org AND t.id=row_task FOR UPDATE;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
DROP TRIGGER task_archived_write ON public.task_features;
CREATE TRIGGER management_feature_task_lock BEFORE INSERT OR UPDATE OR DELETE ON public.task_features
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();
CREATE TRIGGER task_archived_write AFTER INSERT OR UPDATE OR DELETE ON public.task_features
 FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();
CREATE FUNCTION public.management_feature_selection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE parent uuid; feature_state text; parent_state text; current_parent_state text;
BEGIN
 -- Branch TG_OP explicitly: OLD has no row on INSERT.
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.org_id,NEW.task_id,'task:resource',NULL,false,false);
 SELECT r.product_id INTO parent FROM public.feature_revisions r
 WHERE r.org_id=NEW.org_id AND r.feature_id=NEW.feature_id AND r.id=NEW.feature_revision_id;
 PERFORM public.management_feature_lock(NEW.org_id,NEW.feature_id,parent,false);
 SELECT f.lifecycle_state,p.lifecycle_state INTO feature_state,current_parent_state
 FROM public.features f JOIN public.feature_revisions r
 ON r.org_id=f.org_id AND r.feature_id=f.id AND r.revision=f.current_revision
 JOIN public.products p ON p.org_id=r.org_id AND p.id=r.product_id
 WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id;
 SELECT p.lifecycle_state INTO parent_state FROM public.products p WHERE p.org_id=NEW.org_id AND p.id=parent;
 IF feature_state IS DISTINCT FROM 'active' OR parent_state IS DISTINCT FROM 'active'
    OR current_parent_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive feature or parent cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_feature_selection_guard AFTER INSERT OR UPDATE ON public.task_features
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_selection_guard();
"""

GRANTS_SQL = r"""
GRANT SELECT, INSERT ON public.resource_lifecycle_events TO bid_app;
GRANT UPDATE(id) ON public.features TO bid_app;
REVOKE ALL ON FUNCTION public.management_feature_vector(jsonb),public.management_feature_root_guard(),
 public.management_feature_lock(uuid,uuid,uuid,boolean),public.management_feature_revision_guard(),
 public.management_feature_revision_search(),public.management_feature_head_guard(),
 public.management_feature_selection_guard(),public.management_feature_task_lock() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_feature_vector(jsonb),
 public.management_feature_lock(uuid,uuid,uuid,boolean) TO bid_app;
"""
