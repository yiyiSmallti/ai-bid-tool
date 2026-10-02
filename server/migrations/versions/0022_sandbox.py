"""Isolated sandbox source archives and execution attempt gates."""

from alembic import op

revision = "0022"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (
    "sandbox_inputs",
    "sandbox_runs",
    "sandbox_attempts",
    "sandbox_artifacts",
    "sandbox_fetch_receipts",
)


def upgrade():
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT sandbox_document_task UNIQUE (org_id,id,task_id)"
    )
    op.execute(
        """
CREATE TABLE sandbox_inputs (
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	purpose VARCHAR(30) NOT NULL, 
	task_feature_id UUID, 
	feature_revision_id UUID, 
	html_sha256 VARCHAR(64), 
	size_bytes INTEGER, 
	object_key TEXT, 
	generation_job_id UUID, 
	task_resource_id UUID, 
	product_revision_id UUID, 
	source_field VARCHAR(30), 
	source_url_sha256 VARCHAR(64), 
	encrypted_source_url TEXT, 
	spec JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id, document_id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, generation_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, feature_revision_id) REFERENCES feature_revisions (org_id, id), 
	FOREIGN KEY(org_id, product_revision_id) REFERENCES product_revisions (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, task_feature_id, task_id, feature_revision_id) REFERENCES task_features (org_id, id, task_id, feature_revision_id), 
	FOREIGN KEY(org_id, task_resource_id, task_id, product_revision_id) REFERENCES task_resources (org_id, id, task_id, product_revision_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

"""
    )
    op.execute("CREATE INDEX ix_sandbox_inputs_org_id ON sandbox_inputs (org_id)")
    op.execute(
        """
CREATE TABLE sandbox_runs (
	input_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	profile VARCHAR(40) NOT NULL, 
	policy_revision VARCHAR(100) NOT NULL, 
	policy_sha256 VARCHAR(64) NOT NULL, 
	runtime_profile_digest VARCHAR(64) NOT NULL, 
	requested_by UUID NOT NULL, 
	actor_kind VARCHAR(20) NOT NULL, 
	token_id UUID, 
	scope_snapshot JSONB NOT NULL, 
	capture_key UUID, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, request_hash), 
	UNIQUE (org_id, capture_key), 
	UNIQUE (org_id, job_id), 
	UNIQUE (org_id, id, input_id, task_id, job_id), 
	FOREIGN KEY(org_id, input_id, task_id, document_id) REFERENCES sandbox_inputs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, requested_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

"""
    )
    op.execute("CREATE INDEX ix_sandbox_runs_org_id ON sandbox_runs (org_id)")
    op.execute(
        """
CREATE TABLE sandbox_attempts (
	sandbox_run_id UUID NOT NULL, 
	input_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	attempt_id UUID NOT NULL, 
	instance_group_ref_hash VARCHAR(64) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	termination_code VARCHAR(100), 
	cleanup_state VARCHAR(20) NOT NULL, 
	metrics JSONB NOT NULL, 
	runtime_versions JSONB NOT NULL, 
	issues JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, job_id, attempt_id), 
	UNIQUE (org_id, id, sandbox_run_id, input_id, task_id), 
	FOREIGN KEY(org_id, sandbox_run_id, input_id, task_id, job_id) REFERENCES sandbox_runs (org_id, id, input_id, task_id, job_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

"""
    )
    op.execute("CREATE INDEX ix_sandbox_attempts_org_id ON sandbox_attempts (org_id)")
    op.execute(
        """
CREATE TABLE sandbox_artifacts (
	sandbox_run_id UUID NOT NULL, 
	attempt_record_id UUID NOT NULL, 
	input_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	parent_artifact_id UUID, 
	plaintext_sha256 VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	media_type VARCHAR(80) NOT NULL, 
	object_key TEXT NOT NULL, 
	provenance_manifest_hash VARCHAR(64) NOT NULL, 
	origin VARCHAR(30) NOT NULL, 
	width INTEGER, 
	height INTEGER, 
	page INTEGER, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, attempt_record_id, ordinal), 
	UNIQUE (org_id, id, sandbox_run_id, input_id, task_id, attempt_record_id), 
	FOREIGN KEY(org_id, attempt_record_id, sandbox_run_id, input_id, task_id) REFERENCES sandbox_attempts (org_id, id, sandbox_run_id, input_id, task_id), 
	FOREIGN KEY(org_id, parent_artifact_id, sandbox_run_id, input_id, task_id, attempt_record_id) REFERENCES sandbox_artifacts (org_id, id, sandbox_run_id, input_id, task_id, attempt_record_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

"""
    )
    op.execute("CREATE INDEX ix_sandbox_artifacts_org_id ON sandbox_artifacts (org_id)")
    op.execute(
        """
CREATE TABLE sandbox_fetch_receipts (
	sandbox_run_id UUID NOT NULL, 
	attempt_record_id UUID NOT NULL, 
	input_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	request_ordinal INTEGER NOT NULL, 
	parent_redirect_id UUID, 
	url_sha256 VARCHAR(64) NOT NULL, 
	encrypted_request_metadata TEXT, 
	response_sha256 VARCHAR(64), 
	response_bytes INTEGER NOT NULL, 
	status_code INTEGER, 
	decision_code VARCHAR(100) NOT NULL, 
	policy_revision VARCHAR(100) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	bundle_artifact_id UUID, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, attempt_record_id, request_ordinal), 
	UNIQUE (org_id, id, attempt_record_id), 
	FOREIGN KEY(org_id, attempt_record_id, sandbox_run_id, input_id, task_id) REFERENCES sandbox_attempts (org_id, id, sandbox_run_id, input_id, task_id), 
	FOREIGN KEY(org_id, parent_redirect_id, attempt_record_id) REFERENCES sandbox_fetch_receipts (org_id, id, attempt_record_id), 
	FOREIGN KEY(org_id, bundle_artifact_id, sandbox_run_id, input_id, task_id, attempt_record_id) REFERENCES sandbox_artifacts (org_id, id, sandbox_run_id, input_id, task_id, attempt_record_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

"""
    )
    op.execute("CREATE INDEX ix_sandbox_fetch_receipts_org_id ON sandbox_fetch_receipts (org_id)")

    _gates()


def _gates():
    op.execute("ALTER TABLE sandbox_attempts ADD COLUMN descriptor JSONB NOT NULL")
    op.execute("ALTER TABLE sandbox_attempts ADD COLUMN completion_txid BIGINT")
    op.execute(
        "ALTER TABLE sandbox_inputs ADD CONSTRAINT sandbox_input_document FOREIGN KEY (org_id,document_id,task_id) REFERENCES documents(org_id,id,task_id)"
    )
    op.execute(
        "ALTER TABLE sandbox_inputs ADD CONSTRAINT sandbox_generation_binding FOREIGN KEY (org_id,generation_job_id,task_id,document_id) REFERENCES jobs(org_id,id,task_id,document_id)"
    )
    checks = {
        "sandbox_inputs": [
            "purpose IN ('prototype_offline','vendor_capture')",
            "jsonb_typeof(spec)='object' AND spec->>'purpose' IS NOT NULL AND spec->>'purpose'=purpose",
            "generation_job_id IS NULL",
            "(purpose='prototype_offline' AND task_feature_id IS NOT NULL AND feature_revision_id IS NOT NULL AND html_sha256 IS NOT NULL AND size_bytes IS NOT NULL AND object_key IS NOT NULL AND task_resource_id IS NULL AND product_revision_id IS NULL AND source_field IS NULL AND source_url_sha256 IS NULL AND encrypted_source_url IS NULL) OR (purpose='vendor_capture' AND task_feature_id IS NULL AND feature_revision_id IS NULL AND html_sha256 IS NULL AND size_bytes IS NULL AND object_key IS NULL AND task_resource_id IS NOT NULL AND product_revision_id IS NOT NULL AND source_field IS NOT NULL AND source_url_sha256 IS NOT NULL AND encrypted_source_url IS NOT NULL)",
            "html_sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 4194304",
            "source_url_sha256 ~ '^[0-9a-f]{64}$' AND source_field IN ('official_url','whitepaper_url')",
            "object_key = 'org/' || org_id::text || '/sandbox-inputs/' || id::text || '/' || html_sha256",
        ],
        "sandbox_runs": [
            "request_hash ~ '^[0-9a-f]{64}$' AND runtime_profile_digest ~ '^[0-9a-f]{64}$' AND policy_sha256 ~ '^[0-9a-f]{64}$'",
            "actor_kind IN ('session','token','agent') AND ((actor_kind='token')=(token_id IS NOT NULL))",
            'jsonb_typeof(scope_snapshot)=\'array\' AND scope_snapshot <@ \'["sandbox:read","sandbox:render","sandbox:capture","task:read","resource:read","job:read"]\'::jsonb AND scope_snapshot ?& ARRAY[\'task:read\',\'resource:read\',\'job:read\']',
            "profile IN ('prototype-offline-v1','vendor-capture-v1')",
            "policy_revision ~ '^[A-Za-z0-9_.-]{1,100}$'",
        ],
        "sandbox_attempts": [
            "instance_group_ref_hash ~ '^[0-9a-f]{64}$'",
            "cleanup_state IN ('pending','complete','failed')",
            "(ended_at IS NULL AND termination_code IS NULL AND cleanup_state='pending') OR (ended_at IS NOT NULL AND ended_at>=started_at AND termination_code ~ '^[a-z0-9_]{1,100}$' AND cleanup_state IN ('complete','failed'))",
            "jsonb_typeof(metrics)='object' AND jsonb_typeof(runtime_versions)='object' AND jsonb_typeof(issues)='array'",
        ],
        "sandbox_artifacts": [
            "kind IN ('prototype_png','capture_png','pdf_page_png','rendered_html','source_pdf','request_manifest','capture_archive','provenance_manifest')",
            "ordinal BETWEEN 0 AND 31 AND size_bytes BETWEEN 1 AND 67108864",
            "plaintext_sha256 ~ '^[0-9a-f]{64}$' AND provenance_manifest_hash ~ '^[0-9a-f]{64}$'",
            "origin IN ('prototype','public_web_capture','public_pdf_capture')",
            "parent_artifact_id IS DISTINCT FROM id",
            "(kind IN ('prototype_png','capture_png','pdf_page_png') AND width IS NOT NULL AND height IS NOT NULL AND width BETWEEN 1 AND 8192 AND height BETWEEN 1 AND 8192 AND width::bigint*height<=20000000 AND size_bytes<=41943040 AND media_type='image/png') OR (kind NOT IN ('prototype_png','capture_png','pdf_page_png') AND width IS NULL AND height IS NULL AND media_type='application/octet-stream')",
            "(kind='pdf_page_png' AND page IS NOT NULL AND page>=1) OR (kind<>'pdf_page_png' AND page IS NULL)",
            "kind NOT IN ('rendered_html','request_manifest','provenance_manifest') OR size_bytes<=4194304",
            "kind<>'source_pdf' OR size_bytes<=33554432",
        ],
        "sandbox_fetch_receipts": [
            "request_ordinal BETWEEN 0 AND 203 AND response_bytes BETWEEN 0 AND 67108864",
            "url_sha256 ~ '^[0-9a-f]{64}$' AND (response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$')",
            "status_code IS NULL OR status_code BETWEEN 100 AND 599",
            "decision_code ~ '^[a-z0-9_]{1,100}$' AND ended_at>=started_at",
            "parent_redirect_id IS DISTINCT FROM id",
        ],
    }
    for table, predicates in checks.items():
        for n, predicate in enumerate(predicates):
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_gate_{n} CHECK ({predicate})")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        scope = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"
        op.execute(f"CREATE POLICY tenant_scope ON {table} USING ({scope}) WITH CHECK ({scope})")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO bid_app")
    op.execute(
        "GRANT UPDATE (ended_at,termination_code,cleanup_state,metrics,runtime_versions,issues) ON sandbox_attempts TO bid_app"
    )
    op.execute("CREATE INDEX sandbox_runs_task ON sandbox_runs(org_id,task_id,created_at)")
    op.execute(
        "CREATE INDEX sandbox_attempts_run ON sandbox_attempts(org_id,sandbox_run_id,started_at)"
    )
    op.execute(
        "CREATE INDEX sandbox_artifacts_attempt ON sandbox_artifacts(org_id,attempt_record_id,ordinal)"
    )
    op.execute("""
    CREATE FUNCTION sandbox_input_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.jobs j WHERE j.org_id=NEW.org_id
            AND j.id=NEW.extraction_job_id AND j.task_id=NEW.task_id
            AND j.document_id=NEW.document_id AND j.kind='extract' AND j.status='succeeded') THEN
            RAISE EXCEPTION 'Sandbox requires a successful same-task extraction' USING ERRCODE='23514';
        END IF;
        IF NEW.purpose='prototype_offline' THEN
            IF NOT EXISTS (SELECT 1 FROM public.task_features s WHERE s.org_id=NEW.org_id
                AND s.id=NEW.task_feature_id AND s.task_id=NEW.task_id
                AND s.feature_revision_id=NEW.feature_revision_id AND s.active) THEN
                RAISE EXCEPTION 'Sandbox selection is unavailable' USING ERRCODE='23514';
            END IF;
            IF NEW.spec->>'html_sha256' IS DISTINCT FROM NEW.html_sha256
                OR NEW.spec->>'task_feature_id' IS DISTINCT FROM NEW.task_feature_id::text
                OR NEW.spec->>'expected_feature_revision_id' IS DISTINCT FROM NEW.feature_revision_id::text
                OR (NEW.spec->>'html_size_bytes')::integer IS DISTINCT FROM NEW.size_bytes THEN
                RAISE EXCEPTION 'Sandbox prototype binding mismatch' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT EXISTS (SELECT 1 FROM public.task_resources s WHERE s.org_id=NEW.org_id
                AND s.id=NEW.task_resource_id AND s.task_id=NEW.task_id
                AND s.product_revision_id=NEW.product_revision_id AND s.active) THEN
                RAISE EXCEPTION 'Sandbox selection is unavailable' USING ERRCODE='23514';
            END IF;
            IF NEW.spec->>'source_field' IS DISTINCT FROM NEW.source_field
                OR NEW.spec->>'expected_source_url_sha256' IS DISTINCT FROM NEW.source_url_sha256
                OR NEW.spec->>'task_resource_id' IS DISTINCT FROM NEW.task_resource_id::text
                OR NEW.spec->>'expected_product_revision_id' IS DISTINCT FROM NEW.product_revision_id::text THEN
                RAISE EXCEPTION 'Sandbox vendor binding mismatch' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.spec->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
            RAISE EXCEPTION 'Sandbox extraction binding mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "ALTER TABLE sandbox_inputs ADD CONSTRAINT sandbox_spec_fields CHECK ((purpose='prototype_offline' AND spec ?& ARRAY['purpose','extraction_job_id','task_feature_id','expected_feature_revision_id','html_sha256','html_size_bytes','viewport'] AND spec-ARRAY['purpose','extraction_job_id','task_feature_id','expected_feature_revision_id','html_sha256','html_size_bytes','viewport']='{}'::jsonb) OR (purpose='vendor_capture' AND spec ?& ARRAY['purpose','extraction_job_id','task_resource_id','expected_product_revision_id','source_field','expected_source_url_sha256','format','pdf_pages','viewport','archive','capture_key'] AND spec-ARRAY['purpose','extraction_job_id','task_resource_id','expected_product_revision_id','source_field','expected_source_url_sha256','format','pdf_pages','viewport','archive','capture_key']='{}'::jsonb))"
    )
    op.execute(
        "ALTER TABLE sandbox_inputs ADD CONSTRAINT sandbox_viewport_fields CHECK (jsonb_typeof(spec->'viewport')='object' AND spec->'viewport' ?& ARRAY['width','height','device_scale_factor'] AND (spec->'viewport')-ARRAY['width','height','device_scale_factor']='{}'::jsonb AND jsonb_typeof(spec->'viewport'->'width')='number' AND jsonb_typeof(spec->'viewport'->'height')='number' AND spec->'viewport'->'device_scale_factor'='1'::jsonb AND (spec->'viewport'->>'width')::numeric BETWEEN 320 AND 4096 AND (spec->'viewport'->>'height')::numeric BETWEEN 240 AND 4096 AND trunc((spec->'viewport'->>'width')::numeric)=(spec->'viewport'->>'width')::numeric AND trunc((spec->'viewport'->>'height')::numeric)=(spec->'viewport'->>'height')::numeric)"
    )
    op.execute(
        "CREATE TRIGGER sandbox_input_gate BEFORE INSERT ON sandbox_inputs FOR EACH ROW EXECUTE FUNCTION sandbox_input_gate()"
    )
    op.execute("""
    CREATE FUNCTION sandbox_run_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE p text;
    BEGIN
        SELECT purpose INTO p FROM public.sandbox_inputs WHERE org_id=NEW.org_id AND id=NEW.input_id;
        IF (p='prototype_offline') IS DISTINCT FROM (NEW.profile='prototype-offline-v1')
            OR (p='vendor_capture') IS DISTINCT FROM (NEW.capture_key IS NOT NULL)
            OR NOT EXISTS (SELECT 1 FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id
                AND kind='sandbox' AND status='queued') THEN
            RAISE EXCEPTION 'Sandbox run binding mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.token_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.api_tokens
            WHERE org_id=NEW.org_id AND id=NEW.token_id AND user_id=NEW.requested_by) THEN
            RAISE EXCEPTION 'Sandbox actor mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER sandbox_run_gate BEFORE INSERT ON sandbox_runs FOR EACH ROW EXECUTE FUNCTION sandbox_run_gate()"
    )
    op.execute("""
    CREATE FUNCTION sandbox_attempt_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_OP='UPDATE' THEN
            IF OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL THEN
                RAISE EXCEPTION 'Sandbox terminal attempts are immutable' USING ERRCODE='23514';
            END IF;
            NEW.completion_txid := txid_current();
        ELSE
            IF NEW.ended_at IS NOT NULL OR NOT EXISTS (SELECT 1 FROM public.jobs
                WHERE org_id=NEW.org_id AND id=NEW.job_id AND kind='sandbox' AND status='running'
                AND run_id=NEW.attempt_id AND lease_until>clock_timestamp()) THEN
                RAISE EXCEPTION 'Sandbox attempt has no live lease' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER sandbox_attempt_gate BEFORE INSERT OR UPDATE ON sandbox_attempts FOR EACH ROW EXECUTE FUNCTION sandbox_attempt_gate()"
    )
    op.execute("""
    CREATE FUNCTION sandbox_artifact_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE a public.sandbox_attempts; i public.sandbox_inputs; p public.sandbox_artifacts;
    BEGIN
        SELECT * INTO a FROM public.sandbox_attempts WHERE org_id=NEW.org_id AND id=NEW.attempt_record_id;
        SELECT * INTO i FROM public.sandbox_inputs WHERE org_id=NEW.org_id AND id=NEW.input_id;
        IF a.completion_txid IS DISTINCT FROM txid_current() OR a.ended_at IS NULL OR a.cleanup_state<>'complete' OR a.termination_code<>'succeeded'
            OR NEW.object_key IS DISTINCT FROM 'org/' || NEW.org_id::text || '/sandbox-artifacts/' || NEW.sandbox_run_id::text || '/' || a.attempt_id::text || '/' || NEW.id::text || '/' || NEW.plaintext_sha256
            OR (i.purpose='prototype_offline') IS DISTINCT FROM (NEW.origin='prototype')
            OR (i.purpose='prototype_offline' AND NEW.kind NOT IN ('prototype_png','rendered_html','provenance_manifest')) THEN
            RAISE EXCEPTION 'Sandbox artifact binding mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.parent_artifact_id IS NOT NULL THEN
            SELECT * INTO p FROM public.sandbox_artifacts WHERE org_id=NEW.org_id AND id=NEW.parent_artifact_id;
            IF NOT FOUND OR p.ordinal>=NEW.ordinal THEN
                RAISE EXCEPTION 'Sandbox artifact parent must precede child' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER sandbox_artifact_gate BEFORE INSERT ON sandbox_artifacts FOR EACH ROW EXECUTE FUNCTION sandbox_artifact_gate()"
    )
    op.execute("""
    CREATE FUNCTION sandbox_receipt_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.sandbox_attempts WHERE org_id=NEW.org_id AND id=NEW.attempt_record_id AND completion_txid=txid_current()) THEN
            RAISE EXCEPTION 'Sandbox receipts are sealed with their attempt' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.sandbox_runs r JOIN public.sandbox_inputs i
            ON (i.org_id,i.id)=(r.org_id,r.input_id) WHERE r.org_id=NEW.org_id
            AND r.id=NEW.sandbox_run_id AND r.policy_revision=NEW.policy_revision AND i.purpose='vendor_capture') THEN
            RAISE EXCEPTION 'Sandbox receipt policy mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.bundle_artifact_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.sandbox_artifacts
            WHERE org_id=NEW.org_id AND id=NEW.bundle_artifact_id AND kind='capture_archive') THEN
            RAISE EXCEPTION 'Sandbox receipt archive mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.parent_redirect_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.sandbox_fetch_receipts
            WHERE org_id=NEW.org_id AND id=NEW.parent_redirect_id AND request_ordinal<NEW.request_ordinal) THEN
            RAISE EXCEPTION 'Sandbox receipt redirect order mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER sandbox_receipt_gate BEFORE INSERT ON sandbox_fetch_receipts FOR EACH ROW EXECUTE FUNCTION sandbox_receipt_gate()"
    )
    op.execute("""
    CREATE FUNCTION sandbox_complete_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE a public.sandbox_attempts; j public.jobs; i public.sandbox_inputs;
        n integer; bytes bigint; manifest text; actual_origin text;
    BEGIN
        SELECT * INTO a FROM public.sandbox_attempts WHERE org_id=NEW.org_id AND id=NEW.id;
        IF a.termination_code IS DISTINCT FROM 'succeeded' THEN RETURN NEW; END IF;
        SELECT * INTO j FROM public.jobs WHERE org_id=a.org_id AND id=a.job_id;
        SELECT * INTO i FROM public.sandbox_inputs WHERE org_id=a.org_id AND id=a.input_id;
        IF a.cleanup_state<>'complete' OR j.status<>'succeeded' OR j.run_id IS DISTINCT FROM a.attempt_id OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() OR jsonb_path_exists(a.issues, '$[*] ? (@.severity == "block")') THEN
            RAISE EXCEPTION 'Sandbox publication requires current completed attempt' USING ERRCODE='23514';
        END IF;
        SELECT count(*),sum(size_bytes),max(plaintext_sha256) FILTER(WHERE kind='provenance_manifest')
            INTO n,bytes,manifest FROM public.sandbox_artifacts
            WHERE org_id=a.org_id AND attempt_record_id=a.id;
        IF n<3 OR n>32 OR bytes>(CASE WHEN i.purpose='prototype_offline' THEN 67108864 ELSE 134217728 END)
            OR manifest IS NULL OR EXISTS (SELECT 1 FROM public.sandbox_artifacts
                WHERE org_id=a.org_id AND attempt_record_id=a.id AND provenance_manifest_hash<>manifest)
            OR (SELECT count(*) FROM public.sandbox_artifacts WHERE org_id=a.org_id AND attempt_record_id=a.id AND kind='provenance_manifest')<>1 THEN
            RAISE EXCEPTION 'Sandbox publication set is incomplete' USING ERRCODE='23514';
        END IF;
        IF i.purpose='prototype_offline' THEN
            IF n<>3 OR (SELECT count(DISTINCT kind) FROM public.sandbox_artifacts
                WHERE org_id=a.org_id AND attempt_record_id=a.id AND kind IN ('prototype_png','rendered_html','provenance_manifest'))<>3 THEN
                RAISE EXCEPTION 'Sandbox prototype set incomplete' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT EXISTS (SELECT 1 FROM public.sandbox_artifacts WHERE org_id=a.org_id
                AND attempt_record_id=a.id AND kind='request_manifest') THEN
                RAISE EXCEPTION 'Sandbox request manifest missing' USING ERRCODE='23514';
            END IF;
            IF n IS DISTINCT FROM (CASE WHEN i.spec->>'format'='pdf' THEN jsonb_array_length(i.spec->'pdf_pages')+3 ELSE 4 END + CASE WHEN i.spec->>'archive'='bundle' THEN 1 ELSE 0 END) THEN
                RAISE EXCEPTION 'Sandbox capture set contains extra artifacts' USING ERRCODE='23514';
            END IF;
            IF i.spec->>'format'='pdf' THEN
                IF (SELECT count(*) FROM public.sandbox_artifacts WHERE org_id=a.org_id AND attempt_record_id=a.id AND kind='source_pdf')<>1
                    OR (SELECT coalesce(jsonb_agg(page ORDER BY page),'[]'::jsonb) FROM public.sandbox_artifacts
                        WHERE org_id=a.org_id AND attempt_record_id=a.id AND kind='pdf_page_png') IS DISTINCT FROM i.spec->'pdf_pages' THEN
                    RAISE EXCEPTION 'Sandbox PDF pages incomplete' USING ERRCODE='23514';
                END IF;
            ELSE
                IF (SELECT count(DISTINCT kind) FROM public.sandbox_artifacts WHERE org_id=a.org_id
                    AND attempt_record_id=a.id AND kind IN ('capture_png','rendered_html'))<>2 THEN
                    RAISE EXCEPTION 'Sandbox web set incomplete' USING ERRCODE='23514';
                END IF;
            END IF;
            IF i.spec->>'archive'='bundle' AND (SELECT count(*) FROM public.sandbox_artifacts
                WHERE org_id=a.org_id AND attempt_record_id=a.id AND kind='capture_archive')<>1 THEN
                RAISE EXCEPTION 'Sandbox archive missing' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER sandbox_complete_gate AFTER UPDATE ON sandbox_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION sandbox_complete_gate()"
    )


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain sandbox source and execution history"
    )
