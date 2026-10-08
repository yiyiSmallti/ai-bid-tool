"""Immutable uploaded-bid originals and local preparation publication."""

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

TABLES = (
    "bid_submissions",
    "bid_submission_documents",
    "bid_preparations",
    "bid_prepared_documents",
    "bid_document_pages",
    "bid_preparation_publications",
)


def upgrade():
    op.execute(PARENT_SQL)
    for statement in TABLE_SQL:
        op.execute(statement)
    for statement in INDEX_SQL:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy = "org_id = NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO bid_app")
    # Row locks require UPDATE privileges; immutable triggers refuse actual updates.
    op.execute("GRANT UPDATE ON bid_submissions TO bid_app")
    op.execute(GUARD_SQL)
    # Emit only task invalidation metadata; document names, text and pixels never
    # enter the shared event payload or its statement transition projection.
    for table in ("bid_submissions", "bid_preparation_publications"):
        op.execute(
            f"CREATE TRIGGER task_event_insert AFTER INSERT ON public.{table} "
            "REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.produce_task_events('task_id','')"
        )


def downgrade():
    raise RuntimeError(
        "Retain uploaded-bid ciphertext and immutable inventories; disable admissions and repair forward"
    )


TABLE_SQL = (
    r"""
CREATE TABLE bid_submissions (
	task_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	manifest_sha256 VARCHAR(64) NOT NULL,
	file_count INTEGER NOT NULL,
	total_bytes BIGINT NOT NULL,
	request_id UUID NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	created_by UUID NOT NULL,
	state VARCHAR(20) DEFAULT 'uploaded' NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, revision),
	UNIQUE (org_id, created_by, request_id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	CHECK (revision > 0 AND state IN ('uploaded','withdrawn')),
	CHECK (file_count BETWEEN 2 AND 20 AND total_bytes BETWEEN 2 AND 524288000),
	CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_submission_documents (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	file_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	role VARCHAR(10) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	media_type VARCHAR(100) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size_bytes BIGINT NOT NULL,
	storage_key VARCHAR(500) NOT NULL,
	upload_name_encrypted TEXT NOT NULL,
	created_by UUID NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, submission_id, id),
	UNIQUE (org_id, submission_id, ordinal),
	UNIQUE (org_id, submission_id, role, sha256),
	UNIQUE (org_id, file_id),
	FOREIGN KEY(org_id, task_id, submission_id) REFERENCES bid_submissions (org_id, task_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	CHECK (ordinal BETWEEN 1 AND 20 AND size_bytes BETWEEN 1 AND 104857600),
	CHECK (sha256 ~ '^[0-9a-f]{64}$' AND length(upload_name_encrypted)>0),
	CHECK (role IN ('tender','bid') AND ((role='tender')=(kind='tender'))),
	CHECK (kind IN ('tender','qualification','commercial_technical','price','declaration','other')),
	CHECK (media_type IN ('application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
	CHECK (storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text || '/originals/' || file_id::text || '/' || sha256 || CASE media_type WHEN 'application/pdf' THEN '.pdf' ELSE '.docx' END),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_preparations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	job_id UUID NOT NULL,
	request_id UUID NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	created_by UUID NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, submission_id, id),
	UNIQUE (org_id, task_id, submission_id, id, job_id, input_hash),
	UNIQUE (org_id, job_id),
	UNIQUE (org_id, created_by, request_id),
	FOREIGN KEY(org_id, task_id, submission_id) REFERENCES bid_submissions (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id, job_id) REFERENCES jobs (org_id, task_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	CHECK (input_hash ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_prepared_documents (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	document_id UUID NOT NULL,
	page_count INTEGER NOT NULL,
	rendered_pdf_sha256 VARCHAR(64) NOT NULL,
	rendered_pdf_storage_key VARCHAR(500) NOT NULL,
	render_profile VARCHAR(100) NOT NULL,
	renderer_identity VARCHAR(200) NOT NULL,
	citation_mode VARCHAR(10) NOT NULL,
	parsing_warnings JSONB NOT NULL,
	signature_field_count INTEGER NOT NULL,
	structure_encrypted TEXT,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, submission_id, preparation_id, document_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, document_id) REFERENCES bid_submission_documents (org_id, task_id, submission_id, id),
	CHECK (page_count BETWEEN 1 AND 1000 AND signature_field_count>=0),
	CHECK (rendered_pdf_sha256 ~ '^[0-9a-f]{64}$' AND citation_mode IN ('page','block')),
	CHECK (length(render_profile)>0 AND length(renderer_identity)>0),
	CHECK (jsonb_typeof(parsing_warnings)='array' AND jsonb_array_length(parsing_warnings)<=100),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_document_pages (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	document_id UUID NOT NULL,
	page INTEGER NOT NULL,
	role VARCHAR(10) NOT NULL,
	original_sha256 VARCHAR(64) NOT NULL,
	rendered_pdf_sha256 VARCHAR(64) NOT NULL,
	image JSONB NOT NULL,
	storage_key VARCHAR(500) NOT NULL,
	render_profile VARCHAR(100) NOT NULL,
	renderer_identity VARCHAR(200) NOT NULL,
	page_kind VARCHAR(10) NOT NULL,
	text_status VARCHAR(20) NOT NULL,
	text_sha256 VARCHAR(64),
	text_encrypted TEXT,
	structure_encrypted TEXT,
	price_page BOOLEAN DEFAULT true NOT NULL,
	redaction_status VARCHAR(20) DEFAULT 'human_only' NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, submission_id, id),
	UNIQUE (org_id, preparation_id, document_id, page),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, document_id) REFERENCES bid_prepared_documents (org_id, task_id, submission_id, preparation_id, document_id),
	CHECK (page BETWEEN 1 AND 1000 AND role IN ('tender','bid')),
	CHECK (original_sha256 ~ '^[0-9a-f]{64}$' AND rendered_pdf_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (page_kind IN ('text','image') AND text_status IN ('native','unavailable')),
	CHECK ((text_status='unavailable' AND text_sha256 IS NULL AND text_encrypted IS NULL) OR (text_status='native' AND text_sha256 IS NOT NULL AND text_encrypted IS NOT NULL AND text_sha256 ~ '^[0-9a-f]{64}$' AND length(text_encrypted)>0)),
	CHECK (redaction_status='human_only'),
	CHECK (jsonb_typeof(image)='object' AND image ?& ARRAY['sha256','size_bytes','width_px','height_px','media_type'] AND image->>'sha256' ~ '^[0-9a-f]{64}$' AND image->>'media_type'='image/png' AND (image->>'size_bytes')::bigint>0 AND (image->>'width_px')::integer>0 AND (image->>'height_px')::integer>0),
	CHECK (storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text || '/preparations/' || preparation_id::text || '/pages/' || document_id::text || '/' || page::text || '/' || (image->>'sha256') || '.png'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_preparation_publications (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	job_id UUID NOT NULL,
	run_id UUID NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	page_count INTEGER NOT NULL,
	published_by UUID NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, submission_id),
	UNIQUE (org_id, preparation_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, job_id, input_hash) REFERENCES bid_preparations (org_id, task_id, submission_id, id, job_id, input_hash),
	FOREIGN KEY(org_id, published_by) REFERENCES memberships (org_id, user_id),
	CHECK (page_count BETWEEN 2 AND 1000 AND input_hash ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
)

INDEX_SQL = (
    "CREATE INDEX bid_submission_page ON bid_submissions (org_id, task_id, created_at, id)",
    "CREATE INDEX ix_bid_submissions_org_id ON bid_submissions (org_id)",
    "CREATE INDEX ix_bid_submission_documents_org_id ON bid_submission_documents (org_id)",
    "CREATE INDEX bid_preparation_submission ON bid_preparations (org_id, submission_id, created_at, id)",
    "CREATE INDEX ix_bid_preparations_org_id ON bid_preparations (org_id)",
    "CREATE INDEX ix_bid_prepared_documents_org_id ON bid_prepared_documents (org_id)",
    "CREATE INDEX bid_document_page_inventory ON bid_document_pages (org_id, submission_id, document_id, page)",
    "CREATE INDEX ix_bid_document_pages_org_id ON bid_document_pages (org_id)",
    "CREATE INDEX ix_bid_preparation_publications_org_id ON bid_preparation_publications (org_id)",
)

PARENT_SQL = r"""
ALTER TABLE jobs ADD CONSTRAINT bid_review_job_task UNIQUE(org_id,task_id,id);
ALTER TABLE jobs DROP CONSTRAINT job_document_binding;
ALTER TABLE jobs ADD CONSTRAINT job_document_binding CHECK(
 (kind='provider_test' AND task_id IS NULL AND document_id IS NULL)
 OR (kind='bid_review_prepare' AND task_id IS NOT NULL AND document_id IS NULL)
 OR (kind NOT IN ('provider_test','bid_review_prepare') AND task_id IS NOT NULL AND document_id IS NOT NULL));
ALTER TABLE jobs ADD CONSTRAINT bid_review_prepare_actor CHECK(kind<>'bid_review_prepare' OR
 (actor_user_id IS NOT NULL AND actor_kind='session' AND actor_token_id IS NULL AND agent_principal_id IS NULL));
ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_bid_review_scopes CHECK(
 NOT(scopes ?| ARRAY['bid-review\:upload','bid-review\:prepare','bid-review\:report\:render',
 'bid-review\:original\:read','bid-review\:source\:read','bid-review\:report\:read',
 'bid-review\:report\:download','bid-review\:decide','bid-review\:evidence\:review',
 'bid-review\:price\:release','bid-review\:outbound\:authorize','bid-review\:classify']));
"""

GUARD_SQL = r"""
CREATE FUNCTION public.bid_review_actor_live(p_org uuid,p_task uuid,p_actor uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
 JOIN public.orgs o ON o.id=m.org_id
 JOIN public.task_members tm ON (tm.org_id,tm.user_id)=(m.org_id,m.user_id)
 JOIN public.task_workflows w ON (w.org_id,w.task_id)=(tm.org_id,tm.task_id)
 WHERE m.org_id=p_org AND m.user_id=p_actor AND m.active AND u.active AND o.active
 AND m.role IN ('admin','bidder') AND tm.task_id=p_task AND tm.active
 AND tm.role IN ('owner','contributor') AND w.state='active')
$$;
CREATE FUNCTION public.bid_review_human(p_org uuid,p_task uuid,p_actor uuid,p_scope text) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NULLIF(current_setting('app.agent_principal_id',true),'') IS NOT NULL
 OR p_actor IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
 OR NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb ? p_scope,false)
 OR NOT public.bid_review_actor_live(p_org,p_task,p_actor) THEN
  RAISE EXCEPTION 'Human uploaded-bid authority required' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION public.bid_review_submission_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_submissions%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='bid_submissions' THEN
  PERFORM public.bid_review_human(NEW.org_id,NEW.task_id,NEW.created_by,'bid-review\:upload');
  IF NEW.state<>'uploaded' THEN RAISE EXCEPTION 'Initial submission must be uploaded' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO p FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=NEW.submission_id;
  PERFORM public.bid_review_human(NEW.org_id,NEW.task_id,NEW.created_by,'bid-review\:upload');
  IF p.created_by IS DISTINCT FROM NEW.created_by OR p.state<>'uploaded' THEN
   RAISE EXCEPTION 'Original actor mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_bid_submission_actor AFTER INSERT ON bid_submissions
 FOR EACH ROW EXECUTE FUNCTION bid_review_submission_guard();
CREATE TRIGGER zz_bid_document_actor AFTER INSERT ON bid_submission_documents
 FOR EACH ROW EXECUTE FUNCTION bid_review_submission_guard();
CREATE FUNCTION public.bid_review_upload_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_submissions%ROWTYPE; n bigint; bytes bigint; roles bigint; first integer; last integer;
BEGIN
 IF TG_TABLE_NAME='bid_submissions' THEN
  SELECT * INTO p FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=NEW.id;
 ELSE
  SELECT * INTO p FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=NEW.submission_id;
 END IF;
 SELECT count(*),sum(size_bytes),count(DISTINCT role),min(ordinal),max(ordinal)
 INTO n,bytes,roles,first,last FROM public.bid_submission_documents
 WHERE org_id=p.org_id AND submission_id=p.id;
 IF n<>p.file_count OR bytes<>p.total_bytes OR roles<>2 OR first<>1 OR last<>n THEN
  RAISE EXCEPTION 'Uploaded manifest is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_upload_complete AFTER INSERT ON bid_submissions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_review_upload_complete();
CREATE CONSTRAINT TRIGGER bid_file_complete AFTER INSERT ON bid_submission_documents
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_review_upload_complete();
CREATE FUNCTION public.bid_review_preparation_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE j public.jobs%ROWTYPE; p public.bid_submissions%ROWTYPE;
BEGIN
 PERFORM public.bid_review_human(NEW.org_id,NEW.task_id,NEW.created_by,'bid-review\:prepare');
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
 SELECT * INTO p FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=NEW.submission_id;
 IF j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.task_id IS DISTINCT FROM NEW.task_id
 OR j.document_id IS NOT NULL OR j.actor_user_id IS DISTINCT FROM NEW.created_by
 OR j.actor_kind IS DISTINCT FROM 'session' OR j.actor_token_id IS NOT NULL
 OR NOT(j.actor_scopes ? 'bid-review\:prepare') OR p.state<>'uploaded'
 OR EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id) THEN
  RAISE EXCEPTION 'Invalid uploaded-bid preparation binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_bid_preparation_actor AFTER INSERT ON bid_preparations
 FOR EACH ROW EXECUTE FUNCTION bid_review_preparation_guard();
CREATE FUNCTION public.bid_review_inventory_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_preparations%ROWTYPE; j public.jobs%ROWTYPE; d public.bid_submission_documents%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE org_id=NEW.org_id AND id=NEW.preparation_id;
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=p.job_id FOR SHARE;
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR p.created_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.status IS DISTINCT FROM 'running'
 OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR NOT(j.actor_scopes ? 'bid-review\:prepare')
 OR NOT public.bid_review_actor_live(NEW.org_id,NEW.task_id,p.created_by) THEN
  RAISE EXCEPTION 'Current local preparation lease required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_preparation_publications' THEN
  IF NEW.published_by IS DISTINCT FROM p.created_by OR NEW.run_id IS DISTINCT FROM j.run_id THEN
   RAISE EXCEPTION 'Publication actor or attempt mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id) THEN
   RAISE EXCEPTION 'Published page inventory is immutable' USING ERRCODE='42501'; END IF;
  SELECT * INTO d FROM public.bid_submission_documents WHERE org_id=NEW.org_id AND id=NEW.document_id;
  IF TG_TABLE_NAME='bid_document_pages' THEN
   IF NEW.role IS DISTINCT FROM d.role OR NEW.original_sha256 IS DISTINCT FROM d.sha256
   OR (NEW.page_kind='text' AND NEW.text_status<>'native') THEN
    RAISE EXCEPTION 'Page source identity mismatch' USING ERRCODE='23514'; END IF;
  ELSIF (d.media_type='application/pdf' AND (NEW.citation_mode<>'page' OR NEW.rendered_pdf_sha256<>d.sha256
  OR NEW.rendered_pdf_storage_key<>d.storage_key))
  OR (d.media_type<>'application/pdf' AND (NEW.citation_mode<>'block'
  OR NEW.rendered_pdf_storage_key<>'org/' || NEW.org_id::text || '/bid-review/' || NEW.submission_id::text
  || '/preparations/' || NEW.preparation_id::text || '/documents/' || NEW.document_id::text || '/' || NEW.rendered_pdf_sha256 || '.pdf')) THEN
   RAISE EXCEPTION 'Prepared document source identity mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_bid_prepared_document AFTER INSERT ON bid_prepared_documents
 FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_guard();
CREATE TRIGGER zz_bid_document_page AFTER INSERT ON bid_document_pages
 FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_guard();
CREATE TRIGGER zz_bid_preparation_publication AFTER INSERT ON bid_preparation_publications
 FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_guard();
CREATE FUNCTION public.bid_review_inventory_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_preparation_publications%ROWTYPE; total bigint; docs bigint; required bigint;
BEGIN
 SELECT * INTO p FROM public.bid_preparation_publications
 WHERE org_id=NEW.org_id AND preparation_id=NEW.preparation_id;
 IF p.id IS NULL THEN
  RAISE EXCEPTION 'Partial inventory cannot be committed' USING ERRCODE='23514'; END IF;
 SELECT count(*),sum(page_count) INTO docs,total FROM public.bid_prepared_documents
 WHERE org_id=p.org_id AND preparation_id=p.preparation_id;
 SELECT file_count INTO required FROM public.bid_submissions WHERE org_id=p.org_id AND id=p.submission_id;
 IF docs<>required OR total IS DISTINCT FROM p.page_count OR total>1000
 OR EXISTS(SELECT 1 FROM public.bid_prepared_documents d WHERE d.org_id=p.org_id AND d.preparation_id=p.preparation_id
 AND (SELECT count(*) FROM public.bid_document_pages b WHERE b.org_id=d.org_id
 AND b.preparation_id=d.preparation_id AND b.document_id=d.document_id)<>d.page_count)
 OR EXISTS(SELECT 1 FROM public.bid_document_pages b JOIN public.bid_prepared_documents d
 ON (d.org_id,d.task_id,d.submission_id,d.preparation_id,d.document_id)
 =(b.org_id,b.task_id,b.submission_id,b.preparation_id,b.document_id)
 WHERE b.org_id=p.org_id AND b.preparation_id=p.preparation_id AND
 (b.page>d.page_count OR b.rendered_pdf_sha256<>d.rendered_pdf_sha256
 OR b.render_profile<>d.render_profile OR b.renderer_identity<>d.renderer_identity)) THEN
  RAISE EXCEPTION 'Page inventory is incomplete or inconsistent' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_prepared_complete AFTER INSERT ON bid_prepared_documents
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_complete();
CREATE CONSTRAINT TRIGGER bid_page_complete AFTER INSERT ON bid_document_pages
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_complete();
CREATE CONSTRAINT TRIGGER bid_publication_complete AFTER INSERT ON bid_preparation_publications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_review_inventory_complete();
CREATE FUNCTION public.bid_review_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE encrypted_columns text[];
BEGIN
 encrypted_columns:=CASE TG_TABLE_NAME
  WHEN 'bid_submission_documents' THEN ARRAY['upload_name_encrypted']
  WHEN 'bid_prepared_documents' THEN ARRAY['structure_encrypted']
  WHEN 'bid_document_pages' THEN ARRAY['text_encrypted','structure_encrypted']
  ELSE ARRAY[]::text[] END;
 IF TG_OP='UPDATE' AND cardinality(encrypted_columns)>0
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_columns)=(to_jsonb(OLD)-encrypted_columns) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Uploaded-bid versions and inventories are immutable' USING ERRCODE='42501';
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['bid_submissions','bid_submission_documents','bid_preparations',
 'bid_prepared_documents','bid_document_pages','bid_preparation_publications'] LOOP
  EXECUTE format('CREATE TRIGGER bid_review_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate()',tab);
 END LOOP;
END $$;
"""
