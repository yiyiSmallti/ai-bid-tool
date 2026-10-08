"""Offline signature observations and platform-managed public trust anchors."""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

TABLES = ("bid_preparation_trust", "bid_pdf_validations", "bid_signing_candidates")
FUNCTIONS = (
    "platform_trust_anchor_list()",
    "platform_trust_anchor_snapshot()",
    "platform_trust_anchor_add(text, bytea, text, text, text, timestamptz, timestamptz, text)",
    "platform_trust_anchor_disable(uuid, text)",
)


def upgrade():
    op.execute(
        "ALTER TABLE bid_document_pages ADD CONSTRAINT bid_signing_page_identity UNIQUE(org_id,task_id,submission_id,preparation_id,document_id,page,id)"
    )
    for statement in TABLE_SQL:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy = "org_id = NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({policy}) WITH CHECK ({policy})")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO bid_app")
        op.execute(f"CREATE INDEX ix_{table}_org_id ON {table}(org_id)")
    op.execute(PLATFORM_SQL)
    op.execute(GUARD_SQL)
    op.execute("GRANT CREATE ON SCHEMA public TO bid_trust_anchors_fn")
    for function in FUNCTIONS:
        op.execute(f"ALTER FUNCTION public.{function} OWNER TO bid_trust_anchors_fn")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function} TO bid_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM bid_trust_anchors_fn")


def downgrade():
    raise RuntimeError(
        "Retain pinned trust, signing evidence and audit; disable admissions and repair forward"
    )


TABLE_SQL = (
    r"""
CREATE TABLE platform_trust_anchors (
	label VARCHAR(100) NOT NULL,
	fingerprint_sha256 VARCHAR(64) NOT NULL,
	certificate_der BYTEA NOT NULL,
	subject VARCHAR(2000) NOT NULL,
	issuer VARCHAR(2000) NOT NULL,
	not_before TIMESTAMP WITH TIME ZONE NOT NULL,
	not_after TIMESTAMP WITH TIME ZONE NOT NULL,
	enabled BOOLEAN DEFAULT 'true' NOT NULL,
	revision INTEGER DEFAULT '1' NOT NULL,
	created_by VARCHAR(254) NOT NULL,
	disabled_by VARCHAR(254),
	disabled_at TIMESTAMP WITH TIME ZONE,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (octet_length(certificate_der) BETWEEN 1 AND 65536),
	CHECK (length(btrim(label)) BETWEEN 1 AND 100 AND length(btrim(created_by))>0),
	CHECK (not_after>=not_before AND revision>=1),
	CHECK ((enabled AND disabled_by IS NULL AND disabled_at IS NULL) OR (NOT enabled AND disabled_by IS NOT NULL AND disabled_at IS NOT NULL)),
	UNIQUE (fingerprint_sha256)
)

""",
    r"""
CREATE TABLE bid_preparation_trust (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	created_by UUID NOT NULL,
	trust_store_sha256 VARCHAR(64) NOT NULL,
	anchors JSONB NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, created_by) REFERENCES memberships (org_id, user_id),
	UNIQUE (org_id, preparation_id),
	UNIQUE (org_id, task_id, submission_id, preparation_id, trust_store_sha256),
	CHECK (trust_store_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (jsonb_typeof(anchors)='array' AND jsonb_array_length(anchors)<=128),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_pdf_validations (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	document_id UUID NOT NULL,
	original_sha256 VARCHAR(64) NOT NULL,
	validator_identity VARCHAR(200) NOT NULL,
	trust_store_sha256 VARCHAR(64) NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	UNIQUE (org_id, preparation_id, document_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, document_id) REFERENCES bid_prepared_documents (org_id, task_id, submission_id, preparation_id, document_id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, trust_store_sha256) REFERENCES bid_preparation_trust (org_id, task_id, submission_id, preparation_id, trust_store_sha256),
	CHECK (original_sha256 ~ '^[0-9a-f]{64}$' AND trust_store_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK (length(validator_identity)>0 AND length(details_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""
CREATE TABLE bid_signing_candidates (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	document_id UUID NOT NULL,
	page_id UUID NOT NULL,
	page INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	candidate_kind VARCHAR(100) NOT NULL,
	applicability VARCHAR(20) DEFAULT 'unknown' NOT NULL,
	details_encrypted TEXT NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id) REFERENCES bid_preparations (org_id, task_id, submission_id, id),
	UNIQUE (org_id, preparation_id, ordinal),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, document_id, page, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, preparation_id, document_id, page, id),
	CHECK (page BETWEEN 1 AND 1000 AND ordinal BETWEEN 1 AND 10000),
	CHECK (applicability='unknown' AND length(candidate_kind)>0 AND length(details_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
)

PLATFORM_SQL = r"""
REVOKE ALL ON public.platform_trust_anchors FROM PUBLIC, bid_app;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='bid_trust_anchors_fn') THEN
  CREATE ROLE bid_trust_anchors_fn NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
 END IF;
END $$;
ALTER ROLE bid_trust_anchors_fn NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO bid_trust_anchors_fn;
GRANT SELECT, INSERT ON public.platform_trust_anchors TO bid_trust_anchors_fn;
GRANT UPDATE(enabled,revision,disabled_by,disabled_at) ON public.platform_trust_anchors TO bid_trust_anchors_fn;
GRANT INSERT(id,actor_email,action,object_id,outcome,details,created_at) ON public.platform_audit_logs TO bid_trust_anchors_fn;
CREATE FUNCTION public.platform_trust_anchor_list() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg((to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca', true)
 ORDER BY a.created_at,a.id),'[]'::jsonb) FROM public.platform_trust_anchors a
$$;
-- A fresh command snapshot is needed after the admission guard waits on the
-- shared advisory lock; STABLE would keep the pre-wait INSERT snapshot.
CREATE FUNCTION public.platform_trust_anchor_snapshot() RETURNS jsonb
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH snapshot AS (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('id',a.id,'fingerprint_sha256',a.fingerprint_sha256,
 'certificate_der_base64',replace(encode(a.certificate_der,'base64'),E'\n',''),
 'revision',a.revision) ORDER BY a.fingerprint_sha256),'[]'::jsonb) AS anchors
 FROM public.platform_trust_anchors a WHERE a.enabled)
 SELECT jsonb_build_object('sha256',encode(sha256(convert_to(anchors::text,'UTF8')),'hex'),'anchors',anchors)
 FROM snapshot
$$;
CREATE FUNCTION public.platform_trust_anchor_add(p_label text,p_der bytea,p_fingerprint text,
 p_subject text,p_issuer text,p_before timestamptz,p_after timestamptz,p_actor text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a public.platform_trust_anchors%ROWTYPE; inserted boolean:=false;
BEGIN
 IF length(btrim(p_actor))=0 OR p_actor IS NULL OR p_fingerprint<>encode(sha256(p_der),'hex') THEN
  RAISE EXCEPTION 'Invalid trust anchor identity' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(862062001);
 IF (SELECT count(*) FROM public.platform_trust_anchors)>=128 AND
 NOT EXISTS(SELECT 1 FROM public.platform_trust_anchors WHERE fingerprint_sha256=p_fingerprint) THEN
  RAISE EXCEPTION 'Trust anchor capacity exceeded' USING ERRCODE='23514', CONSTRAINT='platform_trust_anchor_capacity'; END IF;
 INSERT INTO public.platform_trust_anchors(id,label,certificate_der,fingerprint_sha256,subject,issuer,
 not_before,not_after,created_by,created_at,enabled,revision)
 VALUES(gen_random_uuid(),p_label,p_der,p_fingerprint,p_subject,p_issuer,p_before,p_after,p_actor,clock_timestamp(),true,1)
 ON CONFLICT(fingerprint_sha256) DO NOTHING RETURNING * INTO a;
 inserted:=FOUND;
 IF inserted THEN
  INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details,created_at)
  VALUES(gen_random_uuid(),p_actor,'platform.trust_anchor.add',a.id::text,'success',
  jsonb_build_object('fingerprint_sha256',a.fingerprint_sha256,'revision',a.revision),clock_timestamp());
 ELSE
  SELECT * INTO STRICT a FROM public.platform_trust_anchors WHERE fingerprint_sha256=p_fingerprint;
 END IF;
 RETURN (to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca',true,'created',inserted);
END $$;
CREATE FUNCTION public.platform_trust_anchor_disable(p_id uuid,p_actor text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a public.platform_trust_anchors%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR length(btrim(p_actor))=0 THEN
  RAISE EXCEPTION 'Platform actor required' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(862062001);
 SELECT * INTO a FROM public.platform_trust_anchors WHERE id=p_id FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF a.enabled THEN
  UPDATE public.platform_trust_anchors SET enabled=false,revision=revision+1,
  disabled_by=p_actor,disabled_at=clock_timestamp() WHERE id=p_id RETURNING * INTO a;
  INSERT INTO public.platform_audit_logs(id,actor_email,action,object_id,outcome,details,created_at)
  VALUES(gen_random_uuid(),p_actor,'platform.trust_anchor.disable',a.id::text,'success',
  jsonb_build_object('fingerprint_sha256',a.fingerprint_sha256,'revision',a.revision),clock_timestamp());
 END IF;
 RETURN (to_jsonb(a)-'certificate_der') || jsonb_build_object('is_ca', true);
END $$;
"""

GUARD_SQL = r"""
CREATE FUNCTION public.bid_signing_trust_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_preparations%ROWTYPE; snapshot jsonb;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE org_id=NEW.org_id AND id=NEW.preparation_id;
 PERFORM public.bid_review_human(NEW.org_id,NEW.task_id,NEW.created_by,'bid-review\:prepare');
 IF p.created_by IS DISTINCT FROM NEW.created_by OR
 EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND preparation_id=NEW.preparation_id) THEN
  RAISE EXCEPTION 'Preparation trust actor mismatch' USING ERRCODE='23514'; END IF;
 -- Serialize admission with anchor writes so a preparation pins exactly one enabled store.
 PERFORM pg_advisory_xact_lock(862062001);
 snapshot:=public.platform_trust_anchor_snapshot();
 IF NEW.trust_store_sha256 IS DISTINCT FROM snapshot->>'sha256' OR NEW.anchors IS DISTINCT FROM snapshot->'anchors' THEN
  RAISE EXCEPTION 'Preparation trust snapshot changed' USING ERRCODE='23514', CONSTRAINT='bid_signing_trust_snapshot'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_bid_signing_trust AFTER INSERT ON bid_preparation_trust
 FOR EACH ROW EXECUTE FUNCTION bid_signing_trust_guard();
CREATE FUNCTION public.bid_signing_admission_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.bid_preparation_trust WHERE org_id=NEW.org_id AND preparation_id=NEW.id) THEN
  RAISE EXCEPTION 'Preparation requires pinned trust' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_signing_admission_complete AFTER INSERT ON bid_preparations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_signing_admission_complete();
CREATE FUNCTION public.bid_signing_inventory_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_preparations%ROWTYPE; j public.jobs%ROWTYPE; d public.bid_submission_documents%ROWTYPE; b public.bid_document_pages%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE org_id=NEW.org_id AND id=NEW.preparation_id;
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=p.job_id FOR SHARE;
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NULLIF(current_setting('app.agent_principal_id',true),'') IS NOT NULL
 OR NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
 OR p.created_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.status IS DISTINCT FROM 'running'
 OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR NOT(j.actor_scopes ? 'bid-review\:prepare')
 OR NOT public.bid_review_actor_live(NEW.org_id,NEW.task_id,p.created_by)
 OR EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND submission_id=NEW.submission_id) THEN
  RAISE EXCEPTION 'Current unpublished local preparation lease required' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.bid_submission_documents WHERE org_id=NEW.org_id AND id=NEW.document_id;
 IF TG_TABLE_NAME='bid_pdf_validations' THEN
  IF NEW.original_sha256 IS DISTINCT FROM d.sha256 THEN
   RAISE EXCEPTION 'Signature original identity mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO b FROM public.bid_document_pages WHERE org_id=NEW.org_id AND id=NEW.page_id;
  IF d.role<>'tender' OR b.text_status<>'native' THEN
   RAISE EXCEPTION 'Signing candidate needs native tender source' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_bid_pdf_validation AFTER INSERT ON bid_pdf_validations
 FOR EACH ROW EXECUTE FUNCTION bid_signing_inventory_guard();
CREATE TRIGGER zz_bid_signing_candidate AFTER INSERT ON bid_signing_candidates
 FOR EACH ROW EXECUTE FUNCTION bid_signing_inventory_guard();
CREATE FUNCTION public.bid_signing_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE p public.bid_preparation_publications%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparation_publications WHERE org_id=NEW.org_id AND preparation_id=NEW.preparation_id;
 IF TG_TABLE_NAME='bid_preparation_trust' THEN RETURN NEW; END IF;
 IF p.id IS NULL OR
 NOT EXISTS(SELECT 1 FROM public.bid_preparation_trust t WHERE t.org_id=p.org_id AND t.preparation_id=p.preparation_id) OR
 EXISTS(SELECT 1 FROM public.bid_prepared_documents d WHERE d.org_id=p.org_id AND d.preparation_id=p.preparation_id
 AND NOT EXISTS(SELECT 1 FROM public.bid_pdf_validations v WHERE (v.org_id,v.task_id,v.submission_id,v.preparation_id,v.document_id)
 =(d.org_id,d.task_id,d.submission_id,d.preparation_id,d.document_id))) THEN
  RAISE EXCEPTION 'Local signing inventory is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER bid_signing_publication_complete AFTER INSERT ON bid_preparation_publications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_signing_complete();
CREATE CONSTRAINT TRIGGER bid_signing_validation_complete AFTER INSERT ON bid_pdf_validations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_signing_complete();
CREATE CONSTRAINT TRIGGER bid_signing_candidate_complete AFTER INSERT ON bid_signing_candidates
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_signing_complete();
CREATE FUNCTION public.bid_signing_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME IN ('bid_pdf_validations','bid_signing_candidates')
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Local signing evidence is immutable' USING ERRCODE='42501';
END $$;
CREATE TRIGGER bid_signing_immutable BEFORE UPDATE OR DELETE ON bid_preparation_trust
 FOR EACH ROW EXECUTE FUNCTION bid_signing_immutable();
CREATE TRIGGER bid_signing_immutable BEFORE UPDATE OR DELETE ON bid_pdf_validations
 FOR EACH ROW EXECUTE FUNCTION bid_signing_immutable();
CREATE TRIGGER bid_signing_immutable BEFORE UPDATE OR DELETE ON bid_signing_candidates
 FOR EACH ROW EXECUTE FUNCTION bid_signing_immutable();
"""
