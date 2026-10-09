"""Frozen uploaded-bid reports and human-only immutable artifact pairs."""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None
TABLES = ("bid_review_report_snapshots", "bid_review_report_artifacts")


def upgrade():
    for statement in TABLE_SQL:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy = "org_id = NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO bid_app")
        op.execute(f"CREATE INDEX ix_{table}_org_id ON {table}(org_id)")
    op.execute(GUARD_SQL)


def downgrade():
    raise RuntimeError("Disable report admission and repair forward; retain immutable snapshots")


TABLE_SQL = (
    r"""
CREATE TABLE bid_review_report_snapshots (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	publication_id UUID NOT NULL,
	job_id UUID NOT NULL,
	created_by UUID NOT NULL,
	request_id UUID NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	report_input_hash VARCHAR(64) NOT NULL,
	decisions_snapshot_sha256 VARCHAR(64) NOT NULL,
	renderer_identity TEXT NOT NULL,
	details_sha256 VARCHAR(64) NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, job_id) REFERENCES jobs (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id, publication_id) REFERENCES bid_review_publications (org_id, task_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, job_id),
	UNIQUE (org_id, created_by, request_id),
	UNIQUE (org_id, task_id, submission_id, review_id, id),
	CHECK (input_hash ~ '^[0-9a-f]{64}$' AND report_input_hash ~ '^[0-9a-f]{64}$' AND decisions_snapshot_sha256 ~ '^[0-9a-f]{64}$' AND details_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (length(details_encrypted)>0 AND length(renderer_identity)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
    r"""
CREATE TABLE bid_review_report_artifacts (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	snapshot_id UUID NOT NULL,
	run_id UUID NOT NULL,
	format VARCHAR(10) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes INTEGER NOT NULL,
	storage_key TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, snapshot_id) REFERENCES bid_review_report_snapshots (org_id, task_id, submission_id, review_id, id),
	UNIQUE (org_id, snapshot_id, format),
	CHECK (format IN ('docx','console') AND sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 104857600),
	CHECK (storage_key LIKE 'org/' || org_id::text || '/bid-review/' || submission_id::text || '/reports/' || snapshot_id::text || '/%'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""",
)

GUARD_SQL = r"""
ALTER TABLE jobs DROP CONSTRAINT bid_review_uploaded_document;
ALTER TABLE jobs ADD CONSTRAINT bid_review_uploaded_document
 CHECK((kind IN ('bid_review','bid_review_report'))=(bid_submission_document_id IS NOT NULL));
ALTER TABLE jobs DROP CONSTRAINT job_document_binding;
ALTER TABLE jobs ADD CONSTRAINT job_document_binding CHECK(
 (kind='provider_test' AND task_id IS NULL AND document_id IS NULL)
 OR (kind='bid_review_prepare' AND task_id IS NOT NULL AND document_id IS NULL)
 OR (kind IN ('bid_review','bid_review_report') AND task_id IS NOT NULL AND document_id IS NULL AND bid_submission_document_id IS NOT NULL)
 OR (kind NOT IN ('provider_test','bid_review_prepare','bid_review','bid_review_report') AND task_id IS NOT NULL AND document_id IS NOT NULL));
ALTER TABLE jobs ADD CONSTRAINT bid_review_report_actor CHECK(kind<>'bid_review_report' OR
 (actor_user_id IS NOT NULL AND actor_kind='session' AND actor_token_id IS NULL AND agent_principal_id IS NULL));
CREATE FUNCTION public.bid_review_report_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='bid_review_report_snapshots'
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Report snapshots and artifacts are immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER bid_review_report_immutable BEFORE UPDATE OR DELETE ON public.bid_review_report_snapshots
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_immutable();
CREATE TRIGGER bid_review_report_immutable BEFORE UPDATE OR DELETE ON public.bid_review_report_artifacts
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_immutable();
CREATE FUNCTION public.bid_review_report_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE frozen public.bid_review_report_snapshots%ROWTYPE;
 job_row public.jobs%ROWTYPE; member public.memberships%ROWTYPE;
 run_row public.bid_review_runs%ROWTYPE; publication public.bid_review_publications%ROWTYPE;
 actor_id uuid; report_scope text;
BEGIN
 IF TG_TABLE_NAME='bid_review_report_snapshots' THEN
  frozen := NEW;
 ELSE
  SELECT * INTO frozen FROM public.bid_review_report_snapshots WHERE org_id=NEW.org_id AND id=NEW.snapshot_id;
 END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=frozen.job_id;
 SELECT * INTO run_row FROM public.bid_review_runs WHERE org_id=NEW.org_id AND id=frozen.review_id;
 SELECT * INTO publication FROM public.bid_review_publications WHERE org_id=NEW.org_id AND id=frozen.publication_id;
 SELECT * INTO member FROM public.memberships WHERE org_id=NEW.org_id AND user_id=frozen.created_by;
 actor_id := NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 report_scope := 'bid-review\:report\:render';
 IF frozen.id IS NULL OR job_row.id IS NULL OR run_row.id IS NULL OR publication.id IS NULL
 OR (publication.org_id,publication.task_id,publication.submission_id,publication.review_id)
 IS DISTINCT FROM (frozen.org_id,frozen.task_id,frozen.submission_id,frozen.review_id)
 OR frozen.report_input_hash IS DISTINCT FROM run_row.input_hash
 OR job_row.kind IS DISTINCT FROM 'bid_review_report'
 OR (job_row.org_id,job_row.task_id,job_row.bid_submission_document_id,job_row.actor_user_id)
 IS DISTINCT FROM (frozen.org_id,frozen.task_id,run_row.tender_document_id,frozen.created_by)
 OR job_row.result->'submission'->>'snapshot_id' IS DISTINCT FROM frozen.id::text
 OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM frozen.input_hash
 OR job_row.actor_kind IS DISTINCT FROM 'session' OR job_row.actor_token_id IS NOT NULL
 OR job_row.agent_principal_id IS NOT NULL OR NOT (job_row.actor_scopes ? report_scope)
 OR member.id IS NULL OR NOT member.active OR member.role NOT IN ('admin','bidder')
 OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=frozen.created_by AND active)
 OR NOT EXISTS(SELECT 1 FROM public.orgs WHERE id=NEW.org_id AND active)
 OR NOT EXISTS(SELECT 1 FROM public.task_workflows WHERE org_id=NEW.org_id AND task_id=frozen.task_id)
 OR (member.role<>'admin' AND NOT EXISTS(SELECT 1 FROM public.task_members WHERE org_id=NEW.org_id AND task_id=frozen.task_id AND user_id=frozen.created_by AND active))
 OR NOT EXISTS(SELECT 1 FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=frozen.submission_id AND state='uploaded')
 OR actor_id IS DISTINCT FROM frozen.created_by
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? report_scope)
 THEN RAISE EXCEPTION 'Report publication requires current human task authority' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_report_snapshots' THEN
  IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session' OR job_row.status<>'queued' THEN
   RAISE EXCEPTION 'Report snapshot requires human admission' USING ERRCODE='42501'; END IF;
 ELSE
  IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
  OR job_row.status<>'running' OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR NEW.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
  OR job_row.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
  OR job_row.lease_until IS NULL OR job_row.lease_until<=now()
  THEN RAISE EXCEPTION 'Report artifact requires live worker ownership' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER bid_review_report_insert_guard BEFORE INSERT ON public.bid_review_report_snapshots
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_insert_guard();
CREATE TRIGGER bid_review_report_insert_guard BEFORE INSERT ON public.bid_review_report_artifacts
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_insert_guard();
CREATE FUNCTION public.bid_review_report_pair_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE frozen public.bid_review_report_snapshots%ROWTYPE; job_row public.jobs%ROWTYPE;
BEGIN
 SELECT * INTO frozen FROM public.bid_review_report_snapshots WHERE org_id=NEW.org_id AND id=NEW.snapshot_id;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=frozen.job_id;
 IF (SELECT count(*) FROM public.bid_review_report_artifacts WHERE org_id=NEW.org_id AND snapshot_id=NEW.snapshot_id)<>2
 OR job_row.status<>'succeeded' OR job_row.run_id IS DISTINCT FROM NEW.run_id
 OR EXISTS(SELECT 1 FROM public.bid_review_report_artifacts artifact_row WHERE artifact_row.org_id=NEW.org_id
 AND artifact_row.snapshot_id=NEW.snapshot_id AND artifact_row.run_id IS DISTINCT FROM NEW.run_id)
 THEN RAISE EXCEPTION 'Word and console report artifacts must publish together' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_review_report_pair_complete AFTER INSERT ON public.bid_review_report_artifacts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_pair_complete();
CREATE FUNCTION public.bid_review_report_job_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.kind='bid_review_report' AND (NEW.kind IS DISTINCT FROM OLD.kind
 OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.document_id IS DISTINCT FROM OLD.document_id
 OR NEW.bid_submission_document_id IS DISTINCT FROM OLD.bid_submission_document_id
 OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id OR NEW.actor_token_id IS DISTINCT FROM OLD.actor_token_id
 OR NEW.actor_kind IS DISTINCT FROM OLD.actor_kind OR NEW.actor_scopes IS DISTINCT FROM OLD.actor_scopes
 OR NEW.result->'submission' IS DISTINCT FROM OLD.result->'submission'
 OR (OLD.status='succeeded' AND NEW.status IS DISTINCT FROM OLD.status)) THEN
 RAISE EXCEPTION 'Report job source and authority are immutable' USING ERRCODE='23514'; END IF;
 IF NEW.kind='bid_review_report' AND NEW.status='succeeded' AND
 NOT EXISTS(SELECT 1 FROM public.bid_review_report_snapshots frozen WHERE frozen.org_id=NEW.org_id AND frozen.job_id=NEW.id
 AND 2=(SELECT count(*) FROM public.bid_review_report_artifacts artifact_row WHERE artifact_row.org_id=NEW.org_id AND artifact_row.snapshot_id=frozen.id)) THEN
 RAISE EXCEPTION 'Succeeded report requires both immutable artifacts' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER bid_review_report_job_guard BEFORE UPDATE ON public.jobs
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_job_guard();
"""
