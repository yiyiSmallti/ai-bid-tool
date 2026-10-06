"""Immutable cloud annotation lineage, human attestation and current release gates."""

from alembic import op
from sqlalchemy import text

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(PARENT_SQL)
    op.execute(TABLE_SQL)
    op.execute(SECURITY_SQL)
    _screenshots()
    op.execute(GUARD_SQL)
    _consumption()
    _invalidation()
    # The co-sign snapshot hashes whole rows with to_jsonb, which renders timestamptz
    # in the session TimeZone; pin it so stored and recomputed snapshots agree across
    # connections. No review rounds existed when this was introduced.
    op.execute(
        "ALTER FUNCTION public.team_cosign_snapshot(uuid,uuid,uuid) SET \"TimeZone\" TO 'UTC'"
    )


def downgrade():
    raise RuntimeError("Retain annotation, privacy and approval history; repair forward")


PARENT_SQL = r"""
ALTER TABLE card_review_signatures ADD CONSTRAINT annotation_signature_scope UNIQUE(org_id,task_id,card_id,round_id,id);
ALTER TABLE response_cards ADD CONSTRAINT annotation_card_scope UNIQUE(org_id,id,task_id,extraction_job_id,requirement_id);
ALTER TABLE evidence_sources ADD CONSTRAINT annotation_certificate_source_scope UNIQUE(org_id,id,task_id,task_certificate_id,certificate_id,certificate_revision_id,certificate_file_id);
ALTER TABLE screenshot_renditions ADD CONSTRAINT annotation_rendition_scope UNIQUE(org_id,task_id,extraction_job_id,asset_id,id);
ALTER TABLE api_tokens ADD CONSTRAINT token_forbidden_annotation_scope CHECK(NOT(scopes ? 'evidence:annotate'));
ALTER TABLE jobs ADD CONSTRAINT annotation_job_kind CHECK(kind NOT IN ('annotation_render','annotation_release') OR (task_id IS NOT NULL AND document_id IS NOT NULL AND actor_user_id IS NOT NULL AND actor_kind='session' AND actor_token_id IS NULL));
"""

TABLE_SQL = r"""

CREATE TABLE annotation_requests (
	expected_card_revision INTEGER NOT NULL, 
	expected_card_revision_id UUID NOT NULL, 
	actor_user_id UUID NOT NULL, 
	request_id UUID NOT NULL, 
	request_sha256 VARCHAR(64) NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	plan_sha256 VARCHAR(64) NOT NULL, 
	reviewed_source_sha256 VARCHAR(64) NOT NULL, 
	manifest JSONB NOT NULL, 
	plan JSONB NOT NULL, 
	renderer JSONB NOT NULL, 
	privacy_attestation JSONB NOT NULL, 
	source_kind VARCHAR(30) NOT NULL, 
	evidence_source_id UUID, 
	task_certificate_id UUID, 
	certificate_id UUID, 
	certificate_revision_id UUID, 
	certificate_file_id UUID, 
	asset_id UUID, 
	parent_rendition_id UUID, 
	task_resource_id UUID, 
	product_revision_id UUID, 
	vendor_archive_id UUID, 
	inherited_privacy_review_id UUID, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, card_id, task_id, extraction_job_id, requirement_id) REFERENCES response_cards (org_id, id, task_id, extraction_job_id, requirement_id), 
	UNIQUE (org_id, task_id, actor_user_id, request_id), 
	UNIQUE (org_id, job_id), 
	UNIQUE (org_id, task_id, extraction_job_id, requirement_id, card_id, id), 
	FOREIGN KEY(org_id, actor_user_id) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, card_id, expected_card_revision_id, expected_card_revision) REFERENCES response_card_revisions (org_id, card_id, id, revision), 
	FOREIGN KEY(org_id, evidence_source_id, task_id, task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) REFERENCES evidence_sources (org_id, id, task_id, task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id), 
	FOREIGN KEY(org_id, task_id, extraction_job_id, asset_id, parent_rendition_id) REFERENCES screenshot_renditions (org_id, task_id, extraction_job_id, asset_id, id), 
	FOREIGN KEY(org_id, task_resource_id, task_id, product_revision_id) REFERENCES task_resources (org_id, id, task_id, product_revision_id), 
	FOREIGN KEY(org_id, task_id, vendor_archive_id) REFERENCES screenshot_vendor_archives (org_id, task_id, id), 
	FOREIGN KEY(org_id, asset_id, inherited_privacy_review_id) REFERENCES screenshot_privacy_reviews (org_id, asset_id, id), 
	CHECK (expected_card_revision > 0), 
	CHECK (request_sha256 ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' AND plan_sha256 ~ '^[0-9a-f]{64}$' AND reviewed_source_sha256 ~ '^[0-9a-f]{64}$'), 
	CHECK ((source_kind='certificate_page' AND evidence_source_id IS NOT NULL AND task_certificate_id IS NOT NULL AND certificate_id IS NOT NULL AND certificate_revision_id IS NOT NULL AND certificate_file_id IS NOT NULL AND asset_id IS NULL AND parent_rendition_id IS NULL AND task_resource_id IS NULL AND product_revision_id IS NULL AND vendor_archive_id IS NULL AND inherited_privacy_review_id IS NULL) OR (source_kind='vendor_rendition' AND evidence_source_id IS NULL AND task_certificate_id IS NULL AND certificate_id IS NULL AND certificate_revision_id IS NULL AND certificate_file_id IS NULL AND asset_id IS NOT NULL AND parent_rendition_id IS NOT NULL AND task_resource_id IS NOT NULL AND product_revision_id IS NOT NULL AND vendor_archive_id IS NOT NULL AND inherited_privacy_review_id IS NOT NULL)), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE annotation_materials (
	request_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	rendition_id UUID NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	manifest JSONB NOT NULL, 
	rendering JSONB NOT NULL, 
	content_pixel_sha256 VARCHAR(64) NOT NULL, 
	root_mapping_sha256 VARCHAR(64) NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, card_id, task_id, extraction_job_id, requirement_id) REFERENCES response_cards (org_id, id, task_id, extraction_job_id, requirement_id), 
	UNIQUE (org_id, request_id), 
	UNIQUE (org_id, rendition_id), 
	UNIQUE (org_id, task_id, extraction_job_id, id), 
	FOREIGN KEY(org_id, task_id, extraction_job_id, requirement_id, card_id, request_id) REFERENCES annotation_requests (org_id, task_id, extraction_job_id, requirement_id, card_id, id), 
	FOREIGN KEY(org_id, task_id, extraction_job_id, asset_id, rendition_id) REFERENCES screenshot_renditions (org_id, task_id, extraction_job_id, asset_id, id), 
	CHECK (input_hash ~ '^[0-9a-f]{64}$' AND content_pixel_sha256 ~ '^[0-9a-f]{64}$' AND root_mapping_sha256 ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;

CREATE TABLE annotation_releases (
	annotation_id UUID NOT NULL, 
	evidence_id UUID NOT NULL, 
	card_revision_id UUID NOT NULL, 
	confirmed_by UUID NOT NULL, 
	review_round_id UUID, 
	commercial_signature_id UUID, 
	technical_signature_id UUID, 
	requirement_review_id UUID NOT NULL, 
	requirement_review_revision INTEGER NOT NULL, 
	run_id UUID NOT NULL, 
	asset_id UUID NOT NULL, 
	rendition_id UUID NOT NULL, 
	approval_sha256 VARCHAR(64) NOT NULL, 
	approval JSONB NOT NULL, 
	renderer JSONB NOT NULL, 
	renderer_identity VARCHAR(64) NOT NULL, 
	rendering JSONB NOT NULL, 
	content_pixel_sha256 VARCHAR(64) NOT NULL, 
	root_mapping_sha256 VARCHAR(64) NOT NULL, 
	task_id UUID NOT NULL, 
	extraction_job_id UUID NOT NULL, 
	requirement_id UUID NOT NULL, 
	card_id UUID NOT NULL, 
	job_id UUID NOT NULL, 
	org_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (org_id, id), 
	UNIQUE (org_id, task_id, id), 
	FOREIGN KEY(org_id, task_id) REFERENCES tasks (org_id, id), 
	FOREIGN KEY(org_id, extraction_job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, job_id, task_id) REFERENCES jobs (org_id, id, task_id), 
	FOREIGN KEY(org_id, requirement_id, task_id, extraction_job_id) REFERENCES requirements (org_id, id, task_id, job_id), 
	FOREIGN KEY(org_id, card_id, task_id, extraction_job_id, requirement_id) REFERENCES response_cards (org_id, id, task_id, extraction_job_id, requirement_id), 
	UNIQUE (org_id, rendition_id), 
	UNIQUE (org_id, evidence_id, card_revision_id, approval_sha256, renderer_identity), 
	FOREIGN KEY(org_id, task_id, extraction_job_id, annotation_id) REFERENCES annotation_materials (org_id, task_id, extraction_job_id, id), 
	FOREIGN KEY(org_id, card_id, evidence_id) REFERENCES evidence (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_id, card_revision_id) REFERENCES response_card_revisions (org_id, card_id, id), 
	FOREIGN KEY(org_id, card_revision_id, evidence_id) REFERENCES card_evidence_links (org_id, revision_id, evidence_id), 
	FOREIGN KEY(org_id, requirement_review_id, task_id, extraction_job_id, requirement_id) REFERENCES requirement_reviews (org_id, id, task_id, extraction_job_id, requirement_id), 
	FOREIGN KEY(org_id, task_id, extraction_job_id, asset_id, rendition_id) REFERENCES screenshot_renditions (org_id, task_id, extraction_job_id, asset_id, id), 
	FOREIGN KEY(org_id, confirmed_by) REFERENCES memberships (org_id, user_id), 
	FOREIGN KEY(org_id, task_id, card_id, review_round_id) REFERENCES card_review_rounds (org_id, task_id, card_id, id), 
	FOREIGN KEY(org_id, task_id, card_id, review_round_id, commercial_signature_id) REFERENCES card_review_signatures (org_id, task_id, card_id, round_id, id), 
	FOREIGN KEY(org_id, task_id, card_id, review_round_id, technical_signature_id) REFERENCES card_review_signatures (org_id, task_id, card_id, round_id, id), 
	CHECK (review_round_id IS NOT NULL OR (commercial_signature_id IS NULL AND technical_signature_id IS NULL)), 
	CHECK (requirement_review_revision > 0), 
	CHECK (approval_sha256 ~ '^[0-9a-f]{64}$' AND renderer_identity ~ '^[0-9a-f]{64}$' AND content_pixel_sha256 ~ '^[0-9a-f]{64}$' AND root_mapping_sha256 ~ '^[0-9a-f]{64}$'), 
	FOREIGN KEY(org_id) REFERENCES orgs (id)
)

;
"""

SECURITY_SQL = r"""
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['annotation_requests','annotation_materials','annotation_releases'] LOOP
  EXECUTE format('CREATE INDEX %I ON public.%I(org_id,task_id,created_at,id)',tab||'_listing',tab);
  EXECUTE format('CREATE INDEX %I ON public.%I(org_id)','ix_'||tab||'_org_id',tab);
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY tenant_scope ON public.%I USING (org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid) WITH CHECK (org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid)',tab);
  EXECUTE format('REVOKE ALL ON public.%I FROM bid_app',tab);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO bid_app',tab);
  -- PostgreSQL requires an UPDATE privilege to acquire SELECT FOR UPDATE locks.
  -- The immutable trigger rejects every actual UPDATE, including id=id.
  EXECUTE format('GRANT UPDATE(id) ON public.%I TO bid_app',tab);
  EXECUTE format('CREATE TRIGGER annotation_immutable BEFORE UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate()',tab);
  EXECUTE format('CREATE TRIGGER annotation_task_lock BEFORE INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.annotation_task_lock()',tab);
  EXECUTE format('CREATE TRIGGER annotation_binding_guard AFTER INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.annotation_binding_guard()',tab);
 END LOOP;
END $$;
CREATE INDEX annotation_releases_material_history ON public.annotation_releases(org_id,annotation_id,created_at,id);
"""
# Function must exist before its triggers; its body is installed with the other
# guards before application connections can observe the committed migration.
SECURITY_SQL = (
    """CREATE FUNCTION public.annotation_binding_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN RETURN NEW; END $$;
CREATE FUNCTION public.annotation_task_lock() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF pg_catalog.row_security_active(TG_RELID)
  AND (NEW.org_id=nullif(current_setting('app.current_org',true),'')::uuid) IS NOT TRUE THEN RETURN NEW; END IF;
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 RETURN NEW;
END $$;
"""
    + SECURITY_SQL
)


def _patch_function(name, changes):
    definition = (
        op.get_bind()
        .execute(text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": name})
        .scalar_one()
    )
    for old, new in changes:
        if definition.count(old) != 1:
            raise RuntimeError(f"Annotation migration expected one {name} fragment: {old[:80]}")
        definition = definition.replace(old, new)
    op.execute(definition)


def _screenshots():
    op.execute(r"""
ALTER TABLE screenshot_renditions ADD COLUMN renderer_identity jsonb, ADD COLUMN provenance_sha256 varchar(64);
ALTER TABLE screenshot_privacy_reviews ADD COLUMN annotation_request_id uuid;
ALTER TABLE screenshot_privacy_reviews ADD CONSTRAINT annotation_privacy_request FOREIGN KEY(org_id,task_id,annotation_request_id) REFERENCES annotation_requests(org_id,task_id,id);
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='screenshot_renditions'::regclass AND
  ((contype='c' AND pg_get_constraintdef(oid) LIKE '%profile%' AND pg_get_constraintdef(oid) LIKE '%prototype-clean-v1%')
   OR (contype='u' AND cardinality(conkey)=5 AND pg_get_constraintdef(oid) LIKE '%parent_rendition_id%')) LOOP
  EXECUTE format('ALTER TABLE screenshot_renditions DROP CONSTRAINT %I',c.conname);
 END LOOP;
END $$;
ALTER TABLE screenshot_renditions ADD CONSTRAINT annotation_profiles CHECK(profile IN ('screenshot-markup-v1','prototype-clean-v1','annotation-candidate-v1','annotation-release-v1'));
ALTER TABLE screenshot_renditions ADD CONSTRAINT annotation_profile_identity CHECK(profile NOT IN ('annotation-candidate-v1','annotation-release-v1') OR (renderer_identity IS NOT NULL AND provenance_sha256 IS NOT NULL AND provenance_sha256 ~ '^[0-9a-f]{64}$'));
CREATE UNIQUE INDEX screenshot_rendition_legacy_identity ON screenshot_renditions(org_id,asset_id,parent_rendition_id,plan_sha256,profile) WHERE profile IN ('screenshot-markup-v1','prototype-clean-v1');
CREATE UNIQUE INDEX screenshot_rendition_annotation_identity ON screenshot_renditions(org_id,asset_id,parent_rendition_id,plan_sha256,profile,renderer_identity,provenance_sha256) WHERE profile IN ('annotation-candidate-v1','annotation-release-v1');
""")
    _patch_function(
        "public.screenshot_asset_gate()",
        [
            (
                "BEGIN\n",
                """BEGIN
  IF NEW.source ? 'annotation_request_id' THEN
    PERFORM public.annotation_certificate_asset(NEW.org_id,NEW.id,NEW.task_id,NEW.extraction_job_id,
      NEW.evidence_source_id,NEW.received_by,NEW.source_sha256,(NEW.source->>'annotation_request_id')::uuid);
    IF NEW.origin<>'certificate' OR NEW.source_kind<>'certificate_page' OR NEW.image_kind<>'certificate_page'
      OR NEW.source_hash_assurance<>'server_verified' OR NEW.task_feature_id IS NOT NULL OR NEW.feature_revision_id IS NOT NULL
      OR NEW.task_resource_id IS NOT NULL OR NEW.product_revision_id IS NOT NULL OR NEW.vendor_archive_id IS NOT NULL OR NEW.prototype_run_id IS NOT NULL
      OR NEW.source->>'kind' IS DISTINCT FROM 'certificate_page'
      OR NEW.source->>'evidence_source_id' IS DISTINCT FROM NEW.evidence_source_id::text
      OR NOT EXISTS(SELECT 1 FROM public.evidence_sources src WHERE src.org_id=NEW.org_id AND src.id=NEW.evidence_source_id
        AND NEW.source_width=(src.preview->>'width_px')::integer AND NEW.source_height=(src.preview->>'height_px')::integer) THEN
      RAISE EXCEPTION 'Invalid cloud certificate asset' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
""",
            )
        ],
    )
    _patch_function(
        "public.screenshot_privacy_gate()",
        [
            (
                "BEGIN\n",
                """BEGIN
  IF NEW.annotation_request_id IS NOT NULL THEN
    PERFORM public.annotation_certificate_privacy(NEW.org_id,NEW.task_id,NEW.asset_id,NEW.rendition_id,
      NEW.id,NEW.annotation_request_id,NEW.reviewed_by,NEW.reviewed_upload_sha256,NEW.stored_image_sha256,NEW.rule_version);
    RETURN NEW;
  END IF;
""",
            )
        ],
    )
    _patch_function(
        "public.screenshot_rendition_gate()",
        [
            (
                "BEGIN\n",
                """BEGIN
  IF NEW.profile IN ('annotation-candidate-v1','annotation-release-v1') THEN
    PERFORM public.annotation_rendition_valid(NEW.org_id,NEW.id);
    RETURN NEW;
  END IF;
  IF EXISTS(SELECT 1 FROM public.screenshot_renditions r WHERE r.org_id=NEW.org_id AND r.id=NEW.parent_rendition_id
    AND r.profile IN ('annotation-candidate-v1','annotation-release-v1')) THEN
    RAISE EXCEPTION 'Legacy annotation cannot derive B05 profiles' USING ERRCODE='23514'; END IF;
""",
            )
        ],
    )
    # Parent checks execute only after real immediate RI constraints, including
    # new cloud attestation parents. The cyclic review link remains deferred.
    for table in ("screenshot_assets", "screenshot_renditions", "screenshot_privacy_reviews"):
        function = {
            "screenshot_assets": "screenshot_asset_gate",
            "screenshot_renditions": "screenshot_rendition_gate",
            "screenshot_privacy_reviews": "screenshot_privacy_gate",
        }[table]
        op.execute(f"DROP TRIGGER screenshot_shape ON {table}")
        op.execute(
            f"CREATE TRIGGER screenshot_shape AFTER INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        op.execute(f"DROP TRIGGER screenshot_scope ON {table}")
        op.execute(
            f"CREATE TRIGGER screenshot_scope AFTER INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION screenshot_scope_gate()"
        )


GUARD_SQL = r"""
CREATE FUNCTION public.annotation_ascii(p_value text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text:=''; point integer; pos integer;
BEGIN
 FOR pos IN 1..char_length(p_value) LOOP
  point:=ascii(substr(p_value,pos,1));
  IF point<127 THEN result:=result||substr(p_value,pos,1);
  ELSIF point<=65535 THEN result:=result||chr(92)||'u'||lpad(to_hex(point),4,'0');
  ELSE point:=point-65536; result:=result||chr(92)||'u'||lpad(to_hex(55296+(point/1024)),4,'0')||chr(92)||'u'||lpad(to_hex(56320+(point%1024)),4,'0');
  END IF;
 END LOOP;
 RETURN result;
END $$;
CREATE FUNCTION public.annotation_digest(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(public.annotation_ascii(public.requirement_canonical(p_value)),'UTF8')),'hex')
$$;
CREATE FUNCTION public.annotation_actor_live(p_org uuid,p_task uuid,p_actor uuid) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.task_members tm JOIN public.memberships m ON (m.org_id,m.user_id)=(tm.org_id,tm.user_id)
 JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
 JOIN public.task_workflows w ON (w.org_id,w.task_id)=(tm.org_id,tm.task_id)
 WHERE tm.org_id=p_org AND tm.task_id=p_task AND tm.user_id=p_actor AND tm.active AND m.active AND u.active AND o.active
 AND m.role IN ('admin','bidder','technical') AND tm.role IN ('owner','contributor') AND w.state='active')
$$;
CREATE FUNCTION public.annotation_source_current(p_org uuid,p_request uuid) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT q.source_kind='certificate_page' AND s.active
 AND q.manifest#>>'{requirement,review_hash}'=public.requirement_hash(q.org_id,q.requirement_id,public.requirement_current_pin(q.org_id,q.requirement_id))
 AND q.manifest#>>'{requirement,source_binding_sha256}' IS NOT DISTINCT FROM public.requirement_current_pin(q.org_id,q.requirement_id)->>'binding_sha256'
 AND e.preview->>'sha256'=q.reviewed_source_sha256 AND e.preview=q.manifest#>'{source,archive,preview}'
 AND e.preview-'name'=q.manifest#>'{source,source_png}'
 AND f.file=q.manifest#>'{source,archive,original}'
 AND f.file->>'sha256'=q.manifest#>>'{source,original_sha256}'
 AND e.page::text=q.manifest#>>'{source,archive,page}'
 AND e.dpi::text=q.manifest#>>'{source,archive,dpi}'
 AND e.render_profile=q.manifest#>>'{source,archive,render_profile}'
 AND e.rendered_at=(q.manifest#>>'{source,archive,rendered_at}')::timestamptz
 AND e.rendered_at=(q.manifest#>>'{source,source_time}')::timestamptz
 AND q.manifest#>>'{source,source_time_kind}'='server_page_rendered_at'
 AND q.manifest#>>'{source,selection_id}'=e.task_certificate_id::text
 AND q.manifest#>>'{source,resource_revision_id}'=e.certificate_revision_id::text
 AND q.manifest#>'{source,authorized_content}'=jsonb_build_object('x',0,'y',0,'width',(e.preview->>'width_px')::integer,'height',(e.preview->>'height_px')::integer)
 AND e.status='unconfirmed_source' AND e.confirmed_by IS NULL
 AND e.certificate_file_id=q.certificate_file_id AND e.certificate_revision_id=q.certificate_revision_id
 FROM public.annotation_requests q JOIN public.evidence_sources e ON (e.org_id,e.id)=(q.org_id,q.evidence_source_id)
 JOIN public.certificate_files f ON (f.org_id,f.id)=(e.org_id,e.certificate_file_id)
 JOIN public.task_certificates s ON (s.org_id,s.id)=(e.org_id,e.task_certificate_id)
 WHERE q.org_id=p_org AND q.id=p_request),false)
$$;
CREATE FUNCTION public.annotation_job_live(p_org uuid,p_job uuid,p_run uuid,p_kind text) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT j.kind=p_kind AND j.status='running' AND j.run_id=p_run AND j.lease_until>clock_timestamp()
 AND j.actor_kind='session' AND j.actor_token_id IS NULL
 AND current_setting('app.actor_kind',true)='worker'
 AND j.id=nullif(current_setting('app.execution_job_id',true),'')::uuid
 AND j.run_id=nullif(current_setting('app.execution_run_id',true),'')::uuid
 AND j.actor_user_id=nullif(current_setting('app.actor_user_id',true),'')::uuid
 FROM public.jobs j WHERE j.org_id=p_org AND j.id=p_job),false)
$$;
CREATE OR REPLACE FUNCTION public.annotation_binding_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE q public.annotation_requests; m public.annotation_materials; r public.screenshot_renditions;
 j public.jobs; c public.response_cards; state text;
BEGIN
 -- All tenant and immediate composite RI constraints precede this AFTER guard.
 PERFORM 1 FROM public.tasks WHERE org_id=NEW.org_id AND id=NEW.task_id FOR UPDATE;
 SELECT w.state INTO state FROM public.task_workflows w WHERE w.org_id=NEW.org_id AND w.task_id=NEW.task_id;
 IF state IS DISTINCT FROM 'active' THEN RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 SELECT * INTO c FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id FOR UPDATE;
 SELECT * INTO j FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.job_id;
 IF j.document_id IS DISTINCT FROM (SELECT document_id FROM public.jobs WHERE org_id=NEW.org_id AND id=NEW.extraction_job_id) THEN
  RAISE EXCEPTION 'Annotation job must retain the tender document' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='annotation_requests' THEN
  PERFORM public.task_write_authority(NEW.org_id,NEW.task_id,'evidence:annotate',NULL,false,true);
  IF NEW.actor_user_id IS DISTINCT FROM nullif(current_setting('app.actor_user_id',true),'')::uuid
    OR NOT public.annotation_actor_live(NEW.org_id,NEW.task_id,NEW.actor_user_id) THEN
   RAISE EXCEPTION 'Human annotation authority required' USING ERRCODE='42501'; END IF;
  IF NEW.source_kind<>'certificate_page' THEN RAISE EXCEPTION 'vendor_source_not_enabled' USING ERRCODE='23514'; END IF;
  IF NEW.input_hash IS DISTINCT FROM public.annotation_digest(NEW.manifest)
    OR NEW.plan_sha256 IS DISTINCT FROM public.annotation_digest(NEW.plan)
    OR NEW.privacy_attestation IS DISTINCT FROM jsonb_build_object('reviewed_source_sha256',NEW.reviewed_source_sha256,'reviewed_by',NEW.actor_user_id::text)
    OR NEW.manifest->>'policy_version' IS DISTINCT FROM 'annotation-binding-v1'
    OR NEW.manifest#>>'{target,source,kind}' IS DISTINCT FROM NEW.source_kind
    OR NEW.manifest#>>'{target,source,evidence_source_id}' IS DISTINCT FROM NEW.evidence_source_id::text
    OR NEW.manifest#>>'{source,kind}' IS DISTINCT FROM NEW.source_kind
    OR NEW.manifest#>>'{source,page_kind}' IS DISTINCT FROM 'pdf_page'
    OR NEW.manifest#>>'{source,archive,org_id}' IS DISTINCT FROM NEW.org_id::text
    OR NEW.manifest#>>'{source,archive,task_id}' IS DISTINCT FROM NEW.task_id::text
    OR NEW.manifest#>>'{source,archive,created_by}' IS DISTINCT FROM (SELECT created_by::text FROM public.evidence_sources WHERE org_id=NEW.org_id AND id=NEW.evidence_source_id)
    OR NEW.manifest->>'card_content_sha256' IS DISTINCT FROM (SELECT public.annotation_digest(jsonb_build_object(
      'response_kind',v.response_kind,'response_text',v.response_text,'deviation',v.deviation,'deviation_note',v.deviation_note,
      'review_domain',v.review_domain,'disposition',v.disposition,'quote_sha256',v.quote_sha256))
      FROM public.response_card_revisions v WHERE v.org_id=NEW.org_id AND v.id=NEW.expected_card_revision_id)
    OR NEW.manifest->>'org_id' IS DISTINCT FROM NEW.org_id::text OR NEW.manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
    OR NEW.manifest#>>'{target,card_id}' IS DISTINCT FROM NEW.card_id::text
    OR NEW.manifest#>>'{target,extraction_job_id}' IS DISTINCT FROM NEW.extraction_job_id::text
    OR NEW.manifest#>>'{requirement,requirement_id}' IS DISTINCT FROM NEW.requirement_id::text
    OR NEW.manifest#>>'{target,expected_card_revision}' IS DISTINCT FROM NEW.expected_card_revision::text
    OR NEW.manifest#>>'{source,archive,id}' IS DISTINCT FROM NEW.evidence_source_id::text
    OR NEW.manifest#>>'{source,archive,task_certificate_id}' IS DISTINCT FROM NEW.task_certificate_id::text
    OR NEW.manifest#>>'{source,archive,certificate_id}' IS DISTINCT FROM NEW.certificate_id::text
    OR NEW.manifest#>>'{source,archive,certificate_revision_id}' IS DISTINCT FROM NEW.certificate_revision_id::text
    OR NEW.manifest#>>'{source,archive,certificate_file_id}' IS DISTINCT FROM NEW.certificate_file_id::text
    OR NEW.manifest->'renderer' IS DISTINCT FROM NEW.renderer
    OR NEW.manifest#>'{target,plan}' IS DISTINCT FROM NEW.plan
    OR NEW.manifest->>'plan_sha256' IS DISTINCT FROM NEW.plan_sha256
    OR NEW.renderer->>'profile' IS DISTINCT FROM 'annotation-candidate-v1'
    OR NEW.plan - ARRAY['crop','boxes'] <> '{}'::jsonb OR NOT (NEW.plan ?& ARRAY['crop','boxes'])
    OR c.current_revision_id IS DISTINCT FROM NEW.expected_card_revision_id OR c.revision IS DISTINCT FROM NEW.expected_card_revision
    OR j.kind<>'annotation_render' OR j.actor_user_id IS DISTINCT FROM NEW.actor_user_id OR j.status<>'queued'
    OR j.result#>>'{submission,annotation_request_id}' IS DISTINCT FROM NEW.id::text
    OR j.result#>'{submission,input_manifest}' IS DISTINCT FROM NEW.manifest
    OR j.result#>>'{submission,input_hash}' IS DISTINCT FROM NEW.input_hash
    OR NOT public.annotation_source_current(NEW.org_id,NEW.id) THEN
   RAISE EXCEPTION 'Annotation request binding mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='annotation_materials' THEN
  SELECT * INTO q FROM public.annotation_requests WHERE org_id=NEW.org_id AND id=NEW.request_id;
  SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.rendition_id;
  IF NOT public.annotation_job_live(NEW.org_id,NEW.job_id,NEW.run_id,'annotation_render')
    OR NOT public.annotation_actor_live(NEW.org_id,NEW.task_id,q.actor_user_id)
    OR NOT public.annotation_source_current(NEW.org_id,q.id)
    OR NOT(j.actor_scopes ? 'evidence:annotate')
    OR q.job_id<>NEW.job_id OR q.input_hash<>NEW.input_hash OR q.manifest<>NEW.manifest
    OR c.current_revision_id<>q.expected_card_revision_id OR c.revision<>q.expected_card_revision
    OR r.profile<>'annotation-candidate-v1' OR r.generation_job_id IS DISTINCT FROM NEW.job_id
    OR r.actor_user_id<>q.actor_user_id OR r.renderer_identity<>q.renderer OR r.plan_sha256<>q.plan_sha256
    OR NEW.rendering->'renderer' IS DISTINCT FROM q.renderer
    OR NEW.rendering->'image' IS DISTINCT FROM r.image
    OR NEW.rendering#>'{canvas,mapping}' IS DISTINCT FROM r.mapping
    OR NEW.rendering#>>'{canvas,width_px}' IS DISTINCT FROM r.image->>'width_px'
    OR NEW.rendering#>>'{canvas,height_px}' IS DISTINCT FROM r.image->>'height_px'
    OR NEW.rendering->>'plan_sha256' IS DISTINCT FROM r.plan_sha256
    OR NEW.rendering->>'content_pixel_sha256' IS DISTINCT FROM NEW.content_pixel_sha256
    OR NEW.rendering->>'root_mapping_sha256' IS DISTINCT FROM NEW.root_mapping_sha256
    OR NEW.rendering->>'provenance_sha256' IS DISTINCT FROM r.provenance_sha256 THEN
   RAISE EXCEPTION 'Annotation candidate publication mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO m FROM public.annotation_materials WHERE org_id=NEW.org_id AND id=NEW.annotation_id;
  SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.rendition_id;
  IF NOT public.annotation_job_live(NEW.org_id,NEW.job_id,NEW.run_id,'annotation_release')
    OR NOT public.annotation_release_current(NEW.org_id,NEW.id)
    OR NEW.content_pixel_sha256<>m.content_pixel_sha256 OR NEW.root_mapping_sha256<>m.root_mapping_sha256
    OR r.profile<>'annotation-release-v1' OR r.generation_job_id IS DISTINCT FROM NEW.job_id
    OR r.parent_rendition_id IS DISTINCT FROM m.rendition_id OR NEW.asset_id<>m.asset_id
    OR r.renderer_identity IS DISTINCT FROM NEW.renderer
    OR NEW.rendering->>'plan_sha256' IS DISTINCT FROM r.plan_sha256
    OR NEW.rendering->'renderer' IS DISTINCT FROM NEW.renderer
    OR NEW.rendering->'image' IS DISTINCT FROM r.image
    OR NEW.rendering#>'{canvas,mapping}' IS DISTINCT FROM r.mapping
    OR NEW.rendering#>>'{canvas,width_px}' IS DISTINCT FROM r.image->>'width_px'
    OR NEW.rendering#>>'{canvas,height_px}' IS DISTINCT FROM r.image->>'height_px'
    OR NEW.rendering->>'content_pixel_sha256' IS DISTINCT FROM NEW.content_pixel_sha256
    OR NEW.rendering->>'root_mapping_sha256' IS DISTINCT FROM NEW.root_mapping_sha256
    OR NEW.rendering->>'provenance_sha256' IS DISTINCT FROM r.provenance_sha256
    OR NEW.approval IS DISTINCT FROM j.result#>'{submission,approval}'
    OR NEW.renderer IS DISTINCT FROM j.result#>'{submission,renderer}' THEN
   RAISE EXCEPTION 'Annotation release publication mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.annotation_certificate_asset(p_org uuid,p_asset uuid,p_task uuid,p_extraction uuid,p_source uuid,p_actor uuid,p_hash text,p_request uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE q public.annotation_requests; j public.jobs; e public.evidence_sources;
BEGIN
 SELECT * INTO q FROM public.annotation_requests WHERE org_id=p_org AND id=p_request;
 SELECT * INTO j FROM public.jobs WHERE org_id=p_org AND id=q.job_id;
 IF q.id IS NULL OR q.task_id<>p_task OR q.extraction_job_id<>p_extraction OR q.evidence_source_id<>p_source
 OR q.actor_user_id<>p_actor OR q.reviewed_source_sha256<>p_hash
 OR NOT public.annotation_job_live(p_org,j.id,j.run_id,'annotation_render')
 OR NOT public.annotation_actor_live(p_org,p_task,p_actor) OR NOT public.annotation_source_current(p_org,q.id) THEN
  RAISE EXCEPTION 'Cloud certificate requires exact human source attestation' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.annotation_certificate_privacy(p_org uuid,p_task uuid,p_asset uuid,p_rendition uuid,p_review uuid,p_request uuid,p_actor uuid,p_upload text,p_image text,p_rule text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE q public.annotation_requests; r public.screenshot_renditions; a public.screenshot_assets;
BEGIN
 SELECT * INTO q FROM public.annotation_requests WHERE org_id=p_org AND id=p_request;
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=p_org AND id=p_rendition;
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=p_org AND id=p_asset;
 PERFORM public.annotation_certificate_asset(p_org,p_asset,p_task,r.extraction_job_id,a.evidence_source_id,p_actor,p_upload,p_request);
 IF r.id IS NULL OR r.asset_id<>p_asset OR r.privacy_review_id<>p_review OR r.parent_rendition_id IS NOT NULL
  OR r.image_sha256<>p_image OR r.upload_sha256<>p_upload OR r.profile<>'annotation-candidate-v1'
  OR p_rule<>'annotation-certificate-privacy-v1' OR a.source->>'annotation_request_id' IS DISTINCT FROM p_request::text THEN
  RAISE EXCEPTION 'Cloud privacy lineage mismatch' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION public.annotation_rendition_valid(p_org uuid,p_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r public.screenshot_renditions; a public.screenshot_assets; p public.screenshot_renditions;
 j public.jobs; q public.annotation_requests; crop jsonb; box jsonb; w integer; h integer; cw integer; ch integer; ox integer; fh integer;
BEGIN
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=p_org AND id=p_id;
 SELECT * INTO a FROM public.screenshot_assets WHERE org_id=p_org AND id=r.asset_id;
 SELECT * INTO j FROM public.jobs WHERE org_id=p_org AND id=r.generation_job_id;
 IF NOT public.annotation_job_live(p_org,j.id,j.run_id,CASE r.profile WHEN 'annotation-candidate-v1' THEN 'annotation_render' ELSE 'annotation_release' END)
 OR a.origin NOT IN ('certificate','vendor') OR r.source_sha256<>a.source_sha256 OR r.actor_user_id<>j.actor_user_id
 OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE org_id=p_org AND asset_id=a.id)
 OR r.renderer_identity->>'profile' IS DISTINCT FROM r.profile
 OR r.renderer_identity->>'protocol_version' IS DISTINCT FROM 'annotation-render-v1'
 OR r.storage_key IS DISTINCT FROM 'org/'||p_org::text||'/screenshots/'||a.id::text||'/'||r.id::text||'/'||r.image_sha256||'.png' THEN
  RAISE EXCEPTION 'Invalid cloud rendition source' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-candidate-v1' THEN
  SELECT * INTO q FROM public.annotation_requests WHERE org_id=p_org AND job_id=j.id;
  IF q.id IS NULL OR NOT public.annotation_source_current(p_org,q.id) OR q.renderer<>r.renderer_identity
   OR q.plan<>r.plan OR q.plan_sha256<>r.plan_sha256 OR q.reviewed_source_sha256<>r.upload_sha256
   OR a.evidence_source_id IS DISTINCT FROM q.evidence_source_id OR r.parent_rendition_id IS NOT NULL THEN
   RAISE EXCEPTION 'Candidate does not match submitted input' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO p FROM public.screenshot_renditions WHERE org_id=p_org AND id=r.parent_rendition_id;
  IF p.id IS NULL OR p.profile<>'annotation-candidate-v1' OR p.asset_id<>r.asset_id
   OR p.privacy_review_id<>r.privacy_review_id OR p.upload_sha256<>r.upload_sha256
   OR p.plan<>r.plan OR p.plan_sha256<>r.plan_sha256
   OR p.mapping-'footer_height' IS DISTINCT FROM r.mapping-'footer_height' THEN
   RAISE EXCEPTION 'Release changes approved image content mapping' USING ERRCODE='23514'; END IF;
 END IF;
 w:=(r.image->>'width_px')::integer; h:=(r.image->>'height_px')::integer;
 cw:=(r.mapping->>'content_width')::integer; ch:=(r.mapping->>'content_height')::integer;
 ox:=(r.mapping->>'content_offset_x')::integer; fh:=(r.mapping->>'footer_height')::integer; crop:=r.mapping->'crop';
 IF (r.image->>'sha256'=r.image_sha256 AND r.image->>'media_type'='image/png'
  AND (r.image->>'size_bytes')::integer BETWEEN 1 AND 41943040 AND w BETWEEN 1 AND 8192 AND h BETWEEN 1 AND 8192
  AND w::bigint*h<=20000000 AND cw>0 AND ch>0 AND fh>0 AND h=ch+fh AND w=greatest(cw,1024)
  AND ox=(w-cw)/2 AND (r.mapping->>'content_offset_y')::integer=0
  AND (crop->>'width')::integer=cw AND (crop->>'height')::integer=ch
  AND (crop->>'x')::integer>=0 AND (crop->>'y')::integer>=0
  AND (crop->>'x')::integer+cw<=a.source_width AND (crop->>'y')::integer+ch<=a.source_height
  AND ((r.plan->'crop'<>'null'::jsonb AND r.plan->'crop'=crop)
    OR (r.plan->'crop'='null'::jsonb AND crop=jsonb_build_object('x',0,'y',0,'width',a.source_width,'height',a.source_height)))
  AND r.plan ?& ARRAY['crop','boxes'] AND r.plan-ARRAY['crop','boxes']='{}'::jsonb
  AND jsonb_typeof(r.plan->'boxes')='array' AND jsonb_array_length(r.plan->'boxes')<=20) IS NOT TRUE THEN
  RAISE EXCEPTION 'Invalid bounded annotation descriptor' USING ERRCODE='23514'; END IF;
 FOR box IN SELECT value FROM jsonb_array_elements(r.plan->'boxes') LOOP
  IF ((box->>'x')::integer >= (crop->>'x')::integer AND (box->>'y')::integer >= (crop->>'y')::integer
   AND (box->>'width')::integer>0 AND (box->>'height')::integer>0
   AND (box->>'x')::integer+(box->>'width')::integer <= (crop->>'x')::integer+cw
   AND (box->>'y')::integer+(box->>'height')::integer <= (crop->>'y')::integer+ch) IS NOT TRUE THEN
   RAISE EXCEPTION 'Invalid annotation box' USING ERRCODE='23514'; END IF;
 END LOOP;
END $$;
"""

GUARD_SQL += r"""
CREATE FUNCTION public.annotation_release_current(p_org uuid,p_release uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE r public.annotation_releases; m public.annotation_materials; candidate public.screenshot_renditions;
 e public.evidence; c public.response_cards; v public.response_card_revisions; b public.requirement_reviews;
 rr public.card_review_rounds; s public.card_review_signatures; pin jsonb; co jsonb; count_signatures integer;
BEGIN
 SELECT * INTO r FROM public.annotation_releases WHERE org_id=p_org AND id=p_release;
 IF r.id IS NULL THEN RETURN false; END IF;
 SELECT * INTO m FROM public.annotation_materials WHERE org_id=p_org AND id=r.annotation_id;
 SELECT * INTO candidate FROM public.screenshot_renditions WHERE org_id=p_org AND id=m.rendition_id;
 SELECT * INTO e FROM public.evidence WHERE org_id=p_org AND id=r.evidence_id;
 SELECT * INTO c FROM public.response_cards WHERE org_id=p_org AND id=r.card_id;
 SELECT * INTO v FROM public.response_card_revisions WHERE org_id=p_org AND id=r.card_revision_id;
 SELECT * INTO b FROM public.requirement_reviews WHERE org_id=p_org AND id=r.requirement_review_id;
 IF m.id IS NULL OR e.id IS NULL OR c.id IS NULL OR v.id IS NULL OR b.id IS NULL
  OR NOT public.annotation_source_current(p_org,m.request_id)
  OR NOT public.requirement_review_current(p_org,r.requirement_id)
  OR b.state<>'confirmed' OR b.revision<>r.requirement_review_revision
  OR e.kind IS DISTINCT FROM 'image_region' OR r.confirmed_by IS DISTINCT FROM v.confirmed_by
  OR c.current_revision_id<>v.id OR e.confirmed_by IS NULL OR e.confirmed_at IS NULL
  OR v.state<>'confirmed' OR v.confirmed_by IS NULL
  OR e.screenshot_asset_id IS DISTINCT FROM m.asset_id OR e.screenshot_rendition_id IS DISTINCT FROM m.rendition_id OR e.image_sha256 IS DISTINCT FROM candidate.image_sha256
  OR r.content_pixel_sha256<>m.content_pixel_sha256 OR r.root_mapping_sha256<>m.root_mapping_sha256
  OR r.approval->>'approval_binding_sha256' IS DISTINCT FROM r.approval_sha256
  OR r.approval->>'evidence_id' IS DISTINCT FROM e.id::text OR r.approval->>'card_id' IS DISTINCT FROM c.id::text
  OR r.approval->>'card_revision_id' IS DISTINCT FROM v.id::text OR r.approval->>'card_revision' IS DISTINCT FROM v.revision::text
  OR r.approval->>'confirmed_by' IS DISTINCT FROM v.confirmed_by::text
  OR (r.approval->>'confirmed_at')::timestamptz IS DISTINCT FROM v.confirmed_at
  OR r.approval->>'candidate_annotation_id' IS DISTINCT FROM m.id::text
  OR r.approval->>'candidate_rendition_id' IS DISTINCT FROM m.rendition_id::text
  OR r.approval->>'candidate_image_sha256' IS DISTINCT FROM candidate.image_sha256
  OR r.approval->>'candidate_plan_sha256' IS DISTINCT FROM candidate.plan_sha256
  OR r.approval->'candidate_content_mapping' IS DISTINCT FROM candidate.mapping
  OR r.approval->>'content_pixel_sha256' IS DISTINCT FROM m.content_pixel_sha256
  OR r.approval->>'root_mapping_sha256' IS DISTINCT FROM m.root_mapping_sha256
  OR r.approval->'reviewed_region' IS DISTINCT FROM e.region
  OR r.approval#>>'{requirement,requirement_id}' IS DISTINCT FROM b.requirement_id::text
  OR r.approval#>>'{requirement,review_revision}' IS DISTINCT FROM b.revision::text
  OR r.approval#>>'{requirement,review_hash}' IS DISTINCT FROM b.review_hash
  OR r.approval#>>'{requirement,state}' IS DISTINCT FROM 'confirmed'
  OR r.approval#>>'{requirement,source_binding_sha256}' IS NULL
  OR r.approval#>>'{requirement,source_binding_sha256}' IS DISTINCT FROM b.source_pin->>'binding_sha256'
  OR r.approval->>'review_domain' IS DISTINCT FROM v.review_domain
  OR r.approval_sha256 IS DISTINCT FROM public.annotation_digest(r.approval-'approval_binding_sha256')
  OR r.renderer_identity IS DISTINCT FROM public.annotation_digest(r.renderer)
  OR r.approval->>'card_content_sha256' IS DISTINCT FROM public.annotation_digest(jsonb_build_object(
    'response_kind',v.response_kind,'response_text',v.response_text,'deviation',v.deviation,'deviation_note',v.deviation_note,
    'review_domain',v.review_domain,'disposition',v.disposition,'quote_sha256',v.quote_sha256))
  OR r.approval->>'evidence_binding_sha256' IS DISTINCT FROM public.annotation_digest(jsonb_build_object(
    'id',e.id,'asset_id',e.screenshot_asset_id,'rendition_id',e.screenshot_rendition_id,'image_sha256',e.image_sha256,
    'region',e.region,'claim_scope',e.claim_scope,'visual_observation',e.visual_observation,'source_sha256',e.source_sha256))
  OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE org_id=p_org AND asset_id=m.asset_id)
  OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE org_id=p_org AND revision_id=v.id AND evidence_id=e.id)
 THEN RETURN false; END IF;
 co:=r.approval->'co_sign';
 SELECT round.* INTO rr FROM public.requirement_workflows w JOIN public.card_review_rounds round
 ON (round.org_id,round.id)=(w.org_id,w.current_round_id) WHERE w.org_id=p_org AND w.requirement_id=r.requirement_id;
 IF rr.id IS NULL THEN
  RETURN r.review_round_id IS NULL AND r.commercial_signature_id IS NULL AND r.technical_signature_id IS NULL
   AND public.team_cosign_signer_authorized(p_org,r.task_id,v.confirmed_by,r.approval->>'review_domain')
   AND r.approval->>'decision_kind'='single_domain'
   AND (co IS NULL OR co='null'::jsonb OR (co->>'round_id' IS NULL AND co->>'status'='not_required'))
   AND public.team_cosign_card_approved(p_org,c.id);
 END IF;
 IF r.review_round_id IS DISTINCT FROM rr.id OR NOT public.team_cosign_round_valid(p_org,rr.id) OR NOT public.team_cosign_card_approved(p_org,c.id)
  OR rr.purpose<>'response' OR co->>'status' IS DISTINCT FROM 'complete'
  OR co->>'round_id' IS DISTINCT FROM rr.id::text OR co->>'round_revision' IS DISTINCT FROM rr.round_revision::text
  OR co->>'purpose' IS DISTINCT FROM rr.purpose OR co->'required_domains' IS DISTINCT FROM rr.required_domains
  OR co->>'policy_revision' IS DISTINCT FROM rr.policy_revision::text
  OR co->>'task_rule_revision' IS DISTINCT FROM rr.task_rule_revision::text
  OR co->>'evidence_sha256' IS DISTINCT FROM rr.evidence_sha256 OR co->>'requirement_sha256' IS DISTINCT FROM rr.requirement_sha256
  OR co->>'citation_sha256' IS DISTINCT FROM rr.citation_sha256 OR co->>'content_sha256' IS DISTINCT FROM rr.content_sha256
  OR (r.commercial_signature_id IS NOT NULL) IS DISTINCT FROM EXISTS(SELECT 1 FROM public.card_review_signatures sig WHERE sig.org_id=p_org AND sig.round_id=rr.id AND sig.domain='commercial')
  OR (r.technical_signature_id IS NOT NULL) IS DISTINCT FROM EXISTS(SELECT 1 FROM public.card_review_signatures sig WHERE sig.org_id=p_org AND sig.round_id=rr.id AND sig.domain='technical')
  OR jsonb_typeof(co->'signatures') IS DISTINCT FROM 'array'
  OR NOT EXISTS(SELECT 1 FROM public.card_review_signatures final_signer WHERE final_signer.org_id=p_org AND final_signer.round_id=rr.id AND final_signer.signer_user_id=v.confirmed_by)
 THEN RETURN false; END IF;
 SELECT count(*) INTO count_signatures FROM public.card_review_signatures WHERE org_id=p_org AND round_id=rr.id;
 IF count_signatures<>jsonb_array_length(co->'signatures') OR count_signatures<>jsonb_array_length(rr.required_domains) THEN RETURN false; END IF;
 FOR s IN SELECT * FROM public.card_review_signatures WHERE org_id=p_org AND round_id=rr.id LOOP
  IF s.id IS DISTINCT FROM (CASE s.domain WHEN 'commercial' THEN r.commercial_signature_id WHEN 'technical' THEN r.technical_signature_id ELSE NULL END) THEN RETURN false; END IF;
  IF (SELECT count(*) FROM jsonb_array_elements(co->'signatures') x WHERE x->>'id'=s.id::text AND x->>'domain'=s.domain
   AND x->>'signer_user_id'=s.signer_user_id::text AND x->>'request_sha256'=s.request_sha256
   AND x->>'reason_sha256' IS NOT DISTINCT FROM s.reason_sha256)<>1 THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
CREATE FUNCTION public.annotation_evidence_gate() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r public.screenshot_renditions; m public.annotation_materials;
BEGIN
 IF NEW.kind<>'image_region' THEN RETURN NEW; END IF;
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.screenshot_rendition_id;
 IF r.profile='annotation-release-v1' THEN RAISE EXCEPTION 'Release cannot become new evidence' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-candidate-v1' THEN
  SELECT * INTO m FROM public.annotation_materials WHERE org_id=NEW.org_id AND rendition_id=r.id;
  IF m.id IS NULL OR m.task_id<>NEW.task_id OR NOT public.annotation_source_current(NEW.org_id,m.request_id)
    OR (NEW.confirmed_by IS NOT NULL AND NOT public.requirement_review_current(NEW.org_id,(SELECT requirement_id FROM public.response_cards WHERE org_id=NEW.org_id AND id=NEW.card_id))) THEN
   RAISE EXCEPTION 'Annotation source or requirement is stale' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER annotation_evidence_binding AFTER INSERT OR UPDATE ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.annotation_evidence_gate();
CREATE FUNCTION public.annotation_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM public.append_task_event(NEW.org_id,NEW.task_id,'board_changed',jsonb_build_object('type','board_changed',
  'invalidate_all',false,'extraction_job_id',NEW.extraction_job_id,'requirement_ids',jsonb_build_array(NEW.requirement_id),
  'card_ids',jsonb_build_array(NEW.card_id)),NEW.extraction_job_id);
 RETURN NULL;
END $$;
CREATE TRIGGER zz_annotation_event AFTER INSERT ON annotation_materials FOR EACH ROW EXECUTE FUNCTION annotation_event();
CREATE TRIGGER zz_annotation_event AFTER INSERT ON annotation_releases FOR EACH ROW EXECUTE FUNCTION annotation_event();
"""


GUARD_SQL += r"""
CREATE FUNCTION public.annotation_publication_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r public.screenshot_renditions;
BEGIN
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=NEW.org_id AND id=NEW.id;
 IF r.profile='annotation-candidate-v1' AND NOT EXISTS(
  SELECT 1 FROM public.annotation_materials m JOIN public.annotation_requests q ON (q.org_id,q.id)=(m.org_id,m.request_id)
  JOIN public.screenshot_privacy_reviews p ON (p.org_id,p.id)=(r.org_id,r.privacy_review_id)
  WHERE m.org_id=r.org_id AND m.rendition_id=r.id AND m.job_id=r.generation_job_id
   AND p.annotation_request_id=q.id AND p.asset_id=r.asset_id AND p.rendition_id=r.id
   AND p.stored_image_sha256=r.image_sha256 AND p.reviewed_upload_sha256=q.reviewed_source_sha256
 ) THEN RAISE EXCEPTION 'Incomplete annotation candidate publication' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-release-v1' AND NOT EXISTS(
  SELECT 1 FROM public.annotation_releases rel WHERE rel.org_id=r.org_id AND rel.rendition_id=r.id AND rel.job_id=r.generation_job_id
 ) THEN RAISE EXCEPTION 'Incomplete annotation release publication' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER zz_annotation_publication_complete AFTER INSERT ON public.screenshot_renditions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.annotation_publication_complete();
"""


def _consumption():
    # Extend the live source predicate used by review rounds, drafts and exports;
    # no mutable current flag is written onto historical candidate/release rows.
    definition = (
        op.get_bind()
        .execute(
            text(
                "SELECT pg_get_functiondef('public.response_evidence_active(uuid,uuid)'::regprocedure)"
            )
        )
        .scalar_one()
    )
    renamed = definition.replace(
        "FUNCTION public.response_evidence_active(",
        "FUNCTION public.annotation_prior_evidence_active(",
        1,
    )
    if renamed == definition:
        raise RuntimeError("Expected qualified response evidence function")
    op.execute(renamed)
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.response_evidence_active(p_org uuid,p_evidence uuid) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT public.annotation_prior_evidence_active(p_org,p_evidence) AND NOT EXISTS(
  SELECT 1 FROM public.evidence e JOIN public.screenshot_renditions r ON (r.org_id,r.id)=(e.org_id,e.screenshot_rendition_id)
  LEFT JOIN public.annotation_materials m ON (m.org_id,m.rendition_id)=(r.org_id,r.id)
  WHERE e.org_id=p_org AND e.id=p_evidence AND (r.profile='annotation-release-v1'
    OR (r.profile='annotation-candidate-v1' AND (m.id IS NULL OR NOT public.annotation_source_current(p_org,m.request_id)))))
$$;
CREATE FUNCTION public.annotation_export_binding(p_org uuid,p_evidence uuid,p_revision uuid,p_attachment jsonb) RETURNS boolean
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
DECLARE e public.evidence; m public.annotation_materials; rel public.annotation_releases; r public.screenshot_renditions;
BEGIN
 SELECT * INTO e FROM public.evidence WHERE org_id=p_org AND id=p_evidence;
 SELECT * INTO m FROM public.annotation_materials WHERE org_id=p_org AND rendition_id=e.screenshot_rendition_id;
 IF m.id IS NULL THEN RETURN NOT(p_attachment ? 'annotation_release_id'); END IF;
 SELECT * INTO rel FROM public.annotation_releases WHERE org_id=p_org AND id=(p_attachment->>'annotation_release_id')::uuid;
 SELECT * INTO r FROM public.screenshot_renditions WHERE org_id=p_org AND id=rel.rendition_id;
 RETURN coalesce(rel.id IS NOT NULL AND rel.annotation_id=m.id AND rel.evidence_id=e.id AND rel.card_revision_id=p_revision
  AND public.annotation_release_current(p_org,rel.id)
  AND p_attachment->>'annotation_approval_sha256'=rel.approval_sha256
  AND p_attachment->>'annotation_release_rendition_id'=r.id::text AND p_attachment->>'annotation_release_image_sha256'=r.image_sha256
  AND p_attachment->>'annotation_content_pixel_sha256'=m.content_pixel_sha256
  AND rel.content_pixel_sha256=m.content_pixel_sha256 AND rel.root_mapping_sha256=m.root_mapping_sha256,false);
END $$;
""")
    _patch_function(
        "public.export_complete_gate()",
        [
            (
                "AND attachment->>'png_sha256'=e.image_sha256)",
                "AND attachment->>'png_sha256'=e.image_sha256 AND public.annotation_export_binding(r.org_id,e.id,i.card_revision_id,attachment))",
            )
        ],
    )
    # Trigger helpers use INVOKER and no public execution; callers receive only
    # the predicates required by existing review/export gates and worker checks.
    op.execute(r"""
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace
 AND proname LIKE 'annotation_%' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO bid_app',f.signature);
 END LOOP;
END $$;
""")


def _invalidation():
    # Preserve every earlier system audit exception and add only this derived
    # append-only notification; the origin guard below proves its live binding.
    old = (
        op.get_bind()
        .execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.audit_logs'::regclass AND conname='audit_system_cosign_invalidation_actor'"
            )
        )
        .scalar_one()
    )
    if not old.startswith("CHECK (") or not old.endswith(")"):
        raise RuntimeError("Unexpected system audit actor constraint")
    op.execute(
        "ALTER TABLE public.audit_logs DROP CONSTRAINT audit_system_cosign_invalidation_actor"
    )
    op.execute(
        "ALTER TABLE public.audit_logs ADD CONSTRAINT audit_system_cosign_invalidation_actor CHECK (("
        + old[7:-1]
        + ") OR coalesce(action='annotation.invalidate' AND actor_kind='system' AND details->>'actor_kind'='system' AND details->>'annotation_id' IS NOT NULL,false))"
    )
    op.execute(INVALIDATION_SQL)
    _patch_function(
        "public.agent_origin_guard()",
        [
            (
                " IF TG_TABLE_NAME='audit_logs' THEN\n",
                """ IF TG_TABLE_NAME='audit_logs' THEN
  IF NEW.action='annotation.invalidate' THEN
   IF pg_trigger_depth()<2 OR NOT public.annotation_invalidation_audit_valid(NEW.org_id,NEW.object_id,NEW.details) THEN
    RAISE EXCEPTION 'Annotation invalidation audit requires a stale immutable binding' USING ERRCODE='42501'; END IF;
   NEW.actor_user_id:=NULL; NEW.actor_token_id:=NULL; NEW.actor_kind:='system'; NEW.initiated_by:='legacy_unknown';
   NEW.on_behalf_of_user_id:=NULL; NEW.agent_principal_id:=NULL; NEW.agent_session_id:=NULL;
   NEW.agent_step_id:=NULL; NEW.job_id:=NULL; NEW.run_id:=NULL; NEW.invocation_id:=NULL; NEW.command:=NULL;
   RETURN NEW;
  END IF;
""",
            )
        ],
    )
    _patch_function(
        "public.team_cosign_invalidate_dependencies()",
        [
            (
                " IF can_derive THEN PERFORM set_config('app.current_org',coalesce(old_org,''),true); END IF;\n RETURN NULL;",
                " PERFORM public.annotation_invalidate_dependencies(changed);\n IF can_derive THEN PERFORM set_config('app.current_org',coalesce(old_org,''),true); END IF;\n RETURN NULL;",
            )
        ],
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.annotation_invalidation_audit_valid(uuid,uuid,jsonb),public.annotation_invalidate_dependencies(jsonb),public.annotation_dependency_event() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.annotation_invalidation_audit_valid(uuid,uuid,jsonb),public.annotation_invalidate_dependencies(jsonb) TO bid_app"
    )


INVALIDATION_SQL = r"""
CREATE UNIQUE INDEX annotation_invalidation_once ON public.audit_logs(org_id,object_id) WHERE action='annotation.invalidate';
CREATE FUNCTION public.annotation_invalidation_audit_valid(p_org uuid,p_object uuid,p_details jsonb) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.annotation_materials m WHERE m.org_id=p_org AND m.id=p_object
  AND NOT public.annotation_source_current(p_org,m.request_id)
  AND p_details=jsonb_build_object('task_id',m.task_id,'extraction_job_id',m.extraction_job_id,'card_id',m.card_id,
    'annotation_id',m.id,'release_id',NULL,'cause','source_changed','actor_kind','system'))
 OR EXISTS(SELECT 1 FROM public.annotation_releases r WHERE r.org_id=p_org AND r.id=p_object
  AND NOT public.annotation_release_current(p_org,r.id)
  AND p_details=jsonb_build_object('task_id',r.task_id,'extraction_job_id',r.extraction_job_id,'card_id',r.card_id,
    'annotation_id',r.annotation_id,'release_id',r.id,'cause','approval_stale','actor_kind','system'))
$$;
CREATE FUNCTION public.annotation_invalidate_dependencies(p_changed jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE item record; payload jsonb; can_derive boolean;
 old_org text:=current_setting('app.current_org',true);
BEGIN
 SELECT rolsuper OR rolbypassrls INTO can_derive FROM pg_catalog.pg_roles WHERE rolname=current_user;
 FOR item IN
  SELECT m.org_id,m.task_id,m.extraction_job_id,m.card_id,m.id AS object_id,m.id AS annotation_id,
    NULL::uuid AS release_id,m.request_id,'source_changed'::text AS cause
  FROM public.annotation_materials m
  WHERE (p_changed->>'org_id' IS NULL OR m.org_id=(p_changed->>'org_id')::uuid)
   AND (p_changed->>'task_id' IS NULL OR m.task_id=(p_changed->>'task_id')::uuid)
   AND NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.org_id=m.org_id AND a.object_id=m.id AND a.action='annotation.invalidate')
  UNION ALL
  SELECT r.org_id,r.task_id,r.extraction_job_id,r.card_id,r.id,r.annotation_id,r.id,NULL::uuid,'approval_stale'::text
  FROM public.annotation_releases r
  WHERE (p_changed->>'org_id' IS NULL OR r.org_id=(p_changed->>'org_id')::uuid)
   AND (p_changed->>'task_id' IS NULL OR r.task_id=(p_changed->>'task_id')::uuid)
   AND NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.org_id=r.org_id AND a.object_id=r.id AND a.action='annotation.invalidate')
  ORDER BY org_id,task_id,object_id LOOP
  IF can_derive THEN PERFORM set_config('app.current_org',item.org_id::text,true); END IF;
  IF (item.release_id IS NULL AND NOT public.annotation_source_current(item.org_id,item.request_id))
   OR (item.release_id IS NOT NULL AND NOT public.annotation_release_current(item.org_id,item.release_id)) THEN
   payload:=jsonb_build_object('task_id',item.task_id,'extraction_job_id',item.extraction_job_id,'card_id',item.card_id,
    'annotation_id',item.annotation_id,'release_id',item.release_id,'cause',item.cause,'actor_kind','system');
   INSERT INTO public.audit_logs(id,org_id,actor_user_id,actor_token_id,action,object_id,details)
    VALUES(gen_random_uuid(),item.org_id,NULL,NULL,'annotation.invalidate',item.object_id,payload)
    ON CONFLICT(org_id,object_id) WHERE action='annotation.invalidate' DO NOTHING;
   IF FOUND THEN
    PERFORM public.append_task_event(item.org_id,item.task_id,'board_changed',jsonb_build_object('type','board_changed',
     'invalidate_all',false,'extraction_job_id',item.extraction_job_id,'requirement_ids','[]'::jsonb,
     'card_ids',jsonb_build_array(item.card_id)),item.extraction_job_id);
   END IF;
  END IF;
 END LOOP;
 IF can_derive THEN PERFORM set_config('app.current_org',coalesce(old_org,''),true); END IF;
END $$;
CREATE FUNCTION public.annotation_dependency_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM public.annotation_invalidate_dependencies(to_jsonb(NEW));
 RETURN NULL;
END $$;
CREATE TRIGGER zz_annotation_requirement_dependency AFTER INSERT ON public.requirement_review_events
 FOR EACH ROW EXECUTE FUNCTION public.annotation_dependency_event();
CREATE TRIGGER zz_annotation_round_dependency AFTER INSERT ON public.card_review_invalidations
 FOR EACH ROW EXECUTE FUNCTION public.annotation_dependency_event();
"""
