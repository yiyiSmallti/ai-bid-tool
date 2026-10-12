-- FUNCTION: check_child_gate()
CREATE FUNCTION public.check_child_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.check_runs%ROWTYPE; item public.check_items%ROWTYPE;
  response public.response_items%ROWTYPE; req public.requirements%ROWTYPE;
  finding public.check_findings%ROWTYPE; revision public.response_card_revisions%ROWTYPE;
  proof public.evidence%ROWTYPE; raw_text text; report_xid text;
  cert public.certificate_revisions%ROWTYPE; expected_date_status text;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE id=NEW.report_id;
  SELECT xmin::text INTO report_xid FROM public.check_runs WHERE id=NEW.report_id;
  IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'Historical check report is closed' USING ERRCODE='42501';
  END IF;
  IF public.citation_publication_is_sealed(NEW.report_id,'check') THEN
    RAISE EXCEPTION 'Completed check publication is closed' USING ERRCODE='42501';
  END IF;
  PERFORM public.check_live_attempt(NEW.report_id);
  IF TG_TABLE_NAME='check_items' THEN
    IF (r.mode='rules' AND NEW.semantic_status<>'not_requested')
      OR (r.mode='combined' AND ((NEW.partition='response' AND NEW.semantic_status='not_requested')
        OR (NEW.partition<>'response' AND NEW.semantic_status<>'not_requested'))) THEN
      RAISE EXCEPTION 'Check semantic coverage must match mode' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE id=NEW.response_item_id;
    SELECT * INTO req FROM public.requirements WHERE id=NEW.requirement_id;
    IF NEW.card_revision_id IS DISTINCT FROM response.card_revision_id
      OR NEW.partition IS DISTINCT FROM (CASE response.kind WHEN 'row' THEN 'response' ELSE response.kind END)
      OR NEW.source IS DISTINCT FROM response.source
      OR NEW.source IS DISTINCT FROM jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,'page',req.page,'location',req.location,'quote',req.quote) THEN
      RAISE EXCEPTION 'Check item source or partition mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificates' THEN
    SELECT * INTO cert FROM public.certificate_revisions WHERE id=NEW.certificate_revision_id;
    expected_date_status := CASE
      WHEN (cert.data->>'valid_until')::date < NEW.assessment_date THEN 'expired'
      WHEN (cert.data->>'valid_from')::date > NEW.assessment_date THEN 'not_yet_valid'
      WHEN cert.data->>'valid_from' IS NOT NULL AND cert.data->>'valid_until' IS NOT NULL THEN 'valid'
      ELSE 'unknown' END;
    IF NEW.assessment_date IS DISTINCT FROM r.assessment_date OR NEW.date_status IS DISTINCT FROM expected_date_status
      OR NOT EXISTS(SELECT 1 FROM public.task_certificates WHERE id=NEW.task_certificate_id AND active) THEN
      RAISE EXCEPTION 'Check certificate dates or selection mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_certificate_items' THEN
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.check_items i
      ON i.report_id=a.report_id
      JOIN public.response_items s ON s.id=i.response_item_id
      JOIN public.card_evidence_links l ON l.revision_id=s.card_revision_id
      JOIN public.evidence e ON e.id=l.evidence_id
      WHERE a.id=NEW.certificate_id AND i.id=NEW.check_item_id
        AND a.report_id=NEW.report_id AND s.kind='row' AND e.confirmed_at IS NOT NULL
        AND e.task_certificate_id=a.task_certificate_id AND e.certificate_revision_id=a.certificate_revision_id
        AND public.response_evidence_active(e.id)) THEN
      RAISE EXCEPTION 'Check certificate lacks confirmed requirement binding' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_findings' THEN
    SELECT * INTO item FROM public.check_items WHERE id=NEW.check_item_id;
    SELECT * INTO revision FROM public.response_card_revisions WHERE id=item.card_revision_id;
    IF NEW.method='semantic' AND (r.mode<>'combined' OR item.semantic_status<>'assessed'
      OR item.semantic_outcome IS DISTINCT FROM 'risk') THEN
      RAISE EXCEPTION 'Semantic finding requires assessed risk' USING ERRCODE='23514';
    END IF;
    IF NEW.source IS DISTINCT FROM item.source OR NEW.review_domain IS DISTINCT FROM revision.review_domain
      OR (SELECT count(*) FROM public.check_findings WHERE check_item_id=NEW.check_item_id)>=20 THEN
      RAISE EXCEPTION 'Check finding source, domain or count mismatch' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='check_finding_citations' THEN
    SELECT * INTO finding FROM public.check_findings WHERE id=NEW.finding_id;
    IF num_nonnulls(NEW.finding_id,NEW.check_item_id)<>1 THEN
      RAISE EXCEPTION 'Check citation requires exactly one target' USING ERRCODE='23514';
    END IF;
    SELECT * INTO item FROM public.check_items WHERE id=COALESCE(NEW.check_item_id,finding.check_item_id);
    IF item.id IS NULL OR item.report_id IS DISTINCT FROM NEW.report_id
      OR item.task_id IS DISTINCT FROM NEW.task_id
      OR (NEW.check_item_id IS NOT NULL AND (r.mode<>'combined'
        OR item.semantic_status<>'assessed' OR item.semantic_outcome IS DISTINCT FROM 'no_risk_found')) THEN
      RAISE EXCEPTION 'Check citation target mismatch' USING ERRCODE='23514';
    END IF;
    SELECT * INTO response FROM public.response_items WHERE id=item.response_item_id;
    IF (SELECT count(*) FROM public.check_finding_citations WHERE ((NEW.finding_id IS NOT NULL AND finding_id=NEW.finding_id)
        OR (NEW.check_item_id IS NOT NULL AND check_item_id=NEW.check_item_id)))>=20 THEN
      RAISE EXCEPTION 'Check citation limit exceeded' USING ERRCODE='23514';
    END IF;
    IF NEW.kind='tender' THEN
      SELECT * INTO req FROM public.requirements WHERE id=item.requirement_id;
      IF NEW.source IS DISTINCT FROM jsonb_set(item.source,'{quote}',to_jsonb(NEW.quote))
        OR (finding.method='deterministic' AND NEW.quote IS DISTINCT FROM req.quote)
        OR NEW.document_id IS DISTINCT FROM req.document_id OR NEW.chunk_id IS DISTINCT FROM req.chunk_id THEN
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
      SELECT * INTO proof FROM public.evidence WHERE id=NEW.evidence_id;
      IF proof.confirmed_at IS NULL OR proof.kind='image_region'
        OR public.response_evidence_active(proof.id) IS DISTINCT FROM true
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE revision_id=item.card_revision_id AND evidence_id=proof.id)
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

-- FUNCTION: check_current_inputs(uuid)
CREATE FUNCTION public.check_current_inputs(p_report uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.requirement_draft_current(c.draft_id) AND public.requirement_prior_check_current_inputs(p_report)
 FROM public.check_runs c WHERE c.id=p_report
$$;

-- FUNCTION: check_decision_gate()
CREATE FUNCTION public.check_decision_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE f public.check_findings%ROWTYPE; r public.check_runs%ROWTYPE; previous public.check_decisions%ROWTYPE;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE id=NEW.report_id;
  PERFORM 1 FROM public.tasks WHERE id=r.task_id FOR SHARE;
  PERFORM pg_advisory_xact_lock(hashtextextended('check.finding:'||NEW.finding_id::text,0));
  SELECT * INTO f FROM public.check_findings WHERE id=NEW.finding_id;
  PERFORM public.response_check_actor(NULL,'session');
  IF NEW.actor_kind IS DISTINCT FROM 'session' OR NOT public.actor_is_human() THEN
    RAISE EXCEPTION 'Check decision requires the responsible human' USING ERRCODE='42501';
  END IF;
  SELECT * INTO previous FROM public.check_decisions WHERE finding_id=NEW.finding_id ORDER BY revision DESC LIMIT 1;
  IF NEW.revision<>coalesce(previous.revision,1)+1
    OR NEW.action IS DISTINCT FROM (CASE WHEN previous.action='dismiss' THEN 'reopen' ELSE 'dismiss' END)
    OR NEW.expected_input_hash IS DISTINCT FROM r.input_hash THEN
    RAISE EXCEPTION 'Check decision revision or input conflict' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.report_id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Stale check report cannot receive decisions' USING ERRCODE='23514';
  END IF;
  IF NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
    RAISE EXCEPTION 'Check decision reason hash mismatch' USING ERRCODE='23514';
  END IF;
  NEW.decided_at := clock_timestamp();
  RETURN NEW;
END $$;

-- FUNCTION: check_immutable_gate()
CREATE FUNCTION public.check_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
  -- The migration owner may rotate ciphertext; runtime grants never allow updates.
  IF TG_TABLE_NAME='check_runs' AND TG_OP='UPDATE'
    AND current_user = (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
    AND (to_jsonb(NEW)-'encrypted_input') = (to_jsonb(OLD)-'encrypted_input') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Check history is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: check_live_attempt(uuid)
CREATE FUNCTION public.check_live_attempt(p_report uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.check_runs%ROWTYPE; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO STRICT r FROM public.check_runs WHERE id=p_report;
  SELECT * INTO j FROM public.jobs WHERE id=r.job_id FOR UPDATE;
  IF j.status IS DISTINCT FROM 'running' OR j.run_id IS DISTINCT FROM r.run_id
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
    OR current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker' THEN
    RAISE EXCEPTION 'Check job attempt is not live' USING ERRCODE='23514';
  END IF;
  PERFORM public.response_check_actor(r.actor_token_id,'worker');
END $$;

-- FUNCTION: check_run_complete()
CREATE FUNCTION public.check_run_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE total integer; actual integer; j public.jobs%ROWTYPE;
BEGIN
  SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id;
  IF j.status NOT IN ('running','succeeded') OR j.run_id IS DISTINCT FROM NEW.run_id
    OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() THEN
    RAISE EXCEPTION 'Check attempt lost before publication' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO total FROM public.requirements WHERE task_id=NEW.task_id AND job_id=NEW.extraction_job_id;
  SELECT count(*) INTO actual FROM public.check_items WHERE report_id=NEW.id;
  IF total NOT BETWEEN 1 AND 2000 OR actual<>total
    OR actual<>(SELECT count(*) FROM public.response_items WHERE draft_id=NEW.draft_id) THEN
    RAISE EXCEPTION 'Check must cover the entire draft exactly once' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i JOIN public.response_items s ON s.id=i.response_item_id
    LEFT JOIN public.response_cards c ON c.id=s.card_id
    WHERE i.report_id=NEW.id AND
      ((s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards x WHERE x.requirement_id=s.requirement_id))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.card_evidence_links l JOIN public.evidence e ON e.id=l.evidence_id
        WHERE l.revision_id=s.card_revision_id AND (e.confirmed_at IS NULL OR public.response_evidence_active(e.id) IS DISTINCT FROM true)))
      OR (s.kind='row' AND EXISTS(SELECT 1 FROM public.response_card_revisions v WHERE v.id=s.card_revision_id
        AND public.response_generation_materials_active(v.model_job_id) IS DISTINCT FROM true)))) THEN
    RAISE EXCEPTION 'Check draft dependencies changed' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM public.task_certificates WHERE task_id=NEW.task_id AND active)
    <> (SELECT count(*) FROM public.check_certificates WHERE report_id=NEW.id)
    OR EXISTS(SELECT 1 FROM public.check_certificates c JOIN public.task_certificates s ON s.id=c.task_certificate_id
      WHERE c.report_id=NEW.id AND NOT s.active) THEN
    RAISE EXCEPTION 'Check must include every selected certificate' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.report_id=NEW.id AND
    NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.finding_id=f.id AND c.kind='tender')) THEN
    RAISE EXCEPTION 'Check finding requires a tender citation' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_items i WHERE i.report_id=NEW.id AND (
    (NEW.mode='rules' AND i.semantic_status<>'not_requested')
    OR (NEW.mode='combined' AND ((i.partition='response' AND i.semantic_status='not_requested')
      OR (i.partition<>'response' AND i.semantic_status<>'not_requested')))
    OR (i.semantic_outcome='risk' AND NOT EXISTS(SELECT 1 FROM public.check_findings f
      WHERE f.check_item_id=i.id AND f.method='semantic'))
    OR (i.semantic_outcome='no_risk_found' AND (
      NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.check_item_id=i.id AND c.kind='tender')
      OR NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.check_item_id=i.id AND c.kind IN ('draft','evidence'))))
    OR ((i.semantic_outcome IS DISTINCT FROM 'risk' OR i.semantic_status<>'assessed')
      AND EXISTS(SELECT 1 FROM public.check_findings f WHERE f.check_item_id=i.id AND f.method='semantic')))) THEN
    RAISE EXCEPTION 'Check semantic outcome lacks matching support' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM public.check_findings f WHERE f.report_id=NEW.id
    AND f.method='semantic' AND f.code='semantic_contradiction'
    AND NOT EXISTS(SELECT 1 FROM public.check_finding_citations c WHERE c.finding_id=f.id AND c.kind IN ('draft','evidence'))) THEN
    RAISE EXCEPTION 'Semantic contradiction requires bid support' USING ERRCODE='23514';
  END IF;
  IF public.check_current_inputs(NEW.id) IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'Check fixed input changed' USING ERRCODE='23514';
  END IF;
  IF (NEW.summary->>'item_count')::integer IS DISTINCT FROM actual
    OR (NEW.summary->>'finding_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_findings WHERE report_id=NEW.id)
    OR (NEW.summary->>'unassessed_count')::integer IS DISTINCT FROM (SELECT count(*)::integer FROM public.check_items i
      WHERE i.report_id=NEW.id AND (i.semantic_status='unassessed' OR EXISTS(SELECT 1 FROM jsonb_array_elements(i.rules) x WHERE x->>'outcome'='unknown')))
    OR NEW.completion IS DISTINCT FROM (CASE WHEN (NEW.summary->>'unassessed_count')::integer>0 THEN 'partial' ELSE 'complete' END) THEN
    RAISE EXCEPTION 'Check summary must match published coverage' USING ERRCODE='23514';
  END IF;
  PERFORM public.citation_seal_publication(NEW.id,'check');
  RETURN NULL;
END $$;

-- FUNCTION: check_run_gate()
CREATE FUNCTION public.check_run_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE j public.jobs%ROWTYPE; d public.draft_runs%ROWTYPE;
BEGIN
  PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
  IF NEW.actor_kind IS DISTINCT FROM 'worker'
    OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens
      WHERE id=NEW.actor_token_id AND scopes ? 'check:run')) THEN
    RAISE EXCEPTION 'Check worker permission required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO j FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
  SELECT * INTO d FROM public.draft_runs WHERE id=NEW.draft_id;
  IF j.kind IS DISTINCT FROM 'check' OR j.status IS DISTINCT FROM 'running'
    OR j.run_id IS DISTINCT FROM NEW.run_id OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp()
    OR j.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
    OR j.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
    OR j.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input

    OR NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
    OR NEW.input_manifest->>'draft_id' IS DISTINCT FROM NEW.draft_id::text
    OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text
    OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text
    OR NEW.input_manifest->>'assessment_date' IS DISTINCT FROM to_char(NEW.assessment_date,'YYYY-MM-DD')
    OR NEW.input_manifest->>'draft_input_hash' IS DISTINCT FROM NEW.draft_input_hash
    OR NEW.input_manifest->>'mode' IS DISTINCT FROM NEW.mode
    OR NEW.input_manifest->>'prompt_version' IS DISTINCT FROM NEW.prompt_version
    OR (NEW.mode='combined' AND NEW.input_manifest->'model_redaction_enabled' IS DISTINCT FROM 'true'::jsonb)
    OR NEW.input_manifest->>'rule_version' IS DISTINCT FROM NEW.rule_version
    OR NEW.input_manifest->>'schema_version' IS DISTINCT FROM NEW.schema_version
    OR jsonb_typeof(NEW.input_manifest->'items') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'certificates') IS DISTINCT FROM 'array'
    OR jsonb_typeof(NEW.input_manifest->'confidential') IS DISTINCT FROM 'array'
    OR d.input_hash IS DISTINCT FROM NEW.draft_input_hash
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=NEW.extraction_job_id AND kind='extract' AND status='succeeded') THEN
    RAISE EXCEPTION 'Invalid check publication attempt or input' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

-- FUNCTION: citation_publication_is_sealed(uuid, text)
CREATE FUNCTION public.citation_publication_is_sealed(p_report uuid, p_kind text) RETURNS boolean
    LANGUAGE sql STRICT
    SET search_path TO 'pg_catalog'
    AS $$
  SELECT EXISTS(
    SELECT 1 FROM pg_locks
    WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted AND objsubid=1
      AND classid=((public.citation_publication_lock_key(p_report,p_kind)>>32)&4294967295)::oid
      AND objid=(public.citation_publication_lock_key(p_report,p_kind)&4294967295)::oid
  )
$$;

-- FUNCTION: citation_publication_lock_key(uuid, text)
CREATE FUNCTION public.citation_publication_lock_key(p_report uuid, p_kind text) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $$
  SELECT hashtextextended('bid.citation_publication:'||p_kind||':'||p_report::text,0)
$$;

-- FUNCTION: citation_seal_publication(uuid, text)
CREATE FUNCTION public.citation_seal_publication(p_report uuid, p_kind text) RETURNS void
    LANGUAGE sql STRICT
    SET search_path TO 'pg_catalog'
    AS $$
  -- A seal only rejects further child rows. Acquiring it directly cannot claim
  -- that verification succeeded, and transaction locks cannot be unlocked.
  SELECT pg_advisory_xact_lock(public.citation_publication_lock_key(p_report,p_kind))
$$;

-- FUNCTION: evidence_source_page_bounds()
CREATE FUNCTION public.evidence_source_page_bounds() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
        DECLARE parent_pages integer;
        BEGIN
            SELECT (file->>'page_count')::integer INTO parent_pages
            FROM public.certificate_files WHERE id = NEW.certificate_file_id AND certificate_id = NEW.certificate_id
                AND certificate_revision_id = NEW.certificate_revision_id;
            IF FOUND AND NEW.page > parent_pages THEN
                RAISE EXCEPTION 'Page is outside source original' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

-- FUNCTION: requirement_archived_guard()
CREATE FUNCTION public.requirement_archived_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 RETURN NEW;
END $$;

-- FUNCTION: requirement_canonical(jsonb)
CREATE FUNCTION public.requirement_canonical(value jsonb) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE result text;
BEGIN
 CASE jsonb_typeof(value)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||public.requirement_canonical(val),',' ORDER BY key COLLATE "C"),'')||'}'
   INTO result FROM jsonb_each(value) AS x(key,val);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(public.requirement_canonical(val),',' ORDER BY ord),'')||']'
   INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS x(val,ord);
 WHEN 'number' THEN result:=regexp_replace(regexp_replace(value::text,'(\.[0-9]*?)0+$','\1'),'\.$','');
 ELSE result:=value::text;
 END CASE;
 RETURN result;
END $_$;

-- FUNCTION: requirement_content(uuid)
CREATE FUNCTION public.requirement_content(p_req uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT jsonb_build_object('category',r.category,'starred',r.starred,'text',r.text,'condition',r.condition,
 'source',jsonb_build_object('document_id',r.document_id,'chunk_id',r.chunk_id,'page',r.page,'location',r.location,'quote',r.quote))
 FROM public.requirements r WHERE r.id=p_req
$$;

-- FUNCTION: requirement_current_pin(uuid)
CREATE FUNCTION public.requirement_current_pin(p_req uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.requirements; c public.chunks; d public.documents; body jsonb; located text; literal text;
 existing_pin jsonb;
 offset_value integer:=1; found integer; first_pos integer; boundary_pos integer; matches integer:=0; boundaries integer:=0;
 separators constant text:=U&'\FF1B;\FF0C,\3002\FF1A:\3001\FF01!\FF1F?\FF08\FF09()\3010\3011[]\300A\300B\201C\201D\2018\2019\0022\0027\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000';
BEGIN
 SELECT source_pin INTO existing_pin FROM public.requirement_reviews WHERE requirement_id=p_req;
 IF existing_pin IS NOT NULL AND public.requirement_pin_valid(p_req,existing_pin) THEN RETURN existing_pin; END IF;
 SELECT * INTO r FROM public.requirements WHERE id=p_req;
 SELECT * INTO c FROM public.chunks WHERE id=r.chunk_id AND task_id=r.task_id AND document_id=r.document_id;
 SELECT * INTO d FROM public.documents WHERE id=r.document_id AND task_id=r.task_id;
 IF c.id IS NULL OR d.id IS NULL OR NOT c.citation_verified THEN RETURN NULL; END IF;
 IF r.location IS NOT NULL THEN SELECT b->>'text' INTO located FROM jsonb_array_elements(c.blocks) b WHERE b-'text'=r.location;
 ELSIF c.blocks IS NULL AND c.page=r.page THEN located:=c.text; END IF;
 IF located IS NULL OR public.response_locate_quote(located,r.quote) IS DISTINCT FROM r.quote THEN RETURN NULL; END IF;
 LOOP
  found:=strpos(substring(located FROM offset_value),r.quote);
  EXIT WHEN found=0;
  found:=found+offset_value-1; matches:=matches+1; first_pos:=coalesce(first_pos,found);
  IF (found=1 OR strpos(separators,substring(located FROM found-1 FOR 1))>0)
   AND (found+length(r.quote)>length(located) OR strpos(separators,substring(located FROM found+length(r.quote) FOR 1))>0)
  THEN boundaries:=boundaries+1; boundary_pos:=found; END IF;
  offset_value:=found+1;
 END LOOP;
 IF matches<>1 THEN IF boundaries<>1 THEN RETURN NULL; END IF; first_pos:=boundary_pos; END IF;
 body:=jsonb_build_object('source',public.requirement_content(p_req)->'source','document_sha256',d.sha256,
 'chunk_sha256',public.requirement_digest(jsonb_build_object('document_id',c.document_id,'text',c.text,'blocks',c.blocks,'seq',c.seq,'page',c.page,'citation_verified',c.citation_verified)),
 'location_sha256',encode(sha256(convert_to(located,'UTF8')),'hex'),'quote_sha256',encode(sha256(convert_to(r.quote,'UTF8')),'hex'),
 'start',first_pos-1,'end',first_pos-1+length(r.quote),'verifier_version','requirement-source-v1');
 RETURN body||jsonb_build_object('binding_sha256',public.requirement_digest(body));
END $$;

-- FUNCTION: requirement_digest(jsonb)
CREATE FUNCTION public.requirement_digest(value jsonb) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT encode(sha256(convert_to(public.requirement_canonical(value),'UTF8')),'hex')
$$;

-- FUNCTION: requirement_draft_current(uuid)
CREATE FUNCTION public.requirement_draft_current(p_draft uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.draft_runs d WHERE d.id=p_draft
 AND jsonb_typeof(d.input_manifest->'requirements')='array'
 AND ARRAY(SELECT r.id FROM public.requirements r WHERE r.job_id=d.extraction_job_id ORDER BY r.id)
 = ARRAY(SELECT (fixed->>'requirement_id')::uuid FROM jsonb_array_elements(d.input_manifest->'requirements') fixed ORDER BY (fixed->>'requirement_id')::uuid)
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(d.input_manifest->'requirements') fixed
 WHERE fixed->'requirement_review' IS DISTINCT FROM public.requirement_review_fields((fixed->>'requirement_id')::uuid)))
$$;

-- FUNCTION: requirement_draft_gate()
CREATE FUNCTION public.requirement_draft_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NOT public.requirement_draft_current(NEW.id) THEN RAISE EXCEPTION 'requirement review manifest is stale' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: requirement_event_pointer()
CREATE FUNCTION public.requirement_event_pointer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v public.requirement_reviews; e public.requirement_review_events;
BEGIN
 SELECT * INTO v FROM public.requirement_reviews WHERE id=NEW.id;
 SELECT * INTO e FROM public.requirement_review_events WHERE id=v.current_event_id;
 IF e.review_id IS DISTINCT FROM v.id OR e.revision IS DISTINCT FROM v.revision OR e.state_after IS DISTINCT FROM v.state
 OR (e.action IN ('confirm','reopen','manual_add','seed') AND e.review_hash IS DISTINCT FROM v.review_hash)
 OR (v.state='confirmed' AND (e.action<>'confirm' OR e.actor_kind<>'session' OR v.confirmed_at IS NULL)) THEN
 RAISE EXCEPTION 'review event pointer mismatch' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;

-- FUNCTION: requirement_hash(uuid, jsonb)
CREATE FUNCTION public.requirement_hash(p_req uuid, p_pin jsonb) RETURNS text
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.requirement_digest(jsonb_build_object('version','requirement-review-v1',
 'task_id',r.task_id,'extraction_job_id',r.job_id,'requirement_id',r.id,'content',public.requirement_content(p_req),
 'source_binding_sha256',p_pin->>'binding_sha256')) FROM public.requirements r WHERE r.id=p_req
$$;

-- FUNCTION: requirement_human_authority(uuid, text)
CREATE FUNCTION public.requirement_human_authority(p_task uuid, p_scope text) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF p_scope IS NULL OR p_scope NOT IN ('req:confirm','req:manual') OR NOT public.actor_is_human() THEN
  RAISE EXCEPTION 'requirement human role required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_task,p_scope,NULL,false,true);
END $$;

-- FUNCTION: requirement_invalidation_audit()
CREATE FUNCTION public.requirement_invalidation_audit() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW.action IN ('invalidate','source_repair') THEN
 INSERT INTO public.audit_logs(id,actor_token_id,action,object_id,details)
 VALUES(gen_random_uuid(),NULL,
 CASE WHEN NEW.action='source_repair' THEN 'requirement.repair_citation' ELSE 'requirement.invalidated' END,
 NEW.requirement_id,jsonb_build_object('task_id',NEW.task_id,'extraction_job_id',NEW.extraction_job_id,
 'requirement_id',NEW.requirement_id,'event_id',NEW.id,'revision',NEW.revision,'actor_kind','system'));
 END IF;
 RETURN NULL;
END $$;

-- FUNCTION: requirement_manual_job_guard()
CREATE FUNCTION public.requirement_manual_job_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD.result->>'origin'='manual' AND NEW IS DISTINCT FROM OLD THEN
 RAISE EXCEPTION 'manual extraction is an immutable terminal receipt' USING ERRCODE='42501'; END IF;
 IF NEW.kind='extract' AND NEW.result->>'origin'='manual' THEN
 PERFORM public.requirement_human_authority(NEW.task_id,'req:manual');
 IF NEW.status<>'succeeded' OR NEW.attempts<>0 OR NEW.provider_config_id IS NOT NULL OR NEW.provider_identity IS NOT NULL
 OR NEW.reasoning IS NOT NULL OR NEW.run_id IS NOT NULL OR NEW.lease_until IS NOT NULL OR NEW.queue_id IS NOT NULL
 OR NEW.finished_at IS NULL THEN RAISE EXCEPTION 'invalid manual extraction receipt' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: requirement_pin_valid(uuid, jsonb)
CREATE FUNCTION public.requirement_pin_valid(p_req uuid, p_pin jsonb) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.requirements; c public.chunks; d public.documents; source_text text; block jsonb;
BEGIN
 SELECT * INTO r FROM public.requirements WHERE id=p_req;
 SELECT * INTO c FROM public.chunks WHERE id=r.chunk_id AND task_id=r.task_id AND document_id=r.document_id;
 SELECT * INTO d FROM public.documents WHERE id=r.document_id AND task_id=r.task_id;
 IF r.id IS NULL OR c.id IS NULL OR d.id IS NULL OR NOT c.citation_verified OR p_pin IS NULL
   OR p_pin->>'verifier_version' IS DISTINCT FROM 'requirement-source-v1'
   OR p_pin->'source' IS DISTINCT FROM public.requirement_content(p_req)->'source'
   OR p_pin->>'document_sha256' IS DISTINCT FROM d.sha256
   OR p_pin->>'chunk_sha256' IS DISTINCT FROM public.requirement_digest(jsonb_build_object('document_id',c.document_id,
      'text',c.text,'blocks',c.blocks,'seq',c.seq,'page',c.page,'citation_verified',c.citation_verified)) THEN RETURN false; END IF;
 IF r.location IS NOT NULL THEN
  SELECT b INTO block FROM jsonb_array_elements(c.blocks) b WHERE b->>'block_id'=r.location->>'block_id';
  IF block IS NULL OR (block-'text') IS DISTINCT FROM r.location THEN RETURN false; END IF;
  source_text:=block->>'text';
 ELSE
  IF c.blocks IS NOT NULL OR r.page IS DISTINCT FROM c.page THEN RETURN false; END IF;
  source_text:=c.text;
 END IF;
 RETURN coalesce(p_pin->>'binding_sha256'=public.requirement_digest(p_pin-'binding_sha256')
 AND p_pin->>'location_sha256'=encode(sha256(convert_to(source_text,'UTF8')),'hex')
 AND p_pin->>'quote_sha256'=encode(sha256(convert_to(r.quote,'UTF8')),'hex')
 AND (p_pin->>'start')::int>=0 AND (p_pin->>'end')::int-(p_pin->>'start')::int=length(r.quote)
 AND substring(source_text FROM (p_pin->>'start')::int+1 FOR length(r.quote))=r.quote,false);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;

-- FUNCTION: requirement_prior_check_current_inputs(uuid)
CREATE FUNCTION public.requirement_prior_check_current_inputs(p_report uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.check_runs%ROWTYPE; t public.tasks%ROWTYPE; i public.check_items%ROWTYPE;
  s public.response_items%ROWTYPE; q public.requirements%ROWTYPE; c public.response_cards%ROWTYPE;
  v public.response_card_revisions%ROWTYPE; e public.evidence%ROWTYPE;
  cf public.confidential_fields%ROWTYPE; fixed jsonb; dep jsonb; cert jsonb; field jsonb;
  value_id uuid; expected_count integer; actual_count integer; citation_results jsonb;
BEGIN
  SELECT * INTO r FROM public.check_runs WHERE id=p_report;
  IF r.id IS NULL THEN RETURN false; END IF;
  SELECT * INTO t FROM public.tasks WHERE id=r.task_id;
  IF (r.input_manifest->>'model_redaction_enabled')::boolean IS DISTINCT FROM t.model_redaction_enabled
    OR (r.input_manifest->>'model_redaction_revision')::integer IS DISTINCT FROM t.model_redaction_revision
    OR r.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE id=r.document_id AND task_id=r.task_id)
    OR NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=r.extraction_job_id
      AND task_id=r.task_id AND document_id=r.document_id AND kind='extract' AND status='succeeded') THEN RETURN false; END IF;
  IF jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.check_items WHERE report_id=p_report)
    OR jsonb_array_length(r.input_manifest->'items')<>(SELECT count(*) FROM public.requirements WHERE task_id=r.task_id AND job_id=r.extraction_job_id) THEN RETURN false; END IF;
  IF (SELECT count(DISTINCT x->>'requirement_id') FROM jsonb_array_elements(r.input_manifest->'items') x)
    <>jsonb_array_length(r.input_manifest->'items') THEN RETURN false; END IF;
  SELECT jsonb_object_agg(verified.requirement_id::text,verified.is_valid) INTO citation_results
    FROM public.response_requirement_citations_valid(
      ARRAY(SELECT requirement_id FROM public.check_items WHERE report_id=p_report)) verified;
  FOR fixed IN SELECT value FROM jsonb_array_elements(r.input_manifest->'items') LOOP
    SELECT * INTO i FROM public.check_items WHERE report_id=p_report AND requirement_id=(fixed->>'requirement_id')::uuid;
    SELECT * INTO s FROM public.response_items WHERE id=i.response_item_id;
    SELECT * INTO q FROM public.requirements WHERE id=i.requirement_id;
    SELECT * INTO c FROM public.response_cards WHERE id=s.card_id;
    SELECT * INTO v FROM public.response_card_revisions WHERE id=s.card_revision_id;
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
      OR coalesce((citation_results->>q.id::text)::boolean,false) IS DISTINCT FROM true
      OR (s.card_id IS NOT NULL AND c.current_revision_id IS DISTINCT FROM s.card_revision_id)
      OR (s.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE requirement_id=q.id))
      OR (i.partition IN ('response','comply_only') AND public.response_quote_current(s.card_revision_id) IS DISTINCT FROM true)
      OR (i.partition='response' AND (v.state IS DISTINCT FROM 'confirmed' OR v.confirmed_at IS NULL))
      OR (i.partition='comply_only' AND v.disposition IS DISTINCT FROM 'comply_only') THEN RETURN false; END IF;
    IF jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array'
      OR jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE revision_id=s.card_revision_id)
      OR (SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(fixed->'evidence') x)<>jsonb_array_length(fixed->'evidence') THEN RETURN false; END IF;
    FOR dep IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
      SELECT * INTO e FROM public.evidence WHERE id=(dep->>'id')::uuid;
      IF e.id IS NULL OR e.task_id IS DISTINCT FROM r.task_id OR e.card_id IS DISTINCT FROM s.card_id
        OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE revision_id=s.card_revision_id AND evidence_id=e.id)
        OR (dep->>'confirmed_at')::timestamptz IS DISTINCT FROM e.confirmed_at
        OR (dep->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(e.id)
        OR (i.partition='response' AND (e.confirmed_at IS NULL OR public.response_evidence_active(e.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
    END LOOP;
    IF i.partition<>'comply_only' AND v.model_job_id IS NOT NULL AND public.response_generation_materials_active(v.model_job_id) IS DISTINCT FROM true
      AND NOT (i.partition='gap' AND fixed->'gap_reasons' ? 'stale_material') THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'certificates');
  IF expected_count<>(SELECT count(*) FROM public.task_certificates WHERE task_id=r.task_id AND active)
    OR expected_count<>(SELECT count(*) FROM public.check_certificates WHERE report_id=p_report)
    OR expected_count<>(SELECT count(DISTINCT x->>'task_certificate_id') FROM jsonb_array_elements(r.input_manifest->'certificates') x) THEN RETURN false; END IF;
  FOR cert IN SELECT value FROM jsonb_array_elements(r.input_manifest->'certificates') LOOP
    IF NOT EXISTS(SELECT 1 FROM public.check_certificates a JOIN public.task_certificates b ON b.id=a.task_certificate_id
      JOIN public.certificate_revisions certificate_revision ON certificate_revision.id=b.certificate_revision_id
      WHERE a.report_id=p_report AND a.task_certificate_id=(cert->>'task_certificate_id')::uuid
        AND a.certificate_revision_id=(cert->>'certificate_revision_id')::uuid AND b.task_id=r.task_id AND b.active
        AND a.date_status=cert->>'date_status' AND a.assessment_date=r.assessment_date
        AND certificate_revision.data->>'valid_from' IS NOT DISTINCT FROM cert->>'valid_from'
        AND certificate_revision.data->>'valid_until' IS NOT DISTINCT FROM cert->>'valid_until') THEN RETURN false; END IF;
    SELECT count(*) INTO actual_count FROM public.check_certificate_items a JOIN public.check_certificates b
      ON b.id=a.certificate_id WHERE a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid;
    IF jsonb_typeof(cert->'requirement_ids') IS DISTINCT FROM 'array'
      OR actual_count<>(SELECT count(DISTINCT i_alias.id) FROM public.check_items i_alias
        JOIN public.response_items s_alias ON s_alias.id=i_alias.response_item_id
        JOIN public.card_evidence_links l ON l.revision_id=s_alias.card_revision_id
        JOIN public.evidence e_alias ON e_alias.id=l.evidence_id
        WHERE i_alias.report_id=p_report AND s_alias.kind='row' AND e_alias.confirmed_at IS NOT NULL
          AND e_alias.task_certificate_id=(cert->>'task_certificate_id')::uuid
          AND e_alias.certificate_revision_id=(cert->>'certificate_revision_id')::uuid)
      OR actual_count<>jsonb_array_length(cert->'requirement_ids')
      OR actual_count<>(SELECT count(DISTINCT x) FROM jsonb_array_elements_text(cert->'requirement_ids') x)
      OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(cert->'requirement_ids') req WHERE NOT EXISTS(
        SELECT 1 FROM public.check_certificate_items a JOIN public.check_certificates b ON b.id=a.certificate_id
        JOIN public.check_items bound_item ON bound_item.id=a.check_item_id
        WHERE a.report_id=p_report AND b.task_certificate_id=(cert->>'task_certificate_id')::uuid AND bound_item.requirement_id=req::uuid)) THEN RETURN false; END IF;
  END LOOP;
  expected_count := jsonb_array_length(r.input_manifest->'confidential');
  IF expected_count<>(SELECT count(*) FROM public.confidential_fields WHERE NOT archived)
    OR expected_count<>(SELECT count(DISTINCT x->>'field_id') FROM jsonb_array_elements(r.input_manifest->'confidential') x) THEN RETURN false; END IF;
  FOR field IN SELECT value FROM jsonb_array_elements(r.input_manifest->'confidential') LOOP
    SELECT * INTO cf FROM public.confidential_fields WHERE id=(field->>'field_id')::uuid;
    SELECT id INTO value_id FROM public.confidential_values WHERE field_id=cf.id
      AND task_id IS NOT DISTINCT FROM CASE cf.scope WHEN 'task' THEN r.task_id ELSE NULL END ORDER BY version DESC LIMIT 1;
    IF cf.id IS NULL OR cf.archived OR cf.revision IS DISTINCT FROM (field->>'field_revision')::integer
      OR value_id IS DISTINCT FROM (field->>'value_id')::uuid THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $$;

-- FUNCTION: requirement_prior_rubric_complete(uuid)
CREATE FUNCTION public.requirement_prior_rubric_complete(p_rubric uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE required_ids uuid[];
BEGIN
  IF public.rubric_shape_complete(p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  SELECT ARRAY(SELECT (entry->>'requirement_id')::uuid
    FROM jsonb_array_elements(r.input_manifest->'requirements') entry)
    INTO required_ids FROM public.score_rubric_sets r WHERE r.id=p_rubric;
  IF required_ids IS NULL THEN RETURN false; END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(required_ids) verified
    WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

-- FUNCTION: requirement_prior_rubric_current_inputs(uuid)
CREATE FUNCTION public.requirement_prior_rubric_current_inputs(p_rubric uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE required_ids uuid[];
BEGIN
  IF public.rubric_input_shapes_current(p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  SELECT ARRAY(SELECT (entry->>'requirement_id')::uuid
    FROM jsonb_array_elements(r.input_manifest->'requirements') entry)
    INTO required_ids FROM public.score_rubric_sets r WHERE r.id=p_rubric;
  IF required_ids IS NULL THEN RETURN false; END IF;
  IF public.rubric_section_bindings_current(p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
  RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(required_ids) verified
    WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

-- FUNCTION: requirement_prior_score_inputs_current(uuid, uuid, integer)
CREATE FUNCTION public.requirement_prior_score_inputs_current(p_draft uuid, p_rubric uuid, p_revision integer) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
 response_row public.response_items%ROWTYPE; requirement_row public.requirements%ROWTYPE;
 card_row record; revision_row record;
 evidence_row public.evidence%ROWTYPE; fixed jsonb; dependency jsonb; source_value jsonb;
 citation_results jsonb; required_ids uuid[];
BEGIN
 SELECT * INTO draft_row FROM public.draft_runs WHERE id=p_draft;
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE id=p_rubric;
 IF draft_row.id IS NULL OR rubric_row.id IS NULL
  OR draft_row.task_id IS DISTINCT FROM rubric_row.task_id
  OR draft_row.extraction_job_id IS DISTINCT FROM rubric_row.extraction_job_id
  OR p_revision IS DISTINCT FROM public.rubric_revision(p_rubric)
  OR public.rubric_shape_complete(p_rubric) IS DISTINCT FROM true
  OR (SELECT action FROM public.score_rubric_decisions WHERE rubric_id=p_rubric
      AND section_id IS NULL AND item_id IS NULL ORDER BY revision DESC LIMIT 1) IS DISTINCT FROM 'confirm'
  OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE prior_rubric_id=p_rubric)
  OR jsonb_typeof(draft_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF (SELECT count(*) FROM public.response_items WHERE draft_id=p_draft)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint
  OR (SELECT count(*) FROM public.requirements WHERE job_id=draft_row.extraction_job_id)
   IS DISTINCT FROM jsonb_array_length(draft_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 SELECT ARRAY(
   SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(draft_row.input_manifest->'requirements') entry
   UNION
   SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(rubric_row.input_manifest->'requirements') entry)
   INTO required_ids;
 SELECT jsonb_object_agg(verified.requirement_id::text,verified.is_valid) INTO citation_results
   FROM public.response_requirement_citations_valid(required_ids) verified;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(rubric_row.input_manifest->'requirements') entry
   WHERE coalesce((citation_results->>(entry->>'requirement_id'))::boolean,false) IS DISTINCT FROM true) THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(draft_row.input_manifest->'requirements') LOOP
  SELECT * INTO response_row FROM public.response_items WHERE draft_id=p_draft AND requirement_id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO requirement_row FROM public.requirements WHERE id=response_row.requirement_id;
  SELECT id,revision INTO card_row FROM public.response_cards WHERE id=response_row.card_id;
  SELECT id,revision,state,confirmed_at,disposition,model_job_id INTO revision_row FROM public.response_card_revisions WHERE id=response_row.card_revision_id;
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
   OR (response_row.card_id IS NULL AND EXISTS(SELECT 1 FROM public.response_cards WHERE requirement_id=requirement_row.id))
   OR (coalesce((citation_results->>requirement_row.id::text)::boolean,false) IS DISTINCT FROM true AND NOT (response_row.kind='gap' AND response_row.gap_reasons ? 'invalid_citation'))
   OR (response_row.kind IN ('row','comply_only') AND public.response_quote_current(revision_row.id) IS DISTINCT FROM true)
   OR (response_row.kind='row' AND (revision_row.state IS DISTINCT FROM 'confirmed' OR revision_row.confirmed_at IS NULL))
   OR (response_row.kind='comply_only' AND revision_row.disposition IS DISTINCT FROM 'comply_only')
   OR jsonb_typeof(fixed->'evidence') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF jsonb_array_length(fixed->'evidence')<>(SELECT count(*) FROM public.card_evidence_links WHERE revision_id=revision_row.id) THEN RETURN false; END IF;
  FOR dependency IN SELECT value FROM jsonb_array_elements(fixed->'evidence') LOOP
   SELECT * INTO evidence_row FROM public.evidence WHERE id=(dependency->>'id')::uuid;
   IF evidence_row.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.card_evidence_links WHERE revision_id=revision_row.id AND evidence_id=evidence_row.id)
    OR (dependency->>'confirmed_at')::timestamptz IS DISTINCT FROM evidence_row.confirmed_at
    OR (dependency->>'active')::boolean IS DISTINCT FROM public.response_evidence_active(evidence_row.id)
    OR (response_row.kind='row' AND (evidence_row.confirmed_at IS NULL OR public.response_evidence_active(evidence_row.id) IS DISTINCT FROM true)) THEN RETURN false; END IF;
  END LOOP;
  IF revision_row.model_job_id IS NOT NULL AND response_row.kind<>'comply_only'
   AND public.response_generation_materials_active(revision_row.model_job_id) IS DISTINCT FROM true
   AND NOT(response_row.kind='gap' AND response_row.gap_reasons ? 'stale_material') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

-- FUNCTION: requirement_response_gate()
CREATE FUNCTION public.requirement_response_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE rid uuid; previous public.response_card_revisions;
BEGIN
 SELECT * INTO previous FROM public.response_card_revisions WHERE card_id=NEW.card_id AND revision=NEW.revision-1;
 SELECT requirement_id INTO rid FROM public.response_cards WHERE id=NEW.card_id;
 IF ((NEW.state='confirmed' AND previous.state IS DISTINCT FROM 'confirmed') OR (NEW.disposition='comply_only' AND previous.disposition IS DISTINCT FROM 'comply_only')) AND NOT public.requirement_review_current(rid) THEN
 RAISE EXCEPTION 'requirement_unconfirmed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: requirement_review_changed()
CREATE FUNCTION public.requirement_review_changed() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_OP='UPDATE' AND (to_jsonb(NEW)-'snapshot_ciphertext')=(to_jsonb(OLD)-'snapshot_ciphertext') THEN RETURN NULL; END IF;
 UPDATE public.requirement_review_sets s SET revision=revision+CASE WHEN TG_OP='INSERT' AND NEW.origin='legacy' THEN 0 ELSE 1 END,
 membership_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(r.id ORDER BY r.id),'[]')) FROM public.requirements r WHERE r.job_id=s.extraction_job_id),
 confirmation_sha256=(SELECT public.requirement_digest(coalesce(jsonb_agg(jsonb_build_array(v.requirement_id,v.revision,v.review_hash,v.state) ORDER BY v.requirement_id),'[]')) FROM public.requirement_reviews v WHERE v.extraction_job_id=s.extraction_job_id)
 WHERE s.extraction_job_id=NEW.extraction_job_id;
 PERFORM public.append_task_event(NEW.task_id,'board_changed',jsonb_build_object('type','board_changed',
 'extraction_job_id',NEW.extraction_job_id,'requirement_ids',jsonb_build_array(NEW.requirement_id),'card_ids','[]'::jsonb,'invalidate_all',false),NULL);
 RETURN NULL;
END $$;

-- FUNCTION: requirement_review_current(uuid)
CREATE FUNCTION public.requirement_review_current(p_req uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.requirement_reviews v WHERE v.requirement_id=p_req
 AND v.state='confirmed' AND v.confirmed_at IS NOT NULL
 AND public.requirement_pin_valid(p_req,v.source_pin)
 AND v.review_hash=public.requirement_hash(p_req,v.source_pin))
$$;

-- FUNCTION: requirement_review_fields(uuid)
CREATE FUNCTION public.requirement_review_fields(p_req uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT jsonb_build_object('policy_version','requirement-review-v1','revision',coalesce(v.revision,1),
 'review_hash',public.requirement_hash(p_req,public.requirement_current_pin(p_req)),
 'state',public.requirement_review_state(p_req)) FROM public.requirements r LEFT JOIN public.requirement_reviews v
 ON v.requirement_id=r.id WHERE r.id=p_req
$$;

-- FUNCTION: requirement_review_guard()
CREATE FUNCTION public.requirement_review_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE r public.requirements; j public.jobs;
 e public.requirement_review_events; previous public.requirement_review_events;
BEGIN
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
END $$;

-- FUNCTION: requirement_review_state(uuid)
CREATE FUNCTION public.requirement_review_state(p_req uuid) RETURNS text
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce((SELECT CASE WHEN state='confirmed' AND NOT public.requirement_review_current(p_req)
 THEN 'invalidated' ELSE state END FROM public.requirement_reviews WHERE requirement_id=p_req),'legacy_unconfirmed')
$$;

-- FUNCTION: requirement_rubric_confirmed(uuid)
CREATE FUNCTION public.requirement_rubric_confirmed(p_rubric uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE rubric public.score_rubric_sets;
BEGIN
 SELECT * INTO rubric FROM public.score_rubric_sets WHERE id=p_rubric;
 IF rubric.id IS NULL OR jsonb_typeof(rubric.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(rubric.input_manifest->'requirements') NOT BETWEEN 1 AND 2000
  OR public.rubric_section_bindings_current(p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
 -- 0046 checks immutable subquote bindings without locating again. New section
 -- sources may include several fixed requirements; deduplicate all references
 -- here while retaining ignored/duplicate coverage requirements in the gate.
 RETURN NOT EXISTS(WITH required AS MATERIALIZED (
  SELECT (entry->>'requirement_id')::uuid AS id
   FROM jsonb_array_elements(rubric.input_manifest->'requirements') entry
  UNION
  SELECT (entry->>'requirement_id')::uuid AS id
   FROM public.score_rubric_sections section,
    LATERAL jsonb_array_elements(public.rubric_section_sources(section)) entry
   WHERE section.rubric_id=p_rubric
 ) SELECT 1 FROM required LEFT JOIN public.requirements req ON req.id=required.id
  WHERE req.id IS NULL OR req.task_id IS DISTINCT FROM rubric.task_id
   OR req.document_id IS DISTINCT FROM rubric.document_id OR req.job_id IS DISTINCT FROM rubric.extraction_job_id
   OR req.category IS DISTINCT FROM 'scoring'
   OR public.requirement_review_current(required.id) IS DISTINCT FROM true);
EXCEPTION WHEN invalid_text_representation THEN RETURN false;
END $$;

-- FUNCTION: requirement_rubric_preparation_current(uuid)
CREATE FUNCTION public.requirement_rubric_preparation_current(p_rubric uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.score_rubric_sets s WHERE s.id=p_rubric
 AND s.input_manifest->'requirement_preparation'=jsonb_build_object('policy_version','requirement-review-v1',
 'entries',(SELECT coalesce(jsonb_agg(jsonb_build_object('requirement_id',r.id,
 'review_hash',public.requirement_hash(r.id,public.requirement_current_pin(r.id))) ORDER BY r.id),'[]')
 FROM public.requirements r WHERE r.task_id=s.task_id AND r.job_id=s.extraction_job_id AND r.category='scoring')))
$$;

-- FUNCTION: requirement_seed_authority(uuid, uuid, boolean)
CREATE FUNCTION public.requirement_seed_authority(p_task uuid, p_job uuid, p_manual boolean) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE j public.jobs;
BEGIN
 IF p_manual THEN PERFORM public.requirement_human_authority(p_task,'req:manual'); RETURN; END IF;
 IF public.actor_is_human() THEN
 PERFORM public.requirement_human_authority(p_task,'req:confirm'); RETURN; END IF;
 SELECT * INTO j FROM public.jobs WHERE id=p_job AND task_id=p_task AND kind='extract';
 IF current_setting('app.actor_kind',true) IS DISTINCT FROM 'worker'
 OR j.id IS DISTINCT FROM nullif(current_setting('app.execution_job_id',true),'')::uuid
 OR j.run_id IS DISTINCT FROM nullif(current_setting('app.execution_run_id',true),'')::uuid
 OR j.run_id IS NULL OR j.status NOT IN ('running','succeeded')
 THEN RAISE EXCEPTION 'extract publication fence required' USING ERRCODE='42501'; END IF;
 PERFORM public.task_write_authority(p_task,'req:extract',NULL,false,false);
END $$;

-- FUNCTION: requirement_source_invalidate()
CREATE FUNCTION public.requirement_source_invalidate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE v public.requirement_reviews; prior public.requirement_review_events; eid uuid; next_state text; next_pin jsonb;
BEGIN
 IF TG_TABLE_NAME='requirements' THEN
 IF ROW(NEW.text,NEW.category,NEW.starred,NEW.condition,NEW.document_id,NEW.chunk_id,NEW.page,NEW.location,NEW.quote)
 IS NOT DISTINCT FROM ROW(OLD.text,OLD.category,OLD.starred,OLD.condition,OLD.document_id,OLD.chunk_id,OLD.page,OLD.location,OLD.quote) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='chunks' THEN
 IF ROW(NEW.text,NEW.blocks,NEW.seq,NEW.page,NEW.citation_verified,NEW.document_id)
 IS NOT DISTINCT FROM ROW(OLD.text,OLD.blocks,OLD.seq,OLD.page,OLD.citation_verified,OLD.document_id) THEN RETURN NEW; END IF;
 ELSIF TG_TABLE_NAME='documents' THEN
 IF NEW.sha256 IS NOT DISTINCT FROM OLD.sha256 THEN RETURN NEW; END IF;
 END IF;
 FOR v IN SELECT rv.* FROM public.requirement_reviews rv JOIN public.requirements r ON r.id=rv.requirement_id
 WHERE (CASE TG_TABLE_NAME WHEN 'requirements' THEN r.id=NEW.id WHEN 'chunks' THEN r.chunk_id=NEW.id ELSE r.document_id=NEW.id END)
 ORDER BY rv.task_id,rv.extraction_job_id,rv.requirement_id FOR UPDATE OF rv LOOP
  PERFORM 1 FROM public.tasks WHERE id=v.task_id FOR UPDATE;
  PERFORM 1 FROM public.requirement_review_sets WHERE extraction_job_id=v.extraction_job_id FOR UPDATE;
  SELECT * INTO prior FROM public.requirement_review_events WHERE id=v.current_event_id;
  eid:=gen_random_uuid(); next_state:=CASE WHEN v.state IN ('confirmed','invalidated') THEN 'invalidated' ELSE v.state END;
  next_pin:=public.requirement_current_pin(v.requirement_id);
  UPDATE public.requirement_reviews SET revision=v.revision+1,current_event_id=eid,state=next_state,
   review_hash=public.requirement_hash(v.requirement_id,next_pin),source_pin=next_pin,
   confirmed_at=NULL WHERE id=v.id;
  INSERT INTO public.requirement_review_events(id,task_id,extraction_job_id,requirement_id,review_id,revision,
   action,state_after,review_hash,snapshot_sha256,snapshot_ciphertext,source_pin,actor_kind)
  VALUES(eid,v.task_id,v.extraction_job_id,v.requirement_id,v.id,v.revision+1,
   CASE WHEN TG_TABLE_NAME='requirements' AND to_jsonb(NEW)->>'quote' IS DISTINCT FROM to_jsonb(OLD)->>'quote' THEN 'source_repair' ELSE 'invalidate' END,
   next_state,prior.review_hash,prior.snapshot_sha256,prior.snapshot_ciphertext,prior.source_pin,'worker');
 END LOOP;
 RETURN NEW;
END $$;

-- FUNCTION: requirement_source_lock()
CREATE FUNCTION public.requirement_source_lock() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_candidate_range_valid(jsonb)
CREATE FUNCTION public.rubric_candidate_range_valid(bounds jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE minimum_value numeric; maximum_value numeric;
BEGIN
 IF bounds IS NULL THEN RETURN true; END IF;
 IF jsonb_typeof(bounds) IS DISTINCT FROM 'object'
  OR (bounds-'minimum'-'maximum')<>'{}'::jsonb
  OR coalesce(jsonb_typeof(bounds->'minimum'),'null') NOT IN ('number','string')
  OR coalesce(jsonb_typeof(bounds->'maximum'),'null') NOT IN ('number','string') THEN RETURN false; END IF;
 BEGIN
  minimum_value:=(bounds->>'minimum')::numeric;
  maximum_value:=(bounds->>'maximum')::numeric;
 EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
 END;
 RETURN minimum_value NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND maximum_value NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND abs(minimum_value)<=9999999999.99999999 AND abs(maximum_value)<=9999999999.99999999
  AND scale(trim_scale(minimum_value))<=8 AND scale(trim_scale(maximum_value))<=8;
END $$;

-- FUNCTION: rubric_canonical_json(jsonb)
CREATE FUNCTION public.rubric_canonical_json(p_value jsonb) RETURNS text
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT CASE jsonb_typeof(p_value)
 WHEN 'object' THEN (SELECT '{'||coalesce(string_agg(to_jsonb(entry.key)::text||':'||public.rubric_canonical_json(entry.value),',' ORDER BY entry.key COLLATE "C"),'')||'}' FROM jsonb_each(p_value) entry)
 WHEN 'array' THEN (SELECT '['||coalesce(string_agg(public.rubric_canonical_json(entry.value),',' ORDER BY entry.position),'')||']' FROM jsonb_array_elements(p_value) WITH ORDINALITY entry(value,position))
 ELSE p_value::text END
$$;

-- FUNCTION: rubric_canonical_mapped(uuid, uuid)
CREATE FUNCTION public.rubric_canonical_mapped(p_rubric uuid, p_requirement uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE visited uuid[]:=ARRAY[]::uuid[]; current_requirement uuid:=p_requirement; latest_row public.score_rubric_coverage_decisions%ROWTYPE;
BEGIN
 LOOP
  IF current_requirement=ANY(visited) THEN RETURN false; END IF;
  visited:=array_append(visited,current_requirement);
  SELECT d.* INTO latest_row FROM public.score_rubric_coverage_decisions d JOIN public.score_rubric_coverage c ON c.id=d.coverage_id
   WHERE c.rubric_id=p_rubric AND c.requirement_id=current_requirement ORDER BY d.revision DESC LIMIT 1;
  IF NOT FOUND THEN RETURN false; END IF;
  IF latest_row.action='mapped' THEN RETURN true; END IF;
  IF latest_row.action<>'duplicate' THEN RETURN false; END IF;
  current_requirement:=latest_row.canonical_requirement_id;
 END LOOP;
END $$;

-- FUNCTION: rubric_child_gate()
CREATE FUNCTION public.rubric_child_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; req_row public.requirements%ROWTYPE; current_xmin text;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 SELECT xmin::text INTO current_xmin FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 IF current_xmin IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Rubric snapshots cannot be extended' USING ERRCODE='42501'; END IF;
 PERFORM public.response_check_actor(set_row.actor_token_id,set_row.actor_kind);
 SELECT * INTO req_row FROM public.requirements WHERE id=NEW.requirement_id;
 IF req_row.job_id IS DISTINCT FROM set_row.extraction_job_id OR req_row.category IS DISTINCT FROM 'scoring'
  OR NEW.source->>'document_id' IS DISTINCT FROM req_row.document_id::text OR NEW.source->>'chunk_id' IS DISTINCT FROM req_row.chunk_id::text
  OR NEW.source->>'quote' IS DISTINCT FROM req_row.quote
  OR NEW.source->>'page' IS DISTINCT FROM req_row.page::text
  OR coalesce(NEW.source->'location','null'::jsonb) IS DISTINCT FROM coalesce(req_row.location,'null'::jsonb) THEN
  RAISE EXCEPTION 'Rubric source must bind fixed scoring requirement' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_complete(uuid)
CREATE FUNCTION public.rubric_complete(p_rubric uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.requirement_rubric_preparation_current(p_rubric) AND public.requirement_rubric_confirmed(p_rubric) AND public.requirement_prior_rubric_complete(p_rubric)
$$;

-- FUNCTION: rubric_coverage_item_gate()
CREATE FUNCTION public.rubric_coverage_item_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE decision_row public.score_rubric_coverage_decisions%ROWTYPE; decision_xmin text;
BEGIN
 SELECT * INTO decision_row FROM public.score_rubric_coverage_decisions WHERE id=NEW.coverage_decision_id;
 SELECT xmin::text INTO decision_xmin FROM public.score_rubric_coverage_decisions WHERE id=NEW.coverage_decision_id;
 PERFORM public.response_check_actor(NULL,'session');
 PERFORM public.response_require_human();
 IF decision_row.action<>'mapped' OR decision_xmin IS DISTINCT FROM pg_current_xact_id()::text OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE id=NEW.rubric_item_id AND requirement_id=decision_row.requirement_id) THEN
 RAISE EXCEPTION 'Mapped coverage must bind its fixed requirement items' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_current_inputs(uuid)
CREATE FUNCTION public.rubric_current_inputs(p_rubric uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.requirement_rubric_preparation_current(p_rubric) AND public.requirement_prior_rubric_current_inputs(p_rubric)
$$;

-- FUNCTION: rubric_immutable_gate()
CREATE FUNCTION public.rubric_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_TABLE_NAME='score_rubric_sets' AND TG_OP='UPDATE'
  AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
  AND (to_jsonb(NEW)-'encrypted_input')=(to_jsonb(OLD)-'encrypted_input') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Rubric history is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: rubric_input_shapes_current(uuid)
CREATE FUNCTION public.rubric_input_shapes_current(p_rubric uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE fixed jsonb; set_row public.score_rubric_sets%ROWTYPE; req_row public.requirements%ROWTYPE; chunk_row public.chunks%ROWTYPE; source_value jsonb;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=p_rubric;
 IF NOT FOUND OR jsonb_typeof(set_row.input_manifest->'requirements') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(set_row.input_manifest->'requirements') NOT BETWEEN 1 AND 2000
  OR (SELECT count(DISTINCT entry->>'requirement_id') FROM jsonb_array_elements(set_row.input_manifest->'requirements') entry) IS DISTINCT FROM jsonb_array_length(set_row.input_manifest->'requirements')::bigint THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=set_row.extraction_job_id AND task_id=set_row.task_id AND document_id=set_row.document_id AND kind='extract' AND status='succeeded')
  OR set_row.input_manifest->>'document_sha256' IS DISTINCT FROM (SELECT sha256 FROM public.documents WHERE id=set_row.document_id AND task_id=set_row.task_id)
  OR jsonb_array_length(set_row.input_manifest->'requirements') IS DISTINCT FROM (SELECT count(*) FROM public.requirements WHERE task_id=set_row.task_id AND job_id=set_row.extraction_job_id AND category='scoring') THEN RETURN false; END IF;
 FOR fixed IN SELECT value FROM jsonb_array_elements(set_row.input_manifest->'requirements') LOOP
  SELECT * INTO req_row FROM public.requirements WHERE id=(fixed->>'requirement_id')::uuid;
  SELECT * INTO chunk_row FROM public.chunks WHERE id=(fixed->>'chunk_id')::uuid;
  source_value:=jsonb_build_object('document_id',req_row.document_id,'chunk_id',req_row.chunk_id,'page',req_row.page,'location',req_row.location,'quote',req_row.quote);
  IF req_row.id IS NULL OR chunk_row.id IS NULL OR req_row.task_id IS DISTINCT FROM set_row.task_id OR req_row.job_id IS DISTINCT FROM set_row.extraction_job_id
   OR req_row.category IS DISTINCT FROM 'scoring' OR req_row.document_id IS DISTINCT FROM set_row.document_id OR req_row.chunk_id IS DISTINCT FROM chunk_row.id
   OR chunk_row.task_id IS DISTINCT FROM set_row.task_id OR chunk_row.document_id IS DISTINCT FROM set_row.document_id
   OR fixed->>'source_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(source_value),'UTF8')),'hex')
   OR fixed->>'requirement_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',req_row.text,'category',req_row.category,'starred',req_row.starred)),'UTF8')),'hex')
   OR fixed->>'chunk_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rubric_canonical_json(jsonb_build_object('text',chunk_row.text,'blocks',chunk_row.blocks,'page',chunk_row.page,'citation_verified',chunk_row.citation_verified)),'UTF8')),'hex') THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

-- FUNCTION: rubric_publication_complete()
CREATE FUNCTION public.rubric_publication_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE new_section public.score_rubric_sections%ROWTYPE;
 owning_job public.jobs%ROWTYPE; citation jsonb;
BEGIN
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO owning_job FROM public.jobs WHERE id=NEW.job_id;
  IF owning_job.status NOT IN ('running','succeeded') OR owning_job.run_id IS DISTINCT FROM NEW.run_id OR owning_job.lease_until IS NULL OR owning_job.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'Rubric owning attempt lost before publication' USING ERRCODE='23514'; END IF;
 END IF;
 IF public.rubric_current_inputs(NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric fixed inputs changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_shape_complete(NEW.id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric lost completeness before publication' USING ERRCODE='23514'; END IF;

 -- current_inputs verified every scoring requirement in the fixed manifest.
 -- Coverage must refer to that exact set and retain its current source binding.
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
  LEFT JOIN public.requirements req ON req.id=coverage.requirement_id
  WHERE coverage.rubric_id=NEW.id AND (
   req.id IS NULL OR req.task_id IS DISTINCT FROM NEW.task_id OR req.document_id IS DISTINCT FROM NEW.document_id
   OR req.job_id IS DISTINCT FROM NEW.extraction_job_id OR req.category IS DISTINCT FROM 'scoring'
   OR coverage.source->>'document_id' IS DISTINCT FROM req.document_id::text
   OR coverage.source->>'chunk_id' IS DISTINCT FROM req.chunk_id::text
   OR coverage.source->>'quote' IS DISTINCT FROM req.quote OR coverage.source->>'page' IS DISTINCT FROM req.page::text
   OR coalesce(coverage.source->'location','null'::jsonb) IS DISTINCT FROM coalesce(req.location,'null'::jsonb)
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.input_manifest->'requirements') entry
     WHERE (entry->>'requirement_id')::uuid=req.id))) THEN
  RAISE EXCEPTION 'Rubric source changed before publication' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.requirements req WHERE req.job_id=NEW.extraction_job_id AND req.category='scoring' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage c WHERE c.rubric_id=NEW.id AND c.requirement_id=req.id))
 OR (NEW.prior_rubric_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_revision_events WHERE rubric_id=NEW.id)) THEN
 RAISE EXCEPTION 'Rubric publication lacks complete coverage or revision history' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NOT NULL THEN
  -- A human may select a prior verified quotation or the pinned full Source,
  -- but cannot turn arbitrary substring input into new citation provenance.
  FOR new_section IN SELECT * FROM public.score_rubric_sections WHERE rubric_id=NEW.id LOOP
   FOR citation IN SELECT value FROM jsonb_array_elements(new_section.sources) LOOP
    IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections previous,
      LATERAL jsonb_array_elements(public.rubric_section_sources(previous)) verified
      WHERE previous.rubric_id=NEW.prior_rubric_id AND verified.value=citation)
     AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage coverage
      WHERE coverage.rubric_id=NEW.prior_rubric_id
       AND coverage.requirement_id::text=citation->>'requirement_id' AND coverage.source=citation->'source'
       AND coverage.source->>'quote'=citation->>'quote') THEN
     RAISE EXCEPTION 'Revision cannot invent section citations' USING ERRCODE='23514';
    END IF;
   END LOOP;
  END LOOP;

 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_range_valid(jsonb)
CREATE FUNCTION public.rubric_range_valid(bounds jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT bounds IS NULL OR coalesce(
  (bounds->>'minimum')::numeric NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND (bounds->>'maximum')::numeric NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND (bounds->>'minimum')::numeric>=0 AND (bounds->>'maximum')::numeric>=(bounds->>'minimum')::numeric
  AND (bounds->>'maximum')::numeric<=9999999999.99999999
  AND scale(trim_scale((bounds->>'minimum')::numeric))<=8 AND scale(trim_scale((bounds->>'maximum')::numeric))<=8,false)
$$;

-- FUNCTION: rubric_review_complete()
CREATE FUNCTION public.rubric_review_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF public.rubric_current_inputs(NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric inputs changed during review' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' AND ((to_jsonb(NEW)->>'action'='mapped') IS DISTINCT FROM EXISTS(SELECT 1 FROM public.score_rubric_coverage_items WHERE coverage_decision_id=NEW.id)) THEN
  RAISE EXCEPTION 'Mapped coverage requires normalized item links' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND public.rubric_complete(NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Confirmed rubric must remain complete through commit' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_review_gate()
CREATE FUNCTION public.rubric_review_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; prior_revision integer; subject_section uuid; subject_item uuid; is_set boolean; previous_action text; previous_revision integer;
BEGIN
 PERFORM public.response_check_actor(NULL,'session');
 PERFORM pg_advisory_xact_lock(hashtextextended('rubric.set:'||NEW.rubric_id::text,0));
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 IF public.rubric_current_inputs(NEW.rubric_id) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Rubric fixed inputs changed' USING ERRCODE='23514'; END IF;
 IF NEW.actor_kind<>'session' OR NOT public.actor_is_human() OR NEW.expected_input_hash IS DISTINCT FROM set_row.input_hash OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
  RAISE EXCEPTION 'Invalid rubric human review context' USING ERRCODE='42501'; END IF;
 IF NEW.set_revision<>public.rubric_revision(NEW.rubric_id)+1
 OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE prior_rubric_id=NEW.rubric_id) THEN
  RAISE EXCEPTION 'Rubric revision conflict' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_rubric_coverage_decisions' THEN
  IF NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE id=NEW.coverage_id AND requirement_id=NEW.requirement_id) THEN RAISE EXCEPTION 'Coverage requires bidder and fixed requirement' USING ERRCODE='42501'; END IF;
  SELECT coalesce(max(revision),1) INTO prior_revision FROM public.score_rubric_coverage_decisions WHERE coverage_id=NEW.coverage_id;
  IF NEW.canonical_requirement_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage WHERE rubric_id=NEW.rubric_id AND requirement_id=NEW.canonical_requirement_id) THEN RAISE EXCEPTION 'Invalid canonical requirement' USING ERRCODE='23514'; END IF;
 ELSE
  subject_section:=NEW.section_id;subject_item:=NEW.item_id;
  is_set:=subject_section IS NULL AND subject_item IS NULL;
  prior_revision:=public.rubric_revision(NEW.rubric_id,subject_section,subject_item);
  IF TG_TABLE_NAME='score_rubric_decisions' THEN
   IF is_set THEN
   IF NEW.action NOT IN ('confirm','reopen') THEN RAISE EXCEPTION 'Set review requires bidder' USING ERRCODE='42501'; END IF;
   IF NEW.action='confirm' AND NOT public.rubric_complete(NEW.rubric_id) THEN RAISE EXCEPTION 'Rubric completeness gate failed' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.action='confirm' AND ((subject_section IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE id=subject_section AND citation_valid))
    OR (subject_item IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE id=subject_item AND citation_valid))) THEN
    RAISE EXCEPTION 'Unresolved citation cannot be confirmed' USING ERRCODE='23514'; END IF;
  END IF;
   SELECT action,revision INTO previous_action,previous_revision FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id
    AND section_id IS NOT DISTINCT FROM subject_section AND item_id IS NOT DISTINCT FROM subject_item ORDER BY revision DESC LIMIT 1;
   IF NOT is_set AND previous_revision IS DISTINCT FROM prior_revision THEN previous_action:=NULL; END IF;
   IF (NEW.action='reopen' AND coalesce(previous_action,'reopen')='reopen') OR (NEW.action<>'reopen' AND coalesce(previous_action,'reopen')<>'reopen') THEN
    RAISE EXCEPTION 'Invalid rubric review transition' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 IF NEW.revision<>prior_revision+1 THEN RAISE EXCEPTION 'Subject revision conflict' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL AND action='confirm'
  AND revision=(SELECT max(revision) FROM public.score_rubric_decisions WHERE rubric_id=NEW.rubric_id AND section_id IS NULL AND item_id IS NULL))
  AND NOT (TG_TABLE_NAME='score_rubric_decisions' AND subject_section IS NULL AND subject_item IS NULL AND to_jsonb(NEW)->>'action'='reopen') THEN
  RAISE EXCEPTION 'Reopen set before changing human review' USING ERRCODE='23514'; END IF;
 NEW.decided_at:=clock_timestamp(); RETURN NEW;
END $$;

-- FUNCTION: rubric_revision(uuid, uuid, uuid)
CREATE FUNCTION public.rubric_revision(p_rubric uuid, p_section uuid DEFAULT NULL::uuid, p_item uuid DEFAULT NULL::uuid) RETURNS integer
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce(max(n),1) FROM (
 SELECT (CASE WHEN p_section IS NULL AND p_item IS NULL THEN set_revision ELSE revision END) n FROM public.score_rubric_decisions
 WHERE rubric_id=p_rubric AND ((p_section IS NULL AND p_item IS NULL) OR (section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item))
 UNION ALL SELECT (CASE WHEN p_section IS NULL AND p_item IS NULL THEN set_revision ELSE revision END) FROM public.score_rubric_classifications
 WHERE rubric_id=p_rubric AND ((p_section IS NULL AND p_item IS NULL) OR (section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item))
 UNION ALL SELECT set_revision FROM public.score_rubric_coverage_decisions WHERE rubric_id=p_rubric AND p_section IS NULL AND p_item IS NULL
 ) revisions
$$;

-- FUNCTION: rubric_revision_event_gate()
CREATE FUNCTION public.rubric_revision_event_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE new_set public.score_rubric_sets%ROWTYPE; old_set public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NULL,'session');
 SELECT * INTO new_set FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 SELECT * INTO old_set FROM public.score_rubric_sets WHERE id=NEW.prior_rubric_id;
 IF NEW.actor_kind<>'session' OR NOT public.actor_is_human() OR NEW.prior_rubric_id IS DISTINCT FROM new_set.prior_rubric_id
 OR NEW.snapshot_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.replacement_snapshot::text,'UTF8')),'hex')
 OR NEW.replacement_snapshot->>'expected_input_hash' IS DISTINCT FROM NEW.expected_input_hash
 OR (NEW.replacement_snapshot->>'expected_revision')::integer IS DISTINCT FROM NEW.expected_revision
 OR NEW.version<>new_set.version OR NEW.input_hash IS DISTINCT FROM new_set.input_hash OR NEW.expected_input_hash IS DISTINCT FROM old_set.input_hash
 OR NEW.expected_revision<>public.rubric_revision(NEW.prior_rubric_id) OR NEW.reason_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.reason,'UTF8')),'hex') THEN
 RAISE EXCEPTION 'Invalid rubric revision history' USING ERRCODE='23514'; END IF;
 NEW.revised_at:=clock_timestamp();RETURN NEW;
END $$;

-- FUNCTION: rubric_section_bindings_current(uuid)
CREATE FUNCTION public.rubric_section_bindings_current(p_rubric uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE id=p_rubric)
 AND NOT EXISTS(SELECT 1 FROM public.score_rubric_sections section
  WHERE section.rubric_id=p_rubric
   AND (section.requirement_id::text IS DISTINCT FROM public.rubric_section_sources(section)->0->>'requirement_id'
    OR section.source IS DISTINCT FROM public.rubric_section_sources(section)->0->'source'
    OR public.rubric_section_source_bindings_valid(p_rubric,public.rubric_section_sources(section)) IS DISTINCT FROM true));
$$;

-- FUNCTION: rubric_section_source_bindings_valid(uuid, jsonb)
CREATE FUNCTION public.rubric_section_source_bindings_valid(p_rubric uuid, p_sources jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; req public.requirements%ROWTYPE;
 entry jsonb; expected_source jsonb;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=p_rubric;
 IF set_row.id IS NULL OR jsonb_typeof(p_sources) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 IF jsonb_array_length(p_sources)=0 THEN RETURN false; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p_sources) LOOP
  IF jsonb_typeof(entry) IS DISTINCT FROM 'object'
   OR NOT entry ?& ARRAY['requirement_id','source','quote']
   OR entry-ARRAY['requirement_id','source','quote'] <> '{}'::jsonb
   OR jsonb_typeof(entry->'requirement_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(entry->'quote') IS DISTINCT FROM 'string'
   OR entry->>'quote' !~ '[^[:space:]]' THEN RETURN false; END IF;
  SELECT * INTO req FROM public.requirements WHERE id=(entry->>'requirement_id')::uuid;
  IF req.id IS NULL OR req.task_id IS DISTINCT FROM set_row.task_id
   OR req.document_id IS DISTINCT FROM set_row.document_id OR req.job_id IS DISTINCT FROM set_row.extraction_job_id
   OR req.category IS DISTINCT FROM 'scoring'
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(set_row.input_manifest->'requirements') fixed
    WHERE fixed->>'requirement_id'=req.id::text) THEN RETURN false; END IF;
  expected_source:=jsonb_build_object('document_id',req.document_id,'chunk_id',req.chunk_id,
   'page',req.page,'location',req.location,'quote',req.quote);
  IF entry->'source' IS DISTINCT FROM expected_source
   OR position(entry->>'quote' in req.quote)=0
   OR position(entry->>'quote' in substring(req.quote from position(entry->>'quote' in req.quote)+1))>0
   THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
EXCEPTION WHEN invalid_text_representation THEN RETURN false;
END $$;

-- FUNCTION: rubric_section_sources_gate()
CREATE FUNCTION public.rubric_section_sources_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 -- NULL is retained only for pre-migration rows. All new snapshots are explicit.
 IF NEW.sources IS NULL
  OR NEW.sources->0->>'requirement_id' IS DISTINCT FROM NEW.requirement_id::text
  OR NEW.sources->0->'source' IS DISTINCT FROM NEW.source
  OR public.rubric_section_sources_valid(NEW.rubric_id,NEW.sources) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Invalid verified section sources' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_section_sources_valid(uuid, jsonb)
CREATE FUNCTION public.rubric_section_sources_valid(p_rubric uuid, p_sources jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF public.rubric_section_source_bindings_valid(p_rubric,p_sources) IS DISTINCT FROM true THEN RETURN false; END IF;
 -- New citations still require normalized span verification. Publication checks
 -- their immutable bindings, then verifies all pinned Sources in one shared batch.
 IF EXISTS(WITH citations AS MATERIALIZED (
   SELECT DISTINCT req.quote COLLATE "C" AS source_text,(entry->>'quote') COLLATE "C" AS quote
   FROM jsonb_array_elements(p_sources) entry
   JOIN public.requirements req ON req.id=(entry->>'requirement_id')::uuid
  ) SELECT 1 FROM citations WHERE public.response_locate_quote(citations.source_text,citations.quote)
   IS DISTINCT FROM citations.quote) THEN RETURN false; END IF;
 RETURN NOT EXISTS(SELECT 1 FROM public.response_requirement_citations_valid(
  ARRAY(SELECT (entry->>'requirement_id')::uuid FROM jsonb_array_elements(p_sources) entry)) verified
  WHERE verified.is_valid IS DISTINCT FROM true);
END $$;

-- FUNCTION: rubric_set_gate()
CREATE FUNCTION public.rubric_set_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE job_row public.jobs%ROWTYPE; prior_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.jobs WHERE id=NEW.extraction_job_id AND task_id=NEW.task_id AND document_id=NEW.document_id AND kind='extract' AND status='succeeded') THEN
  RAISE EXCEPTION 'Rubric requires successful fixed extraction' USING ERRCODE='23514'; END IF;
 IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM public.score_rubric_sets WHERE extraction_job_id=NEW.extraction_job_id) THEN
  RAISE EXCEPTION 'Rubric version conflict' USING ERRCODE='23514'; END IF;
 IF NEW.input_manifest->>'task_id' IS DISTINCT FROM NEW.task_id::text
  OR NEW.input_manifest->>'document_id' IS DISTINCT FROM NEW.document_id::text OR NEW.input_manifest->>'extraction_job_id' IS DISTINCT FROM NEW.extraction_job_id::text THEN
  RAISE EXCEPTION 'Rubric input binding mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.prior_rubric_id IS NULL THEN
  SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
  IF NEW.actor_kind<>'worker' OR job_row.kind IS DISTINCT FROM 'score_rubric' OR job_row.status IS DISTINCT FROM 'running'
   OR job_row.run_id IS DISTINCT FROM NEW.run_id OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
   OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
   OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
   OR job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input
   OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE id=NEW.actor_token_id AND scopes ? 'score:rubric:generate')) THEN
   RAISE EXCEPTION 'Rubric publication requires owning live worker attempt' USING ERRCODE='42501'; END IF;
 ELSE
  PERFORM pg_advisory_xact_lock(hashtextextended('rubric.set:'||NEW.prior_rubric_id::text,0));
  SELECT * INTO prior_row FROM public.score_rubric_sets WHERE id=NEW.prior_rubric_id;
  IF NEW.actor_kind<>'session' OR NOT public.actor_is_human()
   OR NEW.input_hash IS DISTINCT FROM prior_row.input_hash OR NEW.input_manifest IS DISTINCT FROM prior_row.input_manifest
   OR NEW.encrypted_input IS DISTINCT FROM prior_row.encrypted_input OR NEW.extraction_job_id IS DISTINCT FROM prior_row.extraction_job_id
   OR NEW.job_id IS DISTINCT FROM prior_row.job_id OR NEW.run_id IS DISTINCT FROM prior_row.run_id
   OR EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE prior_rubric_id=NEW.prior_rubric_id) THEN
   RAISE EXCEPTION 'Invalid human rubric revision' USING ERRCODE='42501'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_shape_complete(uuid)
CREATE FUNCTION public.rubric_shape_complete(p_rubric uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE set_row public.score_rubric_sets%ROWTYPE; section_row public.score_rubric_sections%ROWTYPE;
 low_value numeric; high_value numeric; total_low numeric:=0; total_high numeric:=0; weight_total numeric; child_count integer;
 all_bounds boolean:=true; section_bounds boolean;
BEGIN
 SELECT * INTO set_row FROM public.score_rubric_sets WHERE id=p_rubric;
 IF NOT FOUND OR NOT public.rubric_range_valid(set_row.overall_score_range) OR set_row.normalization_errors<>'[]'::jsonb THEN RETURN false; END IF;
 IF (set_row.overall_aggregation='capped_sum')<>(set_row.overall_cap IS NOT NULL)
  OR set_row.overall_cap<0 THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_sections s WHERE s.rubric_id=p_rubric
  AND NOT public.rubric_subject_values_valid(p_rubric,s.id,NULL))
 OR EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.rubric_id=p_rubric
  AND NOT public.rubric_subject_values_valid(p_rubric,NULL,i.id)) THEN RETURN false; END IF;
 IF public.rubric_input_shapes_current(p_rubric) IS DISTINCT FROM true
  OR public.rubric_section_bindings_current(p_rubric) IS DISTINCT FROM true THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE rubric_id=p_rubric)
 OR NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE rubric_id=p_rubric) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE rubric_id=p_rubric GROUP BY section_id,"order" HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE rubric_id=p_rubric GROUP BY "order" HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items WHERE rubric_id=p_rubric
  GROUP BY normalize(regexp_replace(btrim(rule_text),'\s+',' ','g'),NFKC),assessment_mode,
   (score_range->>'minimum')::numeric,(score_range->>'maximum')::numeric,weight,
   normalize(regexp_replace(btrim(ambiguity_reason),'\s+',' ','g'),NFKC),source HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE rubric_id=p_rubric GROUP BY fingerprint HAVING count(*)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.rubric_id=p_rubric
  AND (NOT i.citation_valid OR NOT public.rubric_subject_confirmed(p_rubric,NULL,i.id)
   OR NOT public.rubric_range_valid(i.score_range) OR (i.assessment_mode='model_assessable' AND i.score_range IS NULL)
   OR (i.assessment_mode<>'model_assessable' AND NOT coalesce(i.ambiguity_reason ~ '[^[:space:]]',false)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_coverage c LEFT JOIN LATERAL (
   SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   WHERE c.rubric_id=p_rubric AND (latest.id IS NULL OR latest.action='reopen'
    OR (latest.action='mapped' AND NOT EXISTS(SELECT 1 FROM public.score_rubric_coverage_items ci WHERE ci.coverage_decision_id=latest.id))
    OR (latest.action='duplicate' AND NOT public.rubric_canonical_mapped(p_rubric,latest.canonical_requirement_id)))) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.score_rubric_items i WHERE i.rubric_id=p_rubric AND 1<>(
  SELECT count(*) FROM public.score_rubric_coverage c JOIN LATERAL (SELECT * FROM public.score_rubric_coverage_decisions d WHERE d.coverage_id=c.id ORDER BY revision DESC LIMIT 1) latest ON true
   JOIN public.score_rubric_coverage_items ci ON ci.coverage_decision_id=latest.id
   WHERE c.rubric_id=p_rubric AND latest.action='mapped' AND ci.rubric_item_id=i.id AND c.requirement_id=i.requirement_id)) THEN RETURN false; END IF;
 FOR section_row IN SELECT * FROM public.score_rubric_sections WHERE rubric_id=p_rubric LOOP
  IF NOT section_row.citation_valid OR NOT public.rubric_subject_confirmed(p_rubric,section_row.id,NULL) OR NOT public.rubric_range_valid(section_row.score_range) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.score_rubric_items WHERE rubric_id=p_rubric AND section_id=section_row.id)
   OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
  IF section_row.aggregation IN ('formula','non_additive') THEN
   IF NOT coalesce(section_row.aggregation_rule_text ~ '[^[:space:]]',false) OR NOT coalesce(section_row.ambiguity_reason ~ '[^[:space:]]',false) THEN RETURN false; END IF;
   section_bounds:=section_row.score_range IS NOT NULL;
   low_value:=(section_row.score_range->>'minimum')::numeric;
   high_value:=(section_row.score_range->>'maximum')::numeric;
  ELSE
   SELECT count(*),bool_and(score_range IS NOT NULL),sum(weight),
    sum((score_range->>'minimum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END)),
    sum((score_range->>'maximum')::numeric*(CASE WHEN section_row.aggregation='weighted_sum' THEN weight ELSE 1 END))
    INTO child_count,section_bounds,weight_total,low_value,high_value FROM public.score_rubric_items WHERE rubric_id=p_rubric AND section_id=section_row.id;
   IF child_count=0 OR (section_row.aggregation='weighted_sum' AND (weight_total IS DISTINCT FROM 1 OR EXISTS(SELECT 1 FROM public.score_rubric_items WHERE section_id=section_row.id AND weight IS NULL)))
    OR (section_row.aggregation<>'weighted_sum' AND EXISTS(SELECT 1 FROM public.score_rubric_items WHERE section_id=section_row.id AND weight IS NOT NULL)) THEN RETURN false; END IF;
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
 IF set_row.overall_aggregation='weighted_sum' AND (SELECT sum(weight) FROM public.score_rubric_sections WHERE rubric_id=p_rubric AND included_in_overall_total) IS DISTINCT FROM 1 THEN RETURN false; END IF;
 IF set_row.overall_aggregation IN ('formula','non_additive') THEN RETURN coalesce(set_row.overall_rule_text ~ '[^[:space:]]',false); END IF;
 IF set_row.overall_aggregation='capped_sum' THEN total_low:=least(total_low,set_row.overall_cap);total_high:=least(total_high,set_row.overall_cap); END IF;
 RETURN NOT all_bounds OR set_row.overall_score_range IS NULL OR ((set_row.overall_score_range->>'minimum')::numeric=round(total_low,8) AND (set_row.overall_score_range->>'maximum')::numeric=round(total_high,8));
END $$;

-- FUNCTION: rubric_subject_confirmed(uuid, uuid, uuid)
CREATE FUNCTION public.rubric_subject_confirmed(p_rubric uuid, p_section uuid, p_item uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce((SELECT action='confirm' AND revision=public.rubric_revision(p_rubric,p_section,p_item)
 FROM public.score_rubric_decisions WHERE rubric_id=p_rubric
 AND section_id IS NOT DISTINCT FROM p_section AND item_id IS NOT DISTINCT FROM p_item ORDER BY revision DESC LIMIT 1),false)
$$;

-- FUNCTION: rubric_subject_values_gate()
CREATE FUNCTION public.rubric_subject_values_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW.action='confirm' AND (NEW.section_id IS NOT NULL OR NEW.item_id IS NOT NULL)
  AND NOT public.rubric_subject_values_valid(NEW.rubric_id,NEW.section_id,NEW.item_id) THEN
  RAISE EXCEPTION 'Invalid rubric subject cannot be confirmed' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;

-- FUNCTION: rubric_subject_values_valid(uuid, uuid, uuid)
CREATE FUNCTION public.rubric_subject_values_valid(p_rubric uuid, p_section uuid, p_item uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT coalesce(CASE WHEN p_section IS NOT NULL AND p_item IS NULL THEN (
  SELECT public.rubric_range_valid(s.score_range)
   AND (s.weight IS NULL OR (s.weight>0 AND s.weight<=1))
   AND (s.aggregation='capped_sum')=(s.cap IS NOT NULL) AND (s.cap IS NULL OR s.cap>=0)
   AND (s.aggregation NOT IN ('formula','non_additive') OR (
    coalesce(s.aggregation_rule_text ~ '[^[:space:]]',false)
    AND coalesce(s.ambiguity_reason ~ '[^[:space:]]',false)))
  FROM public.score_rubric_sections s WHERE s.rubric_id=p_rubric AND s.id=p_section
 ) WHEN p_item IS NOT NULL AND p_section IS NULL THEN (
  SELECT public.rubric_range_valid(i.score_range)
   AND (i.weight IS NULL OR (i.weight>0 AND i.weight<=1))
   AND (i.assessment_mode<>'model_assessable' OR i.score_range IS NOT NULL)
   AND (i.assessment_mode='model_assessable' OR coalesce(i.ambiguity_reason ~ '[^[:space:]]',false))
  FROM public.score_rubric_items i WHERE i.rubric_id=p_rubric AND i.id=p_item
 ) ELSE false END,false)
$$;

-- FUNCTION: score_child_gate()
CREATE FUNCTION public.score_child_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE report_row public.score_reports%ROWTYPE; job_row public.jobs%ROWTYPE;
 item_row public.score_report_items%ROWTYPE; rubric_item public.score_rubric_items%ROWTYPE;
 response_row public.response_items%ROWTYPE; raw_text text; report_xid text;
BEGIN
 SELECT * INTO report_row FROM public.score_reports WHERE id=NEW.report_id;
 SELECT xmin::text INTO report_xid FROM public.score_reports WHERE id=NEW.report_id;
 IF report_xid IS DISTINCT FROM pg_current_xact_id()::text THEN RAISE EXCEPTION 'Historical score report is closed' USING ERRCODE='42501'; END IF;
 IF public.citation_publication_is_sealed(NEW.report_id,'score') THEN
  RAISE EXCEPTION 'Completed score publication is closed' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=report_row.job_id FOR UPDATE;
 PERFORM public.response_check_actor(report_row.actor_token_id,'worker');
 IF job_row.status IS DISTINCT FROM 'running' OR job_row.run_id IS DISTINCT FROM report_row.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'Score attempt is not live' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='score_report_items' THEN
  SELECT * INTO rubric_item FROM public.score_rubric_items WHERE id=NEW.rubric_item_id;
  SELECT * INTO response_row FROM public.response_items WHERE id=NEW.anchor_response_item_id;
  IF NEW.anchor_partition IS DISTINCT FROM (CASE response_row.kind WHEN 'row' THEN 'response' ELSE response_row.kind END)
   OR NEW.score_range IS DISTINCT FROM rubric_item.score_range
   OR (NEW.outcome='assessed' AND rubric_item.assessment_mode<>'model_assessable') THEN
   RAISE EXCEPTION 'Score item fixed rubric or anchor mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO item_row FROM public.score_report_items WHERE id=NEW.score_item_id;
  IF item_row.report_id IS DISTINCT FROM NEW.report_id OR item_row.task_id IS DISTINCT FROM NEW.task_id THEN
   RAISE EXCEPTION 'Score child report mismatch' USING ERRCODE='23514'; END IF;
  IF TG_TABLE_NAME='score_report_item_responses' THEN
   SELECT * INTO response_row FROM public.response_items WHERE id=NEW.response_item_id;
   IF item_row.outcome<>'assessed' OR response_row.kind IS DISTINCT FROM 'row'
    OR response_row.card_revision_id IS DISTINCT FROM NEW.card_revision_id
    OR NOT EXISTS(SELECT 1 FROM public.response_card_revisions WHERE id=NEW.card_revision_id AND state='confirmed' AND confirmed_at IS NOT NULL) THEN
    RAISE EXCEPTION 'Score support requires a fixed confirmed response' USING ERRCODE='23514'; END IF;
  ELSE
   IF NEW.kind='tender' THEN
    SELECT * INTO rubric_item FROM public.score_rubric_items WHERE id=item_row.rubric_item_id;
    IF NEW.source IS DISTINCT FROM jsonb_set(rubric_item.source,'{quote}',to_jsonb(NEW.quote))
     OR NEW.document_id IS DISTINCT FROM (rubric_item.source->>'document_id')::uuid
     OR NEW.chunk_id IS DISTINCT FROM (rubric_item.source->>'chunk_id')::uuid THEN
     RAISE EXCEPTION 'Score tender citation binding mismatch' USING ERRCODE='23514'; END IF;
    raw_text:=rubric_item.source->>'quote';
   ELSE
    SELECT * INTO response_row FROM public.response_items WHERE id=NEW.response_item_id;
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

-- FUNCTION: score_immutable_gate()
CREATE FUNCTION public.score_immutable_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF TG_TABLE_NAME='score_reports' AND TG_OP='UPDATE'
  AND current_user=(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=TG_RELID)
  AND (to_jsonb(NEW)-'encrypted_input')=(to_jsonb(OLD)-'encrypted_input') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Score report history is immutable' USING ERRCODE='42501';
END $$;

-- FUNCTION: score_inputs_current(uuid, uuid, integer)
CREATE FUNCTION public.score_inputs_current(p_draft uuid, p_rubric uuid, p_revision integer) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $$
 SELECT public.requirement_draft_current(p_draft) AND public.requirement_rubric_preparation_current(p_rubric) AND public.requirement_rubric_confirmed(p_rubric)
 AND public.requirement_prior_score_inputs_current(p_draft,p_rubric,p_revision)
$$;

-- FUNCTION: score_publication_complete()
CREATE FUNCTION public.score_publication_complete() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE job_row public.jobs%ROWTYPE; assessed integer; unassessable integer; section_value jsonb;
BEGIN
 SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id;
 IF job_row.status NOT IN ('running','succeeded') OR job_row.run_id IS DISTINCT FROM NEW.run_id
  OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR public.score_inputs_current(NEW.draft_id,NEW.rubric_id,NEW.rubric_revision) IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Score attempt or inputs lost before publication' USING ERRCODE='23514'; END IF;
 SELECT count(*) FILTER(WHERE outcome='assessed'),count(*) FILTER(WHERE outcome='unassessable') INTO assessed,unassessable
  FROM public.score_report_items WHERE report_id=NEW.id;
 IF assessed+unassessable NOT BETWEEN 1 AND 2000 OR assessed+unassessable<>(SELECT count(*) FROM public.score_rubric_items WHERE rubric_id=NEW.rubric_id)
  OR (NEW.summary->>'assessed_items')::integer IS DISTINCT FROM assessed
  OR (NEW.summary->>'unassessable_items')::integer IS DISTINCT FROM unassessable
  OR (unassessable>0 AND NEW.completion<>'partial')
  OR NEW.summary->>'total_status' IS NULL OR NEW.summary->>'total_status' NOT IN ('estimated','range_only','unavailable')
  OR (NEW.completion='complete' AND NEW.summary->>'total_status'<>'estimated')
  OR ((NEW.summary->>'total_status'='estimated') IS DISTINCT FROM (NEW.summary->>'estimated_total' IS NOT NULL))
  OR ((unassessable>0 OR NEW.completion<>'complete') AND NEW.summary->>'estimated_total' IS NOT NULL)
  OR (NEW.summary->>'total_status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sets WHERE id=NEW.rubric_id AND overall_aggregation IN ('formula','non_additive'))) THEN
  RAISE EXCEPTION 'Score summary must match published coverage' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.score_report_items item WHERE item.report_id=NEW.id AND item.outcome='assessed'
  AND (NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=item.id AND cite.kind='tender')
   OR NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=item.id AND cite.kind='draft')))
  OR EXISTS(SELECT 1 FROM public.score_report_item_responses support WHERE support.report_id=NEW.id
   AND NOT EXISTS(SELECT 1 FROM public.score_item_citations cite WHERE cite.score_item_id=support.score_item_id AND cite.response_item_id=support.response_item_id AND cite.kind='draft')) THEN
  RAISE EXCEPTION 'Score assessed items require tender and actual supporting draft citations' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(NEW.sections)<>(SELECT count(*) FROM public.score_rubric_sections WHERE rubric_id=NEW.rubric_id)
  OR (SELECT count(DISTINCT entry->>'section_key') FROM jsonb_array_elements(NEW.sections) entry)<>jsonb_array_length(NEW.sections) THEN
  RAISE EXCEPTION 'Score report must include each rubric section' USING ERRCODE='23514'; END IF;
 FOR section_value IN SELECT value FROM jsonb_array_elements(NEW.sections) LOOP
  SELECT count(*) FILTER(WHERE item.outcome='assessed'),count(*) FILTER(WHERE item.outcome='unassessable') INTO assessed,unassessable
   FROM public.score_report_items item JOIN public.score_rubric_sections section ON section.id=item.section_id
   WHERE item.report_id=NEW.id AND section.key=section_value->>'section_key';
  IF assessed+unassessable=0 OR (section_value->>'assessed_items')::integer IS DISTINCT FROM assessed
   OR (section_value->>'unassessable_items')::integer IS DISTINCT FROM unassessable
   OR ((section_value->>'status'='estimated') IS DISTINCT FROM (section_value->>'estimated_score' IS NOT NULL))
   OR section_value->>'status' IS NULL OR section_value->>'status' NOT IN ('estimated','range_only','unavailable')
   OR (unassessable>0 AND section_value->>'estimated_score' IS NOT NULL)
   OR (section_value->>'status'='estimated' AND EXISTS(SELECT 1 FROM public.score_rubric_sections WHERE rubric_id=NEW.rubric_id AND key=section_value->>'section_key' AND aggregation IN ('formula','non_additive'))) THEN
   RAISE EXCEPTION 'Score section summary must match published coverage' USING ERRCODE='23514'; END IF;
 END LOOP;
 PERFORM public.citation_seal_publication(NEW.id,'score');
 RETURN NEW;
END $$;

-- FUNCTION: score_report_gate()
CREATE FUNCTION public.score_report_gate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE job_row public.jobs%ROWTYPE; draft_row public.draft_runs%ROWTYPE; rubric_row public.score_rubric_sets%ROWTYPE;
BEGIN
 PERFORM public.response_check_actor(NEW.actor_token_id,NEW.actor_kind);
 IF NEW.actor_kind IS DISTINCT FROM 'worker'
  OR (NEW.actor_token_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.api_tokens WHERE id=NEW.actor_token_id AND scopes ? 'score:run')) THEN
  RAISE EXCEPTION 'Score worker permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO job_row FROM public.jobs WHERE id=NEW.job_id FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE id=NEW.task_id FOR SHARE;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('rubric.set:'||NEW.rubric_id::text,0));
 SELECT * INTO rubric_row FROM public.score_rubric_sets WHERE id=NEW.rubric_id;
 SELECT * INTO draft_row FROM public.draft_runs WHERE id=NEW.draft_id;
 IF job_row.kind IS DISTINCT FROM 'score' OR job_row.status IS DISTINCT FROM 'running'
  OR job_row.run_id IS DISTINCT FROM NEW.run_id OR job_row.lease_until IS NULL OR job_row.lease_until<=clock_timestamp()
  OR job_row.result->'submission'->>'input_hash' IS DISTINCT FROM NEW.input_hash
  OR job_row.result->'submission'->'input_manifest' IS DISTINCT FROM NEW.input_manifest
  OR job_row.result->'submission'->>'encrypted_input' IS DISTINCT FROM NEW.encrypted_input

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
  OR rubric_row.input_hash IS DISTINCT FROM NEW.rubric_input_hash OR rubric_row.version IS DISTINCT FROM NEW.rubric_version THEN
  RAISE EXCEPTION 'Invalid score publication attempt or fixed input' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
