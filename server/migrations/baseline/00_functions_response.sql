-- FUNCTION: response_card_gate()
CREATE FUNCTION public.response_card_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'Cards cannot be deleted' USING ERRCODE='42501';
        ELSIF TG_OP='INSERT' THEN
            IF NEW.revision <> 1 OR NOT EXISTS (SELECT 1 FROM public.requirements r
              JOIN public.jobs j ON j.id=r.job_id
              WHERE r.id=NEW.requirement_id AND r.task_id=NEW.task_id
              AND r.job_id=NEW.extraction_job_id AND j.task_id=NEW.task_id
              AND j.document_id=r.document_id AND j.kind='extract' AND j.status='succeeded') THEN
                RAISE EXCEPTION 'Invalid response requirement binding' USING ERRCODE='23514';
            END IF;
        ELSE
            IF (to_jsonb(NEW)-'revision'-'current_revision_id') IS DISTINCT FROM
               (to_jsonb(OLD)-'revision'-'current_revision_id') OR (NEW.revision<>OLD.revision+1 AND NOT (NEW.revision=1 AND OLD.revision=1 AND EXISTS (SELECT 1 FROM public.response_cards c WHERE c.id=OLD.id AND c.xmin::text=pg_current_xact_id()::text AND NOT EXISTS (SELECT 1 FROM public.response_card_revisions r WHERE r.card_id=OLD.id AND r.revision>1)))) THEN
                RAISE EXCEPTION 'Invalid response pointer advance' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;

-- FUNCTION: response_check_actor(uuid, text)
CREATE FUNCTION public.response_check_actor(p_token uuid, p_kind text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF p_kind IS DISTINCT FROM current_setting('app.actor_kind',true)
            OR p_token IS DISTINCT FROM NULLIF(current_setting('app.actor_token_id',true),'')::uuid THEN
            RAISE EXCEPTION 'Actor context mismatch' USING ERRCODE='42501';
        END IF;
        IF (p_kind='session' AND p_token IS NOT NULL) OR (p_kind='token' AND p_token IS NULL)
          OR (p_token IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.api_tokens t
             WHERE t.id=p_token
             AND NOT t.revoked AND t.expires_at > CURRENT_TIMESTAMP)) THEN
            RAISE EXCEPTION 'Actor token mismatch' USING ERRCODE='42501';
        END IF;
    END $$;

-- FUNCTION: response_citation_valid(uuid)
CREATE FUNCTION public.response_citation_valid(p_requirement uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
          WITH located AS (
            SELECT r.quote, CASE WHEN r.page IS NOT NULL AND r.page=c.page AND r.location IS NULL
                AND c.blocks IS NULL THEN c.text
              WHEN r.page IS NULL AND c.page IS NULL AND r.location IS NOT NULL THEN
                (SELECT b->>'text' FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location)
              END AS source_text
            FROM public.requirements r JOIN public.chunks c ON c.id=r.chunk_id
            WHERE r.id=p_requirement AND c.task_id=r.task_id
              AND c.document_id=r.document_id AND c.citation_verified
          )
          SELECT length(quote)>0 AND position(quote in source_text)>0
            AND public.response_locate_quote(source_text,quote) IS NOT NULL
          FROM located
        $$;

-- FUNCTION: response_draft_complete()
CREATE FUNCTION public.response_draft_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE total integer; actual integer; gaps integer;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM public.jobs g WHERE g.id=NEW.generation_job_id
        AND g.status IN ('running','succeeded') AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Draft generation attempt is no longer current' USING ERRCODE='23514';
      END IF;
      SELECT count(*) INTO total FROM public.requirements WHERE task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
      SELECT count(*),count(*) FILTER(WHERE kind='gap') INTO actual,gaps FROM public.response_items
        WHERE draft_id=NEW.id;
      IF total=0 OR actual<>total OR NEW.completion IS DISTINCT FROM (CASE WHEN gaps>0 THEN 'partial' ELSE 'complete' END) THEN
        RAISE EXCEPTION 'Draft must cover every requirement exactly once' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i LEFT JOIN public.response_cards c
        ON c.id=i.card_id WHERE i.draft_id=NEW.id
        AND ((i.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM i.card_revision_id)
          OR (i.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.requirement_id=i.requirement_id)))) THEN
        RAISE EXCEPTION 'Draft input changed' USING ERRCODE='23514';
      END IF;
      IF EXISTS (SELECT 1 FROM public.response_items i WHERE i.draft_id=NEW.id
        AND i.kind IN ('row','comply_only') AND public.response_citation_valid(i.requirement_id) IS DISTINCT FROM true)
        OR EXISTS (SELECT 1 FROM public.response_items i JOIN public.card_evidence_links l
        ON l.revision_id=i.card_revision_id JOIN public.evidence e ON e.id=l.evidence_id
        WHERE i.draft_id=NEW.id AND i.kind='row'
          AND (e.confirmed_at IS NULL OR public.response_evidence_active(e.id) IS DISTINCT FROM true)) THEN
        RAISE EXCEPTION 'Draft material or citation is not eligible' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;

-- FUNCTION: response_evidence_active(uuid)
CREATE FUNCTION public.response_evidence_active(p_evidence uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.annotation_prior_evidence_active(p_evidence) AND NOT EXISTS(
  SELECT 1 FROM public.evidence e JOIN public.screenshot_renditions r ON (r.id)=(e.screenshot_rendition_id)
  LEFT JOIN public.annotation_materials m ON (m.rendition_id)=(r.id)
  WHERE e.id=p_evidence AND (r.profile='annotation-release-v1'
    OR (r.profile='annotation-candidate-v1' AND (m.id IS NULL OR NOT public.annotation_source_current(m.request_id)))))
$$;

-- FUNCTION: response_evidence_confirmation_complete()
CREATE FUNCTION public.response_evidence_confirmation_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
      IF NEW.confirmed_at IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.response_cards c JOIN public.response_card_revisions r
          ON r.id=c.current_revision_id
        JOIN public.card_evidence_links l ON l.revision_id=r.id
        WHERE c.id=NEW.card_id AND r.state='confirmed'
          AND r.confirmed_at IS NOT NULL AND l.evidence_id=NEW.id) THEN
        RAISE EXCEPTION 'Evidence confirmation requires confirmed response transaction' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;

-- FUNCTION: response_evidence_gate()
CREATE FUNCTION public.response_evidence_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE card_state text; material jsonb; field_value text;
        source public.evidence_sources%ROWTYPE; asset public.screenshot_assets%ROWTYPE;
        rendition public.screenshot_renditions%ROWTYPE; expected_material text;
        selection_active boolean;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Evidence cannot be deleted' USING ERRCODE='42501'; END IF;
  IF TG_OP='UPDATE' THEN
    IF (to_jsonb(NEW)-'confirmed_at'-'quote_check') IS DISTINCT FROM
       (to_jsonb(OLD)-'confirmed_at'-'quote_check')
       OR OLD.confirmed_at IS NOT NULL OR NEW.confirmed_at IS NULL THEN
      RAISE EXCEPTION 'Evidence is immutable after confirmation' USING ERRCODE='42501';
    END IF;
  ELSIF NEW.confirmed_at IS NOT NULL THEN
    RAISE EXCEPTION 'New evidence must be unconfirmed' USING ERRCODE='23514';
  END IF;
  IF NEW.confirmed_at IS NOT NULL THEN
    SELECT r.state INTO card_state
      FROM public.response_cards c JOIN public.response_card_revisions r
        ON r.id=c.current_revision_id
      WHERE c.id=NEW.card_id;
    PERFORM public.response_require_human();
    IF card_state IS DISTINCT FROM 'pending_review'
       OR public.response_evidence_active(NEW.id) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Invalid evidence confirmation' USING ERRCODE='23514';
    END IF;
  END IF;
  CASE NEW.kind
    WHEN 'product' THEN SELECT data INTO material FROM public.product_revisions
      WHERE id=NEW.product_revision_id;
    WHEN 'feature' THEN SELECT data INTO material FROM public.feature_revisions
      WHERE id=NEW.feature_revision_id;
    WHEN 'certificate' THEN SELECT data INTO material FROM public.certificate_revisions
      WHERE id=NEW.certificate_revision_id;
    WHEN 'org_profile' THEN SELECT data INTO material FROM public.org_profile_revisions
      WHERE id=NEW.profile_revision_id;
    WHEN 'certificate_pdf_page' THEN
      SELECT * INTO source FROM public.evidence_sources
        WHERE id=NEW.evidence_source_id;
      IF source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
        OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
        OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
        OR source.page IS DISTINCT FROM NEW.page
        OR source.preview->>'sha256' IS DISTINCT FROM NEW.source_sha256 THEN
        RAISE EXCEPTION 'Evidence page does not match retained source' USING ERRCODE='23514';
      END IF;
    WHEN 'image_region' THEN
      SELECT * INTO asset FROM public.screenshot_assets
        WHERE task_id=NEW.task_id AND id=NEW.screenshot_asset_id;
      SELECT * INTO rendition FROM public.screenshot_renditions
        WHERE task_id=NEW.task_id AND asset_id=NEW.screenshot_asset_id
          AND id=NEW.screenshot_rendition_id AND image_sha256=NEW.image_sha256;
      IF NEW.material_kind='certificate_image' THEN
        SELECT * INTO source FROM public.evidence_sources
          WHERE id=asset.evidence_source_id;
      END IF;
      expected_material := CASE
        WHEN asset.origin='prototype' THEN 'prototype'
        WHEN asset.source_kind='local_browser' THEN 'browser_screenshot'
        WHEN asset.source_kind='certificate_page' THEN 'certificate_image'
        WHEN asset.source_kind IN ('vendor_web','vendor_pdf') THEN asset.source_kind
        WHEN asset.source_kind='upload' AND asset.image_kind='diagram' THEN 'user_diagram'
        WHEN asset.source_kind='upload' AND asset.image_kind='screenshot' THEN 'user_screenshot'
        ELSE NULL END;
      selection_active := CASE
        WHEN NEW.material_kind IN ('user_screenshot','browser_screenshot','user_diagram','prototype')
          THEN EXISTS(SELECT 1 FROM public.task_features s
            WHERE s.id=NEW.task_feature_id
              AND s.feature_revision_id=NEW.feature_revision_id AND s.active)
        WHEN NEW.material_kind IN ('vendor_web','vendor_pdf')
          THEN EXISTS(SELECT 1 FROM public.task_resources s
            WHERE s.id=NEW.task_resource_id
              AND s.product_revision_id=NEW.product_revision_id AND s.active)
        WHEN NEW.material_kind='certificate_image'
          THEN EXISTS(SELECT 1 FROM public.task_certificates s
            WHERE s.id=NEW.task_certificate_id
              AND s.certificate_revision_id=NEW.certificate_revision_id AND s.active)
        ELSE false END;
      IF asset.id IS NULL OR rendition.id IS NULL OR expected_material IS DISTINCT FROM NEW.material_kind
        OR NEW.source_sha256 IS DISTINCT FROM asset.source_sha256
        OR asset.extraction_job_id IS DISTINCT FROM (
          SELECT c.extraction_job_id FROM public.response_cards c
          WHERE c.id=NEW.card_id AND c.task_id=NEW.task_id)
        OR (NEW.region->>'x')::integer+(NEW.region->>'width')::integer
             > (rendition.mapping->>'content_width')::integer
        OR (NEW.region->>'y')::integer+(NEW.region->>'height')::integer
             > (rendition.mapping->>'content_height')::integer
        OR (NEW.task_feature_id,NEW.feature_revision_id) IS DISTINCT FROM
             (asset.task_feature_id,asset.feature_revision_id)
        OR (NEW.task_resource_id,NEW.product_revision_id) IS DISTINCT FROM
             (asset.task_resource_id,asset.product_revision_id)
        OR (NEW.material_kind='certificate_image'
            AND NEW.evidence_source_id IS DISTINCT FROM asset.evidence_source_id)
        OR (NEW.material_kind='certificate_image' AND (
          source.id IS NULL OR source.task_id IS DISTINCT FROM NEW.task_id
          OR source.task_certificate_id IS DISTINCT FROM NEW.task_certificate_id
          OR source.certificate_revision_id IS DISTINCT FROM NEW.certificate_revision_id
          OR source.page IS DISTINCT FROM NEW.page))
        OR selection_active IS DISTINCT FROM true
        OR (NEW.material_kind IN ('user_screenshot','browser_screenshot')
            AND (asset.source->>'environment' IS NULL
              OR asset.source->>'environment' NOT IN ('production','test','development')))
        OR (NEW.material_kind='prototype' AND asset.source_kind<>'prototype_render'
            AND asset.source->>'environment' IS DISTINCT FROM 'prototype')
        OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals w
          WHERE w.asset_id=NEW.screenshot_asset_id)
        OR NOT EXISTS(SELECT 1 FROM public.screenshot_privacy_reviews p
          JOIN public.screenshot_renditions released
            ON released.asset_id=p.asset_id
           AND released.id=p.rendition_id AND released.image_sha256=p.stored_image_sha256
          WHERE p.asset_id=NEW.screenshot_asset_id
            AND p.id=rendition.privacy_review_id) THEN
        RAISE EXCEPTION 'Image evidence does not match retained rendition' USING ERRCODE='23514';
      END IF;
    ELSE RAISE EXCEPTION 'Unknown material kind' USING ERRCODE='23514';
  END CASE;
  IF NEW.kind NOT IN ('certificate_pdf_page','image_region') THEN
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

-- FUNCTION: response_generation_materials_active(uuid)
CREATE FUNCTION public.response_generation_materials_active(p_job uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
      SELECT p_job IS NULL OR EXISTS (
        SELECT 1 FROM public.card_generation_runs g WHERE g.generation_job_id=p_job
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(g.input_manifest->'materials') m
            WHERE CASE m->>'kind'
              WHEN 'product' THEN NOT EXISTS (SELECT 1 FROM public.task_resources s
                WHERE s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.product_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'feature' THEN NOT EXISTS (SELECT 1 FROM public.task_features s
                WHERE s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.feature_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'certificate' THEN NOT EXISTS (SELECT 1 FROM public.task_certificates s
                WHERE s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.certificate_revision_id=(m->>'revision_id')::uuid AND s.active)
              WHEN 'certificate_pdf_page' THEN NOT EXISTS (SELECT 1 FROM public.task_certificates s
                JOIN public.evidence_sources e ON e.task_certificate_id=s.id
                WHERE s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.certificate_revision_id=(m->>'revision_id')::uuid AND s.active
                AND e.id=(m->>'evidence_source_id')::uuid AND e.page=(m->>'page')::int)
              WHEN 'org_profile' THEN NOT EXISTS (SELECT 1 FROM public.task_org_profiles s
                WHERE s.task_id=g.task_id AND s.id=(m->>'selection_id')::uuid
                AND s.profile_revision_id=(m->>'revision_id')::uuid AND s.active)
              ELSE true END))
    $$;

-- FUNCTION: response_generation_revision_gate()
CREATE FUNCTION public.response_generation_revision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE previous public.response_card_revisions%ROWTYPE;
    BEGIN
      SELECT * INTO previous FROM public.response_card_revisions
        WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
      IF previous.state IN ('pending_review','confirmed') AND
        NEW.model_job_id IS DISTINCT FROM previous.model_job_id THEN
        RAISE EXCEPTION 'Protected model dependencies cannot change' USING ERRCODE='23514';
      END IF;
      IF NEW.origin='model' AND previous.deviation='negative' AND NEW.deviation<>'negative' THEN
        RAISE EXCEPTION 'Model cannot weaken negative deviation' USING ERRCODE='23514';
      END IF;
      IF NEW.state='confirmed' AND
        public.response_generation_materials_active(NEW.model_job_id) IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Model input material requires renewed review' USING ERRCODE='23514';
      END IF;
      RETURN NULL;
    END $$;

-- FUNCTION: response_immutable_gate()
CREATE FUNCTION public.response_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN RAISE EXCEPTION 'Response history is append only' USING ERRCODE='42501'; END $$;

-- FUNCTION: response_item_gate()
CREATE FUNCTION public.response_item_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE run public.draft_runs%ROWTYPE; run_xid text; requirement public.requirements%ROWTYPE;
            card public.response_cards%ROWTYPE; revision public.response_card_revisions%ROWTYPE; target_table text;
            requirement_ok boolean; requirement_state text; citation_ok boolean; material_ok boolean; quote_current boolean; expected_reasons jsonb := '[]'::jsonb;
    BEGIN
      SELECT * INTO run FROM public.draft_runs d WHERE d.id=NEW.draft_id;
      SELECT d.xmin::text INTO run_xid FROM public.draft_runs d WHERE d.id=NEW.draft_id;
      IF run_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical draft snapshot is closed' USING ERRCODE='42501';
      END IF;
      SELECT * INTO requirement FROM public.requirements WHERE id=NEW.requirement_id;
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
        SELECT * INTO card FROM public.response_cards WHERE id=NEW.card_id;
        SELECT * INTO revision FROM public.response_card_revisions WHERE id=NEW.card_revision_id;
        IF card.requirement_id IS DISTINCT FROM NEW.requirement_id OR revision.card_id IS DISTINCT FROM card.id
          OR card.current_revision_id IS DISTINCT FROM revision.id THEN
          RAISE EXCEPTION 'Response item card binding invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      requirement_ok := public.requirement_review_current(NEW.requirement_id);
 requirement_state := public.requirement_review_state(NEW.requirement_id);
 IF NEW.kind IN ('row','comply_only') AND NOT requirement_ok THEN RAISE EXCEPTION 'requirement_unconfirmed' USING ERRCODE='23514'; END IF;
      citation_ok := public.response_citation_valid(NEW.requirement_id);
      material_ok := NOT EXISTS (SELECT 1 FROM public.card_evidence_links l WHERE l.revision_id=NEW.card_revision_id AND public.response_evidence_active(l.evidence_id) IS DISTINCT FROM true);
      material_ok := material_ok AND public.response_generation_materials_active(revision.model_job_id);
      quote_current := public.response_quote_current(NEW.card_revision_id);
      IF NEW.kind IN ('row','comply_only') AND
        (citation_ok IS DISTINCT FROM true OR quote_current IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
      END IF;
      IF NEW.kind='gap' THEN
        IF requirement_ok AND citation_ok AND quote_current AND (revision.disposition='comply_only' OR
          (revision.state='confirmed' AND revision.review_domain IS NOT NULL AND material_ok)) THEN
          RAISE EXCEPTION 'Eligible response cannot be hidden as gap' USING ERRCODE='23514';
        END IF;
        IF NOT requirement_ok THEN expected_reasons := jsonb_build_array(CASE WHEN requirement_state='invalidated' THEN 'requirement_invalidated' ELSE 'requirement_unconfirmed' END); END IF;
        IF NEW.card_id IS NULL THEN expected_reasons := expected_reasons || '["missing_card"]'::jsonb;
        ELSE
          IF revision.state<>'confirmed' THEN expected_reasons := expected_reasons ||
            jsonb_build_array(CASE WHEN revision.state IN ('rejected','needs_material') THEN revision.state ELSE 'unconfirmed' END); END IF;
          IF revision.review_domain IS NULL THEN expected_reasons := expected_reasons || '["unclassified"]'::jsonb; END IF;
          IF NOT material_ok THEN expected_reasons := expected_reasons || '["stale_material"]'::jsonb; END IF;
          IF quote_current IS DISTINCT FROM true THEN
            expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb;
          END IF;
        END IF;
        IF citation_ok IS DISTINCT FROM true AND NOT (expected_reasons ? 'invalid_citation') THEN
          expected_reasons := expected_reasons || '["invalid_citation"]'::jsonb;
        END IF;
        IF NEW.gap_reasons IS DISTINCT FROM expected_reasons THEN

          RAISE EXCEPTION 'Gap reasons must reflect current inputs' USING ERRCODE='23514';
        END IF;
      END IF;
      IF NEW.kind='row' AND material_ok IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Model input material requires renewed review' USING ERRCODE='23514';
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
          OR NEW.disposition_at IS DISTINCT FROM revision.disposition_at THEN
          RAISE EXCEPTION 'Comply-only item lacks human disposition' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;

-- FUNCTION: response_item_complete()
CREATE FUNCTION public.response_item_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
      -- The deferred pass re-ran response_item_gate() for the same row the BEFORE
      -- trigger already validated in this transaction. That function is row-local
      -- and never reads public.response_items, every row it references is
      -- protected by the task lock this transaction holds (cards.task_lock and the
      -- requirements.source_lock BEFORE UPDATE trigger both take
      -- SELECT tasks ... FOR UPDATE), and response_items is append-only, so the
      -- second pass could not observe anything the first one did not. The genuine
      -- cross-row invariant is enforced separately by response_draft_complete() on
      -- draft_runs. Skipping the duplicate pass keeps the declared completeness
      -- constraint without paying for it twice; insert any real check here if a
      -- future deferred-only invariant appears.
      RETURN NULL;
    END $$;

-- FUNCTION: response_link_gate()
CREATE FUNCTION public.response_link_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE revision_xid text;
    BEGIN
      SELECT xmin::text INTO revision_xid FROM public.response_card_revisions
        WHERE id=NEW.revision_id AND card_id=NEW.card_id;
      IF revision_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'Historical revision links are closed' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END $$;

-- FUNCTION: response_locate_quote(text, text)
CREATE FUNCTION public.response_locate_quote(source_text text, quote text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
  combining_chars constant text := '̴̵̶̷̸᳔᳢᳣᳤᳥᳦᳧᳨⃒⃓⃘⃙⃚⃥⃦⃪⃫𐨹𖫰𖫱𖫲𖫳𖫴𛲞𝅧𝅨𝅩𖿰𖿱़়਼઼଼఼಼့᬴᯦᰷꦳𑂺𑅳𑇊𑈶𑋩𑌻𑌼𑑆𑓃𑗀𑚷𑠺𑥃𑵂𞥊゙゚्্੍્୍்్್഻഼്්ฺ຺྄္်᜔᜕᜴្᩠᭄᮪᮫᯲᯳⵿꠆꠬꣄꥓꧀꫶꯭𐨿𑁆𑁰𑁿𑂹𑄳𑄴𑇀𑈵𑋪𑍍𑑂𑓂𑖿𑘿𑚶𑜫𑠹𑤽𑤾𑧠𑨴𑩇𑪙𑰿𑵄𑵅𑶗𑽁𑽂ְֱֲֳִֵֶַָׇֹֺֻּֽֿׁׂﬞًࣰٌࣱٍࣲؘَؙُؚِّْٰܑౕౖุู่้๊๋ຸູ່້໊໋ཱིེཻོཽྀུ̡̢̧̨᷐᷎̛༹𝅥𝅦𝅮𝅯𝅰𝅱𝅲᷺〪̖̗̘̙̜̝̞̟̠̣̤̥̦̩̪̫̬̭̮̯̰̱̲̳̹̺̻̼͇͈͉͍͎͓͔͕͖͙͚֑֖֛֢֣֤֥֦֧֪ׅٕٖٜٟۣ۪ۭܱܴܷܸܹܻܼܾ݂݄݆݈߲߽࡙࡚࡛࢙࢚࢛࣏࣐࣑࣒࣓ࣣࣦࣩ࣭࣮࣯ࣶࣹࣺ॒༘༙༵༷࿆ႍ᤻ᨘ᩿᪵᪶᪷᪸᪹᪺᪽ᪿᫀ᫃᫄᫊᭬᳕᳖᳗᳘᳙᳜᳝᳞᳟᳭᷂᷊᷏᷹᷽᷿⃨⃬⃭⃮⃯꤫꤬꤭ꪴ︧︨︩︪︫︬︭𐇽𐋠𐨍𐨺𐫦𐻽𐻾𐻿𐽆𐽇𐽋𐽍𐽎𐽏𐽐𐾃𐾅𝅻𝅼𝅽𝅾𝅿𝆀𝆁𝆂𝆊𝆋𞓮𞣐𞣑𞣒𞣓𞣔𞣕𞣖֚֭᤹〭〮〯𝅭֮ᢩ᷷᷸〫̀́̂̃̄̅̆̇̈̉̊̋̌̍̎̏̐̑̒̓̔̽̾̿̀́͂̓̈́͆͊͋͌͐͑͒͗͛ͣͤͥͦͧͨͩͪͫͬͭͮͯ҃҄҅҆҇֒֓֔֕֗֘֙֜֝֞֟֠֡֨֩֫֬֯ׄؐؑؒؓؔؕؖؗٓٔٗ٘ٙٚٛٝٞۖۗۘۙۚۛۜ۟۠ۡۢۤۧۨ۫۬ܰܲܳܵܶܺܽܿ݀݁݃݅݇݉݊߫߬߭߮߯߰߱߳ࠖࠗ࠘࠙ࠛࠜࠝࠞࠟࠠࠡࠢࠣࠥࠦࠧࠩࠪࠫࠬ࠭࢘࢜࢝࢞࢟࣊࣋࣌࣍࣎ࣔࣕࣖࣗࣘࣙࣚࣛࣜࣝࣞࣟ࣠࣡ࣤࣥࣧࣨ࣪࣫࣬ࣳࣴࣵࣷࣸࣻࣼࣽࣾࣿ॑॓॔৾ྂྃ྆྇፝፞፟៝᤺ᨗ᩵᩶᩷᩸᩹᩺᩻᩼᪰᪱᪲᪳᪴᪻᪼᫁᫂᫅᫆᫇᫈᫉᫋ᫌᫍᫎ᭫᭭᭮᭯᭰᭱᭲᭳᳐᳑᳒᳚᳛᳠᳴᳸᳹᷀᷁᷃᷄᷅᷆᷇᷈᷉᷋᷌᷑᷒ᷓᷔᷕᷖᷗᷘᷙᷚᷛᷜᷝᷞᷟᷠᷡᷢᷣᷤᷥᷦᷧᷨᷩᷪᷫᷬᷭᷮᷯᷰᷱᷲᷳᷴ᷵᷻᷾⃐⃑⃔⃕⃖⃗⃛⃜⃡⃧⃩⃰⳯⳰⳱ⷠⷡⷢⷣⷤⷥⷦⷧⷨⷩⷪⷫⷬⷭⷮⷯⷰⷱⷲⷳⷴⷵⷶⷷⷸⷹⷺⷻⷼⷽⷾⷿ꙯ꙴꙵꙶꙷꙸꙹꙺꙻ꙼꙽ꚞꚟ꛰꛱꣠꣡꣢꣣꣤꣥꣦꣧꣨꣩꣪꣫꣬꣭꣮꣯꣰꣱ꪰꪲꪳꪷꪸꪾ꪿꫁︠︡︢︣︤︥︦︮︯𐍶𐍷𐍸𐍹𐍺𐨏𐨸𐫥𐴤𐴥𐴦𐴧𐺫𐺬𐽈𐽉𐽊𐽌𐾂𐾄𑄀𑄁𑄂𑍦𑍧𑍨𑍩𑍪𑍫𑍬𑍰𑍱𑍲𑍳𑍴𑑞𖬰𖬱𖬲𖬳𖬴𖬵𖬶𝆅𝆆𝆇𝆈𝆉𝆪𝆫𝆬𝆭𝉂𝉃𝉄𞀀𞀁𞀂𞀃𞀄𞀅𞀆𞀈𞀉𞀊𞀋𞀌𞀍𞀎𞀏𞀐𞀑𞀒𞀓𞀔𞀕𞀖𞀗𞀘𞀛𞀜𞀝𞀞𞀟𞀠𞀡𞀣𞀤𞀦𞀧𞀨𞀩𞀪𞂏𞄰𞄱𞄲𞄳𞄴𞄵𞄶𞊮𞋬𞋭𞋮𞋯𞓯𞥄𞥅𞥆𞥇𞥈𞥉̕̚͘᷶〬𞓬𞓭͜͟͢᷼͝͞͠͡᷍ͅ';
  separators constant text := U&'\FF1B;\FF0C,\3002\FF1A:\3001\FF01!\FF1F?\FF08\FF09()\3010\3011[]\300A\300B\201C\201D\2018\2019\0022\0027\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000';
  needle text := public.response_normalize_quote(quote);
  haystack text := '';
  starts integer[] := '{}'; ends integer[] := '{}';
  source_length integer := length(source_text);
  unit_start integer := 1; idx integer; part_index integer;
  previous text; character text; part text;
  search_from integer := 1; relative_match integer; match_start integer;
  span_start integer; span_end integer; candidate text;
  matches integer := 0; bounded_matches integer := 0;
  chosen text; bounded_chosen text;
  exact_start integer; exact_end integer; prefix text; next_character text;
BEGIN
  IF length(needle)=0 OR source_length=0 THEN RETURN NULL; END IF;
  -- Without combining input or leading normalized marks, unit boundaries
  -- compose exactly as whole-source NFKC. Marked input retains the old algorithm.
  IF source_text !~ '[̴̵̶̷̸᳔᳢᳣᳤᳥᳦᳧᳨⃒⃓⃘⃙⃚⃥⃦⃪⃫़়਼઼଼఼಼့᬴᯦᰷꦳゙゚्্੍્୍்్್഻഼്්ฺ຺྄္်᜔᜕᜴្᩠᭄᮪᮫᯲᯳⵿꠆꠬꣄꥓꧀꫶꯭ְֱֲֳִֵֶַָׇֹֺֻּֽֿׁׂﬞًࣰٌࣱٍࣲؘَؙُؚِّْٰܑౕౖุู่้๊๋ຸູ່້໊໋ཱཱཱཱིིེཻོཽྀྀུུ̡̢̧̨᷐᷎̛༹᷺〪̖̗̘̙̜̝̞̟̠̣̤̥̦̩̪̫̬̭̮̯̰̱̲̳̹̺̻̼͇͈͉͍͎͓͔͕͖͙͚֑֖֛֢֣֤֥֦֧֪ׅٕٖٜٟۣ۪ۭܱܴܷܸܹܻܼܾ݂݄݆݈߲߽࡙࡚࡛࢙࢚࢛࣏࣐࣑࣒࣓ࣣࣦࣩ࣭࣮࣯ࣶࣹࣺ॒༘༙༵༷࿆ႍ᤻ᨘ᩿᪵᪶᪷᪸᪹᪺᪽ᪿᫀ᫃᫄᫊᭬᳕᳖᳗᳘᳙᳜᳝᳞᳟᳭᷂᷊᷏᷹᷽᷿⃨⃬⃭⃮⃯꤫꤬꤭ꪴ︧︨︩︪︫︬︭֚֭᤹〭〮〯֮ᢩ᷷᷸〫̀́̂̃̄̅̆̇̈̉̊̋̌̍̎̏̐̑̒̓̔̽̾̿̀́͂̓̈́͆͊͋͌͐͑͒͗͛ͣͤͥͦͧͨͩͪͫͬͭͮͯ҃҄҅҆҇֒֓֔֕֗֘֙֜֝֞֟֠֡֨֩֫֬֯ׄؐؑؒؓؔؕؖؗٓٔٗ٘ٙٚٛٝٞۖۗۘۙۚۛۜ۟۠ۡۢۤۧۨ۫۬ܰܲܳܵܶܺܽܿ݀݁݃݅݇݉݊߫߬߭߮߯߰߱߳ࠖࠗ࠘࠙ࠛࠜࠝࠞࠟࠠࠡࠢࠣࠥࠦࠧࠩࠪࠫࠬ࠭࢘࢜࢝࢞࢟࣊࣋࣌࣍࣎ࣔࣕࣖࣗࣘࣙࣚࣛࣜࣝࣞࣟ࣠࣡ࣤࣥࣧࣨ࣪࣫࣬ࣳࣴࣵࣷࣸࣻࣼࣽࣾࣿ॑॓॔৾ྂྃ྆྇፝፞፟៝᤺ᨗ᩵᩶᩷᩸᩹᩺᩻᩼᪰᪱᪲᪳᪴᪻᪼᫁᫂᫅᫆᫇᫈᫉᫋ᫌᫍᫎ᭫᭭᭮᭯᭰᭱᭲᭳᳐᳑᳒᳚᳛᳠᳴᳸᳹᷀᷁᷃᷄᷅᷆᷇᷈᷉᷋᷌᷑᷒ᷓᷔᷕᷖᷗᷘᷙᷚᷛᷜᷝᷞᷟᷠᷡᷢᷣᷤᷥᷦᷧᷨᷩᷪᷫᷬᷭᷮᷯᷰᷱᷲᷳᷴ᷵᷻᷾⃐⃑⃔⃕⃖⃗⃛⃜⃡⃧⃩⃰⳯⳰⳱ⷠⷡⷢⷣⷤⷥⷦⷧⷨⷩⷪⷫⷬⷭⷮⷯⷰⷱⷲⷳⷴⷵⷶⷷⷸⷹⷺⷻⷼⷽⷾⷿ꙯ꙴꙵꙶꙷꙸꙹꙺꙻ꙼꙽ꚞꚟ꛰꛱꣠꣡꣢꣣꣤꣥꣦꣧꣨꣩꣪꣫꣬꣭꣮꣯꣰꣱ꪰꪲꪳꪷꪸꪾ꪿꫁︠︡︢︣︤︥︦︮︯̕̚͘᷶〬͜͟͢᷼͝͞͠͡᷍ͅﾞﾟ𐨹𖫰𖫱𖫲𖫳𖫴𛲞𝅧𝅨𝅩𖿰𖿱𑂺𑅳𑇊𑈶𑋩𑌻𑌼𑑆𑓃𑗀𑚷𑠺𑥃𑵂𞥊𐨿𑁆𑁰𑁿𑂹𑄳𑄴𑇀𑈵𑋪𑍍𑑂𑓂𑖿𑘿𑚶𑜫𑠹𑤽𑤾𑧠𑨴𑩇𑪙𑰿𑵄𑵅𑶗𑽁𑽂𝅥𝅦𝅮𝅯𝅰𝅱𝅲𐇽𐋠𐨍𐨺𐫦𐻽𐻾𐻿𐽆𐽇𐽋𐽍𐽎𐽏𐽐𐾃𐾅𝅻𝅼𝅽𝅾𝅿𝆀𝆁𝆂𝆊𝆋𞓮𞣐𞣑𞣒𞣓𞣔𞣕𞣖𝅭𐍶𐍷𐍸𐍹𐍺𐨏𐨸𐫥𐴤𐴥𐴦𐴧𐺫𐺬𐽈𐽉𐽊𐽌𐾂𐾄𑄀𑄁𑄂𑍦𑍧𑍨𑍩𑍪𑍫𑍬𑍰𑍱𑍲𑍳𑍴𑑞𖬰𖬱𖬲𖬳𖬴𖬵𖬶𝆅𝆆𝆇𝆈𝆉𝆪𝆫𝆬𝆭𝉂𝉃𝉄𞀀𞀁𞀂𞀃𞀄𞀅𞀆𞀈𞀉𞀊𞀋𞀌𞀍𞀎𞀏𞀐𞀑𞀒𞀓𞀔𞀕𞀖𞀗𞀘𞀛𞀜𞀝𞀞𞀟𞀠𞀡𞀣𞀤𞀦𞀧𞀨𞀩𞀪𞂏𞄰𞄱𞄲𞄳𞄴𞄵𞄶𞊮𞋬𞋭𞋮𞋯𞓯𞥄𞥅𞥆𞥇𞥈𞥉𞓬𞓭]' THEN
    -- A unique normalized occurrence with an exact original spelling needs no
    -- offset arrays. Check both normalization-unit edges, not segment boundaries:
    -- a sole occurrence is valid even inside a word, but never inside a combining
    -- or Hangul unit. Nonempty edge characters exclude discarded whitespace units.
    haystack := public.response_normalize_quote(source_text);
    match_start := position(needle in haystack);
    IF match_start=0 THEN RETURN NULL; END IF;
    IF position(needle in substring(haystack from match_start+1))=0 THEN
      exact_start := position(quote in source_text);
      IF exact_start>0
        AND public.response_normalize_quote(left(quote,1))<>''
        AND public.response_normalize_quote(right(quote,1))<>''
        AND position(left(quote,1) in combining_chars)=0 THEN
        exact_end := exact_start+length(quote)-1;
        prefix := left(source_text,exact_start-1);
        next_character := substring(source_text from exact_end+1 for 1);
        IF (exact_start=1 OR
            normalize(prefix || left(quote,1),NFKC)=
              normalize(prefix,NFKC) || normalize(left(quote,1),NFKC))
          AND (exact_end=source_length OR
            (position(next_character in combining_chars)=0 AND
             normalize(prefix || quote || next_character,NFKC)=
               normalize(prefix || quote,NFKC) || normalize(next_character,NFKC))) THEN
          RETURN quote;
        END IF;
      END IF;
    END IF;
  END IF;
  haystack := '';
  -- Mirror normalized_spans: retain whole combining/composition units,
  -- including Hangul and compatibility expansions, with original offsets.
  FOR idx IN 2..source_length+1 LOOP
    previous := substring(source_text from unit_start for idx-unit_start);
    character := substring(source_text from idx for 1);
    IF idx<=source_length AND
      (position(character in combining_chars)>0 OR
       normalize(previous || character,NFKC) <>
         normalize(previous,NFKC) || normalize(character,NFKC)) THEN
      CONTINUE;
    END IF;
    part := public.response_normalize_quote(previous);
    haystack := haystack || part;
    FOR part_index IN 1..length(part) LOOP
      starts := array_append(starts,unit_start);
      ends := array_append(ends,idx-1);
    END LOOP;
    unit_start := idx;
  END LOOP;
  LOOP
    relative_match := position(needle in substring(haystack from search_from));
    EXIT WHEN relative_match=0;
    match_start := search_from+relative_match-1;
    span_start := starts[match_start];
    span_end := ends[match_start+length(needle)-1];
    candidate := substring(source_text from span_start for span_end-span_start+1);
    -- An inner match in a compatibility expansion is not an original span.
    IF public.response_normalize_quote(candidate)=needle THEN
      matches := matches+1;
      chosen := candidate;
      IF (span_start=1 OR
            position(substring(source_text from span_start-1 for 1) in separators)>0)
        AND (span_end=source_length OR
            position(substring(source_text from span_end+1 for 1) in separators)>0) THEN
        bounded_matches := bounded_matches+1;
        bounded_chosen := candidate;
      END IF;
    END IF;
    search_from := match_start+1;
  END LOOP;
  IF matches=1 THEN RETURN chosen; END IF;
  IF matches>1 AND bounded_matches=1 THEN RETURN bounded_chosen; END IF;
  RETURN NULL;
END $$;

-- FUNCTION: response_normalize_quote(text)
CREATE FUNCTION public.response_normalize_quote(value text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path TO 'pg_catalog'
    AS $$
          SELECT translate(normalize(value, NFKC),
            U&'\201C\201D\201E\201F\2018\2019\201A\201B\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000',
            U&'\0022\0022\0022\0022\0027\0027\0027\0027')
        $$;

-- FUNCTION: response_quote_current(uuid)
CREATE FUNCTION public.response_quote_current(p_revision uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
          SELECT coalesce(v.quote_sha256,
              encode(sha256(convert_to(coalesce(r.model_quote,r.quote),'UTF8')),'hex'))
            = encode(sha256(convert_to(r.quote,'UTF8')),'hex')
          FROM public.response_card_revisions v
          JOIN public.response_cards c ON c.id=v.card_id
          JOIN public.requirements r ON r.id=c.requirement_id
          WHERE v.id=p_revision
        $$;

-- FUNCTION: response_redaction_gate()
CREATE FUNCTION public.response_redaction_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF TG_OP='INSERT' THEN
            IF NOT NEW.model_redaction_enabled OR NEW.model_redaction_revision<>1 THEN
                RAISE EXCEPTION 'Initial redaction must be enabled' USING ERRCODE='23514';
            END IF;
        ELSIF ROW(NEW.model_redaction_enabled,NEW.model_redaction_revision)
          IS DISTINCT FROM ROW(OLD.model_redaction_enabled,OLD.model_redaction_revision) THEN
            PERFORM public.response_require_human();
            IF NEW.model_redaction_revision<>OLD.model_redaction_revision+1 THEN
                RAISE EXCEPTION 'Invalid redaction revision' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;

-- FUNCTION: response_require_human()
CREATE FUNCTION public.response_require_human() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
        IF NOT public.actor_is_human() THEN
            RAISE EXCEPTION 'Human decision context required' USING ERRCODE='42501';
        END IF;
    END $$;

-- FUNCTION: response_requirement_citations_valid(uuid[])
CREATE FUNCTION public.response_requirement_citations_valid(p_requirements uuid[]) RETURNS TABLE(requirement_id uuid, is_valid boolean)
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
  WITH requested AS MATERIALIZED (
    SELECT DISTINCT unnest(coalesce(p_requirements,ARRAY[]::uuid[])) AS requirement_id
  ), resolved AS MATERIALIZED (
    SELECT requested.requirement_id,r.quote COLLATE "C" AS quote,
      coalesce(r.id IS NOT NULL AND c.id IS NOT NULL
        AND c.task_id=r.task_id AND c.document_id=r.document_id
        AND c.citation_verified,false) AS metadata_valid,
      CASE WHEN r.page IS NOT NULL AND r.page=c.page AND r.location IS NULL
          AND c.blocks IS NULL THEN c.text
        WHEN r.page IS NULL AND c.page IS NULL AND r.location IS NOT NULL THEN
          (SELECT b->>'text' FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location)
      END COLLATE "C" AS source_text
    FROM requested
    LEFT JOIN public.requirements r ON r.id=requested.requirement_id
    LEFT JOIN public.chunks c ON c.id=r.chunk_id
  ), sources AS MATERIALIZED (
    SELECT DISTINCT source_text,quote FROM resolved
    WHERE metadata_valid AND source_text IS NOT NULL AND quote IS NOT NULL
  ), verified AS MATERIALIZED (
    SELECT source_text,quote,
      coalesce(length(quote)>0 AND position(quote in source_text)>0
        AND public.response_locate_quote(source_text,quote) IS NOT NULL,false) AS is_valid
    FROM sources
  )
  SELECT resolved.requirement_id,
    coalesce(resolved.metadata_valid AND verified.is_valid,false)
  FROM resolved LEFT JOIN verified
    ON verified.source_text=resolved.source_text AND verified.quote=resolved.quote
$$;

-- FUNCTION: response_revision_complete()
CREATE FUNCTION public.response_revision_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE card public.response_cards%ROWTYPE; links integer; invalid integer;
            previous public.response_card_revisions%ROWTYPE;
    BEGIN
      SELECT * INTO STRICT card FROM public.response_cards WHERE id=NEW.card_id;
      IF card.revision IS DISTINCT FROM (SELECT max(revision) FROM public.response_card_revisions WHERE card_id=NEW.card_id)
        OR NOT EXISTS (SELECT 1 FROM public.response_card_revisions r WHERE r.id=card.current_revision_id AND r.card_id=card.id AND r.revision=card.revision) THEN
        RAISE EXCEPTION 'Response pointer is incomplete' USING ERRCODE='23514';
      END IF;
      SELECT count(*),count(*) FILTER(WHERE e.confirmed_at IS NULL OR NOT public.response_evidence_active(e.id))
        INTO links,invalid FROM public.card_evidence_links l JOIN public.evidence e ON e.id=l.evidence_id
        WHERE l.revision_id=NEW.id;
      IF NEW.response_kind='commitment' AND links<>0 THEN
        RAISE EXCEPTION 'Commitment must not have evidence' USING ERRCODE='23514';
      END IF;
      IF NEW.state='confirmed' AND ((NEW.response_kind='evidence' AND (links=0 OR invalid<>0))
        OR public.response_citation_valid(card.requirement_id) IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'Confirmed response is incomplete' USING ERRCODE='23514';
      END IF;
      SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
      IF previous.state IN ('pending_review','confirmed') OR previous.disposition='comply_only' THEN
        IF EXISTS ((SELECT evidence_id FROM public.card_evidence_links WHERE revision_id=NEW.id
                    EXCEPT SELECT evidence_id FROM public.card_evidence_links WHERE revision_id=previous.id)
                   UNION ALL
                   (SELECT evidence_id FROM public.card_evidence_links WHERE revision_id=previous.id
                    EXCEPT SELECT evidence_id FROM public.card_evidence_links WHERE revision_id=NEW.id)) THEN
          RAISE EXCEPTION 'Protected evidence links cannot change' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NULL;
    END $$;

-- FUNCTION: response_revision_gate()
CREATE FUNCTION public.response_revision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE previous public.response_card_revisions%ROWTYPE; card public.response_cards%ROWTYPE;
            category text; decision boolean; content_changed boolean;
    BEGIN
        PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
        SELECT * INTO STRICT card FROM public.response_cards WHERE id=NEW.card_id FOR UPDATE;
        SELECT r.category INTO category FROM public.requirements r WHERE r.id=card.requirement_id;
        SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id ORDER BY revision DESC LIMIT 1;
        IF NEW.actor_kind='worker' AND (NEW.state<>'draft'
          OR previous.state IN ('pending_review','confirmed') OR previous.disposition='comply_only') THEN
            RAISE EXCEPTION 'Worker may only write editable drafts' USING ERRCODE='42501';
        END IF;
        IF NEW.origin='model' AND NOT EXISTS (SELECT 1 FROM public.jobs g JOIN public.jobs e
          ON e.id=card.extraction_job_id
          WHERE g.id=NEW.model_job_id AND g.task_id=card.task_id
            AND g.document_id=e.document_id AND g.kind='card_generate' AND g.status='running') THEN
            RAISE EXCEPTION 'Model origin requires its bound generation job' USING ERRCODE='23514';
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_at);
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
                PERFORM public.response_require_human();
                IF previous.review_domain IS NOT NULL OR previous.state<>'draft' OR NEW.state<>'draft' OR NEW.reason IS NULL OR content_changed THEN
                    RAISE EXCEPTION 'Invalid classification' USING ERRCODE='23514';
                END IF;
            END IF;
            IF previous.state='confirmed' OR NEW.state IN ('confirmed','rejected','needs_material') THEN
                PERFORM public.response_require_human();
            END IF;
            IF ((previous.state='pending_review' AND NEW.state IN ('draft','rejected','needs_material'))
                OR previous.state='confirmed') AND NEW.reason IS NULL THEN
                RAISE EXCEPTION 'Review reason required' USING ERRCODE='23514';
            END IF;
        END IF;
        decision := ROW(NEW.disposition,NEW.disposition_at)
            IS DISTINCT FROM ROW(previous.disposition,previous.disposition_at);
        IF decision THEN
            IF previous.state='confirmed' OR (previous.state='pending_review' AND NOT
              (NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond'))
              OR (previous.id IS NOT NULL AND previous.state<>NEW.state AND NOT
              (previous.state='pending_review' AND NEW.state='confirmed' AND previous.disposition IS NULL AND NEW.disposition='respond')) THEN
                RAISE EXCEPTION 'Disposition must be a separate review decision' USING ERRCODE='23514';
            END IF;
            PERFORM public.response_require_human();
            IF NEW.disposition IS NULL OR NEW.disposition_at IS NULL
                OR (NEW.state<>'confirmed' AND NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Invalid disposition decision' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.state='confirmed' THEN
            PERFORM public.response_require_human();
            IF btrim(NEW.deviation_note)='满足' THEN
                RAISE EXCEPTION 'Concrete deviation explanation required' USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM public.requirements q WHERE q.id=card.requirement_id
              AND (q.quote ~ '提供.*(证书|检测报告|截图|说明书|证明|复印件)'
               OR q.quote ~* '(certificate|report|screenshot|proof).*(provid|attach)|(provid|attach).*(certificate|report|screenshot|proof)'))
               AND (NOT NEW.reviewed_warning_codes ? 'proof_material_required' OR NEW.reason IS NULL) THEN
                RAISE EXCEPTION 'Proof material warning requires human review' USING ERRCODE='23514';
            END IF;
            IF NEW.confirmed_at IS NULL OR previous.disposition='comply_only' THEN
                RAISE EXCEPTION 'Invalid response confirmation' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;

-- FUNCTION: response_revision_quote_gate()
CREATE FUNCTION public.response_revision_quote_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE current_hash text; previous_hash text; previous_id uuid;
        BEGIN
          SELECT encode(sha256(convert_to(r.quote,'UTF8')),'hex') INTO current_hash
            FROM public.requirements r JOIN public.response_cards c
              ON c.requirement_id=r.id
            WHERE c.id=NEW.card_id;
          SELECT v.id,coalesce(v.quote_sha256,
              encode(sha256(convert_to(coalesce(r.model_quote,r.quote),'UTF8')),'hex'))
            INTO previous_id,previous_hash
            FROM public.response_card_revisions v
            JOIN public.response_cards c ON c.id=v.card_id
            JOIN public.requirements r ON r.id=c.requirement_id
            WHERE v.card_id=NEW.card_id AND v.revision=NEW.revision-1;
          NEW.quote_sha256 := coalesce(NEW.quote_sha256,current_hash);
          IF NEW.quote_sha256 IS DISTINCT FROM current_hash AND
             (previous_id IS NULL OR NEW.quote_sha256 IS DISTINCT FROM previous_hash) THEN
            RAISE EXCEPTION 'Invalid response citation baseline' USING ERRCODE='23514';
          END IF;
          IF NEW.state='confirmed' AND (NEW.quote_sha256 IS DISTINCT FROM current_hash
             OR previous_hash IS DISTINCT FROM current_hash) THEN
            RAISE EXCEPTION 'Response citation requires renewed review' USING ERRCODE='23514';
          END IF;
          IF previous_id IS NOT NULL AND NEW.disposition='comply_only'
             AND NEW.quote_sha256 IS DISTINCT FROM previous_hash THEN
            PERFORM public.response_require_human();
          END IF;
          RETURN NEW;
        END $$;

-- FUNCTION: response_run_gate()
CREATE FUNCTION public.response_run_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    DECLARE expected_kind text;
    BEGIN
      PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
      expected_kind := CASE TG_TABLE_NAME WHEN 'draft_runs' THEN 'draft' ELSE 'card_generate' END;
      IF NOT EXISTS (SELECT 1 FROM public.jobs e JOIN public.jobs g ON g.task_id=e.task_id
        WHERE e.id=NEW.extraction_job_id AND e.task_id=NEW.task_id
        AND e.kind='extract' AND e.status='succeeded' AND g.id=NEW.generation_job_id
        AND g.task_id=e.task_id AND g.document_id=e.document_id AND g.kind=expected_kind
        AND g.status='running' AND NEW.generation_run_id IS NOT NULL AND g.run_id IS NOT DISTINCT FROM NEW.generation_run_id) THEN
        RAISE EXCEPTION 'Invalid response generation job' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$;

-- FUNCTION: response_tenant_insert_gate()
CREATE FUNCTION public.response_tenant_insert_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
    BEGIN
      RETURN NEW;
    END $$;

-- FUNCTION: screenshot_analysis_input_gate()
CREATE FUNCTION public.screenshot_analysis_input_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE rendition public.screenshot_renditions%ROWTYPE;
BEGIN
  IF NEW.rendition_id IS NOT NULL THEN
    SELECT * INTO rendition FROM public.screenshot_renditions WHERE id=NEW.rendition_id;
    IF (rendition.asset_id,rendition.image_sha256,rendition.privacy_review_id) IS DISTINCT FROM (NEW.asset_id,NEW.content_sha256,NEW.privacy_review_id) THEN
      RAISE EXCEPTION 'Analysis image input mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_asset_gate()
CREATE FUNCTION public.screenshot_asset_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE proto public.screenshot_prototype_runs%ROWTYPE;
BEGIN
  IF NEW.source_kind='attachment_page' THEN
    PERFORM public.attachment_screenshot_asset_guard(NEW.id);
    RETURN NEW;
  END IF;
  IF NEW.source ? 'annotation_request_id' THEN
    PERFORM public.annotation_certificate_asset(NEW.id,NEW.task_id,NEW.extraction_job_id,
      NEW.evidence_source_id,NEW.source_sha256,(NEW.source->>'annotation_request_id')::uuid);
    IF NEW.origin<>'certificate' OR NEW.source_kind<>'certificate_page' OR NEW.image_kind<>'certificate_page'
      OR NEW.source_hash_assurance<>'server_verified' OR NEW.task_feature_id IS NOT NULL OR NEW.feature_revision_id IS NOT NULL
      OR NEW.task_resource_id IS NOT NULL OR NEW.product_revision_id IS NOT NULL OR NEW.vendor_archive_id IS NOT NULL OR NEW.prototype_run_id IS NOT NULL
      OR NEW.source->>'kind' IS DISTINCT FROM 'certificate_page'
      OR NEW.source->>'evidence_source_id' IS DISTINCT FROM NEW.evidence_source_id::text
      OR NOT EXISTS(SELECT 1 FROM public.evidence_sources src WHERE src.id=NEW.evidence_source_id
        AND NEW.source_width=(src.preview->>'width_px')::integer AND NEW.source_height=(src.preview->>'height_px')::integer) THEN
      RAISE EXCEPTION 'Invalid cloud certificate asset' USING ERRCODE='23514'; END IF;
    RETURN NEW;
  END IF;
  PERFORM public.screenshot_require_ingest();
  IF jsonb_typeof(NEW.source) IS DISTINCT FROM 'object' OR NEW.source->>'kind' IS DISTINCT FROM NEW.source_kind THEN
    RAISE EXCEPTION 'Source metadata mismatch' USING ERRCODE='23514';
  END IF;
  IF NOT (
    (NEW.origin='user' AND NEW.source_kind='upload' AND NEW.image_kind IN ('screenshot','diagram')
      AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL AND NEW.prototype_run_id IS NULL
      AND NEW.evidence_source_id IS NULL AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='browser' AND NEW.source_kind='local_browser' AND NEW.image_kind='screenshot'
      AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL AND NEW.prototype_run_id IS NULL
      AND NEW.evidence_source_id IS NULL AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='certificate' AND NEW.source_kind='certificate_page' AND NEW.image_kind='certificate_page'
      AND NEW.evidence_source_id IS NOT NULL AND NEW.task_feature_id IS NULL AND NEW.feature_revision_id IS NULL AND NEW.prototype_run_id IS NULL
      AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL)
    OR (NEW.origin='vendor' AND NEW.source_kind IN ('vendor_web','vendor_pdf') AND NEW.image_kind='vendor_page'
      AND NEW.vendor_archive_id IS NOT NULL AND NEW.task_resource_id IS NOT NULL AND NEW.product_revision_id IS NOT NULL
      AND NEW.task_feature_id IS NULL AND NEW.feature_revision_id IS NULL AND NEW.prototype_run_id IS NULL AND NEW.evidence_source_id IS NULL)
    OR (NEW.origin='prototype' AND NEW.source_kind IN ('prototype_render','upload','local_browser') AND NEW.image_kind='prototype'
      AND NEW.prototype_run_id IS NOT NULL AND NEW.task_feature_id IS NOT NULL AND NEW.feature_revision_id IS NOT NULL
      AND NEW.vendor_archive_id IS NULL AND NEW.task_resource_id IS NULL AND NEW.product_revision_id IS NULL AND NEW.evidence_source_id IS NULL)
  ) THEN RAISE EXCEPTION 'Image source branches are exclusive' USING ERRCODE='23514'; END IF;
  IF NEW.source_kind IN ('upload','local_browser') AND (
    (NEW.source->>'task_feature_id')::uuid IS DISTINCT FROM NEW.task_feature_id
    OR NEW.source->>'image_kind' IS DISTINCT FROM NEW.image_kind
    OR (NEW.origin='prototype') IS DISTINCT FROM (NEW.source->>'environment'='prototype')
    OR NEW.source_hash_assurance <> 'client_declared') THEN
    RAISE EXCEPTION 'Client source declaration mismatch' USING ERRCODE='23514';
  END IF;
  IF NEW.evidence_source_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.evidence_sources e
      JOIN public.task_certificates s ON s.id=e.task_certificate_id
      WHERE e.id=NEW.evidence_source_id AND e.task_id=NEW.task_id AND s.active
        AND e.preview->>'sha256'=NEW.source_sha256
        AND (NEW.source->>'evidence_source_id')::uuid=e.id AND NEW.source_hash_assurance='server_verified') THEN
    RAISE EXCEPTION 'Certificate source binding mismatch' USING ERRCODE='23514';
  END IF;
  IF NEW.prototype_run_id IS NOT NULL THEN
    SELECT * INTO proto FROM public.screenshot_prototype_runs WHERE id=NEW.prototype_run_id;
    IF (proto.task_feature_id,proto.feature_revision_id,proto.source_image_sha256) IS DISTINCT FROM
       (NEW.task_feature_id,NEW.feature_revision_id,NEW.source_sha256)
       OR (NEW.source->>'prototype_run_id')::uuid IS DISTINCT FROM proto.id THEN
      RAISE EXCEPTION 'Prototype render binding mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_batch_complete()
CREATE FUNCTION public.screenshot_batch_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE batch_id uuid; batch public.prototype_decision_batches%ROWTYPE; target jsonb; item public.prototype_evidence_decisions%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME='prototype_decision_batches' THEN batch_id:=NEW.id; ELSE batch_id:=NEW.batch_id; END IF;
  SELECT * INTO batch FROM public.prototype_decision_batches WHERE id=batch_id;
  IF jsonb_typeof(batch.target_manifest) IS DISTINCT FROM 'array'
    OR jsonb_array_length(batch.target_manifest) NOT BETWEEN 1 AND 500
    OR (SELECT count(*) FROM public.prototype_evidence_decisions WHERE prototype_evidence_decisions.batch_id=batch.id) <> jsonb_array_length(batch.target_manifest)
    OR (SELECT count(DISTINCT value->>'evidence_id') FROM jsonb_array_elements(batch.target_manifest)) <> jsonb_array_length(batch.target_manifest) THEN
    RAISE EXCEPTION 'Prototype batch must commit every exact target once' USING ERRCODE='23514';
  END IF;
  FOR target IN SELECT value FROM jsonb_array_elements(batch.target_manifest) LOOP
    SELECT * INTO item FROM public.prototype_evidence_decisions WHERE prototype_evidence_decisions.batch_id=batch.id AND evidence_id=(target->>'evidence_id')::uuid;
    IF item.id IS NULL OR (item.card_revision_id,item.rendition_id,item.image_sha256,item.html_sha256,item.task_feature_id,item.previous_decision_id)
      IS DISTINCT FROM ((target->>'card_revision_id')::uuid,(target->>'rendition_id')::uuid,target->>'image_sha256',target->>'html_sha256',
        (target->>'task_feature_id')::uuid,(target->>'expected_previous_decision_id')::uuid) THEN
      RAISE EXCEPTION 'Prototype target manifest changed' USING ERRCODE='23514';
    END IF;
  END LOOP;
  RETURN NULL;
END $$;

-- FUNCTION: screenshot_batch_gate()
CREATE FUNCTION public.screenshot_batch_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  IF NOT public.actor_is_human()
    OR jsonb_typeof(NEW.task_feature_ids) IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.task_feature_ids)=0 THEN
    RAISE EXCEPTION 'Prototype batch requires a responsible human' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_card_warning_gate()
CREATE FUNCTION public.screenshot_card_warning_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE image record; has_redaction boolean; has_crop boolean;
BEGIN
  IF NEW.state <> 'confirmed' THEN RETURN NULL; END IF;
  FOR image IN SELECT a.*, e.screenshot_rendition_id FROM public.card_evidence_links l
    JOIN public.evidence e ON e.id=l.evidence_id
    JOIN public.screenshot_assets a ON a.id=e.screenshot_asset_id
    WHERE l.revision_id=NEW.id AND e.kind='image_region' LOOP
    IF NEW.reason IS NULL OR NOT NEW.reviewed_warning_codes ? 'image_visible_scope_only'
      OR (image.origin='prototype' AND NOT NEW.reviewed_warning_codes ? 'prototype_delivery_obligation')
      OR (image.origin<>'prototype' AND NOT NEW.reviewed_warning_codes ? 'image_source_claim')
      OR (image.source->>'environment' IN ('test','development') AND NOT NEW.reviewed_warning_codes ? 'image_test_environment')
      OR (image.image_kind='diagram' AND NOT NEW.reviewed_warning_codes ? 'image_design_only')
      OR (image.origin='vendor' AND NOT NEW.reviewed_warning_codes ? 'vendor_model_scope')
      OR (image.origin='vendor' AND NOT NEW.reviewed_warning_codes ? 'vendor_capture_incomplete' AND EXISTS (SELECT 1 FROM public.screenshot_vendor_archives v WHERE v.id=image.vendor_archive_id AND (v.provenance->>'incomplete')::boolean)) THEN
      RAISE EXCEPTION 'Image source and scope warnings require human review' USING ERRCODE='23514';
    END IF;
    WITH RECURSIVE lineage AS (
      SELECT r.id,r.parent_rendition_id,r.plan FROM public.screenshot_renditions r
        WHERE r.asset_id=image.id AND r.id=image.screenshot_rendition_id
      UNION ALL
      SELECT p.id,p.parent_rendition_id,p.plan FROM public.screenshot_renditions p JOIN lineage child
        ON p.id=child.parent_rendition_id WHERE p.asset_id=image.id
    ) SELECT bool_or(jsonb_array_length(plan->'redact')>0),bool_or(plan->'crop'<>'null'::jsonb)
      INTO has_redaction,has_crop FROM lineage;
    IF (has_redaction AND NOT NEW.reviewed_warning_codes ? 'image_redaction_review')
      OR (has_crop AND NOT NEW.reviewed_warning_codes ? 'image_crop_review') THEN
      RAISE EXCEPTION 'Image crop and redaction warnings require human review' USING ERRCODE='23514';
    END IF;
  END LOOP;
  RETURN NULL;
END $$;

-- FUNCTION: screenshot_decision_gate()
CREATE FUNCTION public.screenshot_decision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE card public.response_cards%ROWTYPE; rev public.response_card_revisions%ROWTYPE;
        evidence public.evidence%ROWTYPE; asset public.screenshot_assets%ROWTYPE; proto public.screenshot_prototype_runs%ROWTYPE;
        batch public.prototype_decision_batches%ROWTYPE; previous uuid;
BEGIN
  PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
  SELECT * INTO card FROM public.response_cards WHERE id=NEW.card_id FOR UPDATE;
  SELECT * INTO rev FROM public.response_card_revisions WHERE id=NEW.card_revision_id;
  PERFORM public.response_require_human();
  SELECT * INTO evidence FROM public.evidence WHERE id=NEW.evidence_id;
  SELECT * INTO asset FROM public.screenshot_assets WHERE id=NEW.asset_id;
  SELECT * INTO proto FROM public.screenshot_prototype_runs WHERE id=asset.prototype_run_id;
  SELECT * INTO batch FROM public.prototype_decision_batches WHERE id=NEW.batch_id;
  SELECT id INTO previous FROM public.prototype_evidence_decisions WHERE evidence_id=NEW.evidence_id
    ORDER BY created_at DESC,id DESC LIMIT 1;
  IF card.task_id IS DISTINCT FROM NEW.task_id OR card.extraction_job_id IS DISTINCT FROM NEW.extraction_job_id
    OR card.current_revision_id IS DISTINCT FROM NEW.card_revision_id OR rev.card_id IS DISTINCT FROM card.id OR rev.state IS DISTINCT FROM 'confirmed'
    OR evidence.card_id IS DISTINCT FROM card.id OR evidence.kind IS DISTINCT FROM 'image_region' OR evidence.confirmed_at IS NULL
    OR asset.origin IS DISTINCT FROM 'prototype' OR proto.id IS NULL
    OR (evidence.screenshot_asset_id,evidence.screenshot_rendition_id,evidence.image_sha256) IS DISTINCT FROM (NEW.asset_id,NEW.rendition_id,NEW.image_sha256)
    OR (asset.task_feature_id,asset.feature_revision_id,proto.html_sha256) IS DISTINCT FROM (NEW.task_feature_id,NEW.feature_revision_id,NEW.html_sha256)
    OR NEW.previous_decision_id IS DISTINCT FROM previous
    OR (previous IS NOT NULL AND (NEW.reason IS NULL OR length(btrim(NEW.reason))=0))
    OR NOT (batch.task_feature_ids ? NEW.task_feature_id::text)
    OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links l WHERE l.revision_id=NEW.card_revision_id AND l.evidence_id=NEW.evidence_id)
    OR public.response_quote_current(NEW.card_revision_id) IS DISTINCT FROM true
    OR public.response_citation_valid(card.requirement_id) IS DISTINCT FROM true
    OR public.response_evidence_active(NEW.evidence_id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Prototype decision binding is stale or incomplete' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_privacy_gate()
CREATE FUNCTION public.screenshot_privacy_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE rendition public.screenshot_renditions%ROWTYPE;
BEGIN
  IF NEW.attachment_source_id IS NOT NULL THEN
    PERFORM public.attachment_screenshot_privacy_guard(NEW.id);
    RETURN NEW;
  END IF;
  IF NEW.annotation_request_id IS NOT NULL THEN
    PERFORM public.annotation_certificate_privacy(NEW.task_id,NEW.asset_id,NEW.rendition_id,
      NEW.id,NEW.annotation_request_id,NEW.reviewed_upload_sha256,NEW.stored_image_sha256,NEW.rule_version);
    RETURN NEW;
  END IF;
  PERFORM public.screenshot_require_ingest();
  SELECT * INTO rendition FROM public.screenshot_renditions WHERE id=NEW.rendition_id;
  IF rendition.id IS NULL OR rendition.parent_rendition_id IS NOT NULL
    OR rendition.privacy_review_id IS DISTINCT FROM NEW.id OR rendition.asset_id IS DISTINCT FROM NEW.asset_id
    OR rendition.image_sha256 IS DISTINCT FROM NEW.stored_image_sha256
    OR rendition.upload_sha256 IS DISTINCT FROM NEW.reviewed_upload_sha256
    OR NEW.rule_version IS DISTINCT FROM 'screenshot-privacy-v1' THEN
    RAISE EXCEPTION 'Privacy review must bind exact uploaded and stored hashes' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_prototype_gate()
CREATE FUNCTION public.screenshot_prototype_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.jobs j WHERE j.id=NEW.generation_job_id
    AND j.task_id=NEW.task_id AND j.kind='prototype_generate' AND j.status='running')
    OR (NEW.html_sha256 ~ '^[0-9a-f]{64}$' AND NEW.source_image_sha256 ~ '^[0-9a-f]{64}$'
      AND NEW.sandbox_receipt_sha256 ~ '^[0-9a-f]{64}$' AND NEW.input_hash ~ '^[0-9a-f]{64}$'
      AND length(NEW.sandbox_receipt_id)>0 AND jsonb_typeof(NEW.provenance)='object'
      AND length(NEW.provenance->>'provider')>0 AND length(NEW.provenance->>'model')>0
      AND length(NEW.provenance->>'catalog_identity')>0) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Prototype requires a fixed generation and sandbox receipt' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $_$;

-- FUNCTION: screenshot_rendition_gate()
CREATE FUNCTION public.screenshot_rendition_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE asset public.screenshot_assets%ROWTYPE; parent public.screenshot_renditions%ROWTYPE;
        w integer; h integer; cw integer; ch integer; ox integer; oy integer; fh integer;
        source_w integer; source_h integer; crop jsonb; region jsonb;
BEGIN
  IF NEW.profile IN ('annotation-candidate-v1','annotation-release-v1') THEN
    PERFORM public.annotation_rendition_valid(NEW.id);
    RETURN NEW;
  END IF;
  IF EXISTS(SELECT 1 FROM public.screenshot_renditions r WHERE r.id=NEW.parent_rendition_id
    AND r.profile IN ('annotation-candidate-v1','annotation-release-v1')) THEN
    RAISE EXCEPTION 'Legacy annotation cannot derive B05 profiles' USING ERRCODE='23514'; END IF;
  SELECT * INTO asset FROM public.screenshot_assets WHERE id=NEW.asset_id;
  IF asset.id IS NULL OR NEW.source_sha256 IS DISTINCT FROM asset.source_sha256
    OR NEW.profile IS DISTINCT FROM (CASE WHEN asset.origin='prototype' THEN 'prototype-clean-v1' ELSE 'screenshot-markup-v1' END)
    OR EXISTS(SELECT 1 FROM public.screenshot_withdrawals WHERE asset_id=asset.id) THEN
    RAISE EXCEPTION 'Invalid rendition source' USING ERRCODE='23514';
  END IF;
  IF NEW.parent_rendition_id IS NOT NULL THEN
    SELECT * INTO parent FROM public.screenshot_renditions WHERE id=NEW.parent_rendition_id;
    IF parent.asset_id IS DISTINCT FROM NEW.asset_id OR parent.privacy_review_id IS DISTINCT FROM NEW.privacy_review_id
      OR parent.upload_sha256 IS DISTINCT FROM NEW.upload_sha256 OR NEW.generation_job_id IS NULL THEN
      RAISE EXCEPTION 'Rendition cannot change its privacy lineage' USING ERRCODE='23514';
    END IF;
    source_w := (parent.mapping->>'content_width')::integer; source_h := (parent.mapping->>'content_height')::integer;
  ELSE
    PERFORM public.screenshot_require_ingest();
    source_w := asset.source_width; source_h := asset.source_height;
  END IF;
  w := (NEW.image->>'width_px')::integer; h := (NEW.image->>'height_px')::integer;
  cw := (NEW.mapping->>'content_width')::integer; ch := (NEW.mapping->>'content_height')::integer;
  ox := (NEW.mapping->>'content_offset_x')::integer; oy := (NEW.mapping->>'content_offset_y')::integer;
  fh := (NEW.mapping->>'footer_height')::integer; crop := NEW.mapping->'crop';
  IF (jsonb_typeof(NEW.image)='object' AND jsonb_typeof(NEW.mapping)='object' AND jsonb_typeof(NEW.plan)='object'
    AND NEW.image->>'sha256'=NEW.image_sha256 AND NEW.image->>'media_type'='image/png'
    AND w BETWEEN 1 AND 8192 AND h BETWEEN 1 AND 8192 AND w::bigint*h <= 20000000
    AND (NEW.image->>'size_bytes')::integer BETWEEN 1 AND 41943040
    AND cw>0 AND ch>0 AND ox>=0 AND oy=0 AND fh>=0 AND ox+cw<=w AND ch+fh=h
    AND (crop->>'width')::integer=cw AND (crop->>'height')::integer=ch
    AND (crop->>'x')::integer>=0 AND (crop->>'y')::integer>=0
    AND (crop->>'x')::integer+cw<=source_w AND (crop->>'y')::integer+ch<=source_h
    AND NEW.plan ?& ARRAY['redact','crop','boxes'] AND NEW.plan - ARRAY['redact','crop','boxes'] = '{}'::jsonb
    AND jsonb_typeof(NEW.plan->'redact')='array' AND jsonb_array_length(NEW.plan->'redact')<=200
    AND jsonb_typeof(NEW.plan->'boxes')='array' AND jsonb_array_length(NEW.plan->'boxes')<=20
    AND ((asset.origin='prototype' AND fh=0 AND ox=0 AND cw=w) OR (asset.origin<>'prototype' AND fh>0))
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Invalid bounded RGB rendition descriptor' USING ERRCODE='23514';
  END IF;
  FOR region IN SELECT value FROM jsonb_array_elements((NEW.plan->'redact') || (NEW.plan->'boxes')) LOOP
    IF ((region->>'x')::integer>=0 AND (region->>'y')::integer>=0 AND (region->>'width')::integer>0 AND (region->>'height')::integer>0
      AND (region->>'x')::integer+(region->>'width')::integer<=source_w
      AND (region->>'y')::integer+(region->>'height')::integer<=source_h) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Invalid image rectangle' USING ERRCODE='23514';
    END IF;
  END LOOP;
  FOR region IN SELECT value FROM jsonb_array_elements(NEW.plan->'boxes') LOOP
    IF ((region->>'x')::integer >= (crop->>'x')::integer AND (region->>'y')::integer >= (crop->>'y')::integer
      AND (region->>'x')::integer+(region->>'width')::integer <= (crop->>'x')::integer+cw
      AND (region->>'y')::integer+(region->>'height')::integer <= (crop->>'y')::integer+ch) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'Box outside crop' USING ERRCODE='23514';
    END IF;
  END LOOP;
  IF NEW.generation_job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.jobs j WHERE j.id=NEW.generation_job_id AND j.task_id=NEW.task_id AND j.kind='screenshot_render' AND j.status='running') THEN
    RAISE EXCEPTION 'Rendition requires its live rendering job' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_require_ingest()
CREATE FUNCTION public.screenshot_require_ingest() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  IF NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Human privacy release required' USING ERRCODE='42501';
  END IF;
END $$;

-- FUNCTION: screenshot_scope_gate()
CREATE FUNCTION public.screenshot_scope_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE body jsonb; parent jsonb; relation record; parent_id uuid;
BEGIN
  IF NOT EXISTS(SELECT 1 FROM public.jobs j JOIN public.documents d
      ON d.id=j.document_id AND d.task_id=j.task_id
      WHERE j.id=NEW.extraction_job_id
        AND j.task_id=NEW.task_id AND j.kind='extract' AND j.status='succeeded') THEN
    RAISE EXCEPTION 'Screenshot requires its succeeded extraction' USING ERRCODE='23514';
  END IF;
  body := to_jsonb(NEW);
  FOR relation IN SELECT * FROM (VALUES
    ('asset_id','screenshot_assets'),('parent_rendition_id','screenshot_renditions'),
    ('rendition_id','screenshot_renditions'),('vendor_archive_id','screenshot_vendor_archives'),
    ('prototype_run_id','screenshot_prototype_runs'),('search_run_id','screenshot_search_runs'),
    ('search_candidate_id','screenshot_search_candidates'),('analysis_run_id','screenshot_analysis_runs'),
    ('batch_id','prototype_decision_batches'),('previous_decision_id','prototype_evidence_decisions')
  ) AS refs(field_name,table_name) LOOP
    parent_id := (body->>relation.field_name)::uuid;
    IF parent_id IS NOT NULL THEN
      EXECUTE format('SELECT to_jsonb(p) FROM public.%I p WHERE p.id=$1', relation.table_name)
        INTO parent USING parent_id;
      IF parent IS NULL OR parent->>'task_id' IS DISTINCT FROM NEW.task_id::text
        OR parent->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
        RAISE EXCEPTION 'Screenshot parent scope mismatch' USING ERRCODE='23514';
      END IF;
    END IF;
  END LOOP;
  IF body->>'requirement_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.requirements r
    WHERE r.id=(body->>'requirement_id')::uuid
      AND r.task_id=NEW.task_id AND r.job_id=NEW.extraction_job_id) THEN
    RAISE EXCEPTION 'Screenshot requirement scope mismatch' USING ERRCODE='23514';
  END IF;
  IF body->>'task_feature_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.task_features s
    WHERE s.id=(body->>'task_feature_id')::uuid AND s.task_id=NEW.task_id
      AND s.feature_revision_id=(body->>'feature_revision_id')::uuid AND s.active) THEN
    RAISE EXCEPTION 'Screenshot feature binding mismatch' USING ERRCODE='23514';
  END IF;
  IF body->>'task_resource_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.task_resources s
    WHERE s.id=(body->>'task_resource_id')::uuid AND s.task_id=NEW.task_id
      AND s.product_revision_id=(body->>'product_revision_id')::uuid AND s.active) THEN
    RAISE EXCEPTION 'Screenshot product binding mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $_$;

-- FUNCTION: screenshot_selection_no_reactivation()
CREATE FUNCTION public.screenshot_selection_no_reactivation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  IF OLD.active=false AND NEW.active=true THEN
    RAISE EXCEPTION 'Historical selections cannot be reactivated; create a new selection' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_suggestion_gate()
CREATE FUNCTION public.screenshot_suggestion_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE img public.screenshot_analysis_inputs%ROWTYPE; req public.screenshot_analysis_inputs%ROWTYPE;
        rendition public.screenshot_renditions%ROWTYPE; region jsonb;
BEGIN
  SELECT * INTO img FROM public.screenshot_analysis_inputs WHERE id=NEW.image_input_id;
  SELECT * INTO req FROM public.screenshot_analysis_inputs WHERE id=NEW.requirement_input_id;
  SELECT * INTO rendition FROM public.screenshot_renditions WHERE id=img.rendition_id;
  region := NEW.proposal->'region';
  IF img.rendition_id IS NULL OR img.analysis_run_id IS DISTINCT FROM NEW.analysis_run_id
    OR (NEW.requirement_input_id IS NOT NULL AND (req.requirement_id IS NULL OR req.analysis_run_id IS DISTINCT FROM NEW.analysis_run_id))
    OR NEW.proposal->>'image_ref' IS DISTINCT FROM img.ref
    OR NEW.proposal->>'requirement_ref' IS DISTINCT FROM req.ref
    OR (NEW.proposal->>'purpose' IN ('match_requirements','propose_regions','read_text')) IS DISTINCT FROM true
    OR (NEW.proposal->>'purpose'='match_requirements' AND req.requirement_id IS NULL)
    OR (NEW.proposal->>'purpose' IN ('propose_regions','read_text') AND (region IS NULL OR region='null'::jsonb))
    OR (NEW.proposal->>'purpose'='read_text' AND coalesce(length(btrim(NEW.proposal->>'suggested_text')),0)=0) THEN
    RAISE EXCEPTION 'Suggestion requires locally bound inputs' USING ERRCODE='23514';
  END IF;
  IF region IS NOT NULL AND region<>'null'::jsonb AND (
    (region->>'x')::integer >= 0 AND (region->>'y')::integer >= 0
    AND (region->>'width')::integer > 0 AND (region->>'height')::integer > 0
    AND (region->>'x')::integer+(region->>'width')::integer <= (rendition.mapping->>'content_width')::integer
    AND (region->>'y')::integer+(region->>'height')::integer <= (rendition.mapping->>'content_height')::integer
  ) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Suggestion region must use visible content coordinates' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_vendor_archive_gate()
CREATE FUNCTION public.screenshot_vendor_archive_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE r public.sandbox_runs; i public.sandbox_inputs; j public.jobs; a public.sandbox_attempts;
        art public.sandbox_artifacts; rec public.sandbox_fetch_receipts;
BEGIN
  PERFORM public.screenshot_require_ingest();
  SELECT * INTO r FROM public.sandbox_runs WHERE id=NEW.sandbox_run_id;
  SELECT * INTO i FROM public.sandbox_inputs WHERE id=r.input_id;
  SELECT * INTO j FROM public.jobs WHERE id=r.job_id;
  SELECT * INTO a FROM public.sandbox_attempts
    WHERE sandbox_run_id=r.id AND job_id=j.id AND attempt_id=j.run_id;
  SELECT * INTO art FROM public.sandbox_artifacts WHERE id=NEW.archive_artifact_id;
  SELECT * INTO rec FROM public.sandbox_fetch_receipts WHERE id=NEW.entry_receipt_id;
  IF r.id IS NULL OR r.task_id IS DISTINCT FROM NEW.task_id
    OR i.purpose IS DISTINCT FROM 'vendor_capture'
    OR i.extraction_job_id IS DISTINCT FROM NEW.extraction_job_id
    OR (i.task_resource_id,i.product_revision_id) IS DISTINCT FROM (NEW.task_resource_id,NEW.product_revision_id)
    OR j.status IS DISTINCT FROM 'succeeded' OR a.termination_code IS DISTINCT FROM 'succeeded'
    OR a.cleanup_state IS DISTINCT FROM 'complete'
    OR art.attempt_record_id IS DISTINCT FROM a.id
    OR art.kind IS DISTINCT FROM (CASE WHEN i.spec->>'format'='pdf' THEN 'source_pdf' ELSE 'capture_archive' END)
    OR art.plaintext_sha256 IS DISTINCT FROM NEW.archive_sha256
    OR art.object_key IS DISTINCT FROM NEW.storage_key
    OR rec.attempt_record_id IS DISTINCT FROM a.id OR rec.decision_code IS DISTINCT FROM 'allowed'
    OR rec.status_code IS DISTINCT FROM 200 OR rec.response_sha256 IS DISTINCT FROM NEW.content_sha256
    OR (i.spec->>'format'='pdf' AND NEW.content_sha256 IS DISTINCT FROM NEW.archive_sha256)
    -- Search candidates have no producer yet; an unchecked candidate link is refused.
    OR NEW.search_candidate_id IS NOT NULL
    OR jsonb_typeof(NEW.descriptor) IS DISTINCT FROM 'object'
    OR NEW.descriptor->>'sha256' IS DISTINCT FROM NEW.archive_sha256
    OR (NEW.descriptor->>'size_bytes')::bigint IS DISTINCT FROM art.size_bytes::bigint
    OR jsonb_typeof(NEW.provenance) IS DISTINCT FROM 'object'
    OR jsonb_typeof(NEW.provenance->'incomplete') IS DISTINCT FROM 'boolean'
    OR (NEW.provenance->>'failed_request_count')::bigint IS DISTINCT FROM (
      SELECT count(*) FROM public.sandbox_fetch_receipts f
      WHERE f.attempt_record_id=a.id AND f.decision_code<>'allowed')
    OR (NEW.provenance->>'incomplete')::boolean IS DISTINCT FROM (
      (NEW.provenance->>'failed_request_count')::bigint > 0
      OR jsonb_path_exists(a.issues, '$[*] ? (@.code == "vendor_resources_incomplete")')) THEN
    RAISE EXCEPTION 'Vendor archive must match its succeeded sandbox capture' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $_$;

-- FUNCTION: screenshot_vendor_asset_gate()
CREATE FUNCTION public.screenshot_vendor_asset_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v public.screenshot_vendor_archives; archive public.sandbox_artifacts; art public.sandbox_artifacts;
BEGIN
  IF NEW.vendor_archive_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO v FROM public.screenshot_vendor_archives WHERE id=NEW.vendor_archive_id;
  SELECT * INTO archive FROM public.sandbox_artifacts WHERE id=v.archive_artifact_id;
  SELECT * INTO art FROM public.sandbox_artifacts
    WHERE id=(NEW.source->>'sandbox_artifact_id')::uuid;
  IF (v.task_resource_id,v.product_revision_id) IS DISTINCT FROM (NEW.task_resource_id,NEW.product_revision_id)
    OR NEW.source - ARRAY['kind','sandbox_artifact_id'] <> '{}'::jsonb
    OR art.attempt_record_id IS DISTINCT FROM archive.attempt_record_id
    OR art.kind IS DISTINCT FROM (CASE NEW.source_kind WHEN 'vendor_web' THEN 'capture_png' ELSE 'pdf_page_png' END)
    OR (NEW.source_kind='vendor_pdf') IS DISTINCT FROM (archive.kind='source_pdf')
    OR art.plaintext_sha256 IS DISTINCT FROM NEW.source_sha256
    OR (art.width,art.height) IS DISTINCT FROM (NEW.source_width,NEW.source_height)
    OR NEW.source_hash_assurance IS DISTINCT FROM 'server_verified' THEN
    RAISE EXCEPTION 'Vendor image must be a page of its archived capture' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: screenshot_withdraw_gate()
CREATE FUNCTION public.screenshot_withdraw_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  PERFORM public.screenshot_require_ingest();
  IF NEW.reason IS NULL OR length(btrim(NEW.reason))=0 THEN
    RAISE EXCEPTION 'Withdrawal requires a human and reason' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;
