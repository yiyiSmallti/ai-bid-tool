"""Immutable rules checks with tenant, attempt, citation and human decision gates."""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

TABLES = (
    "check_runs",
    "check_items",
    "check_certificates",
    "check_certificate_items",
    "check_findings",
    "check_finding_citations",
    "check_decisions",
)
POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def upgrade():
    # Composite keys bind task-scoped references, including sources absent from a citation.
    op.execute("""
        ALTER TABLE jobs ADD CONSTRAINT check_job_binding UNIQUE(org_id,id,task_id,document_id);
        ALTER TABLE draft_runs ADD CONSTRAINT check_draft_task UNIQUE(org_id,id,task_id),
          ADD CONSTRAINT check_draft_extraction UNIQUE(org_id,id,task_id,extraction_job_id);
        ALTER TABLE response_items ADD CONSTRAINT check_response_requirement UNIQUE(org_id,id,draft_id,requirement_id),
          ADD CONSTRAINT check_response_revision UNIQUE(org_id,id,draft_id,card_revision_id);
        ALTER TABLE task_certificates ADD CONSTRAINT check_certificate_selection UNIQUE(org_id,id,task_id,certificate_revision_id);
        ALTER TABLE chunks ADD CONSTRAINT check_chunk_binding UNIQUE(org_id,id,task_id,document_id);
        ALTER TABLE evidence ADD CONSTRAINT check_evidence_task UNIQUE(org_id,id,task_id);
        ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_check_scopes CHECK (NOT (scopes ? 'check:decide'));
    """)
    op.execute(TABLE_SQL)
    op.execute(CONSTRAINT_SQL)
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({POLICY}) WITH CHECK ({POLICY})'
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
    # PostgreSQL row locking requires one UPDATE column privilege; immutable triggers
    # still reject every actual finding update.
    op.execute("GRANT UPDATE(id) ON check_findings TO bid_app")
    op.execute(GATE_SQL)
    for table in TABLES:
        op.execute(
            f'CREATE TRIGGER check_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION check_immutable_gate()'
        )
        if table not in {"check_runs", "check_decisions"}:
            op.execute(
                f'CREATE TRIGGER check_publication_gate BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION check_child_gate()'
            )
    op.execute("""
        CREATE TRIGGER check_run_gate BEFORE INSERT ON check_runs FOR EACH ROW EXECUTE FUNCTION check_run_gate();
        CREATE CONSTRAINT TRIGGER check_run_complete AFTER INSERT ON check_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_run_complete();
        CREATE TRIGGER check_decision_gate BEFORE INSERT ON check_decisions FOR EACH ROW EXECUTE FUNCTION check_decision_gate();
    """)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain check reports and decisions")


TABLE_SQL = r"""

CREATE TABLE check_runs (
	task_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	draft_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	draft_input_hash VARCHAR(64) NOT NULL, 
	assessment_date DATE NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	rule_version VARCHAR(100) NOT NULL, 
	prompt_version VARCHAR(100), 
	schema_version VARCHAR(100) NOT NULL, 
	input_manifest JSONB NOT NULL, 
	encrypted_input TEXT NOT NULL, 
	completion VARCHAR(20) NOT NULL, 
	limitations JSONB NOT NULL, 
	summary JSONB NOT NULL, 
	actor_user_id UUID NOT NULL, 
	actor_token_id UUID, 
	actor_kind VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	UNIQUE (org_id, job_id), 
	UNIQUE (org_id, id, task_id, draft_id, extraction_job_id), 
	FOREIGN KEY(org_id, job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, draft_id, task_id, extraction_job_id) REFERENCES draft_runs (org_id, id, task_id, extraction_job_id), 
	FOREIGN KEY(org_id, document_id, task_id) REFERENCES documents (org_id, id, task_id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_runs_org_id ON check_runs (org_id);

CREATE TABLE check_items (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	draft_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	response_item_id UUID NOT NULL, 
	card_revision_id UUID, 
	partition VARCHAR(20) NOT NULL, 
	source JSONB NOT NULL, 
	rules JSONB NOT NULL, 
	semantic_status VARCHAR(20) NOT NULL, 
	semantic_reason_code VARCHAR(100), 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	UNIQUE (org_id, report_id, requirement_id), 
	UNIQUE (org_id, id, task_id, report_id), 
	UNIQUE (org_id, id, task_id, report_id, requirement_id), 
	FOREIGN KEY(org_id, report_id, task_id, draft_id, extraction_job_id) REFERENCES check_runs (org_id, id, task_id, draft_id, extraction_job_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, response_item_id, draft_id, requirement_id) REFERENCES response_items (org_id, id, draft_id, requirement_id), 
	FOREIGN KEY(org_id, card_revision_id) REFERENCES response_card_revisions (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_items_org_id ON check_items (org_id);

CREATE TABLE check_certificates (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	task_certificate_id UUID NOT NULL, 
	certificate_revision_id UUID NOT NULL, 
	assessment_date DATE NOT NULL, 
	date_status VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	UNIQUE (org_id, report_id, task_certificate_id), 
	UNIQUE (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, task_certificate_id, task_id, certificate_revision_id) REFERENCES task_certificates (org_id, id, task_id, certificate_revision_id), 
	FOREIGN KEY(org_id, certificate_revision_id) REFERENCES certificate_revisions (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_certificates_org_id ON check_certificates (org_id);

CREATE TABLE check_certificate_items (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	certificate_id UUID NOT NULL, 
	check_item_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	UNIQUE (org_id, report_id, certificate_id, check_item_id), 
	FOREIGN KEY(org_id, certificate_id, task_id, report_id) REFERENCES check_certificates (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, check_item_id, task_id, report_id) REFERENCES check_items (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_certificate_items_org_id ON check_certificate_items (org_id);

CREATE TABLE check_findings (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	check_item_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	method VARCHAR(20) NOT NULL, 
	code VARCHAR(100) NOT NULL, 
	severity VARCHAR(30) NOT NULL, 
	review_domain VARCHAR(20), 
	reason TEXT NOT NULL, 
	source JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	UNIQUE (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, check_item_id, task_id, report_id, requirement_id) REFERENCES check_items (org_id, id, task_id, report_id, requirement_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_findings_org_id ON check_findings (org_id);

CREATE TABLE check_finding_citations (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	finding_id UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	quote TEXT NOT NULL, 
	document_id UUID, 
	chunk_id UUID, 
	draft_id UUID, 
	response_item_id UUID, 
	card_revision_id UUID, 
	field VARCHAR(20), 
	evidence_id UUID, 
	source JSONB, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	FOREIGN KEY(org_id, finding_id, task_id, report_id) REFERENCES check_findings (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, chunk_id, task_id, document_id) REFERENCES chunks (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, document_id, task_id) REFERENCES documents (org_id, id, task_id), 
	FOREIGN KEY(org_id, draft_id, task_id) REFERENCES draft_runs (org_id, id, task_id), 
	FOREIGN KEY(org_id, response_item_id, draft_id, card_revision_id) REFERENCES response_items (org_id, id, draft_id, card_revision_id), 
	FOREIGN KEY(org_id, evidence_id, task_id) REFERENCES evidence (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_finding_citations_org_id ON check_finding_citations (org_id);

CREATE TABLE check_decisions (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	finding_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	action VARCHAR(20) NOT NULL, 
	reason TEXT NOT NULL, 
	reason_sha256 VARCHAR(64) NOT NULL, 
	expected_input_hash VARCHAR(64) NOT NULL, 
	decided_by UUID NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	actor_kind VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, report_id, task_id) REFERENCES check_runs (org_id, id, task_id), 
	UNIQUE (org_id, finding_id, revision), 
	FOREIGN KEY(org_id, finding_id, task_id, report_id) REFERENCES check_findings (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_check_decisions_org_id ON check_decisions (org_id);
"""

CONSTRAINT_SQL = r"""
ALTER TABLE check_runs
  ADD CONSTRAINT check_run_mode CHECK(mode='rules' AND prompt_version IS NULL),
  ADD CONSTRAINT check_run_hashes CHECK(input_hash ~ '^[0-9a-f]{64}$' AND draft_input_hash ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT check_run_state CHECK(completion IN ('complete','partial') AND actor_kind='worker'),
  ADD CONSTRAINT check_run_data CHECK(jsonb_typeof(input_manifest)='object' AND jsonb_typeof(summary)='object' AND jsonb_typeof(limitations)='array' AND length(encrypted_input)>0);
ALTER TABLE check_items
  ADD CONSTRAINT check_item_partition CHECK(partition IN ('response','comply_only','gap')),
  ADD CONSTRAINT check_item_semantic CHECK(semantic_status='not_requested' AND semantic_reason_code IS NULL),
  ADD CONSTRAINT check_item_rules CHECK(jsonb_typeof(rules)='array');
ALTER TABLE check_certificates ADD CONSTRAINT check_certificate_state CHECK(date_status IN ('valid','expired','not_yet_valid','unknown'));
ALTER TABLE check_findings
  ADD CONSTRAINT check_finding_kind CHECK(method='deterministic' AND code IN ('mandatory_response_missing','negative_deviation','unconfirmed_evidence','certificate_expired','certificate_not_yet_valid','certificate_date_unknown')),
  ADD CONSTRAINT check_finding_severity CHECK(severity IN ('disqualification_risk','deduction_risk','info')),
  ADD CONSTRAINT check_finding_domain CHECK(review_domain IN ('commercial','technical')),
  ADD CONSTRAINT check_finding_reason CHECK(length(reason) BETWEEN 1 AND 20000 AND reason ~ '[^[:space:]]');
ALTER TABLE check_finding_citations
  ADD CONSTRAINT check_citation_quote CHECK(length(quote) BETWEEN 1 AND 20000 AND position('{{secret.' in quote)=0 AND position('[REDACTED_' in quote)=0),
  ADD CONSTRAINT check_citation_one_source CHECK(
    (kind='tender' AND document_id IS NOT NULL AND chunk_id IS NOT NULL AND source IS NOT NULL AND draft_id IS NULL AND response_item_id IS NULL AND card_revision_id IS NULL AND field IS NULL AND evidence_id IS NULL)
    OR (kind='draft' AND document_id IS NULL AND chunk_id IS NULL AND source IS NULL AND draft_id IS NOT NULL AND response_item_id IS NOT NULL AND card_revision_id IS NOT NULL AND field IS NOT NULL AND field IN ('response_text','deviation_note') AND evidence_id IS NULL)
    OR (kind='evidence' AND document_id IS NULL AND chunk_id IS NULL AND source IS NULL AND draft_id IS NULL AND response_item_id IS NULL AND card_revision_id IS NULL AND field IS NULL AND evidence_id IS NOT NULL));
ALTER TABLE check_decisions
  ADD CONSTRAINT check_decision_action CHECK(action IN ('dismiss','reopen') AND revision>=2 AND actor_kind='session'),
  ADD CONSTRAINT check_decision_reason CHECK(length(reason) BETWEEN 1 AND 20000 AND reason ~ '[^[:space:]]' AND reason_sha256 ~ '^[0-9a-f]{64}$' AND expected_input_hash ~ '^[0-9a-f]{64}$');
"""

GATE_SQL = r"""
CREATE FUNCTION check_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  -- The migration owner may rotate ciphertext; runtime grants never allow updates.
  IF TG_TABLE_NAME='check_runs' AND TG_OP='UPDATE'
    AND current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
    AND (to_jsonb(NEW)-'encrypted_input') = (to_jsonb(OLD)-'encrypted_input') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Check history is immutable' USING ERRCODE='42501';
END $$;

CREATE FUNCTION check_live_attempt(p_org uuid,p_report uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.check_runs%ROWTYPE; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO STRICT r FROM public.check_runs WHERE org_id=p_org AND id=p_report;
  SELECT * INTO j FROM public.jobs WHERE org_id=p_org AND id=r.job_id FOR UPDATE;
  IF j.status IS DISTINCT FROM 'running' OR j.run_id IS DISTINCT FROM r.run_id
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
    OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker' THEN
    RAISE EXCEPTION 'Check job attempt is not live' USING ERRCODE='23514';
  END IF;
  PERFORM public.response_check_actor(r.org_id,r.actor_user_id,r.actor_token_id,'worker');
END $$;

CREATE FUNCTION check_run_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE j public.jobs%ROWTYPE; d public.draft_runs%ROWTYPE;
BEGIN
  PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
  IF NEW.actor_kind IS DISTINCT FROM 'worker' OR NOT EXISTS(SELECT 1 FROM public.memberships
    WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND role IN ('admin','bidder','technical') AND active)
    OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens
      WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'check:run')) THEN
    RAISE EXCEPTION 'Check worker permission required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
  SELECT * INTO d FROM public.draft_runs WHERE org_id=NEW.org_id AND id=NEW.draft_id;
  IF j.kind IS DISTINCT FROM 'check' OR j.status IS DISTINCT FROM 'running'
    OR j.run_id IS DISTINCT FROM NEW.run_id OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
    OR j.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
    OR j.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
    OR j.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
    OR NEW.input_manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text
    OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
    OR NEW.input_manifest->>'draft_id' IS DISTINCT FROM NEW.draft_id::text
    OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text
    OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text
    OR NEW.input_manifest->>'assessment_date' IS DISTINCT FROM to_char(NEW.assessment_date,'YYYY-MM-DD')
    OR NEW.input_manifest->>'draft_input_hash' IS DISTINCT FROM NEW.draft_input_hash
    OR NEW.input_manifest->>'mode' IS DISTINCT FROM NEW.mode
    OR NEW.input_manifest->>'rule_version' IS DISTINCT FROM NEW.rule_version
    OR NEW.input_manifest->>'schema_version' IS DISTINCT FROM NEW.schema_version
    OR jsonb_typeof(NEW.input_manifest->'items') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'certificates') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'confidential') IS DISTINCT FROM 'array'
    OR d.input_hash IS DISTINCT FROM NEW.draft_input_hash
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id AND kind='extract' AND status='succeeded') THEN
    RAISE EXCEPTION 'Invalid check publication attempt or input' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION check_child_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.check_runs%ROWTYPE; item public.check_items%ROWTYPE;
  response public.response_items%ROWTYPE; req public.requirements%ROWTYPE;
  finding public.check_findings%ROWTYPE; revision public.response_card_revisions%ROWTYPE;
  proof public.evidence%ROWTYPE; raw_text text; report_xid text;
  cert public.certificate_revisions%ROWTYPE; expected_date_status text;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE org_id=NEW.org_id AND id=NEW.report_id;
  SELECT xmin::text INTO report_xid FROM public.check_runs WHERE org_id=NEW.org_id AND id=NEW.report_id;
  IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Historical check report is closed' USING ERRCODE='42501';
  END IF;
  PERFORM public.check_live_attempt(NEW.org_id,NEW.report_id);
  IF TG_TABLE_NAME='check_items' THEN
    SELECT * INTO response FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
    SELECT * INTO req FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
    IF NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
      OR NEW.partition IS DISTINCT FROM (CASE response.kind WHEN 'row' THEN 'response' ELSE response.kind END)
      OR NEW.source IS DISTINCT FROM response.source
      OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,'page',req.page,'location',req.location,'quote',req.quote)
      OR public.response_citation_valid(NEW.org_id,NEW.requirement_id) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Check item source or partition mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificates' THEN
    SELECT * INTO cert FROM public.certificate_revisions WHERE org_id=NEW.org_id AND id=NEW.certificate_revision_id;
    expected_date_status := CASE
      WHEN (cert.data->>'valid_until')::date < NEW.assessment_date THEN 'expired'
      WHEN (cert.data->>'valid_from')::date > NEW.assessment_date THEN 'not_yet_valid'
      WHEN cert.data->>'valid_from' IS NOT NULL AND cert.data->>'valid_until' IS NOT NULL THEN 'valid'
      ELSE 'unknown' END;
    IF NEW.assessment_date IS DISTINCT FROM r.assessment_date OR NEW.date_status IS DISTINCT FROM expected_date_status
      OR NOT EXISTS(SELECT 1 FROM public.task_certificates WHERE org_id=NEW.org_id AND id=NEW.task_certificate_id AND active) THEN
      RAISE EXCEPTION 'Check certificate dates or selection mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificate_items' THEN
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.check_items i
      ON i.org_id=a.org_id AND i.report_id=a.report_id
      JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
      JOIN public.card_evidence_links l ON l.org_id=s.org_id AND l.revision_id=s.card_revision_id
      JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
      WHERE a.org_id=NEW.org_id AND a.id=NEW.certificate_id AND i.id=NEW.check_item_id
        AND a.report_id=NEW.report_id AND s.kind='row' AND e.confirmed_by IS NOT NULL
        AND e.task_certificate_id=a.task_certificate_id AND e.certificate_revision_id=a.certificate_revision_id
        AND public.response_evidence_active(e.org_id,e.id)) THEN
      RAISE EXCEPTION 'Check certificate lacks confirmed requirement binding' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_findings' THEN
    SELECT * INTO item FROM public.check_items WHERE org_id=NEW.org_id AND id=NEW.check_item_id;
    SELECT * INTO revision FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=item.card_revision_id;
    IF NEW.source IS DISTINCT FROM item.source OR NEW.review_domain IS DISTINCT FROM revision.review_domain
      OR (SELECT count(*) FROM public.check_findings WHERE org_id=NEW.org_id AND check_item_id=NEW.check_item_id)>=20 THEN
      RAISE EXCEPTION 'Check finding source, domain or count mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_finding_citations' THEN
    SELECT * INTO finding FROM public.check_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id;
    SELECT * INTO item FROM public.check_items WHERE org_id=NEW.org_id AND id=finding.check_item_id;
    SELECT * INTO response FROM public.response_items WHERE org_id=NEW.org_id AND id=item.response_item_id;
    IF (SELECT count(*) FROM public.check_finding_citations WHERE org_id=NEW.org_id AND finding_id=NEW.finding_id)>=20 THEN
      RAISE EXCEPTION 'Check citation limit exceeded' USING ERRCODE='23514';
    END IF;
    IF NEW.kind='tender' THEN
      SELECT * INTO req FROM public.requirements WHERE org_id=NEW.org_id AND id=item.requirement_id;
      IF NEW.source IS DISTINCT FROM item.source OR NEW.quote IS DISTINCT FROM req.quote
        OR NEW.document_id IS DISTINCT FROM req.document_id OR NEW.chunk_id IS DISTINCT FROM req.chunk_id
        OR public.response_citation_valid(NEW.org_id,req.id) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Check tender citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := req.quote;
    ELSIF NEW.kind='draft' THEN
      IF NEW.response_item_id IS DISTINCT FROM response.id OR NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
        OR NEW.draft_id IS DISTINCT FROM r.draft_id OR response.kind IS DISTINCT FROM 'row' THEN
        RAISE EXCEPTION 'Check response citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := CASE NEW.field WHEN 'response_text' THEN response.response_text WHEN 'deviation_note' THEN response.deviation_note END;
    ELSIF NEW.kind='evidence' THEN
      SELECT * INTO proof FROM public.evidence WHERE org_id=NEW.org_id AND id=NEW.evidence_id;
      IF proof.confirmed_by IS NULL OR proof.kind='image_region'
        OR public.response_evidence_active(NEW.org_id,proof.id) IS DISTINCT FROM true
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=NEW.org_id AND revision_id=item.card_revision_id AND evidence_id=proof.id)
        OR response.kind IS DISTINCT FROM 'row' THEN
        RAISE EXCEPTION 'Check evidence citation mismatch' USING ERRCODE='23514';
      END IF;
      raw_text := proof.quote;
    END IF;
    IF raw_text IS NULL OR position(NEW.quote in raw_text)=0
      OR position(NEW.quote in substring(raw_text from position(NEW.quote in raw_text)+1))>0 THEN
      RAISE EXCEPTION 'Check citation must be a unique exact span' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION check_current_inputs(p_org uuid,p_report uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
-- Subquery aliases deliberately reuse the record names; they always mean the table.
#variable_conflict use_column
DECLARE r public.check_runs%ROWTYPE; t public.tasks%ROWTYPE; i public.check_items%ROWTYPE;
  s public.response_items%ROWTYPE; q public.requirements%ROWTYPE; c public.response_cards%ROWTYPE;
  v public.response_card_revisions%ROWTYPE; e public.evidence%ROWTYPE;
  cf public.confidential_fields%ROWTYPE; fixed jsonb; dep jsonb; cert jsonb; field jsonb;
  value_id uuid; expected_count integer; actual_count integer;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE org_id=p_org AND id=p_report;
  IF r.id IS NULL THEN RETURN false; END IF;
  SELECT * INTO t FROM public.tasks WHERE org_id=p_org AND id=r.task_id;
  IF (r.input_manifest->>'model_redaction_enabled')::boolean IS DISTINCT FROM t.model_redaction_enabled
    OR (r.input_manifest->>'model_redaction_revision')::integer IS DISTINCT FROM t.model_redaction_revision
    OR r.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE org_id=p_org AND id=r.document_id AND task_id=r.task_id)
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=p_org AND id=r.extraction_job_id
      AND task_id=r.task_id AND document_id=r.document_id AND kind='extract' AND status='succeeded') THEN RETURN false; END IF;
  IF jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.check_items WHERE org_id=p_org AND report_id=p_report)
    OR jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.requirements WHERE org_id=p_org AND task_id=r.task_id AND job_id=r.extraction_job_id) THEN RETURN false; END IF;
  IF (SELECT count(DISTINCT x->>'requirement_id') FROM jsonb_array_elements(r.input_manifest->'items') x)
    <>jsonb_array_length(r.input_manifest->'items') THEN RETURN false; END IF;
  FOR fixed IN SELECT value FROM jsonb_array_elements(r.input_manifest->'items') LOOP
    SELECT * INTO i FROM public.check_items WHERE org_id=p_org AND report_id=p_report AND requirement_id=(fixed->>'requirement_id')::uuid;
    SELECT * INTO s FROM public.response_items WHERE org_id=p_org AND id=i.response_item_id;
    SELECT * INTO q FROM public.requirements WHERE org_id=p_org AND id=i.requirement_id;
    SELECT * INTO c FROM public.response_cards WHERE org_id=p_org AND id=s.card_id;
    SELECT * INTO v FROM public.response_card_revisions WHERE org_id=p_org AND id=s.card_revision_id;
    IF i.id IS NULL OR s.id IS NULL OR q.id IS NULL
      OR s.draft_id IS DISTINCT FROM r.draft_id OR q.job_id IS DISTINCT FROM r.extraction_job_id
      OR q.task_id IS DISTINCT FROM r.task_id OR q.document_id IS DISTINCT FROM r.document_id
      OR (fixed->>'response_item_id')::uuid IS DISTINCT FROM s.id
      OR (fixed->>'card_id')::uuid IS DISTINCT FROM s.card_id
      OR (fixed->>'card_revision_id')::uuid IS DISTINCT FROM s.card_revision_id
      OR (fixed->>'document_id')::uuid IS DISTINCT FROM q.document_id
      OR (fixed->>'chunk_id')::uuid IS DISTINCT FROM q.chunk_id
      OR fixed->>'partition' IS DISTINCT FROM i.partition
      OR fixed->>'review_domain' IS DISTINCT FROM v.review_domain
      OR fixed->'gap_reasons' IS DISTINCT FROM s.gap_reasons
      OR s.category IS DISTINCT FROM q.category OR s.starred IS DISTINCT FROM q.starred
      OR i.source IS DISTINCT FROM s.source
      OR i.source IS DISTINCT FROM jsonb_build_object('document_id',q.document_id,'chunk_id',q.chunk_id,'page',q.page,'location',q.location,'quote',q.quote)
      OR public.response_citation_valid(p_org,q.id) IS DISTINCT FROM true
      OR (s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE org_id=p_org AND requirement_id=q.id))
      OR (i.partition IN ('response','comply_only') AND public.response_quote_current(p_org,s.card_revision_id) IS DISTINCT FROM true)
      OR (i.partition='response' AND (v.state IS DISTINCT FROM 'confirmed' OR v.confirmed_by IS NULL))
      OR (i.partition='comply_only' AND v.disposition IS DISTINCT FROM 'comply_only') THEN RETURN false; END IF;
    IF jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=s.card_revision_id)
      OR (SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(fixed->'evidence') x)<>jsonb_array_length(fixed->'evidence') THEN RETURN false; END IF;
    FOR dep IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
      SELECT * INTO e FROM public.evidence WHERE org_id=p_org AND id=(dep->>'id')::uuid;
      IF e.id IS NULL OR e.task_id IS DISTINCT FROM r.task_id OR e.card_id IS DISTINCT FROM s.card_id
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=s.card_revision_id AND evidence_id=e.id)
        OR (dep->>'confirmed_by')::uuid IS DISTINCT FROM e.confirmed_by
        OR (dep->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(p_org,e.id)
        OR (i.partition='response' AND (e.confirmed_by IS NULL OR public.response_evidence_active(p_org,e.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
    END LOOP;
    IF i.partition<>'comply_only' AND v.model_job_id IS NOT NULL AND public.response_generation_materials_active(p_org,v.model_job_id) IS DISTINCT FROM true
      AND NOT (i.partition='gap' AND fixed->'gap_reasons' ? 'stale_material') THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'certificates');
  IF expected_count<>(SELECT count(*) FROM public.task_certificates WHERE org_id=p_org AND task_id=r.task_id AND active)
    OR expected_count<>(SELECT count(*) FROM public.check_certificates WHERE org_id=p_org AND report_id=p_report)
    OR expected_count<>(SELECT count(DISTINCT x->>'task_certificate_id') FROM jsonb_array_elements(r.input_manifest->'certificates') x) THEN RETURN false; END IF;
  FOR cert IN SELECT value FROM jsonb_array_elements(r.input_manifest->'certificates') LOOP
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.task_certificates b ON b.org_id=a.org_id AND b.id=a.task_certificate_id
      JOIN public.certificate_revisions v ON v.org_id=b.org_id AND v.id=b.certificate_revision_id
      WHERE a.org_id=p_org AND a.report_id=p_report AND a.task_certificate_id=(cert->>'task_certificate_id')::uuid
        AND a.certificate_revision_id=(cert->>'certificate_revision_id')::uuid AND b.task_id=r.task_id AND b.active
        AND a.date_status=cert->>'date_status' AND a.assessment_date=r.assessment_date
        AND v.data->>'valid_from' IS NOT DISTINCT FROM cert->>'valid_from'
        AND v.data->>'valid_until' IS NOT DISTINCT FROM cert->>'valid_until') THEN RETURN false; END IF;
    SELECT count(*) INTO actual_count FROM public.check_certificate_items a JOIN public.check_certificates b
      ON b.org_id=a.org_id AND b.id=a.certificate_id WHERE a.org_id=p_org AND a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid;
    IF jsonb_typeof(cert->'requirement_ids') IS DISTINCT FROM 'array'
      OR actual_count<>(SELECT count(DISTINCT i.id) FROM public.check_items i
        JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
        JOIN public.card_evidence_links l ON l.org_id=s.org_id AND l.revision_id=s.card_revision_id
        JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE i.org_id=p_org AND i.report_id=p_report AND s.kind='row' AND e.confirmed_by IS NOT NULL
          AND e.task_certificate_id=(cert->>'task_certificate_id')::uuid
          AND e.certificate_revision_id=(cert->>'certificate_revision_id')::uuid)
      OR actual_count<>jsonb_array_length(cert->'requirement_ids')
      OR actual_count<>(SELECT count(DISTINCT x) FROM jsonb_array_elements_text(cert->'requirement_ids') x)
      OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(cert->'requirement_ids') req WHERE NOT EXISTS(
        SELECT 1 FROM public.check_certificate_items a JOIN public.check_certificates b ON b.org_id=a.org_id AND b.id=a.certificate_id
        JOIN public.check_items i ON i.org_id=a.org_id AND i.id=a.check_item_id
        WHERE a.org_id=p_org AND a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid AND i.requirement_id=req::uuid)) THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'confidential');
  IF expected_count<>(SELECT count(*) FROM public.confidential_fields WHERE org_id=p_org AND NOT archived)
    OR expected_count<>(SELECT count(DISTINCT x->>'field_id') FROM jsonb_array_elements(r.input_manifest->'confidential') x) THEN RETURN false; END IF;
  FOR field IN SELECT value FROM jsonb_array_elements(r.input_manifest->'confidential') LOOP
    SELECT * INTO cf FROM public.confidential_fields WHERE org_id=p_org AND id=(field->>'field_id')::uuid;
    SELECT id INTO value_id FROM public.confidential_values WHERE org_id=p_org AND field_id=cf.id
      AND task_id IS NOT DISTINCT FROM CASE cf.scope WHEN 'task' THEN r.task_id ELSE NULL END ORDER BY version DESC LIMIT 1;
    IF cf.id IS NULL OR cf.archived OR cf.revision IS DISTINCT FROM (field->>'field_revision')::integer
      OR value_id IS DISTINCT FROM (field->>'value_id')::uuid THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $$;

CREATE FUNCTION check_run_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE total integer; actual integer; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
  IF j.status NOT IN ('running','succeeded') OR j.run_id IS DISTINCT FROM NEW.run_id
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() THEN
    RAISE EXCEPTION 'Check attempt lost before publication' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO total FROM public.requirements WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
  SELECT count(*) INTO actual FROM public.check_items WHERE org_id=NEW.org_id AND report_id=NEW.id;
  IF total NOT BETWEEN 1 AND 2000 OR actual<>total
    OR actual<>(SELECT count(*) FROM public.response_items WHERE org_id=NEW.org_id AND draft_id=NEW.draft_id) THEN
    RAISE EXCEPTION 'Check must cover the entire draft exactly once' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i JOIN public.requirements q ON q.org_id=i.org_id AND q.id=i.requirement_id
    WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND
      (i.source IS DISTINCT FROM jsonb_build_object('document_id',q.document_id,'chunk_id',q.chunk_id,'page',q.page,'location',q.location,'quote',q.quote)
       OR public.response_citation_valid(i.org_id,i.requirement_id) IS DISTINCT FROM true
       OR (i.partition IN ('response','comply_only') AND public.response_quote_current(i.org_id,i.card_revision_id) IS DISTINCT FROM true))) THEN
    RAISE EXCEPTION 'Check source changed during publication' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i JOIN public.response_items s ON s.org_id=i.org_id AND s.id=i.response_item_id
    LEFT JOIN public.response_cards c ON c.org_id=s.org_id AND c.id=s.card_id
    WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND
      ((s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.org_id=s.org_id AND x.requirement_id=s.requirement_id))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
        WHERE l.org_id=s.org_id AND l.revision_id=s.card_revision_id AND (e.confirmed_by IS NULL OR public.response_evidence_active(e.org_id,e.id) IS DISTINCT FROM true)))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.response_card_revisions v WHERE v.org_id=s.org_id AND v.id=s.card_revision_id
        AND public.response_generation_materials_active(v.org_id,v.model_job_id) IS DISTINCT FROM true)))) THEN
    RAISE EXCEPTION 'Check draft dependencies changed' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM public.task_certificates WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND active)
    <> (SELECT count(*) FROM public.check_certificates WHERE org_id=NEW.org_id AND report_id=NEW.id)
    OR EXISTS(SELECT 1 FROM public.check_certificates c JOIN public.task_certificates s ON s.org_id=c.org_id AND s.id=c.task_certificate_id
      WHERE c.org_id=NEW.org_id AND c.report_id=NEW.id AND NOT s.active) THEN
    RAISE EXCEPTION 'Check must include every selected certificate' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.org_id=NEW.org_id AND f.report_id=NEW.id AND
    NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.org_id=f.org_id AND c.finding_id=f.id AND c.kind='tender')) THEN
    RAISE EXCEPTION 'Check finding requires a tender citation' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Check fixed input changed' USING ERRCODE='23514';
  END IF;
  IF (NEW.summary->>'item_count')::integer IS DISTINCT FROM actual
    OR (NEW.summary->>'finding_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_findings WHERE org_id=NEW.org_id AND report_id=NEW.id)
    OR (NEW.summary->>'unassessed_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_items i
      WHERE i.org_id=NEW.org_id AND i.report_id=NEW.id AND EXISTS(SELECT 1 FROM jsonb_array_elements(i.rules) x WHERE x->>'outcome'='unknown'))
    OR NEW.completion IS DISTINCT FROM (CASE WHEN (NEW.summary->>'unassessed_count')::integer>0 THEN 'partial' ELSE 'complete' END) THEN
    RAISE EXCEPTION 'Check summary must match published coverage' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION check_decision_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE f public.check_findings%ROWTYPE; r public.check_runs%ROWTYPE; previous public.check_decisions%ROWTYPE; expected_role text;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE org_id=NEW.org_id AND id=NEW.report_id;
  PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=r.task_id FOR SHARE;
  SELECT * INTO f FROM public.check_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id FOR UPDATE;
  expected_role := CASE f.review_domain WHEN 'commercial' THEN 'bidder' WHEN 'technical' THEN 'technical' END;
  PERFORM public.response_check_actor(NEW.org_id,NEW.decided_by,NULL,'session');
  IF NEW.actor_kind IS DISTINCT FROM 'session' OR expected_role IS NULL
    OR NOT EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.decided_by AND active AND role=expected_role) THEN
    RAISE EXCEPTION 'Check decision requires the responsible human' USING ERRCODE='42501';
  END IF;
  SELECT * INTO previous FROM public.check_decisions WHERE org_id=NEW.org_id AND finding_id=NEW.finding_id ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>coalesce(previous.revision,1)+1
    OR NEW.action IS DISTINCT FROM (CASE WHEN previous.action='dismiss' THEN 'reopen' ELSE 'dismiss' END)
    OR NEW.expected_input_hash IS DISTINCT FROM r.input_hash THEN
    RAISE EXCEPTION 'Check decision revision or input conflict' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.org_id,NEW.report_id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Stale check report cannot receive decisions' USING ERRCODE='23514';
  END IF;
  IF NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'Check decision reason hash mismatch' USING ERRCODE='23514';
  END IF;
  NEW.decided_at := clock_timestamp();
  RETURN NEW;
END $$;
"""
