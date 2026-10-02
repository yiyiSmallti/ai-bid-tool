"""Immutable human export decisions and narrowly bound worker candidates."""

from alembic import op

revision = "0021"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (
    "export_template_bindings",
    "export_runs",
    "export_run_items",
    "export_run_evidence",
    "export_render_candidates",
    "exports",
)


def upgrade():
    op.execute("""
CREATE TABLE export_template_bindings (
	template_revision_id UUID NOT NULL, 
	template_sha256 VARCHAR(64) NOT NULL, 
	binding_hash VARCHAR(64) NOT NULL, 
	sections JSONB NOT NULL, 
	static_content_hash VARCHAR(64) NOT NULL, 
	adapter_version VARCHAR(100) NOT NULL, 
	reviewed_by UUID NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, id, template_revision_id), 
	UNIQUE (org_id, template_revision_id, binding_hash), 
	FOREIGN KEY(org_id, template_revision_id) REFERENCES template_revisions (org_id, id), 
	FOREIGN KEY(org_id, reviewed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE export_runs (
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	document_id UUID NOT NULL, 
	draft_run_id UUID NOT NULL, 
	task_template_id UUID NOT NULL, 
	template_revision_id UUID NOT NULL, 
	binding_id UUID NOT NULL, 
	render_job_id UUID NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	manifest_hash VARCHAR(64) NOT NULL, 
	renderer_profile VARCHAR(200) NOT NULL, 
	manifest JSONB NOT NULL, 
	issue_snapshot JSONB NOT NULL, 
	acknowledged_issue_ids JSONB NOT NULL, 
	initiated_by UUID NOT NULL, 
	initiated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, render_job_id), 
	UNIQUE (org_id, id, draft_run_id), 
	UNIQUE (org_id, initiated_by, input_hash, manifest_hash), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, document_id) REFERENCES documents (org_id, id), 
	FOREIGN KEY(org_id, draft_run_id) REFERENCES draft_runs (org_id, id), 
	FOREIGN KEY(org_id, task_template_id) REFERENCES task_templates (org_id, id), 
	FOREIGN KEY(org_id, template_revision_id) REFERENCES template_revisions (org_id, id), 
	FOREIGN KEY(org_id, render_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id, initiated_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, binding_id, template_revision_id) REFERENCES export_template_bindings (org_id, id, template_revision_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE export_run_items (
	run_id UUID NOT NULL, 
	draft_run_id UUID NOT NULL, 
	response_item_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	card_revision_id UUID, 
	kind VARCHAR(20) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, run_id, requirement_id), 
	UNIQUE (org_id, run_id, ordinal), 
	UNIQUE (org_id, run_id, id, card_revision_id), 
	FOREIGN KEY(org_id, run_id, draft_run_id) REFERENCES export_runs (org_id, id, draft_run_id), 
	FOREIGN KEY(org_id, response_item_id) REFERENCES response_items (org_id, id), 
	FOREIGN KEY(org_id, requirement_id) REFERENCES requirements (org_id, id), 
	FOREIGN KEY(org_id, card_revision_id) REFERENCES response_card_revisions (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE export_run_evidence (
	run_id UUID NOT NULL, 
	run_item_id UUID NOT NULL, 
	card_revision_id UUID NOT NULL, 
	evidence_id UUID NOT NULL, 
	attachment_ordinal INTEGER, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, run_item_id, evidence_id), 
	FOREIGN KEY(org_id, run_id, run_item_id, card_revision_id) REFERENCES export_run_items (org_id, run_id, id, card_revision_id), 
	FOREIGN KEY(org_id, card_revision_id, evidence_id) REFERENCES card_evidence_links (org_id, revision_id, evidence_id), 
	FOREIGN KEY(org_id, evidence_id) REFERENCES evidence (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE export_render_candidates (
	run_id UUID NOT NULL, 
	render_job_id UUID NOT NULL, 
	attempt_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	plaintext_sha256 VARCHAR(64) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	object_key VARCHAR(500) NOT NULL, 
	renderer_profile VARCHAR(200) NOT NULL, 
	manifest_hash VARCHAR(64) NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, run_id, attempt_id), 
	UNIQUE (org_id, run_id, id), 
	FOREIGN KEY(org_id, run_id) REFERENCES export_runs (org_id, id), 
	FOREIGN KEY(org_id, render_job_id) REFERENCES jobs (org_id, id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    op.execute("""
CREATE TABLE exports (
	run_id UUID NOT NULL, 
	candidate_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	manifest_hash VARCHAR(64) NOT NULL, 
	file_sha256 VARCHAR(64) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	object_key VARCHAR(500) NOT NULL, 
	released_by UUID NOT NULL, 
	released_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, run_id), 
	FOREIGN KEY(org_id, run_id, candidate_id) REFERENCES export_render_candidates (org_id, run_id, id), 
	FOREIGN KEY(org_id, run_id) REFERENCES export_runs (org_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, released_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

""")
    _relations()
    _functions()
    _policies()
    _constraints()


def downgrade():
    raise RuntimeError("Retain immutable export history; rollback is application-only")


def _functions():
    op.execute("""
    CREATE FUNCTION export_human(p_org uuid, p_roles text[]) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT current_setting('app.actor_kind',true)='session'
        AND NULLIF(current_setting('app.actor_token_id',true),'') IS NULL
        AND p_org=NULLIF(current_setting('app.current_org',true),'')::uuid
        AND EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
          JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org
          AND m.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
          AND m.active AND u.active AND o.active AND m.role=ANY(p_roles))
    $$;
    CREATE FUNCTION export_worker(p_org uuid,p_run uuid,p_job uuid,p_user uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT current_setting('app.actor_kind',true)='worker'
        AND NULLIF(current_setting('app.actor_token_id',true),'') IS NULL
        AND p_org=NULLIF(current_setting('app.current_org',true),'')::uuid
        AND p_run=NULLIF(current_setting('app.export_run_id',true),'')::uuid
        AND p_user=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
        AND EXISTS (SELECT 1 FROM public.jobs j WHERE j.org_id=p_org AND j.id=p_job
          AND j.kind='export_render' AND j.status IN ('running','succeeded')
          AND j.run_id=NULLIF(current_setting('app.export_attempt_id',true),'')::uuid
          AND ((j.status='running' AND j.lease_until>clock_timestamp())
            OR (j.status='succeeded' AND j.xmin::text=pg_current_xact_id()::xid::text)))
        AND EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
          JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=p_org AND m.user_id=p_user
          AND m.active AND u.active AND o.active AND m.role='bidder')
    $$;
    CREATE FUNCTION export_run_access(p_org uuid,p_run uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT public.export_human(p_org,ARRAY['bidder']) OR EXISTS (
        SELECT 1 FROM public.export_runs r WHERE r.org_id=p_org AND r.id=p_run
          AND public.export_worker(r.org_id,r.id,r.render_job_id,r.initiated_by))
    $$;
    CREATE FUNCTION export_insert_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE r public.export_runs%ROWTYPE; c public.export_render_candidates%ROWTYPE;
      j public.jobs%ROWTYPE; actor uuid; xid text;
    BEGIN
      IF NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid THEN
        RAISE EXCEPTION 'Export tenant context required' USING ERRCODE='42501';
      END IF;
      IF TG_TABLE_NAME='export_template_bindings' THEN
        actor := public.response_require_human(NEW.org_id,'admin');
        IF NEW.reviewed_by IS DISTINCT FROM actor OR NOT EXISTS (
          SELECT 1 FROM public.template_revisions t WHERE t.org_id=NEW.org_id
          AND t.id=NEW.template_revision_id AND t.file->>'sha256'=NEW.template_sha256) THEN
          RAISE EXCEPTION 'Invalid template binding actor or hash' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='export_runs' THEN
        actor := public.response_require_human(NEW.org_id,'commercial');
        IF NEW.initiated_by IS DISTINCT FROM actor THEN
          RAISE EXCEPTION 'Export initiator mismatch' USING ERRCODE='42501';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.draft_runs d JOIN public.jobs x
          ON x.org_id=d.org_id AND x.id=d.extraction_job_id
          JOIN public.jobs g ON g.org_id=d.org_id AND g.id=d.generation_job_id
          JOIN public.documents doc ON doc.org_id=d.org_id AND doc.id=x.document_id
          JOIN public.task_templates t ON t.org_id=d.org_id AND t.task_id=d.task_id
          JOIN public.jobs rj ON rj.org_id=d.org_id AND rj.task_id=d.task_id
          WHERE d.org_id=NEW.org_id AND d.id=NEW.draft_run_id AND d.task_id=NEW.task_id
            AND d.extraction_job_id=NEW.extraction_job_id AND x.kind='extract'
            AND x.document_id=NEW.document_id AND doc.task_id=NEW.task_id
            AND g.status='succeeded' AND g.run_id=d.generation_run_id
            AND t.id=NEW.task_template_id AND t.template_revision_id=NEW.template_revision_id
            AND t.active AND rj.id=NEW.render_job_id AND rj.document_id=NEW.document_id
            AND rj.kind='export_render' AND rj.status='queued') THEN
          RAISE EXCEPTION 'Export input relationship invalid' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT * INTO r FROM public.export_runs WHERE org_id=NEW.org_id AND id=NEW.run_id;
        IF r.id IS NULL THEN RAISE EXCEPTION 'Export run unavailable' USING ERRCODE='42501'; END IF;
        IF TG_TABLE_NAME IN ('export_run_items','export_run_evidence') THEN
          actor := public.response_require_human(NEW.org_id,'commercial');
          SELECT xmin::text INTO xid FROM public.export_runs WHERE org_id=r.org_id AND id=r.id;
          IF xid IS DISTINCT FROM pg_current_xact_id()::xid::text OR actor IS DISTINCT FROM r.initiated_by THEN
            RAISE EXCEPTION 'Export snapshot is closed' USING ERRCODE='42501';
          END IF;
          IF TG_TABLE_NAME='export_run_items' THEN
          IF NOT EXISTS (
            SELECT 1 FROM public.response_items i WHERE i.org_id=NEW.org_id
              AND i.id=NEW.response_item_id AND i.draft_id=r.draft_run_id
              AND i.requirement_id=NEW.requirement_id AND i.kind=NEW.kind
              AND i.card_revision_id IS NOT DISTINCT FROM NEW.card_revision_id) THEN
            RAISE EXCEPTION 'Export item relationship invalid' USING ERRCODE='23514';
          END IF;
          END IF;
          IF TG_TABLE_NAME='export_run_evidence' THEN
          IF NOT EXISTS (
            SELECT 1 FROM public.export_run_items i JOIN public.evidence e
              ON e.org_id=i.org_id AND e.id=NEW.evidence_id
              JOIN public.response_card_revisions v ON v.org_id=i.org_id AND v.id=i.card_revision_id
            WHERE i.org_id=NEW.org_id AND i.id=NEW.run_item_id AND i.run_id=r.id
              AND i.kind='row' AND v.response_kind='evidence' AND e.card_id=v.card_id
              AND e.task_id=r.task_id AND e.confirmed_by IS NOT NULL AND e.confirmed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Export evidence relationship invalid' USING ERRCODE='23514';
          END IF;
          END IF;
        ELSIF TG_TABLE_NAME='export_render_candidates' THEN
          SELECT * INTO j FROM public.jobs WHERE org_id=r.org_id AND id=r.render_job_id ;
          IF public.export_worker(r.org_id,r.id,r.render_job_id,r.initiated_by) IS DISTINCT FROM true
            OR j.status<>'running' OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
            OR NEW.attempt_id IS DISTINCT FROM j.run_id THEN
            RAISE EXCEPTION 'Bound active export worker required' USING ERRCODE='42501';
          END IF;
          IF ROW(NEW.render_job_id,NEW.input_hash,NEW.manifest_hash,NEW.renderer_profile)
             IS DISTINCT FROM ROW(r.render_job_id,r.input_hash,r.manifest_hash,r.renderer_profile) THEN
            RAISE EXCEPTION 'Candidate input mismatch' USING ERRCODE='23514';
          END IF;
          IF EXISTS (SELECT 1 FROM public.export_render_candidates prior
            WHERE prior.org_id=NEW.org_id AND prior.run_id=NEW.run_id
              AND prior.plaintext_sha256<>NEW.plaintext_sha256) THEN
            RAISE EXCEPTION 'Export renderer is nondeterministic' USING ERRCODE='23514';
          END IF;
        ELSIF TG_TABLE_NAME='exports' THEN
          actor := public.response_require_human(NEW.org_id,'commercial');
          IF NEW.released_by IS DISTINCT FROM actor THEN
            RAISE EXCEPTION 'Export release actor mismatch' USING ERRCODE='42501';
          END IF;
          SELECT * INTO c FROM public.export_render_candidates WHERE org_id=NEW.org_id AND id=NEW.candidate_id;
          SELECT * INTO j FROM public.jobs WHERE org_id=r.org_id AND id=r.render_job_id ;
          IF c.id IS NULL OR c.run_id<>r.id OR j.status<>'succeeded' OR c.attempt_id IS DISTINCT FROM j.run_id
            OR ROW(NEW.task_id,NEW.mode,NEW.input_hash,NEW.manifest_hash)
              IS DISTINCT FROM ROW(r.task_id,r.mode,r.input_hash,r.manifest_hash)
            OR ROW(NEW.file_sha256,NEW.size_bytes) IS DISTINCT FROM ROW(c.plaintext_sha256,c.size_bytes) THEN
            RAISE EXCEPTION 'Export candidate is not publishable' USING ERRCODE='23514';
          END IF;
          IF EXISTS (SELECT 1 FROM public.export_render_candidates prior
            WHERE prior.org_id=r.org_id AND prior.input_hash=r.input_hash
              AND prior.renderer_profile=r.renderer_profile
              AND prior.plaintext_sha256<>c.plaintext_sha256) THEN
            RAISE EXCEPTION 'Export renderer is nondeterministic' USING ERRCODE='23514';
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)
    _completeness()
    _audit_gate()


def _policies():
    for table in TABLES:
        op.execute(f'CREATE INDEX ix_{table}_org_id ON "{table}" (org_id)')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO bid_app')
        op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON "{table}" FROM bid_app')
        if table == "export_template_bindings":
            access = """public.export_human(org_id,ARRAY['admin','bidder']) OR EXISTS
              (SELECT 1 FROM public.export_runs r WHERE r.org_id=export_template_bindings.org_id
              AND r.binding_id=export_template_bindings.id
              AND public.export_worker(r.org_id,r.id,r.render_job_id,r.initiated_by))"""
            write = "public.export_human(org_id,ARRAY['admin'])"
        elif table == "export_runs":
            access = "public.export_human(org_id,ARRAY['bidder']) OR public.export_worker(org_id,id,render_job_id,initiated_by)"
            write = "public.export_human(org_id,ARRAY['bidder'])"
        elif table == "exports":
            access = write = "public.export_human(org_id,ARRAY['bidder'])"
        else:
            access = "public.export_run_access(org_id,run_id)"
            write = (
                access
                if table == "export_render_candidates"
                else "public.export_human(org_id,ARRAY['bidder'])"
            )
        tenant = "org_id=NULLIF(current_setting('app.current_org',true),'')::uuid"
        op.execute(
            f'CREATE POLICY export_read ON "{table}" FOR SELECT USING ({tenant} AND ({access}))'
        )
        op.execute(
            f'CREATE POLICY export_insert ON "{table}" FOR INSERT WITH CHECK ({tenant} AND ({write}))'
        )
        op.execute(
            f'CREATE TRIGGER aaa_export_actor BEFORE INSERT ON "{table}" FOR EACH ROW EXECUTE FUNCTION export_insert_gate()'
        )
        op.execute(
            f'CREATE TRIGGER export_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION response_immutable_gate()'
        )


def _constraints():
    checks = {
        "export_template_bindings": [
            "template_sha256 ~ '^[0-9a-f]{64}$' AND binding_hash ~ '^[0-9a-f]{64}$' AND static_content_hash ~ '^[0-9a-f]{64}$'",
            "jsonb_typeof(sections)='array' AND jsonb_array_length(sections)=6",
        ],
        "export_runs": [
            "mode IN ('final_section','review_copy')",
            "input_hash ~ '^[0-9a-f]{64}$' AND manifest_hash ~ '^[0-9a-f]{64}$'",
            "jsonb_typeof(manifest)='object' AND jsonb_typeof(issue_snapshot)='array' AND jsonb_typeof(acknowledged_issue_ids)='array'",
        ],
        "export_run_items": ["kind IN ('row','comply_only','gap')", "ordinal>=0"],
        "export_run_evidence": ["attachment_ordinal IS NULL OR attachment_ordinal>0"],
        "export_render_candidates": [
            "input_hash ~ '^[0-9a-f]{64}$' AND manifest_hash ~ '^[0-9a-f]{64}$' AND plaintext_sha256 ~ '^[0-9a-f]{64}$'",
            "size_bytes BETWEEN 1 AND 536870912",
            "object_key='org/' || org_id::text || '/export-candidates/' || run_id::text || '/' || attempt_id::text || '/' || plaintext_sha256 || '.docx'",
        ],
        "exports": [
            "mode IN ('final_section','review_copy')",
            "input_hash ~ '^[0-9a-f]{64}$' AND manifest_hash ~ '^[0-9a-f]{64}$' AND file_sha256 ~ '^[0-9a-f]{64}$'",
            "size_bytes BETWEEN 1 AND 536870912",
            "media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'",
            "object_key='org/' || org_id::text || '/exports/' || id::text || '/' || file_sha256 || '.docx'",
        ],
    }
    for table, rules in checks.items():
        for index, rule in enumerate(rules):
            op.execute(f'ALTER TABLE "{table}" ADD CONSTRAINT {table}_check_{index} CHECK ({rule})')
    for table in ("export_runs", "export_render_candidates", "exports"):
        op.execute(
            f'CREATE CONSTRAINT TRIGGER export_complete AFTER INSERT ON "{table}" DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION export_complete_gate()'
        )


def _completeness():
    op.execute("""
    CREATE FUNCTION export_complete_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE r public.export_runs%ROWTYPE; j public.jobs%ROWTYPE; expected integer; actual integer;
    BEGIN
      IF TG_TABLE_NAME='export_runs' THEN r:=NEW;
      ELSE SELECT * INTO r FROM public.export_runs WHERE org_id=NEW.org_id AND id=NEW.run_id; END IF;
      IF r.id IS NULL THEN RAISE EXCEPTION 'Export run unavailable' USING ERRCODE='42501'; END IF;
      -- The same task/card locks are used by response review and selection writers.
      PERFORM 1 FROM public.tasks WHERE org_id=r.org_id AND id=r.task_id FOR UPDATE;
      PERFORM 1 FROM public.response_cards c WHERE c.org_id=r.org_id
        AND c.task_id=r.task_id ORDER BY c.id FOR UPDATE;
      PERFORM 1 FROM public.memberships m WHERE m.org_id=r.org_id
        AND m.user_id IN (r.initiated_by,NULLIF(current_setting('app.actor_user_id',true),'')::uuid)
        ORDER BY m.user_id FOR SHARE;
      PERFORM 1 FROM public.users u WHERE u.id IN
        (r.initiated_by,NULLIF(current_setting('app.actor_user_id',true),'')::uuid) ORDER BY u.id FOR SHARE;
      PERFORM 1 FROM public.orgs o WHERE o.id=r.org_id FOR SHARE;
      SELECT * INTO j FROM public.jobs WHERE org_id=r.org_id AND id=r.render_job_id FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM public.task_templates t WHERE t.org_id=r.org_id
        AND t.id=r.task_template_id AND t.task_id=r.task_id AND t.active
        AND t.template_revision_id=r.template_revision_id) THEN
        RAISE EXCEPTION 'Export template selection changed' USING ERRCODE='23514';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id
        JOIN public.orgs o ON o.id=m.org_id WHERE m.org_id=r.org_id AND m.user_id=r.initiated_by
          AND m.active AND u.active AND o.active AND m.role='bidder') THEN
        RAISE EXCEPTION 'Export initiator is no longer eligible' USING ERRCODE='42501';
      END IF;
      SELECT count(*) INTO expected FROM public.response_items WHERE org_id=r.org_id AND draft_id=r.draft_run_id;
      SELECT count(*) INTO actual FROM public.export_run_items WHERE org_id=r.org_id AND run_id=r.id;
      IF expected=0 OR actual<>expected OR actual>2000 OR expected<>(SELECT count(*)
        FROM public.requirements q WHERE q.org_id=r.org_id AND q.task_id=r.task_id AND q.job_id=r.extraction_job_id) OR EXISTS (
        SELECT 1 FROM public.response_items i WHERE i.org_id=r.org_id AND i.draft_id=r.draft_run_id
        AND NOT EXISTS (SELECT 1 FROM public.export_run_items e WHERE e.org_id=r.org_id
          AND e.run_id=r.id AND e.response_item_id=i.id)) THEN
        RAISE EXCEPTION 'Export must include complete draft coverage' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i
        LEFT JOIN public.response_cards c ON c.org_id=i.org_id AND c.id=i.card_id
        LEFT JOIN public.response_card_revisions v ON v.org_id=i.org_id AND v.id=i.card_revision_id
        WHERE i.org_id=r.org_id AND i.draft_id=r.draft_run_id AND (
          public.response_citation_valid(i.org_id,i.requirement_id) IS DISTINCT FROM true
          OR (i.kind='gap' AND r.mode='final_section')
          OR (i.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM i.card_revision_id)
          OR (i.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards n
              WHERE n.org_id=i.org_id AND n.requirement_id=i.requirement_id))
          OR (i.kind IN ('row','comply_only') AND
              public.response_quote_current(i.org_id,i.card_revision_id) IS DISTINCT FROM true)
          OR (i.kind='row' AND (v.state IS DISTINCT FROM 'confirmed' OR v.confirmed_by IS NULL
              OR v.confirmed_at IS NULL OR v.disposition IS DISTINCT FROM 'respond'
              OR public.response_generation_materials_active(v.org_id,v.model_job_id) IS DISTINCT FROM true))
          OR (i.kind='comply_only' AND (v.disposition IS DISTINCT FROM 'comply_only'
              OR v.disposition_by IS NULL OR v.disposition_at IS NULL)))) THEN
        RAISE EXCEPTION 'Export response or citation is stale or unconfirmed' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.export_run_items i
        JOIN public.response_card_revisions v ON v.org_id=i.org_id AND v.id=i.card_revision_id
        WHERE i.org_id=r.org_id AND i.run_id=r.id AND (
          (i.kind<>'row' AND EXISTS(SELECT 1 FROM public.export_run_evidence e
            WHERE e.org_id=i.org_id AND e.run_item_id=i.id))
          OR (i.kind='row' AND v.response_kind='commitment' AND
            EXISTS(SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=i.org_id AND l.revision_id=v.id))
          OR (i.kind='row' AND v.response_kind='evidence' AND NOT EXISTS
            (SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=i.org_id AND l.revision_id=v.id))
          OR (i.kind='row' AND EXISTS (SELECT 1 FROM public.card_evidence_links l
            WHERE l.org_id=i.org_id AND l.revision_id=v.id AND NOT EXISTS
              (SELECT 1 FROM public.export_run_evidence e WHERE e.org_id=i.org_id
               AND e.run_item_id=i.id AND e.evidence_id=l.evidence_id)))))
        OR EXISTS (SELECT 1 FROM public.export_run_evidence l JOIN public.evidence e
          ON e.org_id=l.org_id AND e.id=l.evidence_id WHERE l.org_id=r.org_id AND l.run_id=r.id
            AND (e.confirmed_by IS NULL OR e.confirmed_at IS NULL
              OR public.response_evidence_active(e.org_id,e.id) IS DISTINCT FROM true)) THEN
        RAISE EXCEPTION 'Export evidence set is incomplete or invalid' USING ERRCODE='23514';
      END IF;
      IF jsonb_typeof(r.manifest->'items') IS DISTINCT FROM 'array'
        OR jsonb_array_length(r.manifest->'items')<>actual
        OR EXISTS (SELECT 1 FROM public.export_run_items e WHERE e.org_id=r.org_id AND e.run_id=r.id
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
            WHERE item->>'response_item_id'=e.response_item_id::text
              AND item->>'requirement_id'=e.requirement_id::text
              AND item->>'card_revision_id' IS NOT DISTINCT FROM e.card_revision_id::text
              AND item->>'kind'=e.kind AND (item->>'ordinal')::integer=e.ordinal)) THEN
        RAISE EXCEPTION 'Export manifest differs from relational snapshot' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
        JOIN public.response_items i ON i.org_id=r.org_id AND i.id=(item->>'response_item_id')::uuid
        LEFT JOIN public.response_card_revisions v ON v.org_id=i.org_id AND v.id=i.card_revision_id
        WHERE item->'source' IS DISTINCT FROM i.source
          OR item->>'card_id' IS DISTINCT FROM i.card_id::text
          OR item->>'category' IS DISTINCT FROM i.category
          OR (item->>'starred')::boolean IS DISTINCT FROM i.starred
          OR item->>'location_label' IS DISTINCT FROM i.location_label
          OR jsonb_typeof(item->'evidence') IS DISTINCT FROM 'array'
          OR (i.kind='row' AND (
            ROW(item->>'response_kind',item->>'response_text',item->>'deviation',item->>'deviation_note',item->>'table')
              IS DISTINCT FROM ROW(i.response_kind,i.response_text,i.deviation,i.deviation_note,i."table")
            OR item->>'confirmed_by' IS DISTINCT FROM v.confirmed_by::text
            OR (item->>'confirmed_at')::timestamptz IS DISTINCT FROM v.confirmed_at
            OR item->>'disposition_by' IS DISTINCT FROM v.disposition_by::text
            OR (item->>'disposition_at')::timestamptz IS DISTINCT FROM v.disposition_at))
          OR (i.kind='comply_only' AND (item->>'disposition_by' IS DISTINCT FROM i.disposition_by::text
            OR (item->>'disposition_at')::timestamptz IS DISTINCT FROM i.disposition_at))
          OR (i.kind='gap' AND item->'gap_reasons' IS DISTINCT FROM i.gap_reasons)
          OR (i.kind<>'row' AND (jsonb_array_length(item->'evidence')<>0
            OR item->>'response_text' IS NOT NULL OR item->>'response_kind' IS NOT NULL))) THEN
        RAISE EXCEPTION 'Export manifest content differs from confirmed snapshot' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
        JOIN public.export_run_items i ON i.org_id=r.org_id AND i.run_id=r.id
          AND i.response_item_id=(item->>'response_item_id')::uuid
        WHERE jsonb_array_length(item->'evidence')<>(SELECT count(*) FROM public.export_run_evidence l
          WHERE l.org_id=i.org_id AND l.run_item_id=i.id)
        OR EXISTS (SELECT 1 FROM public.export_run_evidence l
          JOIN public.evidence e ON e.org_id=l.org_id AND e.id=l.evidence_id
          WHERE l.org_id=i.org_id AND l.run_item_id=i.id AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(item->'evidence') material
            WHERE material->>'id'=e.id::text AND material->>'confirmed_by'=e.confirmed_by::text
              AND (material->>'confirmed_at')::timestamptz=e.confirmed_at
              AND material->'input'->>'kind'=e.kind AND material->'input'->>'quote'=e.quote
              AND material->>'material_kind'=e.material_kind
              AND material->>'quote_check'=e.quote_check
              AND material->>'selection_id'=COALESCE(e.task_resource_id,e.task_feature_id,
                e.task_certificate_id,e.task_org_profile_id)::text
              AND material->>'resource_revision_id'=COALESCE(e.product_revision_id,e.feature_revision_id,
                e.certificate_revision_id,e.profile_revision_id)::text
              AND (material->>'attachment_ordinal')::integer IS NOT DISTINCT FROM l.attachment_ordinal
              AND CASE WHEN e.kind='certificate_pdf_page' THEN EXISTS (
                SELECT 1 FROM public.evidence_sources src JOIN public.certificate_files original
                  ON original.org_id=src.org_id AND original.id=src.certificate_file_id
                WHERE src.org_id=e.org_id AND src.id=e.evidence_source_id
                  AND src.task_id=r.task_id AND src.task_certificate_id=e.task_certificate_id
                  AND src.certificate_revision_id=e.certificate_revision_id
                  AND src.page=e.page AND src.preview->>'sha256'=e.source_sha256
                  AND material->'input'->>'evidence_source_id'=src.id::text
                  AND material->'source_archive'->>'id'=src.id::text
                  AND material->'source_archive'->>'certificate_file_id'=original.id::text
                  AND material->'source_archive'->>'certificate_revision_id'=src.certificate_revision_id::text
                  AND (material->'source_archive'->>'page')::integer=src.page
                  AND material->'source_archive'->'original'->>'sha256'=original.file->>'sha256'
                  AND material->'source_archive'->'original'->>'size_bytes'=original.file->>'size_bytes'
                  AND material->'source_archive'->'preview'->>'sha256'=src.preview->>'sha256'
                  AND material->'source_archive'->'preview'->>'size_bytes'=src.preview->>'size_bytes'
                  AND l.attachment_ordinal IS NOT NULL
                  AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'attachments') attachment
                    WHERE (attachment->>'ordinal')::integer=l.attachment_ordinal
                      AND attachment->>'evidence_source_id'=src.id::text
                      AND attachment->>'selection_id'=src.task_certificate_id::text
                      AND attachment->>'revision_id'=src.certificate_revision_id::text
                      AND attachment->>'original_sha256'=original.file->>'sha256'
                      AND attachment->>'png_sha256'=src.preview->>'sha256'
                      AND (attachment->>'page')::integer=src.page))
                ELSE material->'input'->>'field_path'=e.field_path
                  AND material->'input'->>'selection_id'=material->>'selection_id'
                  AND l.attachment_ordinal IS NULL END))) THEN
        RAISE EXCEPTION 'Export manifest evidence differs from confirmed links' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r.issue_snapshot) i WHERE i->>'severity'='block')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(r.issue_snapshot) i
          WHERE i->>'severity'='acknowledge' AND NOT (r.acknowledged_issue_ids ? (i->>'issue_id')))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.acknowledged_issue_ids) a
          WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.issue_snapshot) i
            WHERE i->>'severity'='acknowledge' AND i->>'issue_id'=a))
        OR (SELECT count(*) FROM jsonb_array_elements_text(r.acknowledged_issue_ids)) <>
          (SELECT count(DISTINCT a) FROM jsonb_array_elements_text(r.acknowledged_issue_ids) a) THEN
        RAISE EXCEPTION 'Export warning decisions are incomplete' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME='export_render_candidates' THEN
        IF j.status NOT IN ('running','succeeded') OR NEW.attempt_id IS DISTINCT FROM j.run_id
          OR public.export_worker(r.org_id,r.id,r.render_job_id,r.initiated_by) IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'Export attempt is no longer current' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='exports' THEN
        IF public.export_human(r.org_id,ARRAY['bidder']) IS DISTINCT FROM true
          OR j.status<>'succeeded' OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c
            WHERE c.org_id=r.org_id AND c.id=NEW.candidate_id AND c.attempt_id=j.run_id) THEN
          RAISE EXCEPTION 'Export publication requires current successful candidate' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;
    """)


def _relations():
    # Redundant parent keys make same-org cross-task substitutions impossible at
    # the foreign-key layer as well as in the actor/completeness gates.
    unique_keys = {
        "jobs": ("org_id,id,task_id,document_id",),
        "draft_runs": ("org_id,id,task_id,extraction_job_id",),
        "task_templates": ("org_id,id,task_id,template_revision_id",),
        "response_items": ("org_id,id,draft_id,requirement_id,kind",),
        "export_runs": (
            "org_id,id,render_job_id,input_hash,manifest_hash,renderer_profile",
            "org_id,id,task_id,mode,input_hash,manifest_hash",
        ),
        "export_render_candidates": ("org_id,run_id,id,plaintext_sha256,size_bytes",),
    }
    for table, keys in unique_keys.items():
        for index, key in enumerate(keys):
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT export_{table}_parent_{index} UNIQUE ({key})"
            )
    relations = (
        (
            "export_runs",
            "org_id,extraction_job_id,task_id,document_id",
            "jobs",
            "org_id,id,task_id,document_id",
        ),
        (
            "export_runs",
            "org_id,render_job_id,task_id,document_id",
            "jobs",
            "org_id,id,task_id,document_id",
        ),
        (
            "export_runs",
            "org_id,draft_run_id,task_id,extraction_job_id",
            "draft_runs",
            "org_id,id,task_id,extraction_job_id",
        ),
        (
            "export_runs",
            "org_id,task_template_id,task_id,template_revision_id",
            "task_templates",
            "org_id,id,task_id,template_revision_id",
        ),
        (
            "export_run_items",
            "org_id,response_item_id,draft_run_id,requirement_id,kind",
            "response_items",
            "org_id,id,draft_id,requirement_id,kind",
        ),
        (
            "export_render_candidates",
            "org_id,run_id,render_job_id,input_hash,manifest_hash,renderer_profile",
            "export_runs",
            "org_id,id,render_job_id,input_hash,manifest_hash,renderer_profile",
        ),
        (
            "exports",
            "org_id,run_id,task_id,mode,input_hash,manifest_hash",
            "export_runs",
            "org_id,id,task_id,mode,input_hash,manifest_hash",
        ),
        (
            "exports",
            "org_id,run_id,candidate_id,file_sha256,size_bytes",
            "export_render_candidates",
            "org_id,run_id,id,plaintext_sha256,size_bytes",
        ),
    )
    for index, (table, columns, parent, targets) in enumerate(relations):
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT export_relation_{index} FOREIGN KEY ({columns}) REFERENCES {parent} ({targets})"
        )


def _audit_gate():
    op.execute("""
    CREATE FUNCTION export_audit_gate() RETURNS trigger
    LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
    DECLARE j public.jobs%ROWTYPE;
    BEGIN
      IF NEW.action NOT LIKE 'export.%' THEN RETURN NEW; END IF;
      IF NEW.org_id IS DISTINCT FROM NULLIF(current_setting('app.current_org',true),'')::uuid
        OR NEW.actor_user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
        OR NEW.actor_token_id IS NOT NULL
        OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
        OR NEW.details->>'actor_kind' IS DISTINCT FROM current_setting('app.actor_kind',true) THEN
        RAISE EXCEPTION 'Export audit actor context mismatch' USING ERRCODE='42501';
      END IF;
      IF NEW.action IN ('export.render_completed','export.render_failed') THEN
        SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id
          AND id=(NEW.details->>'render_job_id')::uuid;
        IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
          OR j.id IS NULL OR j.kind<>'export_render'
          OR j.run_id IS DISTINCT FROM (NEW.details->>'attempt_id')::uuid
          OR NEW.object_id IS DISTINCT FROM (NEW.details->>'run_id')::uuid
          OR NEW.details->>'run_id' IS DISTINCT FROM current_setting('app.export_run_id',true)
          OR NEW.details->>'attempt_id' IS DISTINCT FROM current_setting('app.export_attempt_id',true)
          OR j.result->'submission'->>'export_run_id' IS DISTINCT FROM NEW.object_id::text
          OR j.result->'submission'->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id::text THEN
          RAISE EXCEPTION 'Export worker audit is not bound to this attempt' USING ERRCODE='42501';
        END IF;
        IF NEW.action='export.render_completed' AND
          (public.export_worker(NEW.org_id,NEW.object_id,j.id,NEW.actor_user_id) IS DISTINCT FROM true
            OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c WHERE c.org_id=NEW.org_id
              AND c.run_id=NEW.object_id AND c.attempt_id=j.run_id)) THEN
          RAISE EXCEPTION 'Export completion audit requires a current candidate' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action='export.binding_created' THEN
        PERFORM public.response_require_human(NEW.org_id,'admin');
        IF NOT EXISTS (SELECT 1 FROM public.export_template_bindings b WHERE b.org_id=NEW.org_id
          AND b.id=NEW.object_id AND b.reviewed_by=NEW.actor_user_id) THEN
          RAISE EXCEPTION 'Export binding audit has no binding' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action IN ('export.prepared','export.released','export.render_cancelled',
        'export.download_link_issued','export.download_served') THEN
        PERFORM public.response_require_human(NEW.org_id,'commercial');
        IF NEW.action IN ('export.prepared','export.render_cancelled') THEN
          IF NOT EXISTS (SELECT 1 FROM public.export_runs r WHERE r.org_id=NEW.org_id AND r.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export run audit has no run' USING ERRCODE='23514';
          END IF;
        ELSE
          IF NOT EXISTS (SELECT 1 FROM public.exports e WHERE e.org_id=NEW.org_id AND e.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export file audit has no release' USING ERRCODE='23514';
          END IF;
        END IF;
      ELSE RAISE EXCEPTION 'Unknown export audit event' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER export_audit_actor BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION export_audit_gate();
    CREATE UNIQUE INDEX export_release_audit_once ON audit_logs(org_id,object_id) WHERE action='export.released';
    """)
