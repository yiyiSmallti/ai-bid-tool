"""Import branches for requirement review and its gates (ADR 0014).

An imported review row keeps its extraction, document and pin bindings; the
seed and human authority and the review order belong to the installation that
wrote it. A draft or card revision imported with its task was current there.
An imported review leaves its set at the revision the package carries.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION public.requirement_draft_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 -- An imported draft was current when its source made it.
 IF public.package_imported(NEW.task_id) THEN RETURN NULL; END IF;
 IF NOT public.requirement_draft_current(NEW.id) THEN RAISE EXCEPTION 'requirement review manifest is stale' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION public.requirement_response_gate()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE rid uuid; previous public.response_card_revisions;
BEGIN
 -- An imported revision was confirmed against its source's requirement review.
 IF public.package_imported((SELECT task_id FROM public.response_cards WHERE id=NEW.card_id)) THEN RETURN NEW; END IF;
 SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
 SELECT requirement_id INTO rid FROM public.response_cards WHERE id=NEW.card_id;
 IF ((NEW.state='confirmed' AND previous.state IS DISTINCT FROM 'confirmed') OR (NEW.disposition='comply_only' AND previous.disposition IS DISTINCT FROM 'comply_only')) AND NOT public.requirement_review_current(rid) THEN
 RAISE EXCEPTION 'requirement_unconfirmed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.requirement_review_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE r public.requirements; j public.jobs;
 e public.requirement_review_events; previous public.requirement_review_events;
BEGIN
 -- An imported review row keeps its bindings; its actors and review order belong to the source.
 IF TG_OP='INSERT' AND public.package_imported(NEW.task_id) THEN
  IF TG_TABLE_NAME='requirement_review_sets' THEN
   SELECT * INTO j FROM public.jobs WHERE id=NEW.extraction_job_id;
   IF j.kind IS DISTINCT FROM 'extract' OR j.status IS DISTINCT FROM 'succeeded' OR j.task_id IS DISTINCT FROM NEW.task_id OR j.document_id IS DISTINCT FROM NEW.document_id
   OR (NEW.origin='manual') IS DISTINCT FROM (coalesce(j.result->>'origin','model')='manual') THEN RAISE EXCEPTION 'invalid review extraction scope' USING ERRCODE='23514'; END IF;
  ELSIF TG_TABLE_NAME='requirement_reviews' THEN
   SELECT * INTO r FROM public.requirements WHERE id=NEW.requirement_id;
   SELECT * INTO j FROM public.jobs WHERE id=NEW.extraction_job_id;
   IF r.document_id IS DISTINCT FROM j.document_id OR r.task_id IS DISTINCT FROM j.task_id OR r.job_id IS DISTINCT FROM j.id
    OR r.task_id IS DISTINCT FROM NEW.task_id THEN
    RAISE EXCEPTION 'requirement extraction document binding differs' USING ERRCODE='23514'; END IF;
   IF NEW.source_pin IS NOT NULL AND NOT public.requirement_pin_valid(NEW.requirement_id,NEW.source_pin) THEN
    RAISE EXCEPTION 'unverified review source pin' USING ERRCODE='23514'; END IF;
   IF NEW.rejected_job_id IS NOT NULL THEN
    SELECT * INTO j FROM public.jobs WHERE id=NEW.rejected_job_id;
    IF j.task_id IS DISTINCT FROM r.task_id OR j.document_id IS DISTINCT FROM r.document_id OR j.kind<>'extract'
     OR public.requirement_digest(j.result->'rejected'->NEW.rejected_index) IS DISTINCT FROM NEW.rejected_summary_sha256 THEN
     RAISE EXCEPTION 'rejected reference changed' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='UPDATE' AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID))
 AND (to_jsonb(NEW)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext'])=(to_jsonb(OLD)-ARRAY['snapshot_ciphertext','reason_ciphertext','receipt_ciphertext']) THEN RETURN NEW; END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'requirement review history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='requirement_review_sets' THEN
  IF TG_OP='INSERT' THEN
   SELECT * INTO j FROM public.jobs WHERE id=NEW.extraction_job_id;
   IF j.kind IS DISTINCT FROM 'extract' OR j.status IS DISTINCT FROM 'succeeded' OR j.task_id IS DISTINCT FROM NEW.task_id OR j.document_id IS DISTINCT FROM NEW.document_id
   OR (NEW.origin='manual') IS DISTINCT FROM (coalesce(j.result->>'origin','model')='manual') THEN RAISE EXCEPTION 'invalid review extraction scope' USING ERRCODE='23514'; END IF;
   PERFORM public.requirement_seed_authority(NEW.task_id,NEW.extraction_job_id,NEW.origin='manual');
  ELSIF pg_trigger_depth()<2 OR ROW(NEW.id,NEW.task_id,NEW.extraction_job_id,NEW.document_id,NEW.origin)
   IS DISTINCT FROM ROW(OLD.id,OLD.task_id,OLD.extraction_job_id,OLD.document_id,OLD.origin) THEN
   RAISE EXCEPTION 'review set updates are producer-owned' USING ERRCODE='42501';
  END IF;
 ELSIF TG_TABLE_NAME='requirement_review_requests' THEN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable review receipt' USING ERRCODE='42501'; END IF;
  PERFORM public.requirement_human_authority(NEW.task_id,CASE WHEN NEW.action='manual_add' THEN 'req:manual' ELSE 'req:confirm' END);
 ELSIF TG_TABLE_NAME='requirement_review_events' THEN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'immutable review event' USING ERRCODE='42501'; END IF;
  IF NEW.action IN ('confirm','reopen','manual_add') THEN
   PERFORM public.requirement_human_authority(NEW.task_id,CASE WHEN NEW.action='manual_add' THEN 'req:manual' ELSE 'req:confirm' END);
   IF NEW.actor_kind<>'session' OR NOT public.actor_is_human() OR NEW.request_id IS NULL
    OR NEW.reason_sha256 IS NULL OR NEW.reason_ciphertext IS NULL THEN RAISE EXCEPTION 'human event binding required' USING ERRCODE='42501'; END IF;
  ELSIF NEW.action='seed' THEN
   PERFORM public.requirement_seed_authority(NEW.task_id,NEW.extraction_job_id,false);
   IF NOT (NEW.actor_kind='worker' OR
 (NEW.actor_kind='session' AND public.actor_is_human() AND NEW.state_after='legacy_unconfirmed' AND EXISTS(
 SELECT 1 FROM public.requirement_reviews v WHERE v.id=NEW.review_id AND v.origin='legacy')))
 THEN RAISE EXCEPTION 'invalid seed actor' USING ERRCODE='42501'; END IF;
  ELSIF pg_trigger_depth()<2 THEN RAISE EXCEPTION 'invalidation requires source producer' USING ERRCODE='42501'; END IF;
  IF NEW.action NOT IN ('invalidate','source_repair') AND NEW.snapshot_sha256 IS DISTINCT FROM public.requirement_digest(public.requirement_content(NEW.requirement_id)) THEN
   RAISE EXCEPTION 'review snapshot content differs' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='requirement_reviews' THEN
  SELECT * INTO r FROM public.requirements WHERE id=NEW.requirement_id;
  SELECT * INTO j FROM public.jobs WHERE id=NEW.extraction_job_id;
  IF r.document_id IS DISTINCT FROM j.document_id OR r.task_id IS DISTINCT FROM j.task_id OR r.job_id IS DISTINCT FROM j.id THEN
   RAISE EXCEPTION 'requirement extraction document binding differs' USING ERRCODE='23514'; END IF;
  IF TG_OP='INSERT' THEN
   PERFORM public.requirement_seed_authority(NEW.task_id,NEW.extraction_job_id,NEW.origin LIKE 'manual_%');
   IF (current_setting('app.actor_kind',true)='session' AND NEW.origin='extracted') OR
 (NEW.origin='legacy' AND NEW.state<>'legacy_unconfirmed') OR NEW.state NOT IN ('unconfirmed','legacy_unconfirmed') OR NEW.revision<>1 THEN RAISE EXCEPTION 'seed cannot approve' USING ERRCODE='42501'; END IF;
  ELSE
   IF ROW(NEW.id,NEW.task_id,NEW.extraction_job_id,NEW.requirement_id,NEW.origin)
    IS DISTINCT FROM ROW(OLD.id,OLD.task_id,OLD.extraction_job_id,OLD.requirement_id,OLD.origin) THEN
    RAISE EXCEPTION 'review identity is immutable' USING ERRCODE='42501'; END IF;
   IF pg_trigger_depth()<2 THEN
    PERFORM public.requirement_human_authority(NEW.task_id,'req:confirm');
    IF NEW.revision<>OLD.revision+1 OR (NEW.state='confirmed' AND OLD.state='confirmed') OR
      (NEW.state='unconfirmed' AND OLD.state<>'confirmed') OR NEW.state NOT IN ('confirmed','unconfirmed') THEN
      RAISE EXCEPTION 'invalid review transition' USING ERRCODE='23514'; END IF;
    IF NEW.review_hash IS DISTINCT FROM public.requirement_hash(NEW.requirement_id,NEW.source_pin) THEN
      RAISE EXCEPTION 'review hash mismatch' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
  IF NEW.source_pin IS NOT NULL AND (TG_OP='INSERT' OR pg_trigger_depth()<2) AND (NOT public.requirement_pin_valid(NEW.requirement_id,NEW.source_pin)
 OR NEW.source_pin IS DISTINCT FROM public.requirement_current_pin(NEW.requirement_id)) THEN
   RAISE EXCEPTION 'unverified review source pin' USING ERRCODE='23514'; END IF;
  IF NEW.confirmed_at IS NOT NULL AND NOT public.actor_is_human() THEN RAISE EXCEPTION 'confirmer mismatch' USING ERRCODE='42501'; END IF;
  IF NEW.rejected_job_id IS NOT NULL THEN
   SELECT * INTO j FROM public.jobs WHERE id=NEW.rejected_job_id;
   IF j.task_id IS DISTINCT FROM r.task_id OR j.document_id IS DISTINCT FROM r.document_id OR j.kind<>'extract'
    OR public.requirement_digest(j.result->'rejected'->NEW.rejected_index) IS DISTINCT FROM NEW.rejected_summary_sha256 THEN
    RAISE EXCEPTION 'rejected reference changed' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.requirement_review_changed()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF TG_OP='UPDATE' AND (to_jsonb(NEW)-'snapshot_ciphertext')=(to_jsonb(OLD)-'snapshot_ciphertext') THEN RETURN NULL; END IF;
 UPDATE public.requirement_review_sets s SET revision=revision+CASE WHEN TG_OP='INSERT' AND (NEW.origin='legacy' OR public.package_imported(NEW.task_id)) THEN 0 ELSE 1 END,
 membership_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(r.id ORDER BY r.id),'[]')) FROM public.requirements r WHERE r.job_id=s.extraction_job_id),
 confirmation_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(jsonb_build_array(v.requirement_id,v.revision,v.review_hash,v.state) ORDER BY v.requirement_id),'[]')) FROM public.requirement_reviews v WHERE v.extraction_job_id=s.extraction_job_id)
 WHERE s.extraction_job_id=NEW.extraction_job_id;
 PERFORM public.append_task_event(NEW.task_id,'board_changed',jsonb_build_object('type','board_changed',
 'extraction_job_id',NEW.extraction_job_id,'requirement_ids',jsonb_build_array(NEW.requirement_id),'card_ids','[]'::jsonb,'invalidate_all',false),NULL);
 RETURN NULL;
END $function$;
"""
    )


def downgrade():
    raise NotImplementedError("Package import branches have no earlier representation")
