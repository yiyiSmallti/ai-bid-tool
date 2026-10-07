"""Retained attachment archives, exact review pins and source privacy bindings."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

TABLES = (
    "attachment_archives",
    "attachment_revisions",
    "attachment_files",
    "attachment_file_parts",
    "attachment_reviews",
    "profile_attachment_links",
    "task_attachments",
    "attachment_privacy_holds",
)


def upgrade():
    op.execute(PARENT_SQL)
    for statement in TABLE_SQL[:-1]:
        op.execute(statement)
    op.execute(SOURCE_SQL)
    op.execute(TABLE_SQL[-1])
    for statement in INDEX_SQL:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy = "org_id = NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
        grants = (
            "SELECT, INSERT, UPDATE"
            if table in ("attachment_archives", "profile_attachment_links", "task_attachments")
            else "SELECT, INSERT"
        )
        op.execute(f"GRANT {grants} ON {table} TO bid_app")
    op.execute(GUARD_SQL)
    op.execute(SCREENSHOT_SQL)
    _patch_screenshot_guards()
    # Existing source/screenshot tables already produce their own events. New
    # task rows use the same statement-level coalescing and ordered head locks.
    for table in ("task_attachments", "attachment_privacy_holds"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            transition = (
                "OLD TABLE AS old_rows"
                if operation == "DELETE"
                else "OLD TABLE AS old_rows NEW TABLE AS new_rows"
                if operation == "UPDATE"
                else "NEW TABLE AS new_rows"
            )
            op.execute(
                f"CREATE TRIGGER task_event_{operation.lower()} AFTER {operation} ON public.{table} "
                f"REFERENCING {transition} FOR EACH STATEMENT "
                "EXECUTE FUNCTION public.produce_task_events('task_id','')"
            )


def downgrade():
    raise RuntimeError(
        "Retain attachment ciphertext and provenance; disable writes and repair forward"
    )


TABLE_SQL = (
    r"""
CREATE TABLE attachment_archives (
	current_revision INTEGER NOT NULL, 
	state_version INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	custodian_user_id UUID NOT NULL, 
	reviewer_user_id UUID NOT NULL, 
	created_by UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	FOREIGN KEY(org_id, custodian_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, reviewer_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	CHECK (current_revision > 0 AND state_version > 0), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE attachment_revisions (
	attachment_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	label_encrypted TEXT NOT NULL, 
	metadata_sha256 VARCHAR(64) NOT NULL, 
	created_by UUID NOT NULL, 
	request_id UUID NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, attachment_id, id), 
	UNIQUE (org_id, attachment_id, revision), 
	FOREIGN KEY(org_id, attachment_id) REFERENCES attachment_archives (org_id, id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	CHECK (revision > 0 AND metadata_sha256 ~ '^[0-9a-f]{64}$' AND length(label_encrypted)>0), 
	CHECK (kind IN ('business_licence','qualification_scan','contract','performance_record')), 
	CHECK (payload_hash ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE attachment_files (
	attachment_id UUID NOT NULL, 
	attachment_revision_id UUID NOT NULL, 
	created_by UUID NOT NULL, 
	file JSONB NOT NULL, 
	storage_key VARCHAR(400) NOT NULL, 
	upload_name_encrypted TEXT NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, attachment_id, attachment_revision_id), 
	UNIQUE (org_id, attachment_revision_id), 
	FOREIGN KEY(org_id, attachment_id, attachment_revision_id) REFERENCES attachment_revisions (org_id, attachment_id, id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	CHECK (jsonb_typeof(file)='object' AND file ?& ARRAY['name','sha256','size_bytes','page_count','media_type'] AND jsonb_typeof(file->'media_type')='string' AND file->>'media_type'='application/pdf' AND jsonb_typeof(file->'sha256')='string' AND file->>'sha256' ~ '^[0-9a-f]{64}$'), 
	CHECK (jsonb_typeof(file->'size_bytes')='number' AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (file->>'size_bytes')::numeric=trunc((file->>'size_bytes')::numeric) AND jsonb_typeof(file->'page_count')='number' AND (file->>'page_count')::numeric BETWEEN 1 AND 200 AND (file->>'page_count')::numeric=trunc((file->>'page_count')::numeric)), 
	CHECK (jsonb_typeof(file->'name')='string' AND length(btrim(file->>'name')) BETWEEN 1 AND 200 AND position('/' in file->>'name')=0 AND position(chr(92) in file->>'name')=0 AND file->>'name' !~ '[[:cntrl:]]' AND right(file->>'name',4)='.pdf' AND length(upload_name_encrypted)>0), 
	CHECK (storage_key='org/' || org_id::text || '/attachment/' || attachment_id::text || '/' || attachment_revision_id::text || '/' || (file->>'sha256') || '.pdf'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE attachment_file_parts (
	attachment_id UUID NOT NULL, 
	attachment_revision_id UUID NOT NULL, 
	attachment_file_id UUID NOT NULL, 
	ordinal INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	media_type VARCHAR(40) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	page_start INTEGER NOT NULL, 
	page_count INTEGER NOT NULL, 
	rotation INTEGER NOT NULL, 
	storage_key VARCHAR(400) NOT NULL, 
	upload_name_encrypted TEXT NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, attachment_file_id, ordinal), 
	FOREIGN KEY(org_id, attachment_file_id, attachment_id, attachment_revision_id) REFERENCES attachment_files (org_id, id, attachment_id, attachment_revision_id), 
	CHECK (ordinal BETWEEN 1 AND 20 AND rotation IN (0,90,180,270)), 
	CHECK (media_type IN ('application/pdf','image/png','image/jpeg') AND sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 41943040), 
	CHECK (page_start BETWEEN 1 AND 200 AND page_count BETWEEN 1 AND 200 AND page_start+page_count-1<=200), 
	CHECK (length(btrim(name)) BETWEEN 1 AND 200 AND position('/' in name)=0 AND position(chr(92) in name)=0 AND name !~ '[[:cntrl:]]' AND length(upload_name_encrypted)>0), 
	CHECK (storage_key='org/' || org_id::text || '/attachment/' || attachment_id::text || '/' || attachment_revision_id::text || '/parts/' || ordinal::text || '/' || sha256 || CASE media_type WHEN 'application/pdf' THEN '.pdf' WHEN 'image/png' THEN '.png' ELSE '.jpg' END), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE attachment_reviews (
	attachment_id UUID NOT NULL, 
	attachment_revision_id UUID NOT NULL, 
	file_id UUID NOT NULL, 
	original_sha256 VARCHAR(64) NOT NULL, 
	metadata_sha256 VARCHAR(64) NOT NULL, 
	prior_review_id UUID, 
	decision VARCHAR(10) NOT NULL, 
	reason VARCHAR(40) NOT NULL, 
	reviewed_by UUID NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	request_id UUID NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, attachment_revision_id, id), 
	UNIQUE (org_id, attachment_id, attachment_revision_id, file_id, id), 
	FOREIGN KEY(org_id, file_id, attachment_id, attachment_revision_id) REFERENCES attachment_files (org_id, id, attachment_id, attachment_revision_id), 
	FOREIGN KEY(org_id, attachment_revision_id, prior_review_id) REFERENCES attachment_reviews (org_id, attachment_revision_id, id), 
	FOREIGN KEY(org_id, reviewed_by) REFERENCES memberships (org_id, user_id), 
	CHECK (decision IN ('approve','reject','revoke') AND (decision='approve')=(reason='accepted_for_internal_use') AND (decision<>'revoke' OR prior_review_id IS NOT NULL)), 
	CHECK (reason IN ('accepted_for_internal_use','wrong_document','unreadable_document','metadata_mismatch','privacy_concern','superseded','withdrawn_by_org','reviewer_unavailable','responsibility_changed','declaration_changed','selection_replaced')), 
	CHECK (original_sha256 ~ '^[0-9a-f]{64}$' AND metadata_sha256 ~ '^[0-9a-f]{64}$'), 
	CHECK (payload_hash ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE profile_attachment_links (
	profile_id UUID NOT NULL, 
	profile_revision_id UUID NOT NULL, 
	field VARCHAR(30) NOT NULL, 
	attachment_id UUID NOT NULL, 
	attachment_revision_id UUID NOT NULL, 
	file_id UUID NOT NULL, 
	approval_id UUID NOT NULL, 
	active BOOLEAN NOT NULL, 
	state_version INTEGER NOT NULL, 
	created_by UUID NOT NULL, 
	request_id UUID NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	CONSTRAINT attachment_link_binding UNIQUE (org_id, id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id), 
	FOREIGN KEY(org_id, profile_id, profile_revision_id) REFERENCES org_profile_revisions (org_id, profile_id, id), 
	FOREIGN KEY(org_id, attachment_id, attachment_revision_id, file_id, approval_id) REFERENCES attachment_reviews (org_id, attachment_id, attachment_revision_id, file_id, id), 
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id), 
	CHECK (field IN ('registration_details','performance_summary','standard_wording') AND state_version>0), 
	CHECK (payload_hash ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE task_attachments (
	task_id UUID NOT NULL, 
	task_org_profile_id UUID NOT NULL, 
	profile_revision_id UUID NOT NULL, 
	profile_attachment_link_id UUID NOT NULL, 
	field VARCHAR(30) NOT NULL, 
	attachment_id UUID NOT NULL, 
	attachment_revision_id UUID NOT NULL, 
	file_id UUID NOT NULL, 
	approval_id UUID NOT NULL, 
	original_sha256 VARCHAR(64) NOT NULL, 
	lot VARCHAR(100) NOT NULL, 
	active BOOLEAN NOT NULL, 
	state_version INTEGER NOT NULL, 
	selected_by UUID NOT NULL, 
	selected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	request_id UUID NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	CONSTRAINT attachment_selection_source_binding UNIQUE (org_id, id, task_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, file_id, approval_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, task_org_profile_id, task_id, profile_revision_id, lot) REFERENCES task_org_profiles (org_id, id, task_id, profile_revision_id, lot), 
	FOREIGN KEY(org_id, profile_attachment_link_id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id) REFERENCES profile_attachment_links (org_id, id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id), 
	FOREIGN KEY(org_id, selected_by) REFERENCES memberships (org_id, user_id), 
	CHECK (original_sha256 ~ '^[0-9a-f]{64}$' AND state_version>0), 
	CHECK (payload_hash ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE attachment_privacy_holds (
	task_id UUID NOT NULL, 
	evidence_source_id UUID NOT NULL, 
	source_png_sha256 VARCHAR(64) NOT NULL, 
	prior_hold_id UUID, 
	reviewed_by UUID NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	reason VARCHAR(30) NOT NULL, 
	request_id UUID NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, evidence_source_id, id), 
	FOREIGN KEY(org_id, task_id, evidence_source_id) REFERENCES evidence_sources (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id, evidence_source_id, prior_hold_id) REFERENCES attachment_privacy_holds (org_id, task_id, evidence_source_id, id), 
	FOREIGN KEY(org_id, reviewed_by) REFERENCES memberships (org_id, user_id), 
	CHECK (reason='sensitive_content' AND source_png_sha256 ~ '^[0-9a-f]{64}$'), 
	CHECK (payload_hash ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
)

INDEX_SQL = (
    "CREATE INDEX attachment_archive_page ON attachment_archives (org_id, created_at, id)",
    "CREATE INDEX ix_attachment_archives_org_id ON attachment_archives (org_id)",
    "CREATE INDEX attachment_revision_page ON attachment_revisions (org_id, attachment_id, created_at, id)",
    "CREATE INDEX ix_attachment_revisions_org_id ON attachment_revisions (org_id)",
    "CREATE INDEX ix_attachment_files_org_id ON attachment_files (org_id)",
    "CREATE INDEX ix_attachment_file_parts_org_id ON attachment_file_parts (org_id)",
    "CREATE INDEX attachment_review_page ON attachment_reviews (org_id, attachment_revision_id, created_at, id)",
    "CREATE INDEX ix_attachment_reviews_org_id ON attachment_reviews (org_id)",
    "CREATE UNIQUE INDEX attachment_link_active_slot ON profile_attachment_links (org_id, profile_revision_id, field, attachment_revision_id) WHERE active",
    "CREATE INDEX attachment_link_page ON profile_attachment_links (org_id, profile_revision_id, created_at, id)",
    "CREATE INDEX ix_profile_attachment_links_org_id ON profile_attachment_links (org_id)",
    "CREATE UNIQUE INDEX attachment_selection_active_slot ON task_attachments (org_id, task_id, task_org_profile_id, attachment_id) WHERE active",
    "CREATE INDEX attachment_selection_page ON task_attachments (org_id, task_id, created_at, id)",
    "CREATE INDEX ix_task_attachments_org_id ON task_attachments (org_id)",
    "CREATE INDEX attachment_hold_page ON attachment_privacy_holds (org_id, evidence_source_id, created_at, id)",
    "CREATE INDEX ix_attachment_privacy_holds_org_id ON attachment_privacy_holds (org_id)",
)

PARENT_SQL = r"""
ALTER TABLE task_org_profiles ADD CONSTRAINT attachment_task_profile_binding UNIQUE(org_id,id,task_id,profile_revision_id,lot);
ALTER TABLE evidence_sources ADD CONSTRAINT attachment_source_task UNIQUE(org_id,task_id,id);
ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_attachment_scopes CHECK(NOT (scopes ?| ARRAY['attachment\:write','attachment\:review','attachment\:manage','attachment\:original\:read','task\:attachment','attachment\:privacy','attachment\:page\:read']));
CREATE UNIQUE INDEX attachment_audit_request ON audit_logs(org_id,actor_user_id,action,(details->>'request_id'))
 WHERE action IN ('attachment.create','attachment.revise','attachment.assign','attachment.review.approve','attachment.review.reject','attachment.review.revoke','attachment.deactivate','profile.attachment.link','profile.attachment.deactivate','task.attachment.select','task.attachment.deactivate','attachment.source.create','attachment.privacy.clear','attachment.privacy.hold','attachment.privacy.withdraw') AND details->>'request_id' IS NOT NULL;
CREATE INDEX attachment_audit_revision ON audit_logs(org_id,resource_revision_id_text,object_id)
 WHERE action IN ('attachment.create','attachment.revise');
"""

SOURCE_SQL = r"""
ALTER TABLE attachment_archives ADD CONSTRAINT attachment_current_revision FOREIGN KEY(org_id,id,current_revision) REFERENCES attachment_revisions(org_id,attachment_id,revision) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE evidence_sources
 ADD COLUMN source_kind varchar(40) NOT NULL DEFAULT 'user_supplied_certificate_pdf',
 ADD COLUMN task_attachment_id uuid, ADD COLUMN task_org_profile_id uuid,
 ADD COLUMN profile_revision_id uuid, ADD COLUMN profile_attachment_link_id uuid,
 ADD COLUMN attachment_id uuid, ADD COLUMN attachment_revision_id uuid,
 ADD COLUMN attachment_file_id uuid, ADD COLUMN approval_id uuid,
 ALTER COLUMN task_certificate_id DROP NOT NULL, ALTER COLUMN certificate_id DROP NOT NULL,
 ALTER COLUMN certificate_revision_id DROP NOT NULL, ALTER COLUMN certificate_file_id DROP NOT NULL;
ALTER TABLE evidence_sources ADD CONSTRAINT attachment_source_branches CHECK(
 (source_kind='user_supplied_certificate_pdf' AND num_nonnulls(task_certificate_id,certificate_id,certificate_revision_id,certificate_file_id)=4 AND num_nonnulls(task_attachment_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,attachment_file_id,approval_id)=0)
 OR (source_kind='user_supplied_attachment_pdf' AND num_nonnulls(task_certificate_id,certificate_id,certificate_revision_id,certificate_file_id)=0 AND num_nonnulls(task_attachment_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,attachment_file_id,approval_id)=8));
ALTER TABLE evidence_sources ADD CONSTRAINT attachment_source_exact_selection FOREIGN KEY(org_id,task_attachment_id,task_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,attachment_file_id,approval_id)
 REFERENCES task_attachments(org_id,id,task_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,file_id,approval_id);
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='evidence_sources'::regclass
 AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (org_id, task_certificate_id, page, render_profile)' LOOP
  EXECUTE format('ALTER TABLE evidence_sources DROP CONSTRAINT %I',c.conname);
 END LOOP;
END $$;
CREATE UNIQUE INDEX certificate_source_identity ON evidence_sources(org_id,task_certificate_id,page,render_profile) WHERE source_kind='user_supplied_certificate_pdf';
CREATE UNIQUE INDEX attachment_source_identity ON evidence_sources(org_id,task_attachment_id,page,render_profile) WHERE source_kind='user_supplied_attachment_pdf';
CREATE INDEX attachment_source_page ON evidence_sources(org_id,task_id,created_at,id) WHERE source_kind='user_supplied_attachment_pdf';
"""

GUARD_SQL = r"""
-- PostgreSQL requires UPDATE privilege for SELECT FOR UPDATE; the immutable
-- trigger allows page-level serialization without allowing byte/lineage mutation.
GRANT UPDATE ON evidence_sources TO bid_app;
CREATE TRIGGER attachment_source_immutable BEFORE UPDATE OR DELETE ON evidence_sources FOR EACH ROW EXECUTE FUNCTION response_immutable_gate();
CREATE INDEX attachment_selection_archive_tasks ON task_attachments(org_id,attachment_id,task_id);
CREATE INDEX attachment_selection_link_tasks ON task_attachments(org_id,profile_attachment_link_id,task_id);
CREATE INDEX attachment_review_latest ON attachment_reviews(org_id,attachment_revision_id,reviewed_at DESC,id DESC);
CREATE INDEX attachment_hold_latest ON attachment_privacy_holds(org_id,evidence_source_id,reviewed_at DESC,id DESC);
CREATE UNIQUE INDEX attachment_review_prior ON attachment_reviews(org_id,attachment_revision_id,prior_review_id) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX attachment_hold_prior ON attachment_privacy_holds(org_id,evidence_source_id,prior_hold_id) NULLS NOT DISTINCT;
CREATE FUNCTION public.attachment_human(p_org uuid,p_actor uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
 OR nullif(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR p_actor IS DISTINCT FROM nullif(current_setting('app.actor_user_id',true),'')::uuid
 OR p_org IS DISTINCT FROM nullif(current_setting('app.current_org',true),'')::uuid
 OR NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=p_actor
 AND m.active AND u.active AND o.active AND m.role IN ('admin','bidder')) THEN
  RAISE EXCEPTION 'Human attachment authority required' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION public.attachment_task_writer(p_org uuid,p_task uuid,p_actor uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM public.attachment_human(p_org,p_actor);
 PERFORM 1 FROM public.tasks WHERE org_id=p_org AND id=p_task FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.task_members tm JOIN public.task_workflows w
 ON (w.org_id,w.task_id)=(tm.org_id,tm.task_id)
 WHERE tm.org_id=p_org AND tm.task_id=p_task AND tm.user_id=p_actor AND tm.active
 AND tm.role IN ('owner','contributor') AND w.state='active') THEN
  RAISE EXCEPTION 'Active task contributor required' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION public.attachment_approval_current(p_org uuid,p_approval uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.attachment_reviews r JOIN public.attachment_archives a
 ON (a.org_id,a.id)=(r.org_id,r.attachment_id)
 WHERE r.org_id=p_org AND r.id=p_approval AND r.decision='approve' AND a.active
 AND NOT EXISTS(SELECT 1 FROM public.attachment_reviews n WHERE n.org_id=r.org_id AND n.prior_review_id=r.id))
$$;
CREATE FUNCTION public.attachment_selection_current(p_org uuid,p_selection uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.task_attachments s
 JOIN public.task_org_profiles p ON (p.org_id,p.id)=(s.org_id,s.task_org_profile_id)
 JOIN public.profile_attachment_links l ON (l.org_id,l.id)=(s.org_id,s.profile_attachment_link_id)
 WHERE s.org_id=p_org AND s.id=p_selection AND s.active AND p.active AND l.active
 AND public.attachment_approval_current(s.org_id,s.approval_id))
$$;
CREATE FUNCTION public.attachment_root_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE actor uuid := nullif(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human(NEW.org_id,actor);
 IF TG_OP='INSERT' THEN
  IF NEW.current_revision<>1 OR NEW.state_version<>1 OR NOT NEW.active OR NEW.created_by<>actor OR NEW.custodian_user_id<>actor THEN
   RAISE EXCEPTION 'Invalid initial archive state' USING ERRCODE='23514'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['current_revision','state_version','active','custodian_user_id','reviewer_user_id']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['current_revision','state_version','active','custodian_user_id','reviewer_user_id'])
   OR NOT OLD.active OR NEW.state_version<>OLD.state_version+1
   OR NEW.current_revision NOT IN (OLD.current_revision,OLD.current_revision+1) THEN
   RAISE EXCEPTION 'Invalid archive state transition' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_OP='INSERT' OR (NEW.custodian_user_id,NEW.reviewer_user_id,NEW.current_revision) IS DISTINCT FROM (OLD.custodian_user_id,OLD.reviewer_user_id,OLD.current_revision) THEN
  IF NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id WHERE m.org_id=NEW.org_id AND m.user_id=NEW.custodian_user_id AND m.active AND u.active AND m.role IN ('admin','bidder'))
   OR NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id WHERE m.org_id=NEW.org_id AND m.user_id=NEW.reviewer_user_id AND m.active AND u.active AND m.role IN ('admin','bidder')) THEN
   RAISE EXCEPTION 'Live custodian and reviewer required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_root_guard AFTER INSERT OR UPDATE OR DELETE ON attachment_archives FOR EACH ROW EXECUTE FUNCTION attachment_root_guard();

CREATE FUNCTION public.attachment_revision_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE root public.attachment_archives%ROWTYPE; parent_xmin xid;
BEGIN
 IF TG_TABLE_NAME='attachment_revisions' THEN
  PERFORM public.attachment_human(NEW.org_id,NEW.created_by);
  SELECT * INTO root FROM public.attachment_archives WHERE org_id=NEW.org_id AND id=NEW.attachment_id FOR UPDATE;
  IF NOT root.active OR NEW.revision<>root.current_revision THEN
   RAISE EXCEPTION 'Revision requires exact active root pointer' USING ERRCODE='23514'; END IF;
 ELSE
  PERFORM public.attachment_human(NEW.org_id,NEW.created_by);
  SELECT xmin INTO parent_xmin FROM public.attachment_revisions WHERE org_id=NEW.org_id AND id=NEW.attachment_revision_id;
  IF parent_xmin IS DISTINCT FROM pg_current_xact_id()::xid THEN
   RAISE EXCEPTION 'File requires a new revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_revision_guard AFTER INSERT ON attachment_revisions FOR EACH ROW EXECUTE FUNCTION attachment_revision_guard();
CREATE TRIGGER zz_attachment_file_guard AFTER INSERT ON attachment_files FOR EACH ROW EXECUTE FUNCTION attachment_revision_guard();
CREATE FUNCTION public.attachment_file_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.org_id=NEW.org_id AND f.attachment_revision_id=NEW.id AND f.attachment_id=NEW.attachment_id) THEN
  RAISE EXCEPTION 'Committed attachment revision requires its file' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER attachment_file_complete AFTER INSERT ON attachment_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION attachment_file_complete();
CREATE FUNCTION public.attachment_parts_disabled() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'attachment_upload_mode_not_enabled' USING ERRCODE='23514'; END $$;
CREATE TRIGGER zz_attachment_parts_disabled AFTER INSERT ON attachment_file_parts FOR EACH ROW EXECUTE FUNCTION attachment_parts_disabled();

CREATE FUNCTION public.attachment_review_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE root public.attachment_archives%ROWTYPE; prior public.attachment_reviews%ROWTYPE;
BEGIN
 PERFORM public.attachment_human(NEW.org_id,NEW.reviewed_by);
 SELECT * INTO root FROM public.attachment_archives WHERE org_id=NEW.org_id AND id=NEW.attachment_id FOR UPDATE;
 IF NOT root.active OR (NEW.decision<>'revoke' AND NEW.reviewed_by<>root.reviewer_user_id) THEN
  RAISE EXCEPTION 'Assigned live archive reviewer required' USING ERRCODE='42501'; END IF;
 SELECT r.* INTO prior FROM public.attachment_reviews r WHERE r.org_id=NEW.org_id AND r.attachment_revision_id=NEW.attachment_revision_id AND r.id<>NEW.id
 AND NOT EXISTS(SELECT 1 FROM public.attachment_reviews n WHERE n.org_id=r.org_id AND n.prior_review_id=r.id AND n.id<>NEW.id);
 IF NEW.prior_review_id IS DISTINCT FROM prior.id
 OR (NEW.decision='approve' AND prior.decision='approve')
 OR (NEW.decision='reject' AND prior.id IS NOT NULL)
 OR (NEW.decision='revoke' AND prior.decision IS DISTINCT FROM 'approve') THEN
  RAISE EXCEPTION 'Archive review transition conflict' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.attachment_files f JOIN public.attachment_revisions r ON (r.org_id,r.id)=(f.org_id,f.attachment_revision_id)
  WHERE f.org_id=NEW.org_id AND f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256 AND r.metadata_sha256=NEW.metadata_sha256) THEN
  RAISE EXCEPTION 'Archive review hash mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_review_guard AFTER INSERT ON attachment_reviews FOR EACH ROW EXECUTE FUNCTION attachment_review_guard();

CREATE FUNCTION public.attachment_link_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE actor uuid := nullif(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human(NEW.org_id,actor);
 IF TG_TABLE_NAME='task_attachments' THEN PERFORM public.attachment_task_writer(NEW.org_id,NEW.task_id,actor); END IF;
 PERFORM 1 FROM public.attachment_archives WHERE org_id=NEW.org_id AND id=NEW.attachment_id FOR UPDATE;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['active','state_version']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active','state_version'])
   OR NOT OLD.active OR NEW.active OR NEW.state_version<>OLD.state_version+1 THEN
   RAISE EXCEPTION 'Only one-way deactivation is permitted' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NOT NEW.active OR NEW.state_version<>1 OR NOT public.attachment_approval_current(NEW.org_id,NEW.approval_id) THEN
  RAISE EXCEPTION 'Current exact archive approval required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='profile_attachment_links' THEN
  IF NEW.created_by<>actor OR NOT EXISTS(SELECT 1 FROM public.org_profile_revisions r
   WHERE r.org_id=NEW.org_id AND r.id=NEW.profile_revision_id AND length(btrim(r.data->>NEW.field))>0) THEN
   RAISE EXCEPTION 'Exact nonempty declaration required' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.selected_by<>actor OR NOT public.attachment_selection_current(NEW.org_id,NEW.id)
   OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.org_id=NEW.org_id AND f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256) THEN
   RAISE EXCEPTION 'Exact active task profile and declaration required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_link_guard AFTER INSERT OR UPDATE OR DELETE ON profile_attachment_links FOR EACH ROW EXECUTE FUNCTION attachment_link_guard();
CREATE TRIGGER zz_attachment_selection_guard AFTER INSERT OR UPDATE OR DELETE ON task_attachments FOR EACH ROW EXECUTE FUNCTION attachment_link_guard();

CREATE FUNCTION public.attachment_source_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.source_kind<>'user_supplied_attachment_pdf' THEN RETURN NEW; END IF;
 PERFORM public.attachment_task_writer(NEW.org_id,NEW.task_id,NEW.created_by);
 PERFORM 1 FROM public.attachment_archives WHERE org_id=NEW.org_id AND id=NEW.attachment_id FOR UPDATE;
 IF NOT public.attachment_selection_current(NEW.org_id,NEW.task_attachment_id)
 OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.org_id=NEW.org_id AND f.id=NEW.attachment_file_id AND NEW.page<=(f.file->>'page_count')::integer) THEN
  RAISE EXCEPTION 'Attachment source parents are unavailable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_source_guard AFTER INSERT ON evidence_sources FOR EACH ROW EXECUTE FUNCTION attachment_source_guard();
CREATE FUNCTION public.attachment_privacy_actor(p_org uuid,p_task uuid,p_source uuid,p_actor uuid,p_sha text) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE src public.evidence_sources%ROWTYPE; root public.attachment_archives%ROWTYPE;
BEGIN
 PERFORM public.attachment_task_writer(p_org,p_task,p_actor);
 SELECT * INTO src FROM public.evidence_sources WHERE org_id=p_org AND id=p_source;
 SELECT * INTO root FROM public.attachment_archives WHERE org_id=p_org AND id=src.attachment_id FOR UPDATE;
 PERFORM 1 FROM public.evidence_sources WHERE org_id=p_org AND id=p_source FOR UPDATE;
 IF src.source_kind IS DISTINCT FROM 'user_supplied_attachment_pdf' OR src.task_id IS DISTINCT FROM p_task
 OR src.preview->>'sha256' IS DISTINCT FROM p_sha OR root.reviewer_user_id IS DISTINCT FROM p_actor
 OR NOT public.attachment_selection_current(p_org,src.task_attachment_id) THEN
  RAISE EXCEPTION 'Exact attachment page and assigned reviewer required' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.attachment_hold_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE prior_id uuid;
BEGIN
 PERFORM public.attachment_privacy_actor(NEW.org_id,NEW.task_id,NEW.evidence_source_id,NEW.reviewed_by,NEW.source_png_sha256);
 SELECT h.id INTO prior_id FROM public.attachment_privacy_holds h WHERE h.org_id=NEW.org_id AND h.evidence_source_id=NEW.evidence_source_id AND h.id<>NEW.id
 AND NOT EXISTS(SELECT 1 FROM public.attachment_privacy_holds n WHERE n.org_id=h.org_id AND n.prior_hold_id=h.id AND n.id<>NEW.id);
 IF NEW.prior_hold_id IS DISTINCT FROM prior_id THEN RAISE EXCEPTION 'Privacy hold transition conflict' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_hold_guard AFTER INSERT ON attachment_privacy_holds FOR EACH ROW EXECUTE FUNCTION attachment_hold_guard();
CREATE FUNCTION public.attachment_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE encrypted_column text;
BEGIN
 -- Only the owning maintenance connection can rewrap record-bound ciphertext;
 -- every business field and identity remains byte-for-byte unchanged.
 encrypted_column := CASE TG_TABLE_NAME
  WHEN 'attachment_revisions' THEN 'label_encrypted'
  WHEN 'attachment_files' THEN 'upload_name_encrypted'
  WHEN 'attachment_file_parts' THEN 'upload_name_encrypted'
  ELSE NULL END;
 IF TG_OP='UPDATE' AND encrypted_column IS NOT NULL
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_column)=(to_jsonb(OLD)-encrypted_column) THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'Attachment history is immutable' USING ERRCODE='42501';
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['attachment_revisions','attachment_files','attachment_file_parts','attachment_reviews','attachment_privacy_holds'] LOOP
  EXECUTE format('CREATE TRIGGER attachment_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate()',tab);
 END LOOP;
END $$;
"""

SCREENSHOT_SQL = r"""
CREATE INDEX attachment_asset_latest ON screenshot_assets(org_id,evidence_source_id,received_at DESC,id DESC) WHERE source_kind='attachment_page';
ALTER TABLE screenshot_privacy_reviews ADD COLUMN attachment_source_id uuid, ADD COLUMN resolved_hold_id uuid;
ALTER TABLE screenshot_privacy_reviews ADD CONSTRAINT attachment_privacy_source FOREIGN KEY(org_id,task_id,attachment_source_id) REFERENCES evidence_sources(org_id,task_id,id);
ALTER TABLE screenshot_privacy_reviews ADD CONSTRAINT attachment_privacy_hold FOREIGN KEY(org_id,task_id,attachment_source_id,resolved_hold_id) REFERENCES attachment_privacy_holds(org_id,task_id,evidence_source_id,id);
ALTER TABLE screenshot_privacy_reviews ADD CONSTRAINT attachment_privacy_branch CHECK((resolved_hold_id IS NULL OR attachment_source_id IS NOT NULL) AND (attachment_source_id IS NULL OR annotation_request_id IS NULL));
ALTER TABLE screenshot_assets ADD CONSTRAINT attachment_asset_source_task FOREIGN KEY(org_id,task_id,evidence_source_id) REFERENCES evidence_sources(org_id,task_id,id);
CREATE FUNCTION public.attachment_screenshot_asset_guard(p_org uuid,p_asset uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE a public.screenshot_assets%ROWTYPE; src public.evidence_sources%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=p_org AND id=p_asset;
 SELECT * INTO src FROM public.evidence_sources WHERE org_id=p_org AND id=a.evidence_source_id;
 PERFORM public.attachment_privacy_actor(p_org,a.task_id,a.evidence_source_id,a.received_by,a.source_sha256);
 IF a.origin<>'attachment' OR a.source_kind<>'attachment_page' OR a.image_kind<>'attachment_page'
 OR a.source_hash_assurance<>'server_verified' OR a.source->>'kind' IS DISTINCT FROM 'attachment_page'
 OR a.source->>'evidence_source_id' IS DISTINCT FROM src.id::text
 OR a.source ? 'annotation_request_id'
 OR num_nonnulls(a.task_feature_id,a.feature_revision_id,a.task_resource_id,a.product_revision_id,a.vendor_archive_id,a.prototype_run_id)<>0
 OR a.source_width<>(src.preview->>'width_px')::integer OR a.source_height<>(src.preview->>'height_px')::integer
 OR EXISTS(SELECT 1 FROM public.attachment_privacy_holds h WHERE h.org_id=p_org AND h.evidence_source_id=src.id) THEN
  RAISE EXCEPTION 'Invalid attachment privacy source or unresolved hold' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.attachment_screenshot_privacy_guard(p_org uuid,p_review uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.screenshot_privacy_reviews%ROWTYPE; a public.screenshot_assets%ROWTYPE; r public.screenshot_renditions%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.screenshot_privacy_reviews WHERE org_id=p_org AND id=p_review;
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=p_org AND id=p.asset_id;
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=p_org AND id=p.rendition_id;
 PERFORM public.attachment_privacy_actor(p_org,p.task_id,p.attachment_source_id,p.reviewed_by,p.reviewed_upload_sha256);
 IF a.source_kind IS DISTINCT FROM 'attachment_page' OR a.evidence_source_id IS DISTINCT FROM p.attachment_source_id
 OR p.annotation_request_id IS NOT NULL OR p.resolved_hold_id IS NOT NULL
 OR p.rule_version<>'screenshot-privacy-v1' OR r.parent_rendition_id IS NOT NULL
 OR r.privacy_review_id IS DISTINCT FROM p.id OR r.asset_id IS DISTINCT FROM p.asset_id
 OR r.image_sha256 IS DISTINCT FROM p.stored_image_sha256 OR r.upload_sha256 IS DISTINCT FROM p.reviewed_upload_sha256
 OR r.profile IS DISTINCT FROM 'screenshot-markup-v1' OR r.plan->'redact' IS DISTINCT FROM '[]'::jsonb OR r.plan->'boxes' IS DISTINCT FROM '[]'::jsonb
 OR (r.mapping->'crop'->>'x')::integer<>0 OR (r.mapping->'crop'->>'y')::integer<>0
 OR (r.mapping->>'content_width')::integer<>a.source_width OR (r.mapping->>'content_height')::integer<>a.source_height
 OR EXISTS(SELECT 1 FROM public.attachment_privacy_holds h WHERE h.org_id=p_org AND h.evidence_source_id=p.attachment_source_id) THEN
  RAISE EXCEPTION 'Attachment privacy requires exact unchanged page without hold' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.attachment_screenshot_rendition_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE a public.screenshot_assets%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=NEW.org_id AND id=NEW.asset_id;
 IF a.source_kind<>'attachment_page' THEN RETURN NEW; END IF;
 PERFORM public.attachment_privacy_actor(NEW.org_id,NEW.task_id,a.evidence_source_id,NEW.actor_user_id,NEW.source_sha256);
 IF NEW.parent_rendition_id IS NOT NULL OR NEW.generation_job_id IS NOT NULL
 OR NEW.profile<>'screenshot-markup-v1' OR NEW.upload_sha256<>a.source_sha256
 OR NEW.plan->'redact' IS DISTINCT FROM '[]'::jsonb OR NEW.plan->'boxes' IS DISTINCT FROM '[]'::jsonb
 OR (NEW.mapping->'crop'->>'x')::integer<>0 OR (NEW.mapping->'crop'->>'y')::integer<>0
 OR (NEW.mapping->>'content_width')::integer<>a.source_width OR (NEW.mapping->>'content_height')::integer<>a.source_height THEN
  RAISE EXCEPTION 'attachment_redaction_not_enabled' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_screenshot_rendition AFTER INSERT ON screenshot_renditions FOR EACH ROW EXECUTE FUNCTION attachment_screenshot_rendition_guard();
CREATE FUNCTION public.attachment_screenshot_withdraw_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE a public.screenshot_assets%ROWTYPE; reviewer uuid;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=NEW.org_id AND id=NEW.asset_id;
 IF a.source_kind<>'attachment_page' THEN RETURN NEW; END IF;
 PERFORM public.attachment_task_writer(NEW.org_id,NEW.task_id,NEW.withdrawn_by);
 SELECT r.reviewer_user_id INTO reviewer FROM public.attachment_archives r
 JOIN public.evidence_sources s ON (s.org_id,s.attachment_id)=(r.org_id,r.id)
 WHERE s.org_id=NEW.org_id AND s.id=a.evidence_source_id;
 IF reviewer IS DISTINCT FROM NEW.withdrawn_by THEN
  RAISE EXCEPTION 'Assigned attachment privacy reviewer required' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_screenshot_withdraw AFTER INSERT ON screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION attachment_screenshot_withdraw_guard();
CREATE FUNCTION public.attachment_screenshot_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE a public.screenshot_assets%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=NEW.org_id AND id=NEW.asset_id;
 IF (a.source_kind='attachment_page') IS DISTINCT FROM (NEW.attachment_source_id IS NOT NULL) THEN
  RAISE EXCEPTION 'Screenshot review attachment branch mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.attachment_source_id IS NOT NULL THEN PERFORM public.attachment_screenshot_privacy_guard(NEW.org_id,NEW.id); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_attachment_screenshot_review AFTER INSERT ON screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION attachment_screenshot_binding_guard();
"""


def _patch_screenshot_guards():
    # Preserve the actual preceding definition, including B05 certificate guards.
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef('public.screenshot_asset_gate()'::regprocedure)")
        )
        .scalar_one()
    )
    if definition.count("BEGIN\n") != 1:
        raise RuntimeError("Expected screenshot asset guard entry point")
    definition = definition.replace(
        "BEGIN\n",
        """BEGIN
  IF NEW.source_kind='attachment_page' THEN
    PERFORM public.attachment_screenshot_asset_guard(NEW.org_id,NEW.id);
    RETURN NEW;
  END IF;
""",
        1,
    )
    _execute_definition(definition)
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef('public.screenshot_privacy_gate()'::regprocedure)")
        )
        .scalar_one()
    )
    if definition.count("BEGIN\n") != 1:
        raise RuntimeError("Expected screenshot privacy guard entry point")
    definition = definition.replace(
        "BEGIN\n",
        """BEGIN
  IF NEW.attachment_source_id IS NOT NULL THEN
    PERFORM public.attachment_screenshot_privacy_guard(NEW.org_id,NEW.id);
    RETURN NEW;
  END IF;
""",
        1,
    )
    _execute_definition(definition)


def _execute_definition(definition):
    # pg_get_functiondef returns ordinary SQL, while Alembic feeds a string to
    # SQLAlchemy text(). Escape only a possible bind-start colon; compilation
    # removes this escape, so SQL literals and POSIX classes stay unchanged.
    statement = re.sub(
        r"\[\[:[A-Za-z]+:\]\]|(?<![:\\]):(?=[A-Za-z_])",
        lambda match: match.group() if match.group().startswith("[[") else "\\:",
        definition,
    )
    op.execute(statement)
