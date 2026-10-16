"""Import branches for library items, pins and attachment archives (ADR 0014).

An imported library item, archive or pin keeps the lifecycle and review history
it had when exported, so a retired item or a revoked approval arrives as it was.
The importer rebuilds each new item's search vector once its revisions exist.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.attachment_link_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
 SET "TimeZone" TO 'UTC'
AS $function$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human();
 IF TG_TABLE_NAME='task_attachments' THEN PERFORM public.attachment_task_writer(NEW.task_id); END IF;
 PERFORM 1 FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['active','state_version']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active','state_version'])
   OR NOT OLD.active OR NEW.active OR NEW.state_version<>OLD.state_version+1 THEN
   RAISE EXCEPTION 'Only one-way deactivation is permitted' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NOT (public.package_import_task() IS NOT NULL) AND (NOT NEW.active OR NEW.state_version<>1 OR NOT public.attachment_approval_current(NEW.approval_id)) THEN
  RAISE EXCEPTION 'Current exact archive approval required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='profile_attachment_links' THEN
  IF NOT EXISTS(SELECT 1 FROM public.org_profile_revisions r
   WHERE r.id=NEW.profile_revision_id AND length(btrim(r.data->>NEW.field))>0) THEN
   RAISE EXCEPTION 'Exact nonempty declaration required' USING ERRCODE='23514'; END IF;
 ELSE
  IF (NOT public.attachment_selection_current(NEW.id) AND NOT public.package_import_task() IS NOT NULL)
   OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256) THEN
   RAISE EXCEPTION 'Exact active task profile and declaration required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.attachment_review_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE root public.attachment_archives%ROWTYPE; prior public.attachment_reviews%ROWTYPE;
BEGIN
 PERFORM public.attachment_human();
 SELECT * INTO root FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF NOT root.active AND NOT public.package_import_task() IS NOT NULL THEN
  RAISE EXCEPTION 'Assigned live archive reviewer required' USING ERRCODE='42501'; END IF;
 SELECT r.* INTO prior FROM public.attachment_reviews r WHERE r.attachment_revision_id=NEW.attachment_revision_id AND r.id<>NEW.id
 AND NOT EXISTS(SELECT 1 FROM public.attachment_reviews n WHERE n.prior_review_id=r.id AND n.id<>NEW.id);
 -- An imported chain arrives in one statement, so its order is the exporter's.
 IF NOT (public.package_import_task() IS NOT NULL) AND (NEW.prior_review_id IS DISTINCT FROM prior.id
 OR (NEW.decision='approve' AND prior.decision='approve')
 OR (NEW.decision='reject' AND prior.id IS NOT NULL)
 OR (NEW.decision='revoke' AND prior.decision IS DISTINCT FROM 'approve')) THEN
  RAISE EXCEPTION 'Archive review transition conflict' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.attachment_files f JOIN public.attachment_revisions r ON (r.id)=(f.attachment_revision_id)
  WHERE f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256 AND r.metadata_sha256=NEW.metadata_sha256) THEN
  RAISE EXCEPTION 'Archive review hash mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.attachment_revision_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE root public.attachment_archives%ROWTYPE; parent_xmin xid;
BEGIN
 IF TG_TABLE_NAME='attachment_revisions' THEN
  PERFORM public.attachment_human();
  SELECT * INTO root FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
  IF (NOT root.active OR NEW.revision<>root.current_revision) AND NOT public.package_import_task() IS NOT NULL THEN
   RAISE EXCEPTION 'Revision requires exact active root pointer' USING ERRCODE='23514'; END IF;
 ELSE
  PERFORM public.attachment_human();
  SELECT xmin INTO parent_xmin FROM public.attachment_revisions WHERE id=NEW.attachment_revision_id;
  IF parent_xmin IS DISTINCT FROM pg_current_xact_id()::xid THEN
   RAISE EXCEPTION 'File requires a new revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.attachment_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
 SET "TimeZone" TO 'UTC'
AS $function$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human();
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.current_revision<>1 OR NEW.state_version<>1 OR NOT NEW.active THEN
   RAISE EXCEPTION 'Invalid initial archive state' USING ERRCODE='23514'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['current_revision','state_version','active']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['current_revision','state_version','active'])
   OR NOT OLD.active OR NEW.state_version<>OLD.state_version+1
   OR NEW.current_revision NOT IN (OLD.current_revision,OLD.current_revision+1) THEN
   RAISE EXCEPTION 'Invalid archive state transition' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.attachment_source_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF NEW.source_kind<>'user_supplied_attachment_pdf' THEN RETURN NEW; END IF;
 PERFORM public.attachment_task_writer(NEW.task_id);
 PERFORM 1 FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF (NOT public.attachment_selection_current(NEW.task_attachment_id) AND NOT public.package_imported(NEW.task_id))
 OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.id=NEW.attachment_file_id AND NEW.page<=(f.file->>'page_count')::integer) THEN
  RAISE EXCEPTION 'Attachment source parents are unavailable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_certificate_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v tsvector;
BEGIN
 -- An imported item keeps its lifecycle; the importer rebuilds its search vector
 -- with an update once its revisions exist.
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN
  NEW.search_vector := ''::tsvector; RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new certificate requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'certificate identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human('certificate');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.certificate_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'certificate lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_certificate_vector(r.data) INTO v FROM public.certificate_revisions r
  WHERE r.certificate_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_certificate_selection_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:certificate',NULL,false,false);
 -- An imported pin may name an item retired after the pin was made.
 IF public.package_imported(NEW.task_id) THEN RETURN NEW; END IF;
 SELECT p.lifecycle_state INTO root_state FROM public.certificates p
 WHERE p.id=NEW.certificate_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive certificate cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_feature_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v tsvector;
BEGIN
 -- An imported item keeps its lifecycle; the importer rebuilds its search vector
 -- with an update once its revisions exist.
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN
  NEW.search_vector := ''::tsvector; RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new feature requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'feature identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.feature_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'feature lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_feature_vector(r.data) INTO v FROM public.feature_revisions r
  WHERE r.feature_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_feature_selection_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE parent uuid; feature_state text; parent_state text; current_parent_state text;
BEGIN
 -- Branch TG_OP explicitly: OLD has no row on INSERT.
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:resource',NULL,false,false);
 -- An imported pin may name an item retired after the pin was made.
 IF public.package_imported(NEW.task_id) THEN RETURN NEW; END IF;
 SELECT r.product_id INTO parent FROM public.feature_revisions r
 WHERE r.feature_id=NEW.feature_id AND r.id=NEW.feature_revision_id;
 PERFORM public.management_feature_lock(NEW.feature_id,parent,false);
 SELECT f.lifecycle_state,p.lifecycle_state INTO feature_state,current_parent_state
 FROM public.features f JOIN public.feature_revisions r
 ON r.feature_id=f.id AND r.revision=f.current_revision
 JOIN public.products p ON p.id=r.product_id
 WHERE f.id=NEW.feature_id;
 SELECT p.lifecycle_state INTO parent_state FROM public.products p WHERE p.id=parent;
 IF feature_state IS DISTINCT FROM 'active' OR parent_state IS DISTINCT FROM 'active'
    OR current_parent_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive feature or parent cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_product_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v tsvector;
BEGIN
 -- An imported item keeps its lifecycle; the importer rebuilds its search vector
 -- with an update once its revisions exist.
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN
  NEW.search_vector := ''::tsvector; RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new product requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'product identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_product_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.product_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'product lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_product_vector(r.data) INTO v FROM public.product_revisions r
  WHERE r.product_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_product_selection_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE s text;
BEGIN
 IF NEW.active AND (TG_OP='INSERT' OR NOT OLD.active) THEN
  PERFORM public.task_write_authority(NEW.task_id,'task:resource',NULL,false,false);
 -- An imported pin may name an item retired after the pin was made.
 IF public.package_imported(NEW.task_id) THEN RETURN NEW; END IF;
  SELECT lifecycle_state INTO s FROM public.products WHERE id=NEW.product_id FOR SHARE;
  IF s IS DISTINCT FROM 'active' THEN
   RAISE EXCEPTION 'inactive product cannot receive a new pin' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_profile_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v tsvector;
BEGIN
 -- An imported item keeps its lifecycle; the importer rebuilds its search vector
 -- with an update once its revisions exist.
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN
  NEW.search_vector := ''::tsvector; RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new profile requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'profile identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_bidder_human('profile');
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.profile_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'profile lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_profile_vector(r.data) INTO v FROM public.org_profile_revisions r
  WHERE r.profile_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_profile_selection_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE root_state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN IF OLD.active THEN RETURN NEW; END IF; END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:profile',NULL,false,false);
 -- An imported pin may name an item retired after the pin was made.
 IF public.package_imported(NEW.task_id) THEN RETURN NEW; END IF;
 SELECT p.lifecycle_state INTO root_state FROM public.org_profiles p
 WHERE p.id=NEW.profile_id FOR SHARE;
 IF root_state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive profile cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_template_root_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v tsvector;
BEGIN
 -- An imported item keeps its lifecycle; the importer rebuilds its search vector
 -- with an update once its revisions exist.
 IF TG_OP='INSERT' AND public.package_import_task() IS NOT NULL THEN
  NEW.search_vector := ''::tsvector; RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.lifecycle_state<>'active' OR NEW.lifecycle_revision<>0 OR NEW.search_vector<>''::tsvector THEN
   RAISE EXCEPTION 'new template requires lifecycle baseline' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.id IS DISTINCT FROM OLD.id THEN
  RAISE EXCEPTION 'template identity is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.lifecycle_state,NEW.lifecycle_revision) IS DISTINCT FROM (OLD.lifecycle_state,OLD.lifecycle_revision) THEN
  PERFORM public.management_template_human();
  IF NEW.lifecycle_revision<>OLD.lifecycle_revision+1 OR NEW.lifecycle_state=OLD.lifecycle_state
    OR NOT EXISTS(SELECT 1 FROM public.resource_lifecycle_events e WHERE e.template_id=NEW.id AND e.revision=NEW.lifecycle_revision
      AND e.before_state=OLD.lifecycle_state AND e.after_state=NEW.lifecycle_state
      AND e.resource_revision=OLD.current_revision
      AND e.actor_kind='session')
    OR NEW.current_revision<>OLD.current_revision THEN
   RAISE EXCEPTION 'template lifecycle transition requires exact event' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT public.management_template_vector(r.data) INTO v FROM public.template_revisions r
  WHERE r.template_id=NEW.id AND r.revision=NEW.current_revision;
 NEW.search_vector=coalesce(v,''::tsvector);
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.management_template_selection_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE state text;
BEGIN
 IF NOT NEW.active THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.active THEN RETURN NEW; END IF;
  -- Retained template pins need this rule even after the root is restored.
  -- The AFTER trigger preserves FK/CHECK rejection precedence.
  RAISE EXCEPTION 'Historical selections cannot be reactivated; create a new selection' USING ERRCODE='42501';
 END IF;
 PERFORM public.task_write_authority(NEW.task_id,'task:template',NULL,false,false);
 -- An imported pin may name an item retired after the pin was made.
 IF public.package_imported(NEW.task_id) THEN RETURN NEW; END IF;
 SELECT t.lifecycle_state INTO state FROM public.templates t
 WHERE t.id=NEW.template_id FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN
  RAISE EXCEPTION 'inactive template cannot receive a new pin' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
