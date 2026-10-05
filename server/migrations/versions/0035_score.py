"""Immutable advisory score reports with tenant and live-publication fences."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

TABLES = (
    "score_reports",
    "score_report_items",
    "score_report_item_responses",
    "score_item_citations",
)
POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def upgrade():
    op.execute(PARENT_SQL)
    op.execute(TABLE_SQL)
    op.execute(CONSTRAINT_SQL)
    op.execute(GATE_SQL)
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({POLICY}) WITH CHECK ({POLICY})'
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
        op.execute(
            f'CREATE TRIGGER score_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION score_immutable_gate()'
        )
        if table != "score_reports":
            op.execute(
                f'CREATE TRIGGER score_child_gate BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION score_child_gate()'
            )
    op.execute(
        "CREATE TRIGGER score_report_gate BEFORE INSERT ON score_reports FOR EACH ROW EXECUTE FUNCTION score_report_gate()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER score_publication_complete AFTER INSERT ON score_reports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION score_publication_complete()"
    )


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain advisory score reports")


PARENT_SQL = r"""
ALTER TABLE score_rubric_sets ADD CONSTRAINT score_rubric_input_binding UNIQUE(org_id,id,task_id,extraction_job_id,document_id);
ALTER TABLE score_rubric_items ADD CONSTRAINT score_rubric_item_binding UNIQUE(org_id,id,task_id,rubric_id,section_id,requirement_id);
ALTER TABLE response_items ADD CONSTRAINT score_response_revision_binding UNIQUE(org_id,id,draft_id,requirement_id,card_revision_id);
"""

TABLE_SQL = r"""

CREATE TABLE score_reports (
	task_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	draft_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	actor_user_id UUID NOT NULL, 
	actor_token_id UUID, 
	rubric_version INTEGER NOT NULL, 
	rubric_revision INTEGER NOT NULL, 
	rubric_input_hash TEXT NOT NULL, 
	input_hash TEXT NOT NULL, 
	draft_input_hash TEXT NOT NULL, 
	prompt_version TEXT NOT NULL, 
	schema_version TEXT NOT NULL, 
	scoring_rule_version TEXT NOT NULL, 
	encrypted_input TEXT NOT NULL, 
	completion TEXT NOT NULL, 
	actor_kind TEXT NOT NULL, 
	assessment_date DATE NOT NULL, 
	input_manifest JSONB NOT NULL, 
	summary JSONB NOT NULL, 
	limitations JSONB NOT NULL, 
	sections JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	UNIQUE (org_id, job_id), 
	UNIQUE (org_id, id, task_id, draft_id, extraction_job_id, rubric_id), 
	FOREIGN KEY(org_id, job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, draft_id, task_id, extraction_job_id) REFERENCES draft_runs (org_id, id, task_id, extraction_job_id), 
	FOREIGN KEY(org_id, document_id, task_id) REFERENCES documents (org_id, id, task_id), 
	FOREIGN KEY(org_id, rubric_id, task_id, extraction_job_id, document_id) REFERENCES score_rubric_sets (org_id, id, task_id, extraction_job_id, document_id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_reports_org_id ON score_reports (org_id);

CREATE TABLE score_report_items (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	draft_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	rubric_item_id UUID NOT NULL, 
	section_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	anchor_response_item_id UUID NOT NULL, 
	anchor_partition TEXT NOT NULL, 
	outcome TEXT NOT NULL, 
	reason_code TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	score_range JSONB, 
	estimated_score NUMERIC(18, 8), 
	deduction_reasons JSONB NOT NULL, 
	strengthening_actions JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	UNIQUE (org_id, report_id, rubric_item_id), 
	UNIQUE (org_id, id, task_id, report_id, draft_id), 
	UNIQUE (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, report_id, task_id, draft_id, extraction_job_id, rubric_id) REFERENCES score_reports (org_id, id, task_id, draft_id, extraction_job_id, rubric_id), 
	FOREIGN KEY(org_id, rubric_item_id, task_id, rubric_id, section_id, requirement_id) REFERENCES score_rubric_items (org_id, id, task_id, rubric_id, section_id, requirement_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, anchor_response_item_id, draft_id, requirement_id) REFERENCES response_items (org_id, id, draft_id, requirement_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_report_items_org_id ON score_report_items (org_id);

CREATE TABLE score_report_item_responses (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	score_item_id UUID NOT NULL, 
	draft_id UUID NOT NULL, 
	response_item_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	card_revision_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	UNIQUE (org_id, score_item_id, response_item_id), 
	UNIQUE (org_id, task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id), 
	FOREIGN KEY(org_id, score_item_id, task_id, report_id, draft_id) REFERENCES score_report_items (org_id, id, task_id, report_id, draft_id), 
	FOREIGN KEY(org_id, response_item_id, draft_id, requirement_id, card_revision_id) REFERENCES response_items (org_id, id, draft_id, requirement_id, card_revision_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_report_item_responses_org_id ON score_report_item_responses (org_id);

CREATE TABLE score_item_citations (
	task_id UUID NOT NULL, 
	report_id UUID NOT NULL, 
	score_item_id UUID NOT NULL, 
	kind TEXT NOT NULL, 
	quote TEXT NOT NULL, 
	document_id UUID, 
	chunk_id UUID, 
	draft_id UUID, 
	response_item_id UUID, 
	card_revision_id UUID, 
	field TEXT, 
	source JSONB, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, score_item_id, task_id, report_id) REFERENCES score_report_items (org_id, id, task_id, report_id), 
	FOREIGN KEY(org_id, document_id, task_id) REFERENCES documents (org_id, id, task_id), 
	FOREIGN KEY(org_id, chunk_id, task_id, document_id) REFERENCES chunks (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id) REFERENCES score_report_item_responses (org_id, task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_item_citations_org_id ON score_item_citations (org_id);
"""

CONSTRAINT_SQL = r"""
ALTER TABLE score_reports ADD CONSTRAINT score_report_shape CHECK (
 actor_kind='worker' AND rubric_version>0 AND rubric_revision>0
 AND input_hash ~ '^[0-9a-f]{64}$' AND draft_input_hash ~ '^[0-9a-f]{64}$'
 AND rubric_input_hash ~ '^[0-9a-f]{64}$' AND completion IN ('complete','partial')
 AND prompt_version ~ '[^[:space:]]' AND schema_version ~ '[^[:space:]]' AND scoring_rule_version ~ '[^[:space:]]'
 AND jsonb_typeof(input_manifest)='object' AND jsonb_typeof(summary)='object'
 AND jsonb_typeof(sections)='array' AND jsonb_typeof(limitations)='array');
ALTER TABLE score_report_items ADD CONSTRAINT score_item_shape CHECK (
 anchor_partition IN ('response','comply_only','gap') AND outcome IN ('assessed','unassessable')
 AND reason_code ~ '[^[:space:]]' AND reason ~ '[^[:space:]]'
 AND jsonb_typeof(deduction_reasons)='array' AND jsonb_typeof(strengthening_actions)='array'
 AND ((outcome='assessed' AND estimated_score IS NOT NULL AND score_range IS NOT NULL
   AND jsonb_typeof(score_range)='object' AND score_range ?& ARRAY['minimum','maximum']
   AND (score_range->>'minimum')::numeric>=0
   AND estimated_score::text NOT IN ('NaN','Infinity','-Infinity')
   AND estimated_score BETWEEN (score_range->>'minimum')::numeric AND (score_range->>'maximum')::numeric
   AND (estimated_score=(score_range->>'maximum')::numeric OR jsonb_array_length(deduction_reasons)>0))
 OR (outcome='unassessable' AND estimated_score IS NULL)));
ALTER TABLE score_item_citations ADD CONSTRAINT score_citation_shape CHECK (
 quote ~ '[^[:space:]]' AND (
 (kind='tender' AND document_id IS NOT NULL AND chunk_id IS NOT NULL AND source IS NOT NULL
  AND draft_id IS NULL AND response_item_id IS NULL AND card_revision_id IS NULL AND field IS NULL)
 OR (kind='draft' AND document_id IS NULL AND chunk_id IS NULL AND source IS NULL
  AND draft_id IS NOT NULL AND response_item_id IS NOT NULL AND card_revision_id IS NOT NULL
  AND field IS NOT NULL AND field IN ('response_text','deviation_note'))));
CREATE INDEX ix_score_reports_task ON score_reports(org_id,task_id,created_at,id);
CREATE INDEX ix_score_report_items_report ON score_report_items(org_id,report_id);
CREATE INDEX ix_score_item_citations_item ON score_item_citations(org_id,score_item_id);
"""

GATE_SQL = r"""
CREATE FUNCTION score_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='score_reports' AND TG_OP='UPDATE'
  AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
  AND (to_jsonb(NEW)-'encrypted_input')=(to_jsonb(OLD)-'encrypted_input') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Score report history is immutable' USING ERRCODE='42501';
END $$;

CREATE FUNCTION score_inputs_current(p_org uuid,p_draft uuid,p_rubric uuid,p_revision integer) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
 response_row public.response_items%ROWTYPE; requirement_row public.requirements%ROWTYPE;
 card_row record; revision_row record;
 evidence_row public.evidence%ROWTYPE; fixed jsonb; dependency jsonb; source_value jsonb;
BEGIN
 SELECT * INTO draft_row FROM public.draft_runs WHERE org_id=p_org AND id=p_draft;
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF draft_row.id IS NULL OR rubric_row.id IS NULL
  OR draft_row.task_id IS DISTINCT FROM rubric_row.task_id
  OR draft_row.extraction_job_id IS DISTINCT FROM rubric_row.extraction_job_id
  OR p_revision IS DISTINCT FROM public.rubric_revision(p_org,p_rubric)
  OR public.rubric_current_inputs(p_org,p_rubric) IS DISTINCT FROM true
  OR public.rubric_complete(p_org,p_rubric) IS DISTINCT FROM true
  OR (SELECT action FROM public.score_rubric_decisions WHERE org_id=p_org AND rubric_id=p_rubric
      AND section_id IS NULL AND item_id IS NULL ORDER BY revision DESC LIMIT 1) IS DISTINCT FROM 'confirm'
  OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=p_org AND prior_rubric_id=p_rubric)
  OR jsonb_typeof(draft_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF (SELECT count(*) FROM public.response_items WHERE org_id=p_org AND draft_id=p_draft)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint
  OR (SELECT count(*) FROM public.requirements WHERE org_id=p_org AND job_id=draft_row.extraction_job_id)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(draft_row.input_manifest->'requirements') LOOP
  SELECT * INTO response_row FROM public.response_items WHERE org_id=p_org AND draft_id=p_draft AND requirement_id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO requirement_row FROM public.requirements WHERE org_id=p_org AND id=response_row.requirement_id;
  SELECT id,revision INTO card_row FROM public.response_cards WHERE org_id=p_org AND id=response_row.card_id;
  SELECT id,revision,state,confirmed_by,disposition,model_job_id INTO revision_row FROM public.response_card_revisions WHERE org_id=p_org AND id=response_row.card_revision_id;
  source_value:=jsonb_build_object('document_id',requirement_row.document_id,'chunk_id',requirement_row.chunk_id,
    'page',requirement_row.page,'location',requirement_row.location,'quote',requirement_row.quote);
  IF response_row.id IS NULL OR requirement_row.id IS NULL
   OR requirement_row.task_id IS DISTINCT FROM draft_row.task_id OR requirement_row.document_id IS DISTINCT FROM rubric_row.document_id
   OR requirement_row.job_id IS DISTINCT FROM draft_row.extraction_job_id
   OR response_row.source IS DISTINCT FROM source_value
   OR fixed->>'source_hash' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(source_value),'UTF8')),'hex')
   OR fixed->>'category' IS DISTINCT FROM requirement_row.category OR (fixed->>'starred')::boolean IS DISTINCT FROM requirement_row.starred
   OR fixed->>'kind' IS DISTINCT FROM response_row.kind
   OR (fixed->>'card_revision_id')::uuid IS DISTINCT FROM response_row.card_revision_id
   OR (response_row.card_id IS NOT NULL AND card_row.revision IS DISTINCT FROM revision_row.revision)
   OR (response_row.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE org_id=p_org AND requirement_id=requirement_row.id))
   OR (public.response_citation_valid(p_org,requirement_row.id) IS DISTINCT FROM true AND NOT (response_row.kind='gap' AND response_row.gap_reasons ? 'invalid_citation'))
   OR (response_row.kind IN ('row','comply_only') AND public.response_quote_current(p_org,revision_row.id) IS DISTINCT FROM true)
   OR (response_row.kind='row' AND (revision_row.state IS DISTINCT FROM 'confirmed' OR revision_row.confirmed_by IS NULL))
   OR (response_row.kind='comply_only' AND revision_row.disposition IS DISTINCT FROM 'comply_only')
   OR jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=revision_row.id) THEN RETURN false; END IF;
  FOR dependency IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
   SELECT * INTO evidence_row FROM public.evidence WHERE org_id=p_org AND id=(dependency->>'id')::uuid;
   IF evidence_row.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=revision_row.id AND evidence_id=evidence_row.id)
    OR (dependency->>'confirmed_by')::uuid IS DISTINCT FROM evidence_row.confirmed_by
    OR (dependency->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(p_org,evidence_row.id)
    OR (response_row.kind='row' AND (evidence_row.confirmed_by IS NULL OR public.response_evidence_active(p_org,evidence_row.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
  END LOOP;
  IF revision_row.model_job_id IS NOT NULL AND response_row.kind<>'comply_only'
   AND public.response_generation_materials_active(p_org,revision_row.model_job_id) IS DISTINCT FROM true
   AND NOT(response_row.kind='gap' AND response_row.gap_reasons ? 'stale_material') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION score_report_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
 IF NEW.actor_kind IS DISTINCT FROM 'worker' OR NOT EXISTS(SELECT 1 FROM public.memberships
  WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active AND role IN ('admin','bidder','technical'))
  OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'score:run')) THEN
  RAISE EXCEPTION 'Score worker permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR SHARE;
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id FOR SHARE;
 SELECT * INTO draft_row FROM public.draft_runs WHERE org_id=NEW.org_id AND id=NEW.draft_id;
 IF job_row.kind IS DISTINCT FROM 'score' OR job_row.status IS DISTINCT FROM 'running'
  OR job_row.run_id IS DISTINCT FROM NEW.run_id OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
  OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
  OR job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
  OR NEW.input_manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text
  OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
  OR NEW.input_manifest->>'draft_id' IS DISTINCT FROM NEW.draft_id::text
  OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text
  OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text
  OR NEW.input_manifest->>'draft_input_hash' IS DISTINCT FROM NEW.draft_input_hash
  OR NEW.input_manifest->>'rubric_id' IS DISTINCT FROM NEW.rubric_id::text
  OR (NEW.input_manifest->>'rubric_version')::integer IS DISTINCT FROM NEW.rubric_version
  OR (NEW.input_manifest->>'rubric_revision')::integer IS DISTINCT FROM NEW.rubric_revision
  OR NEW.input_manifest->>'rubric_input_hash' IS DISTINCT FROM NEW.rubric_input_hash
  OR NEW.input_manifest->>'assessment_date' IS DISTINCT FROM to_char(NEW.assessment_date,'YYYY-MM-DD')
  OR NEW.input_manifest->>'prompt_version' IS DISTINCT FROM NEW.prompt_version
  OR NEW.input_manifest->>'schema_version' IS DISTINCT FROM NEW.schema_version
  OR NEW.input_manifest->>'scoring_rule_version' IS DISTINCT FROM NEW.scoring_rule_version
  OR NEW.input_manifest->'model_redaction_enabled' IS DISTINCT FROM 'true'::jsonb
  OR draft_row.input_hash IS DISTINCT FROM NEW.draft_input_hash
  OR rubric_row.input_hash IS DISTINCT FROM NEW.rubric_input_hash OR rubric_row.version IS DISTINCT FROM NEW.rubric_version
  OR public.score_inputs_current(NEW.org_id,NEW.draft_id,NEW.rubric_id,NEW.rubric_revision) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Invalid score publication attempt or fixed input' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION score_child_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE report_row public.score_reports%ROWTYPE; job_row public.jobs%ROWTYPE;
 item_row public.score_report_items%ROWTYPE; rubric_item public.score_rubric_items%ROWTYPE;
 response_row public.response_items%ROWTYPE; raw_text text; report_xid text;
BEGIN
 SELECT * INTO report_row FROM public.score_reports WHERE org_id=NEW.org_id AND id=NEW.report_id;
 SELECT xmin::text INTO report_xid FROM public.score_reports WHERE org_id=NEW.org_id AND id=NEW.report_id;
 IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Historical score report is closed' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=report_row.job_id FOR UPDATE;
 PERFORM public.response_check_actor(NEW.org_id,report_row.actor_user_id,report_row.actor_token_id,'worker');
 IF job_row.status IS DISTINCT FROM 'running' OR job_row.run_id IS DISTINCT FROM report_row.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'Score attempt is not live' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_report_items' THEN
  SELECT * INTO rubric_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=NEW.rubric_item_id;
  SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.anchor_response_item_id;
  IF NEW.anchor_partition IS DISTINCT FROM (CASE response_row.kind WHEN 'row' THEN 'response' ELSE response_row.kind END)
   OR NEW.score_range IS DISTINCT FROM rubric_item.score_range
   OR (NEW.outcome='assessed' AND rubric_item.assessment_mode<>'model_assessable') THEN
   RAISE EXCEPTION 'Score item fixed rubric or anchor mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO item_row FROM public.score_report_items WHERE org_id=NEW.org_id AND id=NEW.score_item_id;
  IF item_row.report_id IS DISTINCT FROM NEW.report_id OR item_row.task_id IS DISTINCT FROM NEW.task_id THEN
   RAISE EXCEPTION 'Score child report mismatch' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='score_report_item_responses' THEN
   SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
   IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row'
    OR response_row.card_revision_id IS DISTINCT FROM NEW.card_revision_id
    OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions WHERE org_id=NEW.org_id AND id=NEW.card_revision_id AND state='confirmed' AND confirmed_by IS NOT NULL) THEN
    RAISE EXCEPTION 'Score support requires a fixed confirmed response' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.kind='tender' THEN
    SELECT * INTO rubric_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=item_row.rubric_item_id;
    IF NEW.source IS DISTINCT FROM jsonb_set(rubric_item.source,'{quote}',to_jsonb(NEW.quote))
     OR NEW.document_id IS DISTINCT FROM (rubric_item.source->>'document_id')::uuid
     OR NEW.chunk_id IS DISTINCT FROM (rubric_item.source->>'chunk_id')::uuid
     OR public.response_citation_valid(NEW.org_id,rubric_item.requirement_id) IS DISTINCT FROM true THEN
     RAISE EXCEPTION 'Score tender citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=rubric_item.source->>'quote';
   ELSE
    SELECT * INTO response_row FROM public.response_items WHERE org_id=NEW.org_id AND id=NEW.response_item_id;
    IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row' OR NEW.draft_id IS DISTINCT FROM report_row.draft_id
     OR NEW.card_revision_id IS DISTINCT FROM response_row.card_revision_id THEN
     RAISE EXCEPTION 'Score draft citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=(CASE NEW.field WHEN 'response_text' THEN response_row.response_text WHEN 'deviation_note' THEN response_row.deviation_note END);
   END IF;
   IF raw_text IS NULL OR position(NEW.quote in raw_text)=0
    OR position(NEW.quote in substring(raw_text from position(NEW.quote in raw_text)+1))>0 THEN
    RAISE EXCEPTION 'Score citation must be a unique exact span' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION score_publication_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; assessed integer; unassessable integer; section_value jsonb;
BEGIN
 SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
 IF job_row.status NOT IN ('running','succeeded') OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR public.score_inputs_current(NEW.org_id,NEW.draft_id,NEW.rubric_id,NEW.rubric_revision) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Score attempt or inputs lost before publication' USING ERRCODE='23514'; END IF;
 SELECT count(*) FILTER(WHERE outcome='assessed'),count(*) FILTER(WHERE outcome='unassessable') INTO assessed,unassessable
  FROM public.score_report_items WHERE org_id=NEW.org_id AND report_id=NEW.id;
 IF assessed+unassessable NOT BETWEEN 1 AND 2000 OR assessed+unassessable<>(SELECT count(*) FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id)
  OR (NEW.summary->>'assessed_items')::integer IS DISTINCT FROM assessed
  OR (NEW.summary->>'unassessable_items')::integer IS DISTINCT FROM unassessable
  OR (unassessable>0 AND NEW.completion<>'partial')
  OR NEW.summary->>'total_status' IS NULL OR NEW.summary->>'total_status' NOT IN ('estimated','range_only','unavailable')
  OR (NEW.completion='complete' AND NEW.summary->>'total_status'<>'estimated')
  OR ((NEW.summary->>'total_status'='estimated') IS DISTINCT FROM (NEW.summary->>'estimated_total' IS NOT NULL))
  OR ((unassessable>0 OR NEW.completion<>'complete') AND NEW.summary->>'estimated_total' IS NOT NULL)
  OR (NEW.summary->>'total_status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id AND overall_aggregation IN ('formula','non_additive'))) THEN
  RAISE EXCEPTION 'Score summary must match published coverage' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_report_items item WHERE item.org_id=NEW.org_id AND item.report_id=NEW.id AND item.outcome='assessed'
  AND (NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=item.org_id AND cite.score_item_id=item.id AND cite.kind='tender')
   OR NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=item.org_id AND cite.score_item_id=item.id AND cite.kind='draft')))
  OR EXISTS(SELECT 1 FROM public.score_report_item_responses support WHERE support.org_id=NEW.org_id AND support.report_id=NEW.id
   AND NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.org_id=support.org_id AND cite.score_item_id=support.score_item_id AND cite.response_item_id=support.response_item_id AND cite.kind='draft')) THEN
  RAISE EXCEPTION 'Score assessed items require tender and actual supporting draft citations' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.sections)<>(SELECT count(*) FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id)
  OR (SELECT count(DISTINCT entry->>'section_key') FROM jsonb_array_elements(NEW.sections) entry)<>jsonb_array_length(NEW.sections) THEN
  RAISE EXCEPTION 'Score report must include each rubric section' USING ERRCODE='23514'; END IF;
 FOR section_value IN SELECT value FROM jsonb_array_elements(NEW.sections) LOOP
  SELECT count(*) FILTER(WHERE item.outcome='assessed'),count(*) FILTER(WHERE item.outcome='unassessable') INTO assessed,unassessable
   FROM public.score_report_items item JOIN public.score_rubric_sections section ON section.org_id=item.org_id AND section.id=item.section_id
   WHERE item.org_id=NEW.org_id AND item.report_id=NEW.id AND section.key=section_value->>'section_key';
  IF assessed+unassessable=0 OR (section_value->>'assessed_items')::integer IS DISTINCT FROM assessed
   OR (section_value->>'unassessable_items')::integer IS DISTINCT FROM unassessable
   OR ((section_value->>'status'='estimated') IS DISTINCT FROM (section_value->>'estimated_score' IS NOT NULL))
   OR section_value->>'status' IS NULL OR section_value->>'status' NOT IN ('estimated','range_only','unavailable')
   OR (unassessable>0 AND section_value->>'estimated_score' IS NOT NULL)
   OR (section_value->>'status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND key=section_value->>'section_key' AND aggregation IN ('formula','non_additive'))) THEN
   RAISE EXCEPTION 'Score section summary must match published coverage' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
"""
