"""Template library lifecycle, bounded reads and human original-file access."""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(GUARD_SQL)
    op.execute(GRANTS_SQL)


def downgrade():
    raise RuntimeError("Preserve template lifecycle history and repair forward")


SCHEMA_SQL = r"""
ALTER TABLE public.templates
 ADD COLUMN lifecycle_state varchar(8) NOT NULL DEFAULT 'active',
 ADD COLUMN lifecycle_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN search_vector tsvector NOT NULL DEFAULT ''::tsvector,
 ADD CONSTRAINT template_lifecycle_valid CHECK(lifecycle_state IN ('active','inactive')
   AND lifecycle_revision>=0 AND (lifecycle_revision>0 OR lifecycle_state='active'));
CREATE FUNCTION public.management_template_vector(p_data jsonb) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;
UPDATE public.templates t SET search_vector=public.management_template_vector(r.data)
 FROM public.template_revisions r
 WHERE r.org_id=t.org_id AND r.template_id=t.id AND r.revision=t.current_revision;
CREATE INDEX management_templates_browse ON public.templates(org_id,created_at DESC,id DESC);
CREATE INDEX management_templates_state_browse ON public.templates(org_id,lifecycle_state,created_at DESC,id DESC);
CREATE INDEX management_templates_prefix ON public.templates USING gin(search_vector);
CREATE INDEX management_template_history ON public.template_revisions(org_id,template_id,revision DESC,id DESC);
CREATE INDEX management_template_audit_author ON public.audit_logs(org_id,resource_revision_id_text,object_id)
 WHERE action IN ('resource.template.create','resource.template.update');
CREATE INDEX management_template_lifecycle_audit ON public.audit_logs(org_id,object_id,(details->>'event_id'))
 WHERE action IN ('resource.template.deactivate','resource.template.restore');
CREATE INDEX management_bindings_browse ON public.export_template_bindings(org_id,template_revision_id,reviewed_at DESC,id DESC);
ALTER TABLE public.resource_lifecycle_events
 ADD COLUMN template_id uuid,
 DROP CONSTRAINT lifecycle_one_root,
 ADD CONSTRAINT lifecycle_one_root CHECK(num_nonnulls(product_id,feature_id,certificate_id,profile_id,template_id)=1),
 ADD CONSTRAINT lifecycle_template_root FOREIGN KEY(org_id,template_id) REFERENCES public.templates(org_id,id),
 ADD CONSTRAINT lifecycle_template_revision FOREIGN KEY(org_id,template_id,resource_revision)
   REFERENCES public.template_revisions(org_id,template_id,revision);
CREATE UNIQUE INDEX lifecycle_template_sequence ON public.resource_lifecycle_events(org_id,template_id,revision)
 WHERE template_id IS NOT NULL;
CREATE INDEX management_template_lifecycle_history
 ON public.resource_lifecycle_events(org_id,template_id,revision DESC,id DESC) WHERE template_id IS NOT NULL;
-- Existing FORCE RLS and all actor/root foreign keys stay in place. Validate
-- existing token rows rather than silently granting the new human-only scope.
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_template_file_scope
 CHECK(NOT(scopes ? 'template:file:read'));
"""

GUARD_SQL = r"""
CREATE FUNCTION public.management_template_human(p_org uuid,p_actor uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r text;
BEGIN
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
   OR coalesce(current_setting('app.actor_token_id',true),'')<>''
   OR nullif(current_setting('app.actor_user_id',true),'')::uuid IS DISTINCT FROM p_actor
   OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? 'template:write' THEN
  RAISE EXCEPTION 'human template maintenance required' USING ERRCODE='42501'; END IF;
 SELECT m.role INTO r FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.orgs o ON o.id=m.org_id
  WHERE m.org_id=p_org AND m.user_id=p_actor AND m.active AND u.active AND o.active;
 IF r IS DISTINCT FROM 'admin' THEN
  RAISE EXCEPTION 'template maintenance denied' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION public.management_template_root_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 IF pg_catalog.row_security_active(TG_RELID)
    AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new template requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'template identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_template_human(NEW.org_id,nullif(current_setting('app.actor_user_id',true),'')::uuid);
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.org_id=NEW.org_id
      AND e.template_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid)
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'template lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_template_vector(r.data) INTO v FROM public.template_revisions r
  WHERE r.org_id=NEW.org_id AND r.template_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;
CREATE TRIGGER management_template_root_guard BEFORE INSERT OR UPDATE ON public.templates
 FOR EACH ROW EXECUTE FUNCTION public.management_template_root_guard();

CREATE FUNCTION public.management_template_revision_search() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.templates f SET search_vector=public.management_template_vector(NEW.data)
  WHERE f.org_id=NEW.org_id AND f.id=NEW.template_id AND f.current_revision=NEW.revision;
 RETURN NEW;
END $$;
CREATE TRIGGER management_template_revision_search AFTER INSERT ON public.template_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_template_revision_search();
CREATE OR REPLACE FUNCTION public.management_lifecycle_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_id uuid; root_sequence integer; root_revision integer; root_state text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'lifecycle history is immutable' USING ERRCODE='42501'; END IF;
 IF NEW.template_id IS NOT NULL THEN
  PERFORM public.management_template_human(NEW.org_id,NEW.actor_user_id);
 ELSIF NEW.certificate_id IS NOT NULL THEN
  PERFORM public.management_bidder_human(NEW.org_id,NEW.actor_user_id,'certificate');
 ELSIF NEW.profile_id IS NOT NULL THEN
  PERFORM public.management_bidder_human(NEW.org_id,NEW.actor_user_id,'profile');
 ELSE PERFORM public.management_product_human(NEW.org_id,NEW.actor_user_id); END IF;
 IF NEW.product_id IS NOT NULL THEN
  SELECT p.id,p.lifecycle_revision,p.current_revision,p.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.products p
  WHERE p.org_id=NEW.org_id AND p.id=NEW.product_id FOR UPDATE;
 ELSIF NEW.feature_id IS NOT NULL THEN
  SELECT f.id,f.lifecycle_revision,f.current_revision,f.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.features f
  WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id FOR UPDATE;
 ELSIF NEW.certificate_id IS NOT NULL THEN
  SELECT c.id,c.lifecycle_revision,c.current_revision,c.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.certificates c
  WHERE c.org_id=NEW.org_id AND c.id=NEW.certificate_id FOR UPDATE;
 ELSIF NEW.template_id IS NOT NULL THEN
  SELECT t.id,t.lifecycle_revision,t.current_revision,t.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.templates t
  WHERE t.org_id=NEW.org_id AND t.id=NEW.template_id FOR UPDATE;
 ELSE
  SELECT p.id,p.lifecycle_revision,p.current_revision,p.lifecycle_state
  INTO root_id,root_sequence,root_revision,root_state FROM public.org_profiles p
  WHERE p.org_id=NEW.org_id AND p.id=NEW.profile_id FOR UPDATE;
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
 ELSIF NEW.feature_id IS NOT NULL THEN
  UPDATE public.features f SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE f.org_id=NEW.org_id AND f.id=NEW.feature_id;
 ELSIF NEW.certificate_id IS NOT NULL THEN
  UPDATE public.certificates c SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE c.org_id=NEW.org_id AND c.id=NEW.certificate_id;
 ELSIF NEW.template_id IS NOT NULL THEN
  UPDATE public.templates t SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE t.org_id=NEW.org_id AND t.id=NEW.template_id;
 ELSE
  UPDATE public.org_profiles p SET lifecycle_state=NEW.after_state,lifecycle_revision=NEW.revision
   WHERE p.org_id=NEW.org_id AND p.id=NEW.profile_id;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER management_lifecycle_z_apply ON public.resource_lifecycle_events;
CREATE TRIGGER management_lifecycle_z_apply AFTER INSERT ON public.resource_lifecycle_events
 FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_apply();
CREATE OR REPLACE FUNCTION public.management_lifecycle_audit_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_id uuid; resource_kind text;
BEGIN
 IF NEW.product_id IS NOT NULL THEN root_id:=NEW.product_id; resource_kind:='product';
 ELSIF NEW.feature_id IS NOT NULL THEN root_id:=NEW.feature_id; resource_kind:='feature';
 ELSIF NEW.certificate_id IS NOT NULL THEN root_id:=NEW.certificate_id; resource_kind:='certificate';
 ELSIF NEW.template_id IS NOT NULL THEN root_id:=NEW.template_id; resource_kind:='template';
 ELSE root_id:=NEW.profile_id; resource_kind:='profile'; END IF;
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

-- Lock the task first without business decisions in BEFORE triggers. Real RLS
-- and composite foreign keys reject forged relationships before AFTER guards.
DROP TRIGGER task_archived_write ON public.task_templates;
CREATE TRIGGER management_template_task_lock BEFORE INSERT OR UPDATE OR DELETE ON public.task_templates
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();
CREATE TRIGGER task_archived_write AFTER INSERT OR UPDATE OR DELETE ON public.task_templates
 FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();
CREATE FUNCTION public.management_template_selection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.active THEN RETURN NEW; END IF;
  -- Retained template pins need this rule even after the root is restored.
  -- The AFTER trigger preserves RLS/composite-FK/CHECK rejection precedence.
  RAISE EXCEPTION 'Historical selections cannot be reactivated; create a new selection' USING ERRCODE='42501';
 END IF;
 PERFORM public.task_write_authority(NEW.org_id,NEW.task_id,'task:template',NULL,false,false);
 SELECT t.lifecycle_state INTO state FROM public.templates t
 WHERE t.org_id=NEW.org_id AND t.id=NEW.template_id FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive template cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_template_selection_guard AFTER INSERT OR UPDATE ON public.task_templates
 FOR EACH ROW EXECUTE FUNCTION public.management_template_selection_guard();
-- Binding-specific actor/hash checks also run after real foreign keys. Other
-- export tables keep their existing guard order and worker boundaries.
DROP TRIGGER aaa_export_actor ON public.export_template_bindings;
CREATE TRIGGER aaa_export_actor AFTER INSERT ON public.export_template_bindings
 FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

"""

GRANTS_SQL = r"""
GRANT SELECT, INSERT ON public.resource_lifecycle_events TO bid_app;
GRANT UPDATE(id,lifecycle_state,lifecycle_revision,search_vector) ON public.templates TO bid_app;
REVOKE ALL ON FUNCTION public.management_template_human(uuid,uuid),
 public.management_template_vector(jsonb),public.management_template_root_guard(),
 public.management_template_revision_search(),public.management_template_selection_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_template_human(uuid,uuid),public.management_template_vector(jsonb) TO bid_app;
"""
