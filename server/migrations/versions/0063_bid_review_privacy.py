"""Local text privacy derivatives and exact human-only outbound grants."""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

TABLES = (
    "bid_review_name_lists",
    "bid_redaction_snapshots",
    "bid_redacted_pages",
    "bid_outbound_authorizations",
    "bid_outbound_authorized_pages",
)


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
    raise RuntimeError(
        "Retain privacy receipts and ciphertext; disable admissions and repair forward"
    )


TABLE_SQL = (
    r"""
CREATE TABLE bid_review_name_lists (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	request_id UUID NOT NULL,
	created_by UUID NOT NULL,
	names_sha256 VARCHAR(64) NOT NULL,
	names_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id) REFERENCES bid_submissions (org_id, task_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, submission_id, revision),
	UNIQUE (org_id, created_by, request_id),
	CHECK (revision>0 AND names_sha256 ~ '^[0-9a-f]{64}$' AND length(names_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_redaction_snapshots (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	redaction_revision INTEGER NOT NULL,
	policy_version VARCHAR(100) NOT NULL,
	manifest_sha256 VARCHAR(64) NOT NULL,
	confidential_binding_sha256 VARCHAR(64) NOT NULL,
	derived_name_lists_sha256 VARCHAR(64) NOT NULL,
	provider_bindings_sha256 VARCHAR(64) NOT NULL,
	created_by UUID NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, task_id, submission_id, preparation_id, id),
	UNIQUE (org_id, submission_id, manifest_sha256),
	CHECK (redaction_revision>0 AND length(policy_version)>0 AND length(details_encrypted)>0),
	CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$' AND confidential_binding_sha256 ~ '^[0-9a-f]{64}$' AND derived_name_lists_sha256 ~ '^[0-9a-f]{64}$' AND provider_bindings_sha256 ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_redacted_pages (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	snapshot_id UUID NOT NULL,
	page_id UUID NOT NULL,
	sanitized_text_sha256 VARCHAR(64) NOT NULL,
	sanitized_text_encrypted TEXT NOT NULL,
	price_page BOOLEAN NOT NULL,
	classification VARCHAR(20) NOT NULL,
	notes_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256),
	UNIQUE (org_id, snapshot_id, page_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, snapshot_id) REFERENCES bid_redaction_snapshots (org_id, task_id, submission_id, preparation_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	CHECK (sanitized_text_sha256 ~ '^[0-9a-f]{64}$' AND length(sanitized_text_encrypted)>0 AND length(notes_encrypted)>0),
	CHECK (classification IN ('price','non_price','uncertain') AND (price_page=(classification<>'non_price'))),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_outbound_authorizations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	snapshot_id UUID NOT NULL,
	revision INTEGER NOT NULL,
	prior_authorization_id UUID,
	request_id UUID NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	submission_manifest_sha256 VARCHAR(64) NOT NULL,
	preparation_input_hash VARCHAR(64) NOT NULL,
	redaction_manifest_sha256 VARCHAR(64) NOT NULL,
	authorized_sanitized_context_sha256 VARCHAR(64) NOT NULL,
	provider_bindings_sha256 VARCHAR(64) NOT NULL,
	allow_external BOOLEAN NOT NULL,
	authorized_by UUID NOT NULL,
	reason_sha256 VARCHAR(64) NOT NULL,
	reason_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	UNIQUE (org_id, task_id, submission_id, snapshot_id, id),
	UNIQUE (org_id, submission_id, revision),
	UNIQUE (org_id, authorized_by, request_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, snapshot_id) REFERENCES bid_redaction_snapshots (org_id, task_id, submission_id, preparation_id, id),
	FOREIGN KEY(org_id, task_id, prior_authorization_id) REFERENCES bid_outbound_authorizations (org_id, task_id, id),
	FOREIGN KEY(org_id, authorized_by) REFERENCES memberships (org_id, user_id),
	CHECK (revision>0 AND length(reason_encrypted)>0),
	CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND submission_manifest_sha256 ~ '^[0-9a-f]{64}$' AND preparation_input_hash ~ '^[0-9a-f]{64}$' AND redaction_manifest_sha256 ~ '^[0-9a-f]{64}$' AND authorized_sanitized_context_sha256 ~ '^[0-9a-f]{64}$' AND provider_bindings_sha256 ~ '^[0-9a-f]{64}$' AND reason_sha256 ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_outbound_authorized_pages (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	snapshot_id UUID NOT NULL,
	authorization_id UUID NOT NULL,
	page_id UUID NOT NULL,
	sanitized_text_sha256 VARCHAR(64) NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	UNIQUE (org_id, authorization_id, page_id),
	FOREIGN KEY(org_id, task_id, submission_id, snapshot_id, authorization_id) REFERENCES bid_outbound_authorizations (org_id, task_id, submission_id, snapshot_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256) REFERENCES bid_redacted_pages (org_id, task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256),
	CHECK (sanitized_text_sha256 ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
)

GUARD_SQL = r"""
CREATE FUNCTION public.bid_privacy_human(p_org uuid,p_task uuid,p_actor uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM public.bid_review_human(p_org,p_task,p_actor,'bid-review:outbound:authorize');
 IF NOT EXISTS(SELECT 1 FROM public.task_members WHERE org_id=p_org AND task_id=p_task
 AND user_id=p_actor AND active AND role='owner') THEN
  RAISE EXCEPTION 'Human task owner required' USING ERRCODE='42501'; END IF;
 -- Same lock order as API admissions; serializes checked append-only revisions.
 PERFORM 1 FROM public.tasks WHERE org_id=p_org AND id=p_task FOR UPDATE;
END $$;
CREATE FUNCTION public.bid_privacy_insert_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s public.bid_redaction_snapshots%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 p public.bid_outbound_authorizations%ROWTYPE; b public.bid_document_pages%ROWTYPE;
 latest integer; actor uuid;
BEGIN
 IF TG_TABLE_NAME='bid_outbound_authorizations' THEN actor:=NEW.authorized_by;
 ELSIF TG_TABLE_NAME IN ('bid_review_name_lists','bid_redaction_snapshots') THEN actor:=NEW.created_by;
 ELSE actor:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; END IF;
 PERFORM public.bid_privacy_human(NEW.org_id,NEW.task_id,actor);
 IF TG_TABLE_NAME='bid_review_name_lists' THEN
  SELECT COALESCE(max(revision),0) INTO latest FROM public.bid_review_name_lists
   WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id AND id<>NEW.id;
  IF NEW.revision<>latest+1 THEN RAISE EXCEPTION 'Name list revision mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_redaction_snapshots' THEN
  IF NOT EXISTS(SELECT 1 FROM public.bid_preparation_publications
   WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND submission_id=NEW.submission_id
   AND preparation_id=NEW.preparation_id) THEN
   RAISE EXCEPTION 'Published page inventory required' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_redacted_pages' THEN
  SELECT * INTO s FROM public.bid_redaction_snapshots WHERE org_id=NEW.org_id AND id=NEW.snapshot_id;
  SELECT * INTO b FROM public.bid_document_pages WHERE org_id=NEW.org_id AND id=NEW.page_id;
  IF s.created_by IS DISTINCT FROM actor OR b.preparation_id IS DISTINCT FROM NEW.preparation_id
  OR ((b.text_status<>'native' OR b.page_kind<>'text') AND NEW.classification='non_price')
  OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND snapshot_id=NEW.snapshot_id) THEN
   RAISE EXCEPTION 'Current unpublished redaction scope required' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_outbound_authorizations' THEN
  SELECT * INTO s FROM public.bid_redaction_snapshots WHERE org_id=NEW.org_id AND id=NEW.snapshot_id;
  SELECT * INTO p FROM public.bid_outbound_authorizations
   WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id AND id<>NEW.id
   ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>COALESCE(p.revision,0)+1 OR NEW.prior_authorization_id IS DISTINCT FROM p.id
  OR NEW.redaction_manifest_sha256 IS DISTINCT FROM s.manifest_sha256
  OR NEW.provider_bindings_sha256 IS DISTINCT FROM s.provider_bindings_sha256
  OR NOT EXISTS(SELECT 1 FROM public.bid_submissions u JOIN public.bid_preparation_publications v
   ON (u.org_id,u.task_id,u.id)=(v.org_id,v.task_id,v.submission_id)
   WHERE u.org_id=NEW.org_id AND u.task_id=NEW.task_id AND u.id=NEW.submission_id
   AND u.manifest_sha256=NEW.submission_manifest_sha256 AND v.input_hash=NEW.preparation_input_hash
   AND v.preparation_id=NEW.preparation_id) THEN
   RAISE EXCEPTION 'Exact outbound authorization identity mismatch' USING ERRCODE='23514'; END IF;
  IF NOT NEW.allow_external AND (p.id IS NULL OR NEW.snapshot_id<>p.snapshot_id
   OR NEW.authorized_sanitized_context_sha256<>p.authorized_sanitized_context_sha256) THEN
   RAISE EXCEPTION 'Revocation must retain prior disclosure identity' USING ERRCODE='23514'; END IF;
  IF NEW.allow_external AND NOT EXISTS(SELECT 1 FROM public.tasks t
   WHERE t.org_id=NEW.org_id AND t.id=NEW.task_id AND t.model_redaction_enabled
   AND t.model_redaction_revision=s.redaction_revision) THEN
   RAISE EXCEPTION 'Mandatory redaction must be enabled and current' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_outbound_authorized_pages' THEN
  SELECT * INTO g FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND id=NEW.authorization_id;
  IF g.authorized_by IS DISTINCT FROM actor OR NOT EXISTS(SELECT 1 FROM public.bid_redacted_pages r
   JOIN public.bid_document_pages dp ON (dp.org_id,dp.id)=(r.org_id,r.page_id)
   WHERE r.org_id=NEW.org_id AND r.snapshot_id=NEW.snapshot_id AND r.page_id=NEW.page_id
   AND r.sanitized_text_sha256=NEW.sanitized_text_sha256 AND NOT r.price_page AND dp.text_status='native' AND dp.page_kind='text') THEN
   RAISE EXCEPTION 'Only exact non-price native-text scope can be authorized' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['bid_review_name_lists','bid_redaction_snapshots','bid_redacted_pages',
 'bid_outbound_authorizations','bid_outbound_authorized_pages'] LOOP
  EXECUTE format('CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard()',tab);
 END LOOP;
END $$;
CREATE FUNCTION public.bid_privacy_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE g public.bid_outbound_authorizations%ROWTYPE; expected integer; scope_text text;
BEGIN
 IF TG_TABLE_NAME='bid_redaction_snapshots' THEN
  SELECT page_count INTO expected FROM public.bid_preparation_publications
   WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id;
  IF expected IS DISTINCT FROM (SELECT count(*)::integer FROM public.bid_redacted_pages
   WHERE org_id=NEW.org_id AND snapshot_id=NEW.id) THEN
   RAISE EXCEPTION 'Redaction snapshot must cover the prepared inventory' USING ERRCODE='23514'; END IF;
 ELSE
  IF TG_TABLE_NAME='bid_outbound_authorizations' THEN g:=NEW;
  ELSE SELECT * INTO g FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND id=NEW.authorization_id; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.id) THEN
   RAISE EXCEPTION 'Outbound grant requires an exact nonempty page scope' USING ERRCODE='23514'; END IF;
  SELECT '[' || string_agg('{"page_id":"' || page_id::text || '","sanitized_text_sha256":"'
   || sanitized_text_sha256 || '"}', ',' ORDER BY page_id::text) || ']' INTO scope_text
   FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.id;
  IF g.authorized_sanitized_context_sha256 IS DISTINCT FROM encode(sha256(convert_to(scope_text,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'Exact authorized scope digest mismatch' USING ERRCODE='23514'; END IF;
  IF NOT g.allow_external AND EXISTS(
   (SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.id
    EXCEPT SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.prior_authorization_id)
   UNION ALL
   (SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.prior_authorization_id
    EXCEPT SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE org_id=g.org_id AND authorization_id=g.id)
  ) THEN RAISE EXCEPTION 'Revocation cannot alter prior pages' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_privacy_snapshot_complete AFTER INSERT ON bid_redaction_snapshots
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_privacy_complete();
CREATE CONSTRAINT TRIGGER bid_privacy_grant_complete AFTER INSERT ON bid_outbound_authorizations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_privacy_complete();
CREATE CONSTRAINT TRIGGER bid_privacy_grant_page_complete AFTER INSERT ON bid_outbound_authorized_pages
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_privacy_complete();
CREATE FUNCTION public.bid_privacy_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE columns text[];
BEGIN
 columns:=CASE TG_TABLE_NAME WHEN 'bid_review_name_lists' THEN ARRAY['names_encrypted']
 WHEN 'bid_redaction_snapshots' THEN ARRAY['details_encrypted']
 WHEN 'bid_redacted_pages' THEN ARRAY['sanitized_text_encrypted','notes_encrypted']
 WHEN 'bid_outbound_authorizations' THEN ARRAY['reason_encrypted'] ELSE ARRAY[]::text[] END;
 IF TG_OP='UPDATE' AND cardinality(columns)>0
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-columns)=(to_jsonb(OLD)-columns) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Privacy evidence and human grants are immutable' USING ERRCODE='42501';
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['bid_review_name_lists','bid_redaction_snapshots','bid_redacted_pages',
 'bid_outbound_authorizations','bid_outbound_authorized_pages'] LOOP
  EXECUTE format('CREATE TRIGGER bid_privacy_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable()',tab);
 END LOOP;
END $$;
"""
