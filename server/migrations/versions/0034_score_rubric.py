"""Versioned score rubrics with tenant, publication and human review gates."""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

TABLES = (
    "score_rubric_sets",
    "score_rubric_sections",
    "score_rubric_items",
    "score_rubric_coverage",
    "score_rubric_decisions",
    "score_rubric_classifications",
    "score_rubric_coverage_decisions",
    "score_rubric_coverage_items",
    "score_rubric_revision_events",
)
POLICY = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def upgrade():
    op.execute(
        "ALTER TABLE requirements ADD CONSTRAINT rubric_requirement_task UNIQUE(org_id,id,task_id)"
    )
    op.execute(
        "ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_score_scopes CHECK (NOT (scopes ? 'score:rubric:review'))"
    )
    op.execute(TABLE_SQL)
    op.execute(GATE_SQL)
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "{table}" USING ({POLICY}) WITH CHECK ({POLICY})'
        )
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
        op.execute(
            f'CREATE TRIGGER rubric_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION rubric_immutable_gate()'
        )
    op.execute("GRANT UPDATE(id) ON score_rubric_sets TO bid_app")
    op.execute(TRIGGER_SQL)


def downgrade():
    raise RuntimeError("Data-preserving rollback required; retain rubric review history")


TABLE_SQL = r"""

CREATE TABLE score_rubric_sets (
	task_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	prior_rubric_id UUID, 
	version INTEGER NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	input_manifest JSONB NOT NULL, 
	encrypted_input TEXT NOT NULL, 
	normalization_rule_version VARCHAR(100) NOT NULL, 
	prompt_version VARCHAR(100) NOT NULL, 
	schema_version VARCHAR(100) NOT NULL, 
	overall_aggregation VARCHAR(30) NOT NULL, 
	overall_rule_text TEXT, 
	overall_score_range JSONB, 
	overall_cap NUMERIC(18, 8), 
	normalization_errors JSONB NOT NULL,
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
	UNIQUE (org_id, extraction_job_id, version), 
	FOREIGN KEY(org_id, job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id, document_id) REFERENCES jobs (org_id, id, task_id, document_id), 
	FOREIGN KEY(org_id, document_id, task_id) REFERENCES documents (org_id, id, task_id), 
	FOREIGN KEY(org_id, prior_rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, actor_token_id) REFERENCES api_tokens (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_sets_org_id ON score_rubric_sets (org_id);

CREATE TABLE score_rubric_sections (
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	key TEXT NOT NULL, 
	title TEXT NOT NULL, 
	"order" INTEGER NOT NULL, 
	aggregation VARCHAR(30) NOT NULL, 
	aggregation_rule_text TEXT, 
	score_range JSONB, 
	weight NUMERIC(18, 8), 
	cap NUMERIC(18, 8), 
	included_in_overall_total BOOLEAN NOT NULL, 
	ambiguity_reason TEXT, 
	source JSONB NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	citation_valid BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	UNIQUE (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, rubric_id, key), 
	FOREIGN KEY(org_id, requirement_id, task_id) REFERENCES requirements (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_sections_org_id ON score_rubric_sections (org_id);

CREATE TABLE score_rubric_items (
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	section_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	key TEXT NOT NULL, 
	title TEXT NOT NULL, 
	rule_text TEXT NOT NULL, 
	"order" INTEGER NOT NULL, 
	assessment_mode VARCHAR(30) NOT NULL, 
	score_range JSONB, 
	weight NUMERIC(18, 8), 
	ambiguity_reason TEXT, 
	source JSONB NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	citation_valid BOOLEAN NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	UNIQUE (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, rubric_id, key), 
	FOREIGN KEY(org_id, section_id, task_id, rubric_id) REFERENCES score_rubric_sections (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id, requirement_id, task_id) REFERENCES requirements (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_items_org_id ON score_rubric_items (org_id);

CREATE TABLE score_rubric_coverage (
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	source JSONB NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	UNIQUE (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, rubric_id, requirement_id), 
	FOREIGN KEY(org_id, requirement_id, task_id) REFERENCES requirements (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_coverage_org_id ON score_rubric_coverage (org_id);

CREATE TABLE score_rubric_decisions (
	section_id UUID, 
	item_id UUID, 
	action VARCHAR(20) NOT NULL, 
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	set_revision INTEGER NOT NULL, 
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
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, section_id, task_id, rubric_id) REFERENCES score_rubric_sections (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id, item_id, task_id, rubric_id) REFERENCES score_rubric_items (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, rubric_id, set_revision), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_decisions_org_id ON score_rubric_decisions (org_id);

CREATE TABLE score_rubric_classifications (
	section_id UUID, 
	item_id UUID, 
	review_domain VARCHAR(20) NOT NULL, 
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	set_revision INTEGER NOT NULL, 
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
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, section_id, task_id, rubric_id) REFERENCES score_rubric_sections (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id, item_id, task_id, rubric_id) REFERENCES score_rubric_items (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, rubric_id, set_revision), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_classifications_org_id ON score_rubric_classifications (org_id);

CREATE TABLE score_rubric_coverage_decisions (
	coverage_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	canonical_requirement_id UUID, 
	action VARCHAR(20) NOT NULL, 
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	set_revision INTEGER NOT NULL, 
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
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id), 
	UNIQUE (org_id, id, task_id, rubric_id), 
	UNIQUE (org_id, coverage_id, revision), 
	FOREIGN KEY(org_id, coverage_id, task_id, rubric_id) REFERENCES score_rubric_coverage (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id, requirement_id, task_id) REFERENCES requirements (org_id, id, task_id), 
	FOREIGN KEY(org_id, canonical_requirement_id, task_id) REFERENCES requirements (org_id, id, task_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_coverage_decisions_org_id ON score_rubric_coverage_decisions (org_id);

CREATE TABLE score_rubric_coverage_items (
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	coverage_decision_id UUID NOT NULL, 
	rubric_item_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	UNIQUE (org_id, coverage_decision_id, rubric_item_id), 
	FOREIGN KEY(org_id, coverage_decision_id, task_id, rubric_id) REFERENCES score_rubric_coverage_decisions (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id, rubric_item_id, task_id, rubric_id) REFERENCES score_rubric_items (org_id, id, task_id, rubric_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_coverage_items_org_id ON score_rubric_coverage_items (org_id);

CREATE TABLE score_rubric_revision_events (
	task_id UUID NOT NULL, 
	rubric_id UUID NOT NULL, 
	prior_rubric_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	expected_revision INTEGER NOT NULL, 
	expected_input_hash VARCHAR(64) NOT NULL, 
	replacement_snapshot JSONB NOT NULL,
	snapshot_sha256 VARCHAR(64) NOT NULL,
	reason TEXT NOT NULL, 
	reason_sha256 VARCHAR(64) NOT NULL, 
	revised_by UUID NOT NULL, 
	revised_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	actor_kind VARCHAR(20) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, task_id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	UNIQUE (org_id, rubric_id), 
	FOREIGN KEY(org_id, prior_rubric_id, task_id) REFERENCES score_rubric_sets (org_id, id, task_id), 
	FOREIGN KEY(org_id, revised_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
CREATE INDEX ix_score_rubric_revision_events_org_id ON score_rubric_revision_events (org_id);
"""

GATE_SQL = r"""
CREATE UNIQUE INDEX rubric_generated_job_once ON score_rubric_sets(org_id,job_id) WHERE prior_rubric_id IS NULL;
CREATE UNIQUE INDEX rubric_revision_once ON score_rubric_sets(org_id,prior_rubric_id) WHERE prior_rubric_id IS NOT NULL;
ALTER TABLE score_rubric_sets ADD CHECK(jsonb_typeof(input_manifest)='object'), ADD CHECK(coalesce(jsonb_typeof(input_manifest->'requirements')='array',false)), ADD CHECK(jsonb_typeof(normalization_errors)='array'), ADD CHECK(version>=1), ADD CHECK(actor_kind IN ('worker','session')),
 ADD CHECK(length(input_hash)=64), ADD CHECK(overall_aggregation IN ('sum','weighted_sum','capped_sum','formula','non_additive')),
 ADD CHECK((overall_aggregation='capped_sum')=(overall_cap IS NOT NULL)), ADD CHECK(overall_cap>=0 AND overall_cap NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric));
ALTER TABLE score_rubric_sections ADD CHECK(title ~ '[^[:space:]]'), ADD CHECK("order">=1), ADD CHECK(weight>0 AND weight<=1), ADD CHECK(cap>=0 AND cap NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 ADD CHECK(aggregation IN ('sum','weighted_sum','capped_sum','formula','non_additive')),
 ADD CHECK((aggregation='capped_sum')=(cap IS NOT NULL)), ADD CHECK(key ~ '[^[:space:]]');
ALTER TABLE score_rubric_items ADD CHECK(title ~ '[^[:space:]]'), ADD CHECK(rule_text ~ '[^[:space:]]'), ADD UNIQUE(org_id,rubric_id,fingerprint), ADD CHECK("order">=1), ADD CHECK(weight>0 AND weight<=1), ADD CHECK(key ~ '[^[:space:]]'),
 ADD CHECK(assessment_mode IN ('model_assessable','ambiguous','price_comparison','external_comparison','manual_only','unsupported_formula'));
ALTER TABLE score_rubric_decisions ADD CHECK(NOT(section_id IS NOT NULL AND item_id IS NOT NULL)), ADD CHECK(action IN ('confirm','reject','reopen')),
 ADD CHECK(actor_kind='session'), ADD CHECK(revision>=2), ADD CHECK(set_revision>=2), ADD CHECK(reason ~ '[^[:space:]]');
ALTER TABLE score_rubric_classifications ADD CHECK((section_id IS NULL)<>(item_id IS NULL)), ADD CHECK(review_domain IN ('commercial','technical')),
 ADD CHECK(actor_kind='session'), ADD CHECK(revision>=2), ADD CHECK(set_revision>=2), ADD CHECK(reason ~ '[^[:space:]]');
ALTER TABLE score_rubric_coverage_decisions ADD CHECK(action IN ('mapped','duplicate','excluded','reopen')),
 ADD CHECK((action='duplicate')=(canonical_requirement_id IS NOT NULL)), ADD CHECK(canonical_requirement_id<>requirement_id),
 ADD CHECK(actor_kind='session'), ADD CHECK(revision>=2), ADD CHECK(set_revision>=2), ADD CHECK(reason ~ '[^[:space:]]');
ALTER TABLE score_rubric_revision_events ADD CHECK(jsonb_typeof(replacement_snapshot)='object'), ADD CHECK(length(snapshot_sha256)=64), ADD CHECK(actor_kind='session'), ADD CHECK(version>=2), ADD CHECK(reason ~ '[^[:space:]]');

CREATE FUNCTION rubric_immutable_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='score_rubric_sets' AND TG_OP='UPDATE'
  AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
  AND (to_jsonb(NEW)-'encrypted_input')=(to_jsonb(OLD)-'encrypted_input') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Rubric history is immutable' USING ERRCODE='42501';
END $$;

CREATE FUNCTION rubric_revision(p_org uuid,p_rubric uuid,p_section uuid DEFAULT NULL,p_item uuid DEFAULT NULL) RETURNS integer
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT coalesce(max(n),1) FROM (
 SELECT (CASE WHEN p_section IS NULL AND p_item IS NULL THEN set_revision ELSE revision END) n FROM public.score_rubric_decisions
 WHERE org_id=p_org AND rubric_id=p_rubric AND ((p_section IS NULL AND p_item IS NULL) OR (section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item))
 UNION ALL SELECT (CASE WHEN p_section IS NULL AND p_item IS NULL THEN set_revision ELSE revision END) FROM public.score_rubric_classifications
 WHERE org_id=p_org AND rubric_id=p_rubric AND ((p_section IS NULL AND p_item IS NULL) OR (section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item))
 UNION ALL SELECT set_revision FROM public.score_rubric_coverage_decisions WHERE org_id=p_org AND rubric_id=p_rubric AND p_section IS NULL AND p_item IS NULL
 ) revisions
$$;

CREATE FUNCTION rubric_subject_confirmed(p_org uuid,p_rubric uuid,p_section uuid,p_item uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT action='confirm' AND revision=public.rubric_revision(p_org,p_rubric,p_section,p_item)
 FROM public.score_rubric_decisions WHERE org_id=p_org AND rubric_id=p_rubric
 AND section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item ORDER BY revision DESC LIMIT 1),false)
$$;

CREATE FUNCTION rubric_set_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE job_row public.jobs%ROWTYPE; prior_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.actor_user_id,NEW.actor_token_id,NEW.actor_kind);
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id AND task_id=NEW.task_id AND document_id=NEW.document_id AND kind='extract' AND status='succeeded') THEN
  RAISE EXCEPTION 'Rubric requires successful fixed extraction' USING ERRCODE='23514'; END IF;
 IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND extraction_job_id=NEW.extraction_job_id) THEN
  RAISE EXCEPTION 'Rubric version conflict' USING ERRCODE='23514'; END IF;
 IF NEW.input_manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
  OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
  RAISE EXCEPTION 'Rubric input binding mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO job_row FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id FOR UPDATE;
  IF NEW.actor_kind<>'worker' OR job_row.kind IS DISTINCT FROM 'score_rubric' OR job_row.status IS DISTINCT FROM 'running'
   OR job_row.run_id IS DISTINCT FROM NEW.run_id OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
   OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
   OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
   OR job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
   OR NOT EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active AND role IN ('admin','bidder','technical'))
   OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE org_id=NEW.org_id AND id=NEW.actor_token_id AND scopes ? 'score:rubric:generate')) THEN
   RAISE EXCEPTION 'Rubric publication requires owning live worker attempt' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT * INTO prior_row FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.prior_rubric_id FOR UPDATE;
  IF NEW.actor_kind<>'session' OR NOT EXISTS(SELECT 1 FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active AND role IN ('bidder','technical'))
   OR NEW.input_hash IS DISTINCT FROM prior_row.input_hash OR NEW.input_manifest IS DISTINCT FROM prior_row.input_manifest
   OR NEW.encrypted_input IS DISTINCT FROM prior_row.encrypted_input OR NEW.extraction_job_id IS DISTINCT FROM prior_row.extraction_job_id
   OR NEW.job_id IS DISTINCT FROM prior_row.job_id OR NEW.run_id IS DISTINCT FROM prior_row.run_id
   OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND prior_rubric_id=NEW.prior_rubric_id) THEN
   RAISE EXCEPTION 'Invalid human rubric revision' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION rubric_child_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; req_row public.requirements%ROWTYPE; current_xmin text;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id;
 SELECT xmin::text INTO current_xmin FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id;
 IF current_xmin IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Rubric snapshots cannot be extended' USING ERRCODE='42501'; END IF;
 PERFORM public.response_check_actor(set_row.org_id,set_row.actor_user_id,set_row.actor_token_id,set_row.actor_kind);
 SELECT * INTO req_row FROM public.requirements WHERE org_id=NEW.org_id AND id=NEW.requirement_id;
 IF req_row.job_id IS DISTINCT FROM set_row.extraction_job_id OR req_row.category IS DISTINCT FROM 'scoring'
  OR NEW.source->>'document_id' IS DISTINCT FROM req_row.document_id::text OR NEW.source->>'chunk_id' IS DISTINCT FROM req_row.chunk_id::text
  OR NEW.source->>'quote' IS DISTINCT FROM req_row.quote
  OR NEW.source->>'page' IS DISTINCT FROM req_row.page::text
  OR coalesce(NEW.source->'location','null'::jsonb) IS DISTINCT FROM coalesce(req_row.location,'null'::jsonb) THEN
  RAISE EXCEPTION 'Rubric source must bind fixed scoring requirement' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION rubric_range_valid(bounds jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT bounds IS NULL OR coalesce(
  (bounds->>'minimum')::numeric NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND (bounds->>'maximum')::numeric NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND (bounds->>'minimum')::numeric>=0 AND (bounds->>'maximum')::numeric>=(bounds->>'minimum')::numeric
  AND (bounds->>'maximum')::numeric<=9999999999.99999999
  AND scale(trim_scale((bounds->>'minimum')::numeric))<=8 AND scale(trim_scale((bounds->>'maximum')::numeric))<=8,false)
$$;

ALTER TABLE score_rubric_sets ADD CHECK(public.rubric_range_valid(overall_score_range));
ALTER TABLE score_rubric_sections ADD CHECK(public.rubric_range_valid(score_range));
ALTER TABLE score_rubric_items ADD CHECK(public.rubric_range_valid(score_range));

CREATE FUNCTION rubric_canonical_mapped(p_org uuid,p_rubric uuid,p_requirement uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE visited uuid[]:=ARRAY[]::uuid[]; current_requirement uuid:=p_requirement; latest_row public.score_rubric_coverage_decisions%ROWTYPE;
BEGIN
 LOOP
  IF current_requirement=ANY(visited) THEN RETURN false; END IF;
  visited:=array_append(visited,current_requirement);
  SELECT d.* INTO latest_row FROM public.score_rubric_coverage_decisions d JOIN public.score_rubric_coverage c ON c.org_id=d.org_id AND c.id=d.coverage_id
   WHERE c.org_id=p_org AND c.rubric_id=p_rubric AND c.requirement_id=current_requirement ORDER BY d.revision DESC LIMIT 1;
  IF NOT FOUND THEN RETURN false; END IF;
  IF latest_row.action='mapped' THEN RETURN true; END IF;
  IF latest_row.action<>'duplicate' THEN RETURN false; END IF;
  current_requirement:=latest_row.canonical_requirement_id;
 END LOOP;
END $$;

CREATE FUNCTION rubric_canonical_json(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT CASE jsonb_typeof(p_value)
 WHEN 'object' THEN (SELECT '{'||coalesce(string_agg(to_jsonb(entry.key)::text||':'||public.rubric_canonical_json(entry.value),',' ORDER BY entry.key COLLATE "C"),'')||'}' FROM jsonb_each(p_value) entry)
 WHEN 'array' THEN (SELECT '['||coalesce(string_agg(public.rubric_canonical_json(entry.value),',' ORDER BY entry.position),'')||']' FROM jsonb_array_elements(p_value) WITH ORDINALITY entry(value,position))
 ELSE p_value::text END
$$;

CREATE FUNCTION rubric_current_inputs(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE fixed jsonb; set_row public.score_rubric_sets%ROWTYPE; req_row public.requirements%ROWTYPE; chunk_row public.chunks%ROWTYPE; source_value jsonb;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF NOT FOUND OR jsonb_typeof(set_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(set_row.input_manifest->'requirements') NOT BETWEEN 1 AND 2000
  OR (SELECT count(DISTINCT entry->>'requirement_id') FROM jsonb_array_elements(set_row.input_manifest->'requirements') entry) IS DISTINCT FROM jsonb_array_length(set_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE org_id=p_org AND id=set_row.extraction_job_id AND task_id=set_row.task_id AND document_id=set_row.document_id AND kind='extract' AND status='succeeded')
  OR set_row.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE org_id=p_org AND id=set_row.document_id AND task_id=set_row.task_id)
  OR jsonb_array_length(set_row.input_manifest->'requirements') IS DISTINCT FROM (SELECT count(*) FROM public.requirements WHERE org_id=p_org AND task_id=set_row.task_id AND job_id=set_row.extraction_job_id AND category='scoring') THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(set_row.input_manifest->'requirements') LOOP
  SELECT * INTO req_row FROM public.requirements WHERE org_id=p_org AND id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO chunk_row FROM public.chunks WHERE org_id=p_org AND id=(fixed->>'chunk_id')::uuid;
  source_value:=jsonb_build_object('document_id',req_row.document_id,'chunk_id',req_row.chunk_id,'page',req_row.page,'location',req_row.location,'quote',req_row.quote);
  IF req_row.id IS NULL OR chunk_row.id IS NULL OR req_row.task_id IS DISTINCT FROM set_row.task_id OR req_row.job_id IS DISTINCT FROM set_row.extraction_job_id
   OR req_row.category IS DISTINCT FROM 'scoring' OR req_row.document_id IS DISTINCT FROM set_row.document_id OR req_row.chunk_id IS DISTINCT FROM chunk_row.id
   OR chunk_row.task_id IS DISTINCT FROM set_row.task_id OR chunk_row.document_id IS DISTINCT FROM set_row.document_id
   OR public.response_citation_valid(p_org,req_row.id) IS DISTINCT FROM true
   OR fixed->>'source_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(source_value),'UTF8')),'hex')
   OR fixed->>'requirement_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',req_row.text,'category',req_row.category,'starred',req_row.starred)),'UTF8')),'hex')
   OR fixed->>'chunk_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',chunk_row.text,'blocks',chunk_row.blocks,'page',chunk_row.page,'citation_verified',chunk_row.citation_verified)),'UTF8')),'hex') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION rubric_complete(p_org uuid,p_rubric uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; section_row public.score_rubric_sections%ROWTYPE;
 low_value numeric; high_value numeric; total_low numeric:=0; total_high numeric:=0; weight_total numeric; child_count integer;
 all_bounds boolean:=true; section_bounds boolean;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=p_org AND id=p_rubric;
 IF NOT FOUND OR NOT public.rubric_range_valid(set_row.overall_score_range) OR set_row.normalization_errors<>'[]'::jsonb THEN RETURN false; END IF;
 IF public.rubric_current_inputs(p_org,p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric)
 OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY section_id,"order" HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY "order" HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric
  GROUP BY normalize(regexp_replace(btrim(rule_text),'\s+',' ','g'),NFKC),assessment_mode,
   (score_range->>'minimum')::numeric,(score_range->>'maximum')::numeric,weight,
   normalize(regexp_replace(btrim(ambiguity_reason),'\s+',' ','g'),NFKC),source HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric
  AND (NOT i.citation_valid OR NOT public.rubric_subject_confirmed(p_org,p_rubric,NULL,i.id)
   OR NOT public.rubric_range_valid(i.score_range) OR (i.assessment_mode='model_assessable' AND i.score_range IS NULL)
   OR (i.assessment_mode<>'model_assessable' AND NOT coalesce(i.ambiguity_reason ~ '[^[:space:]]',false)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage c LEFT JOIN LATERAL (
   SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.org_id=p_org AND d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   WHERE c.org_id=p_org AND c.rubric_id=p_rubric AND (latest.id IS NULL OR latest.action='reopen'
    OR (latest.action='mapped' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage_items ci WHERE ci.org_id=p_org AND ci.coverage_decision_id=latest.id))
    OR (latest.action='duplicate' AND NOT public.rubric_canonical_mapped(p_org,p_rubric,latest.canonical_requirement_id)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.org_id=p_org AND i.rubric_id=p_rubric AND 1<>(
  SELECT count(*) FROM public.score_rubric_coverage c JOIN LATERAL (SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.org_id=p_org AND d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   JOIN public.score_rubric_coverage_items ci ON ci.org_id=p_org AND ci.coverage_decision_id=latest.id
   WHERE c.org_id=p_org AND c.rubric_id=p_rubric AND latest.action='mapped' AND ci.rubric_item_id=i.id AND c.requirement_id=i.requirement_id)) THEN RETURN false; END IF;
 FOR section_row IN SELECT * FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric LOOP
  IF NOT section_row.citation_valid OR NOT public.rubric_subject_confirmed(p_org,p_rubric,section_row.id,NULL) OR NOT public.rubric_range_valid(section_row.score_range) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric AND section_id=section_row.id)
   OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
  IF section_row.aggregation IN ('formula','non_additive') THEN
   IF NOT coalesce(section_row.aggregation_rule_text ~ '[^[:space:]]',false) OR NOT coalesce(section_row.ambiguity_reason ~ '[^[:space:]]',false) THEN RETURN false; END IF;
   section_bounds:=section_row.score_range IS NOT NULL;
   low_value:=(section_row.score_range->>'minimum')::numeric;
   high_value:=(section_row.score_range->>'maximum')::numeric;
  ELSE
   SELECT count(*),bool_and(score_range IS NOT NULL),sum(weight),
    sum((score_range->>'minimum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END)),
    sum((score_range->>'maximum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END))
    INTO child_count,section_bounds,weight_total,low_value,high_value FROM public.score_rubric_items WHERE org_id=p_org AND rubric_id=p_rubric AND section_id=section_row.id;
   IF child_count=0 OR (section_row.aggregation='weighted_sum' AND (weight_total IS DISTINCT FROM 1 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NULL)))
    OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=p_org AND section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
   IF section_row.aggregation='capped_sum' THEN low_value:=least(low_value,section_row.cap);high_value:=least(high_value,section_row.cap); END IF;
   IF section_bounds AND section_row.score_range IS NOT NULL AND ((section_row.score_range->>'minimum')::numeric<>round(low_value,8) OR (section_row.score_range->>'maximum')::numeric<>round(high_value,8)) THEN RETURN false; END IF;
  END IF;
  IF NOT section_row.included_in_overall_total AND section_row.weight IS NOT NULL THEN RETURN false; END IF;
  IF section_row.included_in_overall_total THEN
   IF set_row.overall_aggregation='weighted_sum' AND section_row.weight IS NULL OR set_row.overall_aggregation<>'weighted_sum' AND section_row.weight IS NOT NULL THEN RETURN false; END IF;
   all_bounds:=all_bounds AND section_bounds;
   total_low:=total_low+low_value*(CASE WHEN set_row.overall_aggregation='weighted_sum' THEN section_row.weight ELSE 1 END);
   total_high:=total_high+high_value*(CASE WHEN set_row.overall_aggregation='weighted_sum' THEN section_row.weight ELSE 1 END);
  END IF;
 END LOOP;
 IF set_row.overall_aggregation='weighted_sum' AND (SELECT sum(weight) FROM public.score_rubric_sections WHERE org_id=p_org AND rubric_id=p_rubric AND included_in_overall_total) IS DISTINCT FROM 1 THEN RETURN false; END IF;
 IF set_row.overall_aggregation IN ('formula','non_additive') THEN RETURN coalesce(set_row.overall_rule_text ~ '[^[:space:]]',false); END IF;
 IF set_row.overall_aggregation='capped_sum' THEN total_low:=least(total_low,set_row.overall_cap);total_high:=least(total_high,set_row.overall_cap); END IF;
 RETURN NOT all_bounds OR set_row.overall_score_range IS NULL OR ((set_row.overall_score_range->>'minimum')::numeric=round(total_low,8) AND (set_row.overall_score_range->>'maximum')::numeric=round(total_high,8));
END $$;

CREATE FUNCTION rubric_review_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; domain_value text; role_value text; prior_revision integer; subject_section uuid; subject_item uuid; is_set boolean; previous_action text; previous_revision integer;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.decided_by,NULL,'session');
 PERFORM 1 FROM public.memberships auth_members JOIN public.users auth_users ON auth_users.id=auth_members.user_id JOIN public.orgs auth_org ON auth_org.id=auth_members.org_id
  WHERE auth_members.org_id=NEW.org_id AND auth_members.user_id=NEW.decided_by FOR SHARE OF auth_members,auth_users,auth_org;
 PERFORM public.response_check_actor(NEW.org_id,NEW.decided_by,NULL,'session');
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id FOR UPDATE;
 SELECT role INTO role_value FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.decided_by AND active;
 IF public.rubric_current_inputs(NEW.org_id,NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric fixed inputs changed' USING ERRCODE='23514'; END IF;
 IF NEW.actor_kind<>'session' OR NEW.expected_input_hash IS DISTINCT FROM set_row.input_hash OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Invalid rubric human review context' USING ERRCODE='42501'; END IF;
 IF NEW.set_revision<>public.rubric_revision(NEW.org_id,NEW.rubric_id)+1
 OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND prior_rubric_id=NEW.rubric_id) THEN
  RAISE EXCEPTION 'Rubric revision conflict' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' THEN
  IF role_value<>'bidder' OR NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE org_id=NEW.org_id AND id=NEW.coverage_id AND requirement_id=NEW.requirement_id) THEN RAISE EXCEPTION 'Coverage requires bidder and fixed requirement' USING ERRCODE='42501'; END IF;
  SELECT coalesce(max(revision),1) INTO prior_revision FROM public.score_rubric_coverage_decisions WHERE org_id=NEW.org_id AND coverage_id=NEW.coverage_id;
  IF NEW.canonical_requirement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND requirement_id=NEW.canonical_requirement_id) THEN RAISE EXCEPTION 'Invalid canonical requirement' USING ERRCODE='23514'; END IF;
 ELSE
  subject_section:=NEW.section_id;subject_item:=NEW.item_id;
  is_set:=subject_section IS NULL AND subject_item IS NULL;
  prior_revision:=public.rubric_revision(NEW.org_id,NEW.rubric_id,subject_section,subject_item);
  IF TG_TABLE_NAME='score_rubric_classifications' THEN
   IF role_value<>'admin' THEN RAISE EXCEPTION 'Classification requires admin' USING ERRCODE='42501'; END IF;
  ELSIF is_set THEN
   IF role_value<>'bidder' OR NEW.action NOT IN ('confirm','reopen') THEN RAISE EXCEPTION 'Set review requires bidder' USING ERRCODE='42501'; END IF;
   IF NEW.action='confirm' AND NOT public.rubric_complete(NEW.org_id,NEW.rubric_id) THEN RAISE EXCEPTION 'Rubric completeness gate failed' USING ERRCODE='23514'; END IF;
  ELSE
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND section_id IS NOT DISTINCT FROM subject_section AND item_id IS NOT DISTINCT FROM subject_item ORDER BY revision DESC LIMIT 1;
   IF role_value IS DISTINCT FROM (CASE domain_value WHEN 'commercial' THEN 'bidder' WHEN 'technical' THEN 'technical' END) THEN RAISE EXCEPTION 'Responsible domain reviewer required' USING ERRCODE='42501'; END IF;
   IF NEW.action='confirm' AND ((subject_section IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND id=subject_section AND citation_valid))
    OR (subject_item IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=subject_item AND citation_valid))) THEN
    RAISE EXCEPTION 'Unresolved citation cannot be confirmed' USING ERRCODE='23514'; END IF;
  END IF;
  IF TG_TABLE_NAME='score_rubric_decisions' THEN
   SELECT action,revision INTO previous_action,previous_revision FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id
    AND section_id IS NOT DISTINCT FROM subject_section AND item_id IS NOT DISTINCT FROM subject_item ORDER BY revision DESC LIMIT 1;
   IF NOT is_set AND previous_revision IS DISTINCT FROM prior_revision THEN previous_action:=NULL; END IF;
   IF (NEW.action='reopen' AND coalesce(previous_action,'reopen')='reopen') OR (NEW.action<>'reopen' AND coalesce(previous_action,'reopen')<>'reopen') THEN
    RAISE EXCEPTION 'Invalid rubric review transition' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 IF NEW.revision<>prior_revision+1 THEN RAISE EXCEPTION 'Subject revision conflict' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND NOT (TG_TABLE_NAME='score_rubric_decisions' AND subject_section IS NULL AND subject_item IS NULL AND to_jsonb(NEW)->>'action'='reopen') THEN
  RAISE EXCEPTION 'Reopen set before changing human review' USING ERRCODE='23514'; END IF;
 NEW.decided_at:=clock_timestamp(); RETURN NEW;
END $$;

CREATE FUNCTION rubric_coverage_item_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE decision_row public.score_rubric_coverage_decisions%ROWTYPE; decision_xmin text;
BEGIN
 SELECT * INTO decision_row FROM public.score_rubric_coverage_decisions WHERE org_id=NEW.org_id AND id=NEW.coverage_decision_id;
 SELECT xmin::text INTO decision_xmin FROM public.score_rubric_coverage_decisions WHERE org_id=NEW.org_id AND id=NEW.coverage_decision_id;
 PERFORM public.response_check_actor(NEW.org_id,decision_row.decided_by,NULL,'session');
 IF decision_row.action<>'mapped' OR decision_xmin IS DISTINCT FROM pg_current_xact_id()::text OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE org_id=NEW.org_id AND id=NEW.rubric_item_id AND requirement_id=decision_row.requirement_id) THEN
 RAISE EXCEPTION 'Mapped coverage must bind its fixed requirement items' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION rubric_review_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 IF public.rubric_current_inputs(NEW.org_id,NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric inputs changed during review' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' AND ((to_jsonb(NEW)->>'action'='mapped') IS DISTINCT FROM EXISTS(SELECT 1 FROM public.score_rubric_coverage_items WHERE org_id=NEW.org_id AND coverage_decision_id=NEW.id)) THEN
  RAISE EXCEPTION 'Mapped coverage requires normalized item links' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_complete(NEW.org_id,NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Confirmed rubric must remain complete through commit' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION rubric_revision_event_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE new_set public.score_rubric_sets%ROWTYPE; old_set public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.org_id,NEW.revised_by,NULL,'session');
 SELECT * INTO new_set FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.rubric_id;
 SELECT * INTO old_set FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.prior_rubric_id;
 IF NEW.actor_kind<>'session' OR NEW.revised_by IS DISTINCT FROM new_set.actor_user_id OR NEW.prior_rubric_id IS DISTINCT FROM new_set.prior_rubric_id
 OR NEW.snapshot_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.replacement_snapshot::text,'UTF8')),'hex')
 OR NEW.replacement_snapshot->>'expected_input_hash' IS DISTINCT FROM NEW.expected_input_hash
 OR (NEW.replacement_snapshot->>'expected_revision')::integer IS DISTINCT FROM NEW.expected_revision
 OR NEW.version<>new_set.version OR NEW.input_hash IS DISTINCT FROM new_set.input_hash OR NEW.expected_input_hash IS DISTINCT FROM old_set.input_hash
 OR NEW.expected_revision<>public.rubric_revision(NEW.org_id,NEW.prior_rubric_id) OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
 RAISE EXCEPTION 'Invalid rubric revision history' USING ERRCODE='23514'; END IF;
 NEW.revised_at:=clock_timestamp();RETURN NEW;
END $$;

CREATE FUNCTION rubric_publication_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE own_domain text; old_section public.score_rubric_sections%ROWTYPE; new_section public.score_rubric_sections%ROWTYPE;
 old_item public.score_rubric_items%ROWTYPE; new_item public.score_rubric_items%ROWTYPE; domain_value text; old_section_key text; new_section_key text;
 prior_set public.score_rubric_sets%ROWTYPE; owning_job public.jobs%ROWTYPE;
BEGIN
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO owning_job FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
  IF owning_job.status NOT IN ('running','succeeded') OR owning_job.run_id IS DISTINCT FROM NEW.run_id OR owning_job.lease_until IS NULL OR owning_job.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'Rubric owning attempt lost before publication' USING ERRCODE='23514'; END IF;
 END IF;
 IF public.rubric_current_inputs(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric fixed inputs changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_complete(NEW.org_id,NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric lost completeness before publication' USING ERRCODE='23514'; END IF;

 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND public.response_citation_valid(org_id,requirement_id) IS DISTINCT FROM true) THEN
  RAISE EXCEPTION 'Rubric source changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.requirements req WHERE req.org_id=NEW.org_id AND req.job_id=NEW.extraction_job_id AND req.category='scoring' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage c WHERE c.org_id=NEW.org_id AND c.rubric_id=NEW.id AND c.requirement_id=req.id))
 OR (NEW.prior_rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_revision_events WHERE org_id=NEW.org_id AND rubric_id=NEW.id)) THEN
 RAISE EXCEPTION 'Rubric publication lacks complete coverage or revision history' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NOT NULL THEN
  SELECT (CASE role WHEN 'bidder' THEN 'commercial' WHEN 'technical' THEN 'technical' END) INTO own_domain FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.actor_user_id AND active;
  SELECT * INTO prior_set FROM public.score_rubric_sets WHERE org_id=NEW.org_id AND id=NEW.prior_rubric_id;
  IF own_domain='technical' AND ROW(NEW.overall_aggregation,NEW.overall_rule_text,NEW.overall_score_range,NEW.overall_cap) IS DISTINCT FROM ROW(prior_set.overall_aggregation,prior_set.overall_rule_text,prior_set.overall_score_range,prior_set.overall_cap) THEN
   RAISE EXCEPTION 'Overall rule revision requires bidder' USING ERRCODE='42501'; END IF;
  FOR old_section IN SELECT * FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id LOOP
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id AND section_id=old_section.id ORDER BY revision DESC LIMIT 1;
   IF domain_value IS DISTINCT FROM own_domain THEN
    SELECT * INTO new_section FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND key=old_section.key;
    IF NOT FOUND OR (to_jsonb(old_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid']) IS DISTINCT FROM (to_jsonb(new_section)-ARRAY['id','rubric_id','created_at','fingerprint','citation_valid']) THEN
     RAISE EXCEPTION 'Cannot revise another review domain section' USING ERRCODE='42501'; END IF;
   END IF;
  END LOOP;
  FOR old_item IN SELECT * FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id LOOP
   SELECT review_domain INTO domain_value FROM public.score_rubric_classifications WHERE org_id=NEW.org_id AND rubric_id=NEW.prior_rubric_id AND item_id=old_item.id ORDER BY revision DESC LIMIT 1;
   IF domain_value IS DISTINCT FROM own_domain THEN
    SELECT * INTO new_item FROM public.score_rubric_items WHERE org_id=NEW.org_id AND rubric_id=NEW.id AND key=old_item.key;
    IF NOT FOUND OR (to_jsonb(old_item)-ARRAY['id','rubric_id','section_id','created_at','fingerprint','citation_valid']) IS DISTINCT FROM (to_jsonb(new_item)-ARRAY['id','rubric_id','section_id','created_at','fingerprint','citation_valid']) THEN
     RAISE EXCEPTION 'Cannot revise another review domain item' USING ERRCODE='42501'; END IF;
    SELECT key INTO old_section_key FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND id=old_item.section_id;
    SELECT key INTO new_section_key FROM public.score_rubric_sections WHERE org_id=NEW.org_id AND id=new_item.section_id;
    IF old_section_key IS DISTINCT FROM new_section_key THEN RAISE EXCEPTION 'Cannot move another review domain item' USING ERRCODE='42501'; END IF;
   END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
"""

TRIGGER_SQL = r"""
CREATE TRIGGER rubric_set_gate BEFORE INSERT ON score_rubric_sets FOR EACH ROW EXECUTE FUNCTION rubric_set_gate();
CREATE CONSTRAINT TRIGGER rubric_complete_publication AFTER INSERT ON score_rubric_sets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rubric_publication_complete();
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON score_rubric_sections FOR EACH ROW EXECUTE FUNCTION rubric_child_gate();
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON score_rubric_items FOR EACH ROW EXECUTE FUNCTION rubric_child_gate();
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON score_rubric_coverage FOR EACH ROW EXECUTE FUNCTION rubric_child_gate();
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION rubric_review_gate();
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON score_rubric_classifications FOR EACH ROW EXECUTE FUNCTION rubric_review_gate();
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON score_rubric_coverage_decisions FOR EACH ROW EXECUTE FUNCTION rubric_review_gate();
CREATE TRIGGER rubric_coverage_item_gate BEFORE INSERT ON score_rubric_coverage_items FOR EACH ROW EXECUTE FUNCTION rubric_coverage_item_gate();
CREATE TRIGGER rubric_revision_event_gate BEFORE INSERT ON score_rubric_revision_events FOR EACH ROW EXECUTE FUNCTION rubric_revision_event_gate();
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON score_rubric_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rubric_review_complete();
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON score_rubric_classifications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rubric_review_complete();
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON score_rubric_coverage_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rubric_review_complete();

"""
