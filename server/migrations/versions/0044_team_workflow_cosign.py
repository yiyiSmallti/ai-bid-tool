"""Immutable human co-sign rounds and database-enforced consumption gates."""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(SCHEMA_SQL)
    op.execute(HELPERS_SQL)
    op.execute(GUARDS_SQL)
    op.execute(INTEGRATION_SQL)
    op.execute(INVALIDATION_SQL)
    op.execute(INVALIDATION_AUDIT_SQL)


SCHEMA_SQL = r"""
ALTER TABLE public.users ADD COLUMN review_authority_epoch bigint NOT NULL DEFAULT 1 CHECK(review_authority_epoch>=1);
ALTER TABLE public.audit_logs ALTER COLUMN actor_user_id DROP NOT NULL,
 ADD CONSTRAINT audit_system_cosign_invalidation_actor CHECK(actor_user_id IS NOT NULL OR coalesce(
   action='task.review_round_invalidated' AND actor_kind='system'
   AND details->>'actor_kind'='system' AND details->>'invalidation_id' IS NOT NULL
   AND details->>'round_id' IS NOT NULL AND details->>'source_id' IS NOT NULL
   AND details->>'cause' IS NOT NULL,false));

ALTER TABLE public.task_workflows DROP CONSTRAINT task_workflows_co_sign_starred_check,
 DROP CONSTRAINT task_workflows_rule_revision_check,
 ADD CHECK(rule_revision>=1), ADD COLUMN rule_changed_at timestamptz;
ALTER TABLE public.api_tokens ADD CONSTRAINT token_forbidden_cosign_scopes
 CHECK(NOT (scopes ?| ARRAY['task:review-policy','card:cosign']));
ALTER TABLE public.requirement_workflows
 ADD COLUMN co_sign_required boolean NOT NULL DEFAULT false,
 ADD COLUMN policy_revision integer NOT NULL DEFAULT 0 CHECK(policy_revision>=0),
 ADD COLUMN current_round_id uuid,
 ADD COLUMN policy_reason_ciphertext text CHECK(policy_reason_ciphertext IS NULL OR (length(policy_reason_ciphertext)>=80 AND policy_reason_ciphertext~'^gAAAA')), ADD COLUMN policy_changed_at timestamptz,
 ADD COLUMN policy_changed_by_user_id uuid,
 ADD FOREIGN KEY(org_id,policy_changed_by_user_id) REFERENCES public.memberships(org_id,user_id),
 ADD CHECK((policy_revision=0 AND NOT co_sign_required AND policy_reason_ciphertext IS NULL
   AND policy_changed_by_user_id IS NULL AND policy_changed_at IS NULL) OR (policy_revision>0 AND policy_changed_at IS NOT NULL AND length(policy_reason_ciphertext)>0
   AND policy_changed_by_user_id IS NOT NULL));
CREATE TABLE public.card_review_rounds (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, extraction_job_id uuid NOT NULL, requirement_id uuid NOT NULL,
 card_revision_id uuid NOT NULL, card_revision integer NOT NULL CHECK(card_revision>=1),
 round_revision integer NOT NULL CHECK(round_revision>=1), policy_revision integer NOT NULL CHECK(policy_revision>=0),
 task_rule_revision integer NOT NULL CHECK(task_rule_revision>=1), access_epoch integer NOT NULL CHECK(access_epoch>=1),
 required_domains jsonb NOT NULL CHECK(required_domains IN ('["commercial"]','["technical"]','["commercial","technical"]')),
 purpose varchar(20) NOT NULL CHECK(purpose IN ('response','disposition')),
 intended_disposition varchar(20) NOT NULL CHECK(intended_disposition IN ('respond','comply_only')),
 prior_card_state varchar(30) NOT NULL,
 evidence_sha256 varchar(64) NOT NULL CHECK(evidence_sha256~'^[0-9a-f]{64}$'),
 requirement_sha256 varchar(64) NOT NULL CHECK(requirement_sha256~'^[0-9a-f]{64}$'),
 citation_sha256 varchar(64) NOT NULL CHECK(citation_sha256~'^[0-9a-f]{64}$'),
 content_sha256 varchar(64) NOT NULL CHECK(content_sha256~'^[0-9a-f]{64}$'),
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 reason_ciphertext text, reason_sha256 varchar(64) CHECK(reason_sha256~'^[0-9a-f]{64}$'),
 created_by_user_id uuid NOT NULL, client_request_id uuid,
 request_sha256 varchar(64) CHECK(request_sha256~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,task_id,card_id,id),
 UNIQUE(org_id,task_id,extraction_job_id,requirement_id,id),
 UNIQUE(org_id,card_id,round_revision), UNIQUE(org_id,task_id,created_by_user_id,client_request_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,card_id,task_id) REFERENCES public.response_cards(org_id,id,task_id),
 FOREIGN KEY(org_id,requirement_id,task_id,extraction_job_id) REFERENCES public.requirements(org_id,id,task_id,job_id),
 FOREIGN KEY(org_id,card_id,card_revision_id,card_revision) REFERENCES public.response_card_revisions(org_id,card_id,id,revision),
 FOREIGN KEY(org_id,created_by_user_id) REFERENCES public.memberships(org_id,user_id),
 FOREIGN KEY(org_id,task_id,created_by_user_id) REFERENCES public.task_members(org_id,task_id,user_id),
 CHECK((purpose='response' AND prior_card_state='pending_review' AND intended_disposition='respond')
   OR (purpose='disposition' AND prior_card_state IN ('draft','rejected','needs_material') AND reason_ciphertext IS NOT NULL AND reason_sha256 IS NOT NULL)),
 CHECK((reason_ciphertext IS NULL)=(reason_sha256 IS NULL)),
 CHECK(reason_ciphertext IS NULL OR (length(reason_ciphertext)>=80 AND reason_ciphertext~'^gAAAA')),
 CHECK((client_request_id IS NULL)=(request_sha256 IS NULL))
);
ALTER TABLE public.requirement_workflows ADD CONSTRAINT requirement_current_review_round
 FOREIGN KEY(org_id,task_id,extraction_job_id,requirement_id,current_round_id)
 REFERENCES public.card_review_rounds(org_id,task_id,extraction_job_id,requirement_id,id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.card_review_signatures (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, round_id uuid NOT NULL,
 purpose varchar(20) NOT NULL CHECK(purpose IN ('response','disposition')),
 domain varchar(20) NOT NULL CHECK(domain IN ('commercial','technical')),
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 2),
 signer_user_id uuid NOT NULL, signer_user_epoch bigint NOT NULL CHECK(signer_user_epoch>=1), signer_org_role varchar(20) NOT NULL CHECK(signer_org_role IN ('bidder','technical')),
 reviewed_evidence_ids jsonb NOT NULL CHECK(jsonb_typeof(reviewed_evidence_ids)='array' AND jsonb_array_length(reviewed_evidence_ids)<=100),
 reviewed_warning_codes jsonb NOT NULL CHECK(jsonb_typeof(reviewed_warning_codes)='array' AND jsonb_array_length(reviewed_warning_codes)<=100),
 reason_ciphertext text, reason_sha256 varchar(64) CHECK(reason_sha256~'^[0-9a-f]{64}$'),
 client_request_id uuid NOT NULL, request_sha256 varchar(64) NOT NULL CHECK(request_sha256~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,round_id,ordinal), UNIQUE(org_id,round_id,domain), UNIQUE(org_id,round_id,signer_user_id),
 UNIQUE(org_id,task_id,signer_user_id,client_request_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,task_id,card_id,round_id) REFERENCES public.card_review_rounds(org_id,task_id,card_id,id),
 FOREIGN KEY(org_id,signer_user_id) REFERENCES public.memberships(org_id,user_id),
 FOREIGN KEY(org_id,task_id,signer_user_id) REFERENCES public.task_members(org_id,task_id,user_id),
 CHECK((domain='commercial' AND signer_org_role='bidder') OR (domain='technical' AND signer_org_role='technical')),
 CHECK((reason_ciphertext IS NULL)=(reason_sha256 IS NULL)),
 CHECK(reason_ciphertext IS NULL OR (length(reason_ciphertext)>=80 AND reason_ciphertext~'^gAAAA')),
 CHECK(purpose<>'disposition' OR (reviewed_evidence_ids='[]' AND reason_sha256 IS NOT NULL)),
 CHECK(reviewed_warning_codes='[]' OR reason_sha256 IS NOT NULL)
);
CREATE TABLE public.card_review_invalidations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES public.orgs(id),
 task_id uuid NOT NULL, card_id uuid NOT NULL, round_id uuid NOT NULL,
 cause varchar(40) NOT NULL CHECK(cause IN ('authority_lost','policy_changed','rule_changed','card_changed','requirement_changed','citation_changed','material_changed')),
 source_id uuid NOT NULL, actor_user_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(org_id,id), UNIQUE(org_id,round_id,cause,source_id),
 FOREIGN KEY(org_id,task_id) REFERENCES public.tasks(org_id,id),
 FOREIGN KEY(org_id,task_id,card_id,round_id) REFERENCES public.card_review_rounds(org_id,task_id,card_id,id),
 FOREIGN KEY(org_id,actor_user_id) REFERENCES public.memberships(org_id,user_id)
);
CREATE INDEX card_review_round_lookup ON public.card_review_rounds(org_id,task_id,card_id,round_revision);
CREATE INDEX card_review_signer_lookup ON public.card_review_signatures(org_id,signer_user_id,round_id);
CREATE INDEX card_review_invalidation_lookup ON public.card_review_invalidations(org_id,round_id);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['card_review_rounds','card_review_signatures','card_review_invalidations'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY tenant_isolation ON public.%I USING(org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid) WITH CHECK(org_id=nullif(current_setting(''app.current_org'',true),'''')::uuid)',tab);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO bid_app',tab);
 END LOOP;
END $$;
"""

HELPERS_SQL = r"""
CREATE FUNCTION public.team_cosign_hash(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(p_value::text,'UTF8')),'hex')
$$;
CREATE FUNCTION public.team_cosign_domains(p_org uuid,p_card uuid) RETURNS jsonb
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN v.review_domain IS NULL THEN '[]'::jsonb
  WHEN coalesce(rw.co_sign_required,false) OR (q.starred AND tw.co_sign_starred)
   THEN '["commercial","technical"]'::jsonb ELSE jsonb_build_array(v.review_domain) END
 FROM public.response_cards c JOIN public.response_card_revisions v ON (v.org_id,v.id)=(c.org_id,c.current_revision_id)
 JOIN public.requirements q ON (q.org_id,q.id)=(c.org_id,c.requirement_id)
 JOIN public.task_workflows tw ON (tw.org_id,tw.task_id)=(c.org_id,c.task_id)
 LEFT JOIN public.requirement_workflows rw ON (rw.org_id,rw.task_id,rw.extraction_job_id,rw.requirement_id)=(c.org_id,c.task_id,c.extraction_job_id,c.requirement_id)
 WHERE c.org_id=p_org AND c.id=p_card
$$;
CREATE FUNCTION public.team_cosign_signer_authorized(p_org uuid,p_task uuid,p_user uuid,p_domain text) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM public.task_members tm JOIN public.memberships m ON (m.org_id,m.user_id)=(tm.org_id,tm.user_id)
 JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
 WHERE tm.org_id=p_org AND tm.task_id=p_task AND tm.user_id=p_user AND tm.active AND m.active AND u.active AND o.active
 AND tm.role IN ('owner','contributor','reviewer') AND tm.review_domains ? p_domain
 AND m.role=(CASE p_domain WHEN 'commercial' THEN 'bidder' WHEN 'technical' THEN 'technical' ELSE '' END))
$$;
CREATE FUNCTION public.team_cosign_warnings(p_org uuid,p_revision uuid) RETURNS jsonb
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 WITH RECURSIVE images AS (
 SELECT a.*,e.screenshot_rendition_id FROM public.card_evidence_links l JOIN public.evidence e ON (e.org_id,e.id)=(l.org_id,l.evidence_id)
 JOIN public.screenshot_assets a ON (a.org_id,a.id)=(e.org_id,e.screenshot_asset_id)
 WHERE l.org_id=p_org AND l.revision_id=p_revision AND e.kind='image_region'
 ), lineage AS (
 SELECT r.id,r.parent_rendition_id,r.plan FROM public.screenshot_renditions r JOIN images i ON i.screenshot_rendition_id=r.id WHERE r.org_id=p_org
 UNION SELECT r.id,r.parent_rendition_id,r.plan FROM public.screenshot_renditions r JOIN lineage child ON r.id=child.parent_rendition_id WHERE r.org_id=p_org
 ), codes AS (
 SELECT 'proof_material_required' AS code FROM public.response_card_revisions v
 JOIN public.response_cards c ON (c.org_id,c.id)=(v.org_id,v.card_id)
 JOIN public.requirements q ON (q.org_id,q.id)=(c.org_id,c.requirement_id)
 WHERE v.org_id=p_org AND v.id=p_revision AND (q.quote ~ '提供.*(证书|检测报告|截图|说明书|证明|复印件)'
 OR q.quote ~* '(certificate|report|screenshot|proof).*(provid|attach)|(provid|attach).*(certificate|report|screenshot|proof)')
 UNION SELECT 'image_visible_scope_only' FROM images
 UNION SELECT 'prototype_delivery_obligation' FROM images i WHERE i.origin='prototype'
 UNION SELECT 'image_source_claim' FROM images i WHERE i.origin<>'prototype'
 UNION SELECT 'vendor_model_scope' FROM images i WHERE i.origin='vendor'
 UNION SELECT 'vendor_capture_incomplete' FROM images i JOIN public.screenshot_vendor_archives va ON va.org_id=i.org_id AND va.id=i.vendor_archive_id WHERE coalesce((va.provenance->>'incomplete')::boolean,false)
 UNION SELECT 'image_test_environment' FROM images i WHERE i.source->>'environment' IN ('test','development')
 UNION SELECT 'image_design_only' FROM images i WHERE i.image_kind='diagram'
 UNION SELECT 'image_redaction_review' FROM lineage l WHERE jsonb_array_length(l.plan->'redact')>0
 UNION SELECT 'image_crop_review' FROM lineage l WHERE l.plan->'crop'<>'null'::jsonb
 UNION SELECT 'memory_input_stale' FROM public.response_card_revisions v WHERE v.org_id=p_org AND v.id=p_revision
  AND public.memory_generation_current(p_org,v.model_job_id) IS DISTINCT FROM true
 ) SELECT coalesce(jsonb_agg(code ORDER BY code),'[]'::jsonb) FROM codes
$$;
CREATE FUNCTION public.team_cosign_snapshot(p_org uuid,p_card uuid,p_revision uuid) RETURNS jsonb
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object(
 'requirement',public.team_cosign_hash(to_jsonb(q)),
 'citation',public.team_cosign_hash(jsonb_build_object('chunk',to_jsonb(ch),'document_id',q.document_id,'page',q.page,'location',q.location,'quote',q.quote)),
 'content',public.team_cosign_hash(jsonb_build_object('response_kind',v.response_kind,'response_text',v.response_text,'deviation',v.deviation,
   'deviation_note',v.deviation_note,'review_domain',v.review_domain,'model_job_id',v.model_job_id,'suggested_disposition',v.suggested_disposition,'review_hint',v.review_hint)),
 'evidence',public.team_cosign_hash(coalesce((SELECT jsonb_agg(to_jsonb(e)-ARRAY['confirmed_by','confirmed_at','quote_check'] ORDER BY e.id)
   FROM public.card_evidence_links l JOIN public.evidence e ON (e.org_id,e.id)=(l.org_id,l.evidence_id)
   WHERE l.org_id=p_org AND l.revision_id=p_revision),'[]'::jsonb)),
 'evidence_ids',coalesce((SELECT jsonb_agg(l.evidence_id ORDER BY l.evidence_id) FROM public.card_evidence_links l
   WHERE l.org_id=p_org AND l.revision_id=p_revision),'[]'::jsonb),
 'warnings',public.team_cosign_warnings(p_org,p_revision))
 FROM public.response_cards c JOIN public.response_card_revisions v ON v.org_id=c.org_id AND v.card_id=c.id AND v.id=p_revision
 JOIN public.requirements q ON (q.org_id,q.id)=(c.org_id,c.requirement_id)
 JOIN public.chunks ch ON (ch.org_id,ch.id)=(q.org_id,q.chunk_id)
 WHERE c.org_id=p_org AND c.id=p_card
$$;
CREATE FUNCTION public.team_cosign_round_valid(p_org uuid,p_round uuid) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT
  NOT EXISTS(SELECT 1 FROM public.card_review_invalidations i WHERE i.org_id=p_org AND i.round_id=rr.id)
  AND rw.current_round_id=rr.id AND rw.policy_revision=rr.policy_revision
  AND (NOT q.starred OR tw.rule_revision=rr.task_rule_revision)
  AND rr.required_domains=public.team_cosign_domains(p_org,rr.card_id)
  AND rr.snapshot=public.team_cosign_snapshot(p_org,rr.card_id,rr.card_revision_id)
  AND public.response_citation_valid(p_org,rr.requirement_id) IS TRUE
  AND public.response_quote_current(p_org,rr.card_revision_id) IS TRUE
  AND (rr.purpose='disposition' OR (public.response_generation_materials_active(p_org,pinned.model_job_id) IS TRUE
  AND public.memory_generation_current(p_org,pinned.model_job_id) IS TRUE
  AND NOT EXISTS(SELECT 1 FROM public.card_evidence_links l WHERE l.org_id=p_org AND l.revision_id=rr.card_revision_id
    AND public.response_evidence_active(p_org,l.evidence_id) IS DISTINCT FROM true)))
  AND NOT EXISTS(SELECT 1 FROM public.card_review_signatures sig WHERE sig.org_id=p_org AND sig.round_id=rr.id
    AND (NOT public.team_cosign_signer_authorized(p_org,rr.task_id,sig.signer_user_id,sig.domain)
      OR sig.signer_user_epoch IS DISTINCT FROM (SELECT u.review_authority_epoch FROM public.users u WHERE u.id=sig.signer_user_id)))
  AND (c.current_revision_id=rr.card_revision_id OR
   (cv.revision=rr.card_revision+1 AND cv.actor_kind='session'
    AND ((rr.purpose='response' AND cv.state='confirmed') OR (rr.purpose='disposition' AND cv.disposition=rr.intended_disposition AND cv.state=rr.prior_card_state))
    AND public.team_cosign_snapshot(p_org,rr.card_id,cv.id)=rr.snapshot
    AND EXISTS(SELECT 1 FROM public.card_review_signatures sig WHERE sig.org_id=p_org AND sig.round_id=rr.id AND sig.signer_user_id=cv.actor_user_id)))
 FROM public.card_review_rounds rr
 JOIN public.response_cards c ON (c.org_id,c.id)=(rr.org_id,rr.card_id)
 JOIN public.response_card_revisions cv ON (cv.org_id,cv.id)=(c.org_id,c.current_revision_id)
 JOIN public.response_card_revisions pinned ON (pinned.org_id,pinned.id)=(rr.org_id,rr.card_revision_id)
 JOIN public.requirement_workflows rw ON (rw.org_id,rw.task_id,rw.extraction_job_id,rw.requirement_id)=(rr.org_id,rr.task_id,rr.extraction_job_id,rr.requirement_id)
 JOIN public.task_workflows tw ON (tw.org_id,tw.task_id)=(rr.org_id,rr.task_id)
 JOIN public.requirements q ON (q.org_id,q.id)=(rr.org_id,rr.requirement_id)
 WHERE rr.org_id=p_org AND rr.id=p_round),false)
$$;
CREATE FUNCTION public.team_cosign_complete(p_org uuid,p_round uuid) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT public.team_cosign_round_valid(p_org,p_round) AND coalesce((SELECT
  NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(rr.required_domains) d WHERE NOT EXISTS(
   SELECT 1 FROM public.card_review_signatures sig WHERE sig.org_id=p_org AND sig.round_id=rr.id AND sig.domain=d.value))
 FROM public.card_review_rounds rr WHERE rr.org_id=p_org AND rr.id=p_round),false)
$$;
CREATE FUNCTION public.team_cosign_card_approved(p_org uuid,p_card uuid) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT coalesce((SELECT CASE WHEN rw.current_round_id IS NULL THEN
  jsonb_array_length(public.team_cosign_domains(p_org,p_card))=1
  AND cv.created_at>=greatest(coalesce(rw.policy_changed_at,'-infinity'::timestamptz),
   CASE WHEN q.starred THEN coalesce(tw.rule_changed_at,'-infinity'::timestamptz) ELSE '-infinity'::timestamptz END)
 WHEN jsonb_array_length(public.team_cosign_domains(p_org,p_card))=1 AND cv.state IN ('draft','rejected','needs_material')
  AND cv.disposition IS NOT NULL AND cv.actor_kind='session' AND cv.disposition_by=cv.actor_user_id
  AND cv.created_at>=greatest(coalesce(rw.policy_changed_at,'-infinity'::timestamptz),
   CASE WHEN q.starred THEN coalesce(tw.rule_changed_at,'-infinity'::timestamptz) ELSE '-infinity'::timestamptz END)
  AND cv.revision>rr.card_revision AND EXISTS(SELECT 1 FROM public.response_card_revisions prior
   WHERE prior.org_id=cv.org_id AND prior.card_id=cv.card_id AND prior.revision=cv.revision-1
   AND (prior.disposition,prior.disposition_by,prior.disposition_at) IS DISTINCT FROM (cv.disposition,cv.disposition_by,cv.disposition_at))
  THEN true
 ELSE public.team_cosign_complete(p_org,rw.current_round_id) AND c.current_revision_id<>rr.card_revision_id
  AND ((rr.purpose='response' AND cv.state='confirmed' AND cv.disposition='respond')
    OR (rr.purpose='disposition' AND cv.disposition=rr.intended_disposition)) END
 FROM public.response_cards c JOIN public.response_card_revisions cv ON (cv.org_id,cv.id)=(c.org_id,c.current_revision_id)
 JOIN public.requirements q ON (q.org_id,q.id)=(c.org_id,c.requirement_id)
 JOIN public.task_workflows tw ON (tw.org_id,tw.task_id)=(c.org_id,c.task_id)
 LEFT JOIN public.requirement_workflows rw ON (rw.org_id,rw.task_id,rw.extraction_job_id,rw.requirement_id)=(c.org_id,c.task_id,c.extraction_job_id,c.requirement_id)
 LEFT JOIN public.card_review_rounds rr ON (rr.org_id,rr.id)=(rw.org_id,rw.current_round_id)
 WHERE c.org_id=p_org AND c.id=p_card),false)
$$;
CREATE FUNCTION public.team_cosign_human(p_org uuid,p_task uuid,p_domain text) RETURNS uuid
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid;
BEGIN
 uid:=public.task_collaboration_human(p_org,p_task,'card:cosign');
 PERFORM 1 FROM public.task_members tm JOIN public.memberships m ON (m.org_id,m.user_id)=(tm.org_id,tm.user_id)
 JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
 WHERE tm.org_id=p_org AND tm.task_id=p_task AND tm.user_id=uid FOR SHARE OF tm,m,u,o;
 IF NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ?& ARRAY['card:read','evidence:confirm']
 OR NOT public.team_cosign_signer_authorized(p_org,p_task,uid,p_domain) THEN
  RAISE EXCEPTION 'professional task co-sign human required' USING ERRCODE='42501'; END IF;
 RETURN uid;
END $$;
"""

GUARDS_SQL = r"""
CREATE FUNCTION public.team_cosign_user_epoch() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN NEW.review_authority_epoch:=1;
 ELSIF NEW.active IS DISTINCT FROM OLD.active THEN NEW.review_authority_epoch:=OLD.review_authority_epoch+1;
 ELSIF NEW.review_authority_epoch<>OLD.review_authority_epoch THEN
  RAISE EXCEPTION 'user review authority epoch is derived' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER aaa_team_cosign_user_epoch BEFORE INSERT OR UPDATE ON public.users
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_user_epoch();
CREATE FUNCTION public.team_cosign_policy_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid;
BEGIN
 IF TG_TABLE_NAME='task_workflows' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.co_sign_starred OR NEW.rule_revision<>1 OR NEW.rule_changed_at IS NOT NULL THEN RAISE EXCEPTION 'initial review rule' USING ERRCODE='23514'; END IF;
  ELSIF (NEW.co_sign_starred,NEW.rule_revision) IS DISTINCT FROM (OLD.co_sign_starred,OLD.rule_revision) THEN
   PERFORM public.task_workflow_human(NEW.org_id,NEW.task_id,'task:review-policy');
   NEW.rule_changed_at:=clock_timestamp();
   IF OLD.state<>'active' OR NEW.rule_revision<>OLD.rule_revision+1 OR NEW.last_reason_ciphertext IS NULL
    OR NEW.co_sign_starred=OLD.co_sign_starred THEN RAISE EXCEPTION 'review rule revision or reason' USING ERRCODE='23514'; END IF;
  ELSIF NEW.rule_changed_at IS DISTINCT FROM OLD.rule_changed_at THEN
   RAISE EXCEPTION 'immutable rule change marker' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.requirements q JOIN public.jobs j ON (j.org_id,j.id)=(q.org_id,q.job_id)
   WHERE (q.org_id,q.task_id,q.job_id,q.id)=(NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id)
   AND j.kind='extract' AND j.status='succeeded') THEN RAISE EXCEPTION 'saved extraction required for policy' USING ERRCODE='23514'; END IF;
  IF (TG_OP='INSERT' AND NEW.policy_revision>0) OR (TG_OP='UPDATE' AND
    (NEW.co_sign_required,NEW.policy_revision,NEW.policy_reason_ciphertext,NEW.policy_changed_by_user_id)
    IS DISTINCT FROM (OLD.co_sign_required,OLD.policy_revision,OLD.policy_reason_ciphertext,OLD.policy_changed_by_user_id)) THEN
   PERFORM public.task_workflow_human(NEW.org_id,NEW.task_id,'task:review-policy');
   NEW.policy_changed_at:=clock_timestamp();
   PERFORM 1 FROM public.tasks t WHERE t.org_id=NEW.org_id AND t.id=NEW.task_id FOR UPDATE;
   IF NOT EXISTS(SELECT 1 FROM public.task_workflows tw WHERE tw.org_id=NEW.org_id AND tw.task_id=NEW.task_id AND tw.state='active')
    OR NEW.policy_revision<>(CASE WHEN TG_OP='INSERT' THEN 1 ELSE OLD.policy_revision+1 END)
    OR NEW.policy_changed_by_user_id IS DISTINCT FROM uid OR coalesce(length(NEW.policy_reason_ciphertext),0)=0 THEN
    RAISE EXCEPTION 'review policy revision or actor' USING ERRCODE='23514'; END IF;
  ELSIF TG_OP='UPDATE' AND NEW.policy_changed_at IS DISTINCT FROM OLD.policy_changed_at THEN
   RAISE EXCEPTION 'immutable policy change marker' USING ERRCODE='23514';
  END IF;
  IF (TG_OP='INSERT' AND NEW.current_round_id IS NOT NULL) OR
    (TG_OP='UPDATE' AND NEW.current_round_id IS DISTINCT FROM OLD.current_round_id) THEN
   IF NEW.current_round_id IS NULL OR NOT EXISTS(SELECT 1 FROM public.card_review_rounds rr
    WHERE (rr.org_id,rr.task_id,rr.extraction_job_id,rr.requirement_id,rr.id)=
     (NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id,NEW.current_round_id)
    AND rr.xmin=pg_current_xact_id()::xid
    AND rr.round_revision=(SELECT max(other.round_revision) FROM public.card_review_rounds other WHERE other.org_id=rr.org_id AND other.card_id=rr.card_id)) THEN
    RAISE EXCEPTION 'round pointer must bind newly opened round' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER aaa_team_cosign_policy BEFORE INSERT OR UPDATE ON public.requirement_workflows
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_policy_guard();
CREATE TRIGGER aaa_team_cosign_rule BEFORE INSERT OR UPDATE ON public.task_workflows
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_policy_guard();

CREATE FUNCTION public.team_cosign_history_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rr public.card_review_rounds; cv public.response_card_revisions; cr public.response_cards;
 uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid; old_round uuid; expected_ids jsonb; expected_warnings jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'co-sign history is immutable' USING ERRCODE='42501'; END IF;
 IF NEW.org_id IS DISTINCT FROM nullif(current_setting('app.current_org',true),'')::uuid THEN
  RAISE EXCEPTION 'co-sign tenant context required' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.tasks t WHERE t.org_id=NEW.org_id AND t.id=NEW.task_id FOR UPDATE;
 IF TG_TABLE_NAME='card_review_invalidations' THEN
  IF pg_trigger_depth()<2 THEN
   PERFORM public.task_collaboration_human(NEW.org_id,NEW.task_id,'card:read');
   IF NEW.actor_user_id IS DISTINCT FROM uid OR public.team_cosign_round_valid(NEW.org_id,NEW.round_id) THEN
    RAISE EXCEPTION 'invalidation must reflect lost validity' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO cr FROM public.response_cards c WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id AND c.task_id=NEW.task_id FOR UPDATE;
 SELECT * INTO cv FROM public.response_card_revisions v WHERE (v.org_id,v.id)=(cr.org_id,cr.current_revision_id);
 IF cr.id IS NULL THEN RAISE EXCEPTION 'co-sign card parent mismatch' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='card_review_rounds' THEN
  IF NEW.created_by_user_id IS DISTINCT FROM uid OR NEW.card_revision_id IS DISTINCT FROM cr.current_revision_id
    OR NEW.card_revision<>cr.revision OR (NEW.extraction_job_id,NEW.requirement_id) IS DISTINCT FROM (cr.extraction_job_id,cr.requirement_id) THEN
   RAISE EXCEPTION 'co-sign round revision conflict' USING ERRCODE='23514'; END IF;
  IF NEW.purpose='disposition' THEN
   PERFORM public.team_cosign_human(NEW.org_id,NEW.task_id,cv.review_domain);
  ELSE
   IF NOT EXISTS(SELECT 1 FROM public.task_members tm JOIN public.memberships m ON (m.org_id,m.user_id)=(tm.org_id,tm.user_id)
    JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
    WHERE tm.org_id=NEW.org_id AND tm.task_id=NEW.task_id AND tm.user_id=uid AND tm.active AND m.active AND u.active AND o.active
      AND tm.role IN ('owner','contributor') AND m.role IN ('admin','bidder','technical'))
      AND NOT public.team_cosign_signer_authorized(NEW.org_id,NEW.task_id,uid,cv.review_domain) THEN
    RAISE EXCEPTION 'task submitter authority required' USING ERRCODE='42501'; END IF;
   IF cv.state<>'pending_review' OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions v WHERE v.org_id=NEW.org_id AND v.id=cv.id
     AND v.xmin=pg_current_xact_id()::xid AND v.actor_user_id=uid)
     OR NOT coalesce(nullif(current_setting('app.actor_scopes',true),''),'[]')::jsonb ? 'card:write' THEN
    PERFORM public.team_cosign_human(NEW.org_id,NEW.task_id,cv.review_domain);
   IF cv.state<>'pending_review' THEN RAISE EXCEPTION 'response round requires pending review' USING ERRCODE='23514'; END IF; END IF;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM public.task_workflows tw WHERE tw.org_id=NEW.org_id AND tw.task_id=NEW.task_id AND tw.state='active')
   OR cv.review_domain IS NULL OR public.response_citation_valid(NEW.org_id,NEW.requirement_id) IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'review round requires active classified citation' USING ERRCODE='23514'; END IF;
  NEW.prior_card_state:=cv.state;
  NEW.round_revision:=coalesce((SELECT max(other.round_revision) FROM public.card_review_rounds other WHERE other.org_id=NEW.org_id AND other.card_id=NEW.card_id),0)+1;
  SELECT tw.rule_revision,tw.access_epoch INTO NEW.task_rule_revision,NEW.access_epoch FROM public.task_workflows tw WHERE tw.org_id=NEW.org_id AND tw.task_id=NEW.task_id;
  SELECT rw.policy_revision,rw.current_round_id INTO NEW.policy_revision,old_round FROM public.requirement_workflows rw
   WHERE (rw.org_id,rw.task_id,rw.extraction_job_id,rw.requirement_id)=(NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id);
  NEW.policy_revision:=coalesce(NEW.policy_revision,0);
  NEW.required_domains:=public.team_cosign_domains(NEW.org_id,NEW.card_id);
  NEW.snapshot:=public.team_cosign_snapshot(NEW.org_id,NEW.card_id,NEW.card_revision_id);
  NEW.evidence_sha256:=NEW.snapshot->>'evidence'; NEW.requirement_sha256:=NEW.snapshot->>'requirement';
  NEW.citation_sha256:=NEW.snapshot->>'citation'; NEW.content_sha256:=NEW.snapshot->>'content';
  IF old_round IS NOT NULL THEN
   INSERT INTO public.card_review_invalidations(org_id,task_id,card_id,round_id,cause,source_id,actor_user_id)
    VALUES(NEW.org_id,NEW.task_id,NEW.card_id,old_round,'card_changed',NEW.id,uid) ON CONFLICT DO NOTHING;
  END IF;
 ELSE
  SELECT * INTO rr FROM public.card_review_rounds r WHERE r.org_id=NEW.org_id AND r.id=NEW.round_id
    AND r.task_id=NEW.task_id AND r.card_id=NEW.card_id FOR UPDATE;
  IF rr.id IS NULL OR NOT public.team_cosign_round_valid(NEW.org_id,rr.id)
   OR cr.current_revision_id<>rr.card_revision_id OR NEW.purpose<>rr.purpose OR NOT rr.required_domains ? NEW.domain THEN
   RAISE EXCEPTION 'stale co-sign round or purpose' USING ERRCODE='23514'; END IF;
  uid:=public.team_cosign_human(NEW.org_id,NEW.task_id,NEW.domain);
  IF NEW.signer_user_id IS DISTINCT FROM uid THEN RAISE EXCEPTION 'co-sign signer mismatch' USING ERRCODE='42501'; END IF;
  SELECT m.role,u.review_authority_epoch INTO NEW.signer_org_role,NEW.signer_user_epoch FROM public.memberships m JOIN public.users u ON u.id=m.user_id WHERE m.org_id=NEW.org_id AND m.user_id=uid;
  expected_ids:=(CASE WHEN rr.purpose='response' THEN rr.snapshot->'evidence_ids' ELSE '[]'::jsonb END);
  expected_warnings:=rr.snapshot->'warnings';
  IF jsonb_typeof(NEW.reviewed_evidence_ids) IS DISTINCT FROM 'array' OR jsonb_typeof(NEW.reviewed_warning_codes) IS DISTINCT FROM 'array'
   OR jsonb_array_length(NEW.reviewed_evidence_ids)<>jsonb_array_length(expected_ids)
   OR NOT NEW.reviewed_evidence_ids @> expected_ids OR NOT expected_ids @> NEW.reviewed_evidence_ids
   OR jsonb_array_length(NEW.reviewed_warning_codes)<>jsonb_array_length(expected_warnings)
   OR NOT NEW.reviewed_warning_codes @> expected_warnings OR NOT expected_warnings @> NEW.reviewed_warning_codes THEN
   RAISE EXCEPTION 'review every pinned evidence and warning exactly once' USING ERRCODE='23514'; END IF;
  NEW.ordinal:=(SELECT count(*)+1 FROM public.card_review_signatures sig WHERE sig.org_id=NEW.org_id AND sig.round_id=NEW.round_id);
  IF rr.purpose='response' AND (cv.response_kind IS NULL OR cv.response_text IS NULL OR cv.deviation IS NULL
   OR cv.deviation_note IS NULL OR btrim(cv.deviation_note)='满足'
   OR (cv.response_kind='evidence' AND expected_ids='[]') OR (cv.response_kind='commitment' AND expected_ids<>'[]')) THEN
   RAISE EXCEPTION 'complete response required before signing' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.team_cosign_round_pointer() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO public.requirement_workflows(org_id,task_id,extraction_job_id,requirement_id,current_round_id)
 VALUES(NEW.org_id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id,NEW.id)
 ON CONFLICT(org_id,task_id,extraction_job_id,requirement_id) DO UPDATE SET current_round_id=excluded.current_round_id;
 RETURN NULL;
END $$;
CREATE TRIGGER team_cosign_round_pointer AFTER INSERT ON public.card_review_rounds
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_round_pointer();
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['card_review_rounds','card_review_signatures','card_review_invalidations'] LOOP
  EXECUTE format('CREATE TRIGGER team_cosign_history_guard BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.team_cosign_history_guard()',tab);
 END LOOP;
END $$;
CREATE FUNCTION public.team_cosign_finalizer(p_org uuid,p_domain text,p_card uuid) RETURNS uuid
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rr public.card_review_rounds; cr public.response_cards;
 uid uuid:=nullif(current_setting('app.actor_user_id',true),'')::uuid; signing_domain text;
BEGIN
 SELECT * INTO cr FROM public.response_cards c WHERE c.org_id=p_org AND c.id=p_card;
 SELECT r.* INTO rr FROM public.card_review_rounds r JOIN public.requirement_workflows rw ON (rw.org_id,rw.current_round_id)=(r.org_id,r.id)
  WHERE r.org_id=p_org AND r.card_id=p_card;
 IF rr.id IS NULL THEN
  IF jsonb_array_length(public.team_cosign_domains(p_org,p_card))>1 THEN RAISE EXCEPTION 'cosign_required' USING ERRCODE='23514'; END IF;
  RETURN public.response_require_human(p_org,p_domain);
 END IF;
 PERFORM 1 FROM public.card_review_signatures sig JOIN public.task_members tm ON (tm.org_id,tm.task_id,tm.user_id)=(sig.org_id,sig.task_id,sig.signer_user_id)
 JOIN public.memberships m ON (m.org_id,m.user_id)=(tm.org_id,tm.user_id) JOIN public.users u ON u.id=m.user_id JOIN public.orgs o ON o.id=m.org_id
 WHERE sig.org_id=p_org AND sig.round_id=rr.id ORDER BY sig.signer_user_id FOR SHARE OF tm,m,u,o;
 IF rr.card_revision_id<>cr.current_revision_id OR NOT public.team_cosign_complete(p_org,rr.id) THEN
  RAISE EXCEPTION 'complete current co-sign required' USING ERRCODE='23514'; END IF;
 SELECT sig.domain INTO signing_domain FROM public.card_review_signatures sig
  WHERE sig.org_id=p_org AND sig.round_id=rr.id AND sig.signer_user_id=uid AND sig.xmin=pg_current_xact_id()::xid;
 IF signing_domain IS NULL THEN RAISE EXCEPTION 'last signature must finalize atomically' USING ERRCODE='42501'; END IF;
 RETURN public.team_cosign_human(p_org,cr.task_id,signing_domain);
END $$;
CREATE FUNCTION public.team_cosign_revision_gate() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rr public.card_review_rounds; prev public.response_card_revisions;
BEGIN
 SELECT r.* INTO rr FROM public.card_review_rounds r JOIN public.requirement_workflows rw ON (rw.org_id,rw.current_round_id)=(r.org_id,r.id)
  WHERE r.org_id=NEW.org_id AND r.card_id=NEW.card_id;
 SELECT v.* INTO prev FROM public.response_card_revisions v WHERE v.org_id=NEW.org_id AND v.card_id=NEW.card_id AND v.revision=NEW.revision-1;
 IF NEW.state='confirmed' AND rr.id IS NULL THEN RAISE EXCEPTION 'response confirmation requires review round' USING ERRCODE='23514'; END IF;
 IF NEW.state='confirmed' OR (NEW.disposition IS DISTINCT FROM prev.disposition AND NEW.disposition IS NOT NULL) THEN
  IF NEW.state='confirmed' THEN
   PERFORM public.team_cosign_finalizer(NEW.org_id,NEW.review_domain,NEW.card_id);
  ELSE PERFORM public.team_cosign_disposition_human(NEW.org_id,NEW.review_domain,NEW.card_id); END IF;
  IF rr.id IS NOT NULL AND (NEW.state='confirmed' OR jsonb_array_length(public.team_cosign_domains(NEW.org_id,NEW.card_id))>1) AND ((NEW.state='confirmed' AND rr.purpose<>'response') OR
    (NEW.state<>'confirmed' AND (rr.purpose<>'disposition' OR rr.intended_disposition IS DISTINCT FROM NEW.disposition))) THEN
   RAISE EXCEPTION 'co-sign purpose cannot be reused' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER aaa_team_cosign_revision BEFORE INSERT ON public.response_card_revisions
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_revision_gate();
CREATE FUNCTION public.team_cosign_submission_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.state='pending_review' AND NOT EXISTS(SELECT 1 FROM public.card_review_rounds rr
   WHERE rr.org_id=NEW.org_id AND rr.card_id=NEW.card_id AND rr.card_revision_id=NEW.id AND rr.purpose='response') THEN
  RAISE EXCEPTION 'submission requires pinned response review round' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER team_cosign_submission_complete AFTER INSERT ON public.response_card_revisions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.team_cosign_submission_complete();
CREATE FUNCTION public.team_cosign_signature_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rr public.card_review_rounds; cv public.response_card_revisions;
BEGIN
 SELECT r.* INTO rr FROM public.card_review_rounds r WHERE r.org_id=NEW.org_id AND r.id=NEW.round_id;
 IF (SELECT count(*) FROM public.card_review_signatures sig WHERE sig.org_id=NEW.org_id AND sig.round_id=rr.id)=jsonb_array_length(rr.required_domains) THEN
  SELECT v.* INTO cv FROM public.response_card_revisions v JOIN public.response_cards c ON (c.org_id,c.current_revision_id)=(v.org_id,v.id)
   WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
  IF NOT public.team_cosign_complete(NEW.org_id,rr.id) OR cv.revision<>rr.card_revision+1 OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions v
   WHERE v.org_id=cv.org_id AND v.id=cv.id AND v.xmin=pg_current_xact_id()::xid)
   OR (rr.purpose='response' AND cv.state<>'confirmed') OR (rr.purpose='disposition' AND cv.disposition IS DISTINCT FROM rr.intended_disposition) THEN
   RAISE EXCEPTION 'last co-sign must finalize in its transaction' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER team_cosign_signature_complete AFTER INSERT ON public.card_review_signatures
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.team_cosign_signature_complete();
"""

INTEGRATION_SQL = r"""
CREATE FUNCTION pg_temp.team_cosign_replace(p_definition text,p_old text,p_new text) RETURNS text
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF p_old='' OR strpos(p_definition,p_old)=0 THEN
  RAISE EXCEPTION 'co-sign migration gate replacement target missing: %',p_old;
 END IF;
 RETURN replace(p_definition,p_old,p_new);
END $$;
CREATE FUNCTION public.team_cosign_disposition_human(p_org uuid,p_domain text,p_card uuid) RETURNS uuid
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE task uuid; uid uuid;
BEGIN
 IF jsonb_array_length(public.team_cosign_domains(p_org,p_card))=1 THEN
  SELECT c.task_id INTO task FROM public.response_cards c WHERE c.org_id=p_org AND c.id=p_card;
  uid:=public.task_collaboration_human(p_org,task,'evidence:confirm');
  IF NOT public.team_cosign_signer_authorized(p_org,task,uid,p_domain) THEN
   RAISE EXCEPTION 'task disposition reviewer required' USING ERRCODE='42501'; END IF;
  RETURN public.response_require_human(p_org,p_domain);
 END IF;
 RETURN public.team_cosign_finalizer(p_org,p_domain,p_card);
END $$;
-- Keep the mature response gates, replacing only their primary-domain finalizer
-- checks. Reject/reopen/needs-material still use response_require_human directly.
DO $$ DECLARE definition text; updated text; BEGIN
 SELECT pg_get_functiondef('public.response_revision_gate()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,
  'PERFORM public.response_require_human(NEW.org_id,NEW.review_domain);',
  'IF NEW.state=''confirmed'' THEN PERFORM public.team_cosign_finalizer(NEW.org_id,NEW.review_domain,NEW.card_id); ELSE PERFORM public.response_require_human(NEW.org_id,NEW.review_domain); END IF;');
 updated:=pg_temp.team_cosign_replace(updated,
  'actor := public.response_require_human(NEW.org_id,NEW.review_domain);',
  'actor := public.team_cosign_finalizer(NEW.org_id,NEW.review_domain,NEW.card_id);');
 updated:=pg_temp.team_cosign_replace(updated,
  E'actor := public.team_cosign_finalizer(NEW.org_id,NEW.review_domain,NEW.card_id);\n            IF NEW.disposition',
  E'actor := public.team_cosign_disposition_human(NEW.org_id,NEW.review_domain,NEW.card_id);\n            IF NEW.disposition');
 IF updated=definition THEN RAISE EXCEPTION 'response revision gate source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.response_evidence_gate()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,'actor := public.response_require_human(NEW.org_id,domain);',
  'actor := public.team_cosign_finalizer(NEW.org_id,domain,NEW.card_id);');
 IF updated=definition THEN RAISE EXCEPTION 'response evidence gate source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.task_workflow_guard()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,
  'CASE WHEN NEW.state<>OLD.state THEN ''task:archive'' ELSE ''task:members:write'' END',
  'CASE WHEN NEW.state<>OLD.state THEN ''task:archive'' WHEN NEW.rule_revision<>OLD.rule_revision THEN ''task:review-policy'' ELSE ''task:members:write'' END');
 IF updated=definition THEN RAISE EXCEPTION 'task workflow guard source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.requirement_assignment_guard()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,
  ' uid=public.task_collaboration_human(NEW.org_id,NEW.task_id,''card:assign'');',
  $patch$
 IF TG_OP='INSERT' AND NEW.assignment_revision=0 THEN
  IF NEW.policy_revision=0 AND NEW.current_round_id IS NULL THEN
   RAISE EXCEPTION 'default workflow requires round or policy' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' AND (NEW.assignee_user_id,NEW.assignment_revision,NEW.changed_by_user_id,NEW.changed_at,NEW.last_reason_ciphertext)
  IS NOT DISTINCT FROM (OLD.assignee_user_id,OLD.assignment_revision,OLD.changed_by_user_id,OLD.changed_at,OLD.last_reason_ciphertext) THEN
  IF (NEW.org_id,NEW.id,NEW.task_id,NEW.requirement_id,NEW.extraction_job_id,NEW.created_at) IS DISTINCT FROM
    (OLD.org_id,OLD.id,OLD.task_id,OLD.requirement_id,OLD.extraction_job_id,OLD.created_at) THEN
   RAISE EXCEPTION 'immutable workflow identity' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 uid=public.task_collaboration_human(NEW.org_id,NEW.task_id,'card:assign');
$patch$);
 IF updated=definition THEN RAISE EXCEPTION 'assignment guard source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.response_item_gate()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,'citation_ok boolean; material_ok boolean;',
  'cosign_ok boolean; citation_ok boolean; material_ok boolean;');
 updated:=pg_temp.team_cosign_replace(updated,'citation_ok := public.response_citation_valid',
  E'cosign_ok := public.team_cosign_card_approved(NEW.org_id,NEW.card_id);\n      IF NEW.kind IN (''row'',''comply_only'') AND NOT cosign_ok THEN RAISE EXCEPTION ''cosign_required'' USING ERRCODE=''23514''; END IF;\n      citation_ok := public.response_citation_valid');
 updated:=pg_temp.team_cosign_replace(updated,'IF citation_ok AND quote_current AND (revision.disposition=',
  'IF cosign_ok AND citation_ok AND quote_current AND (revision.disposition=');
 updated:=pg_temp.team_cosign_replace(updated,'IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN',
  E'IF NEW.card_id IS NOT NULL AND NOT cosign_ok AND (jsonb_array_length(public.team_cosign_domains(NEW.org_id,NEW.card_id))>1 OR revision.state=''confirmed'' OR revision.disposition=''comply_only'' OR EXISTS(SELECT 1 FROM public.requirement_workflows rw JOIN public.response_cards c ON (c.org_id,c.task_id,c.extraction_job_id,c.requirement_id)=(rw.org_id,rw.task_id,rw.extraction_job_id,rw.requirement_id) WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id AND rw.current_round_id IS NOT NULL)) THEN expected_reasons := expected_reasons || ''["cosign_required"]''::jsonb; END IF;\n        IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN');
 IF updated=definition THEN RAISE EXCEPTION 'response item gate source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.score_inputs_current(uuid,uuid,uuid,integer)'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,'IF response_row.id IS NULL OR requirement_row.id IS NULL',
  'IF (response_row.kind<>''gap'' AND NOT public.team_cosign_card_approved(p_org,response_row.card_id)) OR response_row.id IS NULL OR requirement_row.id IS NULL');
 IF updated=definition THEN RAISE EXCEPTION 'score current inputs source changed'; END IF;
 EXECUTE updated;
 SELECT pg_get_functiondef('public.check_current_inputs(uuid,uuid)'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,'IF i.id IS NULL OR s.id IS NULL OR q.id IS NULL',
  'IF (s.kind<>''gap'' AND NOT public.team_cosign_card_approved(p_org,s.card_id)) OR i.id IS NULL OR s.id IS NULL OR q.id IS NULL');
 IF updated=definition THEN RAISE EXCEPTION 'check current inputs source changed'; END IF;
 EXECUTE updated;

END $$;
"""

INVALIDATION_SQL = r"""
CREATE FUNCTION public.team_cosign_invalidate_dependencies() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE rr public.card_review_rounds; payload jsonb; changed jsonb; why text;
 old_org text:=current_setting('app.current_org',true); can_derive boolean; uid uuid;
BEGIN
 changed:=(CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END);
 IF TG_TABLE_NAME='requirement_workflows' AND ((TG_OP='INSERT' AND (changed->>'policy_revision')::integer=0)
  OR (TG_OP='UPDATE' AND changed->>'policy_revision'=to_jsonb(OLD)->>'policy_revision')) THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='task_workflows' AND TG_OP='UPDATE' AND changed->>'rule_revision'=to_jsonb(OLD)->>'rule_revision' THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='task_workflows' AND TG_OP='INSERT' THEN RETURN NULL; END IF;
 why:=(CASE WHEN TG_TABLE_NAME IN ('memberships','users','orgs','task_members') THEN 'authority_lost'
  WHEN TG_TABLE_NAME='requirement_workflows' THEN 'policy_changed'
  WHEN TG_TABLE_NAME='task_workflows' THEN 'rule_changed'
  WHEN TG_TABLE_NAME='response_cards' THEN 'card_changed'
  WHEN TG_TABLE_NAME='requirements' THEN 'requirement_changed'
  WHEN TG_TABLE_NAME='chunks' THEN 'citation_changed' ELSE 'material_changed' END);
 SELECT r.rolsuper OR r.rolbypassrls INTO can_derive FROM pg_catalog.pg_roles r WHERE r.rolname=current_user;
 FOR rr IN SELECT r.* FROM public.card_review_rounds r
  WHERE NOT EXISTS(SELECT 1 FROM public.card_review_invalidations i WHERE i.org_id=r.org_id AND i.round_id=r.id)
   AND (changed->>'org_id' IS NULL OR r.org_id=(changed->>'org_id')::uuid)
   AND (changed->>'task_id' IS NULL OR r.task_id=(changed->>'task_id')::uuid)
  ORDER BY r.org_id,r.task_id,r.id LOOP
  IF can_derive THEN PERFORM set_config('app.current_org',rr.org_id::text,true); END IF;
  IF NOT public.team_cosign_round_valid(rr.org_id,rr.id) THEN
   uid:=nullif(current_setting('app.actor_user_id',true),'')::uuid;
   IF NOT EXISTS(SELECT 1 FROM public.memberships m WHERE m.org_id=rr.org_id AND m.user_id=uid) THEN uid:=NULL; END IF;
   INSERT INTO public.card_review_invalidations(org_id,task_id,card_id,round_id,cause,source_id,actor_user_id)
    VALUES(rr.org_id,rr.task_id,rr.card_id,rr.id,why,coalesce((changed->>'id')::uuid,(changed->>'owner_id')::uuid,rr.id),uid) ON CONFLICT DO NOTHING;
  END IF;
 END LOOP;
 IF can_derive THEN PERFORM set_config('app.current_org',coalesce(old_org,''),true); END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.team_cosign_event() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE source uuid; previous_org text:=current_setting('app.current_org',true); can_derive boolean;
BEGIN
 SELECT r.rolsuper OR r.rolbypassrls INTO can_derive FROM pg_catalog.pg_roles r WHERE r.rolname=current_user;
 IF can_derive THEN PERFORM set_config('app.current_org',NEW.org_id::text,true); END IF;
 SELECT c.extraction_job_id INTO source FROM public.response_cards c WHERE c.org_id=NEW.org_id AND c.id=NEW.card_id;
 PERFORM public.append_task_event(NEW.org_id,NEW.task_id,'board_changed',jsonb_build_object(
  'type','board_changed','invalidate_all',false,'extraction_job_id',source,
  'requirement_ids','[]'::jsonb,'card_ids',jsonb_build_array(NEW.card_id)),source);
 IF can_derive THEN PERFORM set_config('app.current_org',coalesce(previous_org,''),true); END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['card_review_rounds','card_review_signatures','card_review_invalidations'] LOOP
  EXECUTE format('CREATE TRIGGER zz_team_cosign_event AFTER INSERT ON public.%I FOR EACH ROW EXECUTE FUNCTION public.team_cosign_event()',tab);
 END LOOP;
 FOREACH tab IN ARRAY ARRAY['memberships','users','orgs','task_members','requirement_workflows','task_workflows','requirements','chunks',
  'task_resources','task_features','task_certificates','task_org_profiles','memories','memory_revisions','memory_scope_epochs'] LOOP
  EXECUTE format('CREATE TRIGGER team_cosign_dependency AFTER INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.team_cosign_invalidate_dependencies()',tab);
 END LOOP;
END $$;
CREATE TRIGGER team_cosign_dependency AFTER INSERT ON public.screenshot_withdrawals
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_invalidate_dependencies();
-- A card pointer may be flushed before its unchanged evidence links. Defer this
-- retirement materialization; every read/consumption gate still checks live inputs.
CREATE CONSTRAINT TRIGGER team_cosign_dependency AFTER UPDATE ON public.response_cards
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.team_cosign_invalidate_dependencies();
REVOKE ALL ON FUNCTION public.team_cosign_hash(jsonb),public.team_cosign_domains(uuid,uuid),
 public.team_cosign_signer_authorized(uuid,uuid,uuid,text),public.team_cosign_warnings(uuid,uuid),
 public.team_cosign_snapshot(uuid,uuid,uuid),public.team_cosign_round_valid(uuid,uuid),
 public.team_cosign_complete(uuid,uuid),public.team_cosign_card_approved(uuid,uuid),
 public.team_cosign_human(uuid,uuid,text),public.team_cosign_user_epoch(),public.team_cosign_policy_guard(),public.team_cosign_history_guard(),
 public.team_cosign_round_pointer(),public.team_cosign_finalizer(uuid,text,uuid),public.team_cosign_revision_gate(),
 public.team_cosign_submission_complete(),public.team_cosign_signature_complete(),public.team_cosign_disposition_human(uuid,text,uuid),
 public.team_cosign_invalidate_dependencies(),public.team_cosign_event() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.team_cosign_hash(jsonb),public.team_cosign_domains(uuid,uuid),
 public.team_cosign_signer_authorized(uuid,uuid,uuid,text),public.team_cosign_warnings(uuid,uuid),
 public.team_cosign_snapshot(uuid,uuid,uuid),public.team_cosign_round_valid(uuid,uuid),
 public.team_cosign_complete(uuid,uuid),public.team_cosign_card_approved(uuid,uuid),
 public.team_cosign_human(uuid,uuid,text),public.team_cosign_finalizer(uuid,text,uuid),
 public.team_cosign_disposition_human(uuid,text,uuid) TO bid_app;
"""


INVALIDATION_AUDIT_SQL = r"""
-- System revocation has no human org actor. Permit that one honest NULL actor
-- only from the insertion trigger for its exact immutable invalidation, never
-- from a request-controlled app.* flag or a fabricated round creator identity.
DO $$ DECLARE definition text; updated text; BEGIN
 SELECT pg_get_functiondef('public.agent_origin_guard()'::regprocedure) INTO definition;
 updated:=pg_temp.team_cosign_replace(definition,
  ' IF TG_TABLE_NAME=''audit_logs'' THEN NEW.actor_kind:=COALESCE(kind,''legacy_unknown''); END IF;',
  $patch$
 IF TG_TABLE_NAME='audit_logs' THEN
  IF NEW.action='task.review_round_invalidated' THEN
   IF pg_trigger_depth()<2 OR NOT EXISTS(SELECT 1 FROM public.card_review_invalidations inv
    WHERE inv.org_id=NEW.org_id AND inv.task_id=NEW.object_id
      AND inv.xmin=pg_current_xact_id()::xid
      AND inv.actor_user_id IS NOT DISTINCT FROM NEW.actor_user_id
      AND NEW.details=jsonb_build_object('task_id',inv.task_id,'card_id',inv.card_id,
       'round_id',inv.round_id,'invalidation_id',inv.id,'cause',inv.cause,'source_id',inv.source_id,
       'actor_user_id',inv.actor_user_id,'actor_kind',
        (CASE WHEN inv.actor_user_id IS NULL THEN 'system' ELSE coalesce(kind,'legacy_unknown') END))) THEN
    RAISE EXCEPTION 'invalidation audit requires its immutable trigger source' USING ERRCODE='42501'; END IF;
   IF NEW.actor_user_id IS NULL THEN
    NEW.actor_kind:='system'; NEW.initiated_by:='legacy_unknown';
    NEW.actor_token_id:=NULL; NEW.on_behalf_of_user_id:=NULL;
    NEW.agent_principal_id:=NULL; NEW.agent_session_id:=NULL; NEW.agent_step_id:=NULL;
    NEW.job_id:=NULL; NEW.run_id:=NULL; NEW.invocation_id:=NULL; NEW.command:=NULL;
    RETURN NEW;
   END IF;
  END IF;
  NEW.actor_kind:=COALESCE(kind,'legacy_unknown');
 END IF;
$patch$);
 EXECUTE updated;
END $$;
CREATE FUNCTION public.team_cosign_invalidation_audit() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE audit_actor_kind text:=(CASE WHEN NEW.actor_user_id IS NULL THEN 'system'
 ELSE coalesce(nullif(current_setting('app.actor_kind',true),''),'legacy_unknown') END);
BEGIN
 INSERT INTO public.audit_logs(id,org_id,actor_user_id,actor_token_id,action,object_id,details,job_id,run_id)
 VALUES(gen_random_uuid(),NEW.org_id,NEW.actor_user_id,
   CASE WHEN NEW.actor_user_id IS NULL THEN NULL ELSE nullif(current_setting('app.actor_token_id',true),'')::uuid END,
   'task.review_round_invalidated',NEW.task_id,
   jsonb_build_object('task_id',NEW.task_id,'card_id',NEW.card_id,'round_id',NEW.round_id,
    'invalidation_id',NEW.id,'cause',NEW.cause,'source_id',NEW.source_id,
    'actor_user_id',NEW.actor_user_id,'actor_kind',audit_actor_kind),
   CASE WHEN NEW.actor_user_id IS NULL THEN NULL ELSE nullif(current_setting('app.execution_job_id',true),'')::uuid END,
   CASE WHEN NEW.actor_user_id IS NULL THEN NULL ELSE nullif(current_setting('app.execution_run_id',true),'')::uuid END);
 RETURN NULL;
END $$;
CREATE TRIGGER aa_team_cosign_invalidation_audit AFTER INSERT ON public.card_review_invalidations
 FOR EACH ROW EXECUTE FUNCTION public.team_cosign_invalidation_audit();
REVOKE ALL ON FUNCTION public.team_cosign_invalidation_audit() FROM PUBLIC;
DROP FUNCTION pg_temp.team_cosign_replace(text,text,text);
"""


def downgrade():
    raise RuntimeError("Retain human co-sign history; disable writes and repair forward")
