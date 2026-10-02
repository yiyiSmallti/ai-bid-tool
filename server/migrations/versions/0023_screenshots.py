"""Screenshot Phase A provenance, human release and exact prototype decisions."""

from alembic import op

revision = "0023"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (
    "screenshot_search_runs",
    "screenshot_search_candidates",
    "screenshot_vendor_archives",
    "screenshot_prototype_runs",
    "screenshot_assets",
    "screenshot_renditions",
    "screenshot_privacy_reviews",
    "screenshot_withdrawals",
    "screenshot_analysis_runs",
    "screenshot_analysis_inputs",
    "screenshot_suggestions",
    "prototype_decision_batches",
    "prototype_evidence_decisions",
)


def upgrade():
    op.execute("""
CREATE TABLE screenshot_search_runs (
	task_resource_id UUID NOT NULL, 
	product_revision_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	manifest JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_resource_id, task_id, product_revision_id) REFERENCES task_resources (org_id, id, task_id, product_revision_id), 
	FOREIGN KEY(org_id, product_revision_id) REFERENCES product_revisions (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_search_candidates (
	search_run_id UUID NOT NULL, 
	ref VARCHAR(80) NOT NULL, 
	source_url TEXT NOT NULL, 
	title TEXT NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id, search_run_id) REFERENCES screenshot_search_runs (org_id, task_id, id), 
	UNIQUE (org_id, search_run_id, ref), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_vendor_archives (
	task_resource_id UUID NOT NULL, 
	product_revision_id UUID NOT NULL, 
	search_candidate_id UUID, 
	content_sha256 VARCHAR(64) NOT NULL, 
	archive_sha256 VARCHAR(64) NOT NULL, 
	storage_key TEXT NOT NULL, 
	descriptor JSONB NOT NULL, 
	provenance JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_resource_id, task_id, product_revision_id) REFERENCES task_resources (org_id, id, task_id, product_revision_id), 
	FOREIGN KEY(org_id, product_revision_id) REFERENCES product_revisions (org_id, id), 
	FOREIGN KEY(org_id, task_id, search_candidate_id) REFERENCES screenshot_search_candidates (org_id, task_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_prototype_runs (
	requirement_id UUID NOT NULL, 
	task_feature_id UUID NOT NULL, 
	feature_revision_id UUID NOT NULL, 
	generation_job_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	html_sha256 VARCHAR(64) NOT NULL, 
	html_storage_key TEXT NOT NULL, 
	source_image_sha256 VARCHAR(64) NOT NULL, 
	source_image_key TEXT NOT NULL, 
	sandbox_receipt_id VARCHAR(200) NOT NULL, 
	sandbox_receipt_sha256 VARCHAR(64) NOT NULL, 
	provenance JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, task_feature_id, task_id, feature_revision_id) REFERENCES task_features (org_id, id, task_id, feature_revision_id), 
	FOREIGN KEY(org_id, feature_revision_id) REFERENCES feature_revisions (org_id, id), 
	FOREIGN KEY(org_id, generation_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_assets (
	source_kind VARCHAR(30) NOT NULL, 
	image_kind VARCHAR(30) NOT NULL, 
	origin VARCHAR(30) NOT NULL, 
	task_feature_id UUID, 
	feature_revision_id UUID, 
	task_resource_id UUID, 
	product_revision_id UUID, 
	evidence_source_id UUID, 
	vendor_archive_id UUID, 
	prototype_run_id UUID, 
	source_sha256 VARCHAR(64) NOT NULL, 
	source_hash_assurance VARCHAR(30) NOT NULL, 
	source_width INTEGER NOT NULL, 
	source_height INTEGER NOT NULL, 
	source JSONB NOT NULL, 
	received_by UUID NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	idempotency_key UUID NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, task_id, idempotency_key), 
	FOREIGN KEY(org_id, received_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, task_feature_id, task_id, feature_revision_id) REFERENCES task_features (org_id, id, task_id, feature_revision_id), 
	FOREIGN KEY(org_id, feature_revision_id) REFERENCES feature_revisions (org_id, id), 
	FOREIGN KEY(org_id, task_resource_id, task_id, product_revision_id) REFERENCES task_resources (org_id, id, task_id, product_revision_id), 
	FOREIGN KEY(org_id, product_revision_id) REFERENCES product_revisions (org_id, id), 
	FOREIGN KEY(org_id, evidence_source_id) REFERENCES evidence_sources (org_id, id), 
	FOREIGN KEY(org_id, task_id, vendor_archive_id) REFERENCES screenshot_vendor_archives (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id, prototype_run_id) REFERENCES screenshot_prototype_runs (org_id, task_id, id), 
	CHECK (source_width BETWEEN 1 AND 8192 AND source_height BETWEEN 1 AND 8192 AND source_width::bigint * source_height <= 20000000), 
	CHECK (source_hash_assurance IN ('client_declared','server_verified')), 
	CHECK (source_sha256 ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_renditions (
	asset_id UUID NOT NULL, 
	parent_rendition_id UUID, 
	privacy_review_id UUID NOT NULL, 
	source_sha256 VARCHAR(64) NOT NULL, 
	upload_sha256 VARCHAR(64) NOT NULL, 
	image_sha256 VARCHAR(64) NOT NULL, 
	plan_sha256 VARCHAR(64) NOT NULL, 
	plan JSONB NOT NULL, 
	mapping JSONB NOT NULL, 
	image JSONB NOT NULL, 
	profile VARCHAR(40) NOT NULL, 
	storage_key TEXT NOT NULL, 
	generation_job_id UUID, 
	actor_user_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, asset_id, id), 
	UNIQUE (org_id, asset_id, id, image_sha256), 
	UNIQUE (org_id, asset_id, parent_rendition_id, plan_sha256, profile), 
	FOREIGN KEY(org_id, task_id, asset_id) REFERENCES screenshot_assets (org_id, task_id, id), 
	FOREIGN KEY(org_id, asset_id, parent_rendition_id) REFERENCES screenshot_renditions (org_id, asset_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, generation_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	CHECK (image_sha256 ~ '^[0-9a-f]{64}$' AND upload_sha256 ~ '^[0-9a-f]{64}$' AND plan_sha256 ~ '^[0-9a-f]{64}$'), 
	CHECK (profile IN ('screenshot-markup-v1','prototype-clean-v1')), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_privacy_reviews (
	asset_id UUID NOT NULL, 
	rendition_id UUID NOT NULL, 
	reviewed_upload_sha256 VARCHAR(64) NOT NULL, 
	stored_image_sha256 VARCHAR(64) NOT NULL, 
	reviewed_by UUID NOT NULL, 
	rule_version VARCHAR(40) NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, asset_id), 
	UNIQUE (org_id, asset_id, id), 
	FOREIGN KEY(org_id, task_id, asset_id) REFERENCES screenshot_assets (org_id, task_id, id), 
	FOREIGN KEY(org_id, asset_id, rendition_id, stored_image_sha256) REFERENCES screenshot_renditions (org_id, asset_id, id, image_sha256), 
	FOREIGN KEY(org_id, reviewed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_withdrawals (
	asset_id UUID NOT NULL, 
	reason TEXT NOT NULL, 
	withdrawn_by UUID NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, asset_id), 
	FOREIGN KEY(org_id, task_id, asset_id) REFERENCES screenshot_assets (org_id, task_id, id), 
	FOREIGN KEY(org_id, withdrawn_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_analysis_runs (
	job_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	input_manifest JSONB NOT NULL, 
	completion VARCHAR(20) NOT NULL, 
	rejected JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, job_id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	CHECK (completion IN ('complete','partial')), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_analysis_inputs (
	analysis_run_id UUID NOT NULL, 
	ref VARCHAR(80) NOT NULL, 
	requirement_id UUID, 
	asset_id UUID, 
	rendition_id UUID, 
	privacy_review_id UUID, 
	content_sha256 VARCHAR(64) NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, analysis_run_id, ref), 
	UNIQUE (org_id, analysis_run_id, id), 
	FOREIGN KEY(org_id, task_id, analysis_run_id) REFERENCES screenshot_analysis_runs (org_id, task_id, id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, asset_id, rendition_id) REFERENCES screenshot_renditions (org_id, asset_id, id), 
	FOREIGN KEY(org_id, asset_id, privacy_review_id) REFERENCES screenshot_privacy_reviews (org_id, asset_id, id), 
	CHECK ((requirement_id IS NOT NULL AND rendition_id IS NULL AND asset_id IS NULL AND privacy_review_id IS NULL) OR (requirement_id IS NULL AND rendition_id IS NOT NULL AND asset_id IS NOT NULL AND privacy_review_id IS NOT NULL)), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE screenshot_suggestions (
	analysis_run_id UUID NOT NULL, 
	image_input_id UUID NOT NULL, 
	requirement_input_id UUID, 
	proposal JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id, analysis_run_id) REFERENCES screenshot_analysis_runs (org_id, task_id, id), 
	FOREIGN KEY(org_id, analysis_run_id, image_input_id) REFERENCES screenshot_analysis_inputs (org_id, analysis_run_id, id), 
	FOREIGN KEY(org_id, analysis_run_id, requirement_input_id) REFERENCES screenshot_analysis_inputs (org_id, analysis_run_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE prototype_decision_batches (
	module_label VARCHAR(200) NOT NULL, 
	task_feature_ids JSONB NOT NULL, 
	target_manifest JSONB NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	idempotency_key UUID NOT NULL, 
	decided_by UUID NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, task_id, idempotency_key), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute("""
CREATE TABLE prototype_evidence_decisions (
	batch_id UUID NOT NULL, 
	evidence_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	card_revision_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	rendition_id UUID NOT NULL, 
	image_sha256 VARCHAR(64) NOT NULL, 
	html_sha256 VARCHAR(64) NOT NULL, 
	task_feature_id UUID NOT NULL, 
	feature_revision_id UUID NOT NULL, 
	previous_decision_id UUID, 
	decision VARCHAR(10) NOT NULL, 
	keep_basis VARCHAR(30), 
	reason TEXT, 
	decided_by UUID NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	UNIQUE (org_id, evidence_id, id), 
	UNIQUE (org_id, batch_id, evidence_id), 
	FOREIGN KEY(org_id, task_id, batch_id) REFERENCES prototype_decision_batches (org_id, task_id, id), 
	FOREIGN KEY(org_id, card_id, evidence_id) REFERENCES evidence (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, card_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id, asset_id, rendition_id, image_sha256) REFERENCES screenshot_renditions (org_id, asset_id, id, image_sha256), 
	FOREIGN KEY(org_id, task_feature_id, task_id, feature_revision_id) REFERENCES task_features (org_id, id, task_id, feature_revision_id), 
	FOREIGN KEY(org_id, feature_revision_id) REFERENCES feature_revisions (org_id, id), 
	FOREIGN KEY(org_id, evidence_id, previous_decision_id) REFERENCES prototype_evidence_decisions (org_id, evidence_id, id), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	CHECK ((decision = 'keep' AND keep_basis IS NOT NULL AND keep_basis IN ('already_delivered','will_deliver')) OR (decision = 'replace' AND keep_basis IS NULL)), 
	CHECK (previous_decision_id IS NULL OR (reason IS NOT NULL AND length(btrim(reason)) > 0)), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)
""")
    op.execute(
        "ALTER TABLE screenshot_renditions ADD CONSTRAINT screenshot_rendition_review_fk FOREIGN KEY(org_id, asset_id, privacy_review_id) REFERENCES screenshot_privacy_reviews (org_id, asset_id, id) DEFERRABLE INITIALLY DEFERRED"
    )
    for table in TABLES:
        op.execute(f'CREATE INDEX ix_{table}_org_id ON "{table}" (org_id)')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"CREATE POLICY tenant_scope ON {table} USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)"
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
        op.execute(
            f'CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION response_tenant_insert_gate()'
        )
        op.execute(
            f'CREATE TRIGGER screenshot_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION response_immutable_gate()'
        )
    _image_evidence()
    _gates()


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain image provenance and decisions")


def _image_evidence():
    op.execute(
        "ALTER TABLE usage_records ADD COLUMN image_count integer NOT NULL DEFAULT 0 CHECK (image_count BETWEEN 0 AND 20)"
    )
    op.execute("ALTER TABLE usage_records ADD COLUMN image_price_revision varchar(100)")
    op.execute("ALTER TABLE usage_records ADD COLUMN image_input_sha256 varchar(64)")
    op.execute(
        "ALTER TABLE usage_records ADD CONSTRAINT usage_image_manifest CHECK ((image_count = 0 AND image_price_revision IS NULL AND image_input_sha256 IS NULL) OR (image_count > 0 AND image_price_revision IS NOT NULL AND image_input_sha256 IS NOT NULL AND image_input_sha256 ~ '^[0-9a-f]{64}$'))"
    )
    op.execute("ALTER TABLE evidence ALTER COLUMN quote DROP NOT NULL")
    op.execute("ALTER TABLE evidence ADD COLUMN screenshot_asset_id uuid")
    op.execute("ALTER TABLE evidence ADD COLUMN screenshot_rendition_id uuid")
    op.execute("ALTER TABLE evidence ADD COLUMN image_sha256 varchar(64)")
    op.execute("ALTER TABLE evidence ADD COLUMN region jsonb")
    op.execute("ALTER TABLE evidence ADD COLUMN claim_scope varchar(40)")
    op.execute("ALTER TABLE evidence ADD COLUMN visual_observation text")
    op.execute(
        "ALTER TABLE evidence ADD CONSTRAINT evidence_image_asset_fk FOREIGN KEY (org_id,task_id,screenshot_asset_id) REFERENCES screenshot_assets(org_id,task_id,id)"
    )
    op.execute(
        "ALTER TABLE evidence ADD CONSTRAINT evidence_image_rendition_fk FOREIGN KEY (org_id,screenshot_asset_id,screenshot_rendition_id,image_sha256) REFERENCES screenshot_renditions(org_id,asset_id,id,image_sha256)"
    )
    # Additive: replacing token_forbidden_scopes would drop earlier bans such as billing:redeem.
    op.execute(
        "ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_screenshot_scopes "
        "CHECK (NOT (scopes ? 'screenshot:ingest'))"
    )
    op.execute("""
-- Integrate this fragment into migration 0023 after the screenshot tables and
-- the new evidence columns/FKs exist.  It replaces the 0015 evidence checks
-- and functions whose closed list otherwise rejects image_region evidence.
-- 0023 must also ALTER evidence.quote DROP NOT NULL and add the columns/FKs
-- represented by app.models.response_cards.Evidence.

ALTER TABLE evidence DROP CONSTRAINT evidence_gate_0;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_1;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_2;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_3;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_4;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_5;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_6;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_7;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_8;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_9;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_10;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_11;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_12;
ALTER TABLE evidence DROP CONSTRAINT evidence_gate_13;

ALTER TABLE evidence ADD CONSTRAINT evidence_gate_0 CHECK (
  kind IN ('product','feature','certificate','org_profile','certificate_pdf_page','image_region')
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_1 CHECK (
  (kind='image_region' AND quote IS NULL)
  OR (kind<>'image_region' AND quote IS NOT NULL
    AND length(quote)<=20000 AND quote ~ '[^[:space:]]')
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_2 CHECK (
  (confirmed_by IS NULL)=(confirmed_at IS NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_3 CHECK (
  (kind='product' OR (kind='image_region' AND material_kind IN ('vendor_web','vendor_pdf')))
  = (task_resource_id IS NOT NULL AND product_revision_id IS NOT NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_4 CHECK (
  (task_resource_id IS NULL)=(product_revision_id IS NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_5 CHECK (
  (kind='feature' OR (kind='image_region' AND material_kind IN
    ('user_screenshot','browser_screenshot','user_diagram','prototype')))
  = (task_feature_id IS NOT NULL AND feature_revision_id IS NOT NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_6 CHECK (
  (task_feature_id IS NULL)=(feature_revision_id IS NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_7 CHECK (
  (kind IN ('certificate','certificate_pdf_page')
    OR (kind='image_region' AND material_kind='certificate_image'))
  = (task_certificate_id IS NOT NULL AND certificate_revision_id IS NOT NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_8 CHECK (
  (task_certificate_id IS NULL)=(certificate_revision_id IS NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_9 CHECK (
  (kind='org_profile')=(task_org_profile_id IS NOT NULL AND profile_revision_id IS NOT NULL)
  AND (task_org_profile_id IS NULL)=(profile_revision_id IS NULL)
);
ALTER TABLE evidence ADD CONSTRAINT evidence_gate_10 CHECK (
  (
    (kind='certificate_pdf_page'
    AND evidence_source_id IS NOT NULL AND field_path IS NULL
    AND page IS NOT NULL AND page>0
    AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$'
    AND material_kind='user_supplied_pdf_page'
    AND quote_check IN ('unreviewed_page','human_page_review')
    AND screenshot_asset_id IS NULL AND screenshot_rendition_id IS NULL
    AND image_sha256 IS NULL AND region IS NULL AND claim_scope IS NULL
    AND visual_observation IS NULL)
  OR (
    kind IN ('product','feature','certificate','org_profile')
    AND evidence_source_id IS NULL AND field_path IS NOT NULL AND field_path ~ '[^[:space:]]'
    AND page IS NULL AND source_sha256 IS NULL
    AND material_kind='declaration' AND quote_check='exact_field_match'
    AND screenshot_asset_id IS NULL AND screenshot_rendition_id IS NULL
    AND image_sha256 IS NULL AND region IS NULL AND claim_scope IS NULL
    AND visual_observation IS NULL)
  OR (
    kind='image_region'
    AND field_path IS NULL
    AND screenshot_asset_id IS NOT NULL AND screenshot_rendition_id IS NOT NULL
    AND image_sha256 IS NOT NULL AND image_sha256 ~ '^[0-9a-f]{64}$'
    AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$'
    AND (
      (material_kind='certificate_image' AND evidence_source_id IS NOT NULL
        AND page IS NOT NULL AND page>0)
      OR (material_kind<>'certificate_image' AND evidence_source_id IS NULL AND page IS NULL)
    )
    AND region IS NOT NULL AND jsonb_typeof(region)='object'
    AND region ?& ARRAY['x','y','width','height']
    AND (region - ARRAY['x','y','width','height']::text[])='{}'::jsonb
    AND (region->>'x') ~ '^[0-9]+$' AND (region->>'y') ~ '^[0-9]+$'
    AND (region->>'width') ~ '^[1-9][0-9]*$' AND (region->>'height') ~ '^[1-9][0-9]*$'
    AND (region->>'x')::integer<=8192 AND (region->>'y')::integer<=8192
    AND (region->>'width')::integer<=8192 AND (region->>'height')::integer<=8192
    AND claim_scope IS NOT NULL
    AND claim_scope IN ('functional_observation','design_explanation',
      'document_excerpt','hardware_documentation')
    AND visual_observation IS NOT NULL
    AND length(visual_observation)<=4000 AND visual_observation ~ '[^[:space:]]'
    AND material_kind IN ('user_screenshot','browser_screenshot','user_diagram',
      'certificate_image','vendor_web','vendor_pdf','prototype')
    AND quote_check IN ('unreviewed_image','human_image_review')
    AND (
      (claim_scope='functional_observation' AND material_kind IN
        ('user_screenshot','browser_screenshot','prototype'))
      OR (claim_scope='design_explanation' AND material_kind='user_diagram')
      OR (claim_scope='document_excerpt' AND material_kind='certificate_image')
      OR (claim_scope='hardware_documentation' AND material_kind IN ('vendor_web','vendor_pdf'))
    )
  ))
  AND (quote_check NOT IN ('human_page_review','human_image_review') OR confirmed_by IS NOT NULL)
  AND (confirmed_by IS NULL OR quote_check NOT IN ('unreviewed_page','unreviewed_image'))
);

CREATE OR REPLACE FUNCTION response_evidence_active(p_org uuid,p_evidence uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT CASE e.kind
   WHEN 'product' THEN EXISTS(SELECT 1 FROM public.task_resources s
     WHERE s.org_id=e.org_id AND s.id=e.task_resource_id AND s.active)
   WHEN 'feature' THEN EXISTS(SELECT 1 FROM public.task_features s
     WHERE s.org_id=e.org_id AND s.id=e.task_feature_id AND s.active)
   WHEN 'certificate' THEN EXISTS(SELECT 1 FROM public.task_certificates s
     WHERE s.org_id=e.org_id AND s.id=e.task_certificate_id AND s.active)
   WHEN 'certificate_pdf_page' THEN EXISTS(SELECT 1 FROM public.task_certificates s
     WHERE s.org_id=e.org_id AND s.id=e.task_certificate_id AND s.active)
   WHEN 'org_profile' THEN EXISTS(SELECT 1 FROM public.task_org_profiles s
     WHERE s.org_id=e.org_id AND s.id=e.task_org_profile_id AND s.active)
   WHEN 'image_region' THEN
     NOT EXISTS(SELECT 1 FROM public.screenshot_withdrawals w
       WHERE w.org_id=e.org_id AND w.asset_id=e.screenshot_asset_id)
     AND EXISTS(
       SELECT 1 FROM public.screenshot_assets a
       JOIN public.screenshot_renditions r
         ON r.org_id=a.org_id AND r.task_id=a.task_id AND r.asset_id=a.id
       JOIN public.response_cards c
         ON c.org_id=e.org_id AND c.task_id=e.task_id AND c.id=e.card_id
        AND c.extraction_job_id=a.extraction_job_id
       JOIN public.screenshot_privacy_reviews p
         ON p.org_id=r.org_id AND p.asset_id=r.asset_id AND p.id=r.privacy_review_id
       JOIN public.screenshot_renditions released
         ON released.org_id=p.org_id AND released.asset_id=p.asset_id
        AND released.id=p.rendition_id AND released.image_sha256=p.stored_image_sha256
       WHERE a.org_id=e.org_id AND a.task_id=e.task_id AND a.id=e.screenshot_asset_id
         AND r.id=e.screenshot_rendition_id AND r.image_sha256=e.image_sha256
         AND (e.material_kind NOT IN ('user_screenshot','browser_screenshot')
           OR a.source->>'environment' IN ('production','test','development'))
         AND (e.material_kind<>'prototype' OR a.source_kind='prototype_render'
           OR a.source->>'environment'='prototype')
     )
     AND CASE
       WHEN e.material_kind IN ('user_screenshot','browser_screenshot','user_diagram','prototype')
         THEN EXISTS(SELECT 1 FROM public.task_features s
           WHERE s.org_id=e.org_id AND s.id=e.task_feature_id
             AND s.feature_revision_id=e.feature_revision_id AND s.active)
       WHEN e.material_kind IN ('vendor_web','vendor_pdf')
         THEN EXISTS(SELECT 1 FROM public.task_resources s
           WHERE s.org_id=e.org_id AND s.id=e.task_resource_id
             AND s.product_revision_id=e.product_revision_id AND s.active)
       WHEN e.material_kind='certificate_image'
         THEN EXISTS(SELECT 1 FROM public.task_certificates s
           WHERE s.org_id=e.org_id AND s.id=e.task_certificate_id
             AND s.certificate_revision_id=e.certificate_revision_id AND s.active)
       ELSE false END
   ELSE false END
  FROM public.evidence e WHERE e.org_id=p_org AND e.id=p_evidence
$$;

CREATE OR REPLACE FUNCTION response_evidence_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE domain text; card_state text; actor uuid; material jsonb; field_value text;
        source public.evidence_sources%ROWTYPE; asset public.screenshot_assets%ROWTYPE;
        rendition public.screenshot_renditions%ROWTYPE; expected_material text;
        selection_active boolean;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Evidence cannot be deleted' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' THEN
    IF (to_jsonb(NEW)-'confirmed_by'-'confirmed_at'-'quote_check') IS DISTINCT FROM
       (to_jsonb(OLD)-'confirmed_by'-'confirmed_at'-'quote_check')
       OR OLD.confirmed_by IS NOT NULL OR NEW.confirmed_by IS NULL THEN
      RAISE EXCEPTION 'Evidence is immutable after confirmation' USING ERRCODE='42501';
    END IF;
  ELSIF NEW.confirmed_by IS NOT NULL THEN
    RAISE EXCEPTION 'New evidence must be unconfirmed' USING ERRCODE='23514';
  END IF;
  IF NEW.confirmed_by IS NOT NULL THEN
    SELECT r.review_domain,r.state INTO domain,card_state
      FROM public.response_cards c JOIN public.response_card_revisions r
        ON r.org_id=c.org_id AND r.id=c.current_revision_id
      WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
    actor := public.response_require_human(NEW.org_id,domain);
    IF card_state IS DISTINCT FROM 'pending_review' OR NEW.confirmed_by IS DISTINCT FROM actor
       OR NEW.confirmed_at IS NULL
       OR public.response_evidence_active(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Invalid evidence confirmation' USING ERRCODE='23514';
    END IF;
  END IF;
  CASE NEW.kind
    WHEN 'product' THEN SELECT data INTO material FROM public.product_revisions
      WHERE org_id=NEW.org_id AND id=NEW.product_revision_id;
    WHEN 'feature' THEN SELECT data INTO material FROM public.feature_revisions
      WHERE org_id=NEW.org_id AND id=NEW.feature_revision_id;
    WHEN 'certificate' THEN SELECT data INTO material FROM public.certificate_revisions
      WHERE org_id=NEW.org_id AND id=NEW.certificate_revision_id;
    WHEN 'org_profile' THEN SELECT data INTO material FROM public.org_profile_revisions
      WHERE org_id=NEW.org_id AND id=NEW.profile_revision_id;
    WHEN 'certificate_pdf_page' THEN
      SELECT * INTO source FROM public.evidence_sources
        WHERE org_id=NEW.org_id AND id=NEW.evidence_source_id;
      IF source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
        OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
        OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
        OR source.page IS DISTINCT FROM NEW.page
        OR source.preview->>'sha256' IS DISTINCT FROM NEW.source_sha256 THEN
        RAISE EXCEPTION 'Evidence page does not match retained source' USING ERRCODE='23514';
      END IF;
    WHEN 'image_region' THEN
      SELECT * INTO asset FROM public.screenshot_assets
        WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND id=NEW.screenshot_asset_id;
      SELECT * INTO rendition FROM public.screenshot_renditions
        WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND asset_id=NEW.screenshot_asset_id
          AND id=NEW.screenshot_rendition_id AND image_sha256=NEW.image_sha256;
      IF NEW.material_kind='certificate_image' THEN
        SELECT * INTO source FROM public.evidence_sources
          WHERE org_id=NEW.org_id AND id=asset.evidence_source_id;
      END IF;
      expected_material := CASE
        WHEN asset.origin='prototype' THEN 'prototype'
        WHEN asset.source_kind='local_browser' THEN 'browser_screenshot'
        WHEN asset.source_kind='certificate_page' THEN 'certificate_image'
        WHEN asset.source_kind IN ('vendor_web','vendor_pdf') THEN asset.source_kind
        WHEN asset.source_kind='upload' AND asset.image_kind='diagram' THEN 'user_diagram'
        WHEN asset.source_kind='upload' AND asset.image_kind='screenshot' THEN 'user_screenshot'
        ELSE NULL END;
      selection_active := CASE
        WHEN NEW.material_kind IN ('user_screenshot','browser_screenshot','user_diagram','prototype')
          THEN EXISTS(SELECT 1 FROM public.task_features s
            WHERE s.org_id=NEW.org_id AND s.id=NEW.task_feature_id
              AND s.feature_revision_id=NEW.feature_revision_id AND s.active)
        WHEN NEW.material_kind IN ('vendor_web','vendor_pdf')
          THEN EXISTS(SELECT 1 FROM public.task_resources s
            WHERE s.org_id=NEW.org_id AND s.id=NEW.task_resource_id
              AND s.product_revision_id=NEW.product_revision_id AND s.active)
        WHEN NEW.material_kind='certificate_image'
          THEN EXISTS(SELECT 1 FROM public.task_certificates s
            WHERE s.org_id=NEW.org_id AND s.id=NEW.task_certificate_id
              AND s.certificate_revision_id=NEW.certificate_revision_id AND s.active)
        ELSE false END;
      IF asset.id IS NULL OR rendition.id IS NULL OR expected_material IS DISTINCT FROM NEW.material_kind
        OR NEW.source_sha256 IS DISTINCT FROM asset.source_sha256
        OR asset.extraction_job_id IS DISTINCT FROM (
          SELECT c.extraction_job_id FROM public.response_cards c
          WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id AND c.task_id=NEW.task_id)
        OR (NEW.region->>'x')::integer+(NEW.region->>'width')::integer
             > (rendition.mapping->>'content_width')::integer
        OR (NEW.region->>'y')::integer+(NEW.region->>'height')::integer
             > (rendition.mapping->>'content_height')::integer
        OR (NEW.task_feature_id,NEW.feature_revision_id) IS DISTINCT FROM
             (asset.task_feature_id,asset.feature_revision_id)
        OR (NEW.task_resource_id,NEW.product_revision_id) IS DISTINCT FROM
             (asset.task_resource_id,asset.product_revision_id)
        OR (NEW.material_kind='certificate_image'
            AND NEW.evidence_source_id IS DISTINCT FROM asset.evidence_source_id)
        OR (NEW.material_kind='certificate_image' AND (
          source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
          OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
          OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
          OR source.page IS DISTINCT FROM NEW.page))
        OR selection_active IS DISTINCT FROM true
        OR (NEW.material_kind IN ('user_screenshot','browser_screenshot')
            AND (asset.source->>'environment' IS NULL
              OR asset.source->>'environment' NOT IN ('production','test','development')))
        OR (NEW.material_kind='prototype' AND asset.source_kind<>'prototype_render'
            AND asset.source->>'environment' IS DISTINCT FROM 'prototype')
        OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals w
          WHERE w.org_id=NEW.org_id AND w.asset_id=NEW.screenshot_asset_id)
        OR NOT EXISTS(SELECT 1 FROM public.screenshot_privacy_reviews p
          JOIN public.screenshot_renditions released
            ON released.org_id=p.org_id AND released.asset_id=p.asset_id
           AND released.id=p.rendition_id AND released.image_sha256=p.stored_image_sha256
          WHERE p.org_id=NEW.org_id AND p.asset_id=NEW.screenshot_asset_id
            AND p.id=rendition.privacy_review_id) THEN
        RAISE EXCEPTION 'Image evidence does not match retained rendition' USING ERRCODE='23514';
      END IF;
    ELSE RAISE EXCEPTION 'Unknown material kind' USING ERRCODE='23514';
  END CASE;
  IF NEW.kind NOT IN ('certificate_pdf_page','image_region') THEN
    IF NOT (CASE NEW.kind
      WHEN 'product' THEN NEW.field_path IN ('name','vendor','model','model_version','official_url','whitepaper_url')
      WHEN 'feature' THEN NEW.field_path IN ('product_id','name','description','status')
      WHEN 'certificate' THEN NEW.field_path IN ('name','number','kind','valid_from','valid_until')
      WHEN 'org_profile' THEN NEW.field_path IN ('name','registration_details','performance_summary','standard_wording')
      ELSE false END) THEN
      RAISE EXCEPTION 'Evidence field is not referenceable' USING ERRCODE='23514';
    END IF;
    field_value := material->>NEW.field_path;
    IF field_value IS NULL OR position(NEW.quote in field_value)=0 THEN
      RAISE EXCEPTION 'Evidence quote is not exact field text' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
""")


def _gates():
    op.execute(
        """
CREATE FUNCTION screenshot_require_ingest(p_org uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE actor uuid;
BEGIN
  actor := NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
  IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
     OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
     OR p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
     OR NOT EXISTS(SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
       JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=actor
       AND m.active AND u.active AND o.active AND m.role IN ('admin','bidder','technical')) THEN
    RAISE EXCEPTION 'Human privacy release required' USING ERRCODE='42501';
  END IF;
  RETURN actor;
END $$;

CREATE FUNCTION screenshot_scope_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE body jsonb; parent jsonb; relation record; parent_id uuid;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.jobs j JOIN public.documents d
      ON d.org_id=j.org_id AND d.id=j.document_id AND d.task_id=j.task_id
      WHERE j.org_id=NEW.org_id AND j.id=NEW.extraction_job_id
        AND j.task_id=NEW.task_id AND j.kind='extract' AND j.status='succeeded') THEN
    RAISE EXCEPTION 'Screenshot requires its succeeded extraction' USING ERRCODE='23514';
  END IF;
  body := to_jsonb(NEW);
  FOR relation IN SELECT * FROM (VALUES
    ('asset_id','screenshot_assets'),('parent_rendition_id','screenshot_renditions'),
    ('rendition_id','screenshot_renditions'),('vendor_archive_id','screenshot_vendor_archives'),
    ('prototype_run_id','screenshot_prototype_runs'),('search_run_id','screenshot_search_runs'),
    ('search_candidate_id','screenshot_search_candidates'),('analysis_run_id','screenshot_analysis_runs'),
    ('batch_id','prototype_decision_batches'),('previous_decision_id','prototype_evidence_decisions')
  ) AS refs(field_name,table_name) LOOP
    parent_id := (body->>relation.field_name)::uuid;
    IF parent_id IS NOT NULL THEN
      EXECUTE format('SELECT to_jsonb(p) FROM public.%I p WHERE p.org_id=$1 AND p.id=$2', relation.table_name)
        INTO parent USING NEW.org_id,parent_id;
      IF parent IS NULL OR parent->>'task_id' IS DISTINCT FROM NEW.task_id::text
        OR parent->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
        RAISE EXCEPTION 'Screenshot parent scope mismatch' USING ERRCODE='23514';
      END IF;
    END IF;
  END LOOP;
  IF body->>'requirement_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.requirements r
    WHERE r.org_id=NEW.org_id AND r.id=(body->>'requirement_id')::uuid
      AND r.task_id=NEW.task_id AND r.job_id=NEW.extraction_job_id) THEN
    RAISE EXCEPTION 'Screenshot requirement scope mismatch' USING ERRCODE='23514';
  END IF;
  IF body->>'task_feature_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.task_features s
    WHERE s.org_id=NEW.org_id AND s.id=(body->>'task_feature_id')::uuid AND s.task_id=NEW.task_id
      AND s.feature_revision_id=(body->>'feature_revision_id')::uuid AND s.active) THEN
    RAISE EXCEPTION 'Screenshot feature binding mismatch' USING ERRCODE='23514';
  END IF;
  IF body->>'task_resource_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.task_resources s
    WHERE s.org_id=NEW.org_id AND s.id=(body->>'task_resource_id')::uuid AND s.task_id=NEW.task_id
      AND s.product_revision_id=(body->>'product_revision_id')::uuid AND s.active) THEN
    RAISE EXCEPTION 'Screenshot product binding mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_asset_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE proto public.screenshot_prototype_runs%ROWTYPE;
BEGIN
  IF NEW.received_by IS DISTINCT FROM public.screenshot_require_ingest(NEW.org_id) THEN
    RAISE EXCEPTION 'Privacy actor mismatch' USING ERRCODE='42501';
  END IF;
  IF jsonb_typeof(NEW.source) IS DISTINCT FROM 'object' OR NEW.source->>'kind' IS DISTINCT FROM NEW.source_kind THEN
    RAISE EXCEPTION 'Source metadata mismatch' USING ERRCODE='23514';
  END IF;
  IF NOT (
    (NEW.origin='user' AND NEW.source_kind='upload' AND NEW.image_kind IN ('screenshot','diagram')
      AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL AND NEW.prototype_run_id IS NULL
      AND NEW.evidence_source_id IS NULL AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='browser' AND NEW.source_kind='local_browser' AND NEW.image_kind='screenshot'
      AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL AND NEW.prototype_run_id IS NULL
      AND NEW.evidence_source_id IS NULL AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='certificate' AND NEW.source_kind='certificate_page' AND NEW.image_kind='certificate_page'
      AND NEW.evidence_source_id IS NOT NULL AND NEW.task_feature_id IS NULL AND NEW.feature_revision_id IS NULL AND NEW.prototype_run_id IS NULL
      AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='vendor' AND NEW.source_kind IN ('vendor_web','vendor_pdf') AND NEW.image_kind='vendor_page'
      AND NEW.vendor_archive_id IS NOT NULL AND NEW.task_resource_id IS NOT NULL AND NEW.product_revision_id IS NOT NULL
      AND NEW.task_feature_id IS NULL AND NEW.feature_revision_id IS NULL AND NEW.prototype_run_id IS NULL AND NEW.evidence_source_id IS NULL)
    OR (NEW.origin='prototype' AND NEW.source_kind IN ('prototype_render','upload','local_browser') AND NEW.image_kind='prototype'
      AND NEW.prototype_run_id IS NOT NULL AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL
      AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL AND NEW.evidence_source_id IS NULL)
  ) THEN RAISE EXCEPTION 'Image source branches are exclusive' USING ERRCODE='23514'; END IF;
  IF NEW.source_kind IN ('upload','local_browser') AND (
    (NEW.source->>'task_feature_id')::uuid IS DISTINCT FROM NEW.task_feature_id
    OR NEW.source->>'image_kind' IS DISTINCT FROM NEW.image_kind
    OR (NEW.origin='prototype') IS DISTINCT FROM (NEW.source->>'environment'='prototype')
    OR NEW.source_hash_assurance <> 'client_declared') THEN
    RAISE EXCEPTION 'Client source declaration mismatch' USING ERRCODE='23514';
  END IF;
  IF NEW.evidence_source_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.evidence_sources e
      JOIN public.task_certificates s ON s.org_id=e.org_id AND s.id=e.task_certificate_id
      WHERE e.org_id=NEW.org_id AND e.id=NEW.evidence_source_id AND e.task_id=NEW.task_id AND s.active
        AND e.preview->>'sha256'=NEW.source_sha256
        AND (NEW.source->>'evidence_source_id')::uuid=e.id AND NEW.source_hash_assurance='server_verified') THEN
    RAISE EXCEPTION 'Certificate source binding mismatch' USING ERRCODE='23514';
  END IF;
  IF NEW.prototype_run_id IS NOT NULL THEN
    SELECT * INTO proto FROM public.screenshot_prototype_runs WHERE org_id=NEW.org_id AND id=NEW.prototype_run_id;
    IF (proto.task_feature_id,proto.feature_revision_id,proto.source_image_sha256) IS DISTINCT FROM
       (NEW.task_feature_id,NEW.feature_revision_id,NEW.source_sha256)
       OR (NEW.source->>'prototype_run_id')::uuid IS DISTINCT FROM proto.id THEN
      RAISE EXCEPTION 'Prototype render binding mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_rendition_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE asset public.screenshot_assets%ROWTYPE; parent public.screenshot_renditions%ROWTYPE;
        w integer; h integer; cw integer; ch integer; ox integer; oy integer; fh integer;
        source_w integer; source_h integer; crop jsonb; region jsonb;
BEGIN
  SELECT * INTO asset FROM public.screenshot_assets WHERE org_id=NEW.org_id AND id=NEW.asset_id;
  IF asset.id IS NULL OR NEW.source_sha256 IS DISTINCT FROM asset.source_sha256
    OR NEW.profile IS DISTINCT FROM (CASE WHEN asset.origin='prototype' THEN 'prototype-clean-v1' ELSE 'screenshot-markup-v1' END)
    OR NEW.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
    OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE org_id=NEW.org_id AND asset_id=asset.id) THEN
    RAISE EXCEPTION 'Invalid rendition source' USING ERRCODE='23514';
  END IF;
  IF NEW.parent_rendition_id IS NOT NULL THEN
    SELECT * INTO parent FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.parent_rendition_id;
    IF parent.asset_id IS DISTINCT FROM NEW.asset_id OR parent.privacy_review_id IS DISTINCT FROM NEW.privacy_review_id
      OR parent.upload_sha256 IS DISTINCT FROM NEW.upload_sha256 OR NEW.generation_job_id IS NULL THEN
      RAISE EXCEPTION 'Rendition cannot change its privacy lineage' USING ERRCODE='23514';
    END IF;
    source_w := (parent.mapping->>'content_width')::integer; source_h := (parent.mapping->>'content_height')::integer;
  ELSE
    PERFORM public.screenshot_require_ingest(NEW.org_id);
    source_w := asset.source_width; source_h := asset.source_height;
  END IF;
  w := (NEW.image->>'width_px')::integer; h := (NEW.image->>'height_px')::integer;
  cw := (NEW.mapping->>'content_width')::integer; ch := (NEW.mapping->>'content_height')::integer;
  ox := (NEW.mapping->>'content_offset_x')::integer; oy := (NEW.mapping->>'content_offset_y')::integer;
  fh := (NEW.mapping->>'footer_height')::integer; crop := NEW.mapping->'crop';
  IF (jsonb_typeof(NEW.image)='object' AND jsonb_typeof(NEW.mapping)='object' AND jsonb_typeof(NEW.plan)='object'
    AND NEW.image->>'sha256'=NEW.image_sha256 AND NEW.image->>'media_type'='image/png'
    AND w BETWEEN 1 AND 8192 AND h BETWEEN 1 AND 8192 AND w::bigint*h <= 20000000
    AND (NEW.image->>'size_bytes')::integer BETWEEN 1 AND 41943040
    AND cw>0 AND ch>0 AND ox>=0 AND oy=0 AND fh>=0 AND ox+cw<=w AND ch+fh=h
    AND (crop->>'width')::integer=cw AND (crop->>'height')::integer=ch
    AND (crop->>'x')::integer>=0 AND (crop->>'y')::integer>=0
    AND (crop->>'x')::integer+cw<=source_w AND (crop->>'y')::integer+ch<=source_h
    AND NEW.plan ?& ARRAY['redact','crop','boxes'] AND NEW.plan - ARRAY['redact','crop','boxes'] = '{}'::jsonb
    AND jsonb_typeof(NEW.plan->'redact')='array' AND jsonb_array_length(NEW.plan->'redact')<=200
    AND jsonb_typeof(NEW.plan->'boxes')='array' AND jsonb_array_length(NEW.plan->'boxes')<=20
    AND ((asset.origin='prototype' AND fh=0 AND ox=0 AND cw=w) OR (asset.origin<>'prototype' AND fh>0))
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Invalid bounded RGB rendition descriptor' USING ERRCODE='23514';
  END IF;
  FOR region IN SELECT value FROM jsonb_array_elements((NEW.plan->'redact') || (NEW.plan->'boxes')) LOOP
    IF ((region->>'x')::integer>=0 AND (region->>'y')::integer>=0 AND (region->>'width')::integer>0 AND (region->>'height')::integer>0
      AND (region->>'x')::integer+(region->>'width')::integer<=source_w
      AND (region->>'y')::integer+(region->>'height')::integer<=source_h) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Invalid image rectangle' USING ERRCODE='23514';
    END IF;
  END LOOP;
  FOR region IN SELECT value FROM jsonb_array_elements(NEW.plan->'boxes') LOOP
    IF ((region->>'x')::integer >= (crop->>'x')::integer AND (region->>'y')::integer >= (crop->>'y')::integer
      AND (region->>'x')::integer+(region->>'width')::integer <= (crop->>'x')::integer+cw
      AND (region->>'y')::integer+(region->>'height')::integer <= (crop->>'y')::integer+ch) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Box outside crop' USING ERRCODE='23514';
    END IF;
  END LOOP;
  IF NEW.generation_job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs j WHERE j.org_id=NEW.org_id
      AND j.id=NEW.generation_job_id AND j.task_id=NEW.task_id AND j.kind='screenshot_render' AND j.status='running') THEN
    RAISE EXCEPTION 'Rendition requires its live rendering job' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_privacy_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rendition public.screenshot_renditions%ROWTYPE;
BEGIN
  IF NEW.reviewed_by IS DISTINCT FROM public.screenshot_require_ingest(NEW.org_id) THEN
    RAISE EXCEPTION 'Privacy reviewer mismatch' USING ERRCODE='42501';
  END IF;
  SELECT * INTO rendition FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.rendition_id;
  IF rendition.id IS NULL OR rendition.parent_rendition_id IS NOT NULL
    OR rendition.privacy_review_id IS DISTINCT FROM NEW.id OR rendition.asset_id IS DISTINCT FROM NEW.asset_id
    OR rendition.image_sha256 IS DISTINCT FROM NEW.stored_image_sha256
    OR rendition.upload_sha256 IS DISTINCT FROM NEW.reviewed_upload_sha256
    OR NEW.rule_version IS DISTINCT FROM 'screenshot-privacy-v1' THEN
    RAISE EXCEPTION 'Privacy review must bind exact uploaded and stored hashes' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_withdraw_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF NEW.withdrawn_by IS DISTINCT FROM public.screenshot_require_ingest(NEW.org_id)
    OR NEW.reason IS NULL OR length(btrim(NEW.reason))=0 THEN
    RAISE EXCEPTION 'Withdrawal requires a human and reason' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_prototype_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.jobs j WHERE j.org_id=NEW.org_id AND j.id=NEW.generation_job_id
    AND j.task_id=NEW.task_id AND j.kind='prototype_generate' AND j.status='running')
    OR NOT (NEW.html_storage_key LIKE 'org/' || NEW.org_id::text || '/%')
    OR NOT (NEW.source_image_key LIKE 'org/' || NEW.org_id::text || '/%')
    OR (NEW.html_sha256 ~ '^[0-9a-f]{64}$' AND NEW.source_image_sha256 ~ '^[0-9a-f]{64}$'
      AND NEW.sandbox_receipt_sha256 ~ '^[0-9a-f]{64}$' AND NEW.input_hash ~ '^[0-9a-f]{64}$'
      AND length(NEW.sandbox_receipt_id)>0 AND jsonb_typeof(NEW.provenance)='object'
      AND length(NEW.provenance->>'provider')>0 AND length(NEW.provenance->>'model')>0
      AND length(NEW.provenance->>'catalog_identity')>0) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Prototype requires a fixed generation and sandbox receipt' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_decision_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE card public.response_cards%ROWTYPE; rev public.response_card_revisions%ROWTYPE;
        evidence public.evidence%ROWTYPE; asset public.screenshot_assets%ROWTYPE; proto public.screenshot_prototype_runs%ROWTYPE;
        batch public.prototype_decision_batches%ROWTYPE; previous uuid; human uuid;
BEGIN
  PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
  SELECT * INTO card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id FOR UPDATE;
  SELECT * INTO rev FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id;
  human := public.response_require_human(NEW.org_id,rev.review_domain);
  SELECT * INTO evidence FROM public.evidence WHERE org_id=NEW.org_id AND id=NEW.evidence_id;
  SELECT * INTO asset FROM public.screenshot_assets WHERE org_id=NEW.org_id AND id=NEW.asset_id;
  SELECT * INTO proto FROM public.screenshot_prototype_runs WHERE org_id=NEW.org_id AND id=asset.prototype_run_id;
  SELECT * INTO batch FROM public.prototype_decision_batches WHERE org_id=NEW.org_id AND id=NEW.batch_id;
  SELECT id INTO previous FROM public.prototype_evidence_decisions WHERE org_id=NEW.org_id AND evidence_id=NEW.evidence_id
    ORDER BY created_at DESC,id DESC LIMIT 1;
  IF NEW.decided_by IS DISTINCT FROM human OR batch.decided_by IS DISTINCT FROM human
    OR card.task_id IS DISTINCT FROM NEW.task_id OR card.extraction_job_id IS DISTINCT FROM NEW.extraction_job_id
    OR card.current_revision_id IS DISTINCT FROM NEW.card_revision_id OR rev.card_id IS DISTINCT FROM card.id OR rev.state IS DISTINCT FROM 'confirmed'
    OR evidence.card_id IS DISTINCT FROM card.id OR evidence.kind IS DISTINCT FROM 'image_region' OR evidence.confirmed_by IS NULL
    OR asset.origin IS DISTINCT FROM 'prototype' OR proto.id IS NULL
    OR (evidence.screenshot_asset_id,evidence.screenshot_rendition_id,evidence.image_sha256) IS DISTINCT FROM (NEW.asset_id,NEW.rendition_id,NEW.image_sha256)
    OR (asset.task_feature_id,asset.feature_revision_id,proto.html_sha256) IS DISTINCT FROM (NEW.task_feature_id,NEW.feature_revision_id,NEW.html_sha256)
    OR NEW.previous_decision_id IS DISTINCT FROM previous
    OR (previous IS NOT NULL AND (NEW.reason IS NULL OR length(btrim(NEW.reason))=0))
    OR NOT (batch.task_feature_ids ? NEW.task_feature_id::text)
    OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=NEW.org_id AND l.revision_id=NEW.card_revision_id AND l.evidence_id=NEW.evidence_id)
    OR public.response_quote_current(NEW.org_id,NEW.card_revision_id) IS DISTINCT FROM true
    OR public.response_citation_valid(NEW.org_id,card.requirement_id) IS DISTINCT FROM true
    OR public.response_evidence_active(NEW.org_id,NEW.evidence_id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Prototype decision binding is stale or incomplete' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_batch_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF NEW.decided_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
    OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
    OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
    OR NOT EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.decided_by AND active AND role IN ('technical','bidder'))
    OR jsonb_typeof(NEW.task_feature_ids) IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.task_feature_ids)=0 THEN
    RAISE EXCEPTION 'Prototype batch requires a responsible human' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_analysis_input_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rendition public.screenshot_renditions%ROWTYPE;
BEGIN
  IF NEW.rendition_id IS NOT NULL THEN
    SELECT * INTO rendition FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.rendition_id;
    IF (rendition.asset_id,rendition.image_sha256,rendition.privacy_review_id) IS DISTINCT FROM (NEW.asset_id,NEW.content_sha256,NEW.privacy_review_id) THEN
      RAISE EXCEPTION 'Analysis image input mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_suggestion_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE img public.screenshot_analysis_inputs%ROWTYPE; req public.screenshot_analysis_inputs%ROWTYPE;
        rendition public.screenshot_renditions%ROWTYPE; region jsonb;
BEGIN
  SELECT * INTO img FROM public.screenshot_analysis_inputs WHERE org_id=NEW.org_id AND id=NEW.image_input_id;
  SELECT * INTO req FROM public.screenshot_analysis_inputs WHERE org_id=NEW.org_id AND id=NEW.requirement_input_id;
  SELECT * INTO rendition FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=img.rendition_id;
  region := NEW.proposal->'region';
  IF img.rendition_id IS NULL OR img.analysis_run_id IS DISTINCT FROM NEW.analysis_run_id
    OR (NEW.requirement_input_id IS NOT NULL AND (req.requirement_id IS NULL OR req.analysis_run_id IS DISTINCT FROM NEW.analysis_run_id))
    OR NEW.proposal->>'image_ref' IS DISTINCT FROM img.ref
    OR NEW.proposal->>'requirement_ref' IS DISTINCT FROM req.ref
    OR (NEW.proposal->>'purpose' IN ('match_requirements','propose_regions','read_text')) IS DISTINCT FROM true
    OR (NEW.proposal->>'purpose'='match_requirements' AND req.requirement_id IS NULL)
    OR (NEW.proposal->>'purpose' IN ('propose_regions','read_text') AND (region IS NULL OR region='null'::jsonb))
    OR (NEW.proposal->>'purpose'='read_text' AND coalesce(length(btrim(NEW.proposal->>'suggested_text')),0)=0) THEN
    RAISE EXCEPTION 'Suggestion requires locally bound inputs' USING ERRCODE='23514';
  END IF;
  IF region IS NOT NULL AND region<>'null'::jsonb AND (
    (region->>'x')::integer >= 0 AND (region->>'y')::integer >= 0
    AND (region->>'width')::integer > 0 AND (region->>'height')::integer > 0
    AND (region->>'x')::integer+(region->>'width')::integer <= (rendition.mapping->>'content_width')::integer
    AND (region->>'y')::integer+(region->>'height')::integer <= (rendition.mapping->>'content_height')::integer
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Suggestion region must use visible content coordinates' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION screenshot_batch_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE batch_id uuid; batch public.prototype_decision_batches%ROWTYPE; target jsonb; item public.prototype_evidence_decisions%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME='prototype_decision_batches' THEN batch_id:=NEW.id; ELSE batch_id:=NEW.batch_id; END IF;
  SELECT * INTO batch FROM public.prototype_decision_batches WHERE org_id=NEW.org_id AND id=batch_id;
  IF jsonb_typeof(batch.target_manifest) IS DISTINCT FROM 'array'
    OR jsonb_array_length(batch.target_manifest) NOT BETWEEN 1 AND 500
    OR (SELECT count(*) FROM public.prototype_evidence_decisions WHERE org_id=NEW.org_id AND prototype_evidence_decisions.batch_id=batch.id) <> jsonb_array_length(batch.target_manifest)
    OR (SELECT count(DISTINCT value->>'evidence_id') FROM jsonb_array_elements(batch.target_manifest)) <> jsonb_array_length(batch.target_manifest) THEN
    RAISE EXCEPTION 'Prototype batch must commit every exact target once' USING ERRCODE='23514';
  END IF;
  FOR target IN SELECT value FROM jsonb_array_elements(batch.target_manifest) LOOP
    SELECT * INTO item FROM public.prototype_evidence_decisions WHERE org_id=batch.org_id
      AND prototype_evidence_decisions.batch_id=batch.id AND evidence_id=(target->>'evidence_id')::uuid;
    IF item.id IS NULL OR (item.card_revision_id,item.rendition_id,item.image_sha256,item.html_sha256,item.task_feature_id,item.previous_decision_id)
      IS DISTINCT FROM ((target->>'card_revision_id')::uuid,(target->>'rendition_id')::uuid,target->>'image_sha256',target->>'html_sha256',
        (target->>'task_feature_id')::uuid,(target->>'expected_previous_decision_id')::uuid) THEN
      RAISE EXCEPTION 'Prototype target manifest changed' USING ERRCODE='23514';
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
"""
    )
    for table in TABLES:
        op.execute(
            f'CREATE TRIGGER screenshot_scope BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION screenshot_scope_gate()'
        )
    for table, function in (
        ("screenshot_assets", "screenshot_asset_gate"),
        ("screenshot_renditions", "screenshot_rendition_gate"),
        ("screenshot_privacy_reviews", "screenshot_privacy_gate"),
        ("screenshot_withdrawals", "screenshot_withdraw_gate"),
        ("screenshot_prototype_runs", "screenshot_prototype_gate"),
        ("prototype_decision_batches", "screenshot_batch_gate"),
        ("prototype_evidence_decisions", "screenshot_decision_gate"),
        ("screenshot_analysis_inputs", "screenshot_analysis_input_gate"),
        ("screenshot_suggestions", "screenshot_suggestion_gate"),
    ):
        op.execute(
            f'CREATE TRIGGER screenshot_shape BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION {function}()'
        )
    op.execute(
        "CREATE UNIQUE INDEX screenshot_single_root ON screenshot_renditions (org_id,asset_id) WHERE parent_rendition_id IS NULL"
    )
    op.execute(
        "CREATE INDEX screenshot_asset_listing ON screenshot_assets (org_id,task_id,extraction_job_id,created_at,id)"
    )
    op.execute(
        "CREATE INDEX screenshot_suggestion_listing ON screenshot_suggestions (org_id,analysis_run_id,created_at,id)"
    )
    for table in ("prototype_decision_batches", "prototype_evidence_decisions"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER screenshot_batch_complete AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION screenshot_batch_complete()"
        )
    op.execute("""
CREATE FUNCTION screenshot_card_warning_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE image record; has_redaction boolean; has_crop boolean;
BEGIN
  IF NEW.state <> 'confirmed' THEN RETURN NULL; END IF;
  FOR image IN SELECT a.*, e.screenshot_rendition_id FROM public.card_evidence_links l
    JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
    JOIN public.screenshot_assets a ON a.org_id=e.org_id AND a.id=e.screenshot_asset_id
    WHERE l.org_id=NEW.org_id AND l.revision_id=NEW.id AND e.kind='image_region' LOOP
    IF NEW.reason IS NULL OR NOT NEW.reviewed_warning_codes ? 'image_visible_scope_only'
      OR (image.origin='prototype' AND NOT NEW.reviewed_warning_codes ? 'prototype_delivery_obligation')
      OR (image.origin<>'prototype' AND NOT NEW.reviewed_warning_codes ? 'image_source_claim')
      OR (image.source->>'environment' IN ('test','development') AND NOT NEW.reviewed_warning_codes ? 'image_test_environment')
      OR (image.image_kind='diagram' AND NOT NEW.reviewed_warning_codes ? 'image_design_only') THEN
      RAISE EXCEPTION 'Image source and scope warnings require human review' USING ERRCODE='23514';
    END IF;
    WITH RECURSIVE lineage AS (
      SELECT r.id,r.parent_rendition_id,r.plan FROM public.screenshot_renditions r
        WHERE r.org_id=NEW.org_id AND r.asset_id=image.id AND r.id=image.screenshot_rendition_id
      UNION ALL
      SELECT p.id,p.parent_rendition_id,p.plan FROM public.screenshot_renditions p JOIN lineage child
        ON p.id=child.parent_rendition_id WHERE p.org_id=NEW.org_id AND p.asset_id=image.id
    ) SELECT bool_or(jsonb_array_length(plan->'redact')>0),bool_or(plan->'crop'<>'null'::jsonb)
      INTO has_redaction,has_crop FROM lineage;
    IF (has_redaction AND NOT NEW.reviewed_warning_codes ? 'image_redaction_review')
      OR (has_crop AND NOT NEW.reviewed_warning_codes ? 'image_crop_review') THEN
      RAISE EXCEPTION 'Image crop and redaction warnings require human review' USING ERRCODE='23514';
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER screenshot_card_warning_gate AFTER INSERT ON response_card_revisions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION screenshot_card_warning_gate();
""")
    op.execute("""
CREATE FUNCTION screenshot_selection_no_reactivation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF OLD.active=false AND NEW.active=true THEN
    RAISE EXCEPTION 'Historical selections cannot be reactivated; create a new selection' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
""")
    for table in ("task_features", "task_resources", "task_certificates"):
        op.execute(
            f"CREATE TRIGGER screenshot_selection_no_reactivation BEFORE UPDATE OF active ON {table} FOR EACH ROW EXECUTE FUNCTION screenshot_selection_no_reactivation()"
        )
