"""Privacy-gated review jobs and immutable cited outputs."""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

TABLES = (
    "bid_review_runs",
    "bid_review_obligations",
    "bid_review_signing_requirements",
    "bid_review_required_locations",
    "bid_review_publications",
)


def upgrade():
    op.execute(PARENT_SQL)
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
    raise RuntimeError(
        "Disable review admission and repair forward; retain encrypted results and charges"
    )


TABLE_SQL = (
    r"""

CREATE TABLE bid_review_runs (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	tender_document_id UUID NOT NULL,
	authorization_id UUID NOT NULL,
	job_id UUID NOT NULL,
	request_id UUID NOT NULL,
	created_by UUID NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	manifest JSONB NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	UNIQUE (org_id, task_id, submission_id, id),
	UNIQUE (org_id, job_id),
	UNIQUE (org_id, created_by, request_id),
	FOREIGN KEY(org_id, task_id, job_id) REFERENCES jobs (org_id, task_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	FOREIGN KEY(org_id, task_id, authorization_id) REFERENCES bid_outbound_authorizations (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, tender_document_id) REFERENCES bid_submission_documents (org_id, task_id, submission_id, id),
	CHECK (input_hash ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_obligations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	page_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	UNIQUE (org_id, review_id, ordinal),
	CHECK (ordinal BETWEEN 1 AND 2000 AND length(details_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_signing_requirements (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	page_id UUID NOT NULL,
	candidate_id UUID,
 applicability VARCHAR(20) NOT NULL,
	ordinal INTEGER NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	UNIQUE (org_id, task_id, submission_id, review_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, candidate_id) REFERENCES bid_signing_candidates (org_id, task_id, id),
	UNIQUE (org_id, review_id, candidate_id),
 CHECK (applicability IN ('applies','not_applicable','alternative','unknown')),
	UNIQUE (org_id, review_id, ordinal),
	CHECK (ordinal BETWEEN 1 AND 10000 AND length(details_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_required_locations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	requirement_id UUID NOT NULL,
	page_id UUID,
	ordinal INTEGER NOT NULL,
	group_id VARCHAR(64),
	status VARCHAR(20) NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, requirement_id) REFERENCES bid_review_signing_requirements (org_id, task_id, submission_id, review_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	UNIQUE (org_id, review_id, ordinal),
	CHECK (ordinal BETWEEN 1 AND 10000 AND status='unresolved'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_publications (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	run_id UUID NOT NULL,
	completion VARCHAR(20) NOT NULL,
	coverage JSONB NOT NULL,
	uncovered_codes JSONB NOT NULL,
	output_hash VARCHAR(64) NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	UNIQUE (org_id, review_id),
	CHECK (completion IN ('complete','partial') AND output_hash ~ '^[0-9a-f]{64}$'),
 CHECK (jsonb_typeof(coverage)='object' AND coverage ?& ARRAY['obligations','signing_requirements','required_locations'] AND jsonb_typeof(uncovered_codes)='array' AND jsonb_array_length(uncovered_codes)<=100),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
)

PARENT_SQL = r"""
ALTER TABLE jobs ADD COLUMN bid_submission_document_id uuid;
ALTER TABLE jobs ADD CONSTRAINT bid_review_job_source FOREIGN KEY(org_id,task_id,bid_submission_document_id)
 REFERENCES bid_submission_documents(org_id,task_id,id);
ALTER TABLE jobs ADD CONSTRAINT bid_review_uploaded_document CHECK((kind='bid_review')=(bid_submission_document_id IS NOT NULL));
ALTER TABLE jobs DROP CONSTRAINT job_document_binding;
ALTER TABLE jobs ADD CONSTRAINT job_document_binding CHECK(
 (kind='provider_test' AND task_id IS NULL AND document_id IS NULL)
 OR (kind='bid_review_prepare' AND task_id IS NOT NULL AND document_id IS NULL)
 OR (kind='bid_review' AND task_id IS NOT NULL AND document_id IS NULL AND bid_submission_document_id IS NOT NULL)
 OR (kind NOT IN ('provider_test','bid_review_prepare','bid_review') AND task_id IS NOT NULL AND document_id IS NOT NULL));
"""

GUARD_SQL = r"""
CREATE FUNCTION public.bid_review_run_live(p_org uuid,p_task uuid,p_user uuid,p_token uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.orgs o ON o.id=m.org_id
 JOIN public.task_members tm ON (tm.org_id,tm.user_id)=(m.org_id,m.user_id)
 JOIN public.task_workflows w ON (w.org_id,w.task_id)=(tm.org_id,tm.task_id)
 WHERE m.org_id=p_org AND m.user_id=p_user AND m.active AND u.active AND o.active
 AND m.role IN ('admin','bidder','technical') AND tm.task_id=p_task AND tm.active
 AND tm.role IN ('owner','contributor') AND w.state='active')
 AND (p_token IS NULL OR EXISTS(SELECT 1 FROM public.api_tokens t WHERE t.org_id=p_org
 AND t.id=p_token AND t.user_id=p_user AND NOT t.revoked AND t.expires_at>clock_timestamp()
 AND t.scopes ? 'bid-review\:run'))
$$;
CREATE FUNCTION public.bid_review_run_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE j public.jobs%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 d public.bid_submission_documents%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
 SELECT * INTO g FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND id=NEW.authorization_id;
 SELECT * INTO d FROM public.bid_submission_documents WHERE org_id=NEW.org_id AND id=NEW.tender_document_id;
 IF j.id IS NULL OR j.kind<>'bid_review' OR j.task_id<>NEW.task_id
 OR j.bid_submission_document_id<>NEW.tender_document_id OR d.role<>'tender'
 OR g.id IS NULL OR NOT public.bid_review_actor_live(NEW.org_id,NEW.task_id,g.authorized_by)
 OR NOT EXISTS(SELECT 1 FROM public.task_members m WHERE m.org_id=NEW.org_id AND m.task_id=NEW.task_id AND m.user_id=g.authorized_by AND m.active AND m.role='owner')
 OR d.submission_id<>NEW.submission_id OR g.submission_id<>NEW.submission_id
 OR g.preparation_id<>NEW.preparation_id OR NOT g.allow_external
 OR j.actor_user_id IS DISTINCT FROM NEW.created_by
 OR NEW.created_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR COALESCE(current_setting('app.actor_kind',true),'') NOT IN ('session','token')
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT(j.actor_scopes ? 'bid-review\:run')
 OR NOT public.bid_review_run_live(NEW.org_id,NEW.task_id,NEW.created_by,j.actor_token_id)
 OR NEW.input_hash IS DISTINCT FROM j.result->'submission'->>'input_hash'
 OR NEW.manifest IS DISTINCT FROM j.result->'submission'->'input_manifest'
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.org_id=NEW.org_id
 AND a.submission_id=NEW.submission_id AND a.revision>g.revision) THEN
 RAISE EXCEPTION 'Review requires live exact outbound authority' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER bid_review_run_insert AFTER INSERT ON bid_review_runs
 FOR EACH ROW EXECUTE FUNCTION bid_review_run_guard();
CREATE FUNCTION public.bid_review_output_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.bid_review_runs%ROWTYPE; j public.jobs%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 p public.bid_document_pages%ROWTYPE; c public.bid_signing_candidates%ROWTYPE;
BEGIN
 SELECT * INTO r FROM public.bid_review_runs WHERE org_id=NEW.org_id AND id=NEW.review_id;
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=r.job_id FOR SHARE;
 SELECT * INTO g FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND id=r.authorization_id;
 IF r.id IS NULL OR g.id IS NULL OR NOT g.allow_external OR j.status IS DISTINCT FROM 'running' OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT public.bid_review_run_live(NEW.org_id,NEW.task_id,j.actor_user_id,j.actor_token_id)
 OR NOT public.bid_review_actor_live(NEW.org_id,NEW.task_id,g.authorized_by)
 OR NOT EXISTS(SELECT 1 FROM public.task_members m WHERE m.org_id=NEW.org_id AND m.task_id=NEW.task_id AND m.user_id=g.authorized_by AND m.active AND m.role='owner')
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.org_id=NEW.org_id AND a.submission_id=NEW.submission_id AND a.revision>g.revision)
 OR EXISTS(SELECT 1 FROM public.bid_review_publications WHERE org_id=NEW.org_id AND review_id=r.id) THEN
 RAISE EXCEPTION 'Live review attempt and grant required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_publications' THEN
  IF NEW.run_id<>j.run_id THEN RAISE EXCEPTION 'Review attempt mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.page_id IS NOT NULL THEN
   SELECT * INTO p FROM public.bid_document_pages WHERE org_id=NEW.org_id AND id=NEW.page_id;
   IF p.preparation_id<>r.preparation_id OR p.submission_id<>r.submission_id THEN
    RAISE EXCEPTION 'Review page parent mismatch' USING ERRCODE='23514'; END IF;
   IF TG_TABLE_NAME='bid_review_required_locations' THEN
    IF p.role<>'bid' OR (NEW.group_id IS NOT NULL AND NEW.group_id<>p.document_id::text) THEN RAISE EXCEPTION 'Required location must be a bid page in its group' USING ERRCODE='23514'; END IF;
   ELSE
    IF p.role<>'tender' THEN RAISE EXCEPTION 'Review quote must be tender text' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='bid_review_obligations' AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages a
     WHERE a.org_id=NEW.org_id AND a.authorization_id=r.authorization_id AND a.page_id=NEW.page_id) THEN
     RAISE EXCEPTION 'Obligation page was not authorized' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
  IF TG_TABLE_NAME='bid_review_signing_requirements' THEN
   IF NEW.candidate_id IS NOT NULL THEN
   SELECT * INTO c FROM public.bid_signing_candidates WHERE org_id=NEW.org_id AND id=NEW.candidate_id;
   IF c.submission_id<>r.submission_id OR c.preparation_id<>r.preparation_id OR c.page_id<>NEW.page_id THEN
    RAISE EXCEPTION 'Signing candidate parent mismatch' USING ERRCODE='23514'; END IF;
   END IF;
   IF (NEW.candidate_id IS NULL OR NEW.applicability<>'unknown') AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages a WHERE a.org_id=NEW.org_id AND a.authorization_id=r.authorization_id AND a.page_id=NEW.page_id) THEN
    RAISE EXCEPTION 'Signing confirmation page was not authorized' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bid_review_output_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_review_publications%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_review_publications WHERE org_id=NEW.org_id AND review_id=NEW.review_id;
 IF p.id IS NULL OR (p.coverage->>'obligations')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_obligations WHERE org_id=NEW.org_id AND review_id=NEW.review_id)
 OR (p.coverage->>'signing_requirements')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_signing_requirements WHERE org_id=NEW.org_id AND review_id=NEW.review_id)
 OR (p.coverage->>'required_locations')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_required_locations WHERE org_id=NEW.org_id AND review_id=NEW.review_id) THEN
 RAISE EXCEPTION 'Review publication is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bid_review_output_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME IN ('bid_review_obligations','bid_review_signing_requirements')
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Review inputs and publication are immutable' USING ERRCODE='42501';
END $$;
CREATE FUNCTION public.bid_review_job_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.bid_submission_document_id IS DISTINCT FROM OLD.bid_submission_document_id
 OR (OLD.kind='bid_review' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.document_id IS DISTINCT FROM OLD.document_id
 OR NEW.result->'submission' IS DISTINCT FROM OLD.result->'submission')) THEN
 RAISE EXCEPTION 'Review job input is immutable' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER bid_review_job_immutable BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION bid_review_job_immutable();
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['bid_review_runs','bid_review_obligations','bid_review_signing_requirements','bid_review_required_locations','bid_review_publications'] LOOP
 EXECUTE format('CREATE TRIGGER bid_review_output_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable()',tab);
 END LOOP;
 FOREACH tab IN ARRAY ARRAY['bid_review_obligations','bid_review_signing_requirements','bid_review_required_locations','bid_review_publications'] LOOP
 EXECUTE format('CREATE TRIGGER bid_review_output_guard BEFORE INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_guard()',tab);
 EXECUTE format('CREATE CONSTRAINT TRIGGER bid_review_output_complete AFTER INSERT ON public.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_complete()',tab);
 END LOOP;
END $$;
"""
