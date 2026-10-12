-- FUNCTION: annotation_actor_live(uuid)
CREATE FUNCTION public.annotation_actor_live(p_task uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.tasks WHERE id=p_task)
$$;

-- FUNCTION: annotation_ascii(text)
CREATE FUNCTION public.annotation_ascii(p_value text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $$
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

-- FUNCTION: annotation_binding_guard()
CREATE FUNCTION public.annotation_binding_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE q public.annotation_requests; m public.annotation_materials; r public.screenshot_renditions;
 j public.jobs; c public.response_cards;
BEGIN
 -- Immediate RI constraints precede this AFTER guard.
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'task_archived' USING ERRCODE='23514'; END IF;
 SELECT * INTO c FROM public.response_cards WHERE id=NEW.card_id FOR UPDATE;
 SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
 IF j.document_id IS DISTINCT FROM (SELECT document_id FROM public.jobs WHERE id=NEW.extraction_job_id) THEN
  RAISE EXCEPTION 'Annotation job must retain the tender document' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='annotation_requests' THEN
  PERFORM public.task_write_authority(NEW.task_id,'evidence:annotate',NULL,false,true);
  IF NOT public.actor_is_human() OR NOT public.annotation_actor_live(NEW.task_id) THEN
   RAISE EXCEPTION 'Human annotation authority required' USING ERRCODE='42501'; END IF;
  IF NEW.source_kind<>'certificate_page' THEN RAISE EXCEPTION 'vendor_source_not_enabled' USING ERRCODE='23514'; END IF;
  IF NEW.input_hash IS DISTINCT FROM public.annotation_digest(NEW.manifest)
    OR NEW.plan_sha256 IS DISTINCT FROM public.annotation_digest(NEW.plan)
    OR NEW.privacy_attestation IS DISTINCT FROM jsonb_build_object('reviewed_source_sha256',NEW.reviewed_source_sha256)
    OR NEW.manifest->>'policy_version' IS DISTINCT FROM 'annotation-binding-v1'
    OR NEW.manifest#>>'{target,source,kind}' IS DISTINCT FROM NEW.source_kind
    OR NEW.manifest#>>'{target,source,evidence_source_id}' IS DISTINCT FROM NEW.evidence_source_id::text
    OR NEW.manifest#>>'{source,kind}' IS DISTINCT FROM NEW.source_kind
    OR NEW.manifest#>>'{source,page_kind}' IS DISTINCT FROM 'pdf_page'
    OR NEW.manifest#>>'{source,archive,task_id}' IS DISTINCT FROM NEW.task_id::text
    OR NEW.manifest->>'card_content_sha256' IS DISTINCT FROM (SELECT public.annotation_digest(jsonb_build_object(
      'response_kind',v.response_kind,'response_text',v.response_text,'deviation',v.deviation,'deviation_note',v.deviation_note,
      'review_domain',v.review_domain,'disposition',v.disposition,'quote_sha256',v.quote_sha256))
      FROM public.response_card_revisions v WHERE v.id=NEW.expected_card_revision_id)
    OR NEW.manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
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
    OR j.kind<>'annotation_render' OR j.actor_kind IS DISTINCT FROM 'session'
    OR j.actor_token_id IS NOT NULL OR j.agent_session_id IS NOT NULL OR j.status<>'queued'
    OR j.result#>>'{submission,annotation_request_id}' IS DISTINCT FROM NEW.id::text
    OR j.result#>'{submission,input_manifest}' IS DISTINCT FROM NEW.manifest
    OR j.result#>>'{submission,input_hash}' IS DISTINCT FROM NEW.input_hash
    OR NOT public.annotation_source_current(NEW.id) THEN
   RAISE EXCEPTION 'Annotation request binding mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='annotation_materials' THEN
  SELECT * INTO q FROM public.annotation_requests WHERE id=NEW.request_id;
  SELECT * INTO r FROM public.screenshot_renditions WHERE id=NEW.rendition_id;
  IF NOT public.annotation_job_live(NEW.job_id,NEW.run_id,'annotation_render')
    OR NOT public.annotation_actor_live(NEW.task_id)
    OR NOT public.annotation_source_current(q.id)
    OR NOT(j.actor_scopes ? 'evidence:annotate')
    OR q.job_id<>NEW.job_id OR q.input_hash<>NEW.input_hash OR q.manifest<>NEW.manifest
    OR c.current_revision_id<>q.expected_card_revision_id OR c.revision<>q.expected_card_revision
    OR r.profile<>'annotation-candidate-v1' OR r.generation_job_id IS DISTINCT FROM NEW.job_id
    OR r.renderer_identity<>q.renderer OR r.plan_sha256<>q.plan_sha256
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
  SELECT * INTO m FROM public.annotation_materials WHERE id=NEW.annotation_id;
  SELECT * INTO r FROM public.screenshot_renditions WHERE id=NEW.rendition_id;
  IF NOT public.annotation_job_live(NEW.job_id,NEW.run_id,'annotation_release')
    OR NOT public.annotation_release_current(NEW.id)
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

-- FUNCTION: annotation_certificate_asset(uuid, uuid, uuid, uuid, text, uuid)
CREATE FUNCTION public.annotation_certificate_asset(p_asset uuid, p_task uuid, p_extraction uuid, p_source uuid, p_hash text, p_request uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE q public.annotation_requests; j public.jobs;
BEGIN
 SELECT * INTO q FROM public.annotation_requests WHERE id=p_request;
 SELECT * INTO j FROM public.jobs WHERE id=q.job_id;
 IF q.id IS NULL OR q.task_id<>p_task OR q.extraction_job_id<>p_extraction OR q.evidence_source_id<>p_source
 OR q.reviewed_source_sha256<>p_hash
 OR NOT public.annotation_job_live(j.id,j.run_id,'annotation_render')
 OR NOT public.annotation_actor_live(p_task) OR NOT public.annotation_source_current(q.id) THEN
  RAISE EXCEPTION 'Cloud certificate requires exact human source attestation' USING ERRCODE='23514'; END IF;
END $$;

-- FUNCTION: annotation_certificate_privacy(uuid, uuid, uuid, uuid, uuid, text, text, text)
CREATE FUNCTION public.annotation_certificate_privacy(p_task uuid, p_asset uuid, p_rendition uuid, p_review uuid, p_request uuid, p_upload text, p_image text, p_rule text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE q public.annotation_requests; r public.screenshot_renditions; a public.screenshot_assets;
BEGIN
 SELECT * INTO q FROM public.annotation_requests WHERE id=p_request;
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=p_rendition;
 SELECT * INTO a FROM public.screenshot_assets WHERE id=p_asset;
 PERFORM public.annotation_certificate_asset(p_asset,p_task,r.extraction_job_id,a.evidence_source_id,p_upload,p_request);
 IF r.id IS NULL OR r.asset_id<>p_asset OR r.privacy_review_id<>p_review OR r.parent_rendition_id IS NOT NULL
  OR r.image_sha256<>p_image OR r.upload_sha256<>p_upload OR r.profile<>'annotation-candidate-v1'
  OR p_rule<>'annotation-certificate-privacy-v1' OR a.source->>'annotation_request_id' IS DISTINCT FROM p_request::text THEN
  RAISE EXCEPTION 'Cloud privacy lineage mismatch' USING ERRCODE='23514'; END IF;
END $$;

-- FUNCTION: annotation_dependency_event()
CREATE FUNCTION public.annotation_dependency_event() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM public.annotation_invalidate_dependencies(to_jsonb(NEW));
 RETURN NULL;
END $$;

-- FUNCTION: annotation_digest(jsonb)
CREATE FUNCTION public.annotation_digest(p_value jsonb) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT encode(sha256(convert_to(public.annotation_ascii(public.requirement_canonical(p_value)),'UTF8')),'hex')
$$;

-- FUNCTION: annotation_event()
CREATE FUNCTION public.annotation_event() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM public.append_task_event(NEW.task_id,'board_changed',jsonb_build_object('type','board_changed',
  'invalidate_all',false,'extraction_job_id',NEW.extraction_job_id,'requirement_ids',jsonb_build_array(NEW.requirement_id),
  'card_ids',jsonb_build_array(NEW.card_id)),NEW.extraction_job_id);
 RETURN NULL;
END $$;

-- FUNCTION: annotation_evidence_gate()
CREATE FUNCTION public.annotation_evidence_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.screenshot_renditions; m public.annotation_materials;
BEGIN
 IF NEW.kind<>'image_region' THEN RETURN NEW; END IF;
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=NEW.screenshot_rendition_id;
 IF r.profile='annotation-release-v1' THEN RAISE EXCEPTION 'Release cannot become new evidence' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-candidate-v1' THEN
  SELECT * INTO m FROM public.annotation_materials WHERE rendition_id=r.id;
  IF m.id IS NULL OR m.task_id<>NEW.task_id OR NOT public.annotation_source_current(m.request_id)
    OR (NEW.confirmed_at IS NOT NULL AND NOT public.requirement_review_current((SELECT requirement_id FROM public.response_cards WHERE id=NEW.card_id))) THEN
   RAISE EXCEPTION 'Annotation source or requirement is stale' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: annotation_export_binding(uuid, uuid, jsonb)
CREATE FUNCTION public.annotation_export_binding(p_evidence uuid, p_revision uuid, p_attachment jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE e public.evidence; m public.annotation_materials; rel public.annotation_releases; r public.screenshot_renditions;
BEGIN
 SELECT * INTO e FROM public.evidence WHERE id=p_evidence;
 SELECT * INTO m FROM public.annotation_materials WHERE rendition_id=e.screenshot_rendition_id;
 IF m.id IS NULL THEN RETURN NOT(p_attachment ? 'annotation_release_id'); END IF;
 SELECT * INTO rel FROM public.annotation_releases WHERE id=(p_attachment->>'annotation_release_id')::uuid;
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=rel.rendition_id;
 RETURN coalesce(rel.id IS NOT NULL AND rel.annotation_id=m.id AND rel.evidence_id=e.id AND rel.card_revision_id=p_revision
  AND public.annotation_release_current(rel.id)
  AND p_attachment->>'annotation_approval_sha256'=rel.approval_sha256
  AND p_attachment->>'annotation_release_rendition_id'=r.id::text AND p_attachment->>'annotation_release_image_sha256'=r.image_sha256
  AND p_attachment->>'annotation_content_pixel_sha256'=m.content_pixel_sha256
  AND rel.content_pixel_sha256=m.content_pixel_sha256 AND rel.root_mapping_sha256=m.root_mapping_sha256,false);
END $$;

-- FUNCTION: annotation_invalidate_dependencies(jsonb)
CREATE FUNCTION public.annotation_invalidate_dependencies(p_changed jsonb) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE item record; payload jsonb;
BEGIN
 FOR item IN
  SELECT m.task_id,m.extraction_job_id,m.card_id,m.id AS object_id,m.id AS annotation_id,
    NULL::uuid AS release_id,m.request_id,'source_changed'::text AS cause
  FROM public.annotation_materials m
  WHERE (p_changed->>'task_id' IS NULL OR m.task_id=(p_changed->>'task_id')::uuid)
   AND NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.object_id=m.id AND a.action='annotation.invalidate')
  UNION ALL
  SELECT r.task_id,r.extraction_job_id,r.card_id,r.id,r.annotation_id,r.id,NULL::uuid,'approval_stale'::text
  FROM public.annotation_releases r
  WHERE (p_changed->>'task_id' IS NULL OR r.task_id=(p_changed->>'task_id')::uuid)
   AND NOT EXISTS(SELECT 1 FROM public.audit_logs a WHERE a.object_id=r.id AND a.action='annotation.invalidate')
  ORDER BY task_id,object_id LOOP
  IF (item.release_id IS NULL AND NOT public.annotation_source_current(item.request_id))
   OR (item.release_id IS NOT NULL AND NOT public.annotation_release_current(item.release_id)) THEN
   payload:=jsonb_build_object('task_id',item.task_id,'extraction_job_id',item.extraction_job_id,'card_id',item.card_id,
    'annotation_id',item.annotation_id,'release_id',item.release_id,'cause',item.cause,'actor_kind','system');
   INSERT INTO public.audit_logs(id,actor_token_id,action,object_id,details)
    VALUES(gen_random_uuid(),NULL,'annotation.invalidate',item.object_id,payload)
    ON CONFLICT(object_id) WHERE action='annotation.invalidate' DO NOTHING;
   IF FOUND THEN
    PERFORM public.append_task_event(item.task_id,'board_changed',jsonb_build_object('type','board_changed',
     'invalidate_all',false,'extraction_job_id',item.extraction_job_id,'requirement_ids','[]'::jsonb,
     'card_ids',jsonb_build_array(item.card_id)),item.extraction_job_id);
   END IF;
  END IF;
 END LOOP;
END $$;

-- FUNCTION: annotation_invalidation_audit_valid(uuid, jsonb)
CREATE FUNCTION public.annotation_invalidation_audit_valid(p_object uuid, p_details jsonb) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.annotation_materials m WHERE m.id=p_object
  AND NOT public.annotation_source_current(m.request_id)
  AND p_details=jsonb_build_object('task_id',m.task_id,'extraction_job_id',m.extraction_job_id,'card_id',m.card_id,
    'annotation_id',m.id,'release_id',NULL,'cause','source_changed','actor_kind','system'))
 OR EXISTS(SELECT 1 FROM public.annotation_releases r WHERE r.id=p_object
  AND NOT public.annotation_release_current(r.id)
  AND p_details=jsonb_build_object('task_id',r.task_id,'extraction_job_id',r.extraction_job_id,'card_id',r.card_id,
    'annotation_id',r.annotation_id,'release_id',r.id,'cause','approval_stale','actor_kind','system'))
$$;

-- FUNCTION: annotation_job_live(uuid, uuid, text)
CREATE FUNCTION public.annotation_job_live(p_job uuid, p_run uuid, p_kind text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce((SELECT j.kind=p_kind AND j.status='running' AND j.run_id=p_run AND j.lease_until>clock_timestamp()
 AND j.actor_kind='session' AND j.actor_token_id IS NULL
 AND current_setting('app.actor_kind',true)='worker'
 AND j.id=nullif(current_setting('app.execution_job_id',true),'')::uuid
 AND j.run_id=nullif(current_setting('app.execution_run_id',true),'')::uuid
 FROM public.jobs j WHERE j.id=p_job),false)
$$;

-- FUNCTION: annotation_prior_evidence_active(uuid)
CREATE FUNCTION public.annotation_prior_evidence_active(p_evidence uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
  SELECT CASE e.kind
   WHEN 'product' THEN EXISTS(SELECT 1 FROM public.task_resources s
     WHERE s.id=e.task_resource_id AND s.active)
   WHEN 'feature' THEN EXISTS(SELECT 1 FROM public.task_features s
     WHERE s.id=e.task_feature_id AND s.active)
   WHEN 'certificate' THEN EXISTS(SELECT 1 FROM public.task_certificates s
     WHERE s.id=e.task_certificate_id AND s.active)
   WHEN 'certificate_pdf_page' THEN EXISTS(SELECT 1 FROM public.task_certificates s
     WHERE s.id=e.task_certificate_id AND s.active)
   WHEN 'org_profile' THEN EXISTS(SELECT 1 FROM public.task_org_profiles s
     WHERE s.id=e.task_org_profile_id AND s.active)
   WHEN 'image_region' THEN
     NOT EXISTS(SELECT 1 FROM public.screenshot_withdrawals w
       WHERE w.asset_id=e.screenshot_asset_id)
     AND EXISTS(
       SELECT 1 FROM public.screenshot_assets a
       JOIN public.screenshot_renditions r
         ON r.task_id=a.task_id AND r.asset_id=a.id
       JOIN public.response_cards c
         ON c.task_id=e.task_id AND c.id=e.card_id
        AND c.extraction_job_id=a.extraction_job_id
       JOIN public.screenshot_privacy_reviews p
         ON p.asset_id=r.asset_id AND p.id=r.privacy_review_id
       JOIN public.screenshot_renditions released
         ON released.asset_id=p.asset_id
        AND released.id=p.rendition_id AND released.image_sha256=p.stored_image_sha256
       WHERE a.task_id=e.task_id AND a.id=e.screenshot_asset_id
         AND r.id=e.screenshot_rendition_id AND r.image_sha256=e.image_sha256
         AND (e.material_kind NOT IN ('user_screenshot','browser_screenshot')
           OR a.source->>'environment' IN ('production','test','development'))
         AND (e.material_kind<>'prototype' OR a.source_kind='prototype_render'
           OR a.source->>'environment'='prototype')
     )
     AND CASE
       WHEN e.material_kind IN ('user_screenshot','browser_screenshot','user_diagram','prototype')
         THEN EXISTS(SELECT 1 FROM public.task_features s
           WHERE s.id=e.task_feature_id
             AND s.feature_revision_id=e.feature_revision_id AND s.active)
       WHEN e.material_kind IN ('vendor_web','vendor_pdf')
         THEN EXISTS(SELECT 1 FROM public.task_resources s
           WHERE s.id=e.task_resource_id
             AND s.product_revision_id=e.product_revision_id AND s.active)
       WHEN e.material_kind='certificate_image'
         THEN EXISTS(SELECT 1 FROM public.task_certificates s
           WHERE s.id=e.task_certificate_id
             AND s.certificate_revision_id=e.certificate_revision_id AND s.active)
       ELSE false END
   ELSE false END
  FROM public.evidence e WHERE e.id=p_evidence
$$;

-- FUNCTION: annotation_publication_complete()
CREATE FUNCTION public.annotation_publication_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.screenshot_renditions;
BEGIN
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=NEW.id;
 IF r.profile='annotation-candidate-v1' AND NOT EXISTS(
  SELECT 1 FROM public.annotation_materials m JOIN public.annotation_requests q ON (q.id)=(m.request_id)
  JOIN public.screenshot_privacy_reviews p ON (p.id)=(r.privacy_review_id)
  WHERE m.rendition_id=r.id AND m.job_id=r.generation_job_id
   AND p.annotation_request_id=q.id AND p.asset_id=r.asset_id AND p.rendition_id=r.id
   AND p.stored_image_sha256=r.image_sha256 AND p.reviewed_upload_sha256=q.reviewed_source_sha256
 ) THEN RAISE EXCEPTION 'Incomplete annotation candidate publication' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-release-v1' AND NOT EXISTS(
  SELECT 1 FROM public.annotation_releases rel WHERE rel.rendition_id=r.id AND rel.job_id=r.generation_job_id
 ) THEN RAISE EXCEPTION 'Incomplete annotation release publication' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: annotation_release_current(uuid)
CREATE FUNCTION public.annotation_release_current(p_release uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.annotation_releases; m public.annotation_materials; candidate public.screenshot_renditions;
 e public.evidence; c public.response_cards; v public.response_card_revisions; b public.requirement_reviews;
BEGIN
 SELECT * INTO r FROM public.annotation_releases WHERE id=p_release;
 IF r.id IS NULL THEN RETURN false; END IF;
 SELECT * INTO m FROM public.annotation_materials WHERE id=r.annotation_id;
 SELECT * INTO candidate FROM public.screenshot_renditions WHERE id=m.rendition_id;
 SELECT * INTO e FROM public.evidence WHERE id=r.evidence_id;
 SELECT * INTO c FROM public.response_cards WHERE id=r.card_id;
 SELECT * INTO v FROM public.response_card_revisions WHERE id=r.card_revision_id;
 SELECT * INTO b FROM public.requirement_reviews WHERE id=r.requirement_review_id;
 IF m.id IS NULL OR e.id IS NULL OR c.id IS NULL OR v.id IS NULL OR b.id IS NULL
  OR NOT public.annotation_source_current(m.request_id)
  OR NOT public.requirement_review_current(r.requirement_id)
  OR b.state<>'confirmed' OR b.revision<>r.requirement_review_revision
  OR e.kind IS DISTINCT FROM 'image_region'
  OR c.current_revision_id<>v.id OR e.confirmed_at IS NULL
  OR v.state<>'confirmed' OR v.confirmed_at IS NULL
  OR e.screenshot_asset_id IS DISTINCT FROM m.asset_id OR e.screenshot_rendition_id IS DISTINCT FROM m.rendition_id OR e.image_sha256 IS DISTINCT FROM candidate.image_sha256
  OR r.content_pixel_sha256<>m.content_pixel_sha256 OR r.root_mapping_sha256<>m.root_mapping_sha256
  OR r.approval->>'approval_binding_sha256' IS DISTINCT FROM r.approval_sha256
  OR r.approval->>'evidence_id' IS DISTINCT FROM e.id::text OR r.approval->>'card_id' IS DISTINCT FROM c.id::text
  OR r.approval->>'card_revision_id' IS DISTINCT FROM v.id::text OR r.approval->>'card_revision' IS DISTINCT FROM v.revision::text
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
  OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE asset_id=m.asset_id)
  OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE revision_id=v.id AND evidence_id=e.id)
 THEN RETURN false; END IF;
 RETURN true;
END $$;

-- FUNCTION: annotation_rendition_valid(uuid)
CREATE FUNCTION public.annotation_rendition_valid(p_id uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.screenshot_renditions; a public.screenshot_assets; p public.screenshot_renditions;
 j public.jobs; q public.annotation_requests; crop jsonb; box jsonb; w integer; h integer; cw integer; ch integer; ox integer; fh integer;
BEGIN
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=p_id;
 SELECT * INTO a FROM public.screenshot_assets WHERE id=r.asset_id;
 SELECT * INTO j FROM public.jobs WHERE id=r.generation_job_id;
 IF NOT public.annotation_job_live(j.id,j.run_id,(CASE r.profile WHEN 'annotation-candidate-v1' THEN 'annotation_render' ELSE 'annotation_release' END))
 OR a.origin NOT IN ('certificate','vendor') OR r.source_sha256<>a.source_sha256
 OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE asset_id=a.id)
 OR r.renderer_identity->>'profile' IS DISTINCT FROM r.profile
 OR r.renderer_identity->>'protocol_version' IS DISTINCT FROM 'annotation-render-v1'
 OR r.storage_key IS DISTINCT FROM 'screenshots/'||a.id::text||'/'||r.id::text||'/'||r.image_sha256||'.png' THEN
  RAISE EXCEPTION 'Invalid cloud rendition source' USING ERRCODE='23514'; END IF;
 IF r.profile='annotation-candidate-v1' THEN
  SELECT * INTO q FROM public.annotation_requests WHERE job_id=j.id;
  IF q.id IS NULL OR NOT public.annotation_source_current(q.id) OR q.renderer<>r.renderer_identity
   OR q.plan<>r.plan OR q.plan_sha256<>r.plan_sha256 OR q.reviewed_source_sha256<>r.upload_sha256
   OR a.evidence_source_id IS DISTINCT FROM q.evidence_source_id OR r.parent_rendition_id IS NOT NULL THEN
   RAISE EXCEPTION 'Candidate does not match submitted input' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO p FROM public.screenshot_renditions WHERE id=r.parent_rendition_id;
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

-- FUNCTION: annotation_source_current(uuid)
CREATE FUNCTION public.annotation_source_current(p_request uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce((SELECT q.source_kind='certificate_page' AND s.active
 AND q.manifest#>>'{requirement,review_hash}'=public.requirement_hash(q.requirement_id,public.requirement_current_pin(q.requirement_id))
 AND q.manifest#>>'{requirement,source_binding_sha256}' IS NOT DISTINCT FROM public.requirement_current_pin(q.requirement_id)->>'binding_sha256'
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
 AND e.status='unconfirmed_source' AND e.confirmed_at IS NULL
 AND e.certificate_file_id=q.certificate_file_id AND e.certificate_revision_id=q.certificate_revision_id
 FROM public.annotation_requests q JOIN public.evidence_sources e ON (e.id)=(q.evidence_source_id)
 JOIN public.certificate_files f ON (f.id)=(e.certificate_file_id)
 JOIN public.task_certificates s ON (s.id)=(e.task_certificate_id)
 WHERE q.id=p_request),false)
$$;

-- FUNCTION: annotation_task_lock()
CREATE FUNCTION public.annotation_task_lock() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_approval_current(uuid)
CREATE FUNCTION public.attachment_approval_current(p_approval uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.attachment_reviews r JOIN public.attachment_archives a
 ON (a.id)=(r.attachment_id)
 WHERE r.id=p_approval AND r.decision='approve' AND a.active
 AND NOT EXISTS(SELECT 1 FROM public.attachment_reviews n WHERE n.prior_review_id=r.id))
$$;

-- FUNCTION: attachment_file_complete()
CREATE FUNCTION public.attachment_file_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.attachment_revision_id=NEW.id AND f.attachment_id=NEW.attachment_id) THEN
  RAISE EXCEPTION 'Committed attachment revision requires its file' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_hold_guard()
CREATE FUNCTION public.attachment_hold_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE prior_id uuid;
BEGIN
 PERFORM public.attachment_privacy_actor(NEW.task_id,NEW.evidence_source_id,NEW.source_png_sha256);
 SELECT h.id INTO prior_id FROM public.attachment_privacy_holds h WHERE h.evidence_source_id=NEW.evidence_source_id AND h.id<>NEW.id
 AND NOT EXISTS(SELECT 1 FROM public.attachment_privacy_holds n WHERE n.prior_hold_id=h.id AND n.id<>NEW.id);
 IF NEW.prior_hold_id IS DISTINCT FROM prior_id THEN RAISE EXCEPTION 'Privacy hold transition conflict' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_human()
CREATE FUNCTION public.attachment_human() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT public.actor_is_human() THEN
  RAISE EXCEPTION 'Human attachment authority required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: attachment_immutable_gate()
CREATE FUNCTION public.attachment_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE encrypted_column text;
BEGIN
 -- Only the owning maintenance connection can rewrap record-bound ciphertext;
 -- every business field and identity remains byte-for-byte unchanged.
 encrypted_column := CASE TG_TABLE_NAME
  WHEN 'attachment_revisions' THEN 'label_encrypted'
  WHEN 'attachment_files' THEN 'upload_name_encrypted'
  WHEN 'attachment_file_parts' THEN 'upload_name_encrypted'
  ELSE NULL END;
 IF TG_OP='UPDATE' AND encrypted_column IS NOT NULL
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_column)=(to_jsonb(OLD)-encrypted_column) THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'Attachment history is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: attachment_link_guard()
CREATE FUNCTION public.attachment_link_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human();
 IF TG_TABLE_NAME='task_attachments' THEN PERFORM public.attachment_task_writer(NEW.task_id); END IF;
 PERFORM 1 FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['active','state_version']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active','state_version'])
   OR NOT OLD.active OR NEW.active OR NEW.state_version<>OLD.state_version+1 THEN
   RAISE EXCEPTION 'Only one-way deactivation is permitted' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NOT NEW.active OR NEW.state_version<>1 OR NOT public.attachment_approval_current(NEW.approval_id) THEN
  RAISE EXCEPTION 'Current exact archive approval required' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='profile_attachment_links' THEN
  IF NOT EXISTS(SELECT 1 FROM public.org_profile_revisions r
   WHERE r.id=NEW.profile_revision_id AND length(btrim(r.data->>NEW.field))>0) THEN
   RAISE EXCEPTION 'Exact nonempty declaration required' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT public.attachment_selection_current(NEW.id)
   OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256) THEN
   RAISE EXCEPTION 'Exact active task profile and declaration required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_parts_disabled()
CREATE FUNCTION public.attachment_parts_disabled() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN RAISE EXCEPTION 'attachment_upload_mode_not_enabled' USING ERRCODE='23514'; END $$;

-- FUNCTION: attachment_privacy_actor(uuid, uuid, text)
CREATE FUNCTION public.attachment_privacy_actor(p_task uuid, p_source uuid, p_sha text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE src public.evidence_sources%ROWTYPE; root public.attachment_archives%ROWTYPE;
BEGIN
 PERFORM public.attachment_task_writer(p_task);
 SELECT * INTO src FROM public.evidence_sources WHERE id=p_source;
 SELECT * INTO root FROM public.attachment_archives WHERE id=src.attachment_id FOR UPDATE;
 PERFORM 1 FROM public.evidence_sources WHERE id=p_source FOR UPDATE;
 IF src.source_kind IS DISTINCT FROM 'user_supplied_attachment_pdf' OR src.task_id IS DISTINCT FROM p_task
 OR src.preview->>'sha256' IS DISTINCT FROM p_sha OR root.id IS NULL
 OR NOT public.attachment_selection_current(src.task_attachment_id) THEN
  RAISE EXCEPTION 'Exact attachment page and assigned reviewer required' USING ERRCODE='23514'; END IF;
END $$;

-- FUNCTION: attachment_review_guard()
CREATE FUNCTION public.attachment_review_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root public.attachment_archives%ROWTYPE; prior public.attachment_reviews%ROWTYPE;
BEGIN
 PERFORM public.attachment_human();
 SELECT * INTO root FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF NOT root.active THEN
  RAISE EXCEPTION 'Assigned live archive reviewer required' USING ERRCODE='42501'; END IF;
 SELECT r.* INTO prior FROM public.attachment_reviews r WHERE r.attachment_revision_id=NEW.attachment_revision_id AND r.id<>NEW.id
 AND NOT EXISTS(SELECT 1 FROM public.attachment_reviews n WHERE n.prior_review_id=r.id AND n.id<>NEW.id);
 IF NEW.prior_review_id IS DISTINCT FROM prior.id
 OR (NEW.decision='approve' AND prior.decision='approve')
 OR (NEW.decision='reject' AND prior.id IS NOT NULL)
 OR (NEW.decision='revoke' AND prior.decision IS DISTINCT FROM 'approve') THEN
  RAISE EXCEPTION 'Archive review transition conflict' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.attachment_files f JOIN public.attachment_revisions r ON (r.id)=(f.attachment_revision_id)
  WHERE f.id=NEW.file_id AND f.file->>'sha256'=NEW.original_sha256 AND r.metadata_sha256=NEW.metadata_sha256) THEN
  RAISE EXCEPTION 'Archive review hash mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_revision_guard()
CREATE FUNCTION public.attachment_revision_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE root public.attachment_archives%ROWTYPE; parent_xmin xid;
BEGIN
 IF TG_TABLE_NAME='attachment_revisions' THEN
  PERFORM public.attachment_human();
  SELECT * INTO root FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
  IF NOT root.active OR NEW.revision<>root.current_revision THEN
   RAISE EXCEPTION 'Revision requires exact active root pointer' USING ERRCODE='23514'; END IF;
 ELSE
  PERFORM public.attachment_human();
  SELECT xmin INTO parent_xmin FROM public.attachment_revisions WHERE id=NEW.attachment_revision_id;
  IF parent_xmin IS DISTINCT FROM pg_current_xact_id()::xid THEN
   RAISE EXCEPTION 'File requires a new revision' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_root_guard()
CREATE FUNCTION public.attachment_root_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Attachment history cannot be deleted' USING ERRCODE='42501'; END IF;
 PERFORM public.attachment_human();
 IF TG_OP='INSERT' THEN
  IF NEW.current_revision<>1 OR NEW.state_version<>1 OR NOT NEW.active THEN
   RAISE EXCEPTION 'Invalid initial archive state' USING ERRCODE='23514'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['current_revision','state_version','active']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['current_revision','state_version','active'])
   OR NOT OLD.active OR NEW.state_version<>OLD.state_version+1
   OR NEW.current_revision NOT IN (OLD.current_revision,OLD.current_revision+1) THEN
   RAISE EXCEPTION 'Invalid archive state transition' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_screenshot_asset_guard(uuid)
CREATE FUNCTION public.attachment_screenshot_asset_guard(p_asset uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.screenshot_assets%ROWTYPE; src public.evidence_sources%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE id=p_asset;
 SELECT * INTO src FROM public.evidence_sources WHERE id=a.evidence_source_id;
 PERFORM public.attachment_privacy_actor(a.task_id,a.evidence_source_id,a.source_sha256);
 IF a.origin<>'attachment' OR a.source_kind<>'attachment_page' OR a.image_kind<>'attachment_page'
 OR a.source_hash_assurance<>'server_verified' OR a.source->>'kind' IS DISTINCT FROM 'attachment_page'
 OR a.source->>'evidence_source_id' IS DISTINCT FROM src.id::text
 OR a.source ? 'annotation_request_id'
 OR num_nonnulls(a.task_feature_id,a.feature_revision_id,a.task_resource_id,a.product_revision_id,a.vendor_archive_id,a.prototype_run_id)<>0
 OR a.source_width<>(src.preview->>'width_px')::integer OR a.source_height<>(src.preview->>'height_px')::integer
 OR EXISTS(SELECT 1 FROM public.attachment_privacy_holds h WHERE h.evidence_source_id=src.id) THEN
  RAISE EXCEPTION 'Invalid attachment privacy source or unresolved hold' USING ERRCODE='23514'; END IF;
END $$;

-- FUNCTION: attachment_screenshot_binding_guard()
CREATE FUNCTION public.attachment_screenshot_binding_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.screenshot_assets%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE id=NEW.asset_id;
 IF (a.source_kind='attachment_page') IS DISTINCT FROM (NEW.attachment_source_id IS NOT NULL) THEN
  RAISE EXCEPTION 'Screenshot review attachment branch mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.attachment_source_id IS NOT NULL THEN PERFORM public.attachment_screenshot_privacy_guard(NEW.id); END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_screenshot_privacy_guard(uuid)
CREATE FUNCTION public.attachment_screenshot_privacy_guard(p_review uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.screenshot_privacy_reviews%ROWTYPE; a public.screenshot_assets%ROWTYPE; r public.screenshot_renditions%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.screenshot_privacy_reviews WHERE id=p_review;
 SELECT * INTO a FROM public.screenshot_assets WHERE id=p.asset_id;
 SELECT * INTO r FROM public.screenshot_renditions WHERE id=p.rendition_id;
 PERFORM public.attachment_privacy_actor(p.task_id,p.attachment_source_id,p.reviewed_upload_sha256);
 IF a.source_kind IS DISTINCT FROM 'attachment_page' OR a.evidence_source_id IS DISTINCT FROM p.attachment_source_id
 OR p.annotation_request_id IS NOT NULL OR p.resolved_hold_id IS NOT NULL
 OR p.rule_version<>'screenshot-privacy-v1' OR r.parent_rendition_id IS NOT NULL
 OR r.privacy_review_id IS DISTINCT FROM p.id OR r.asset_id IS DISTINCT FROM p.asset_id
 OR r.image_sha256 IS DISTINCT FROM p.stored_image_sha256 OR r.upload_sha256 IS DISTINCT FROM p.reviewed_upload_sha256
 OR r.profile IS DISTINCT FROM 'screenshot-markup-v1' OR r.plan->'redact' IS DISTINCT FROM '[]'::jsonb OR r.plan->'boxes' IS DISTINCT FROM '[]'::jsonb
 OR (r.mapping->'crop'->>'x')::integer<>0 OR (r.mapping->'crop'->>'y')::integer<>0
 OR (r.mapping->>'content_width')::integer<>a.source_width OR (r.mapping->>'content_height')::integer<>a.source_height
 OR EXISTS(SELECT 1 FROM public.attachment_privacy_holds h WHERE h.evidence_source_id=p.attachment_source_id) THEN
  RAISE EXCEPTION 'Attachment privacy requires exact unchanged page without hold' USING ERRCODE='23514'; END IF;
END $$;

-- FUNCTION: attachment_screenshot_rendition_guard()
CREATE FUNCTION public.attachment_screenshot_rendition_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.screenshot_assets%ROWTYPE;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE id=NEW.asset_id;
 IF a.source_kind<>'attachment_page' THEN RETURN NEW; END IF;
 PERFORM public.attachment_privacy_actor(NEW.task_id,a.evidence_source_id,NEW.source_sha256);
 IF NEW.parent_rendition_id IS NOT NULL OR NEW.generation_job_id IS NOT NULL
 OR NEW.profile<>'screenshot-markup-v1' OR NEW.upload_sha256<>a.source_sha256
 OR NEW.plan->'redact' IS DISTINCT FROM '[]'::jsonb OR NEW.plan->'boxes' IS DISTINCT FROM '[]'::jsonb
 OR (NEW.mapping->'crop'->>'x')::integer<>0 OR (NEW.mapping->'crop'->>'y')::integer<>0
 OR (NEW.mapping->>'content_width')::integer<>a.source_width OR (NEW.mapping->>'content_height')::integer<>a.source_height THEN
  RAISE EXCEPTION 'attachment_redaction_not_enabled' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_screenshot_withdraw_guard()
CREATE FUNCTION public.attachment_screenshot_withdraw_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE a public.screenshot_assets%ROWTYPE; archive_id uuid;
BEGIN
 SELECT * INTO a FROM public.screenshot_assets WHERE id=NEW.asset_id;
 IF a.source_kind<>'attachment_page' THEN RETURN NEW; END IF;
 PERFORM public.attachment_task_writer(NEW.task_id);
 SELECT r.id INTO archive_id FROM public.attachment_archives r
 JOIN public.evidence_sources s ON (s.attachment_id)=(r.id)
 WHERE s.id=a.evidence_source_id;
 IF archive_id IS NULL THEN
  RAISE EXCEPTION 'Assigned attachment privacy reviewer required' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_selection_current(uuid)
CREATE FUNCTION public.attachment_selection_current(p_selection uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.task_attachments s
 JOIN public.task_org_profiles p ON (p.id)=(s.task_org_profile_id)
 JOIN public.profile_attachment_links l ON (l.id)=(s.profile_attachment_link_id)
 WHERE s.id=p_selection AND s.active AND p.active AND l.active
 AND public.attachment_approval_current(s.approval_id))
$$;

-- FUNCTION: attachment_source_guard()
CREATE FUNCTION public.attachment_source_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW.source_kind<>'user_supplied_attachment_pdf' THEN RETURN NEW; END IF;
 PERFORM public.attachment_task_writer(NEW.task_id);
 PERFORM 1 FROM public.attachment_archives WHERE id=NEW.attachment_id FOR UPDATE;
 IF NOT public.attachment_selection_current(NEW.task_attachment_id)
 OR NOT EXISTS(SELECT 1 FROM public.attachment_files f WHERE f.id=NEW.attachment_file_id AND NEW.page<=(f.file->>'page_count')::integer) THEN
  RAISE EXCEPTION 'Attachment source parents are unavailable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: attachment_task_writer(uuid)
CREATE FUNCTION public.attachment_task_writer(p_task uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM public.attachment_human();
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Active task contributor required' USING ERRCODE='42501'; END IF;
END $$;

-- FUNCTION: bid_presence_complete()
CREATE FUNCTION public.bid_presence_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE actual_ids jsonb; expected_ids jsonb; parent_id uuid;
BEGIN
 IF TG_TABLE_NAME IN ('bid_presence_preparations','bid_presence_images') THEN
  IF TG_TABLE_NAME='bid_presence_preparations' THEN parent_id := NEW.id; ELSE parent_id := NEW.presence_preparation_id; END IF;
  SELECT expected_page_ids INTO expected_ids FROM public.bid_presence_preparations WHERE id=parent_id;
  SELECT COALESCE(jsonb_agg(page_id::text ORDER BY page_id::text),'[]'::jsonb) INTO actual_ids FROM public.bid_presence_images WHERE presence_preparation_id=parent_id;
 ELSE
  IF TG_TABLE_NAME='bid_presence_authorizations' THEN parent_id := NEW.id; ELSE parent_id := NEW.authorization_id; END IF;
  SELECT image_ids INTO expected_ids FROM public.bid_presence_authorizations WHERE id=parent_id;
  SELECT COALESCE(jsonb_agg(image_id::text ORDER BY image_id::text),'[]'::jsonb) INTO actual_ids FROM public.bid_presence_authorized_images WHERE authorization_id=parent_id;
 END IF;
 IF actual_ids IS DISTINCT FROM expected_ids OR jsonb_array_length(actual_ids) NOT BETWEEN 1 AND 40 THEN
  RAISE EXCEPTION 'Presence scope must exactly match its fixed inventory' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_presence_immutable()
CREATE FUNCTION public.bid_presence_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND TG_TABLE_NAME IN ('bid_presence_preparations','bid_presence_authorizations')
 AND (to_jsonb(NEW)-'details_encrypted'-'reason_encrypted')=(to_jsonb(OLD)-'details_encrypted'-'reason_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Presence images and authorization history are immutable' USING ERRCODE='23514';
END $$;

-- FUNCTION: bid_presence_insert_guard()
CREATE FUNCTION public.bid_presence_insert_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE prepared public.bid_presence_preparations%ROWTYPE;
 granted public.bid_presence_authorizations%ROWTYPE;
 source_page public.bid_document_pages%ROWTYPE;
 prior_grant public.bid_presence_authorizations%ROWTYPE;
 permission_name text := 'bid-review' || chr(58) || 'outbound' || chr(58) || 'authorize';
BEGIN
 IF TG_TABLE_NAME='bid_presence_preparations' THEN prepared := NEW;
 ELSIF TG_TABLE_NAME='bid_presence_authorizations' THEN
  granted := NEW;
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE id=NEW.presence_preparation_id;
 ELSIF TG_TABLE_NAME='bid_presence_images' THEN
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE id=NEW.presence_preparation_id;
 ELSE
  SELECT * INTO granted FROM public.bid_presence_authorizations WHERE id=NEW.authorization_id;
  SELECT * INTO prepared FROM public.bid_presence_preparations WHERE id=NEW.presence_preparation_id;
 END IF;
 IF prepared.id IS NULL OR NOT public.actor_is_human()
 OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? permission_name)
 OR NOT public.bid_review_actor_live(NEW.task_id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_review_publications WHERE review_id=prepared.source_review_id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE submission_id=NEW.submission_id AND preparation_id=prepared.preparation_id)
 THEN RAISE EXCEPTION 'Presence disclosure requires current human task owner' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_presence_images' THEN
  SELECT * INTO source_page FROM public.bid_document_pages WHERE id=NEW.page_id;
  IF NOT (prepared.expected_page_ids ? NEW.page_id::text)
  OR source_page.role IS DISTINCT FROM 'bid' OR source_page.preparation_id IS DISTINCT FROM prepared.preparation_id
  OR source_page.image->>'sha256' IS DISTINCT FROM NEW.source_sha256
  OR NOT EXISTS(SELECT 1 FROM public.bid_review_required_locations loc
    JOIN public.bid_review_signing_requirements req ON req.id=loc.requirement_id
    WHERE loc.review_id=prepared.source_review_id AND loc.page_id=NEW.page_id AND req.applicability='applies')
  OR NOT EXISTS(SELECT 1 FROM public.bid_redacted_pages redacted WHERE redacted.page_id=NEW.page_id AND redacted.classification='non_price' AND NOT redacted.price_page)
  THEN RAISE EXCEPTION 'Presence image requires non-price confirmed required page' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_presence_authorizations' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.submission_id::text || '/presence',0));
  SELECT * INTO prior_grant FROM public.bid_presence_authorizations WHERE submission_id=NEW.submission_id ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>COALESCE(prior_grant.revision,0)+1
  OR NEW.prior_authorization_id IS DISTINCT FROM prior_grant.id
  OR NEW.manifest_sha256 IS DISTINCT FROM prepared.manifest_sha256
  OR (NOT NEW.allow_external AND (prior_grant.id IS NULL OR prior_grant.presence_preparation_id IS DISTINCT FROM NEW.presence_preparation_id))
  THEN RAISE EXCEPTION 'Presence grant predecessor or manifest mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_presence_authorized_images' THEN
  IF NOT (granted.image_ids ? NEW.image_id::text) THEN
   RAISE EXCEPTION 'Presence image was not in the fixed human grant' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_privacy_complete()
CREATE FUNCTION public.bid_privacy_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE g public.bid_outbound_authorizations%ROWTYPE; expected integer; scope_text text;
BEGIN
 IF TG_TABLE_NAME='bid_redaction_snapshots' THEN
  SELECT page_count INTO expected FROM public.bid_preparation_publications
   WHERE submission_id=NEW.submission_id;
  IF expected IS DISTINCT FROM (SELECT count(*)::integer FROM public.bid_redacted_pages
   WHERE snapshot_id=NEW.id) THEN
   RAISE EXCEPTION 'Redaction snapshot must cover the prepared inventory' USING ERRCODE='23514'; END IF;
 ELSE
  IF TG_TABLE_NAME='bid_outbound_authorizations' THEN g:=NEW;
  ELSE SELECT * INTO g FROM public.bid_outbound_authorizations WHERE id=NEW.authorization_id; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.id) THEN
   RAISE EXCEPTION 'Outbound grant requires an exact nonempty page scope' USING ERRCODE='23514'; END IF;
  SELECT '[' || string_agg('{"page_id":"' || page_id::text || '","sanitized_text_sha256":"'
   || sanitized_text_sha256 || '"}', ',' ORDER BY page_id::text) || ']' INTO scope_text
   FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.id;
  IF g.authorized_sanitized_context_sha256 IS DISTINCT FROM encode(sha256(convert_to(scope_text,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'Exact authorized scope digest mismatch' USING ERRCODE='23514'; END IF;
  IF NOT g.allow_external AND EXISTS(
   (SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.id
    EXCEPT SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.prior_authorization_id)
   UNION ALL
   (SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.prior_authorization_id
    EXCEPT SELECT page_id,sanitized_text_sha256 FROM public.bid_outbound_authorized_pages WHERE authorization_id=g.id)
  ) THEN RAISE EXCEPTION 'Revocation cannot alter prior pages' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_privacy_human(uuid)
CREATE FUNCTION public.bid_privacy_human(p_task uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM public.bid_review_human(p_task,'bid-review:outbound:authorize');
 -- Same lock order as API admissions; serializes checked append-only revisions.
 PERFORM 1 FROM public.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'Human task owner required' USING ERRCODE='42501'; END IF;
END;
$$;

-- FUNCTION: bid_privacy_immutable()
CREATE FUNCTION public.bid_privacy_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE columns text[];
BEGIN
 columns:=CASE TG_TABLE_NAME WHEN 'bid_review_name_lists' THEN ARRAY['names_encrypted']
 WHEN 'bid_redaction_snapshots' THEN ARRAY['details_encrypted']
 WHEN 'bid_redacted_pages' THEN ARRAY['sanitized_text_encrypted','notes_encrypted']
 WHEN 'bid_outbound_authorizations' THEN ARRAY['reason_encrypted'] ELSE ARRAY[]::text[] END;
 IF TG_OP='UPDATE' AND cardinality(columns)>0
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-columns)=(to_jsonb(OLD)-columns) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Privacy evidence and human grants are immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: bid_privacy_insert_guard()
CREATE FUNCTION public.bid_privacy_insert_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE s public.bid_redaction_snapshots%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 p public.bid_outbound_authorizations%ROWTYPE; b public.bid_document_pages%ROWTYPE;
 latest integer;
BEGIN
 PERFORM public.bid_privacy_human(NEW.task_id);
 IF TG_TABLE_NAME='bid_review_name_lists' THEN
  SELECT COALESCE(max(revision),0) INTO latest FROM public.bid_review_name_lists
   WHERE submission_id=NEW.submission_id AND id<>NEW.id;
  IF NEW.revision<>latest+1 THEN RAISE EXCEPTION 'Name list revision mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_redaction_snapshots' THEN
  IF NOT EXISTS(SELECT 1 FROM public.bid_preparation_publications
   WHERE task_id=NEW.task_id AND submission_id=NEW.submission_id
   AND preparation_id=NEW.preparation_id) THEN
   RAISE EXCEPTION 'Published page inventory required' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_redacted_pages' THEN
  SELECT * INTO s FROM public.bid_redaction_snapshots WHERE id=NEW.snapshot_id;
  SELECT * INTO b FROM public.bid_document_pages WHERE id=NEW.page_id;
  IF s.id IS NULL OR b.preparation_id IS DISTINCT FROM NEW.preparation_id
  OR ((b.text_status<>'native' OR b.page_kind<>'text') AND NEW.classification='non_price')
  OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations WHERE snapshot_id=NEW.snapshot_id) THEN
   RAISE EXCEPTION 'Current unpublished redaction scope required' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_outbound_authorizations' THEN
  SELECT * INTO s FROM public.bid_redaction_snapshots WHERE id=NEW.snapshot_id;
  SELECT * INTO p FROM public.bid_outbound_authorizations
   WHERE submission_id=NEW.submission_id AND id<>NEW.id
   ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>COALESCE(p.revision,0)+1 OR NEW.prior_authorization_id IS DISTINCT FROM p.id
  OR NEW.redaction_manifest_sha256 IS DISTINCT FROM s.manifest_sha256
  OR NEW.provider_bindings_sha256 IS DISTINCT FROM s.provider_bindings_sha256
  OR NOT EXISTS(SELECT 1 FROM public.bid_submissions u JOIN public.bid_preparation_publications v
   ON (u.task_id,u.id)=(v.task_id,v.submission_id)
   WHERE u.task_id=NEW.task_id AND u.id=NEW.submission_id
   AND u.manifest_sha256=NEW.submission_manifest_sha256 AND v.input_hash=NEW.preparation_input_hash
   AND v.preparation_id=NEW.preparation_id) THEN
   RAISE EXCEPTION 'Exact outbound authorization identity mismatch' USING ERRCODE='23514'; END IF;
  IF NOT NEW.allow_external AND (p.id IS NULL OR NEW.snapshot_id<>p.snapshot_id
   OR NEW.authorized_sanitized_context_sha256<>p.authorized_sanitized_context_sha256) THEN
   RAISE EXCEPTION 'Revocation must retain prior disclosure identity' USING ERRCODE='23514'; END IF;
  IF NEW.allow_external AND NOT EXISTS(SELECT 1 FROM public.tasks t
   WHERE t.id=NEW.task_id AND t.model_redaction_enabled
   AND t.model_redaction_revision=s.redaction_revision) THEN
   RAISE EXCEPTION 'Mandatory redaction must be enabled and current' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='bid_outbound_authorized_pages' THEN
  SELECT * INTO g FROM public.bid_outbound_authorizations WHERE id=NEW.authorization_id;
  IF g.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.bid_redacted_pages r
   JOIN public.bid_document_pages dp ON (dp.id)=(r.page_id)
   WHERE r.snapshot_id=NEW.snapshot_id AND r.page_id=NEW.page_id
   AND r.sanitized_text_sha256=NEW.sanitized_text_sha256 AND NOT r.price_page AND dp.text_status='native' AND dp.page_kind='text') THEN
   RAISE EXCEPTION 'Only exact non-price native-text scope can be authorized' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_actor_live(uuid)
CREATE FUNCTION public.bid_review_actor_live(p_task uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.tasks task_row WHERE task_row.id=p_task)
$$;

-- FUNCTION: bid_review_finding_event_guard()
CREATE FUNCTION public.bid_review_finding_event_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE f public.bid_review_findings%ROWTYPE; r public.bid_review_runs%ROWTYPE;
 prior public.bid_review_finding_events%ROWTYPE; classification public.bid_review_finding_events%ROWTYPE;
BEGIN
 -- bid_app has no UPDATE privilege on immutable findings; serialize with the
 -- same per-finding advisory lock the decision service takes.
 PERFORM pg_advisory_xact_lock(hashtextextended('bid-review-finding:' || NEW.finding_id::text, 0));
 SELECT * INTO f FROM public.bid_review_findings WHERE id=NEW.finding_id;
 SELECT * INTO r FROM public.bid_review_runs WHERE id=NEW.review_id;
 IF f.id IS NULL OR r.id IS NULL OR NOT public.actor_is_human()
 OR NOT public.bid_review_actor_live(NEW.task_id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_review_publications WHERE review_id=r.id)
 OR NEW.expected_input_hash IS DISTINCT FROM r.input_hash
 OR NOT EXISTS(SELECT 1 FROM public.bid_submissions WHERE id=r.submission_id AND state='uploaded')
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.submission_id=r.submission_id AND a.revision>(SELECT revision FROM public.bid_outbound_authorizations WHERE id=r.authorization_id)) THEN
 RAISE EXCEPTION 'Human decision requires current task and report authority' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM public.bid_review_finding_events WHERE finding_id=f.id ORDER BY revision DESC LIMIT 1;
 IF NEW.revision IS DISTINCT FROM COALESCE(prior.revision,1)+1 OR NEW.prior_decision_id IS DISTINCT FROM prior.id THEN
 RAISE EXCEPTION 'Finding revision conflict' USING ERRCODE='23514'; END IF;
 IF NEW.action='classify' THEN
  IF NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'bid-review:classify')
  OR NEW.state<>COALESCE(prior.state,'open') OR NEW.state<>'open' THEN
  RAISE EXCEPTION 'Finding classification requires human task management' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT * INTO classification FROM public.bid_review_finding_events WHERE finding_id=f.id AND action='classify' ORDER BY revision DESC LIMIT 1;
  IF classification.id IS NULL
  OR NEW.review_domain IS DISTINCT FROM classification.review_domain
  OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? 'bid-review:decide') THEN
   RAISE EXCEPTION 'Finding domain requires its authorized human reviewer' USING ERRCODE='42501'; END IF;
  IF (NEW.action='reopen' AND (COALESCE(prior.state,'open')='open' OR NEW.state<>'open'))
  OR (NEW.action='dismiss' AND (COALESCE(prior.state,'open')<>'open' OR NEW.state<>'dismissed'))
  OR (NEW.action='confirm' AND (COALESCE(prior.state,'open')<>'open' OR NEW.state<>'confirmed')) THEN
   RAISE EXCEPTION 'Invalid finding disposition transition' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_finding_immutable()
CREATE FUNCTION public.bid_review_finding_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE encrypted_column text;
BEGIN
 encrypted_column := CASE TG_TABLE_NAME WHEN 'bid_review_findings' THEN 'details_encrypted'
 WHEN 'bid_review_finding_events' THEN 'reason_encrypted' ELSE NULL END;
 IF TG_OP='UPDATE' AND encrypted_column IS NOT NULL
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_column)=(to_jsonb(OLD)-encrypted_column) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Findings and human events are immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: bid_review_finding_output_guard()
CREATE FUNCTION public.bid_review_finding_output_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.bid_review_runs%ROWTYPE; j public.jobs%ROWTYPE;
 p public.bid_document_pages%ROWTYPE; d public.bid_submission_documents%ROWTYPE; f public.bid_review_findings%ROWTYPE;
BEGIN
 SELECT * INTO r FROM public.bid_review_runs WHERE id=NEW.review_id;
 SELECT * INTO j FROM public.jobs WHERE id=r.job_id FOR SHARE;
 IF r.id IS NULL OR j.status IS DISTINCT FROM 'running' OR j.run_id IS NULL
 OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT public.bid_review_run_live(NEW.task_id,j.actor_token_id)
 OR EXISTS(SELECT 1 FROM public.bid_review_publications WHERE review_id=r.id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a
 WHERE a.id=r.authorization_id AND a.allow_external
 AND public.bid_review_actor_live(a.task_id)
 AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorizations b
 WHERE b.submission_id=a.submission_id AND b.revision>a.revision)) THEN
 RAISE EXCEPTION 'Findings require a live authorized review attempt' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_findings' THEN
  IF NEW.absence_kind='submission_inventory' AND (NEW.absence_manifest_sha256 IS DISTINCT FROM
   (SELECT manifest_sha256 FROM public.bid_submissions WHERE id=r.submission_id)
   OR NEW.search_source_count<>(SELECT count(*) FROM public.bid_submission_documents WHERE submission_id=r.submission_id AND role='bid')) THEN
   RAISE EXCEPTION 'Absence inventory binding mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.absence_kind='locations' AND NEW.absence_coverage='all_bid_pages' AND NEW.search_source_count<>
   (SELECT count(*) FROM public.bid_document_pages WHERE preparation_id=r.preparation_id AND role='bid') THEN
   RAISE EXCEPTION 'Whole-bid search is incomplete' USING ERRCODE='23514'; END IF;
  IF (SELECT count(*) FROM public.bid_review_findings WHERE review_id=r.id AND obligation_id=NEW.obligation_id)>=20 THEN
   RAISE EXCEPTION 'Finding obligation bound exceeded' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO f FROM public.bid_review_findings WHERE id=NEW.finding_id;
  IF (NEW.kind='inventory_document' AND f.absence_kind IS DISTINCT FROM 'submission_inventory')
  OR (NEW.kind='absence_page' AND f.absence_kind IS DISTINCT FROM 'locations') THEN
   RAISE EXCEPTION 'Absence source kind mismatch' USING ERRCODE='23514'; END IF;
  SELECT * INTO d FROM public.bid_submission_documents WHERE id=NEW.document_id;
  IF NEW.preparation_id IS DISTINCT FROM r.preparation_id THEN RAISE EXCEPTION 'Finding preparation mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.kind IN ('inventory_document','rule_document') THEN
   IF NEW.kind='rule_document' AND NOT EXISTS(SELECT 1 FROM public.bid_pdf_validations v WHERE v.submission_id=r.submission_id AND v.preparation_id=r.preparation_id AND v.document_id=d.id AND v.id=NEW.validation_id) THEN RAISE EXCEPTION 'PDF rule requires prepared validation' USING ERRCODE='23514'; END IF;
   IF d.role IS DISTINCT FROM 'bid' THEN RAISE EXCEPTION 'Absent file inventory must be submitted bids' USING ERRCODE='23514'; END IF;
  ELSE
   SELECT * INTO p FROM public.bid_document_pages WHERE id=NEW.page_id;
   IF p.id IS NULL OR p.preparation_id IS DISTINCT FROM r.preparation_id
   OR p.document_id IS DISTINCT FROM NEW.document_id
   OR p.role IS DISTINCT FROM (CASE WHEN NEW.kind='tender_support' THEN 'tender' ELSE 'bid' END)
   OR (NEW.kind IN ('tender_support','bid_support','absence_page') AND NOT EXISTS(
    SELECT 1 FROM public.bid_outbound_authorized_pages a WHERE a.authorization_id=r.authorization_id AND a.page_id=p.id)) THEN
    RAISE EXCEPTION 'Finding source is outside exact authorized inventory' USING ERRCODE='23514'; END IF;
  END IF;
  IF NEW.kind IN ('tender_support','bid_support') AND NEW.ordinal>20 THEN
   RAISE EXCEPTION 'Finding support bound exceeded' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_findings_complete()
CREATE FUNCTION public.bid_review_findings_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v_review_id uuid; target uuid; publication public.bid_review_publications%ROWTYPE;
BEGIN
 v_review_id := NEW.review_id;
 IF TG_TABLE_NAME='bid_review_findings' THEN target := NEW.id;
 ELSIF TG_TABLE_NAME='bid_review_finding_sources' THEN target := NEW.finding_id; END IF;
 SELECT * INTO publication FROM public.bid_review_publications publication_row WHERE publication_row.review_id=v_review_id;
 IF publication.id IS NULL OR (TG_TABLE_NAME='bid_review_publications' AND COALESCE((publication.coverage->>'findings')::integer,0)
 IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_findings finding_row WHERE finding_row.review_id=v_review_id))
 OR EXISTS(SELECT 1 FROM public.bid_review_findings f WHERE f.review_id=v_review_id
 AND (target IS NULL OR f.id=target) AND (NOT EXISTS(SELECT 1 FROM public.bid_review_finding_sources s JOIN public.bid_review_obligations o ON (o.id)=(f.obligation_id) WHERE s.finding_id=f.id AND s.kind='tender_support' AND s.page_id=o.page_id)
 OR f.tender_support_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.finding_id=f.id AND s.kind='tender_support')
 OR f.bid_support_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.finding_id=f.id AND s.kind='bid_support')
 OR f.search_source_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.finding_id=f.id AND s.kind IN ('absence_page','inventory_document'))
 OR f.rule_document_count<>(SELECT count(*) FROM public.bid_review_finding_sources s WHERE s.finding_id=f.id AND s.kind='rule_document'))) THEN
 RAISE EXCEPTION 'Finding publication is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_human(uuid, text)
CREATE FUNCTION public.bid_review_human(p_task uuid, p_scope text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT public.actor_is_human()
 OR NOT COALESCE(NULLIF(current_setting('app.actor_scopes',true),'')::jsonb ? p_scope,false)
 OR NOT public.bid_review_actor_live(p_task) THEN
  RAISE EXCEPTION 'Human uploaded-bid authority required' USING ERRCODE='42501'; END IF;
END;
$$;

-- FUNCTION: bid_review_immutable_gate()
CREATE FUNCTION public.bid_review_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE encrypted_columns text[];
BEGIN
 encrypted_columns:=CASE TG_TABLE_NAME
  WHEN 'bid_submission_documents' THEN ARRAY['upload_name_encrypted']
  WHEN 'bid_prepared_documents' THEN ARRAY['structure_encrypted']
  WHEN 'bid_document_pages' THEN ARRAY['text_encrypted','structure_encrypted']
  ELSE ARRAY[]::text[] END;
 IF TG_OP='UPDATE' AND cardinality(encrypted_columns)>0
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-encrypted_columns)=(to_jsonb(OLD)-encrypted_columns) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Uploaded-bid versions and inventories are immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: bid_review_inventory_complete()
CREATE FUNCTION public.bid_review_inventory_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_preparation_publications%ROWTYPE; total bigint; docs bigint; required bigint;
BEGIN
 SELECT * INTO p FROM public.bid_preparation_publications
 WHERE preparation_id=NEW.preparation_id;
 IF p.id IS NULL THEN
  RAISE EXCEPTION 'Partial inventory cannot be committed' USING ERRCODE='23514'; END IF;
 SELECT count(*),sum(page_count) INTO docs,total FROM public.bid_prepared_documents
 WHERE preparation_id=p.preparation_id;
 SELECT file_count INTO required FROM public.bid_submissions WHERE id=p.submission_id;
 IF docs<>required OR total IS DISTINCT FROM p.page_count OR total>1000
 OR EXISTS(SELECT 1 FROM public.bid_prepared_documents d WHERE d.preparation_id=p.preparation_id
 AND (SELECT count(*) FROM public.bid_document_pages b WHERE b.preparation_id=d.preparation_id AND b.document_id=d.document_id)<>d.page_count)
 OR EXISTS(SELECT 1 FROM public.bid_document_pages b JOIN public.bid_prepared_documents d
 ON (d.task_id,d.submission_id,d.preparation_id,d.document_id)
 =(b.task_id,b.submission_id,b.preparation_id,b.document_id)
 WHERE b.preparation_id=p.preparation_id AND
 (b.page>d.page_count OR b.rendered_pdf_sha256<>d.rendered_pdf_sha256
 OR b.render_profile<>d.render_profile OR b.renderer_identity<>d.renderer_identity)) THEN
  RAISE EXCEPTION 'Page inventory is incomplete or inconsistent' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_inventory_guard()
CREATE FUNCTION public.bid_review_inventory_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_preparations%ROWTYPE; j public.jobs%ROWTYPE; d public.bid_submission_documents%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE id=NEW.preparation_id;
 SELECT * INTO j FROM public.jobs WHERE id=p.job_id FOR SHARE;
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.status IS DISTINCT FROM 'running'
 OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR NOT(j.actor_scopes ? 'bid-review:prepare')
 OR NOT public.bid_review_actor_live(NEW.task_id) THEN
  RAISE EXCEPTION 'Current local preparation lease required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_preparation_publications' THEN
  IF NEW.run_id IS DISTINCT FROM j.run_id THEN
   RAISE EXCEPTION 'Publication actor or attempt mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE submission_id=NEW.submission_id) THEN
   RAISE EXCEPTION 'Published page inventory is immutable' USING ERRCODE='42501'; END IF;
  SELECT * INTO d FROM public.bid_submission_documents WHERE id=NEW.document_id;
  IF TG_TABLE_NAME='bid_document_pages' THEN
   IF NEW.role IS DISTINCT FROM d.role OR NEW.original_sha256 IS DISTINCT FROM d.sha256
   OR (NEW.page_kind='text' AND NEW.text_status<>'native') THEN
    RAISE EXCEPTION 'Page source identity mismatch' USING ERRCODE='23514'; END IF;
  ELSIF (d.media_type='application/pdf' AND (NEW.citation_mode<>'page' OR NEW.rendered_pdf_sha256<>d.sha256
  OR NEW.rendered_pdf_storage_key<>d.storage_key))
  OR (d.media_type<>'application/pdf' AND (NEW.citation_mode<>'block'
  OR NEW.rendered_pdf_storage_key<>'bid-review/' || NEW.submission_id::text
  || '/preparations/' || NEW.preparation_id::text || '/documents/' || NEW.document_id::text || '/' || NEW.rendered_pdf_sha256 || '.pdf')) THEN
   RAISE EXCEPTION 'Prepared document source identity mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_job_immutable()
CREATE FUNCTION public.bid_review_job_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW.bid_submission_document_id IS DISTINCT FROM OLD.bid_submission_document_id
 OR (OLD.kind='bid_review' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.document_id IS DISTINCT FROM OLD.document_id
 OR NEW.result->'submission' IS DISTINCT FROM OLD.result->'submission')) THEN
 RAISE EXCEPTION 'Review job input is immutable' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_output_complete()
CREATE FUNCTION public.bid_review_output_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_review_publications%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_review_publications WHERE review_id=NEW.review_id;
 IF p.id IS NULL OR (p.coverage->>'obligations')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_obligations WHERE review_id=NEW.review_id)
 OR (p.coverage->>'signing_requirements')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_signing_requirements WHERE review_id=NEW.review_id)
 OR (p.coverage->>'required_locations')::int IS DISTINCT FROM (SELECT count(*) FROM public.bid_review_required_locations WHERE review_id=NEW.review_id) THEN
 RAISE EXCEPTION 'Review publication is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_output_guard()
CREATE FUNCTION public.bid_review_output_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.bid_review_runs%ROWTYPE; j public.jobs%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 p public.bid_document_pages%ROWTYPE; c public.bid_signing_candidates%ROWTYPE;
BEGIN
 SELECT * INTO r FROM public.bid_review_runs WHERE id=NEW.review_id;
 SELECT * INTO j FROM public.jobs WHERE id=r.job_id FOR SHARE;
 SELECT * INTO g FROM public.bid_outbound_authorizations WHERE id=r.authorization_id;
 IF r.id IS NULL OR g.id IS NULL OR NOT g.allow_external OR j.status IS DISTINCT FROM 'running' OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT public.bid_review_run_live(NEW.task_id,j.actor_token_id)
 OR NOT public.bid_review_actor_live(NEW.task_id)
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.submission_id=NEW.submission_id AND a.revision>g.revision)
 OR EXISTS(SELECT 1 FROM public.bid_review_publications WHERE review_id=r.id) THEN
 RAISE EXCEPTION 'Live review attempt and grant required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_publications' THEN
  IF NEW.run_id<>j.run_id THEN RAISE EXCEPTION 'Review attempt mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.page_id IS NOT NULL THEN
   SELECT * INTO p FROM public.bid_document_pages WHERE id=NEW.page_id;
   IF p.preparation_id<>r.preparation_id OR p.submission_id<>r.submission_id THEN
    RAISE EXCEPTION 'Review page parent mismatch' USING ERRCODE='23514'; END IF;
   IF TG_TABLE_NAME='bid_review_required_locations' THEN
    IF p.role<>'bid' OR (NEW.group_id IS NOT NULL AND NEW.group_id<>p.document_id::text) THEN RAISE EXCEPTION 'Required location must be a bid page in its group' USING ERRCODE='23514'; END IF;
   ELSE
    IF p.role<>'tender' THEN RAISE EXCEPTION 'Review quote must be tender text' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='bid_review_obligations' AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages a
     WHERE a.authorization_id=r.authorization_id AND a.page_id=NEW.page_id) THEN
     RAISE EXCEPTION 'Obligation page was not authorized' USING ERRCODE='23514'; END IF;
   END IF;
  END IF;
  IF TG_TABLE_NAME='bid_review_signing_requirements' THEN
   IF NEW.candidate_id IS NOT NULL THEN
   SELECT * INTO c FROM public.bid_signing_candidates WHERE id=NEW.candidate_id;
   IF c.submission_id<>r.submission_id OR c.preparation_id<>r.preparation_id OR c.page_id<>NEW.page_id THEN
    RAISE EXCEPTION 'Signing candidate parent mismatch' USING ERRCODE='23514'; END IF;
   END IF;
   IF (NEW.candidate_id IS NULL OR NEW.applicability<>'unknown') AND NOT EXISTS(SELECT 1 FROM public.bid_outbound_authorized_pages a WHERE a.authorization_id=r.authorization_id AND a.page_id=NEW.page_id) THEN
    RAISE EXCEPTION 'Signing confirmation page was not authorized' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_output_immutable()
CREATE FUNCTION public.bid_review_output_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME IN ('bid_review_obligations','bid_review_signing_requirements')
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Review inputs and publication are immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: bid_review_preparation_guard()
CREATE FUNCTION public.bid_review_preparation_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE j public.jobs%ROWTYPE; p public.bid_submissions%ROWTYPE;
BEGIN
 PERFORM public.bid_review_human(NEW.task_id,'bid-review:prepare');
 SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
 SELECT * INTO p FROM public.bid_submissions WHERE id=NEW.submission_id;
 IF j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.task_id IS DISTINCT FROM NEW.task_id
 OR j.document_id IS NOT NULL
 OR j.actor_kind IS DISTINCT FROM 'session' OR j.actor_token_id IS NOT NULL
 OR NOT(j.actor_scopes ? 'bid-review:prepare') OR p.state<>'uploaded'
 OR EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE submission_id=NEW.submission_id) THEN
  RAISE EXCEPTION 'Invalid uploaded-bid preparation binding' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_report_immutable()
CREATE FUNCTION public.bid_review_report_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME='bid_review_report_snapshots'
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Report snapshots and artifacts are immutable' USING ERRCODE='23514';
END $$;

-- FUNCTION: bid_review_report_insert_guard()
CREATE FUNCTION public.bid_review_report_insert_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE frozen public.bid_review_report_snapshots%ROWTYPE;
 job_row public.jobs%ROWTYPE;
 run_row public.bid_review_runs%ROWTYPE; publication public.bid_review_publications%ROWTYPE;
 report_scope text;
BEGIN
 IF TG_TABLE_NAME='bid_review_report_snapshots' THEN
  frozen := NEW;
 ELSE
  SELECT * INTO frozen FROM public.bid_review_report_snapshots WHERE id=NEW.snapshot_id;
 END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=frozen.job_id;
 SELECT * INTO run_row FROM public.bid_review_runs WHERE id=frozen.review_id;
 SELECT * INTO publication FROM public.bid_review_publications WHERE id=frozen.publication_id;
 report_scope := 'bid-review:report:render';
 IF frozen.id IS NULL OR job_row.id IS NULL OR run_row.id IS NULL OR publication.id IS NULL
 OR (publication.task_id,publication.submission_id,publication.review_id)
 IS DISTINCT FROM (frozen.task_id,frozen.submission_id,frozen.review_id)
 OR frozen.report_input_hash IS DISTINCT FROM run_row.input_hash
 OR job_row.kind IS DISTINCT FROM 'bid_review_report'
 OR (job_row.task_id,job_row.bid_submission_document_id)
 IS DISTINCT FROM (frozen.task_id,run_row.tender_document_id)
 OR job_row.result->'submission'->>'snapshot_id' IS DISTINCT FROM frozen.id::text
 OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM frozen.input_hash
 OR job_row.actor_kind IS DISTINCT FROM 'session' OR job_row.actor_token_id IS NOT NULL
 OR job_row.agent_principal_id IS NOT NULL OR NOT (job_row.actor_scopes ? report_scope)
 OR NOT public.bid_review_actor_live(frozen.task_id)
 OR NOT EXISTS(SELECT 1 FROM public.bid_submissions WHERE id=frozen.submission_id AND state='uploaded')
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NOT (COALESCE(current_setting('app.actor_scopes',true),'[]')::jsonb ? report_scope)
 THEN RAISE EXCEPTION 'Report publication requires current human task authority' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='bid_review_report_snapshots' THEN
  IF NOT public.actor_is_human() OR job_row.status<>'queued' THEN
   RAISE EXCEPTION 'Report snapshot requires human admission' USING ERRCODE='42501'; END IF;
 ELSE
  IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
  OR job_row.status<>'running' OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR NEW.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
  OR job_row.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
  OR job_row.lease_until IS NULL OR job_row.lease_until<=now()
  THEN RAISE EXCEPTION 'Report artifact requires live worker ownership' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_report_job_guard()
CREATE FUNCTION public.bid_review_report_job_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF OLD.kind='bid_review_report' AND (NEW.kind IS DISTINCT FROM OLD.kind
 OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.document_id IS DISTINCT FROM OLD.document_id
 OR NEW.bid_submission_document_id IS DISTINCT FROM OLD.bid_submission_document_id
 OR NEW.actor_token_id IS DISTINCT FROM OLD.actor_token_id
 OR NEW.actor_kind IS DISTINCT FROM OLD.actor_kind OR NEW.actor_scopes IS DISTINCT FROM OLD.actor_scopes
 OR NEW.result->'submission' IS DISTINCT FROM OLD.result->'submission'
 OR (OLD.status='succeeded' AND NEW.status IS DISTINCT FROM OLD.status)) THEN
 RAISE EXCEPTION 'Report job source and authority are immutable' USING ERRCODE='23514'; END IF;
 IF NEW.kind='bid_review_report' AND NEW.status='succeeded' AND
 NOT EXISTS(SELECT 1 FROM public.bid_review_report_snapshots frozen WHERE frozen.job_id=NEW.id
 AND 2=(SELECT count(*) FROM public.bid_review_report_artifacts artifact_row WHERE artifact_row.snapshot_id=frozen.id)) THEN
 RAISE EXCEPTION 'Succeeded report requires both immutable artifacts' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_report_pair_complete()
CREATE FUNCTION public.bid_review_report_pair_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE frozen public.bid_review_report_snapshots%ROWTYPE; job_row public.jobs%ROWTYPE;
BEGIN
 SELECT * INTO frozen FROM public.bid_review_report_snapshots WHERE id=NEW.snapshot_id;
 SELECT * INTO job_row FROM public.jobs WHERE id=frozen.job_id;
 IF (SELECT count(*) FROM public.bid_review_report_artifacts WHERE snapshot_id=NEW.snapshot_id)<>2
 OR job_row.status<>'succeeded' OR job_row.run_id IS DISTINCT FROM NEW.run_id
 OR EXISTS(SELECT 1 FROM public.bid_review_report_artifacts artifact_row WHERE artifact_row.snapshot_id=NEW.snapshot_id AND artifact_row.run_id IS DISTINCT FROM NEW.run_id)
 THEN RAISE EXCEPTION 'Word and console report artifacts must publish together' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_run_guard()
CREATE FUNCTION public.bid_review_run_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE j public.jobs%ROWTYPE; g public.bid_outbound_authorizations%ROWTYPE;
 d public.bid_submission_documents%ROWTYPE;
BEGIN
 SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
 SELECT * INTO g FROM public.bid_outbound_authorizations WHERE id=NEW.authorization_id;
 SELECT * INTO d FROM public.bid_submission_documents WHERE id=NEW.tender_document_id;
 IF j.id IS NULL OR j.kind<>'bid_review' OR j.task_id<>NEW.task_id
 OR j.bid_submission_document_id<>NEW.tender_document_id OR d.role<>'tender'
 OR g.id IS NULL OR NOT public.bid_review_actor_live(NEW.task_id)
 OR d.submission_id<>NEW.submission_id OR g.submission_id<>NEW.submission_id
 OR g.preparation_id<>NEW.preparation_id OR NOT g.allow_external
 OR (NOT public.actor_is_human() AND (current_setting('app.actor_kind',true) IS DISTINCT FROM 'token'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NULL))
 OR j.actor_token_id IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid
 OR NOT(j.actor_scopes ? 'bid-review:run')
 OR NOT public.bid_review_run_live(NEW.task_id,j.actor_token_id)
 OR NEW.input_hash IS DISTINCT FROM j.result->'submission'->>'input_hash'
 OR NEW.manifest IS DISTINCT FROM j.result->'submission'->'input_manifest'
 OR EXISTS(SELECT 1 FROM public.bid_outbound_authorizations a WHERE a.submission_id=NEW.submission_id AND a.revision>g.revision) THEN
 RAISE EXCEPTION 'Review requires live exact outbound authority' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_run_live(uuid, uuid)
CREATE FUNCTION public.bid_review_run_live(p_task uuid, p_token uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.tasks task_row WHERE task_row.id=p_task)
 AND (p_token IS NULL OR EXISTS(SELECT 1 FROM public.api_tokens token_row
 WHERE token_row.id=p_token AND NOT token_row.revoked AND token_row.expires_at>clock_timestamp()
 AND token_row.scopes ? 'bid-review:run'))
$$;

-- FUNCTION: bid_review_submission_guard()
CREATE FUNCTION public.bid_review_submission_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_submissions%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='bid_submissions' THEN
  PERFORM public.bid_review_human(NEW.task_id,'bid-review:upload');
  IF NEW.state<>'uploaded' THEN RAISE EXCEPTION 'Initial submission must be uploaded' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO p FROM public.bid_submissions WHERE id=NEW.submission_id;
  PERFORM public.bid_review_human(NEW.task_id,'bid-review:upload');
  IF p.id IS NULL OR p.state<>'uploaded' THEN
   RAISE EXCEPTION 'Original actor mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_review_upload_complete()
CREATE FUNCTION public.bid_review_upload_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_submissions%ROWTYPE; n bigint; bytes bigint; roles bigint; first integer; last integer;
BEGIN
 IF TG_TABLE_NAME='bid_submissions' THEN
  SELECT * INTO p FROM public.bid_submissions WHERE id=NEW.id;
 ELSE
  SELECT * INTO p FROM public.bid_submissions WHERE id=NEW.submission_id;
 END IF;
 SELECT count(*),sum(size_bytes),count(DISTINCT role),min(ordinal),max(ordinal)
 INTO n,bytes,roles,first,last FROM public.bid_submission_documents
 WHERE submission_id=p.id;
 IF n<>p.file_count OR bytes<>p.total_bytes OR roles<>2 OR first<>1 OR last<>n THEN
  RAISE EXCEPTION 'Uploaded manifest is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_signing_admission_complete()
CREATE FUNCTION public.bid_signing_admission_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.bid_preparation_trust WHERE preparation_id=NEW.id) THEN
  RAISE EXCEPTION 'Preparation requires pinned trust' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_signing_complete()
CREATE FUNCTION public.bid_signing_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_preparation_publications%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparation_publications WHERE preparation_id=NEW.preparation_id;
 IF TG_TABLE_NAME='bid_preparation_trust' THEN RETURN NEW; END IF;
 IF p.id IS NULL OR
 NOT EXISTS(SELECT 1 FROM public.bid_preparation_trust t WHERE t.preparation_id=p.preparation_id) OR
 EXISTS(SELECT 1 FROM public.bid_prepared_documents d WHERE d.preparation_id=p.preparation_id
 AND NOT EXISTS(SELECT 1 FROM public.bid_pdf_validations v WHERE (v.task_id,v.submission_id,v.preparation_id,v.document_id)
 =(d.task_id,d.submission_id,d.preparation_id,d.document_id))) THEN
  RAISE EXCEPTION 'Local signing inventory is incomplete' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_signing_immutable()
CREATE FUNCTION public.bid_signing_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    SET "TimeZone" TO 'UTC'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND TG_TABLE_NAME IN ('bid_pdf_validations','bid_signing_candidates')
 AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
 AND (to_jsonb(NEW)-'details_encrypted')=(to_jsonb(OLD)-'details_encrypted') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Local signing evidence is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: bid_signing_inventory_guard()
CREATE FUNCTION public.bid_signing_inventory_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_preparations%ROWTYPE; j public.jobs%ROWTYPE; d public.bid_submission_documents%ROWTYPE; b public.bid_document_pages%ROWTYPE;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE id=NEW.preparation_id;
 SELECT * INTO j FROM public.jobs WHERE id=p.job_id FOR SHARE;
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
 OR NULLIF(current_setting('app.agent_principal_id',true),'') IS NOT NULL
 OR j.id IS DISTINCT FROM NULLIF(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM NULLIF(current_setting('app.execution_run_id',true),'')::uuid
 OR j.kind IS DISTINCT FROM 'bid_review_prepare' OR j.status IS DISTINCT FROM 'running'
 OR j.run_id IS NULL OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
 OR NOT(j.actor_scopes ? 'bid-review:prepare')
 OR NOT public.bid_review_actor_live(NEW.task_id)
 OR EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE submission_id=NEW.submission_id) THEN
  RAISE EXCEPTION 'Current unpublished local preparation lease required' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.bid_submission_documents WHERE id=NEW.document_id;
 IF TG_TABLE_NAME='bid_pdf_validations' THEN
  IF NEW.original_sha256 IS DISTINCT FROM d.sha256 THEN
   RAISE EXCEPTION 'Signature original identity mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO b FROM public.bid_document_pages WHERE id=NEW.page_id;
  IF d.role<>'tender' OR b.text_status<>'native' THEN
   RAISE EXCEPTION 'Signing candidate needs native tender source' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: bid_signing_trust_guard()
CREATE FUNCTION public.bid_signing_trust_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p public.bid_preparations%ROWTYPE; snapshot jsonb;
BEGIN
 SELECT * INTO p FROM public.bid_preparations WHERE id=NEW.preparation_id;
 PERFORM public.bid_review_human(NEW.task_id,'bid-review:prepare');
 IF p.id IS NULL OR
 EXISTS(SELECT 1 FROM public.bid_preparation_publications WHERE preparation_id=NEW.preparation_id) THEN
  RAISE EXCEPTION 'Preparation trust actor mismatch' USING ERRCODE='23514'; END IF;
 -- Serialize admission with anchor writes so a preparation pins exactly one enabled store.
 PERFORM pg_advisory_xact_lock(862062001);
 snapshot:=public.platform_trust_anchor_snapshot();
 IF NEW.trust_store_sha256 IS DISTINCT FROM snapshot->>'sha256' OR NEW.anchors IS DISTINCT FROM snapshot->'anchors' THEN
  RAISE EXCEPTION 'Preparation trust snapshot changed' USING ERRCODE='23514', CONSTRAINT='bid_signing_trust_snapshot'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: export_audit_gate()
CREATE FUNCTION public.export_audit_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE j public.jobs%ROWTYPE;
    BEGIN
      IF NEW.action NOT LIKE 'export.%' THEN RETURN NEW; END IF;
      IF NEW.actor_token_id IS NOT NULL
        OR NULLIF(current_setting('app.actor_token_id',true),'') IS NOT NULL
        OR NEW.details->>'actor_kind' IS DISTINCT FROM current_setting('app.actor_kind',true) THEN
        RAISE EXCEPTION 'Export audit actor context mismatch' USING ERRCODE='42501';
      END IF;
      IF NEW.action IN ('export.render_completed','export.render_failed') THEN
        SELECT * INTO j FROM public.jobs WHERE id=(NEW.details->>'render_job_id')::uuid;
        IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
          OR j.id IS NULL OR j.kind<>'export_render'
          OR j.run_id IS DISTINCT FROM (NEW.details->>'attempt_id')::uuid
          OR NEW.object_id IS DISTINCT FROM (NEW.details->>'run_id')::uuid
          OR NEW.details->>'run_id' IS DISTINCT FROM current_setting('app.export_run_id',true)
          OR NEW.details->>'attempt_id' IS DISTINCT FROM current_setting('app.export_attempt_id',true)
          OR j.result->'submission'->>'export_run_id' IS DISTINCT FROM NEW.object_id::text THEN
          RAISE EXCEPTION 'Export worker audit is not bound to this attempt' USING ERRCODE='42501';
        END IF;
        IF NEW.action='export.render_completed' AND
          (public.export_worker(NEW.object_id,j.id) IS DISTINCT FROM true
            OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c WHERE c.run_id=NEW.object_id AND c.attempt_id=j.run_id)) THEN
          RAISE EXCEPTION 'Export completion audit requires a current candidate' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action='export.binding_created' THEN
        PERFORM public.response_require_human();
        IF NOT EXISTS (SELECT 1 FROM public.export_template_bindings b WHERE b.id=NEW.object_id AND b.reviewed_at IS NOT NULL) THEN
          RAISE EXCEPTION 'Export binding audit has no binding' USING ERRCODE='23514';
        END IF;
      ELSIF NEW.action IN ('export.prepared','export.released','export.render_cancelled',
        'export.download_link_issued','export.download_served','export.preview_opened') THEN
        PERFORM public.response_require_human();
        IF NEW.action IN ('export.prepared','export.render_cancelled') THEN
          IF NOT EXISTS (SELECT 1 FROM public.export_runs r WHERE r.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export run audit has no run' USING ERRCODE='23514';
          END IF;
        ELSE
          IF NOT EXISTS (SELECT 1 FROM public.exports e WHERE e.id=NEW.object_id) THEN
            RAISE EXCEPTION 'Export file audit has no release' USING ERRCODE='23514';
          END IF;
        END IF;
      ELSE RAISE EXCEPTION 'Unknown export audit event' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;

-- FUNCTION: export_complete_gate()
CREATE FUNCTION public.export_complete_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE r public.export_runs%ROWTYPE; j public.jobs%ROWTYPE; expected integer; actual integer;
    BEGIN
      IF TG_TABLE_NAME='export_runs' THEN r:=NEW;
      ELSE SELECT * INTO r FROM public.export_runs WHERE id=NEW.run_id; END IF;
      IF r.id IS NULL THEN RAISE EXCEPTION 'Export run unavailable' USING ERRCODE='42501'; END IF;
      -- The same task/card locks are used by response review and selection writers.
      PERFORM 1 FROM public.tasks WHERE id=r.task_id FOR UPDATE;
      PERFORM 1 FROM public.response_cards c WHERE c.task_id=r.task_id ORDER BY c.id FOR UPDATE;
      SELECT * INTO j FROM public.jobs WHERE id=r.render_job_id FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM public.task_templates t WHERE t.id=r.task_template_id AND t.task_id=r.task_id AND t.active
        AND t.template_revision_id=r.template_revision_id) THEN
        RAISE EXCEPTION 'Export template selection changed' USING ERRCODE='23514';
      END IF;
      SELECT count(*) INTO expected FROM public.response_items WHERE draft_id=r.draft_run_id;
      SELECT count(*) INTO actual FROM public.export_run_items WHERE run_id=r.id;
      IF expected=0 OR actual<>expected OR actual>2000 OR expected<>(SELECT count(*)
        FROM public.requirements q WHERE q.task_id=r.task_id AND q.job_id=r.extraction_job_id) OR EXISTS (
        SELECT 1 FROM public.response_items i WHERE i.draft_id=r.draft_run_id
        AND NOT EXISTS (SELECT 1 FROM public.export_run_items e WHERE e.run_id=r.id AND e.response_item_id=i.id)) THEN
        RAISE EXCEPTION 'Export must include complete draft coverage' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i
        LEFT JOIN public.response_cards c ON c.id=i.card_id
        LEFT JOIN public.response_card_revisions v ON v.id=i.card_revision_id
        WHERE i.draft_id=r.draft_run_id AND (
          public.response_citation_valid(i.requirement_id) IS DISTINCT FROM true
          OR (i.kind='gap' AND r.mode='final_section')
          OR (i.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM i.card_revision_id)
          OR (i.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards n
              WHERE n.requirement_id=i.requirement_id))
          OR (i.kind IN ('row','comply_only') AND
              public.response_quote_current(i.card_revision_id) IS DISTINCT FROM true)
          OR (i.kind='row' AND (v.state IS DISTINCT FROM 'confirmed'
              OR v.confirmed_at IS NULL OR v.disposition IS DISTINCT FROM 'respond'
              OR public.response_generation_materials_active(v.model_job_id) IS DISTINCT FROM true))
          OR (i.kind='comply_only' AND (v.disposition IS DISTINCT FROM 'comply_only'
              OR v.disposition_at IS NULL)))) THEN
        RAISE EXCEPTION 'Export response or citation is stale or unconfirmed' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.export_run_items i
        JOIN public.response_card_revisions v ON v.id=i.card_revision_id
        WHERE i.run_id=r.id AND (
          (i.kind<>'row' AND EXISTS(SELECT 1 FROM public.export_run_evidence e
            WHERE e.run_item_id=i.id))
          OR (i.kind='row' AND v.response_kind='commitment' AND
            EXISTS(SELECT 1 FROM public.card_evidence_links l WHERE l.revision_id=v.id))
          OR (i.kind='row' AND v.response_kind='evidence' AND NOT EXISTS
            (SELECT 1 FROM public.card_evidence_links l WHERE l.revision_id=v.id))
          OR (i.kind='row' AND EXISTS (SELECT 1 FROM public.card_evidence_links l
            WHERE l.revision_id=v.id AND NOT EXISTS
              (SELECT 1 FROM public.export_run_evidence e WHERE e.run_item_id=i.id AND e.evidence_id=l.evidence_id)))))
        OR EXISTS (SELECT 1 FROM public.export_run_evidence l JOIN public.evidence e
          ON e.id=l.evidence_id WHERE l.run_id=r.id
            AND (e.confirmed_at IS NULL
              OR public.response_evidence_active(e.id) IS DISTINCT FROM true)) THEN
        RAISE EXCEPTION 'Export evidence set is incomplete or invalid' USING ERRCODE='23514';
      END IF;
      IF jsonb_typeof(r.manifest->'items') IS DISTINCT FROM 'array'
        OR jsonb_array_length(r.manifest->'items')<>actual
        OR EXISTS (SELECT 1 FROM public.export_run_items e WHERE e.run_id=r.id
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
            WHERE item->>'response_item_id'=e.response_item_id::text
              AND item->>'requirement_id'=e.requirement_id::text
              AND item->>'card_revision_id' IS NOT DISTINCT FROM e.card_revision_id::text
              AND item->>'kind'=e.kind AND (item->>'ordinal')::integer=e.ordinal)) THEN
        RAISE EXCEPTION 'Export manifest differs from relational snapshot' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
        JOIN public.response_items i ON i.id=(item->>'response_item_id')::uuid
        LEFT JOIN public.response_card_revisions v ON v.id=i.card_revision_id
        WHERE item->'source' IS DISTINCT FROM i.source
          OR item->>'card_id' IS DISTINCT FROM i.card_id::text
          OR item->>'category' IS DISTINCT FROM i.category
          OR (item->>'starred')::boolean IS DISTINCT FROM i.starred
          OR item->>'location_label' IS DISTINCT FROM i.location_label
          OR jsonb_typeof(item->'evidence') IS DISTINCT FROM 'array'
          OR (i.kind='row' AND (
            ROW(item->>'response_kind',item->>'response_text',item->>'deviation',item->>'deviation_note',item->>'table')
              IS DISTINCT FROM ROW(i.response_kind,i.response_text,i.deviation,i.deviation_note,i."table")
            OR (item->>'confirmed_at')::timestamptz IS DISTINCT FROM v.confirmed_at
            OR (item->>'disposition_at')::timestamptz IS DISTINCT FROM v.disposition_at))
          OR (i.kind='comply_only' AND ((item->>'disposition_at')::timestamptz IS DISTINCT FROM i.disposition_at))
          OR (i.kind='gap' AND item->'gap_reasons' IS DISTINCT FROM i.gap_reasons)
          OR (i.kind<>'row' AND (jsonb_array_length(item->'evidence')<>0
            OR item->>'response_text' IS NOT NULL OR item->>'response_kind' IS NOT NULL))) THEN
        RAISE EXCEPTION 'Export manifest content differs from confirmed snapshot' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'items') item
        JOIN public.export_run_items i ON i.run_id=r.id
          AND i.response_item_id=(item->>'response_item_id')::uuid
        WHERE jsonb_array_length(item->'evidence')<>(SELECT count(*) FROM public.export_run_evidence l
          WHERE l.run_item_id=i.id)
        OR EXISTS (SELECT 1 FROM public.export_run_evidence l
          JOIN public.evidence e ON e.id=l.evidence_id
          WHERE l.run_item_id=i.id AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(item->'evidence') material
            WHERE material->>'id'=e.id::text
              AND (material->>'confirmed_at')::timestamptz=e.confirmed_at
              AND material->'input'->>'kind'=e.kind AND material->'input'->>'quote' IS NOT DISTINCT FROM e.quote
              AND material->>'material_kind'=e.material_kind
              AND material->>'quote_check'=e.quote_check
              AND material->>'selection_id'=COALESCE(e.task_resource_id,e.task_feature_id,
                e.task_certificate_id,e.task_org_profile_id)::text
              AND material->>'resource_revision_id'=COALESCE(e.product_revision_id,e.feature_revision_id,
                e.certificate_revision_id,e.profile_revision_id)::text
              AND (material->>'attachment_ordinal')::integer IS NOT DISTINCT FROM l.attachment_ordinal
              AND (CASE WHEN e.kind='certificate_pdf_page' THEN EXISTS (
                SELECT 1 FROM public.evidence_sources src JOIN public.certificate_files original
                  ON original.id=src.certificate_file_id
                WHERE src.id=e.evidence_source_id
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
                WHEN e.kind='image_region' THEN
                  material->>'screenshot_rendition_id'=e.screenshot_rendition_id::text
                  AND material->>'image_sha256'=e.image_sha256
                  AND material->'input'->>'rendition_id'=e.screenshot_rendition_id::text
                  AND l.attachment_ordinal IS NOT NULL
                  AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.manifest->'attachments') attachment
                    WHERE (attachment->>'ordinal')::integer=l.attachment_ordinal
                      AND attachment->>'kind'='image'
                      AND attachment->>'rendition_id'=e.screenshot_rendition_id::text
                      AND attachment->>'png_sha256'=e.image_sha256 AND public.annotation_export_binding(e.id,i.card_revision_id,attachment))
                  AND public.export_prototype_kept(r.mode,e.id,i.card_revision_id,
                    l.prototype_decision_id)
                  AND (l.prototype_decision_id IS NULL OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(r.manifest->'prototype_decisions'->'decisions') d
                    WHERE d->>'evidence_id'=e.id::text AND d->>'decision'='keep'
                      AND d->>'decision_id'=l.prototype_decision_id::text))
                ELSE l.prototype_decision_id IS NULL
                  AND material->'input'->>'field_path'=e.field_path
                  AND material->'input'->>'selection_id'=material->>'selection_id'
                  AND l.attachment_ordinal IS NULL END)))) THEN
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
          OR public.export_worker(r.id,r.render_job_id) IS DISTINCT FROM true THEN
          RAISE EXCEPTION 'Export attempt is no longer current' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='exports' THEN
        IF public.export_human() IS DISTINCT FROM true
          OR j.status<>'succeeded' OR NOT EXISTS (SELECT 1 FROM public.export_render_candidates c
            WHERE c.id=NEW.candidate_id AND c.attempt_id=j.run_id) THEN
          RAISE EXCEPTION 'Export publication requires current successful candidate' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;

-- FUNCTION: export_human()
CREATE FUNCTION public.export_human() RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$ SELECT public.actor_is_human() $$;

-- FUNCTION: export_insert_gate()
CREATE FUNCTION public.export_insert_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE r public.export_runs%ROWTYPE; c public.export_render_candidates%ROWTYPE;
      j public.jobs%ROWTYPE; xid text;
    BEGIN
      IF TG_TABLE_NAME='export_template_bindings' THEN
        PERFORM public.response_require_human();
        IF NOT EXISTS (
          SELECT 1 FROM public.template_revisions t WHERE t.id=NEW.template_revision_id AND t.file->>'sha256'=NEW.template_sha256) THEN
          RAISE EXCEPTION 'Invalid template binding actor or hash' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='export_runs' THEN
        PERFORM public.response_require_human();
        IF NOT EXISTS (SELECT 1 FROM public.draft_runs d JOIN public.jobs x
          ON x.id=d.extraction_job_id
          JOIN public.jobs g ON g.id=d.generation_job_id
          JOIN public.documents doc ON doc.id=x.document_id
          JOIN public.task_templates t ON t.task_id=d.task_id
          JOIN public.jobs rj ON rj.task_id=d.task_id
          WHERE d.id=NEW.draft_run_id AND d.task_id=NEW.task_id
            AND d.extraction_job_id=NEW.extraction_job_id AND x.kind='extract'
            AND x.document_id=NEW.document_id AND doc.task_id=NEW.task_id
            AND g.status='succeeded' AND g.run_id=d.generation_run_id
            AND t.id=NEW.task_template_id AND t.template_revision_id=NEW.template_revision_id
            AND t.active AND rj.id=NEW.render_job_id AND rj.document_id=NEW.document_id
            AND rj.kind='export_render' AND rj.status='queued') THEN
          RAISE EXCEPTION 'Export input relationship invalid' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT * INTO r FROM public.export_runs WHERE id=NEW.run_id;
        IF r.id IS NULL THEN RAISE EXCEPTION 'Export run unavailable' USING ERRCODE='42501'; END IF;
        IF TG_TABLE_NAME IN ('export_run_items','export_run_evidence') THEN
          PERFORM public.response_require_human();
          SELECT xmin::text INTO xid FROM public.export_runs WHERE id=r.id;
          IF xid IS DISTINCT FROM pg_current_xact_id()::xid::text THEN
            RAISE EXCEPTION 'Export snapshot is closed' USING ERRCODE='42501';
          END IF;
          IF TG_TABLE_NAME='export_run_items' THEN
          IF NOT EXISTS (
            SELECT 1 FROM public.response_items i WHERE i.id=NEW.response_item_id AND i.draft_id=r.draft_run_id
              AND i.requirement_id=NEW.requirement_id AND i.kind=NEW.kind
              AND i.card_revision_id IS NOT DISTINCT FROM NEW.card_revision_id) THEN
            RAISE EXCEPTION 'Export item relationship invalid' USING ERRCODE='23514';
          END IF;
          END IF;
          IF TG_TABLE_NAME='export_run_evidence' THEN
          IF NOT EXISTS (
            SELECT 1 FROM public.export_run_items i JOIN public.evidence e
              ON e.id=NEW.evidence_id
              JOIN public.response_card_revisions v ON v.id=i.card_revision_id
            WHERE i.id=NEW.run_item_id AND i.run_id=r.id
              AND i.kind='row' AND v.response_kind='evidence' AND e.card_id=v.card_id
              AND e.task_id=r.task_id AND e.confirmed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Export evidence relationship invalid' USING ERRCODE='23514';
          END IF;
          END IF;
        ELSIF TG_TABLE_NAME='export_render_candidates' THEN
          SELECT * INTO j FROM public.jobs WHERE id=r.render_job_id ;
          IF public.export_worker(r.id,r.render_job_id) IS DISTINCT FROM true
            OR j.status<>'running' OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
            OR NEW.attempt_id IS DISTINCT FROM j.run_id THEN
            RAISE EXCEPTION 'Bound active export worker required' USING ERRCODE='42501';
          END IF;
          IF ROW(NEW.render_job_id,NEW.input_hash,NEW.manifest_hash,NEW.renderer_profile)
             IS DISTINCT FROM ROW(r.render_job_id,r.input_hash,r.manifest_hash,r.renderer_profile) THEN
            RAISE EXCEPTION 'Candidate input mismatch' USING ERRCODE='23514';
          END IF;
          IF EXISTS (SELECT 1 FROM public.export_render_candidates prior
            WHERE prior.run_id=NEW.run_id
              AND prior.plaintext_sha256<>NEW.plaintext_sha256) THEN
            RAISE EXCEPTION 'Export renderer is nondeterministic' USING ERRCODE='23514';
          END IF;
        ELSIF TG_TABLE_NAME='exports' THEN
          PERFORM public.response_require_human();
          SELECT * INTO c FROM public.export_render_candidates WHERE id=NEW.candidate_id;
          SELECT * INTO j FROM public.jobs WHERE id=r.render_job_id ;
          IF c.id IS NULL OR c.run_id<>r.id OR j.status<>'succeeded' OR c.attempt_id IS DISTINCT FROM j.run_id
            OR ROW(NEW.task_id,NEW.mode,NEW.input_hash,NEW.manifest_hash)
              IS DISTINCT FROM ROW(r.task_id,r.mode,r.input_hash,r.manifest_hash)
            OR ROW(NEW.file_sha256,NEW.size_bytes) IS DISTINCT FROM ROW(c.plaintext_sha256,c.size_bytes) THEN
            RAISE EXCEPTION 'Export candidate is not publishable' USING ERRCODE='23514';
          END IF;
          IF EXISTS (SELECT 1 FROM public.export_render_candidates prior
            WHERE prior.input_hash=r.input_hash
              AND prior.renderer_profile=r.renderer_profile
              AND prior.plaintext_sha256<>c.plaintext_sha256) THEN
            RAISE EXCEPTION 'Export renderer is nondeterministic' USING ERRCODE='23514';
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;

-- FUNCTION: export_prototype_kept(text, uuid, uuid, uuid)
CREATE FUNCTION public.export_prototype_kept(p_mode text, p_evidence uuid, p_revision uuid, p_decision uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
    DECLARE e public.evidence; d public.prototype_evidence_decisions; a public.screenshot_assets;
    BEGIN
      SELECT * INTO e FROM public.evidence WHERE id=p_evidence;
      SELECT * INTO a FROM public.screenshot_assets x
        WHERE x.id=e.screenshot_asset_id;
      -- Review copies and non-prototype images never depend on a decision.
      IF p_mode<>'final_section' OR a.origin IS DISTINCT FROM 'prototype' THEN
        RETURN p_decision IS NULL;
      END IF;
      SELECT * INTO d FROM public.prototype_evidence_decisions x
        WHERE x.evidence_id=p_evidence
        ORDER BY x.created_at DESC, x.id DESC LIMIT 1;
      RETURN d.id IS NOT NULL AND d.id=p_decision AND d.decision='keep'
        AND d.card_revision_id=p_revision AND d.asset_id=e.screenshot_asset_id
        AND d.rendition_id=e.screenshot_rendition_id AND d.image_sha256=e.image_sha256
        AND d.task_feature_id=a.task_feature_id AND d.feature_revision_id=a.feature_revision_id;
    END $$;

-- FUNCTION: export_worker(uuid, uuid)
CREATE FUNCTION public.export_worker(p_run uuid, p_job uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
      SELECT current_setting('app.actor_kind',true)='worker'
        AND NULLIF(current_setting('app.actor_token_id',true),'') IS NULL
        AND p_run=NULLIF(current_setting('app.export_run_id',true),'')::uuid
        AND EXISTS (SELECT 1 FROM public.jobs j WHERE j.id=p_job
          AND j.kind='export_render' AND j.status IN ('running','succeeded')
          AND j.run_id=NULLIF(current_setting('app.export_attempt_id',true),'')::uuid
          AND ((j.status='running' AND j.lease_until>clock_timestamp())
            OR (j.status='succeeded' AND j.xmin::text=pg_current_xact_id()::xid::text)))
    $$;

-- FUNCTION: sandbox_artifact_gate()
CREATE FUNCTION public.sandbox_artifact_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE a public.sandbox_attempts; i public.sandbox_inputs; p public.sandbox_artifacts;
    BEGIN
        SELECT * INTO a FROM public.sandbox_attempts WHERE id=NEW.attempt_record_id;
        SELECT * INTO i FROM public.sandbox_inputs WHERE id=NEW.input_id;
        IF a.completion_txid IS DISTINCT FROM txid_current() OR a.ended_at IS NULL OR a.cleanup_state<>'complete' OR a.termination_code<>'succeeded'
            OR NEW.object_key IS DISTINCT FROM 'sandbox-artifacts/' || NEW.sandbox_run_id::text || '/' || a.attempt_id::text || '/' || NEW.id::text || '/' || NEW.plaintext_sha256
            OR (i.purpose='prototype_offline') IS DISTINCT FROM (NEW.origin='prototype')
            OR (i.purpose='prototype_offline' AND NEW.kind NOT IN ('prototype_png','rendered_html','provenance_manifest')) THEN
            RAISE EXCEPTION 'Sandbox artifact binding mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.parent_artifact_id IS NOT NULL THEN
            SELECT * INTO p FROM public.sandbox_artifacts WHERE id=NEW.parent_artifact_id;
            IF NOT FOUND OR p.ordinal>=NEW.ordinal THEN
                RAISE EXCEPTION 'Sandbox artifact parent must precede child' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $$;

-- FUNCTION: sandbox_attempt_gate()
CREATE FUNCTION public.sandbox_attempt_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF TG_OP='UPDATE' THEN
            IF OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL THEN
                RAISE EXCEPTION 'Sandbox terminal attempts are immutable' USING ERRCODE='23514';
            END IF;
            NEW.completion_txid := txid_current();
        ELSE
            IF NEW.ended_at IS NOT NULL OR NOT EXISTS (SELECT 1 FROM public.jobs
                WHERE id=NEW.job_id AND kind='sandbox' AND status='running'
                AND run_id=NEW.attempt_id AND lease_until>clock_timestamp()) THEN
                RAISE EXCEPTION 'Sandbox attempt has no live lease' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $$;

-- FUNCTION: sandbox_complete_gate()
CREATE FUNCTION public.sandbox_complete_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
    DECLARE a public.sandbox_attempts; j public.jobs; i public.sandbox_inputs;
        n integer; bytes bigint; manifest text; actual_origin text;
    BEGIN
        SELECT * INTO a FROM public.sandbox_attempts WHERE id=NEW.id;
        IF a.termination_code IS DISTINCT FROM 'succeeded' THEN RETURN NEW; END IF;
        SELECT * INTO j FROM public.jobs WHERE id=a.job_id;
        SELECT * INTO i FROM public.sandbox_inputs WHERE id=a.input_id;
        IF a.cleanup_state<>'complete' OR j.status<>'succeeded' OR j.run_id IS DISTINCT FROM a.attempt_id OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() OR jsonb_path_exists(a.issues, '$[*] ? (@.severity == "block")') THEN
            RAISE EXCEPTION 'Sandbox publication requires current completed attempt' USING ERRCODE='23514';
        END IF;
        SELECT count(*),sum(size_bytes),max(plaintext_sha256) FILTER(WHERE kind='provenance_manifest')
            INTO n,bytes,manifest FROM public.sandbox_artifacts
            WHERE attempt_record_id=a.id;
        IF n<3 OR n>32 OR bytes>(CASE WHEN i.purpose='prototype_offline' THEN 67108864 ELSE 134217728 END)
            OR manifest IS NULL OR EXISTS (SELECT 1 FROM public.sandbox_artifacts
                WHERE attempt_record_id=a.id AND provenance_manifest_hash<>manifest)
            OR (SELECT count(*) FROM public.sandbox_artifacts WHERE attempt_record_id=a.id AND kind='provenance_manifest')<>1 THEN
            RAISE EXCEPTION 'Sandbox publication set is incomplete' USING ERRCODE='23514';
        END IF;
        IF i.purpose='prototype_offline' THEN
            IF n<>3 OR (SELECT count(DISTINCT kind) FROM public.sandbox_artifacts
                WHERE attempt_record_id=a.id AND kind IN ('prototype_png','rendered_html','provenance_manifest'))<>3 THEN
                RAISE EXCEPTION 'Sandbox prototype set incomplete' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT EXISTS (SELECT 1 FROM public.sandbox_artifacts WHERE attempt_record_id=a.id AND kind='request_manifest') THEN
                RAISE EXCEPTION 'Sandbox request manifest missing' USING ERRCODE='23514';
            END IF;
            IF n IS DISTINCT FROM (CASE WHEN i.spec->>'format'='pdf' THEN jsonb_array_length(i.spec->'pdf_pages')+3 ELSE 4 END + CASE WHEN i.spec->>'archive'='bundle' THEN 1 ELSE 0 END) THEN
                RAISE EXCEPTION 'Sandbox capture set contains extra artifacts' USING ERRCODE='23514';
            END IF;
            IF i.spec->>'format'='pdf' THEN
                IF (SELECT count(*) FROM public.sandbox_artifacts WHERE attempt_record_id=a.id AND kind='source_pdf')<>1
                    OR (SELECT coalesce(jsonb_agg(page ORDER BY page),'[]'::jsonb) FROM public.sandbox_artifacts
                        WHERE attempt_record_id=a.id AND kind='pdf_page_png') IS DISTINCT FROM i.spec->'pdf_pages' THEN
                    RAISE EXCEPTION 'Sandbox PDF pages incomplete' USING ERRCODE='23514';
                END IF;
            ELSE
                IF (SELECT count(DISTINCT kind) FROM public.sandbox_artifacts WHERE attempt_record_id=a.id AND kind IN ('capture_png','rendered_html'))<>2 THEN
                    RAISE EXCEPTION 'Sandbox web set incomplete' USING ERRCODE='23514';
                END IF;
            END IF;
            IF i.spec->>'archive'='bundle' AND (SELECT count(*) FROM public.sandbox_artifacts
                WHERE attempt_record_id=a.id AND kind='capture_archive')<>1 THEN
                RAISE EXCEPTION 'Sandbox archive missing' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END; $_$;

-- FUNCTION: sandbox_input_gate()
CREATE FUNCTION public.sandbox_input_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.jobs j WHERE j.id=NEW.extraction_job_id AND j.task_id=NEW.task_id
            AND j.document_id=NEW.document_id AND j.kind='extract' AND j.status='succeeded') THEN
            RAISE EXCEPTION 'Sandbox requires a successful same-task extraction' USING ERRCODE='23514';
        END IF;
        IF NEW.purpose='prototype_offline' THEN
            IF NOT EXISTS (SELECT 1 FROM public.task_features s WHERE s.id=NEW.task_feature_id AND s.task_id=NEW.task_id
                AND s.feature_revision_id=NEW.feature_revision_id AND s.active) THEN
                RAISE EXCEPTION 'Sandbox selection is unavailable' USING ERRCODE='23514';
            END IF;
            IF NEW.spec->>'html_sha256' IS DISTINCT FROM NEW.html_sha256
                OR NEW.spec->>'task_feature_id' IS DISTINCT FROM NEW.task_feature_id::text
                OR NEW.spec->>'expected_feature_revision_id' IS DISTINCT FROM NEW.feature_revision_id::text
                OR (NEW.spec->>'html_size_bytes')::integer IS DISTINCT FROM NEW.size_bytes THEN
                RAISE EXCEPTION 'Sandbox prototype binding mismatch' USING ERRCODE='23514';
            END IF;
        ELSE
            IF NOT EXISTS (SELECT 1 FROM public.task_resources s WHERE s.id=NEW.task_resource_id AND s.task_id=NEW.task_id
                AND s.product_revision_id=NEW.product_revision_id AND s.active) THEN
                RAISE EXCEPTION 'Sandbox selection is unavailable' USING ERRCODE='23514';
            END IF;
            IF NEW.spec->>'source_field' IS DISTINCT FROM NEW.source_field
                OR NEW.spec->>'expected_source_url_sha256' IS DISTINCT FROM NEW.source_url_sha256
                OR NEW.spec->>'task_resource_id' IS DISTINCT FROM NEW.task_resource_id::text
                OR NEW.spec->>'expected_product_revision_id' IS DISTINCT FROM NEW.product_revision_id::text THEN
                RAISE EXCEPTION 'Sandbox vendor binding mismatch' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.spec->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
            RAISE EXCEPTION 'Sandbox extraction binding mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;

-- FUNCTION: sandbox_receipt_gate()
CREATE FUNCTION public.sandbox_receipt_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.sandbox_attempts WHERE id=NEW.attempt_record_id AND completion_txid=txid_current()) THEN
            RAISE EXCEPTION 'Sandbox receipts are sealed with their attempt' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.sandbox_runs r JOIN public.sandbox_inputs i
            ON (i.id)=(r.input_id) WHERE r.id=NEW.sandbox_run_id AND r.policy_revision=NEW.policy_revision AND i.purpose='vendor_capture') THEN
            RAISE EXCEPTION 'Sandbox receipt policy mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.bundle_artifact_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.sandbox_artifacts
            WHERE id=NEW.bundle_artifact_id AND kind='capture_archive') THEN
            RAISE EXCEPTION 'Sandbox receipt archive mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.parent_redirect_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.sandbox_fetch_receipts
            WHERE id=NEW.parent_redirect_id AND request_ordinal<NEW.request_ordinal) THEN
            RAISE EXCEPTION 'Sandbox receipt redirect order mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;

-- FUNCTION: sandbox_run_gate()
CREATE FUNCTION public.sandbox_run_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE p text;
    BEGIN
        SELECT purpose INTO p FROM public.sandbox_inputs WHERE id=NEW.input_id;
        IF (p='prototype_offline') IS DISTINCT FROM (NEW.profile='prototype-offline-v1')
            OR (p='vendor_capture') IS DISTINCT FROM (NEW.capture_key IS NOT NULL)
            OR NOT EXISTS (SELECT 1 FROM public.jobs WHERE id=NEW.job_id
                AND kind='sandbox' AND status='queued') THEN
            RAISE EXCEPTION 'Sandbox run binding mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.token_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.api_tokens
            WHERE id=NEW.token_id AND NOT revoked AND expires_at>clock_timestamp()) THEN
            RAISE EXCEPTION 'Sandbox actor mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END; $$;
