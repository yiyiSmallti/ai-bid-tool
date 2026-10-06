"""Certificate/profile bounded reads and human lifecycle; preserve all pinned history."""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(GUARD_SQL)
    op.execute(GRANTS_SQL)


def downgrade():
    raise RuntimeError("Preserve certificate/profile lifecycle history and repair forward")


SCHEMA_SQL = r"""
ALTER TABLE public.resource_lifecycle_events
 ADD COLUMN certificate_id uuid,
 ADD COLUMN profile_id uuid,
 DROP CONSTRAINT lifecycle_one_root,
 ADD CONSTRAINT lifecycle_one_root CHECK(num_nonnulls(product_id,feature_id,certificate_id,profile_id)=1);
ALTER TABLE public.resource_lifecycle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resource_lifecycle_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_library_lifecycle_scopes
 CHECK (NOT (scopes ?| ARRAY['certificate:lifecycle','profile:lifecycle']));

ALTER TABLE public.certificates
 ADD COLUMN lifecycle_state varchar(8) NOT NULL DEFAULT 'active',
 ADD COLUMN lifecycle_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN search_vector tsvector NOT NULL DEFAULT ''::tsvector,
 ADD CONSTRAINT certificate_lifecycle_valid CHECK(lifecycle_state IN ('active','inactive')
   AND lifecycle_revision>=0 AND (lifecycle_revision>0 OR lifecycle_state='active'));
CREATE FUNCTION public.management_certificate_vector(p_data jsonb) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','') || ' ' || coalesce(p_data->>'number','')), '[^[:alnum:]]+', ' ', 'g'))
$$;
UPDATE public.certificates p SET search_vector=public.management_certificate_vector(r.data)
 FROM public.certificate_revisions r
 WHERE r.org_id=p.org_id AND r.certificate_id=p.id AND r.revision=p.current_revision;
CREATE INDEX management_certificates_browse ON public.certificates(org_id,created_at DESC,id DESC);
CREATE INDEX management_certificates_state_browse ON public.certificates(org_id,lifecycle_state,created_at DESC,id DESC);
CREATE INDEX management_certificates_prefix ON public.certificates USING gin(search_vector);
CREATE INDEX management_certificate_history ON public.certificate_revisions(org_id,certificate_id,revision DESC,id DESC);
CREATE INDEX management_certificate_audit_author ON public.audit_logs(org_id,resource_revision_id_text,object_id)
 WHERE action IN ('resource.certificate.create','resource.certificate.update','resource.certificate.file.create');
CREATE INDEX management_certificate_lifecycle_audit ON public.audit_logs(org_id,object_id,(details->>'event_id'))
 WHERE action IN ('resource.certificate.deactivate','resource.certificate.restore');
ALTER TABLE public.resource_lifecycle_events
 ADD CONSTRAINT lifecycle_certificate_root FOREIGN KEY(org_id,certificate_id) REFERENCES public.certificates(org_id,id),
 ADD CONSTRAINT lifecycle_certificate_revision FOREIGN KEY(org_id,certificate_id,resource_revision)
   REFERENCES public.certificate_revisions(org_id,certificate_id,revision);
CREATE UNIQUE INDEX lifecycle_certificate_sequence ON public.resource_lifecycle_events(org_id,certificate_id,revision)
 WHERE certificate_id IS NOT NULL;
CREATE INDEX management_certificate_lifecycle_history
 ON public.resource_lifecycle_events(org_id,certificate_id,revision DESC,id DESC) WHERE certificate_id IS NOT NULL;

ALTER TABLE public.org_profiles
 ADD COLUMN lifecycle_state varchar(8) NOT NULL DEFAULT 'active',
 ADD COLUMN lifecycle_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN search_vector tsvector NOT NULL DEFAULT ''::tsvector,
 ADD CONSTRAINT profile_lifecycle_valid CHECK(lifecycle_state IN ('active','inactive')
   AND lifecycle_revision>=0 AND (lifecycle_revision>0 OR lifecycle_state='active'));
CREATE FUNCTION public.management_profile_vector(p_data jsonb) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT to_tsvector('simple'::regconfig,
   regexp_replace(public.management_casefold(coalesce(p_data->>'name','')), '[^[:alnum:]]+', ' ', 'g'))
$$;
UPDATE public.org_profiles p SET search_vector=public.management_profile_vector(r.data)
 FROM public.org_profile_revisions r
 WHERE r.org_id=p.org_id AND r.profile_id=p.id AND r.revision=p.current_revision;
CREATE INDEX management_profiles_browse ON public.org_profiles(org_id,created_at DESC,id DESC);
CREATE INDEX management_profiles_state_browse ON public.org_profiles(org_id,lifecycle_state,created_at DESC,id DESC);
CREATE INDEX management_profiles_prefix ON public.org_profiles USING gin(search_vector);
CREATE INDEX management_profile_history ON public.org_profile_revisions(org_id,profile_id,revision DESC,id DESC);
CREATE INDEX management_profile_audit_author ON public.audit_logs(org_id,resource_revision_id_text,object_id)
 WHERE action IN ('resource.profile.create','resource.profile.update');
CREATE INDEX management_profile_lifecycle_audit ON public.audit_logs(org_id,object_id,(details->>'event_id'))
 WHERE action IN ('resource.profile.deactivate','resource.profile.restore');
ALTER TABLE public.resource_lifecycle_events
 ADD CONSTRAINT lifecycle_profile_root FOREIGN KEY(org_id,profile_id) REFERENCES public.org_profiles(org_id,id),
 ADD CONSTRAINT lifecycle_profile_revision FOREIGN KEY(org_id,profile_id,resource_revision)
   REFERENCES public.org_profile_revisions(org_id,profile_id,revision);
CREATE UNIQUE INDEX lifecycle_profile_sequence ON public.resource_lifecycle_events(org_id,profile_id,revision)
 WHERE profile_id IS NOT NULL;
CREATE INDEX management_profile_lifecycle_history
 ON public.resource_lifecycle_events(org_id,profile_id,revision DESC,id DESC) WHERE profile_id IS NOT NULL;
"""

GUARD_SQL = r"""
CREATE FUNCTION public.management_bidder_human(p_org uuid,p_actor uuid,p_kind text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r text; scopes jsonb;
BEGIN
 scopes:=coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb;
 IF p_kind NOT IN ('certificate','profile')
   OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
   OR coalesce(current_setting('app.actor_token_id',true),'')<>''
   OR nullif(current_setting('app.actor_user_id',true),'')::uuid IS DISTINCT FROM p_actor
   OR NOT (scopes ? (p_kind || ':write')) OR NOT (scopes ? (p_kind || ':lifecycle')) THEN
  RAISE EXCEPTION 'human library maintenance required' USING ERRCODE='42501'; END IF;
 SELECT m.role INTO r FROM public.memberships m JOIN public.users u ON u.id=m.user_id
  JOIN public.orgs o ON o.id=m.org_id
  WHERE m.org_id=p_org AND m.user_id=p_actor AND m.active AND u.active AND o.active;
 IF r IS NULL OR r NOT IN ('admin','bidder') THEN
  RAISE EXCEPTION 'library maintenance denied' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION public.management_certificate_root_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 IF pg_catalog.row_security_active(TG_RELID)
    AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new certificate requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'certificate identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human(NEW.org_id,nullif(current_setting('app.actor_user_id',true),'')::uuid,'certificate');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.org_id=NEW.org_id
      AND e.certificate_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid)
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'certificate lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_certificate_vector(r.data) INTO v FROM public.certificate_revisions r
  WHERE r.org_id=NEW.org_id AND r.certificate_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;
CREATE TRIGGER management_certificate_root_guard BEFORE INSERT OR UPDATE ON public.certificates
 FOR EACH ROW EXECUTE FUNCTION public.management_certificate_root_guard();


CREATE FUNCTION public.management_certificate_revision_search() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.certificates p SET search_vector=public.management_certificate_vector(NEW.data)
  WHERE p.org_id=NEW.org_id AND p.id=NEW.certificate_id AND p.current_revision=NEW.revision;
 RETURN NEW;
END $$;
CREATE TRIGGER management_certificate_revision_search AFTER INSERT ON public.certificate_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_certificate_revision_search();
-- BEFORE only locks the visible task. AFTER guards run after tenant policy and
-- RI_ConstraintTrigger checks, preserving the real isolation rejection first.
DROP TRIGGER task_archived_write ON public.task_certificates;
CREATE TRIGGER management_certificate_task_lock BEFORE INSERT OR UPDATE OR DELETE ON public.task_certificates
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();
CREATE TRIGGER task_archived_write AFTER INSERT OR UPDATE OR DELETE ON public.task_certificates
 FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();
CREATE FUNCTION public.management_certificate_selection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.org_id,NEW.task_id,'task:certificate',NULL,false,false);
 SELECT p.lifecycle_state INTO root_state FROM public.certificates p
 WHERE p.org_id=NEW.org_id AND p.id=NEW.certificate_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive certificate cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_certificate_selection_guard AFTER INSERT OR UPDATE ON public.task_certificates
 FOR EACH ROW EXECUTE FUNCTION public.management_certificate_selection_guard();
CREATE FUNCTION public.management_profile_root_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE v tsvector;
BEGIN
 IF pg_catalog.row_security_active(TG_RELID)
    AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new profile requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'profile identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human(NEW.org_id,nullif(current_setting('app.actor_user_id',true),'')::uuid,'profile');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.org_id=NEW.org_id
      AND e.profile_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid)
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'profile lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_profile_vector(r.data) INTO v FROM public.org_profile_revisions r
  WHERE r.org_id=NEW.org_id AND r.profile_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $$;
CREATE TRIGGER management_profile_root_guard BEFORE INSERT OR UPDATE ON public.org_profiles
 FOR EACH ROW EXECUTE FUNCTION public.management_profile_root_guard();


CREATE FUNCTION public.management_profile_revision_search() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.org_profiles p SET search_vector=public.management_profile_vector(NEW.data)
  WHERE p.org_id=NEW.org_id AND p.id=NEW.profile_id AND p.current_revision=NEW.revision;
 RETURN NEW;
END $$;
CREATE TRIGGER management_profile_revision_search AFTER INSERT ON public.org_profile_revisions
 FOR EACH ROW EXECUTE FUNCTION public.management_profile_revision_search();
-- BEFORE only locks the visible task. AFTER guards run after tenant policy and
-- RI_ConstraintTrigger checks, preserving the real isolation rejection first.
DROP TRIGGER task_archived_write ON public.task_org_profiles;
CREATE TRIGGER management_profile_task_lock BEFORE INSERT OR UPDATE OR DELETE ON public.task_org_profiles
 FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();
CREATE TRIGGER task_archived_write AFTER INSERT OR UPDATE OR DELETE ON public.task_org_profiles
 FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();
CREATE FUNCTION public.management_profile_selection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.org_id,NEW.task_id,'task:profile',NULL,false,false);
 SELECT p.lifecycle_state INTO root_state FROM public.org_profiles p
 WHERE p.org_id=NEW.org_id AND p.id=NEW.profile_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive profile cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER management_profile_selection_guard AFTER INSERT OR UPDATE ON public.task_org_profiles
 FOR EACH ROW EXECUTE FUNCTION public.management_profile_selection_guard();
-- All arms run AFTER immediate RI_ConstraintTrigger checks: forged relationships
-- are rejected by their real FK, then valid rows always undergo human authority.
CREATE OR REPLACE FUNCTION public.management_lifecycle_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE root_id uuid; root_sequence integer; root_revision integer; root_state text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'lifecycle history is immutable' USING ERRCODE='42501'; END IF;
 IF NEW.certificate_id IS NOT NULL THEN
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
"""

GRANTS_SQL = r"""
GRANT SELECT, INSERT ON public.resource_lifecycle_events TO bid_app;
REVOKE ALL ON FUNCTION public.management_bidder_human(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_bidder_human(uuid,uuid,text) TO bid_app;

GRANT UPDATE(id,lifecycle_state,lifecycle_revision,search_vector) ON public.certificates TO bid_app;
REVOKE ALL ON FUNCTION public.management_certificate_vector(jsonb),public.management_certificate_root_guard(),
 public.management_certificate_revision_search(),public.management_certificate_selection_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_certificate_vector(jsonb) TO bid_app;

GRANT UPDATE(id,lifecycle_state,lifecycle_revision,search_vector) ON public.org_profiles TO bid_app;
REVOKE ALL ON FUNCTION public.management_profile_vector(jsonb),public.management_profile_root_guard(),
 public.management_profile_revision_search(),public.management_profile_selection_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.management_profile_vector(jsonb) TO bid_app;
"""
