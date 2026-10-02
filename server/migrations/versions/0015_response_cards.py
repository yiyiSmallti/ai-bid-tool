"""Add append-only response review, human decision gates and complete draft snapshots."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = (
    "response_cards",
    "response_card_revisions",
    "evidence",
    "card_evidence_links",
    "card_generation_runs",
    "draft_runs",
    "response_items",
)


def upgrade():
    op.execute("ALTER TABLE tasks ADD COLUMN model_redaction_enabled boolean NOT NULL DEFAULT true")
    op.execute(
        "ALTER TABLE tasks ADD COLUMN model_redaction_revision integer NOT NULL DEFAULT 1 CHECK (model_redaction_revision > 0)"
    )
    op.execute("ALTER TABLE tasks ADD COLUMN model_redaction_by uuid")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT task_redaction_actor FOREIGN KEY (org_id, model_redaction_by) REFERENCES memberships (org_id, user_id)"
    )
    op.execute("""
CREATE TABLE response_cards (
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	current_revision_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	UNIQUE (org_id, task_id, extraction_job_id, requirement_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, requirement_id) REFERENCES requirements (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE response_card_revisions (
	card_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(30) NOT NULL, 
	review_domain VARCHAR(20), 
	disposition VARCHAR(20), 
	disposition_by UUID, 
	disposition_at TIMESTAMP WITH TIME ZONE, 
	response_kind VARCHAR(20), 
	suggested_disposition VARCHAR(20), 
	response_text TEXT, 
	deviation VARCHAR(20), 
	deviation_note TEXT, 
	review_hint VARCHAR(30), 
	reason TEXT, 
	reviewed_warning_codes JSONB NOT NULL, 
	confirmed_by UUID, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	origin VARCHAR(20) NOT NULL, 
	model_job_id UUID, 
	actor_user_id UUID NOT NULL, 
	actor_token_id UUID, 
	actor_kind VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, card_id, revision), 
	UNIQUE (org_id, card_id, id, revision), 
	UNIQUE (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id) REFERENCES response_cards (org_id, id), 
	FOREIGN KEY(org_id, disposition_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, confirmed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id, model_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE evidence (
	task_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	task_resource_id UUID, 
	product_revision_id UUID, 
	task_feature_id UUID, 
	feature_revision_id UUID, 
	task_certificate_id UUID, 
	certificate_revision_id UUID, 
	task_org_profile_id UUID, 
	profile_revision_id UUID, 
	evidence_source_id UUID, 
	field_path VARCHAR(200), 
	quote TEXT NOT NULL, 
	material_kind VARCHAR(40) NOT NULL, 
	quote_check VARCHAR(40) NOT NULL, 
	source_sha256 VARCHAR(64), 
	page INTEGER, 
	confirmed_by UUID, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, task_id) REFERENCES response_cards (org_id, id, task_id), 
	FOREIGN KEY(org_id, confirmed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, task_resource_id) REFERENCES task_resources (org_id, id), 
	FOREIGN KEY(org_id, product_revision_id) REFERENCES product_revisions (org_id, id), 
	FOREIGN KEY(org_id, task_feature_id) REFERENCES task_features (org_id, id), 
	FOREIGN KEY(org_id, feature_revision_id) REFERENCES feature_revisions (org_id, id), 
	FOREIGN KEY(org_id, task_certificate_id) REFERENCES task_certificates (org_id, id), 
	FOREIGN KEY(org_id, certificate_revision_id) REFERENCES certificate_revisions (org_id, id), 
	FOREIGN KEY(org_id, task_org_profile_id) REFERENCES task_org_profiles (org_id, id), 
	FOREIGN KEY(org_id, profile_revision_id) REFERENCES org_profile_revisions (org_id, id), 
	FOREIGN KEY(org_id, evidence_source_id) REFERENCES evidence_sources (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE card_evidence_links (
	card_id UUID NOT NULL, 
	revision_id UUID NOT NULL, 
	evidence_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, revision_id, evidence_id), 
	FOREIGN KEY(org_id, card_id, revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, evidence_id) REFERENCES evidence (org_id, card_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE card_generation_runs (
	target_revisions JSONB NOT NULL, 
	platform_model_id VARCHAR(40), 
	model_revision INTEGER, 
	reasoning VARCHAR(20), 
	model_redaction_enabled BOOLEAN NOT NULL, 
	model_redaction_revision INTEGER NOT NULL, 
	redaction_rule_version VARCHAR(40) NOT NULL, 
	prompt_version VARCHAR(40) NOT NULL, 
	schema_version VARCHAR(40) NOT NULL, 
	adapter_version VARCHAR(40) NOT NULL, 
	encrypted_input TEXT, 
	result JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	generation_job_id UUID NOT NULL,
	generation_run_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	actor_user_id UUID NOT NULL, 
	actor_token_id UUID, 
	actor_kind VARCHAR(20) NOT NULL, 
	input_manifest JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, generation_job_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, generation_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE draft_runs (
	completion VARCHAR(20) NOT NULL, 
	summary JSONB NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	generation_job_id UUID NOT NULL,
	generation_run_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	actor_user_id UUID NOT NULL, 
	actor_token_id UUID, 
	actor_kind VARCHAR(20) NOT NULL, 
	input_manifest JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, generation_job_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, generation_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	UNIQUE (org_id, task_id, extraction_job_id, input_hash), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE response_items (
	draft_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
    category VARCHAR(20) NOT NULL,
    starred BOOLEAN NOT NULL,
	card_id UUID, 
	card_revision_id UUID, 
	kind VARCHAR(20) NOT NULL, 
	"table" VARCHAR(20), 
	source JSONB NOT NULL, 
	location_label TEXT NOT NULL, 
	response_kind VARCHAR(20), 
	response_text TEXT, 
	deviation VARCHAR(20), 
	deviation_note TEXT, 
	disposition_by UUID, 
	disposition_at TIMESTAMP WITH TIME ZONE, 
	gap_reasons JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, draft_id, requirement_id), 
	FOREIGN KEY(org_id, draft_id) REFERENCES draft_runs (org_id, id), 
	FOREIGN KEY(org_id, requirement_id) REFERENCES requirements (org_id, id), 
	FOREIGN KEY(org_id, card_id) REFERENCES response_cards (org_id, id), 
	FOREIGN KEY(org_id, card_revision_id) REFERENCES response_card_revisions (org_id, id), 
	FOREIGN KEY(org_id, disposition_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute(
        "ALTER TABLE response_cards ADD CONSTRAINT response_card_current_revision FOREIGN KEY (org_id,id,current_revision_id,revision) REFERENCES response_card_revisions(org_id,card_id,id,revision) DEFERRABLE INITIALLY DEFERRED"
    )
    for table in TABLES:
        op.execute(f'CREATE INDEX ix_{table}_org_id ON "{table}" (org_id)')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"CREATE POLICY tenant_scope ON {table} USING (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.current_org',true),'')::uuid)"
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
    op.execute("GRANT UPDATE (current_revision_id, revision) ON response_cards TO bid_app")
    op.execute("GRANT UPDATE (confirmed_by, confirmed_at, quote_check) ON evidence TO bid_app")
    _constraints()
    _functions()
    op.execute("""
    CREATE FUNCTION response_tenant_insert_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
      IF NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid THEN
        RAISE EXCEPTION 'Tenant actor context mismatch' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION response_tenant_insert_gate()"
        )


def downgrade():
    raise RuntimeError(
        "Data-preserving rollback required; retain response review and draft history"
    )


def _constraints():
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT response_job_task_binding UNIQUE (org_id,id,task_id)"
    )
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT response_job_document_binding UNIQUE (org_id,id,task_id,document_id)"
    )
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT response_requirement_binding UNIQUE (org_id,id,task_id,job_id)"
    )
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT requirement_job_document_binding FOREIGN KEY (org_id,job_id,task_id,document_id) REFERENCES jobs(org_id,id,task_id,document_id)"
    )
    op.execute(
        "ALTER TABLE response_cards ADD CONSTRAINT response_card_requirement_binding FOREIGN KEY (org_id,requirement_id,task_id,extraction_job_id) REFERENCES requirements(org_id,id,task_id,job_id)"
    )
    op.execute(
        "ALTER TABLE response_cards ADD CONSTRAINT response_card_job_binding FOREIGN KEY (org_id,extraction_job_id,task_id) REFERENCES jobs(org_id,id,task_id)"
    )
    for table in ("draft_runs", "card_generation_runs"):
        for column in ("extraction_job_id", "generation_job_id"):
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_binding FOREIGN KEY (org_id,{column},task_id) REFERENCES jobs(org_id,id,task_id)"
            )
    op.execute(
        "ALTER TABLE response_items ADD CONSTRAINT response_item_card_revision_binding FOREIGN KEY (org_id,card_id,card_revision_id) REFERENCES response_card_revisions(org_id,card_id,id)"
    )
    constraints = {
        "response_cards": ["revision > 0"],
        "response_card_revisions": [
            "revision > 0",
            "state IN ('draft','pending_review','confirmed','rejected','needs_material')",
            "review_domain IS NULL OR review_domain IN ('commercial','technical')",
            "disposition IS NULL OR disposition IN ('respond','comply_only')",
            "(disposition IS NULL AND disposition_by IS NULL AND disposition_at IS NULL) OR (disposition IS NOT NULL AND disposition_by IS NOT NULL AND disposition_at IS NOT NULL)",
            "response_kind IS NULL OR response_kind IN ('evidence','commitment')",
            "suggested_disposition IS NULL OR suggested_disposition IN ('respond','comply_only')",
            "deviation IS NULL OR deviation IN ('none','positive','negative')",
            "review_hint IS NULL OR review_hint = 'needs_material'",
            "response_text IS NULL OR (length(response_text) <= 20000 AND response_text ~ '[^[:space:]]')",
            "deviation_note IS NULL OR (length(deviation_note) <= 10000 AND deviation_note ~ '[^[:space:]]')",
            "reason IS NULL OR (length(reason) <= 10000 AND reason ~ '[^[:space:]]')",
            "jsonb_typeof(reviewed_warning_codes) = 'array'",
            "origin IN ('human','agent','model') AND actor_kind IN ('session','token','agent','worker')",
            "origin = CASE actor_kind WHEN 'session' THEN 'human' WHEN 'worker' THEN 'model' ELSE 'agent' END",
            "origin <> 'model' OR (actor_kind = 'worker' AND model_job_id IS NOT NULL)",
            "(state = 'confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND disposition = 'respond' AND response_kind IS NOT NULL AND response_text IS NOT NULL AND deviation IS NOT NULL AND deviation_note IS NOT NULL AND review_domain IS NOT NULL) OR (state <> 'confirmed' AND confirmed_by IS NULL AND confirmed_at IS NULL)",
        ],
        "evidence": [
            "kind IN ('product','feature','certificate','org_profile','certificate_pdf_page')",
            "length(quote) <= 20000 AND quote ~ '[^[:space:]]'",
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)",
            "(kind = 'product') = (task_resource_id IS NOT NULL AND product_revision_id IS NOT NULL)",
            "(task_resource_id IS NULL) = (product_revision_id IS NULL)",
            "(kind = 'feature') = (task_feature_id IS NOT NULL AND feature_revision_id IS NOT NULL)",
            "(task_feature_id IS NULL) = (feature_revision_id IS NULL)",
            "(kind IN ('certificate','certificate_pdf_page')) = (task_certificate_id IS NOT NULL AND certificate_revision_id IS NOT NULL)",
            "(task_certificate_id IS NULL) = (certificate_revision_id IS NULL)",
            "(kind = 'org_profile') = (task_org_profile_id IS NOT NULL AND profile_revision_id IS NOT NULL)",
            "(task_org_profile_id IS NULL) = (profile_revision_id IS NULL)",
            "(kind = 'certificate_pdf_page' AND evidence_source_id IS NOT NULL AND field_path IS NULL AND page IS NOT NULL AND page > 0 AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$' AND material_kind = 'user_supplied_pdf_page' AND quote_check IN ('unreviewed_page','human_page_review')) OR (kind <> 'certificate_pdf_page' AND evidence_source_id IS NULL AND field_path IS NOT NULL AND field_path ~ '[^[:space:]]' AND page IS NULL AND source_sha256 IS NULL AND material_kind = 'declaration' AND quote_check = 'exact_field_match')",
            "quote_check <> 'human_page_review' OR confirmed_by IS NOT NULL",
            "confirmed_by IS NULL OR quote_check <> 'unreviewed_page'",
        ],
        "draft_runs": [
            "input_hash ~ '^[0-9a-f]{64}$'",
            "completion IN ('complete','partial')",
            "jsonb_typeof(input_manifest) = 'object' AND jsonb_typeof(summary) = 'object'",
            "actor_kind IN ('session','token','agent','worker')",
        ],
        "card_generation_runs": [
            "input_hash ~ '^[0-9a-f]{64}$'",
            "model_redaction_revision > 0",
            "jsonb_typeof(input_manifest) = 'object' AND jsonb_typeof(target_revisions) = 'object' AND jsonb_typeof(result) = 'object'",
            "actor_kind IN ('session','token','agent','worker')",
        ],
        "response_items": [
            "category IN ('technical','qualification','substantive','scoring')",
            "kind IN ('row','comply_only','gap')",
            "jsonb_typeof(source) = 'object' AND location_label ~ '[^[:space:]]' AND jsonb_typeof(gap_reasons) = 'array'",
            "(card_id IS NULL) = (card_revision_id IS NULL)",
            "(kind = 'row' AND card_id IS NOT NULL AND \"table\" IS NOT NULL AND \"table\" IN ('substantive','commercial','technical') AND response_kind IS NOT NULL AND response_kind IN ('evidence','commitment') AND response_text IS NOT NULL AND deviation IS NOT NULL AND deviation IN ('none','positive','negative') AND deviation_note IS NOT NULL AND disposition_by IS NULL AND disposition_at IS NULL AND jsonb_array_length(gap_reasons) = 0) OR (kind = 'comply_only' AND card_id IS NOT NULL AND \"table\" IS NULL AND response_kind IS NULL AND response_text IS NULL AND deviation IS NULL AND deviation_note IS NULL AND disposition_by IS NOT NULL AND disposition_at IS NOT NULL AND jsonb_array_length(gap_reasons) = 0) OR (kind = 'gap' AND \"table\" IS NULL AND response_kind IS NULL AND response_text IS NULL AND deviation IS NULL AND deviation_note IS NULL AND disposition_by IS NULL AND disposition_at IS NULL AND jsonb_array_length(gap_reasons) > 0)",
        ],
    }
    for table, checks in constraints.items():
        for number, check in enumerate(checks):
            op.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT {table}_gate_{number} CHECK ({check})'
            )
    # The selected revision and task are part of each typed foreign key, not free UUID claims.
    for selection, revision_column, evidence_column in (
        ("task_resources", "product_revision_id", "task_resource_id"),
        ("task_features", "feature_revision_id", "task_feature_id"),
        ("task_certificates", "certificate_revision_id", "task_certificate_id"),
        ("task_org_profiles", "profile_revision_id", "task_org_profile_id"),
    ):
        op.execute(
            f"ALTER TABLE {selection} ADD CONSTRAINT {selection}_evidence_binding UNIQUE (org_id,id,task_id,{revision_column})"
        )
        op.execute(
            f"ALTER TABLE evidence ADD CONSTRAINT evidence_{selection}_binding FOREIGN KEY (org_id,{evidence_column},task_id,{revision_column}) REFERENCES {selection}(org_id,id,task_id,{revision_column})"
        )


def _functions():
    op.execute("""
    CREATE FUNCTION response_require_human(p_org uuid, p_domain text) RETURNS uuid
    LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE actor uuid; actual_role text;
    BEGIN
        actor := NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
        IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
           OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL OR actor IS NULL
           OR p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid THEN
            RAISE EXCEPTION 'Human decision context required' USING ERRCODE='42501';
        END IF;
        SELECT m.role INTO actual_role FROM public.memberships m
            JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
            WHERE m.org_id=p_org AND m.user_id=actor AND m.active AND u.active AND o.active;
        -- Parenthesized: inside IF, PL/pgSQL would end the condition at CASE's first THEN.
        IF actual_role IS NULL OR actual_role IS DISTINCT FROM
            (CASE p_domain WHEN 'commercial' THEN 'bidder' WHEN 'technical' THEN 'technical'
                          WHEN 'admin' THEN 'admin' ELSE NULL END) THEN
            RAISE EXCEPTION 'Human decision role required' USING ERRCODE='42501';
        END IF;
        RETURN actor;
    END $$;
    CREATE FUNCTION response_check_actor(p_org uuid,p_user uuid,p_token uuid,p_kind text)
    RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
        IF p_kind IS DISTINCT FROM current_setting('app.actor_kind',true)
            OR p_user IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
            OR p_token IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
            OR p_org IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
            OR NOT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
                JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=p_user
                AND m.active AND u.active AND o.active) THEN
            RAISE EXCEPTION 'Actor context mismatch' USING ERRCODE='42501';
        END IF;
        IF (p_kind='session' AND p_token IS NOT NULL) OR (p_kind='token' AND p_token IS NULL)
          OR (p_token IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.api_tokens t
             WHERE t.org_id=p_org AND t.id=p_token AND t.user_id=p_user
             AND NOT t.revoked AND t.expires_at > CURRENT_TIMESTAMP)) THEN
            RAISE EXCEPTION 'Actor token mismatch' USING ERRCODE='42501';
        END IF;
    END $$;
    CREATE FUNCTION response_redaction_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE actor uuid;
    BEGIN
        IF TG_OP='INSERT' THEN
            IF NOT NEW.model_redaction_enabled OR NEW.model_redaction_revision<>1 OR NEW.model_redaction_by IS NOT NULL THEN
                RAISE EXCEPTION 'Initial redaction must be enabled' USING ERRCODE='23514';
            END IF;
        ELSIF ROW(NEW.model_redaction_enabled,NEW.model_redaction_revision,NEW.model_redaction_by)
          IS DISTINCT FROM ROW(OLD.model_redaction_enabled,OLD.model_redaction_revision,OLD.model_redaction_by) THEN
            actor := public.response_require_human(NEW.org_id,'admin');
            IF NEW.model_redaction_revision<>OLD.model_redaction_revision+1 OR NEW.model_redaction_by IS DISTINCT FROM actor THEN
                RAISE EXCEPTION 'Invalid redaction revision' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER task_redaction_gate BEFORE INSERT OR UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION response_redaction_gate();
    CREATE FUNCTION response_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN RAISE EXCEPTION 'Response history is append only' USING ERRCODE='42501'; END $$;
    """)
    for table in (
        "response_card_revisions",
        "card_evidence_links",
        "card_generation_runs",
        "draft_runs",
        "response_items",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION response_immutable_gate()"
        )
    op.execute("""
    CREATE FUNCTION response_card_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Cards cannot be deleted' USING ERRCODE='42501';
        ELSIF TG_OP='INSERT' THEN
            IF NEW.revision <> 1 OR NOT EXISTS (SELECT 1 FROM public.requirements r
              JOIN public.jobs j ON j.org_id=r.org_id AND j.id=r.job_id
              WHERE r.org_id=NEW.org_id AND r.id=NEW.requirement_id AND r.task_id=NEW.task_id
              AND r.job_id=NEW.extraction_job_id AND j.task_id=NEW.task_id
              AND j.document_id=r.document_id AND j.kind='extract' AND j.status='succeeded') THEN
                RAISE EXCEPTION 'Invalid response requirement binding' USING ERRCODE='23514';
            END IF;
        ELSE
            IF (to_jsonb(NEW)-'revision'-'current_revision_id') IS DISTINCT FROM
               (to_jsonb(OLD)-'revision'-'current_revision_id') OR (NEW.revision<>OLD.revision+1 AND NOT (NEW.revision=1 AND OLD.revision=1 AND EXISTS (SELECT 1 FROM public.response_cards c WHERE c.org_id=OLD.org_id AND c.id=OLD.id AND c.xmin::text=pg_current_xact_id()::text AND NOT EXISTS (SELECT 1 FROM public.response_card_revisions r WHERE r.org_id=OLD.org_id AND r.card_id=OLD.id AND r.revision>1)))) THEN
                RAISE EXCEPTION 'Invalid response pointer advance' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER response_card_gate BEFORE INSERT OR UPDATE OR DELETE ON response_cards
    FOR EACH ROW EXECUTE FUNCTION response_card_gate();
    CREATE FUNCTION response_revision_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE previous public.response_card_revisions%ROWTYPE; card public.response_cards%ROWTYPE;
            category text; actor uuid; decision boolean; content_changed boolean;
    BEGIN
        PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
        SELECT * INTO STRICT card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id FOR UPDATE;
        SELECT r.category INTO category FROM public.requirements r WHERE r.org_id=card.org_id AND r.id=card.requirement_id;
        SELECT * INTO previous FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
        IF NEW.actor_kind='worker' AND (NEW.state<>'draft'
          OR previous.state IN ('pending_review','confirmed') OR previous.disposition='comply_only') THEN
            RAISE EXCEPTION 'Worker may only write editable drafts' USING ERRCODE='42501';
        END IF;
        IF NEW.origin='model' AND NOT EXISTS (SELECT 1 FROM public.jobs g JOIN public.jobs e
          ON e.org_id=g.org_id AND e.id=card.extraction_job_id
          WHERE g.org_id=NEW.org_id AND g.id=NEW.model_job_id AND g.task_id=card.task_id
            AND g.document_id=e.document_id AND g.kind='card_generate' AND g.status='running') THEN
            RAISE EXCEPTION 'Model origin requires its bound generation job' USING ERRCODE='23514';
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_by,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_by,previous.disposition_at);
        IF NEW.revision <> COALESCE(previous.revision,0)+1 THEN
            RAISE EXCEPTION 'Response revision conflict' USING ERRCODE='23514';
        END IF;
        IF category='technical' AND NEW.review_domain IS DISTINCT FROM 'technical'
           OR category='qualification' AND NEW.review_domain IS DISTINCT FROM 'commercial' THEN
            RAISE EXCEPTION 'Fixed review domain cannot change' USING ERRCODE='23514';
        END IF;
        IF previous.id IS NULL THEN
            IF NEW.state<>'draft' THEN RAISE EXCEPTION 'Initial state must be draft' USING ERRCODE='23514'; END IF;
            IF category NOT IN ('technical','qualification') AND NEW.review_domain IS NOT NULL THEN
                RAISE EXCEPTION 'Unclassified initial requirement' USING ERRCODE='23514';
            END IF;
        ELSE
            content_changed := ROW(NEW.response_kind,NEW.response_text,NEW.deviation,NEW.deviation_note,NEW.suggested_disposition,NEW.review_hint)
                IS DISTINCT FROM ROW(previous.response_kind,previous.response_text,previous.deviation,previous.deviation_note,previous.suggested_disposition,previous.review_hint);
            IF NOT ((previous.state='draft' AND NEW.state IN ('draft','pending_review'))
              OR (previous.state='pending_review' AND NEW.state IN ('draft','confirmed','rejected','needs_material'))
              OR (previous.state IN ('rejected','needs_material') AND (NEW.state='draft' OR (NEW.state=previous.state AND decision AND NOT content_changed)))
              OR (previous.state='confirmed' AND NEW.state='draft')) THEN
                RAISE EXCEPTION 'Illegal response state transition' USING ERRCODE='23514';
            END IF;
            IF previous.state IN ('pending_review','confirmed') AND content_changed THEN
                RAISE EXCEPTION 'Protected response content cannot change' USING ERRCODE='23514';
            END IF;
            IF previous.disposition='comply_only' AND
                (content_changed OR NEW.state IS DISTINCT FROM previous.state) THEN
                RAISE EXCEPTION 'Comply-only response content is protected' USING ERRCODE='23514';
            END IF;
            IF NEW.review_domain IS DISTINCT FROM previous.review_domain THEN
                PERFORM public.response_require_human(NEW.org_id,'admin');
                IF previous.review_domain IS NOT NULL OR previous.state<>'draft' OR NEW.state<>'draft' OR NEW.reason IS NULL OR content_changed THEN
                    RAISE EXCEPTION 'Invalid classification' USING ERRCODE='23514';
                END IF;
            END IF;
            IF previous.state='confirmed' OR NEW.state IN ('confirmed','rejected','needs_material') THEN
                PERFORM public.response_require_human(NEW.org_id,NEW.review_domain);
            END IF;
            IF ((previous.state='pending_review' AND NEW.state IN ('draft','rejected','needs_material'))
                OR previous.state='confirmed') AND NEW.reason IS NULL THEN
                RAISE EXCEPTION 'Review reason required' USING ERRCODE='23514';
            END IF;
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_by,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_by,previous.disposition_at);
        IF decision THEN
            IF previous.state='confirmed' OR (previous.state='pending_review' AND NOT
              (NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond'))
              OR (previous.id IS NOT NULL AND previous.state<>NEW.state AND NOT
              (previous.state='pending_review' AND NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond')) THEN
                RAISE EXCEPTION 'Disposition must be a separate review decision' USING ERRCODE='23514';
            END IF;
            actor := public.response_require_human(NEW.org_id,NEW.review_domain);
            IF NEW.disposition IS NULL OR NEW.disposition_by IS DISTINCT FROM actor OR NEW.disposition_at IS NULL
                OR (NEW.state<>'confirmed' AND NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Invalid disposition decision' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.state='confirmed' THEN
            actor := public.response_require_human(NEW.org_id,NEW.review_domain);
            IF btrim(NEW.deviation_note)='满足' THEN
                RAISE EXCEPTION 'Concrete deviation explanation required' USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM public.requirements q WHERE q.org_id=card.org_id AND q.id=card.requirement_id
              AND (q.quote ~ '提供.*(证书|检测报告|截图|说明书|证明|复印件)'
               OR q.quote ~* '(certificate|report|screenshot|proof).*(provid|attach)|(provid|attach).*(certificate|report|screenshot|proof)'))
               AND (NOT NEW.reviewed_warning_codes ? 'proof_material_required' OR NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Proof material warning requires human review' USING ERRCODE='23514';
            END IF;
            IF NEW.confirmed_by IS DISTINCT FROM actor OR previous.disposition='comply_only' THEN
                RAISE EXCEPTION 'Invalid response confirmation' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER response_revision_gate BEFORE INSERT ON response_card_revisions
    FOR EACH ROW EXECUTE FUNCTION response_revision_gate();
    """)
    _evidence_functions()
    _completion_functions()


def _evidence_functions():
    op.execute("""
    CREATE FUNCTION response_evidence_active(p_org uuid,p_evidence uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT CASE e.kind
       WHEN 'product' THEN EXISTS(SELECT 1 FROM public.task_resources s WHERE s.org_id=e.org_id AND s.id=e.task_resource_id AND s.active)
       WHEN 'feature' THEN EXISTS(SELECT 1 FROM public.task_features s WHERE s.org_id=e.org_id AND s.id=e.task_feature_id AND s.active)
       WHEN 'certificate' THEN EXISTS(SELECT 1 FROM public.task_certificates s WHERE s.org_id=e.org_id AND s.id=e.task_certificate_id AND s.active)
       WHEN 'certificate_pdf_page' THEN EXISTS(SELECT 1 FROM public.task_certificates s WHERE s.org_id=e.org_id AND s.id=e.task_certificate_id AND s.active)
       WHEN 'org_profile' THEN EXISTS(SELECT 1 FROM public.task_org_profiles s WHERE s.org_id=e.org_id AND s.id=e.task_org_profile_id AND s.active)
       ELSE false END FROM public.evidence e WHERE e.org_id=p_org AND e.id=p_evidence
    $$;
    CREATE FUNCTION response_evidence_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE domain text; card_state text; actor uuid; material jsonb; field_value text; source public.evidence_sources%ROWTYPE;
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
        SELECT r.review_domain,r.state INTO domain,card_state FROM public.response_cards c JOIN public.response_card_revisions r
          ON r.org_id=c.org_id AND r.id=c.current_revision_id WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
        actor := public.response_require_human(NEW.org_id,domain);
        IF card_state IS DISTINCT FROM 'pending_review' OR NEW.confirmed_by IS DISTINCT FROM actor OR NEW.confirmed_at IS NULL
           OR public.response_evidence_active(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'Invalid evidence confirmation' USING ERRCODE='23514';
        END IF;
      END IF;
      CASE NEW.kind
        WHEN 'product' THEN SELECT data INTO material FROM public.product_revisions WHERE org_id=NEW.org_id AND id=NEW.product_revision_id;
        WHEN 'feature' THEN SELECT data INTO material FROM public.feature_revisions WHERE org_id=NEW.org_id AND id=NEW.feature_revision_id;
        WHEN 'certificate' THEN SELECT data INTO material FROM public.certificate_revisions WHERE org_id=NEW.org_id AND id=NEW.certificate_revision_id;
        WHEN 'org_profile' THEN SELECT data INTO material FROM public.org_profile_revisions WHERE org_id=NEW.org_id AND id=NEW.profile_revision_id;
        WHEN 'certificate_pdf_page' THEN
          SELECT * INTO source FROM public.evidence_sources WHERE org_id=NEW.org_id AND id=NEW.evidence_source_id;
          IF source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
            OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
            OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
            OR source.page IS DISTINCT FROM NEW.page OR source.preview->>'sha256' IS DISTINCT FROM NEW.source_sha256 THEN
            RAISE EXCEPTION 'Evidence page does not match retained source' USING ERRCODE='23514';
          END IF;
        ELSE RAISE EXCEPTION 'Unknown material kind' USING ERRCODE='23514';
      END CASE;
      IF NEW.kind<>'certificate_pdf_page' THEN
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
    CREATE TRIGGER response_evidence_gate BEFORE INSERT OR UPDATE OR DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION response_evidence_gate();
    CREATE FUNCTION response_evidence_confirmation_complete() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    BEGIN
      IF NEW.confirmed_by IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.response_cards c JOIN public.response_card_revisions r
          ON r.org_id=c.org_id AND r.id=c.current_revision_id
        JOIN public.card_evidence_links l ON l.org_id=r.org_id AND l.revision_id=r.id
        WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id AND r.state='confirmed'
          AND r.confirmed_by=NEW.confirmed_by AND l.evidence_id=NEW.id) THEN
        RAISE EXCEPTION 'Evidence confirmation requires confirmed response transaction' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER response_evidence_confirmation_complete AFTER UPDATE ON evidence
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION response_evidence_confirmation_complete();
    CREATE FUNCTION response_link_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE revision_xid text;
    BEGIN
      SELECT xmin::text INTO revision_xid FROM public.response_card_revisions
        WHERE org_id=NEW.org_id AND id=NEW.revision_id AND card_id=NEW.card_id;
      IF revision_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical revision links are closed' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER response_link_gate BEFORE INSERT ON card_evidence_links
    FOR EACH ROW EXECUTE FUNCTION response_link_gate();
    CREATE FUNCTION response_citation_valid(p_org uuid,p_requirement uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT length(btrim(r.quote))>0 AND position(r.quote in c.text)>0
        AND c.task_id=r.task_id AND c.document_id=r.document_id AND c.citation_verified
        AND ((r.page IS NOT NULL AND r.page=c.page AND r.location IS NULL)
          OR (r.page IS NULL AND c.page IS NULL AND r.location IS NOT NULL
            AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.blocks) b
              WHERE b-'text'=r.location AND position(r.quote in b->>'text')>0)))
      FROM public.requirements r JOIN public.chunks c ON c.org_id=r.org_id AND c.id=r.chunk_id
      WHERE r.org_id=p_org AND r.id=p_requirement
    $$;
    CREATE FUNCTION response_revision_complete() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE card public.response_cards%ROWTYPE; links integer; invalid integer;
            previous public.response_card_revisions%ROWTYPE;
    BEGIN
      SELECT * INTO STRICT card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
      IF card.revision IS DISTINCT FROM (SELECT max(revision) FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id)
        OR NOT EXISTS (SELECT 1 FROM public.response_card_revisions r WHERE r.org_id=card.org_id
          AND r.id=card.current_revision_id AND r.card_id=card.id AND r.revision=card.revision) THEN
        RAISE EXCEPTION 'Response pointer is incomplete' USING ERRCODE='23514';
      END IF;
      SELECT count(*),count(*) FILTER(WHERE e.confirmed_by IS NULL OR NOT public.response_evidence_active(e.org_id,e.id))
        INTO links,invalid FROM public.card_evidence_links l JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE l.org_id=NEW.org_id AND l.revision_id=NEW.id;
      IF NEW.response_kind='commitment' AND links<>0 THEN
        RAISE EXCEPTION 'Commitment must not have evidence' USING ERRCODE='23514';
      END IF;
      IF NEW.state='confirmed' AND ((NEW.response_kind='evidence' AND (links=0 OR invalid<>0))
        OR public.response_citation_valid(NEW.org_id,card.requirement_id) IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Confirmed response is incomplete' USING ERRCODE='23514';
      END IF;
      SELECT * INTO previous FROM public.response_card_revisions WHERE org_id=NEW.org_id AND card_id=NEW.card_id AND revision=NEW.revision-1;
      IF previous.state IN ('pending_review','confirmed') OR previous.disposition='comply_only' THEN
        IF EXISTS ((SELECT evidence_id FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=NEW.id
                    EXCEPT SELECT evidence_id FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=previous.id)
                   UNION ALL
                   (SELECT evidence_id FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=previous.id
                    EXCEPT SELECT evidence_id FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=NEW.id)) THEN
          RAISE EXCEPTION 'Protected evidence links cannot change' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER response_revision_complete AFTER INSERT ON response_card_revisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION response_revision_complete();
    """)


def _completion_functions():
    op.execute("""
    CREATE FUNCTION response_run_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE expected_kind text;
    BEGIN
      PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
      expected_kind := CASE TG_TABLE_NAME WHEN 'draft_runs' THEN 'draft' ELSE 'card_generate' END;
      IF NOT EXISTS (SELECT 1 FROM public.jobs e JOIN public.jobs g ON g.org_id=e.org_id
        WHERE e.org_id=NEW.org_id AND e.id=NEW.extraction_job_id AND e.task_id=NEW.task_id
        AND e.kind='extract' AND e.status='succeeded' AND g.id=NEW.generation_job_id
        AND g.task_id=e.task_id AND g.document_id=e.document_id AND g.kind=expected_kind
        AND g.status='running' AND NEW.generation_run_id IS NOT NULL AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Invalid response generation job' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER response_draft_run_gate BEFORE INSERT ON draft_runs
    FOR EACH ROW EXECUTE FUNCTION response_run_gate();
    CREATE TRIGGER response_model_run_gate BEFORE INSERT ON card_generation_runs
    FOR EACH ROW EXECUTE FUNCTION response_run_gate();
    CREATE FUNCTION response_item_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE run public.draft_runs%ROWTYPE; run_xid text; requirement public.requirements%ROWTYPE;
            card public.response_cards%ROWTYPE; revision public.response_card_revisions%ROWTYPE; target_table text;
            citation_ok boolean; material_ok boolean; expected_reasons jsonb := '[]'::jsonb;
    BEGIN
      SELECT * INTO run FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      SELECT d.xmin::text INTO run_xid FROM public.draft_runs d WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_id;
      IF run_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical draft snapshot is closed' USING ERRCODE='42501';
      END IF;
      SELECT * INTO requirement FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
      IF requirement.id IS NULL OR requirement.task_id IS DISTINCT FROM run.task_id
        OR requirement.job_id IS DISTINCT FROM run.extraction_job_id
        OR NEW.category IS DISTINCT FROM requirement.category
        OR NEW.starred IS DISTINCT FROM requirement.starred
        OR NEW.source->>'quote' IS DISTINCT FROM requirement.quote
        OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',requirement.document_id,
            'chunk_id',requirement.chunk_id,'page',requirement.page,'location',requirement.location,
            'quote',requirement.quote) THEN
        RAISE EXCEPTION 'Response item source binding invalid' USING ERRCODE='23514';
      END IF;
      IF NEW.card_id IS NOT NULL THEN
        SELECT * INTO card FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id;
        SELECT * INTO revision FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id;
        IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id
          OR card.current_revision_id IS DISTINCT FROM revision.id THEN
          RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      citation_ok := public.response_citation_valid(NEW.org_id,NEW.requirement_id);
      material_ok := NOT EXISTS (SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=NEW.org_id
        AND l.revision_id=NEW.card_revision_id AND public.response_evidence_active(l.org_id,l.evidence_id) IS DISTINCT FROM true);
      IF NEW.kind='gap' THEN
        IF citation_ok AND (revision.disposition='comply_only' OR
          (revision.state='confirmed' AND revision.review_domain IS NOT NULL AND material_ok)) THEN
          RAISE EXCEPTION 'Eligible response cannot be hidden as gap' USING ERRCODE='23514';
        END IF;
        IF NEW.card_id IS NULL THEN expected_reasons := expected_reasons || '["missing_card"]'::jsonb;
        ELSE
          IF revision.state<>'confirmed' THEN expected_reasons := expected_reasons ||
            jsonb_build_array(CASE WHEN revision.state IN ('rejected','needs_material') THEN revision.state ELSE 'unconfirmed' END); END IF;
          IF revision.review_domain IS NULL THEN expected_reasons := expected_reasons || '["unclassified"]'::jsonb; END IF;
          IF NOT material_ok THEN expected_reasons := expected_reasons || '["stale_material"]'::jsonb; END IF;
        END IF;
        IF citation_ok IS DISTINCT FROM true THEN expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb; END IF;
        IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN
          RAISE EXCEPTION 'Gap reasons must reflect current inputs' USING ERRCODE='23514';
        END IF;
      END IF;
      IF NEW.kind='row' THEN
        target_table := CASE WHEN requirement.starred OR requirement.category='substantive' THEN 'substantive' ELSE revision.review_domain END;
        IF revision.state IS DISTINCT FROM 'confirmed' OR revision.disposition IS DISTINCT FROM 'respond'
          OR NEW."table" IS DISTINCT FROM target_table
          OR ROW(NEW.response_kind,NEW.response_text,NEW.deviation,NEW.deviation_note)
            IS DISTINCT FROM ROW(revision.response_kind,revision.response_text,revision.deviation,revision.deviation_note) THEN
          RAISE EXCEPTION 'Response row is not confirmed content' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.kind='comply_only' THEN
        IF revision.disposition IS DISTINCT FROM 'comply_only' OR revision.review_domain IS NULL
          OR ROW(NEW.disposition_by,NEW.disposition_at) IS DISTINCT FROM ROW(revision.disposition_by,revision.disposition_at) THEN
          RAISE EXCEPTION 'Comply-only item lacks human disposition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER response_item_gate BEFORE INSERT ON response_items
    FOR EACH ROW EXECUTE FUNCTION response_item_gate();
    CREATE CONSTRAINT TRIGGER response_item_complete AFTER INSERT ON response_items
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION response_item_gate();
    CREATE FUNCTION response_draft_complete() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE total integer; actual integer; gaps integer;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM public.jobs g WHERE g.org_id=NEW.org_id AND g.id=NEW.generation_job_id
        AND g.status IN ('running','succeeded') AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Draft generation attempt is no longer current' USING ERRCODE='23514';
      END IF;
      SELECT count(*) INTO total FROM public.requirements WHERE org_id=NEW.org_id
        AND task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
      SELECT count(*),count(*) FILTER(WHERE kind='gap') INTO actual,gaps FROM public.response_items
        WHERE org_id=NEW.org_id AND draft_id=NEW.id;
      IF total=0 OR actual<>total OR NEW.completion IS DISTINCT FROM (CASE WHEN gaps>0 THEN 'partial' ELSE 'complete' END) THEN
        RAISE EXCEPTION 'Draft must cover every requirement exactly once' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i LEFT JOIN public.response_cards c
        ON c.org_id=i.org_id AND c.id=i.card_id WHERE i.org_id=NEW.org_id AND i.draft_id=NEW.id
        AND ((i.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM i.card_revision_id)
          OR (i.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.org_id=i.org_id AND x.requirement_id=i.requirement_id)))) THEN
        RAISE EXCEPTION 'Draft input changed' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i WHERE i.org_id=NEW.org_id AND i.draft_id=NEW.id
        AND i.kind IN ('row','comply_only') AND public.response_citation_valid(i.org_id,i.requirement_id) IS DISTINCT FROM true)
        OR EXISTS (SELECT 1 FROM public.response_items i JOIN public.card_evidence_links l
        ON l.org_id=i.org_id AND l.revision_id=i.card_revision_id JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE i.org_id=NEW.org_id AND i.draft_id=NEW.id AND i.kind='row'
          AND (e.confirmed_by IS NULL OR public.response_evidence_active(e.org_id,e.id) IS DISTINCT FROM true)) THEN
        RAISE EXCEPTION 'Draft material or citation is not eligible' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;
    CREATE CONSTRAINT TRIGGER response_draft_complete AFTER INSERT ON draft_runs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION response_draft_complete();
    """)
