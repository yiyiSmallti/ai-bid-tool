"""Immutable uploaded-bid compliance findings and human revision history."""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

TABLES = ("bid_review_findings", "bid_review_finding_sources", "bid_review_finding_events")


def upgrade():
    op.execute(
        "ALTER TABLE bid_review_obligations ADD CONSTRAINT bid_review_obligation_run_key UNIQUE (org_id, task_id, submission_id, review_id, id)"
    )
    op.execute(
        "ALTER TABLE bid_pdf_validations ADD CONSTRAINT bid_review_pdf_validation_key UNIQUE (org_id, task_id, submission_id, preparation_id, document_id, id)"
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
    op.execute(
        "CREATE INDEX bid_review_findings_page ON bid_review_findings(org_id,review_id,ordinal)"
    )
    op.execute(
        "CREATE UNIQUE INDEX bid_review_absence_page_unique ON bid_review_finding_sources(org_id,finding_id,page_id) WHERE kind='absence_page'"
    )
    op.execute(
        "CREATE UNIQUE INDEX bid_review_inventory_source_unique ON bid_review_finding_sources(org_id,finding_id,kind,document_id) WHERE kind IN ('inventory_document','rule_document')"
    )
    op.execute(GUARD_SQL)


def downgrade():
    raise RuntimeError(
        "Disable finding admission and repair forward; retain immutable results and decisions"
    )


TABLE_SQL = (
    r"""

CREATE TABLE bid_review_findings (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	obligation_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	code VARCHAR(100) NOT NULL,
	outcome VARCHAR(20) NOT NULL,
	severity VARCHAR(10) NOT NULL,
	impact VARCHAR(20) NOT NULL,
	tender_support_count INTEGER NOT NULL,
	bid_support_count INTEGER NOT NULL,
	search_source_count INTEGER NOT NULL,
	rule_document_count INTEGER NOT NULL,
	absence_kind VARCHAR(30),
	absence_coverage VARCHAR(30),
	absence_method VARCHAR(30),
	absence_manifest_sha256 VARCHAR(64),
	limitation_count INTEGER NOT NULL,
	details_encrypted TEXT NOT NULL,
	details_sha256 VARCHAR(64) NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id) REFERENCES bid_review_runs (org_id, task_id, submission_id, id),
	UNIQUE (org_id, task_id, submission_id, review_id, id),
	UNIQUE (org_id, review_id, ordinal),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, obligation_id) REFERENCES bid_review_obligations (org_id, task_id, submission_id, review_id, id),
	CHECK (tender_support_count BETWEEN 1 AND 20 AND bid_support_count BETWEEN 0 AND 20 AND search_source_count BETWEEN 0 AND 1000 AND rule_document_count BETWEEN 0 AND 19 AND limitation_count BETWEEN 0 AND 100),
	CHECK ((absence_kind IS NULL AND absence_coverage IS NULL AND absence_method IS NULL AND absence_manifest_sha256 IS NULL AND search_source_count=0 AND (bid_support_count>0 OR rule_document_count>0)) OR (absence_kind='locations' AND absence_coverage IN ('required_locations','all_bid_pages','partial') AND absence_method IN ('local_text','local_pdf','ocr','llm','human') AND absence_manifest_sha256 IS NULL AND (search_source_count>0 OR (outcome='unknown' AND absence_coverage='partial'))) OR (absence_kind='submission_inventory' AND absence_coverage IN ('complete_inventory','partial') AND absence_method IN ('manifest_rule','llm_mapping','human') AND absence_manifest_sha256 ~ '^[0-9a-f]{64}$' AND search_source_count BETWEEN 1 AND 19)),
	CHECK ((absence_coverage IS DISTINCT FROM 'partial' OR (outcome='unknown' AND limitation_count>0)) AND (outcome<>'unknown' OR limitation_count>0)),
	CHECK (ordinal BETWEEN 1 AND 10000 AND code ~ '^[a-z][a-z0-9_]{0,99}$'),
	CHECK (outcome IN ('responded','deviation','missing','unknown') AND severity IN ('fatal','high','medium') AND impact IN ('rejection','lost_points','both','uncertain')),
	CHECK ((outcome<>'responded' OR bid_support_count>0) AND (outcome<>'deviation' OR bid_support_count>0 OR rule_document_count>0) AND (outcome NOT IN ('missing','unknown') OR absence_kind IS NOT NULL) AND (outcome<>'missing' OR bid_support_count=0) AND (rule_document_count=0 OR (code='signature_validation_invalid' AND outcome='deviation'))),
	CHECK (length(details_encrypted)>0 AND details_sha256 ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_finding_sources (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	finding_id UUID NOT NULL,
	preparation_id UUID NOT NULL,
	validation_id UUID,
	document_id UUID NOT NULL,
	page_id UUID,
	kind VARCHAR(30) NOT NULL,
	ordinal INTEGER NOT NULL,
	quote_sha256 VARCHAR(64),
	start_offset INTEGER,
	end_offset INTEGER,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, finding_id) REFERENCES bid_review_findings (org_id, task_id, submission_id, review_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, preparation_id, document_id, validation_id) REFERENCES bid_pdf_validations (org_id, task_id, submission_id, preparation_id, document_id, id),
	CHECK ((kind='rule_document')=(validation_id IS NOT NULL)),
	FOREIGN KEY(org_id, task_id, submission_id, document_id) REFERENCES bid_submission_documents (org_id, task_id, submission_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, page_id) REFERENCES bid_document_pages (org_id, task_id, submission_id, id),
	UNIQUE (org_id, finding_id, kind, ordinal),
	CHECK (kind IN ('tender_support','bid_support','absence_page','inventory_document','rule_document') AND ordinal BETWEEN 1 AND 1000),
	CHECK ((kind IN ('inventory_document','rule_document'))=(page_id IS NULL)),
	CHECK ((kind IN ('tender_support','bid_support'))=(quote_sha256 IS NOT NULL)),
	CHECK (quote_sha256 IS NULL OR quote_sha256 ~ '^[0-9a-f]{64}$'),
	CHECK ((quote_sha256 IS NULL AND start_offset IS NULL AND end_offset IS NULL) OR (quote_sha256 IS NOT NULL AND start_offset IS NOT NULL AND end_offset IS NOT NULL AND start_offset>=0 AND end_offset>start_offset)),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
    r"""

CREATE TABLE bid_review_finding_events (
	task_id UUID NOT NULL,
	submission_id UUID NOT NULL,
	review_id UUID NOT NULL,
	finding_id UUID NOT NULL,
	request_id UUID NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	prior_decision_id UUID,
	revision INTEGER NOT NULL,
	action VARCHAR(20) NOT NULL,
	review_domain VARCHAR(20) NOT NULL,
	state VARCHAR(20) NOT NULL,
	expected_input_hash VARCHAR(64) NOT NULL,
	reason_sha256 VARCHAR(64) NOT NULL,
	reason_encrypted TEXT NOT NULL,
	decided_by UUID NOT NULL,
	org_id UUID NOT NULL,
	id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (org_id, id),
	UNIQUE (org_id, task_id, id),
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, finding_id) REFERENCES bid_review_findings (org_id, task_id, submission_id, review_id, id),
	UNIQUE (org_id, task_id, submission_id, review_id, finding_id, id),
	UNIQUE (org_id, finding_id, revision),
	UNIQUE (org_id, decided_by, request_id),
	FOREIGN KEY(org_id, decided_by) REFERENCES memberships (org_id, user_id),
	FOREIGN KEY(org_id, task_id, submission_id, review_id, finding_id, prior_decision_id) REFERENCES bid_review_finding_events (org_id, task_id, submission_id, review_id, finding_id, id),
	CHECK (revision BETWEEN 2 AND 100000 AND action IN ('classify','dismiss','reopen','confirm') AND review_domain IN ('commercial','technical') AND state IN ('open','dismissed','confirmed')),
	CHECK (expected_input_hash ~ '^[0-9a-f]{64}$' AND reason_sha256 ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$' AND length(reason_encrypted)>0),
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""",
)

GUARD_SQL = r"""
CREATE FUNCTION public.bid_review_finding_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET "TimeZone" TO 'UTC' AS $$
DECLARE encrypted_column text;
BEGIN
 encrypted_column := CASE TG_TABLE_NAME WHEN 'bid_review_findings' THEN 'details_encrypted'
 WHEN 'bid_review_finding_events' THEN 'reason_encrypted' ELSE NULL END;
 IF TG_OP='UPDATE' AND encrypted_column IS NOT NULL
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_column)=(to_jsonb(OLD)-encrypted_column) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Findings and human events are immutable' USING ERRCODE='42501';
END $$;
CREATE FUNCTION public.bid_review_finding_output_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r public.bid_review_runs%ROWTYPE; j public.jobs%ROWTYPE;
 p public.bid_document_pages%ROWTYPE; d public.bid_submission_documents%ROWTYPE; f public.bid_review_findings%ROWTYPE;
BEGIN
 SELECT * INTO r FROM public.bid_review_runs WHERE org_id=NEW.org_id AND id=NEW.review_id;
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=r.job_id FOR SHARE;
 IF r.id IS NULL OR j.status IS DISTINCT FROM 'running' OR j.run_id IS NULL
 OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT public.bid_review_run_live(NEW.org_id,NEW.task_id,j.actor_user_id,j.actor_token_id)
 OR EXISTS(SELECT 1 FROM public.bid_review_publications WHERE org_id=NEW.org_id AND review_id=r.id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a
 WHERE a.org_id=NEW.org_id AND a.id=r.authorization_id AND a.allow_external
 AND public.bid_review_actor_live(a.org_id,a.task_id,a.authorized_by)
 AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorizations b
 WHERE b.org_id=a.org_id AND b.submission_id=a.submission_id AND b.revision>a.revision)) THEN
 RAISE EXCEPTION 'Findings require a live authorized review attempt' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_findings' THEN
  IF NEW.absence_kind='submission_inventory' AND (NEW.absence_manifest_sha256 IS DISTINCT FROM
   (SELECT manifest_sha256 FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=r.submission_id)
   OR NEW.search_source_count<>(SELECT count(*) FROM public.bid_submission_documents WHERE org_id=NEW.org_id AND submission_id=r.submission_id AND role='bid')) THEN
   RAISE EXCEPTION 'Absence inventory binding mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.absence_kind='locations' AND NEW.absence_coverage='all_bid_pages' AND NEW.search_source_count<>
   (SELECT count(*) FROM public.bid_document_pages WHERE org_id=NEW.org_id AND preparation_id=r.preparation_id AND role='bid') THEN
   RAISE EXCEPTION 'Whole-bid search is incomplete' USING ERRCODE='23514'; END IF;
  IF (SELECT count(*) FROM public.bid_review_findings WHERE org_id=NEW.org_id AND review_id=r.id AND obligation_id=NEW.obligation_id)>=20 THEN
   RAISE EXCEPTION 'Finding obligation bound exceeded' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO f FROM public.bid_review_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id;
  IF (NEW.kind='inventory_document' AND f.absence_kind IS DISTINCT FROM 'submission_inventory')
  OR (NEW.kind='absence_page' AND f.absence_kind IS DISTINCT FROM 'locations') THEN
   RAISE EXCEPTION 'Absence source kind mismatch' USING ERRCODE='23514'; END IF;
  SELECT * INTO d FROM public.bid_submission_documents WHERE org_id=NEW.org_id AND id=NEW.document_id;
  IF NEW.preparation_id IS DISTINCT FROM r.preparation_id THEN RAISE EXCEPTION 'Finding preparation mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.kind IN ('inventory_document','rule_document') THEN
   IF NEW.kind='rule_document' AND NOT EXISTS(SELECT 1 FROM public.bid_pdf_validations v WHERE v.org_id=NEW.org_id AND v.submission_id=r.submission_id AND v.preparation_id=r.preparation_id AND v.document_id=d.id AND v.id=NEW.validation_id) THEN RAISE EXCEPTION 'PDF rule requires prepared validation' USING ERRCODE='23514'; END IF;
   IF d.role IS DISTINCT FROM 'bid' THEN RAISE EXCEPTION 'Absent file inventory must be submitted bids' USING ERRCODE='23514'; END IF;
  ELSE
   SELECT * INTO p FROM public.bid_document_pages WHERE org_id=NEW.org_id AND id=NEW.page_id;
   IF p.id IS NULL OR p.preparation_id IS DISTINCT FROM r.preparation_id
   OR p.document_id IS DISTINCT FROM NEW.document_id
   OR p.role IS DISTINCT FROM CASE WHEN NEW.kind='tender_support' THEN 'tender' ELSE 'bid' END
   OR (NEW.kind IN ('tender_support','bid_support','absence_page') AND NOT EXISTS(
    SELECT 1 FROM public.bid_outbound_authorized_pages a WHERE a.org_id=NEW.org_id
    AND a.authorization_id=r.authorization_id AND a.page_id=p.id)) THEN
    RAISE EXCEPTION 'Finding source is outside exact authorized inventory' USING ERRCODE='23514'; END IF;
  END IF;
  IF NEW.kind IN ('tender_support','bid_support') AND NEW.ordinal>20 THEN
   RAISE EXCEPTION 'Finding support bound exceeded' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bid_review_findings_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE review uuid; organization uuid; target uuid; publication public.bid_review_publications%ROWTYPE;
BEGIN
 review := NEW.review_id; organization := NEW.org_id;
 IF TG_TABLE_NAME='bid_review_findings' THEN target := NEW.id;
 ELSIF TG_TABLE_NAME='bid_review_finding_sources' THEN target := NEW.finding_id; END IF;
 SELECT * INTO publication FROM public.bid_review_publications WHERE org_id=organization AND review_id=review;
 IF publication.id IS NULL OR (TG_TABLE_NAME='bid_review_publications' AND COALESCE((publication.coverage->>'findings')::integer,0)
 IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_findings WHERE org_id=organization AND review_id=review))
 OR EXISTS(SELECT 1 FROM public.bid_review_findings f WHERE f.org_id=organization AND f.review_id=review
 AND (target IS NULL OR f.id=target) AND (NOT EXISTS(SELECT 1 FROM public.bid_review_finding_sources s JOIN public.bid_review_obligations o ON (o.org_id,o.id)=(f.org_id,f.obligation_id) WHERE s.org_id=f.org_id AND s.finding_id=f.id AND s.kind='tender_support' AND s.page_id=o.page_id)
 OR f.tender_support_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.org_id=f.org_id AND s.finding_id=f.id AND s.kind='tender_support')
 OR f.bid_support_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.org_id=f.org_id AND s.finding_id=f.id AND s.kind='bid_support')
 OR f.search_source_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.org_id=f.org_id AND s.finding_id=f.id AND s.kind IN ('absence_page','inventory_document'))
 OR f.rule_document_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.org_id=f.org_id AND s.finding_id=f.id AND s.kind='rule_document'))) THEN
 RAISE EXCEPTION 'Finding publication is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bid_review_finding_event_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE f public.bid_review_findings%ROWTYPE; r public.bid_review_runs%ROWTYPE;
 prior public.bid_review_finding_events%ROWTYPE; classification public.bid_review_finding_events%ROWTYPE;
 member public.memberships%ROWTYPE; tm public.task_members%ROWTYPE;
BEGIN
 SELECT * INTO f FROM public.bid_review_findings WHERE org_id=NEW.org_id AND id=NEW.finding_id FOR UPDATE;
 SELECT * INTO r FROM public.bid_review_runs WHERE org_id=NEW.org_id AND id=NEW.review_id;
 SELECT * INTO member FROM public.memberships WHERE org_id=NEW.org_id AND user_id=NEW.decided_by;
 SELECT * INTO tm FROM public.task_members WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND user_id=NEW.decided_by AND active;
 IF f.id IS NULL OR r.id IS NULL OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'session'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NEW.decided_by IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
 OR NOT member.active OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=NEW.decided_by AND active)
 OR NOT EXISTS(SELECT 1 FROM public.orgs WHERE id=NEW.org_id AND active)
 OR NOT EXISTS(SELECT 1 FROM public.task_workflows WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND state='active')
 OR NOT EXISTS(SELECT 1 FROM public.bid_review_publications WHERE org_id=NEW.org_id AND review_id=r.id)
 OR NEW.expected_input_hash IS DISTINCT FROM r.input_hash
 OR NOT EXISTS(SELECT 1 FROM public.bid_submissions WHERE org_id=NEW.org_id AND id=r.submission_id AND state='uploaded')
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.org_id=NEW.org_id AND a.submission_id=r.submission_id AND a.revision>(SELECT revision FROM public.bid_outbound_authorizations WHERE org_id=NEW.org_id AND id=r.authorization_id)) THEN
 RAISE EXCEPTION 'Human decision requires current task and report authority' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM public.bid_review_finding_events WHERE org_id=NEW.org_id AND finding_id=f.id ORDER BY revision DESC LIMIT 1;
 IF NEW.revision IS DISTINCT FROM COALESCE(prior.revision,1)+1 OR NEW.prior_decision_id IS DISTINCT FROM prior.id THEN
 RAISE EXCEPTION 'Finding revision conflict' USING ERRCODE='23514'; END IF;
 IF NEW.action='classify' THEN
  IF member.role<>'admin'
  OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'bid-review\:classify')
  OR NEW.state<>COALESCE(prior.state,'open') OR NEW.state<>'open' THEN
  RAISE EXCEPTION 'Finding classification requires human task management' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT * INTO classification FROM public.bid_review_finding_events WHERE org_id=NEW.org_id AND finding_id=f.id AND action='classify' ORDER BY revision DESC LIMIT 1;
  IF classification.id IS NULL OR tm.id IS NULL OR tm.role NOT IN ('owner','contributor','reviewer')
  OR NOT (tm.review_domains ? NEW.review_domain)
  OR member.role IS DISTINCT FROM CASE NEW.review_domain WHEN 'commercial' THEN 'bidder' ELSE 'technical' END
  OR NEW.review_domain IS DISTINCT FROM classification.review_domain
  OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'bid-review\:decide') THEN
   RAISE EXCEPTION 'Finding domain requires its authorized human reviewer' USING ERRCODE='42501'; END IF;
  IF (NEW.action='reopen' AND (COALESCE(prior.state,'open')='open' OR NEW.state<>'open'))
  OR (NEW.action='dismiss' AND (COALESCE(prior.state,'open')<>'open' OR NEW.state<>'dismissed'))
  OR (NEW.action='confirm' AND (COALESCE(prior.state,'open')<>'open' OR NEW.state<>'confirmed')) THEN
   RAISE EXCEPTION 'Invalid finding disposition transition' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['bid_review_findings','bid_review_finding_sources','bid_review_finding_events'] LOOP
  EXECUTE format('CREATE TRIGGER bid_review_finding_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_immutable()',tab);
 END LOOP;
 FOREACH tab IN ARRAY ARRAY['bid_review_findings','bid_review_finding_sources'] LOOP
  EXECUTE format('CREATE TRIGGER bid_review_finding_output_guard BEFORE INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_output_guard()',tab);
  EXECUTE format('CREATE CONSTRAINT TRIGGER bid_review_findings_complete AFTER INSERT ON public.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_findings_complete()',tab);
 END LOOP;
END $$;
CREATE CONSTRAINT TRIGGER bid_review_findings_complete AFTER INSERT ON bid_review_publications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_findings_complete();
CREATE TRIGGER bid_review_finding_event_guard BEFORE INSERT ON bid_review_finding_events
 FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_event_guard();
"""
