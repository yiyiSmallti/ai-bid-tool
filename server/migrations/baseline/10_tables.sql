-- TABLE: score_rubric_sections
CREATE TABLE public.score_rubric_sections (
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    key text NOT NULL,
    title text NOT NULL,
    "order" integer NOT NULL,
    aggregation character varying(30) NOT NULL,
    aggregation_rule_text text,
    score_range jsonb,
    weight numeric(18,8),
    cap numeric(18,8),
    included_in_overall_total boolean NOT NULL,
    ambiguity_reason text,
    source jsonb NOT NULL,
    fingerprint character varying(64) NOT NULL,
    citation_valid boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sources jsonb,
    CONSTRAINT rubric_candidate_section_cap_finite CHECK ((cap <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT rubric_candidate_section_range CHECK (public.rubric_candidate_range_valid(score_range)),
    CONSTRAINT rubric_candidate_section_weight_finite CHECK ((weight <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT rubric_section_sources_shape CHECK (((sources IS NULL) OR ((jsonb_typeof(sources) = 'array'::text) AND (jsonb_array_length(sources) > 0)))),
    CONSTRAINT score_rubric_sections_aggregation_check CHECK (((aggregation)::text = ANY ((ARRAY['sum'::character varying, 'weighted_sum'::character varying, 'capped_sum'::character varying, 'formula'::character varying, 'non_additive'::character varying])::text[]))),
    CONSTRAINT score_rubric_sections_key_check CHECK ((key ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_sections_order_check CHECK (("order" >= 1)),
    CONSTRAINT score_rubric_sections_title_check CHECK ((title ~ '[^[:space:]]'::text))
);

-- TABLE: agent_job_links
CREATE TABLE public.agent_job_links (
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    job_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    step_id uuid,
    owned boolean NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_link_role CHECK ((((role)::text = ANY ((ARRAY['controller'::character varying, 'tool'::character varying])::text[])) AND (((role)::text <> 'tool'::text) OR (step_id IS NOT NULL))))
);

-- TABLE: agent_messages
CREATE TABLE public.agent_messages (
    session_id uuid NOT NULL,
    ordinal integer NOT NULL,
    role character varying(20) NOT NULL,
    step_id uuid,
    content_enc text NOT NULL,
    content text NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    idempotency_key uuid,
    request_sha256 character varying(64),
    receipt_enc text,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_message_receipt CHECK ((((idempotency_key IS NULL) AND (request_sha256 IS NULL) AND (receipt_enc IS NULL)) OR ((idempotency_key IS NOT NULL) AND (request_sha256 IS NOT NULL) AND (receipt_enc IS NOT NULL) AND ((role)::text = 'human'::text)))),
    CONSTRAINT agent_message_shape CHECK (((ordinal >= 1) AND ((role)::text = ANY ((ARRAY['human'::character varying, 'assistant'::character varying, 'tool'::character varying, 'system'::character varying])::text[])) AND (length(content) <= 8000)))
);

-- TABLE: agent_pauses
CREATE TABLE public.agent_pauses (
    session_id uuid NOT NULL,
    step_id uuid,
    kind character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    question text NOT NULL,
    question_enc text NOT NULL,
    action character varying(30),
    resource_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    input_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    input_sha256 character varying(64) NOT NULL,
    resolved_at timestamp with time zone,
    resume_idempotency_key uuid,
    request_sha256 character varying(64),
    receipt_enc text,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_pause_receipt CHECK ((((resume_idempotency_key IS NULL) AND (request_sha256 IS NULL) AND (receipt_enc IS NULL)) OR ((resume_idempotency_key IS NOT NULL) AND (request_sha256 IS NOT NULL) AND (receipt_enc IS NOT NULL) AND ((status)::text = 'resolved'::text)))),
    CONSTRAINT agent_pause_shape CHECK ((((kind)::text = ANY ((ARRAY['human_action'::character varying, 'authority'::character varying, 'recovery'::character varying])::text[])) AND ((status)::text = ANY ((ARRAY['pending'::character varying, 'resolved'::character varying, 'cancelled'::character varying, 'expired'::character varying])::text[])) AND ((length(question) >= 1) AND (length(question) <= 2000))))
);

-- TABLE: agent_principals
CREATE TABLE public.agent_principals (
    initial_grants jsonb NOT NULL,
    scopes jsonb NOT NULL,
    authority_expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_principal_scopes CHECK (((jsonb_typeof(initial_grants) = 'array'::text) AND (jsonb_typeof(scopes) = 'array'::text) AND (initial_grants @> scopes)))
);

-- TABLE: agent_sessions
CREATE TABLE public.agent_sessions (
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    state character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    limits jsonb NOT NULL,
    model_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    steps_used integer DEFAULT 0 NOT NULL,
    active_seconds_used integer DEFAULT 0 NOT NULL,
    vendor_calls_used integer DEFAULT 0 NOT NULL,
    active_since timestamp with time zone,
    current_job_id uuid,
    current_run_id uuid,
    pause_id uuid,
    tool_schema_sha256 character varying(64) NOT NULL,
    model_sha256 character varying(64) NOT NULL,
    input_sha256 character varying(64) NOT NULL,
    start_idempotency_key uuid NOT NULL,
    start_request_sha256 character varying(64) NOT NULL,
    start_receipt_enc text,
    cancel_idempotency_key uuid,
    cancel_request_sha256 character varying(64),
    cancel_receipt_enc text,
    expires_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_cancel_receipt CHECK ((((cancel_idempotency_key IS NULL) AND (cancel_request_sha256 IS NULL) AND (cancel_receipt_enc IS NULL)) OR ((cancel_idempotency_key IS NOT NULL) AND (cancel_request_sha256 IS NOT NULL) AND (cancel_receipt_enc IS NOT NULL) AND ((state)::text = 'cancelled'::text)))),
    CONSTRAINT agent_session_counters CHECK (((revision >= 1) AND (steps_used >= 0) AND (active_seconds_used >= 0) AND (vendor_calls_used >= 0))),
    CONSTRAINT agent_session_state CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'waiting_job'::character varying, 'paused'::character varying, 'completed'::character varying, 'partial'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);

-- TABLE: agent_steps
CREATE TABLE public.agent_steps (
    session_id uuid NOT NULL,
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    ordinal integer NOT NULL,
    kind character varying(20) NOT NULL,
    state character varying(20) DEFAULT 'planned'::character varying NOT NULL,
    command character varying(100),
    created_by_job_id uuid NOT NULL,
    created_by_run_id uuid NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    last_transition_job_id uuid NOT NULL,
    last_transition_run_id uuid NOT NULL,
    invocation_id uuid,
    arguments_enc text,
    arguments_sha256 character varying(64),
    input_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    schema_sha256 character varying(64) NOT NULL,
    child_job_id uuid,
    result_enc text,
    result_sha256 character varying(64),
    exit_code integer,
    error_code character varying(80),
    usage_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    finished_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_step_exit CHECK ((exit_code = ANY (ARRAY[0, 2, 3, 4, 5]))),
    CONSTRAINT agent_step_state CHECK (((ordinal >= 1) AND (revision >= 1) AND ((kind)::text = ANY ((ARRAY['decision'::character varying, 'tool'::character varying])::text[])) AND ((state)::text = ANY ((ARRAY['planned'::character varying, 'submitted'::character varying, 'waiting_job'::character varying, 'completed'::character varying, 'failed'::character varying, 'uncertain'::character varying])::text[]))))
);

-- TABLE: annotation_materials
CREATE TABLE public.annotation_materials (
    request_id uuid NOT NULL,
    run_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    rendition_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    manifest jsonb NOT NULL,
    rendering jsonb NOT NULL,
    content_pixel_sha256 character varying(64) NOT NULL,
    root_mapping_sha256 character varying(64) NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    card_id uuid NOT NULL,
    job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT annotation_materials_check CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((content_pixel_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((root_mapping_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: annotation_releases
CREATE TABLE public.annotation_releases (
    annotation_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    card_revision_id uuid NOT NULL,
    requirement_review_id uuid NOT NULL,
    requirement_review_revision integer NOT NULL,
    run_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    rendition_id uuid NOT NULL,
    approval_sha256 character varying(64) NOT NULL,
    approval jsonb NOT NULL,
    renderer jsonb NOT NULL,
    renderer_identity character varying(64) NOT NULL,
    rendering jsonb NOT NULL,
    content_pixel_sha256 character varying(64) NOT NULL,
    root_mapping_sha256 character varying(64) NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    card_id uuid NOT NULL,
    job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT annotation_releases_check1 CHECK ((((approval_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((renderer_identity)::text ~ '^[0-9a-f]{64}$'::text) AND ((content_pixel_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((root_mapping_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT annotation_releases_requirement_review_revision_check CHECK ((requirement_review_revision > 0))
);

-- TABLE: annotation_requests
CREATE TABLE public.annotation_requests (
    expected_card_revision integer NOT NULL,
    expected_card_revision_id uuid NOT NULL,
    request_id uuid NOT NULL,
    request_sha256 character varying(64) NOT NULL,
    input_hash character varying(64) NOT NULL,
    plan_sha256 character varying(64) NOT NULL,
    reviewed_source_sha256 character varying(64) NOT NULL,
    manifest jsonb NOT NULL,
    plan jsonb NOT NULL,
    renderer jsonb NOT NULL,
    privacy_attestation jsonb NOT NULL,
    source_kind character varying(30) NOT NULL,
    evidence_source_id uuid,
    task_certificate_id uuid,
    certificate_id uuid,
    certificate_revision_id uuid,
    certificate_file_id uuid,
    asset_id uuid,
    parent_rendition_id uuid,
    task_resource_id uuid,
    product_revision_id uuid,
    vendor_archive_id uuid,
    inherited_privacy_review_id uuid,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    card_id uuid NOT NULL,
    job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT annotation_requests_check CHECK ((((request_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((plan_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((reviewed_source_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT annotation_requests_check1 CHECK (((((source_kind)::text = 'certificate_page'::text) AND (evidence_source_id IS NOT NULL) AND (task_certificate_id IS NOT NULL) AND (certificate_id IS NOT NULL) AND (certificate_revision_id IS NOT NULL) AND (certificate_file_id IS NOT NULL) AND (asset_id IS NULL) AND (parent_rendition_id IS NULL) AND (task_resource_id IS NULL) AND (product_revision_id IS NULL) AND (vendor_archive_id IS NULL) AND (inherited_privacy_review_id IS NULL)) OR (((source_kind)::text = 'vendor_rendition'::text) AND (evidence_source_id IS NULL) AND (task_certificate_id IS NULL) AND (certificate_id IS NULL) AND (certificate_revision_id IS NULL) AND (certificate_file_id IS NULL) AND (asset_id IS NOT NULL) AND (parent_rendition_id IS NOT NULL) AND (task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL) AND (vendor_archive_id IS NOT NULL) AND (inherited_privacy_review_id IS NOT NULL)))),
    CONSTRAINT annotation_requests_expected_card_revision_check CHECK ((expected_card_revision > 0))
);

-- TABLE: api_tokens
CREATE TABLE public.api_tokens (
    name character varying(100) NOT NULL,
    digest character varying(64) NOT NULL,
    scopes jsonb NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT token_forbidden_agent_scopes CHECK ((NOT (scopes ?| ARRAY['agent:read'::text, 'agent:run'::text, 'agent:cancel'::text]))),
    CONSTRAINT token_forbidden_annotation_scope CHECK ((NOT (scopes ? 'evidence:annotate'::text))),
    CONSTRAINT token_forbidden_attachment_scopes CHECK ((NOT (scopes ?| ARRAY['attachment:write'::text, 'attachment:review'::text, 'attachment:manage'::text, 'attachment:original:read'::text, 'task:attachment'::text, 'attachment:privacy'::text, 'attachment:page:read'::text]))),
    CONSTRAINT token_forbidden_bid_review_scopes CHECK ((NOT (scopes ?| ARRAY['bid-review:upload'::text, 'bid-review:prepare'::text, 'bid-review:report:render'::text, 'bid-review:original:read'::text, 'bid-review:source:read'::text, 'bid-review:report:read'::text, 'bid-review:report:download'::text, 'bid-review:decide'::text, 'bid-review:evidence:review'::text, 'bid-review:price:release'::text, 'bid-review:outbound:authorize'::text, 'bid-review:classify'::text]))),
    CONSTRAINT token_forbidden_check_scopes CHECK ((NOT (scopes ? 'check:decide'::text))),
    CONSTRAINT token_forbidden_confidential_scopes CHECK (((NOT (scopes ? 'confidential:write'::text)) AND (NOT (scopes ? 'confidential:reveal'::text)))),
    CONSTRAINT token_forbidden_cosign_scopes CHECK ((NOT (scopes ?| ARRAY['task:review-policy'::text, 'card:cosign'::text]))),
    CONSTRAINT token_forbidden_creation_scope CHECK ((NOT (scopes ? 'token:create'::text))),
    CONSTRAINT token_forbidden_library_lifecycle_scopes CHECK ((NOT (scopes ?| ARRAY['certificate:lifecycle'::text, 'profile:lifecycle'::text]))),
    CONSTRAINT token_forbidden_member_scope CHECK ((NOT (scopes ? 'member:manage'::text))),
    CONSTRAINT token_forbidden_memory_scopes CHECK ((NOT (scopes ?| ARRAY['memory:approve'::text, 'memory:manage'::text, 'memory:eval:read'::text, 'memory:eval:review'::text]))),
    CONSTRAINT token_forbidden_requirement_scopes CHECK ((NOT (scopes ?| ARRAY['req:confirm'::text, 'req:manual'::text]))),
    CONSTRAINT token_forbidden_scopes CHECK (((NOT (scopes ? 'evidence:confirm'::text)) AND (NOT (scopes ? 'export'::text)))),
    CONSTRAINT token_forbidden_score_scopes CHECK ((NOT (scopes ? 'score:rubric:review'::text))),
    CONSTRAINT token_forbidden_screenshot_scopes CHECK ((NOT (scopes ? 'screenshot:ingest'::text))),
    CONSTRAINT token_forbidden_template_file_scope CHECK ((NOT (scopes ? 'template:file:read'::text))),
    CONSTRAINT token_forbidden_workflow_scopes CHECK ((NOT (scopes ?| ARRAY['task:members:write'::text, 'task:archive'::text, 'card:assign'::text, 'card:comment'::text, 'task:review-policy'::text, 'card:cosign'::text]))),
    CONSTRAINT token_no_provider_write CHECK ((NOT (scopes ? 'provider:write'::text)))
);

-- TABLE: attachment_archives
CREATE TABLE public.attachment_archives (
    current_revision integer NOT NULL,
    state_version integer NOT NULL,
    active boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_archives_check CHECK (((current_revision > 0) AND (state_version > 0)))
);

-- TABLE: attachment_file_parts
CREATE TABLE public.attachment_file_parts (
    attachment_id uuid NOT NULL,
    attachment_revision_id uuid NOT NULL,
    attachment_file_id uuid NOT NULL,
    ordinal integer NOT NULL,
    name character varying(200) NOT NULL,
    media_type character varying(40) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size_bytes bigint NOT NULL,
    page_start integer NOT NULL,
    page_count integer NOT NULL,
    rotation integer NOT NULL,
    storage_key character varying(400) NOT NULL,
    upload_name_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_file_parts_check CHECK ((((ordinal >= 1) AND (ordinal <= 20)) AND (rotation = ANY (ARRAY[0, 90, 180, 270])))),
    CONSTRAINT attachment_file_parts_check1 CHECK ((((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'image/png'::character varying, 'image/jpeg'::character varying])::text[])) AND ((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 41943040)))),
    CONSTRAINT attachment_file_parts_check2 CHECK ((((page_start >= 1) AND (page_start <= 200)) AND ((page_count >= 1) AND (page_count <= 200)) AND (((page_start + page_count) - 1) <= 200))),
    CONSTRAINT attachment_file_parts_check3 CHECK ((((length(btrim((name)::text)) >= 1) AND (length(btrim((name)::text)) <= 200)) AND (POSITION(('/'::text) IN (name)) = 0) AND (POSITION((chr(92)) IN (name)) = 0) AND ((name)::text !~ '[[:cntrl:]]'::text) AND (length(upload_name_encrypted) > 0))),
    CONSTRAINT attachment_file_parts_check4 CHECK (((storage_key)::text = ((((((((('attachment/'::text) || (attachment_id)::text) || '/'::text) || (attachment_revision_id)::text) || '/parts/'::text) || (ordinal)::text) || '/'::text) || (sha256)::text) || CASE media_type WHEN 'application/pdf'::text THEN '.pdf'::text WHEN 'image/png'::text THEN '.png'::text ELSE '.jpg'::text END)))
);

-- TABLE: attachment_files
CREATE TABLE public.attachment_files (
    attachment_id uuid NOT NULL,
    attachment_revision_id uuid NOT NULL,
    file jsonb NOT NULL,
    storage_key character varying(400) NOT NULL,
    upload_name_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_files_check CHECK (((jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((file ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((file ->> 'name'::text))) = 0) AND ((file ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND ("right"((file ->> 'name'::text), 4) = '.pdf'::text) AND (length(upload_name_encrypted) > 0))),
    CONSTRAINT attachment_files_check1 CHECK (((storage_key)::text = ((((((('attachment/'::text) || (attachment_id)::text) || '/'::text) || (attachment_revision_id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.pdf'::text))),
    CONSTRAINT attachment_files_file_check CHECK (((jsonb_typeof(file) = 'object'::text) AND (file ?& ARRAY['name'::text, 'sha256'::text, 'size_bytes'::text, 'page_count'::text, 'media_type'::text]) AND (jsonb_typeof((file -> 'media_type'::text)) = 'string'::text) AND ((file ->> 'media_type'::text) = 'application/pdf'::text) AND (jsonb_typeof((file -> 'sha256'::text)) = 'string'::text) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT attachment_files_file_check1 CHECK (((jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric)) AND (jsonb_typeof((file -> 'page_count'::text)) = 'number'::text) AND ((((file ->> 'page_count'::text))::numeric >= (1)::numeric) AND (((file ->> 'page_count'::text))::numeric <= (200)::numeric)) AND (((file ->> 'page_count'::text))::numeric = trunc(((file ->> 'page_count'::text))::numeric))))
);

-- TABLE: attachment_privacy_holds
CREATE TABLE public.attachment_privacy_holds (
    task_id uuid NOT NULL,
    evidence_source_id uuid NOT NULL,
    source_png_sha256 character varying(64) NOT NULL,
    prior_hold_id uuid,
    reviewed_at timestamp with time zone NOT NULL,
    reason character varying(30) NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_privacy_holds_check CHECK ((((reason)::text = 'sensitive_content'::text) AND ((source_png_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT attachment_privacy_holds_payload_hash_check CHECK (((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: attachment_reviews
CREATE TABLE public.attachment_reviews (
    attachment_id uuid NOT NULL,
    attachment_revision_id uuid NOT NULL,
    file_id uuid NOT NULL,
    original_sha256 character varying(64) NOT NULL,
    metadata_sha256 character varying(64) NOT NULL,
    prior_review_id uuid,
    decision character varying(10) NOT NULL,
    reason character varying(40) NOT NULL,
    reviewed_at timestamp with time zone NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_reviews_check CHECK ((((decision)::text = ANY ((ARRAY['approve'::character varying, 'reject'::character varying, 'revoke'::character varying])::text[])) AND (((decision)::text = 'approve'::text) = ((reason)::text = 'accepted_for_internal_use'::text)) AND (((decision)::text <> 'revoke'::text) OR (prior_review_id IS NOT NULL)))),
    CONSTRAINT attachment_reviews_check1 CHECK ((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((metadata_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT attachment_reviews_payload_hash_check CHECK (((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT attachment_reviews_reason_check CHECK (((reason)::text = ANY ((ARRAY['accepted_for_internal_use'::character varying, 'wrong_document'::character varying, 'unreadable_document'::character varying, 'metadata_mismatch'::character varying, 'privacy_concern'::character varying, 'superseded'::character varying, 'withdrawn_by_org'::character varying, 'reviewer_unavailable'::character varying, 'responsibility_changed'::character varying, 'declaration_changed'::character varying, 'selection_replaced'::character varying])::text[])))
);

-- TABLE: attachment_revisions
CREATE TABLE public.attachment_revisions (
    attachment_id uuid NOT NULL,
    revision integer NOT NULL,
    kind character varying(30) NOT NULL,
    label_encrypted text NOT NULL,
    metadata_sha256 character varying(64) NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachment_revisions_check CHECK (((revision > 0) AND ((metadata_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(label_encrypted) > 0))),
    CONSTRAINT attachment_revisions_kind_check CHECK (((kind)::text = ANY ((ARRAY['business_licence'::character varying, 'qualification_scan'::character varying, 'contract'::character varying, 'performance_record'::character varying])::text[]))),
    CONSTRAINT attachment_revisions_payload_hash_check CHECK (((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: audit_logs
CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_token_id uuid,
    action character varying(100) NOT NULL,
    object_id uuid NOT NULL,
    details jsonb NOT NULL,
    actor_kind character varying(20),
    job_id uuid,
    run_id uuid,
    initiated_by character varying(30),
    agent_principal_id uuid,
    agent_session_id uuid,
    agent_step_id uuid,
    invocation_id uuid,
    command character varying(100),
    resource_revision_id_text text GENERATED ALWAYS AS ((details ->> 'new_revision_id'::text)) STORED,
    memory_revision_id_text text GENERATED ALWAYS AS ((details ->> 'revision_id'::text)) STORED,
    CONSTRAINT audit_logs_agent_origin_binding CHECK ((((initiated_by)::text = 'builtin_agent'::text) AND (agent_principal_id IS NOT NULL) AND (agent_session_id IS NOT NULL) AND (actor_token_id IS NULL)) OR (((initiated_by)::text = 'external_agent'::text) AND (actor_token_id IS NOT NULL) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)) OR (((COALESCE(initiated_by, 'legacy_unknown'::character varying))::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying])::text[])) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL))),
    CONSTRAINT audit_logs_agent_origin_shape CHECK (((initiated_by IS NULL) OR ((initiated_by)::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying, 'builtin_agent'::character varying, 'external_agent'::character varying])::text[])))),
    CONSTRAINT audit_object CHECK ((jsonb_typeof(details) = 'object'::text))
);

-- TABLE: bid_document_pages
CREATE TABLE public.bid_document_pages (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    document_id uuid NOT NULL,
    page integer NOT NULL,
    role character varying(10) NOT NULL,
    original_sha256 character varying(64) NOT NULL,
    rendered_pdf_sha256 character varying(64) NOT NULL,
    image jsonb NOT NULL,
    storage_key character varying(500) NOT NULL,
    render_profile character varying(100) NOT NULL,
    renderer_identity character varying(200) NOT NULL,
    page_kind character varying(10) NOT NULL,
    text_status character varying(20) NOT NULL,
    text_sha256 character varying(64),
    text_encrypted text,
    structure_encrypted text,
    price_page boolean DEFAULT true NOT NULL,
    redaction_status character varying(20) DEFAULT 'human_only'::character varying NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_document_pages_check CHECK ((((page >= 1) AND (page <= 1000)) AND ((role)::text = ANY ((ARRAY['tender'::character varying, 'bid'::character varying])::text[])))),
    CONSTRAINT bid_document_pages_check1 CHECK ((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((rendered_pdf_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT bid_document_pages_check2 CHECK ((((page_kind)::text = ANY ((ARRAY['text'::character varying, 'image'::character varying])::text[])) AND ((text_status)::text = ANY ((ARRAY['native'::character varying, 'unavailable'::character varying])::text[])))),
    CONSTRAINT bid_document_pages_check3 CHECK (((((text_status)::text = 'unavailable'::text) AND (text_sha256 IS NULL) AND (text_encrypted IS NULL)) OR (((text_status)::text = 'native'::text) AND (text_sha256 IS NOT NULL) AND (text_encrypted IS NOT NULL) AND ((text_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(text_encrypted) > 0)))),
    CONSTRAINT bid_document_pages_check4 CHECK (((storage_key)::text = ((((((((((('bid-review/'::text) || (submission_id)::text) || '/preparations/'::text) || (preparation_id)::text) || '/pages/'::text) || (document_id)::text) || '/'::text) || (page)::text) || '/'::text) || (image ->> 'sha256'::text)) || '.png'::text))),
    CONSTRAINT bid_document_pages_image_check CHECK (((jsonb_typeof(image) = 'object'::text) AND (image ?& ARRAY['sha256'::text, 'size_bytes'::text, 'width_px'::text, 'height_px'::text, 'media_type'::text]) AND ((image ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND ((image ->> 'media_type'::text) = 'image/png'::text) AND (((image ->> 'size_bytes'::text))::bigint > 0) AND (((image ->> 'width_px'::text))::integer > 0) AND (((image ->> 'height_px'::text))::integer > 0))),
    CONSTRAINT bid_document_pages_redaction_status_check CHECK (((redaction_status)::text = 'human_only'::text))
);

-- TABLE: bid_outbound_authorizations
CREATE TABLE public.bid_outbound_authorizations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    revision integer NOT NULL,
    prior_authorization_id uuid,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    submission_manifest_sha256 character varying(64) NOT NULL,
    preparation_input_hash character varying(64) NOT NULL,
    redaction_manifest_sha256 character varying(64) NOT NULL,
    authorized_sanitized_context_sha256 character varying(64) NOT NULL,
    provider_bindings_sha256 character varying(64) NOT NULL,
    allow_external boolean NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    reason_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_outbound_authorizations_check CHECK (((revision > 0) AND (length(reason_encrypted) > 0))),
    CONSTRAINT bid_outbound_authorizations_check1 CHECK ((((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((submission_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((preparation_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((redaction_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((authorized_sanitized_context_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provider_bindings_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_outbound_authorized_pages
CREATE TABLE public.bid_outbound_authorized_pages (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    page_id uuid NOT NULL,
    sanitized_text_sha256 character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_outbound_authorized_pages_sanitized_text_sha256_check CHECK (((sanitized_text_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: bid_pdf_validations
CREATE TABLE public.bid_pdf_validations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    document_id uuid NOT NULL,
    original_sha256 character varying(64) NOT NULL,
    validator_identity character varying(200) NOT NULL,
    trust_store_sha256 character varying(64) NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_pdf_validations_check CHECK ((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((trust_store_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT bid_pdf_validations_check1 CHECK (((length((validator_identity)::text) > 0) AND (length(details_encrypted) > 0)))
);

-- TABLE: bid_preparation_publications
CREATE TABLE public.bid_preparation_publications (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    page_count integer NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_preparation_publications_check CHECK ((((page_count >= 2) AND (page_count <= 1000)) AND ((input_hash)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_preparation_trust
CREATE TABLE public.bid_preparation_trust (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    trust_store_sha256 character varying(64) NOT NULL,
    anchors jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_preparation_trust_anchors_check CHECK (((jsonb_typeof(anchors) = 'array'::text) AND (jsonb_array_length(anchors) <= 128))),
    CONSTRAINT bid_preparation_trust_trust_store_sha256_check CHECK (((trust_store_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: bid_preparations
CREATE TABLE public.bid_preparations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    job_id uuid NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    input_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_preparations_check CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_prepared_documents
CREATE TABLE public.bid_prepared_documents (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_count integer NOT NULL,
    rendered_pdf_sha256 character varying(64) NOT NULL,
    rendered_pdf_storage_key character varying(500) NOT NULL,
    render_profile character varying(100) NOT NULL,
    renderer_identity character varying(200) NOT NULL,
    citation_mode character varying(10) NOT NULL,
    parsing_warnings jsonb NOT NULL,
    signature_field_count integer NOT NULL,
    structure_encrypted text,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_prepared_documents_check CHECK ((((page_count >= 1) AND (page_count <= 1000)) AND (signature_field_count >= 0))),
    CONSTRAINT bid_prepared_documents_check1 CHECK ((((rendered_pdf_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((citation_mode)::text = ANY ((ARRAY['page'::character varying, 'block'::character varying])::text[])))),
    CONSTRAINT bid_prepared_documents_check2 CHECK (((length((render_profile)::text) > 0) AND (length((renderer_identity)::text) > 0))),
    CONSTRAINT bid_prepared_documents_parsing_warnings_check CHECK (((jsonb_typeof(parsing_warnings) = 'array'::text) AND (jsonb_array_length(parsing_warnings) <= 100)))
);

-- TABLE: bid_presence_authorizations
CREATE TABLE public.bid_presence_authorizations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    presence_preparation_id uuid NOT NULL,
    revision integer NOT NULL,
    prior_authorization_id uuid,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    manifest_sha256 character varying(64) NOT NULL,
    image_ids jsonb NOT NULL,
    allow_external boolean NOT NULL,
    reason_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_presence_authorizations_check CHECK (((revision > 0) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(reason_encrypted) > 0))),
    CONSTRAINT bid_presence_authorizations_image_ids_check CHECK (((jsonb_typeof(image_ids) = 'array'::text) AND ((jsonb_array_length(image_ids) >= 1) AND (jsonb_array_length(image_ids) <= 40))))
);

-- TABLE: bid_presence_authorized_images
CREATE TABLE public.bid_presence_authorized_images (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    presence_preparation_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    image_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: bid_presence_images
CREATE TABLE public.bid_presence_images (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    presence_preparation_id uuid NOT NULL,
    page_id uuid NOT NULL,
    sha256 character varying(64) NOT NULL,
    source_sha256 character varying(64) NOT NULL,
    size_bytes integer NOT NULL,
    width_px integer NOT NULL,
    height_px integer NOT NULL,
    blur jsonb NOT NULL,
    privacy_receipt_sha256 character varying(64) NOT NULL,
    storage_key text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_presence_images_check CHECK ((((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((privacy_receipt_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT bid_presence_images_check1 CHECK ((((size_bytes >= 1) AND (size_bytes <= 4194304)) AND ((width_px >= 1) AND (width_px <= 8192)) AND ((height_px >= 1) AND (height_px <= 8192)) AND ((width_px * height_px) <= 16000000))),
    CONSTRAINT bid_presence_images_check2 CHECK ((storage_key = ((((((('bid-review/'::text) || (submission_id)::text) || '/presence/'::text) || (presence_preparation_id)::text) || '/'::text) || (sha256)::text) || '.jpg'::text)))
);

-- TABLE: bid_presence_preparations
CREATE TABLE public.bid_presence_preparations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    source_review_id uuid NOT NULL,
    manifest_sha256 character varying(64) NOT NULL,
    expected_page_ids jsonb NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_presence_preparations_check CHECK ((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(details_encrypted) > 0))),
    CONSTRAINT bid_presence_preparations_expected_page_ids_check CHECK (((jsonb_typeof(expected_page_ids) = 'array'::text) AND ((jsonb_array_length(expected_page_ids) >= 1) AND (jsonb_array_length(expected_page_ids) <= 40))))
);

-- TABLE: bid_redacted_pages
CREATE TABLE public.bid_redacted_pages (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    page_id uuid NOT NULL,
    sanitized_text_sha256 character varying(64) NOT NULL,
    sanitized_text_encrypted text NOT NULL,
    price_page boolean NOT NULL,
    classification character varying(20) NOT NULL,
    notes_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_redacted_pages_check CHECK ((((sanitized_text_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(sanitized_text_encrypted) > 0) AND (length(notes_encrypted) > 0))),
    CONSTRAINT bid_redacted_pages_check1 CHECK ((((classification)::text = ANY ((ARRAY['price'::character varying, 'non_price'::character varying, 'uncertain'::character varying])::text[])) AND (price_page = ((classification)::text <> 'non_price'::text))))
);

-- TABLE: bid_redaction_snapshots
CREATE TABLE public.bid_redaction_snapshots (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    redaction_revision integer NOT NULL,
    policy_version character varying(100) NOT NULL,
    manifest_sha256 character varying(64) NOT NULL,
    confidential_binding_sha256 character varying(64) NOT NULL,
    derived_name_lists_sha256 character varying(64) NOT NULL,
    provider_bindings_sha256 character varying(64) NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_redaction_snapshots_check CHECK (((redaction_revision > 0) AND (length((policy_version)::text) > 0) AND (length(details_encrypted) > 0))),
    CONSTRAINT bid_redaction_snapshots_check1 CHECK ((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((confidential_binding_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((derived_name_lists_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provider_bindings_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_review_finding_events
CREATE TABLE public.bid_review_finding_events (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    prior_decision_id uuid,
    revision integer NOT NULL,
    action character varying(20) NOT NULL,
    review_domain character varying(20) NOT NULL,
    state character varying(20) NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    reason_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_finding_events_check CHECK ((((revision >= 2) AND (revision <= 100000)) AND ((action)::text = ANY ((ARRAY['classify'::character varying, 'dismiss'::character varying, 'reopen'::character varying, 'confirm'::character varying])::text[])) AND ((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[])) AND ((state)::text = ANY ((ARRAY['open'::character varying, 'dismissed'::character varying, 'confirmed'::character varying])::text[])))),
    CONSTRAINT bid_review_finding_events_check1 CHECK ((((expected_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND (length(reason_encrypted) > 0)))
);

-- TABLE: bid_review_finding_sources
CREATE TABLE public.bid_review_finding_sources (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    validation_id uuid,
    document_id uuid NOT NULL,
    page_id uuid,
    kind character varying(30) NOT NULL,
    ordinal integer NOT NULL,
    quote_sha256 character varying(64),
    start_offset integer,
    end_offset integer,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_finding_sources_check CHECK ((((kind)::text = 'rule_document'::text) = (validation_id IS NOT NULL))),
    CONSTRAINT bid_review_finding_sources_check1 CHECK ((((kind)::text = ANY ((ARRAY['tender_support'::character varying, 'bid_support'::character varying, 'absence_page'::character varying, 'inventory_document'::character varying, 'rule_document'::character varying])::text[])) AND ((ordinal >= 1) AND (ordinal <= 1000)))),
    CONSTRAINT bid_review_finding_sources_check2 CHECK ((((kind)::text = ANY ((ARRAY['inventory_document'::character varying, 'rule_document'::character varying])::text[])) = (page_id IS NULL))),
    CONSTRAINT bid_review_finding_sources_check3 CHECK ((((kind)::text = ANY ((ARRAY['tender_support'::character varying, 'bid_support'::character varying])::text[])) = (quote_sha256 IS NOT NULL))),
    CONSTRAINT bid_review_finding_sources_check4 CHECK ((((quote_sha256 IS NULL) AND (start_offset IS NULL) AND (end_offset IS NULL)) OR ((quote_sha256 IS NOT NULL) AND (start_offset IS NOT NULL) AND (end_offset IS NOT NULL) AND (start_offset >= 0) AND (end_offset > start_offset)))),
    CONSTRAINT bid_review_finding_sources_quote_sha256_check CHECK (((quote_sha256 IS NULL) OR ((quote_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_review_findings
CREATE TABLE public.bid_review_findings (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    obligation_id uuid NOT NULL,
    ordinal integer NOT NULL,
    code character varying(100) NOT NULL,
    outcome character varying(20) NOT NULL,
    severity character varying(10) NOT NULL,
    impact character varying(20) NOT NULL,
    tender_support_count integer NOT NULL,
    bid_support_count integer NOT NULL,
    search_source_count integer NOT NULL,
    rule_document_count integer NOT NULL,
    absence_kind character varying(30),
    absence_coverage character varying(30),
    absence_method character varying(30),
    absence_manifest_sha256 character varying(64),
    limitation_count integer NOT NULL,
    details_encrypted text NOT NULL,
    details_sha256 character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_findings_check CHECK ((((tender_support_count >= 1) AND (tender_support_count <= 20)) AND ((bid_support_count >= 0) AND (bid_support_count <= 20)) AND ((search_source_count >= 0) AND (search_source_count <= 1000)) AND ((rule_document_count >= 0) AND (rule_document_count <= 19)) AND ((limitation_count >= 0) AND (limitation_count <= 100)))),
    CONSTRAINT bid_review_findings_check1 CHECK ((((absence_kind IS NULL) AND (absence_coverage IS NULL) AND (absence_method IS NULL) AND (absence_manifest_sha256 IS NULL) AND (search_source_count = 0) AND ((bid_support_count > 0) OR (rule_document_count > 0))) OR (((absence_kind)::text = 'locations'::text) AND ((absence_coverage)::text = ANY ((ARRAY['required_locations'::character varying, 'all_bid_pages'::character varying, 'partial'::character varying])::text[])) AND ((absence_method)::text = ANY ((ARRAY['local_text'::character varying, 'local_pdf'::character varying, 'ocr'::character varying, 'llm'::character varying, 'human'::character varying])::text[])) AND (absence_manifest_sha256 IS NULL) AND ((search_source_count > 0) OR (((outcome)::text = 'unknown'::text) AND ((absence_coverage)::text = 'partial'::text)))) OR (((absence_kind)::text = 'submission_inventory'::text) AND ((absence_coverage)::text = ANY ((ARRAY['complete_inventory'::character varying, 'partial'::character varying])::text[])) AND ((absence_method)::text = ANY ((ARRAY['manifest_rule'::character varying, 'llm_mapping'::character varying, 'human'::character varying])::text[])) AND ((absence_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((search_source_count >= 1) AND (search_source_count <= 19))))),
    CONSTRAINT bid_review_findings_check2 CHECK (((((absence_coverage)::text IS DISTINCT FROM 'partial'::text) OR (((outcome)::text = 'unknown'::text) AND (limitation_count > 0))) AND (((outcome)::text <> 'unknown'::text) OR (limitation_count > 0)))),
    CONSTRAINT bid_review_findings_check3 CHECK ((((ordinal >= 1) AND (ordinal <= 10000)) AND ((code)::text ~ '^[a-z][a-z0-9_]{0,99}$'::text))),
    CONSTRAINT bid_review_findings_check4 CHECK ((((outcome)::text = ANY ((ARRAY['responded'::character varying, 'deviation'::character varying, 'missing'::character varying, 'unknown'::character varying])::text[])) AND ((severity)::text = ANY ((ARRAY['fatal'::character varying, 'high'::character varying, 'medium'::character varying])::text[])) AND ((impact)::text = ANY ((ARRAY['rejection'::character varying, 'lost_points'::character varying, 'both'::character varying, 'uncertain'::character varying])::text[])))),
    CONSTRAINT bid_review_findings_check5 CHECK (((((outcome)::text <> 'responded'::text) OR (bid_support_count > 0)) AND (((outcome)::text <> 'deviation'::text) OR (bid_support_count > 0) OR (rule_document_count > 0)) AND (((outcome)::text <> ALL ((ARRAY['missing'::character varying, 'unknown'::character varying])::text[])) OR (absence_kind IS NOT NULL)) AND (((outcome)::text <> 'missing'::text) OR (bid_support_count = 0)) AND ((rule_document_count = 0) OR (((code)::text = 'signature_validation_invalid'::text) AND ((outcome)::text = 'deviation'::text))))),
    CONSTRAINT bid_review_findings_check6 CHECK (((length(details_encrypted) > 0) AND ((details_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_review_name_lists
CREATE TABLE public.bid_review_name_lists (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    revision integer NOT NULL,
    request_id uuid NOT NULL,
    names_sha256 character varying(64) NOT NULL,
    names_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_name_lists_check CHECK (((revision > 0) AND ((names_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(names_encrypted) > 0)))
);

-- TABLE: bid_review_obligations
CREATE TABLE public.bid_review_obligations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    page_id uuid NOT NULL,
    ordinal integer NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_obligations_check CHECK ((((ordinal >= 1) AND (ordinal <= 2000)) AND (length(details_encrypted) > 0)))
);

-- TABLE: bid_review_publications
CREATE TABLE public.bid_review_publications (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    run_id uuid NOT NULL,
    completion character varying(20) NOT NULL,
    coverage jsonb NOT NULL,
    uncovered_codes jsonb NOT NULL,
    output_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_publications_check CHECK ((((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])) AND ((output_hash)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT bid_review_publications_check1 CHECK (((jsonb_typeof(coverage) = 'object'::text) AND (coverage ?& ARRAY['obligations'::text, 'signing_requirements'::text, 'required_locations'::text]) AND (jsonb_typeof(uncovered_codes) = 'array'::text) AND (jsonb_array_length(uncovered_codes) <= 100)))
);

-- TABLE: bid_review_report_artifacts
CREATE TABLE public.bid_review_report_artifacts (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    run_id uuid NOT NULL,
    format character varying(10) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size_bytes integer NOT NULL,
    storage_key text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_report_artifacts_check CHECK ((((format)::text = ANY ((ARRAY['docx'::character varying, 'console'::character varying])::text[])) AND ((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 104857600)))),
    CONSTRAINT bid_review_report_artifacts_check1 CHECK ((storage_key ~~ ((((('bid-review/'::text) || (submission_id)::text) || '/reports/'::text) || (snapshot_id)::text) || '/%'::text)))
);

-- TABLE: bid_review_report_snapshots
CREATE TABLE public.bid_review_report_snapshots (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    job_id uuid NOT NULL,
    request_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    report_input_hash character varying(64) NOT NULL,
    decisions_snapshot_sha256 character varying(64) NOT NULL,
    renderer_identity text NOT NULL,
    details_sha256 character varying(64) NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_report_snapshots_check CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((report_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((decisions_snapshot_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((details_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT bid_review_report_snapshots_check1 CHECK (((length(details_encrypted) > 0) AND (length(renderer_identity) > 0)))
);

-- TABLE: bid_review_required_locations
CREATE TABLE public.bid_review_required_locations (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    page_id uuid,
    ordinal integer NOT NULL,
    group_id character varying(64),
    status character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_required_locations_check CHECK ((((ordinal >= 1) AND (ordinal <= 10000)) AND ((status)::text = ANY ((ARRAY['unresolved'::character varying, 'triage_present'::character varying, 'triage_absent'::character varying, 'triage_uncertain'::character varying])::text[]))))
);

-- TABLE: bid_review_runs
CREATE TABLE public.bid_review_runs (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    tender_document_id uuid NOT NULL,
    authorization_id uuid NOT NULL,
    job_id uuid NOT NULL,
    request_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    payload_hash character varying(64) NOT NULL,
    manifest jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_runs_check CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: bid_review_signing_requirements
CREATE TABLE public.bid_review_signing_requirements (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    review_id uuid NOT NULL,
    page_id uuid NOT NULL,
    candidate_id uuid,
    applicability character varying(20) NOT NULL,
    ordinal integer NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_review_signing_requirements_applicability_check CHECK (((applicability)::text = ANY ((ARRAY['applies'::character varying, 'not_applicable'::character varying, 'alternative'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT bid_review_signing_requirements_check CHECK ((((ordinal >= 1) AND (ordinal <= 10000)) AND (length(details_encrypted) > 0)))
);

-- TABLE: bid_signing_candidates
CREATE TABLE public.bid_signing_candidates (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_id uuid NOT NULL,
    page integer NOT NULL,
    ordinal integer NOT NULL,
    candidate_kind character varying(100) NOT NULL,
    applicability character varying(20) DEFAULT 'unknown'::character varying NOT NULL,
    details_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_signing_candidates_check CHECK ((((page >= 1) AND (page <= 1000)) AND ((ordinal >= 1) AND (ordinal <= 10000)))),
    CONSTRAINT bid_signing_candidates_check1 CHECK ((((applicability)::text = 'unknown'::text) AND (length((candidate_kind)::text) > 0) AND (length(details_encrypted) > 0)))
);

-- TABLE: bid_submission_documents
CREATE TABLE public.bid_submission_documents (
    task_id uuid NOT NULL,
    submission_id uuid NOT NULL,
    file_id uuid NOT NULL,
    ordinal integer NOT NULL,
    role character varying(10) NOT NULL,
    kind character varying(30) NOT NULL,
    media_type character varying(100) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size_bytes bigint NOT NULL,
    storage_key character varying(500) NOT NULL,
    upload_name_encrypted text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_submission_documents_check CHECK ((((ordinal >= 1) AND (ordinal <= 20)) AND ((size_bytes >= 1) AND (size_bytes <= 104857600)))),
    CONSTRAINT bid_submission_documents_check1 CHECK ((((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(upload_name_encrypted) > 0))),
    CONSTRAINT bid_submission_documents_check2 CHECK ((((role)::text = ANY ((ARRAY['tender'::character varying, 'bid'::character varying])::text[])) AND (((role)::text = 'tender'::text) = ((kind)::text = 'tender'::text)))),
    CONSTRAINT bid_submission_documents_check3 CHECK (((storage_key)::text = ((((((('bid-review/'::text) || (submission_id)::text) || '/originals/'::text) || (file_id)::text) || '/'::text) || (sha256)::text) || CASE media_type WHEN 'application/pdf'::text THEN '.pdf'::text ELSE '.docx'::text END))),
    CONSTRAINT bid_submission_documents_kind_check CHECK (((kind)::text = ANY ((ARRAY['tender'::character varying, 'qualification'::character varying, 'commercial_technical'::character varying, 'price'::character varying, 'declaration'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT bid_submission_documents_media_type_check CHECK (((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::character varying])::text[])))
);

-- TABLE: bid_submissions
CREATE TABLE public.bid_submissions (
    task_id uuid NOT NULL,
    revision integer NOT NULL,
    manifest_sha256 character varying(64) NOT NULL,
    file_count integer NOT NULL,
    total_bytes bigint NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    state character varying(20) DEFAULT 'uploaded'::character varying NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bid_submissions_check CHECK (((revision > 0) AND ((state)::text = ANY ((ARRAY['uploaded'::character varying, 'withdrawn'::character varying])::text[])))),
    CONSTRAINT bid_submissions_check1 CHECK ((((file_count >= 2) AND (file_count <= 20)) AND ((total_bytes >= 2) AND (total_bytes <= 524288000)))),
    CONSTRAINT bid_submissions_check2 CHECK ((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: card_evidence_links
CREATE TABLE public.card_evidence_links (
    card_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: card_generation_runs
CREATE TABLE public.card_generation_runs (
    target_revisions jsonb NOT NULL,
    model_revision integer,
    reasoning character varying(20),
    model_redaction_enabled boolean NOT NULL,
    model_redaction_revision integer NOT NULL,
    redaction_rule_version character varying(40) NOT NULL,
    prompt_version character varying(40) NOT NULL,
    schema_version character varying(40) NOT NULL,
    adapter_version character varying(100) NOT NULL,
    encrypted_input text,
    result jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    generation_job_id uuid NOT NULL,
    generation_run_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    actor_token_id uuid,
    actor_kind character varying(20) NOT NULL,
    input_manifest jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT card_generation_runs_gate_0 CHECK (((input_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT card_generation_runs_gate_1 CHECK ((model_redaction_revision > 0)),
    CONSTRAINT card_generation_runs_gate_2 CHECK (((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(target_revisions) = 'object'::text) AND (jsonb_typeof(result) = 'object'::text))),
    CONSTRAINT card_generation_runs_gate_3 CHECK (((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[])))
);

-- TABLE: certificate_file_parts
CREATE TABLE public.certificate_file_parts (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    certificate_id uuid NOT NULL,
    certificate_revision_id uuid NOT NULL,
    certificate_file_id uuid NOT NULL,
    ordinal integer NOT NULL,
    name character varying(200) NOT NULL,
    media_type character varying(40) NOT NULL,
    sha256 character varying(64) NOT NULL,
    size_bytes bigint NOT NULL,
    page_start integer NOT NULL,
    page_count integer NOT NULL,
    rotation integer NOT NULL,
    storage_key character varying(400) NOT NULL,
    CONSTRAINT certificate_part_binding CHECK (((storage_key)::text = (((((((('certificate/'::text) || (certificate_id)::text) || '/'::text) || (certificate_revision_id)::text) || '/parts/'::text) || (ordinal)::text) || '-'::text) || (sha256)::text))),
    CONSTRAINT certificate_part_hash CHECK (((sha256)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT certificate_part_name CHECK (((btrim((name)::text) <> ''::text) AND (POSITION(('/'::text) IN (name)) = 0) AND (POSITION((chr(92)) IN (name)) = 0) AND ((name)::text !~ '[[:cntrl:]]'::text))),
    CONSTRAINT certificate_part_ordinal CHECK (((ordinal >= 1) AND (ordinal <= 20))),
    CONSTRAINT certificate_part_pages CHECK ((((page_start >= 1) AND (page_start <= 200)) AND ((page_count >= 1) AND (page_count <= 200)) AND (((page_start + page_count) - 1) <= 200))),
    CONSTRAINT certificate_part_rotation CHECK ((rotation = ANY (ARRAY[0, 90, 180, 270]))),
    CONSTRAINT certificate_part_size CHECK (((size_bytes >= 1) AND (size_bytes <= 41943040))),
    CONSTRAINT certificate_part_type CHECK (((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'image/png'::character varying, 'image/jpeg'::character varying])::text[])))
);

-- TABLE: certificate_files
CREATE TABLE public.certificate_files (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    certificate_id uuid NOT NULL,
    certificate_revision_id uuid NOT NULL,
    file jsonb NOT NULL,
    storage_key character varying(400) NOT NULL,
    CONSTRAINT certificate_file_binding CHECK (((storage_key)::text = ((((((('certificate/'::text) || (certificate_id)::text) || '/'::text) || (certificate_revision_id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.pdf'::text))),
    CONSTRAINT certificate_file_hash CHECK ((((file ->> 'sha256'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'sha256'::text)) = 'string'::text) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT certificate_file_name CHECK ((((file ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((file ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((file ->> 'name'::text))) = 0) AND ((file ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND (lower("right"((file ->> 'name'::text), 4)) = '.pdf'::text))),
    CONSTRAINT certificate_file_object CHECK ((jsonb_typeof(file) = 'object'::text)),
    CONSTRAINT certificate_file_pages CHECK ((((file ->> 'page_count'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'page_count'::text)) = 'number'::text) AND ((((file ->> 'page_count'::text))::numeric >= (1)::numeric) AND (((file ->> 'page_count'::text))::numeric <= (200)::numeric)) AND (((file ->> 'page_count'::text))::numeric = trunc(((file ->> 'page_count'::text))::numeric)))),
    CONSTRAINT certificate_file_size CHECK ((((file ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric)))),
    CONSTRAINT certificate_file_type CHECK ((((file ->> 'media_type'::text) IS NOT NULL) AND ((file ->> 'media_type'::text) = 'application/pdf'::text)))
);

-- TABLE: certificate_revisions
CREATE TABLE public.certificate_revisions (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    certificate_id uuid NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    CONSTRAINT certificate_declared_dates CHECK (((((data ->> 'valid_from'::text) IS NULL) OR (((data ->> 'valid_from'::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text) AND (((data ->> 'valid_from'::text))::date IS NOT NULL))) AND (((data ->> 'valid_until'::text) IS NULL) OR (((data ->> 'valid_until'::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text) AND (((data ->> 'valid_until'::text))::date IS NOT NULL))) AND (((data ->> 'valid_from'::text) IS NULL) OR ((data ->> 'valid_until'::text) IS NULL) OR (((data ->> 'valid_from'::text))::date <= ((data ->> 'valid_until'::text))::date)))),
    CONSTRAINT certificate_declared_identity CHECK ((((data ->> 'name'::text) IS NOT NULL) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200)) AND ((data ->> 'number'::text) IS NOT NULL) AND ((length(btrim((data ->> 'number'::text))) >= 1) AND (length(btrim((data ->> 'number'::text))) <= 200)))),
    CONSTRAINT certificate_declared_kind CHECK ((((data ->> 'kind'::text) IS NOT NULL) AND ((data ->> 'kind'::text) = ANY (ARRAY['qualification'::text, 'personnel'::text])))),
    CONSTRAINT certificate_revision_number_positive CHECK ((revision > 0)),
    CONSTRAINT certificate_revision_object CHECK ((jsonb_typeof(data) = 'object'::text))
);

-- TABLE: certificates
CREATE TABLE public.certificates (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    current_revision integer NOT NULL,
    lifecycle_state character varying(8) DEFAULT 'active'::character varying NOT NULL,
    lifecycle_revision integer DEFAULT 0 NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    CONSTRAINT certificate_lifecycle_valid CHECK ((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text)))),
    CONSTRAINT certificate_revision_positive CHECK ((current_revision > 0))
);

-- TABLE: check_certificate_items
CREATE TABLE public.check_certificate_items (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    certificate_id uuid NOT NULL,
    check_item_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: check_certificates
CREATE TABLE public.check_certificates (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    task_certificate_id uuid NOT NULL,
    certificate_revision_id uuid NOT NULL,
    assessment_date date NOT NULL,
    date_status character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT check_certificate_state CHECK (((date_status)::text = ANY ((ARRAY['valid'::character varying, 'expired'::character varying, 'not_yet_valid'::character varying, 'unknown'::character varying])::text[])))
);

-- TABLE: check_decisions
CREATE TABLE public.check_decisions (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    finding_id uuid NOT NULL,
    revision integer NOT NULL,
    action character varying(20) NOT NULL,
    reason text NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT check_decision_action CHECK ((((action)::text = ANY ((ARRAY['dismiss'::character varying, 'reopen'::character varying])::text[])) AND (revision >= 2) AND ((actor_kind)::text = 'session'::text))),
    CONSTRAINT check_decision_reason CHECK ((((length(reason) >= 1) AND (length(reason) <= 20000)) AND (reason ~ '[^[:space:]]'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((expected_input_hash)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: check_finding_citations
CREATE TABLE public.check_finding_citations (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    finding_id uuid,
    kind character varying(20) NOT NULL,
    quote text NOT NULL,
    document_id uuid,
    chunk_id uuid,
    draft_id uuid,
    response_item_id uuid,
    card_revision_id uuid,
    field character varying(20),
    evidence_id uuid,
    source jsonb,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    check_item_id uuid,
    CONSTRAINT check_citation_one_source CHECK (((((kind)::text = 'tender'::text) AND (document_id IS NOT NULL) AND (chunk_id IS NOT NULL) AND (source IS NOT NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL) AND (evidence_id IS NULL)) OR (((kind)::text = 'draft'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NOT NULL) AND (response_item_id IS NOT NULL) AND (card_revision_id IS NOT NULL) AND (field IS NOT NULL) AND ((field)::text = ANY ((ARRAY['response_text'::character varying, 'deviation_note'::character varying])::text[])) AND (evidence_id IS NULL)) OR (((kind)::text = 'evidence'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL) AND (evidence_id IS NOT NULL)))),
    CONSTRAINT check_citation_one_target CHECK ((num_nonnulls(finding_id, check_item_id) = 1)),
    CONSTRAINT check_citation_quote CHECK ((((length(quote) >= 1) AND (length(quote) <= 20000)) AND (POSITION(('{{secret.'::text) IN (quote)) = 0) AND (POSITION(('[REDACTED_'::text) IN (quote)) = 0)))
);

-- TABLE: check_findings
CREATE TABLE public.check_findings (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    check_item_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    method character varying(20) NOT NULL,
    code character varying(100) NOT NULL,
    severity character varying(30) NOT NULL,
    review_domain character varying(20),
    reason text NOT NULL,
    source jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT check_finding_domain CHECK (((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[]))),
    CONSTRAINT check_finding_kind CHECK (((((method)::text = 'deterministic'::text) AND ((code)::text = ANY ((ARRAY['mandatory_response_missing'::character varying, 'negative_deviation'::character varying, 'unconfirmed_evidence'::character varying, 'certificate_expired'::character varying, 'certificate_not_yet_valid'::character varying, 'certificate_date_unknown'::character varying])::text[]))) OR (((method)::text = 'semantic'::text) AND ((code)::text = ANY ((ARRAY['semantic_contradiction'::character varying, 'insufficient_support'::character varying, 'obligation_coverage_uncertain'::character varying])::text[]))))),
    CONSTRAINT check_finding_reason CHECK ((((length(reason) >= 1) AND (length(reason) <= 20000)) AND (reason ~ '[^[:space:]]'::text))),
    CONSTRAINT check_finding_severity CHECK (((severity)::text = ANY ((ARRAY['disqualification_risk'::character varying, 'deduction_risk'::character varying, 'info'::character varying])::text[])))
);

-- TABLE: check_items
CREATE TABLE public.check_items (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    response_item_id uuid NOT NULL,
    card_revision_id uuid,
    partition character varying(20) NOT NULL,
    source jsonb NOT NULL,
    rules jsonb NOT NULL,
    semantic_status character varying(20) NOT NULL,
    semantic_reason_code character varying(100),
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    semantic_outcome character varying(20),
    CONSTRAINT check_item_partition CHECK (((partition)::text = ANY ((ARRAY['response'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[]))),
    CONSTRAINT check_item_rules CHECK ((jsonb_typeof(rules) = 'array'::text)),
    CONSTRAINT check_item_semantic CHECK (((((semantic_status)::text = 'not_requested'::text) AND (semantic_outcome IS NULL) AND (semantic_reason_code IS NULL)) OR (((semantic_status)::text = 'assessed'::text) AND (semantic_outcome IS NOT NULL) AND ((semantic_outcome)::text = ANY ((ARRAY['no_risk_found'::character varying, 'risk'::character varying])::text[])) AND (semantic_reason_code IS NULL)) OR (((semantic_status)::text = 'unassessed'::text) AND (semantic_outcome IS NOT NULL) AND ((semantic_outcome)::text = 'unknown'::text) AND (semantic_reason_code IS NOT NULL) AND ((semantic_reason_code)::text = 'semantic_unknown'::text)) OR (((semantic_status)::text = 'unassessed'::text) AND (semantic_outcome IS NULL) AND (semantic_reason_code IS NOT NULL) AND ((semantic_reason_code)::text = ANY ((ARRAY['missing_requirement_id'::character varying, 'duplicate_requirement_id'::character varying, 'unknown_requirement_id'::character varying, 'cross_requirement_citation'::character varying, 'ref_not_sent'::character varying, 'quote_not_at_position'::character varying, 'ambiguous_quote'::character varying, 'redacted_input_unassessable'::character varying, 'sensitive_model_output'::character varying, 'unknown_placeholder'::character varying, 'insufficient_citations'::character varying, 'invalid_semantic_answer'::character varying, 'check_finding_limit'::character varying, 'semantic_provider_failed'::character varying])::text[])))))
);

-- TABLE: check_runs
CREATE TABLE public.check_runs (
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    document_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    draft_input_hash character varying(64) NOT NULL,
    assessment_date date NOT NULL,
    mode character varying(20) NOT NULL,
    rule_version character varying(100) NOT NULL,
    prompt_version character varying(100),
    schema_version character varying(100) NOT NULL,
    input_manifest jsonb NOT NULL,
    encrypted_input text NOT NULL,
    completion character varying(20) NOT NULL,
    limitations jsonb NOT NULL,
    summary jsonb NOT NULL,
    actor_token_id uuid,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT check_run_data CHECK (((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text) AND (jsonb_typeof(limitations) = 'array'::text) AND (length(encrypted_input) > 0))),
    CONSTRAINT check_run_hashes CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((draft_input_hash)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT check_run_mode CHECK (((((mode)::text = 'rules'::text) AND (prompt_version IS NULL)) OR (((mode)::text = 'combined'::text) AND (prompt_version IS NOT NULL) AND ((prompt_version)::text ~ '[^[:space:]]'::text)))),
    CONSTRAINT check_run_state CHECK ((((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])) AND ((actor_kind)::text = 'worker'::text)))
);

-- TABLE: chunks
CREATE TABLE public.chunks (
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    page integer,
    text text NOT NULL,
    ocr boolean NOT NULL,
    citation_verified boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    seq integer NOT NULL,
    blocks jsonb,
    CONSTRAINT chunk_page_positive CHECK ((page > 0)),
    CONSTRAINT chunk_position CHECK ((((page IS NOT NULL) AND (blocks IS NULL)) OR ((page IS NULL) AND (jsonb_typeof(blocks) = 'array'::text) AND (jsonb_array_length(blocks) > 0)))),
    CONSTRAINT chunk_seq_positive CHECK ((seq > 0))
);

-- TABLE: confidential_field_search_tokens
CREATE TABLE public.confidential_field_search_tokens (
    token text NOT NULL COLLATE pg_catalog."C",
    field_id uuid NOT NULL
);

-- TABLE: confidential_fields
CREATE TABLE public.confidential_fields (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    key character varying(48) NOT NULL,
    label character varying(100) NOT NULL,
    kind character varying(20) NOT NULL,
    scope character varying(10) NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (public.management_confidential_vector((key)::text, (label)::text)) STORED,
    CONSTRAINT confidential_field_key CHECK (((key)::text ~ '^[a-z][a-z0-9_]{1,47}$'::text)),
    CONSTRAINT confidential_field_kind CHECK (((kind)::text = ANY ((ARRAY['amount'::character varying, 'contact'::character varying, 'identity'::character varying, 'bank_account'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT confidential_field_label CHECK ((btrim((label)::text) <> ''::text)),
    CONSTRAINT confidential_field_revision CHECK ((revision >= 1)),
    CONSTRAINT confidential_field_scope CHECK (((scope)::text = ANY ((ARRAY['library'::character varying, 'task'::character varying])::text[])))
);

-- TABLE: confidential_values
CREATE TABLE public.confidential_values (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    field_id uuid NOT NULL,
    task_id uuid,
    version integer NOT NULL,
    encrypted_value text NOT NULL,
    tail character varying(4),
    CONSTRAINT confidential_value_version CHECK ((version >= 1))
);

-- TABLE: documents
CREATE TABLE public.documents (
    task_id uuid NOT NULL,
    name character varying(200) NOT NULL,
    sha256 character varying(64) NOT NULL,
    storage_key text NOT NULL,
    media_type character varying(100) NOT NULL,
    page_count integer,
    status character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    citation_mode character varying(10),
    CONSTRAINT documents_citation_mode_check CHECK (((citation_mode)::text = ANY ((ARRAY['page'::character varying, 'block'::character varying])::text[])))
);

-- TABLE: draft_runs
CREATE TABLE public.draft_runs (
    completion character varying(20) NOT NULL,
    summary jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    generation_job_id uuid NOT NULL,
    generation_run_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    actor_token_id uuid,
    actor_kind character varying(20) NOT NULL,
    input_manifest jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT draft_runs_gate_0 CHECK (((input_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT draft_runs_gate_1 CHECK (((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[]))),
    CONSTRAINT draft_runs_gate_2 CHECK (((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text))),
    CONSTRAINT draft_runs_gate_3 CHECK (((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[])))
);

-- TABLE: evidence
CREATE TABLE public.evidence (
    task_id uuid NOT NULL,
    card_id uuid NOT NULL,
    kind character varying(30) NOT NULL,
    task_resource_id uuid,
    product_revision_id uuid,
    task_feature_id uuid,
    feature_revision_id uuid,
    task_certificate_id uuid,
    certificate_revision_id uuid,
    task_org_profile_id uuid,
    profile_revision_id uuid,
    evidence_source_id uuid,
    field_path character varying(200),
    quote text,
    material_kind character varying(40) NOT NULL,
    quote_check character varying(40) NOT NULL,
    source_sha256 character varying(64),
    page integer,
    confirmed_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    screenshot_asset_id uuid,
    screenshot_rendition_id uuid,
    image_sha256 character varying(64),
    region jsonb,
    claim_scope character varying(40),
    visual_observation text,
    CONSTRAINT evidence_gate_0 CHECK (((kind)::text = ANY ((ARRAY['product'::character varying, 'feature'::character varying, 'certificate'::character varying, 'org_profile'::character varying, 'certificate_pdf_page'::character varying, 'image_region'::character varying])::text[]))),
    CONSTRAINT evidence_gate_1 CHECK (((((kind)::text = 'image_region'::text) AND (quote IS NULL)) OR (((kind)::text <> 'image_region'::text) AND (quote IS NOT NULL) AND (length(quote) <= 20000) AND (quote ~ '[^[:space:]]'::text)))),
    CONSTRAINT evidence_gate_10 CHECK ((((((kind)::text = 'certificate_pdf_page'::text) AND (evidence_source_id IS NOT NULL) AND (field_path IS NULL) AND (page IS NOT NULL) AND (page > 0) AND (source_sha256 IS NOT NULL) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((material_kind)::text = 'user_supplied_pdf_page'::text) AND ((quote_check)::text = ANY ((ARRAY['unreviewed_page'::character varying, 'human_page_review'::character varying])::text[])) AND (screenshot_asset_id IS NULL) AND (screenshot_rendition_id IS NULL) AND (image_sha256 IS NULL) AND (region IS NULL) AND (claim_scope IS NULL) AND (visual_observation IS NULL)) OR (((kind)::text = ANY ((ARRAY['product'::character varying, 'feature'::character varying, 'certificate'::character varying, 'org_profile'::character varying])::text[])) AND (evidence_source_id IS NULL) AND (field_path IS NOT NULL) AND ((field_path)::text ~ '[^[:space:]]'::text) AND (page IS NULL) AND (source_sha256 IS NULL) AND ((material_kind)::text = 'declaration'::text) AND ((quote_check)::text = 'exact_field_match'::text) AND (screenshot_asset_id IS NULL) AND (screenshot_rendition_id IS NULL) AND (image_sha256 IS NULL) AND (region IS NULL) AND (claim_scope IS NULL) AND (visual_observation IS NULL)) OR (((kind)::text = 'image_region'::text) AND (field_path IS NULL) AND (screenshot_asset_id IS NOT NULL) AND (screenshot_rendition_id IS NOT NULL) AND (image_sha256 IS NOT NULL) AND ((image_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (source_sha256 IS NOT NULL) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((((material_kind)::text = 'certificate_image'::text) AND (evidence_source_id IS NOT NULL) AND (page IS NOT NULL) AND (page > 0)) OR (((material_kind)::text <> 'certificate_image'::text) AND (evidence_source_id IS NULL) AND (page IS NULL))) AND (region IS NOT NULL) AND (jsonb_typeof(region) = 'object'::text) AND (region ?& ARRAY['x'::text, 'y'::text, 'width'::text, 'height'::text]) AND ((region - ARRAY['x'::text, 'y'::text, 'width'::text, 'height'::text]) = '{}'::jsonb) AND ((region ->> 'x'::text) ~ '^[0-9]+$'::text) AND ((region ->> 'y'::text) ~ '^[0-9]+$'::text) AND ((region ->> 'width'::text) ~ '^[1-9][0-9]*$'::text) AND ((region ->> 'height'::text) ~ '^[1-9][0-9]*$'::text) AND (((region ->> 'x'::text))::integer <= 8192) AND (((region ->> 'y'::text))::integer <= 8192) AND (((region ->> 'width'::text))::integer <= 8192) AND (((region ->> 'height'::text))::integer <= 8192) AND (claim_scope IS NOT NULL) AND ((claim_scope)::text = ANY ((ARRAY['functional_observation'::character varying, 'design_explanation'::character varying, 'document_excerpt'::character varying, 'hardware_documentation'::character varying])::text[])) AND (visual_observation IS NOT NULL) AND (length(visual_observation) <= 4000) AND (visual_observation ~ '[^[:space:]]'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'user_diagram'::character varying, 'certificate_image'::character varying, 'vendor_web'::character varying, 'vendor_pdf'::character varying, 'prototype'::character varying])::text[])) AND ((quote_check)::text = ANY ((ARRAY['unreviewed_image'::character varying, 'human_image_review'::character varying])::text[])) AND ((((claim_scope)::text = 'functional_observation'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'prototype'::character varying])::text[]))) OR (((claim_scope)::text = 'design_explanation'::text) AND ((material_kind)::text = 'user_diagram'::text)) OR (((claim_scope)::text = 'document_excerpt'::text) AND ((material_kind)::text = 'certificate_image'::text)) OR (((claim_scope)::text = 'hardware_documentation'::text) AND ((material_kind)::text = ANY ((ARRAY['vendor_web'::character varying, 'vendor_pdf'::character varying])::text[])))))) AND (((quote_check)::text <> ALL ((ARRAY['human_page_review'::character varying, 'human_image_review'::character varying])::text[])) OR (confirmed_at IS NOT NULL)) AND ((confirmed_at IS NULL) OR ((quote_check)::text <> ALL ((ARRAY['unreviewed_page'::character varying, 'unreviewed_image'::character varying])::text[]))))),
    CONSTRAINT evidence_gate_3 CHECK (((((kind)::text = 'product'::text) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = ANY ((ARRAY['vendor_web'::character varying, 'vendor_pdf'::character varying])::text[])))) = ((task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL)))),
    CONSTRAINT evidence_gate_4 CHECK (((task_resource_id IS NULL) = (product_revision_id IS NULL))),
    CONSTRAINT evidence_gate_5 CHECK (((((kind)::text = 'feature'::text) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'user_diagram'::character varying, 'prototype'::character varying])::text[])))) = ((task_feature_id IS NOT NULL) AND (feature_revision_id IS NOT NULL)))),
    CONSTRAINT evidence_gate_6 CHECK (((task_feature_id IS NULL) = (feature_revision_id IS NULL))),
    CONSTRAINT evidence_gate_7 CHECK (((((kind)::text = ANY ((ARRAY['certificate'::character varying, 'certificate_pdf_page'::character varying])::text[])) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = 'certificate_image'::text))) = ((task_certificate_id IS NOT NULL) AND (certificate_revision_id IS NOT NULL)))),
    CONSTRAINT evidence_gate_8 CHECK (((task_certificate_id IS NULL) = (certificate_revision_id IS NULL))),
    CONSTRAINT evidence_gate_9 CHECK (((((kind)::text = 'org_profile'::text) = ((task_org_profile_id IS NOT NULL) AND (profile_revision_id IS NOT NULL))) AND ((task_org_profile_id IS NULL) = (profile_revision_id IS NULL))))
);

-- TABLE: evidence_sources
CREATE TABLE public.evidence_sources (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    task_certificate_id uuid,
    certificate_id uuid,
    certificate_revision_id uuid,
    certificate_file_id uuid,
    page integer NOT NULL,
    render_profile character varying(40) NOT NULL,
    dpi integer NOT NULL,
    preview jsonb NOT NULL,
    storage_key character varying(400) NOT NULL,
    rendered_at timestamp with time zone NOT NULL,
    status character varying(40) NOT NULL,
    eligible_for_draft_export boolean NOT NULL,
    source_kind character varying(40) DEFAULT 'user_supplied_certificate_pdf'::character varying NOT NULL,
    task_attachment_id uuid,
    task_org_profile_id uuid,
    profile_revision_id uuid,
    profile_attachment_link_id uuid,
    attachment_id uuid,
    attachment_revision_id uuid,
    attachment_file_id uuid,
    approval_id uuid,
    confirmed_at timestamp with time zone,
    CONSTRAINT attachment_source_branches CHECK (((((source_kind)::text = 'user_supplied_certificate_pdf'::text) AND (num_nonnulls(task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) = 4) AND (num_nonnulls(task_attachment_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, attachment_file_id, approval_id) = 0)) OR (((source_kind)::text = 'user_supplied_attachment_pdf'::text) AND (num_nonnulls(task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) = 0) AND (num_nonnulls(task_attachment_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, attachment_file_id, approval_id) = 8)))),
    CONSTRAINT source_page CHECK (((page >= 1) AND (page <= 200))),
    CONSTRAINT source_preview_binding CHECK (((storage_key)::text = ((((('evidence-source/'::text) || (id)::text) || '/'::text) || (preview ->> 'sha256'::text)) || '.png'::text))),
    CONSTRAINT source_preview_dimensions CHECK ((((preview ->> 'width_px'::text) IS NOT NULL) AND ((preview ->> 'height_px'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'width_px'::text)) = 'number'::text) AND (jsonb_typeof((preview -> 'height_px'::text)) = 'number'::text) AND ((((preview ->> 'width_px'::text))::numeric >= (1)::numeric) AND (((preview ->> 'width_px'::text))::numeric <= (8192)::numeric)) AND ((((preview ->> 'height_px'::text))::numeric >= (1)::numeric) AND (((preview ->> 'height_px'::text))::numeric <= (8192)::numeric)) AND (((preview ->> 'width_px'::text))::numeric = trunc(((preview ->> 'width_px'::text))::numeric)) AND (((preview ->> 'height_px'::text))::numeric = trunc(((preview ->> 'height_px'::text))::numeric)) AND ((((preview ->> 'width_px'::text))::numeric * ((preview ->> 'height_px'::text))::numeric) <= (20000000)::numeric))),
    CONSTRAINT source_preview_hash CHECK ((((preview ->> 'sha256'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'sha256'::text)) = 'string'::text) AND ((preview ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT source_preview_media CHECK ((((preview ->> 'media_type'::text) IS NOT NULL) AND ((preview ->> 'media_type'::text) = 'image/png'::text))),
    CONSTRAINT source_preview_name CHECK ((((preview ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'name'::text)) = 'string'::text) AND ((length(btrim((preview ->> 'name'::text))) >= 1) AND (length(btrim((preview ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((preview ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((preview ->> 'name'::text))) = 0) AND ((preview ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND (lower("right"((preview ->> 'name'::text), 4)) = '.png'::text))),
    CONSTRAINT source_preview_object CHECK ((jsonb_typeof(preview) = 'object'::text)),
    CONSTRAINT source_preview_size CHECK ((((preview ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'size_bytes'::text)) = 'number'::text) AND ((((preview ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((preview ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((preview ->> 'size_bytes'::text))::numeric = trunc(((preview ->> 'size_bytes'::text))::numeric)))),
    CONSTRAINT source_profile CHECK ((((render_profile)::text = 'pdf-page-preview-v1'::text) AND (dpi = 150))),
    CONSTRAINT source_unconfirmed CHECK ((((status)::text = 'unconfirmed_source'::text) AND (confirmed_at IS NULL) AND (eligible_for_draft_export = false)))
);

-- TABLE: export_render_candidates
CREATE TABLE public.export_render_candidates (
    run_id uuid NOT NULL,
    render_job_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    plaintext_sha256 character varying(64) NOT NULL,
    size_bytes bigint NOT NULL,
    object_key character varying(500) NOT NULL,
    renderer_profile character varying(200) NOT NULL,
    manifest_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT export_render_candidates_check_0 CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((plaintext_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT export_render_candidates_check_1 CHECK (((size_bytes >= 1) AND (size_bytes <= 536870912))),
    CONSTRAINT export_render_candidates_check_2 CHECK (((object_key)::text = ((((((('export-candidates/'::text) || (run_id)::text) || '/'::text) || (attempt_id)::text) || '/'::text) || (plaintext_sha256)::text) || '.docx'::text)))
);

-- TABLE: export_run_evidence
CREATE TABLE public.export_run_evidence (
    run_id uuid NOT NULL,
    run_item_id uuid NOT NULL,
    card_revision_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    attachment_ordinal integer,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    prototype_decision_id uuid,
    CONSTRAINT export_run_evidence_check_0 CHECK (((attachment_ordinal IS NULL) OR (attachment_ordinal > 0)))
);

-- TABLE: export_run_items
CREATE TABLE public.export_run_items (
    run_id uuid NOT NULL,
    draft_run_id uuid NOT NULL,
    response_item_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    card_revision_id uuid,
    kind character varying(20) NOT NULL,
    ordinal integer NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT export_run_items_check_0 CHECK (((kind)::text = ANY ((ARRAY['row'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[]))),
    CONSTRAINT export_run_items_check_1 CHECK ((ordinal >= 0))
);

-- TABLE: export_runs
CREATE TABLE public.export_runs (
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    document_id uuid NOT NULL,
    draft_run_id uuid NOT NULL,
    task_template_id uuid NOT NULL,
    template_revision_id uuid NOT NULL,
    binding_id uuid NOT NULL,
    render_job_id uuid NOT NULL,
    mode character varying(20) NOT NULL,
    input_hash character varying(64) NOT NULL,
    manifest_hash character varying(64) NOT NULL,
    renderer_profile character varying(200) NOT NULL,
    manifest jsonb NOT NULL,
    issue_snapshot jsonb NOT NULL,
    acknowledged_issue_ids jsonb NOT NULL,
    initiated_at timestamp with time zone NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT export_runs_check_0 CHECK (((mode)::text = ANY ((ARRAY['final_section'::character varying, 'review_copy'::character varying])::text[]))),
    CONSTRAINT export_runs_check_1 CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT export_runs_check_2 CHECK (((jsonb_typeof(manifest) = 'object'::text) AND (jsonb_typeof(issue_snapshot) = 'array'::text) AND (jsonb_typeof(acknowledged_issue_ids) = 'array'::text)))
);

-- TABLE: export_template_bindings
CREATE TABLE public.export_template_bindings (
    template_revision_id uuid NOT NULL,
    template_sha256 character varying(64) NOT NULL,
    binding_hash character varying(64) NOT NULL,
    sections jsonb NOT NULL,
    static_content_hash character varying(64) NOT NULL,
    adapter_version character varying(100) NOT NULL,
    reviewed_at timestamp with time zone NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT export_template_bindings_check_0 CHECK ((((template_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((binding_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((static_content_hash)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT export_template_bindings_check_1 CHECK (((jsonb_typeof(sections) = 'array'::text) AND (jsonb_array_length(sections) = 6)))
);

-- TABLE: exports
CREATE TABLE public.exports (
    run_id uuid NOT NULL,
    candidate_id uuid NOT NULL,
    task_id uuid NOT NULL,
    mode character varying(20) NOT NULL,
    input_hash character varying(64) NOT NULL,
    manifest_hash character varying(64) NOT NULL,
    file_sha256 character varying(64) NOT NULL,
    size_bytes bigint NOT NULL,
    media_type character varying(100) NOT NULL,
    object_key character varying(500) NOT NULL,
    released_at timestamp with time zone NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT exports_check_0 CHECK (((mode)::text = ANY ((ARRAY['final_section'::character varying, 'review_copy'::character varying])::text[]))),
    CONSTRAINT exports_check_1 CHECK ((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((file_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT exports_check_2 CHECK (((size_bytes >= 1) AND (size_bytes <= 536870912))),
    CONSTRAINT exports_check_3 CHECK (((media_type)::text = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::text)),
    CONSTRAINT exports_check_4 CHECK (((object_key)::text = ((((('exports/'::text) || (id)::text) || '/'::text) || (file_sha256)::text) || '.docx'::text)))
);

-- TABLE: feature_revisions
CREATE TABLE public.feature_revisions (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    feature_id uuid NOT NULL,
    product_id uuid NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    CONSTRAINT feature_declared_status CHECK ((((data ->> 'status'::text) IS NOT NULL) AND ((data ->> 'status'::text) = ANY (ARRAY['implemented'::text, 'developing'::text, 'planned'::text])))),
    CONSTRAINT feature_product_matches CHECK ((((data ->> 'product_id'::text) IS NOT NULL) AND ((data ->> 'product_id'::text) = (product_id)::text))),
    CONSTRAINT feature_revision_number_positive CHECK ((revision > 0)),
    CONSTRAINT feature_revision_object CHECK ((jsonb_typeof(data) = 'object'::text))
);

-- TABLE: features
CREATE TABLE public.features (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    current_revision integer NOT NULL,
    lifecycle_state character varying(8) DEFAULT 'active'::character varying NOT NULL,
    lifecycle_revision integer DEFAULT 0 NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    CONSTRAINT feature_lifecycle_valid CHECK ((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text)))),
    CONSTRAINT feature_revision_positive CHECK ((current_revision > 0))
);

-- TABLE: jobs
CREATE TABLE public.jobs (
    task_id uuid,
    document_id uuid,
    kind character varying(20) NOT NULL,
    cache_key character varying(64) NOT NULL,
    status character varying(20) NOT NULL,
    queue_id integer,
    attempts integer NOT NULL,
    lease_until timestamp with time zone,
    result jsonb NOT NULL,
    error jsonb,
    finished_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    run_id uuid,
    reasoning character varying(20),
    provider_config_id uuid,
    provider_identity jsonb,
    actor_token_id uuid,
    actor_kind character varying(20),
    actor_scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    initiated_by character varying(30),
    agent_principal_id uuid,
    agent_session_id uuid,
    agent_step_id uuid,
    invocation_id uuid,
    command character varying(100),
    bid_submission_document_id uuid,
    CONSTRAINT annotation_job_kind CHECK ((((kind)::text <> ALL ((ARRAY['annotation_render'::character varying, 'annotation_release'::character varying])::text[])) OR ((task_id IS NOT NULL) AND (document_id IS NOT NULL) AND ((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL)))),
    CONSTRAINT bid_review_prepare_actor CHECK ((((kind)::text <> 'bid_review_prepare'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND (agent_principal_id IS NULL)))),
    CONSTRAINT bid_review_report_actor CHECK ((((kind)::text <> 'bid_review_report'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND (agent_principal_id IS NULL)))),
    CONSTRAINT bid_review_uploaded_document CHECK ((((kind)::text = ANY ((ARRAY['bid_review'::character varying, 'bid_review_report'::character varying])::text[])) = (bid_submission_document_id IS NOT NULL))),
    CONSTRAINT job_actor_shape CHECK (((jsonb_typeof(actor_scopes) = 'array'::text) AND ((actor_kind IS NULL) OR ((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[]))) AND ((actor_token_id IS NULL) OR ((actor_kind)::text <> 'session'::text)))),
    CONSTRAINT job_document_binding CHECK (((((kind)::text = 'provider_test'::text) AND (task_id IS NULL) AND (document_id IS NULL)) OR (((kind)::text = 'bid_review_prepare'::text) AND (task_id IS NOT NULL) AND (document_id IS NULL)) OR (((kind)::text = ANY ((ARRAY['bid_review'::character varying, 'bid_review_report'::character varying])::text[])) AND (task_id IS NOT NULL) AND (document_id IS NULL) AND (bid_submission_document_id IS NOT NULL)) OR (((kind)::text <> ALL ((ARRAY['provider_test'::character varying, 'bid_review_prepare'::character varying, 'bid_review'::character varying, 'bid_review_report'::character varying])::text[])) AND (task_id IS NOT NULL) AND (document_id IS NOT NULL)))),
    CONSTRAINT job_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT jobs_agent_origin_binding CHECK ((((initiated_by)::text = 'builtin_agent'::text) AND (agent_principal_id IS NOT NULL) AND (agent_session_id IS NOT NULL) AND (actor_token_id IS NULL)) OR (((initiated_by)::text = 'external_agent'::text) AND (actor_token_id IS NOT NULL) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)) OR (((COALESCE(initiated_by, 'legacy_unknown'::character varying))::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying])::text[])) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL))),
    CONSTRAINT jobs_agent_origin_shape CHECK (((initiated_by IS NULL) OR ((initiated_by)::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying, 'builtin_agent'::character varying, 'external_agent'::character varying])::text[]))))
);

-- TABLE: memories
CREATE TABLE public.memories (
    scope character varying(20) NOT NULL,
    task_id uuid,
    current_revision_id uuid NOT NULL,
    revision integer NOT NULL,
    deleted_at timestamp with time zone,
    source_feedback_event_id uuid,
    generator_version character varying(100),
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    search_tags text[] DEFAULT '{}'::text[] NOT NULL,
    search_kind text,
    search_status text,
    search_expires_at timestamp with time zone,
    CONSTRAINT memories_check CHECK ((((scope)::text = 'library'::text) AND (task_id IS NULL))),
    CONSTRAINT memories_check1 CHECK (((source_feedback_event_id IS NULL) = (generator_version IS NULL))),
    CONSTRAINT memories_revision_check CHECK ((revision >= 1))
);

-- TABLE: memory_call_inputs
CREATE TABLE public.memory_call_inputs (
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    call_id uuid NOT NULL,
    retrieval_id uuid NOT NULL,
    requirement_ids jsonb NOT NULL,
    memories jsonb NOT NULL,
    manifest_sha256 character varying(64) NOT NULL,
    prompt_sha256 character varying(64) NOT NULL,
    prompt_version character varying(100) NOT NULL,
    encrypted_prompt text NOT NULL,
    state character varying(20) NOT NULL,
    usage_record_id uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_call_inputs_check CHECK ((((state)::text = 'completed'::text) = (usage_record_id IS NOT NULL))),
    CONSTRAINT memory_call_inputs_check1 CHECK ((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((prompt_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT memory_call_inputs_memories_check CHECK (((jsonb_typeof(memories) = 'array'::text) AND (jsonb_array_length(memories) <= 50))),
    CONSTRAINT memory_call_inputs_requirement_ids_check CHECK (((jsonb_typeof(requirement_ids) = 'array'::text) AND (jsonb_array_length(requirement_ids) > 0))),
    CONSTRAINT memory_call_inputs_state_check CHECK (((state)::text = ANY ((ARRAY['admitted'::character varying, 'completed'::character varying, 'unknown'::character varying])::text[])))
);

-- TABLE: memory_eval_samples
CREATE TABLE public.memory_eval_samples (
    task_id uuid NOT NULL,
    feedback_event_id uuid NOT NULL,
    card_id uuid NOT NULL,
    before_revision_id uuid NOT NULL,
    after_revision_id uuid NOT NULL,
    label character varying(30) NOT NULL,
    generator_version character varying(100) NOT NULL,
    sanitized_sha256 character varying(64) NOT NULL,
    encrypted_summary text NOT NULL,
    review_state character varying(20) NOT NULL,
    review_revision integer NOT NULL,
    reviewed_at timestamp with time zone,
    review_reason_sha256 character varying(64),
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_eval_samples_check1 CHECK ((((review_state)::text = 'unreviewed'::text) = (reviewed_at IS NULL))),
    CONSTRAINT memory_eval_samples_review_revision_check CHECK ((review_revision >= 1)),
    CONSTRAINT memory_eval_samples_review_state_check CHECK (((review_state)::text = ANY ((ARRAY['unreviewed'::character varying, 'accepted'::character varying, 'excluded'::character varying])::text[]))),
    CONSTRAINT memory_eval_samples_sanitized_sha256_check CHECK (((sanitized_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: memory_feedback_events
CREATE TABLE public.memory_feedback_events (
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    model_job_id uuid NOT NULL,
    card_id uuid NOT NULL,
    before_revision_id uuid NOT NULL,
    after_revision_id uuid NOT NULL,
    actor_kind character varying(20) NOT NULL,
    kind character varying(30) NOT NULL,
    review_domain character varying(20),
    encrypted_summary text NOT NULL,
    sanitized_sha256 character varying(64) NOT NULL,
    sanitizer_version character varying(100) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_feedback_events_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT memory_feedback_events_kind_check CHECK (((kind)::text = ANY ((ARRAY['card_rejected'::character varying, 'card_edited'::character varying, 'card_confirmed'::character varying])::text[]))),
    CONSTRAINT memory_feedback_events_sanitized_sha256_check CHECK (((sanitized_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: memory_retrieval_items
CREATE TABLE public.memory_retrieval_items (
    retrieval_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    memory_revision_id uuid NOT NULL,
    revision integer NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    sent_sha256 character varying(64) NOT NULL,
    rank integer NOT NULL,
    selected boolean NOT NULL,
    omission_reason character varying(40),
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_retrieval_items_check CHECK (((rank >= 1) AND (revision >= 1))),
    CONSTRAINT memory_retrieval_items_check1 CHECK ((selected = (omission_reason IS NULL))),
    CONSTRAINT memory_retrieval_items_check2 CHECK ((((content_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((sent_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT memory_retrieval_items_omission_reason_check CHECK (((omission_reason IS NULL) OR ((omission_reason)::text = ANY ((ARRAY['shadowed'::character varying, 'same_priority_conflict'::character varying, 'context_limit'::character varying, 'top_k_limit'::character varying])::text[]))))
);

-- TABLE: memory_retrievals
CREATE TABLE public.memory_retrievals (
    task_id uuid,
    actor_token_id uuid,
    data jsonb NOT NULL,
    encrypted_query text NOT NULL,
    encrypted_snapshot text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_retrievals_data_check CHECK ((jsonb_typeof(data) = 'object'::text))
);

-- TABLE: memory_revisions
CREATE TABLE public.memory_revisions (
    memory_id uuid NOT NULL,
    revision integer NOT NULL,
    content jsonb NOT NULL,
    normalized_text text NOT NULL,
    normalized_tags text[] NOT NULL,
    status character varying(20) NOT NULL,
    source jsonb NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    actor_kind character varying(20) NOT NULL,
    actor_token_id uuid,
    confirmed_at timestamp with time zone,
    decision character varying(20),
    decision_reason_sha256 character varying(64),
    expires_at timestamp with time zone,
    task_id uuid,
    card_id uuid,
    card_revision_id uuid,
    feedback_event_id uuid,
    proposal_job_id uuid,
    proposal_run_id uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memory_revisions_actor_kind_check CHECK (((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'worker'::character varying, 'agent'::character varying])::text[]))),
    CONSTRAINT memory_revisions_check1 CHECK ((((status)::text = 'active'::text) = (confirmed_at IS NOT NULL))),
    CONSTRAINT memory_revisions_check2 CHECK ((((status)::text <> 'active'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND ((decision)::text = 'approve'::text)))),
    CONSTRAINT memory_revisions_check3 CHECK ((((status)::text <> 'candidate'::text) OR (decision IS NULL))),
    CONSTRAINT memory_revisions_check4 CHECK (((decision IS NULL) = (decision_reason_sha256 IS NULL))),
    CONSTRAINT memory_revisions_check5 CHECK (((card_id IS NULL) = (card_revision_id IS NULL))),
    CONSTRAINT memory_revisions_check6 CHECK (((card_id IS NULL) OR (task_id IS NOT NULL))),
    CONSTRAINT memory_revisions_check7 CHECK (((proposal_job_id IS NULL) = (proposal_run_id IS NULL))),
    CONSTRAINT memory_revisions_content_check CHECK (((jsonb_typeof(content) = 'object'::text) AND COALESCE(((content ->> 'kind'::text) = ANY (ARRAY['rule'::text, 'preference'::text])), false) AND COALESCE(((content ->> 'conflict_key'::text) ~ '^[a-z0-9][a-z0-9_.-]{0,99}$'::text), false) AND COALESCE(((length((content ->> 'text'::text)) >= 1) AND (length((content ->> 'text'::text)) <= 2000)), false) AND COALESCE(((content ->> 'text'::text) ~ '[^[:space:]]'::text), false) AND COALESCE((jsonb_typeof((content -> 'tags'::text)) = 'array'::text), false) AND (jsonb_array_length((content -> 'tags'::text)) <= 20))),
    CONSTRAINT memory_revisions_content_sha256_check CHECK (((content_sha256)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT memory_revisions_decision_check CHECK (((decision IS NULL) OR ((decision)::text = ANY ((ARRAY['approve'::character varying, 'reject'::character varying, 'disable'::character varying, 'delete'::character varying])::text[])))),
    CONSTRAINT memory_revisions_decision_reason_sha256_check CHECK (((decision_reason_sha256 IS NULL) OR ((decision_reason_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT memory_revisions_revision_check CHECK ((revision >= 1)),
    CONSTRAINT memory_revisions_source_check CHECK (((jsonb_typeof(source) = 'object'::text) AND COALESCE(((source ->> 'origin'::text) = ANY (ARRAY['human'::text, 'system'::text])), false))),
    CONSTRAINT memory_revisions_status_check CHECK (((status)::text = ANY ((ARRAY['candidate'::character varying, 'active'::character varying, 'disabled'::character varying])::text[])))
);

-- TABLE: memory_scope_epochs
CREATE TABLE public.memory_scope_epochs (
    scope character varying(20) NOT NULL,
    epoch bigint NOT NULL,
    CONSTRAINT memory_scope_epochs_check CHECK ((((scope)::text = 'library'::text) AND (epoch >= 0)))
);

-- TABLE: org_profile_revisions
CREATE TABLE public.org_profile_revisions (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    profile_id uuid NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    CONSTRAINT profile_declared_name CHECK ((((data ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((data -> 'name'::text)) = 'string'::text) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200)))),
    CONSTRAINT profile_performance_summary_text CHECK ((((data ->> 'performance_summary'::text) IS NULL) OR ((jsonb_typeof((data -> 'performance_summary'::text)) = 'string'::text) AND ((length(btrim((data ->> 'performance_summary'::text))) >= 1) AND (length(btrim((data ->> 'performance_summary'::text))) <= 20000))))),
    CONSTRAINT profile_registration_details_text CHECK ((((data ->> 'registration_details'::text) IS NULL) OR ((jsonb_typeof((data -> 'registration_details'::text)) = 'string'::text) AND ((length(btrim((data ->> 'registration_details'::text))) >= 1) AND (length(btrim((data ->> 'registration_details'::text))) <= 10000))))),
    CONSTRAINT profile_revision_number_positive CHECK ((revision > 0)),
    CONSTRAINT profile_revision_object CHECK ((jsonb_typeof(data) = 'object'::text)),
    CONSTRAINT profile_standard_wording_text CHECK ((((data ->> 'standard_wording'::text) IS NULL) OR ((jsonb_typeof((data -> 'standard_wording'::text)) = 'string'::text) AND ((length(btrim((data ->> 'standard_wording'::text))) >= 1) AND (length(btrim((data ->> 'standard_wording'::text))) <= 20000)))))
);

-- TABLE: org_profiles
CREATE TABLE public.org_profiles (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    current_revision integer NOT NULL,
    lifecycle_state character varying(8) DEFAULT 'active'::character varying NOT NULL,
    lifecycle_revision integer DEFAULT 0 NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    CONSTRAINT profile_lifecycle_valid CHECK ((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text)))),
    CONSTRAINT profile_revision_positive CHECK ((current_revision > 0))
);

-- TABLE: platform_credentials
CREATE TABLE public.platform_credentials (
    id uuid NOT NULL,
    name character varying(40) NOT NULL,
    purpose character varying(30) NOT NULL,
    provider character varying(20) NOT NULL,
    endpoint character varying(300) NOT NULL,
    encrypted_key text,
    envelope_version integer DEFAULT 1 NOT NULL,
    fingerprint character varying(23) NOT NULL,
    last_four character varying(4) NOT NULL,
    state character varying(10) DEFAULT 'disabled'::character varying NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    secret_version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credential_cipher_state CHECK (((((state)::text = 'removed'::text) AND (encrypted_key IS NULL)) OR (((state)::text <> 'removed'::text) AND (encrypted_key IS NOT NULL) AND ((length(encrypted_key) >= 100) AND (length(encrypted_key) <= 16384))))),
    CONSTRAINT credential_provider_purpose CHECK (((((purpose)::text = 'vendor_search'::text) AND ((provider)::text = 'perplexity'::text) AND ((endpoint)::text = 'https://api.perplexity.ai'::text)) OR (((purpose)::text = ANY ((ARRAY['standalone_llm'::character varying])::text[])) AND ((provider)::text = ANY ((ARRAY['anthropic'::character varying, 'openai'::character varying])::text[]))) OR (((purpose)::text = 'clef_workers_ai'::text) AND ((provider)::text = 'cloudflare'::text) AND ((endpoint)::text = 'https://gateway.ai.cloudflare.com'::text)) OR (((purpose)::text = 'clef_gateway'::text) AND ((provider)::text = 'cloudflare'::text) AND ((endpoint)::text = 'https://api.cloudflare.com/client/v4'::text)))),
    CONSTRAINT platform_credentials_check CHECK (((secret_version >= 1) AND (secret_version <= revision))),
    CONSTRAINT platform_credentials_endpoint_check CHECK ((((endpoint)::text ~ '^https://(\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*)(/[^[:space:]?#%\\]*)?$'::text) AND ((endpoint)::text !~ '/$'::text) AND ((endpoint)::text !~ '[[:cntrl:]]'::text))),
    CONSTRAINT platform_credentials_envelope_version_check CHECK ((envelope_version = 1)),
    CONSTRAINT platform_credentials_fingerprint_check CHECK (((fingerprint)::text ~ '^sha256:[a-f0-9]{16}$'::text)),
    CONSTRAINT platform_credentials_last_four_check CHECK (((last_four)::text ~ '^[!-~]{4}$'::text)),
    CONSTRAINT platform_credentials_name_check CHECK (((name)::text ~ '^[a-z0-9_]{1,40}$'::text)),
    CONSTRAINT platform_credentials_provider_check CHECK (((provider)::text = ANY ((ARRAY['anthropic'::character varying, 'openai'::character varying, 'perplexity'::character varying, 'cloudflare'::character varying])::text[]))),
    CONSTRAINT platform_credentials_purpose_check CHECK (((purpose)::text = ANY ((ARRAY['standalone_llm'::character varying, 'vendor_search'::character varying, 'clef_workers_ai'::character varying, 'clef_gateway'::character varying])::text[]))),
    CONSTRAINT platform_credentials_revision_check CHECK ((revision >= 1)),
    CONSTRAINT platform_credentials_state_check CHECK (((state)::text = ANY ((ARRAY['active'::character varying, 'disabled'::character varying, 'removed'::character varying])::text[])))
);

-- TABLE: platform_trust_anchors
CREATE TABLE public.platform_trust_anchors (
    label character varying(100) NOT NULL,
    fingerprint_sha256 character varying(64) NOT NULL,
    certificate_der bytea NOT NULL,
    subject character varying(2000) NOT NULL,
    issuer character varying(2000) NOT NULL,
    not_before timestamp with time zone NOT NULL,
    not_after timestamp with time zone NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    disabled_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT platform_trust_anchors_certificate_der_check CHECK (((octet_length(certificate_der) >= 1) AND (octet_length(certificate_der) <= 65536))),
    CONSTRAINT platform_trust_anchors_check CHECK (((length(btrim((label)::text)) >= 1) AND (length(btrim((label)::text)) <= 100))),
    CONSTRAINT platform_trust_anchors_check1 CHECK (((not_after >= not_before) AND (revision >= 1))),
    CONSTRAINT platform_trust_anchors_check2 CHECK (((enabled AND (disabled_at IS NULL)) OR ((NOT enabled) AND (disabled_at IS NOT NULL)))),
    CONSTRAINT platform_trust_anchors_fingerprint_sha256_check CHECK (((fingerprint_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: product_revisions
CREATE TABLE public.product_revisions (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    product_id uuid NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    CONSTRAINT revision_object CHECK ((jsonb_typeof(data) = 'object'::text)),
    CONSTRAINT revision_positive CHECK ((revision > 0))
);

-- TABLE: products
CREATE TABLE public.products (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    current_revision integer NOT NULL,
    lifecycle_state character varying(8) DEFAULT 'active'::character varying NOT NULL,
    lifecycle_revision integer DEFAULT 0 NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    CONSTRAINT product_lifecycle_valid CHECK ((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text)))),
    CONSTRAINT product_revision_positive CHECK ((current_revision > 0))
);

-- TABLE: profile_attachment_links
CREATE TABLE public.profile_attachment_links (
    profile_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    field character varying(30) NOT NULL,
    attachment_id uuid NOT NULL,
    attachment_revision_id uuid NOT NULL,
    file_id uuid NOT NULL,
    approval_id uuid NOT NULL,
    active boolean NOT NULL,
    state_version integer NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT profile_attachment_links_check CHECK ((((field)::text = ANY ((ARRAY['registration_details'::character varying, 'performance_summary'::character varying, 'standard_wording'::character varying])::text[])) AND (state_version > 0))),
    CONSTRAINT profile_attachment_links_payload_hash_check CHECK (((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: prototype_decision_batches
CREATE TABLE public.prototype_decision_batches (
    module_label character varying(200) NOT NULL,
    task_feature_ids jsonb NOT NULL,
    target_manifest jsonb NOT NULL,
    input_hash character varying(64) NOT NULL,
    request_hash character varying(64) NOT NULL,
    idempotency_key uuid NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: prototype_evidence_decisions
CREATE TABLE public.prototype_evidence_decisions (
    batch_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    card_id uuid NOT NULL,
    card_revision_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    rendition_id uuid NOT NULL,
    image_sha256 character varying(64) NOT NULL,
    html_sha256 character varying(64) NOT NULL,
    task_feature_id uuid NOT NULL,
    feature_revision_id uuid NOT NULL,
    previous_decision_id uuid,
    decision character varying(10) NOT NULL,
    keep_basis character varying(30),
    reason text,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT prototype_evidence_decisions_check CHECK (((((decision)::text = 'keep'::text) AND (keep_basis IS NOT NULL) AND ((keep_basis)::text = ANY ((ARRAY['already_delivered'::character varying, 'will_deliver'::character varying])::text[]))) OR (((decision)::text = 'replace'::text) AND (keep_basis IS NULL)))),
    CONSTRAINT prototype_evidence_decisions_check1 CHECK (((previous_decision_id IS NULL) OR ((reason IS NOT NULL) AND (length(btrim(reason)) > 0))))
);

-- TABLE: provider_configs
CREATE TABLE public.provider_configs (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    capability character varying(40) NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    encrypted_key text,
    key_last4 character varying(4),
    CONSTRAINT provider_config_object CHECK ((jsonb_typeof(data) = 'object'::text)),
    CONSTRAINT provider_config_source CHECK (((encrypted_key IS NOT NULL) AND (length(encrypted_key) > 0) AND (key_last4 IS NOT NULL) AND (length((key_last4)::text) = 4) AND ((data ->> 'provider'::text) = ANY (ARRAY['anthropic'::text, 'openai'::text])) AND ((length((data ->> 'model'::text)) >= 1) AND (length((data ->> 'model'::text)) <= 100)) AND ((data ->> 'json_mode'::text) = ANY (ARRAY['json_schema'::text, 'json_object'::text])))),
    CONSTRAINT provider_revision_capability CHECK (((revision > 0) AND ((capability)::text = 'llm_extract'::text)))
);

-- TABLE: requirement_review_events
CREATE TABLE public.requirement_review_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    review_id uuid NOT NULL,
    revision integer NOT NULL,
    action character varying(30) NOT NULL,
    state_after character varying(30) NOT NULL,
    review_hash character varying(64) NOT NULL,
    snapshot_sha256 character varying(64) NOT NULL,
    snapshot_ciphertext text NOT NULL,
    reason_ciphertext text,
    reason_sha256 character varying(64),
    source_pin jsonb,
    actor_kind character varying(20) NOT NULL,
    request_id uuid,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT requirement_review_events_action_check CHECK (((action)::text = ANY ((ARRAY['seed'::character varying, 'manual_add'::character varying, 'confirm'::character varying, 'reopen'::character varying, 'invalidate'::character varying, 'source_repair'::character varying])::text[]))),
    CONSTRAINT requirement_review_events_actor_kind_check CHECK (((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'worker'::character varying, 'migration'::character varying])::text[]))),
    CONSTRAINT requirement_review_events_revision_check CHECK ((revision >= 1)),
    CONSTRAINT requirement_review_events_snapshot_ciphertext_check CHECK (((length(snapshot_ciphertext) >= 80) AND (snapshot_ciphertext ~ '^gAAAA'::text))),
    CONSTRAINT requirement_review_events_state_after_check CHECK (((state_after)::text = ANY ((ARRAY['unconfirmed'::character varying, 'legacy_unconfirmed'::character varying, 'confirmed'::character varying, 'invalidated'::character varying])::text[])))
);

-- TABLE: requirement_review_requests
CREATE TABLE public.requirement_review_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    request_id uuid NOT NULL,
    action character varying(30) NOT NULL,
    request_sha256 character varying(64) NOT NULL,
    receipt_ciphertext text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);

-- TABLE: requirement_review_sets
CREATE TABLE public.requirement_review_sets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    document_id uuid NOT NULL,
    origin character varying(20) NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    membership_sha256 character varying(64) NOT NULL,
    confirmation_sha256 character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT requirement_review_sets_origin_check CHECK (((origin)::text = ANY ((ARRAY['model'::character varying, 'manual'::character varying])::text[]))),
    CONSTRAINT requirement_review_sets_revision_check CHECK ((revision >= 1))
);

-- TABLE: requirement_reviews
CREATE TABLE public.requirement_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    origin character varying(30) NOT NULL,
    revision integer NOT NULL,
    current_event_id uuid NOT NULL,
    state character varying(30) NOT NULL,
    review_hash character varying(64) NOT NULL,
    source_pin jsonb,
    snapshot_ciphertext text NOT NULL,
    confirmed_at timestamp with time zone,
    rejected_job_id uuid,
    rejected_index integer,
    rejected_summary_sha256 character varying(64),
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT requirement_reviews_check CHECK (((((state)::text = 'confirmed'::text) AND (confirmed_at IS NOT NULL) AND (source_pin IS NOT NULL)) OR (((state)::text <> 'confirmed'::text) AND (confirmed_at IS NULL)))),
    CONSTRAINT requirement_reviews_check1 CHECK ((((rejected_job_id IS NULL) AND (rejected_index IS NULL) AND (rejected_summary_sha256 IS NULL)) OR ((rejected_job_id IS NOT NULL) AND (rejected_index IS NOT NULL) AND (rejected_summary_sha256 IS NOT NULL)))),
    CONSTRAINT requirement_reviews_origin_check CHECK (((origin)::text = ANY ((ARRAY['extracted'::character varying, 'legacy'::character varying, 'manual_missing'::character varying, 'manual_rejected'::character varying])::text[]))),
    CONSTRAINT requirement_reviews_rejected_index_check CHECK ((rejected_index >= 0)),
    CONSTRAINT requirement_reviews_revision_check CHECK ((revision >= 1)),
    CONSTRAINT requirement_reviews_snapshot_ciphertext_check CHECK (((length(snapshot_ciphertext) >= 80) AND (snapshot_ciphertext ~ '^gAAAA'::text))),
    CONSTRAINT requirement_reviews_state_check CHECK (((state)::text = ANY ((ARRAY['unconfirmed'::character varying, 'legacy_unconfirmed'::character varying, 'confirmed'::character varying, 'invalidated'::character varying])::text[])))
);

-- TABLE: requirements
CREATE TABLE public.requirements (
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    page integer,
    quote text NOT NULL,
    text text NOT NULL,
    category character varying(20) NOT NULL,
    starred boolean NOT NULL,
    condition jsonb NOT NULL,
    fingerprint character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    location jsonb,
    job_id uuid NOT NULL,
    model_quote text,
    CONSTRAINT requirement_citation CHECK (((length(quote) > 0) AND (((page > 0) AND (location IS NULL)) OR ((page IS NULL) AND (jsonb_typeof(location) = 'object'::text) AND (length(COALESCE((location ->> 'block_id'::text), ''::text)) > 0) AND (length(COALESCE((location ->> 'label'::text), ''::text)) > 0)))))
);

-- TABLE: resource_lifecycle_events
CREATE TABLE public.resource_lifecycle_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    product_id uuid,
    revision integer NOT NULL,
    resource_revision integer NOT NULL,
    before_state character varying(8) NOT NULL,
    after_state character varying(8) NOT NULL,
    reason_code character varying(20) NOT NULL,
    actor_kind character varying(8) DEFAULT 'session'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    feature_id uuid,
    certificate_id uuid,
    profile_id uuid,
    template_id uuid,
    CONSTRAINT lifecycle_one_root CHECK ((num_nonnulls(product_id, feature_id, certificate_id, profile_id, template_id) = 1)),
    CONSTRAINT resource_lifecycle_events_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT resource_lifecycle_events_after_state_check CHECK (((after_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[]))),
    CONSTRAINT resource_lifecycle_events_before_state_check CHECK (((before_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[]))),
    CONSTRAINT resource_lifecycle_events_check CHECK (((before_state)::text <> (after_state)::text)),
    CONSTRAINT resource_lifecycle_events_check1 CHECK ((((after_state)::text = 'active'::text) = ((reason_code)::text = 'restored'::text))),
    CONSTRAINT resource_lifecycle_events_reason_code_check CHECK (((reason_code)::text = ANY ((ARRAY['obsolete'::character varying, 'duplicate'::character varying, 'unavailable'::character varying, 'restored'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT resource_lifecycle_events_resource_revision_check CHECK ((resource_revision >= 1)),
    CONSTRAINT resource_lifecycle_events_revision_check CHECK ((revision >= 1))
);

-- TABLE: response_card_revisions
CREATE TABLE public.response_card_revisions (
    card_id uuid NOT NULL,
    revision integer NOT NULL,
    state character varying(30) NOT NULL,
    review_domain character varying(20),
    disposition character varying(20),
    disposition_at timestamp with time zone,
    response_kind character varying(20),
    suggested_disposition character varying(20),
    response_text text,
    deviation character varying(20),
    deviation_note text,
    review_hint character varying(30),
    reason text,
    reviewed_warning_codes jsonb NOT NULL,
    confirmed_at timestamp with time zone,
    origin character varying(20) NOT NULL,
    model_job_id uuid,
    actor_token_id uuid,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    quote_sha256 character varying(64),
    CONSTRAINT response_card_revisions_gate_0 CHECK ((revision > 0)),
    CONSTRAINT response_card_revisions_gate_1 CHECK (((state)::text = ANY ((ARRAY['draft'::character varying, 'pending_review'::character varying, 'confirmed'::character varying, 'rejected'::character varying, 'needs_material'::character varying])::text[]))),
    CONSTRAINT response_card_revisions_gate_10 CHECK (((deviation_note IS NULL) OR ((length(deviation_note) <= 10000) AND (deviation_note ~ '[^[:space:]]'::text)))),
    CONSTRAINT response_card_revisions_gate_11 CHECK (((reason IS NULL) OR ((length(reason) <= 10000) AND (reason ~ '[^[:space:]]'::text)))),
    CONSTRAINT response_card_revisions_gate_12 CHECK ((jsonb_typeof(reviewed_warning_codes) = 'array'::text)),
    CONSTRAINT response_card_revisions_gate_13 CHECK ((((origin)::text = ANY ((ARRAY['human'::character varying, 'agent'::character varying, 'model'::character varying])::text[])) AND ((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_14 CHECK (((origin)::text = CASE actor_kind WHEN 'session'::text THEN 'human'::text WHEN 'worker'::text THEN 'model'::text ELSE 'agent'::text END)),
    CONSTRAINT response_card_revisions_gate_15 CHECK ((((origin)::text <> 'model'::text) OR (((actor_kind)::text = 'worker'::text) AND (model_job_id IS NOT NULL)))),
    CONSTRAINT response_card_revisions_gate_16 CHECK (((((state)::text = 'confirmed'::text) AND (confirmed_at IS NOT NULL) AND ((disposition)::text = 'respond'::text) AND (response_kind IS NOT NULL) AND (response_text IS NOT NULL) AND (deviation IS NOT NULL) AND (deviation_note IS NOT NULL) AND (review_domain IS NOT NULL)) OR (((state)::text <> 'confirmed'::text) AND (confirmed_at IS NULL)))),
    CONSTRAINT response_card_revisions_gate_2 CHECK (((review_domain IS NULL) OR ((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_3 CHECK (((disposition IS NULL) OR ((disposition)::text = ANY ((ARRAY['respond'::character varying, 'comply_only'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_4 CHECK ((((disposition IS NULL) AND (disposition_at IS NULL)) OR ((disposition IS NOT NULL) AND (disposition_at IS NOT NULL)))),
    CONSTRAINT response_card_revisions_gate_5 CHECK (((response_kind IS NULL) OR ((response_kind)::text = ANY ((ARRAY['evidence'::character varying, 'commitment'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_6 CHECK (((suggested_disposition IS NULL) OR ((suggested_disposition)::text = ANY ((ARRAY['respond'::character varying, 'comply_only'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_7 CHECK (((deviation IS NULL) OR ((deviation)::text = ANY ((ARRAY['none'::character varying, 'positive'::character varying, 'negative'::character varying])::text[])))),
    CONSTRAINT response_card_revisions_gate_8 CHECK (((review_hint IS NULL) OR ((review_hint)::text = 'needs_material'::text))),
    CONSTRAINT response_card_revisions_gate_9 CHECK (((response_text IS NULL) OR ((length(response_text) <= 20000) AND (response_text ~ '[^[:space:]]'::text)))),
    CONSTRAINT response_card_revisions_quote_sha256_check CHECK (((quote_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: response_cards
CREATE TABLE public.response_cards (
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    current_revision_id uuid NOT NULL,
    revision integer NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT response_cards_gate_0 CHECK ((revision > 0))
);

-- TABLE: response_items
CREATE TABLE public.response_items (
    draft_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    category character varying(20) NOT NULL,
    starred boolean NOT NULL,
    card_id uuid,
    card_revision_id uuid,
    kind character varying(20) NOT NULL,
    "table" character varying(20),
    source jsonb NOT NULL,
    location_label text NOT NULL,
    response_kind character varying(20),
    response_text text,
    deviation character varying(20),
    deviation_note text,
    disposition_at timestamp with time zone,
    gap_reasons jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT response_items_gate_0 CHECK (((category)::text = ANY ((ARRAY['technical'::character varying, 'qualification'::character varying, 'substantive'::character varying, 'scoring'::character varying])::text[]))),
    CONSTRAINT response_items_gate_1 CHECK (((kind)::text = ANY ((ARRAY['row'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[]))),
    CONSTRAINT response_items_gate_2 CHECK (((jsonb_typeof(source) = 'object'::text) AND (location_label ~ '[^[:space:]]'::text) AND (jsonb_typeof(gap_reasons) = 'array'::text))),
    CONSTRAINT response_items_gate_3 CHECK (((card_id IS NULL) = (card_revision_id IS NULL))),
    CONSTRAINT response_items_gate_4 CHECK (((((kind)::text = 'row'::text) AND (card_id IS NOT NULL) AND ("table" IS NOT NULL) AND (("table")::text = ANY ((ARRAY['substantive'::character varying, 'commercial'::character varying, 'technical'::character varying])::text[])) AND (response_kind IS NOT NULL) AND ((response_kind)::text = ANY ((ARRAY['evidence'::character varying, 'commitment'::character varying])::text[])) AND (response_text IS NOT NULL) AND (deviation IS NOT NULL) AND ((deviation)::text = ANY ((ARRAY['none'::character varying, 'positive'::character varying, 'negative'::character varying])::text[])) AND (deviation_note IS NOT NULL) AND (disposition_at IS NULL) AND (jsonb_array_length(gap_reasons) = 0)) OR (((kind)::text = 'comply_only'::text) AND (card_id IS NOT NULL) AND ("table" IS NULL) AND (response_kind IS NULL) AND (response_text IS NULL) AND (deviation IS NULL) AND (deviation_note IS NULL) AND (disposition_at IS NOT NULL) AND (jsonb_array_length(gap_reasons) = 0)) OR (((kind)::text = 'gap'::text) AND ("table" IS NULL) AND (response_kind IS NULL) AND (response_text IS NULL) AND (deviation IS NULL) AND (deviation_note IS NULL) AND (disposition_at IS NULL) AND (jsonb_array_length(gap_reasons) > 0))))
);

-- TABLE: sandbox_artifacts
CREATE TABLE public.sandbox_artifacts (
    sandbox_run_id uuid NOT NULL,
    attempt_record_id uuid NOT NULL,
    input_id uuid NOT NULL,
    task_id uuid NOT NULL,
    kind character varying(30) NOT NULL,
    ordinal integer NOT NULL,
    parent_artifact_id uuid,
    plaintext_sha256 character varying(64) NOT NULL,
    size_bytes integer NOT NULL,
    media_type character varying(80) NOT NULL,
    object_key text NOT NULL,
    provenance_manifest_hash character varying(64) NOT NULL,
    origin character varying(30) NOT NULL,
    width integer,
    height integer,
    page integer,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sandbox_artifacts_gate_0 CHECK (((kind)::text = ANY ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying, 'rendered_html'::character varying, 'source_pdf'::character varying, 'request_manifest'::character varying, 'capture_archive'::character varying, 'provenance_manifest'::character varying])::text[]))),
    CONSTRAINT sandbox_artifacts_gate_1 CHECK ((((ordinal >= 0) AND (ordinal <= 31)) AND ((size_bytes >= 1) AND (size_bytes <= 67108864)))),
    CONSTRAINT sandbox_artifacts_gate_2 CHECK ((((plaintext_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provenance_manifest_hash)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT sandbox_artifacts_gate_3 CHECK (((origin)::text = ANY ((ARRAY['prototype'::character varying, 'public_web_capture'::character varying, 'public_pdf_capture'::character varying])::text[]))),
    CONSTRAINT sandbox_artifacts_gate_4 CHECK ((parent_artifact_id IS DISTINCT FROM id)),
    CONSTRAINT sandbox_artifacts_gate_5 CHECK (((((kind)::text = ANY ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying])::text[])) AND (width IS NOT NULL) AND (height IS NOT NULL) AND ((width >= 1) AND (width <= 8192)) AND ((height >= 1) AND (height <= 8192)) AND (((width)::bigint * height) <= 20000000) AND (size_bytes <= 41943040) AND ((media_type)::text = 'image/png'::text)) OR (((kind)::text <> ALL ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying])::text[])) AND (width IS NULL) AND (height IS NULL) AND ((media_type)::text = 'application/octet-stream'::text)))),
    CONSTRAINT sandbox_artifacts_gate_6 CHECK (((((kind)::text = 'pdf_page_png'::text) AND (page IS NOT NULL) AND (page >= 1)) OR (((kind)::text <> 'pdf_page_png'::text) AND (page IS NULL)))),
    CONSTRAINT sandbox_artifacts_gate_7 CHECK ((((kind)::text <> ALL ((ARRAY['rendered_html'::character varying, 'request_manifest'::character varying, 'provenance_manifest'::character varying])::text[])) OR (size_bytes <= 4194304))),
    CONSTRAINT sandbox_artifacts_gate_8 CHECK ((((kind)::text <> 'source_pdf'::text) OR (size_bytes <= 33554432)))
);

-- TABLE: sandbox_attempts
CREATE TABLE public.sandbox_attempts (
    sandbox_run_id uuid NOT NULL,
    input_id uuid NOT NULL,
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    instance_group_ref_hash character varying(64) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    termination_code character varying(100),
    cleanup_state character varying(20) NOT NULL,
    metrics jsonb NOT NULL,
    runtime_versions jsonb NOT NULL,
    issues jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    descriptor jsonb NOT NULL,
    completion_txid bigint,
    CONSTRAINT sandbox_attempts_gate_0 CHECK (((instance_group_ref_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT sandbox_attempts_gate_1 CHECK (((cleanup_state)::text = ANY ((ARRAY['pending'::character varying, 'complete'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT sandbox_attempts_gate_2 CHECK ((((ended_at IS NULL) AND (termination_code IS NULL) AND ((cleanup_state)::text = 'pending'::text)) OR ((ended_at IS NOT NULL) AND (ended_at >= started_at) AND ((termination_code)::text ~ '^[a-z0-9_]{1,100}$'::text) AND ((cleanup_state)::text = ANY ((ARRAY['complete'::character varying, 'failed'::character varying])::text[]))))),
    CONSTRAINT sandbox_attempts_gate_3 CHECK (((jsonb_typeof(metrics) = 'object'::text) AND (jsonb_typeof(runtime_versions) = 'object'::text) AND (jsonb_typeof(issues) = 'array'::text)))
);

-- TABLE: sandbox_fetch_receipts
CREATE TABLE public.sandbox_fetch_receipts (
    sandbox_run_id uuid NOT NULL,
    attempt_record_id uuid NOT NULL,
    input_id uuid NOT NULL,
    task_id uuid NOT NULL,
    request_ordinal integer NOT NULL,
    parent_redirect_id uuid,
    url_sha256 character varying(64) NOT NULL,
    encrypted_request_metadata text,
    response_sha256 character varying(64),
    response_bytes integer NOT NULL,
    status_code integer,
    decision_code character varying(100) NOT NULL,
    policy_revision character varying(100) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone NOT NULL,
    bundle_artifact_id uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sandbox_fetch_receipts_gate_0 CHECK ((((request_ordinal >= 0) AND (request_ordinal <= 203)) AND ((response_bytes >= 0) AND (response_bytes <= 67108864)))),
    CONSTRAINT sandbox_fetch_receipts_gate_1 CHECK ((((url_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((response_sha256 IS NULL) OR ((response_sha256)::text ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT sandbox_fetch_receipts_gate_2 CHECK (((status_code IS NULL) OR ((status_code >= 100) AND (status_code <= 599)))),
    CONSTRAINT sandbox_fetch_receipts_gate_3 CHECK ((((decision_code)::text ~ '^[a-z0-9_]{1,100}$'::text) AND (ended_at >= started_at))),
    CONSTRAINT sandbox_fetch_receipts_gate_4 CHECK ((parent_redirect_id IS DISTINCT FROM id))
);

-- TABLE: sandbox_inputs
CREATE TABLE public.sandbox_inputs (
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    purpose character varying(30) NOT NULL,
    task_feature_id uuid,
    feature_revision_id uuid,
    html_sha256 character varying(64),
    size_bytes integer,
    object_key text,
    generation_job_id uuid,
    task_resource_id uuid,
    product_revision_id uuid,
    source_field character varying(30),
    source_url_sha256 character varying(64),
    encrypted_source_url text,
    spec jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sandbox_inputs_gate_0 CHECK (((purpose)::text = ANY ((ARRAY['prototype_offline'::character varying, 'vendor_capture'::character varying])::text[]))),
    CONSTRAINT sandbox_inputs_gate_1 CHECK (((jsonb_typeof(spec) = 'object'::text) AND ((spec ->> 'purpose'::text) IS NOT NULL) AND ((spec ->> 'purpose'::text) = (purpose)::text))),
    CONSTRAINT sandbox_inputs_gate_2 CHECK ((generation_job_id IS NULL)),
    CONSTRAINT sandbox_inputs_gate_3 CHECK (((((purpose)::text = 'prototype_offline'::text) AND (task_feature_id IS NOT NULL) AND (feature_revision_id IS NOT NULL) AND (html_sha256 IS NOT NULL) AND (size_bytes IS NOT NULL) AND (object_key IS NOT NULL) AND (task_resource_id IS NULL) AND (product_revision_id IS NULL) AND (source_field IS NULL) AND (source_url_sha256 IS NULL) AND (encrypted_source_url IS NULL)) OR (((purpose)::text = 'vendor_capture'::text) AND (task_feature_id IS NULL) AND (feature_revision_id IS NULL) AND (html_sha256 IS NULL) AND (size_bytes IS NULL) AND (object_key IS NULL) AND (task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL) AND (source_field IS NOT NULL) AND (source_url_sha256 IS NOT NULL) AND (encrypted_source_url IS NOT NULL)))),
    CONSTRAINT sandbox_inputs_gate_4 CHECK ((((html_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 4194304)))),
    CONSTRAINT sandbox_inputs_gate_5 CHECK ((((source_url_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((source_field)::text = ANY ((ARRAY['official_url'::character varying, 'whitepaper_url'::character varying])::text[])))),
    CONSTRAINT sandbox_inputs_gate_6 CHECK ((object_key = (((('sandbox-inputs/'::text) || (id)::text) || '/'::text) || (html_sha256)::text))),
    CONSTRAINT sandbox_spec_fields CHECK (((((purpose)::text = 'prototype_offline'::text) AND (spec ?& ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_feature_id'::text, 'expected_feature_revision_id'::text, 'html_sha256'::text, 'html_size_bytes'::text, 'viewport'::text]) AND ((spec - ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_feature_id'::text, 'expected_feature_revision_id'::text, 'html_sha256'::text, 'html_size_bytes'::text, 'viewport'::text]) = '{}'::jsonb)) OR (((purpose)::text = 'vendor_capture'::text) AND (spec ?& ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_resource_id'::text, 'expected_product_revision_id'::text, 'source_field'::text, 'expected_source_url_sha256'::text, 'format'::text, 'pdf_pages'::text, 'viewport'::text, 'archive'::text, 'capture_key'::text]) AND ((spec - ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_resource_id'::text, 'expected_product_revision_id'::text, 'source_field'::text, 'expected_source_url_sha256'::text, 'format'::text, 'pdf_pages'::text, 'viewport'::text, 'archive'::text, 'capture_key'::text]) = '{}'::jsonb)))),
    CONSTRAINT sandbox_viewport_fields CHECK (((jsonb_typeof((spec -> 'viewport'::text)) = 'object'::text) AND ((spec -> 'viewport'::text) ?& ARRAY['width'::text, 'height'::text, 'device_scale_factor'::text]) AND (((spec -> 'viewport'::text) - ARRAY['width'::text, 'height'::text, 'device_scale_factor'::text]) = '{}'::jsonb) AND (jsonb_typeof(((spec -> 'viewport'::text) -> 'width'::text)) = 'number'::text) AND (jsonb_typeof(((spec -> 'viewport'::text) -> 'height'::text)) = 'number'::text) AND (((spec -> 'viewport'::text) -> 'device_scale_factor'::text) = '1'::jsonb) AND (((((spec -> 'viewport'::text) ->> 'width'::text))::numeric >= (320)::numeric) AND ((((spec -> 'viewport'::text) ->> 'width'::text))::numeric <= (4096)::numeric)) AND (((((spec -> 'viewport'::text) ->> 'height'::text))::numeric >= (240)::numeric) AND ((((spec -> 'viewport'::text) ->> 'height'::text))::numeric <= (4096)::numeric)) AND (trunc((((spec -> 'viewport'::text) ->> 'width'::text))::numeric) = (((spec -> 'viewport'::text) ->> 'width'::text))::numeric) AND (trunc((((spec -> 'viewport'::text) ->> 'height'::text))::numeric) = (((spec -> 'viewport'::text) ->> 'height'::text))::numeric)))
);

-- TABLE: sandbox_runs
CREATE TABLE public.sandbox_runs (
    input_id uuid NOT NULL,
    task_id uuid NOT NULL,
    document_id uuid NOT NULL,
    job_id uuid NOT NULL,
    request_hash character varying(64) NOT NULL,
    profile character varying(40) NOT NULL,
    policy_revision character varying(100) NOT NULL,
    policy_sha256 character varying(64) NOT NULL,
    runtime_profile_digest character varying(64) NOT NULL,
    actor_kind character varying(20) NOT NULL,
    token_id uuid,
    scope_snapshot jsonb NOT NULL,
    capture_key uuid,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sandbox_runs_gate_0 CHECK ((((request_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((runtime_profile_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((policy_sha256)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT sandbox_runs_gate_1 CHECK ((((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying])::text[])) AND (((actor_kind)::text = 'token'::text) = (token_id IS NOT NULL)))),
    CONSTRAINT sandbox_runs_gate_2 CHECK (((jsonb_typeof(scope_snapshot) = 'array'::text) AND (scope_snapshot <@ '["sandbox:read", "sandbox:render", "sandbox:capture", "task:read", "resource:read", "job:read"]'::jsonb) AND (scope_snapshot ?& ARRAY['task:read'::text, 'resource:read'::text, 'job:read'::text]))),
    CONSTRAINT sandbox_runs_gate_3 CHECK (((profile)::text = ANY ((ARRAY['prototype-offline-v1'::character varying, 'vendor-capture-v1'::character varying])::text[]))),
    CONSTRAINT sandbox_runs_gate_4 CHECK (((policy_revision)::text ~ '^[A-Za-z0-9_.-]{1,100}$'::text))
);

-- TABLE: score_item_citations
CREATE TABLE public.score_item_citations (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    score_item_id uuid NOT NULL,
    kind text NOT NULL,
    quote text NOT NULL,
    document_id uuid,
    chunk_id uuid,
    draft_id uuid,
    response_item_id uuid,
    card_revision_id uuid,
    field text,
    source jsonb,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_citation_shape CHECK (((quote ~ '[^[:space:]]'::text) AND (((kind = 'tender'::text) AND (document_id IS NOT NULL) AND (chunk_id IS NOT NULL) AND (source IS NOT NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL)) OR ((kind = 'draft'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NOT NULL) AND (response_item_id IS NOT NULL) AND (card_revision_id IS NOT NULL) AND (field IS NOT NULL) AND (field = ANY (ARRAY['response_text'::text, 'deviation_note'::text]))))))
);

-- TABLE: score_report_item_responses
CREATE TABLE public.score_report_item_responses (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    score_item_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    response_item_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    card_revision_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: score_report_items
CREATE TABLE public.score_report_items (
    task_id uuid NOT NULL,
    report_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    rubric_item_id uuid NOT NULL,
    section_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    anchor_response_item_id uuid NOT NULL,
    anchor_partition text NOT NULL,
    outcome text NOT NULL,
    reason_code text NOT NULL,
    reason text NOT NULL,
    score_range jsonb,
    estimated_score numeric(18,8),
    deduction_reasons jsonb NOT NULL,
    strengthening_actions jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_item_shape CHECK (((anchor_partition = ANY (ARRAY['response'::text, 'comply_only'::text, 'gap'::text])) AND (outcome = ANY (ARRAY['assessed'::text, 'unassessable'::text])) AND (reason_code ~ '[^[:space:]]'::text) AND (reason ~ '[^[:space:]]'::text) AND (jsonb_typeof(deduction_reasons) = 'array'::text) AND (jsonb_typeof(strengthening_actions) = 'array'::text) AND (((outcome = 'assessed'::text) AND (estimated_score IS NOT NULL) AND (score_range IS NOT NULL) AND (jsonb_typeof(score_range) = 'object'::text) AND (score_range ?& ARRAY['minimum'::text, 'maximum'::text]) AND (((score_range ->> 'minimum'::text))::numeric >= (0)::numeric) AND ((estimated_score)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND ((estimated_score >= ((score_range ->> 'minimum'::text))::numeric) AND (estimated_score <= ((score_range ->> 'maximum'::text))::numeric)) AND ((estimated_score = ((score_range ->> 'maximum'::text))::numeric) OR (jsonb_array_length(deduction_reasons) > 0))) OR ((outcome = 'unassessable'::text) AND (estimated_score IS NULL)))))
);

-- TABLE: score_reports
CREATE TABLE public.score_reports (
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    document_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    actor_token_id uuid,
    rubric_version integer NOT NULL,
    rubric_revision integer NOT NULL,
    rubric_input_hash text NOT NULL,
    input_hash text NOT NULL,
    draft_input_hash text NOT NULL,
    prompt_version text NOT NULL,
    schema_version text NOT NULL,
    scoring_rule_version text NOT NULL,
    encrypted_input text NOT NULL,
    completion text NOT NULL,
    actor_kind text NOT NULL,
    assessment_date date NOT NULL,
    input_manifest jsonb NOT NULL,
    summary jsonb NOT NULL,
    limitations jsonb NOT NULL,
    sections jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_report_shape CHECK (((actor_kind = 'worker'::text) AND (rubric_version > 0) AND (rubric_revision > 0) AND (input_hash ~ '^[0-9a-f]{64}$'::text) AND (draft_input_hash ~ '^[0-9a-f]{64}$'::text) AND (rubric_input_hash ~ '^[0-9a-f]{64}$'::text) AND (completion = ANY (ARRAY['complete'::text, 'partial'::text])) AND (prompt_version ~ '[^[:space:]]'::text) AND (schema_version ~ '[^[:space:]]'::text) AND (scoring_rule_version ~ '[^[:space:]]'::text) AND (jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text) AND (jsonb_typeof(sections) = 'array'::text) AND (jsonb_typeof(limitations) = 'array'::text)))
);

-- TABLE: score_rubric_classifications
CREATE TABLE public.score_rubric_classifications (
    section_id uuid,
    item_id uuid,
    review_domain character varying(20) NOT NULL,
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    revision integer NOT NULL,
    set_revision integer NOT NULL,
    reason text NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_rubric_classifications_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT score_rubric_classifications_check CHECK (((section_id IS NULL) <> (item_id IS NULL))),
    CONSTRAINT score_rubric_classifications_reason_check CHECK ((reason ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_classifications_review_domain_check CHECK (((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[]))),
    CONSTRAINT score_rubric_classifications_revision_check CHECK ((revision >= 2)),
    CONSTRAINT score_rubric_classifications_set_revision_check CHECK ((set_revision >= 2))
);

-- TABLE: score_rubric_coverage
CREATE TABLE public.score_rubric_coverage (
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    source jsonb NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: score_rubric_coverage_decisions
CREATE TABLE public.score_rubric_coverage_decisions (
    coverage_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    canonical_requirement_id uuid,
    action character varying(20) NOT NULL,
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    revision integer NOT NULL,
    set_revision integer NOT NULL,
    reason text NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_rubric_coverage_decisions_action_check CHECK (((action)::text = ANY ((ARRAY['mapped'::character varying, 'duplicate'::character varying, 'excluded'::character varying, 'reopen'::character varying])::text[]))),
    CONSTRAINT score_rubric_coverage_decisions_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT score_rubric_coverage_decisions_check CHECK ((((action)::text = 'duplicate'::text) = (canonical_requirement_id IS NOT NULL))),
    CONSTRAINT score_rubric_coverage_decisions_check1 CHECK ((canonical_requirement_id <> requirement_id)),
    CONSTRAINT score_rubric_coverage_decisions_reason_check CHECK ((reason ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_coverage_decisions_revision_check CHECK ((revision >= 2)),
    CONSTRAINT score_rubric_coverage_decisions_set_revision_check CHECK ((set_revision >= 2))
);

-- TABLE: score_rubric_coverage_items
CREATE TABLE public.score_rubric_coverage_items (
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    coverage_decision_id uuid NOT NULL,
    rubric_item_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: score_rubric_decisions
CREATE TABLE public.score_rubric_decisions (
    section_id uuid,
    item_id uuid,
    action character varying(20) NOT NULL,
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    revision integer NOT NULL,
    set_revision integer NOT NULL,
    reason text NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_rubric_decisions_action_check CHECK (((action)::text = ANY ((ARRAY['confirm'::character varying, 'reject'::character varying, 'reopen'::character varying])::text[]))),
    CONSTRAINT score_rubric_decisions_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT score_rubric_decisions_check CHECK ((NOT ((section_id IS NOT NULL) AND (item_id IS NOT NULL)))),
    CONSTRAINT score_rubric_decisions_reason_check CHECK ((reason ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_decisions_revision_check CHECK ((revision >= 2)),
    CONSTRAINT score_rubric_decisions_set_revision_check CHECK ((set_revision >= 2))
);

-- TABLE: score_rubric_items
CREATE TABLE public.score_rubric_items (
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    section_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    key text NOT NULL,
    title text NOT NULL,
    rule_text text NOT NULL,
    "order" integer NOT NULL,
    assessment_mode character varying(30) NOT NULL,
    score_range jsonb,
    weight numeric(18,8),
    ambiguity_reason text,
    source jsonb NOT NULL,
    fingerprint character varying(64) NOT NULL,
    citation_valid boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rubric_candidate_item_range CHECK (public.rubric_candidate_range_valid(score_range)),
    CONSTRAINT rubric_candidate_item_weight_finite CHECK ((weight <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT score_rubric_items_assessment_mode_check CHECK (((assessment_mode)::text = ANY ((ARRAY['model_assessable'::character varying, 'ambiguous'::character varying, 'price_comparison'::character varying, 'external_comparison'::character varying, 'manual_only'::character varying, 'unsupported_formula'::character varying])::text[]))),
    CONSTRAINT score_rubric_items_key_check CHECK ((key ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_items_order_check CHECK (("order" >= 1)),
    CONSTRAINT score_rubric_items_rule_text_check CHECK ((rule_text ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_items_title_check CHECK ((title ~ '[^[:space:]]'::text))
);

-- TABLE: score_rubric_revision_events
CREATE TABLE public.score_rubric_revision_events (
    task_id uuid NOT NULL,
    rubric_id uuid NOT NULL,
    prior_rubric_id uuid NOT NULL,
    version integer NOT NULL,
    input_hash character varying(64) NOT NULL,
    expected_revision integer NOT NULL,
    expected_input_hash character varying(64) NOT NULL,
    replacement_snapshot jsonb NOT NULL,
    snapshot_sha256 character varying(64) NOT NULL,
    reason text NOT NULL,
    reason_sha256 character varying(64) NOT NULL,
    revised_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT score_rubric_revision_events_actor_kind_check CHECK (((actor_kind)::text = 'session'::text)),
    CONSTRAINT score_rubric_revision_events_reason_check CHECK ((reason ~ '[^[:space:]]'::text)),
    CONSTRAINT score_rubric_revision_events_replacement_snapshot_check CHECK ((jsonb_typeof(replacement_snapshot) = 'object'::text)),
    CONSTRAINT score_rubric_revision_events_snapshot_sha256_check CHECK ((length((snapshot_sha256)::text) = 64)),
    CONSTRAINT score_rubric_revision_events_version_check CHECK ((version >= 2))
);

-- TABLE: score_rubric_sets
CREATE TABLE public.score_rubric_sets (
    task_id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    document_id uuid NOT NULL,
    prior_rubric_id uuid,
    version integer NOT NULL,
    input_hash character varying(64) NOT NULL,
    input_manifest jsonb NOT NULL,
    encrypted_input text NOT NULL,
    normalization_rule_version character varying(100) NOT NULL,
    prompt_version character varying(100) NOT NULL,
    schema_version character varying(100) NOT NULL,
    overall_aggregation character varying(30) NOT NULL,
    overall_rule_text text,
    overall_score_range jsonb,
    overall_cap numeric(18,8),
    normalization_errors jsonb NOT NULL,
    actor_token_id uuid,
    actor_kind character varying(20) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rubric_candidate_overall_cap_finite CHECK ((overall_cap <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric]))),
    CONSTRAINT rubric_candidate_overall_range CHECK (public.rubric_candidate_range_valid(overall_score_range)),
    CONSTRAINT score_rubric_sets_actor_kind_check CHECK (((actor_kind)::text = ANY ((ARRAY['worker'::character varying, 'session'::character varying])::text[]))),
    CONSTRAINT score_rubric_sets_input_hash_check CHECK ((length((input_hash)::text) = 64)),
    CONSTRAINT score_rubric_sets_input_manifest_check CHECK ((jsonb_typeof(input_manifest) = 'object'::text)),
    CONSTRAINT score_rubric_sets_input_manifest_check1 CHECK (COALESCE((jsonb_typeof((input_manifest -> 'requirements'::text)) = 'array'::text), false)),
    CONSTRAINT score_rubric_sets_normalization_errors_check CHECK ((jsonb_typeof(normalization_errors) = 'array'::text)),
    CONSTRAINT score_rubric_sets_overall_aggregation_check CHECK (((overall_aggregation)::text = ANY ((ARRAY['sum'::character varying, 'weighted_sum'::character varying, 'capped_sum'::character varying, 'formula'::character varying, 'non_additive'::character varying])::text[]))),
    CONSTRAINT score_rubric_sets_version_check CHECK ((version >= 1))
);

-- TABLE: screenshot_analysis_inputs
CREATE TABLE public.screenshot_analysis_inputs (
    analysis_run_id uuid NOT NULL,
    ref character varying(80) NOT NULL,
    requirement_id uuid,
    asset_id uuid,
    rendition_id uuid,
    privacy_review_id uuid,
    content_sha256 character varying(64) NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT screenshot_analysis_inputs_check CHECK ((((requirement_id IS NOT NULL) AND (rendition_id IS NULL) AND (asset_id IS NULL) AND (privacy_review_id IS NULL)) OR ((requirement_id IS NULL) AND (rendition_id IS NOT NULL) AND (asset_id IS NOT NULL) AND (privacy_review_id IS NOT NULL))))
);

-- TABLE: screenshot_analysis_runs
CREATE TABLE public.screenshot_analysis_runs (
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    input_manifest jsonb NOT NULL,
    completion character varying(20) NOT NULL,
    rejected jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT screenshot_analysis_runs_completion_check CHECK (((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])))
);

-- TABLE: screenshot_assets
CREATE TABLE public.screenshot_assets (
    source_kind character varying(30) NOT NULL,
    image_kind character varying(30) NOT NULL,
    origin character varying(30) NOT NULL,
    task_feature_id uuid,
    feature_revision_id uuid,
    task_resource_id uuid,
    product_revision_id uuid,
    evidence_source_id uuid,
    vendor_archive_id uuid,
    prototype_run_id uuid,
    source_sha256 character varying(64) NOT NULL,
    source_hash_assurance character varying(30) NOT NULL,
    source_width integer NOT NULL,
    source_height integer NOT NULL,
    source jsonb NOT NULL,
    received_at timestamp with time zone NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash character varying(64) NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT screenshot_assets_check CHECK ((((source_width >= 1) AND (source_width <= 8192)) AND ((source_height >= 1) AND (source_height <= 8192)) AND (((source_width)::bigint * source_height) <= 20000000))),
    CONSTRAINT screenshot_assets_source_hash_assurance_check CHECK (((source_hash_assurance)::text = ANY ((ARRAY['client_declared'::character varying, 'server_verified'::character varying])::text[]))),
    CONSTRAINT screenshot_assets_source_sha256_check CHECK (((source_sha256)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: screenshot_privacy_reviews
CREATE TABLE public.screenshot_privacy_reviews (
    asset_id uuid NOT NULL,
    rendition_id uuid NOT NULL,
    reviewed_upload_sha256 character varying(64) NOT NULL,
    stored_image_sha256 character varying(64) NOT NULL,
    rule_version character varying(40) NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    annotation_request_id uuid,
    attachment_source_id uuid,
    resolved_hold_id uuid,
    CONSTRAINT attachment_privacy_branch CHECK ((((resolved_hold_id IS NULL) OR (attachment_source_id IS NOT NULL)) AND ((attachment_source_id IS NULL) OR (annotation_request_id IS NULL))))
);

-- TABLE: screenshot_prototype_runs
CREATE TABLE public.screenshot_prototype_runs (
    requirement_id uuid NOT NULL,
    task_feature_id uuid NOT NULL,
    feature_revision_id uuid NOT NULL,
    generation_job_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    html_sha256 character varying(64) NOT NULL,
    html_storage_key text NOT NULL,
    source_image_sha256 character varying(64) NOT NULL,
    source_image_key text NOT NULL,
    sandbox_receipt_id character varying(200) NOT NULL,
    sandbox_receipt_sha256 character varying(64) NOT NULL,
    provenance jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: screenshot_renditions
CREATE TABLE public.screenshot_renditions (
    asset_id uuid NOT NULL,
    parent_rendition_id uuid,
    privacy_review_id uuid NOT NULL,
    source_sha256 character varying(64) NOT NULL,
    upload_sha256 character varying(64) NOT NULL,
    image_sha256 character varying(64) NOT NULL,
    plan_sha256 character varying(64) NOT NULL,
    plan jsonb NOT NULL,
    mapping jsonb NOT NULL,
    image jsonb NOT NULL,
    profile character varying(40) NOT NULL,
    storage_key text NOT NULL,
    generation_job_id uuid,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    renderer_identity jsonb,
    provenance_sha256 character varying(64),
    CONSTRAINT annotation_profile_identity CHECK ((((profile)::text <> ALL ((ARRAY['annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[])) OR ((renderer_identity IS NOT NULL) AND (provenance_sha256 IS NOT NULL) AND ((provenance_sha256)::text ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT annotation_profiles CHECK (((profile)::text = ANY ((ARRAY['screenshot-markup-v1'::character varying, 'prototype-clean-v1'::character varying, 'annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[]))),
    CONSTRAINT screenshot_renditions_check CHECK ((((image_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((upload_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((plan_sha256)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: screenshot_search_candidates
CREATE TABLE public.screenshot_search_candidates (
    search_run_id uuid NOT NULL,
    ref character varying(80) NOT NULL,
    source_url text NOT NULL,
    title text NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: screenshot_search_runs
CREATE TABLE public.screenshot_search_runs (
    task_resource_id uuid NOT NULL,
    product_revision_id uuid NOT NULL,
    job_id uuid NOT NULL,
    input_hash character varying(64) NOT NULL,
    manifest jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: screenshot_suggestions
CREATE TABLE public.screenshot_suggestions (
    analysis_run_id uuid NOT NULL,
    image_input_id uuid NOT NULL,
    requirement_input_id uuid,
    proposal jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: screenshot_vendor_archives
CREATE TABLE public.screenshot_vendor_archives (
    task_resource_id uuid NOT NULL,
    product_revision_id uuid NOT NULL,
    search_candidate_id uuid,
    content_sha256 character varying(64) NOT NULL,
    archive_sha256 character varying(64) NOT NULL,
    storage_key text NOT NULL,
    descriptor jsonb NOT NULL,
    provenance jsonb NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sandbox_run_id uuid NOT NULL,
    archive_artifact_id uuid NOT NULL,
    entry_receipt_id uuid NOT NULL
);

-- TABLE: screenshot_withdrawals
CREATE TABLE public.screenshot_withdrawals (
    asset_id uuid NOT NULL,
    reason text NOT NULL,
    task_id uuid NOT NULL,
    extraction_job_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- TABLE: simulated_resources
CREATE TABLE public.simulated_resources (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    job_id uuid NOT NULL,
    product_id uuid,
    feature_id uuid,
    source_url text,
    CONSTRAINT simulated_one_resource CHECK (((product_id IS NULL) <> (feature_id IS NULL)))
);

-- TABLE: task_attachments
CREATE TABLE public.task_attachments (
    task_id uuid NOT NULL,
    task_org_profile_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    profile_attachment_link_id uuid NOT NULL,
    field character varying(30) NOT NULL,
    attachment_id uuid NOT NULL,
    attachment_revision_id uuid NOT NULL,
    file_id uuid NOT NULL,
    approval_id uuid NOT NULL,
    original_sha256 character varying(64) NOT NULL,
    lot character varying(100) NOT NULL,
    active boolean NOT NULL,
    state_version integer NOT NULL,
    selected_at timestamp with time zone NOT NULL,
    request_id uuid NOT NULL,
    payload_hash character varying(64) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT task_attachments_check CHECK ((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (state_version > 0))),
    CONSTRAINT task_attachments_payload_hash_check CHECK (((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: task_certificates
CREATE TABLE public.task_certificates (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    certificate_id uuid NOT NULL,
    certificate_revision_id uuid NOT NULL,
    lot character varying(100) DEFAULT ''::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL
);

-- TABLE: task_event_heads
CREATE TABLE public.task_event_heads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    last_seq bigint DEFAULT 0 NOT NULL,
    retained_floor_seq bigint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT task_event_heads_check CHECK (((retained_floor_seq >= 0) AND (retained_floor_seq <= last_seq))),
    CONSTRAINT task_event_heads_last_seq_check CHECK ((last_seq >= 0))
);

-- TABLE: task_events
CREATE TABLE public.task_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    seq bigint NOT NULL,
    event_kind text NOT NULL,
    payload jsonb NOT NULL,
    source_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT task_events_check CHECK (((jsonb_typeof(payload) = 'object'::text) AND (octet_length((payload)::text) <= 3072) AND ((payload ->> 'type'::text) = event_kind))),
    CONSTRAINT task_events_event_kind_check CHECK ((event_kind = ANY (ARRAY['board_changed'::text, 'job_progress'::text, 'access_changed'::text]))),
    CONSTRAINT task_events_seq_check CHECK ((seq > 0))
);

-- TABLE: task_features
CREATE TABLE public.task_features (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    feature_id uuid NOT NULL,
    feature_revision_id uuid NOT NULL,
    lot character varying(100) DEFAULT ''::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL
);

-- TABLE: task_org_profiles
CREATE TABLE public.task_org_profiles (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    profile_revision_id uuid NOT NULL,
    lot character varying(100) DEFAULT ''::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL
);

-- TABLE: task_resources
CREATE TABLE public.task_resources (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    product_id uuid NOT NULL,
    product_revision_id uuid NOT NULL,
    lot character varying(100) DEFAULT ''::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL
);

-- TABLE: task_templates
CREATE TABLE public.task_templates (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    task_id uuid NOT NULL,
    template_id uuid NOT NULL,
    template_revision_id uuid NOT NULL,
    lot character varying(100) DEFAULT ''::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL
);

-- TABLE: tasks
CREATE TABLE public.tasks (
    name character varying(200) NOT NULL,
    tender_number character varying(100),
    deadline timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    model_redaction_enabled boolean DEFAULT true NOT NULL,
    model_redaction_revision integer DEFAULT 1 NOT NULL,
    CONSTRAINT tasks_model_redaction_revision_check CHECK ((model_redaction_revision > 0))
);

-- TABLE: template_revisions
CREATE TABLE public.template_revisions (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    template_id uuid NOT NULL,
    revision integer NOT NULL,
    data jsonb NOT NULL,
    file jsonb NOT NULL,
    storage_key character varying(400) NOT NULL,
    CONSTRAINT template_declared_name CHECK ((((data ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((data -> 'name'::text)) = 'string'::text) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200)))),
    CONSTRAINT template_file_binding CHECK (((storage_key)::text = ((((((('template/'::text) || (template_id)::text) || '/'::text) || (id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.docx'::text))),
    CONSTRAINT template_file_hash CHECK ((((file ->> 'sha256'::text) IS NOT NULL) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT template_file_name CHECK ((((file ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200)))),
    CONSTRAINT template_file_object CHECK ((jsonb_typeof(file) = 'object'::text)),
    CONSTRAINT template_file_size CHECK ((((file ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric)))),
    CONSTRAINT template_file_type CHECK ((((file ->> 'media_type'::text) IS NOT NULL) AND ((file ->> 'media_type'::text) = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::text))),
    CONSTRAINT template_revision_number_positive CHECK ((revision > 0)),
    CONSTRAINT template_revision_object CHECK ((jsonb_typeof(data) = 'object'::text))
);

-- TABLE: templates
CREATE TABLE public.templates (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    current_revision integer NOT NULL,
    lifecycle_state character varying(8) DEFAULT 'active'::character varying NOT NULL,
    lifecycle_revision integer DEFAULT 0 NOT NULL,
    search_vector tsvector DEFAULT ''::tsvector NOT NULL,
    CONSTRAINT template_lifecycle_valid CHECK ((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text)))),
    CONSTRAINT template_revision_positive CHECK ((current_revision > 0))
);

-- TABLE: usage_records
CREATE TABLE public.usage_records (
    task_id uuid,
    provider character varying(100) NOT NULL,
    model character varying(100) NOT NULL,
    version character varying(100) NOT NULL,
    duration_ms integer NOT NULL,
    tokens integer NOT NULL,
    ocr_pages integer NOT NULL,
    test_only boolean NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    job_id uuid,
    run_id uuid,
    call_id uuid,
    provider_config_id uuid,
    image_count integer DEFAULT 0 NOT NULL,
    image_input_sha256 character varying(64),
    capability character varying(20) DEFAULT 'llm'::character varying NOT NULL,
    search_requests integer DEFAULT 0 NOT NULL,
    gateway_request_id character varying(128),
    gateway_trace_id character varying(128),
    browser_units integer DEFAULT 0 NOT NULL,
    CONSTRAINT usage_call_identity CHECK (((call_id IS NULL) OR ((job_id IS NOT NULL) AND (run_id IS NOT NULL)))),
    CONSTRAINT usage_capability CHECK (((capability)::text = ANY ((ARRAY['llm'::character varying, 'vision'::character varying, 'ocr'::character varying, 'search'::character varying, 'embedding'::character varying, 'browser'::character varying])::text[]))),
    CONSTRAINT usage_gateway_telemetry CHECK ((((gateway_request_id IS NULL) OR ((gateway_request_id)::text ~ '^[A-Za-z0-9_-]{1,128}$'::text)) AND ((gateway_trace_id IS NULL) OR ((gateway_trace_id)::text ~ '^[A-Za-z0-9_-]{1,128}$'::text)))),
    CONSTRAINT usage_image_manifest CHECK ((((image_count = 0) AND (image_input_sha256 IS NULL)) OR ((image_count > 0) AND (image_input_sha256 IS NOT NULL) AND ((image_input_sha256)::text ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT usage_records_browser_units_check CHECK ((browser_units >= 0)),
    CONSTRAINT usage_records_image_count_check CHECK (((image_count >= 0) AND (image_count <= 20))),
    CONSTRAINT usage_records_input_tokens_check CHECK ((input_tokens >= 0)),
    CONSTRAINT usage_records_output_tokens_check CHECK ((output_tokens >= 0)),
    CONSTRAINT usage_search_requests_nonnegative CHECK ((search_requests >= 0))
);

-- TABLE: vendor_calls
CREATE TABLE public.vendor_calls (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    run_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    state character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    task_id uuid,
    capability character varying(20) DEFAULT 'llm'::character varying NOT NULL,
    request_sha256 character varying(64) DEFAULT repeat('0'::text, 64) NOT NULL,
    provider_identity jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT vendor_call_metadata CHECK ((((capability)::text = ANY ((ARRAY['llm'::character varying, 'vision'::character varying, 'ocr'::character varying, 'search'::character varying, 'embedding'::character varying, 'browser'::character varying])::text[])) AND ((request_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (jsonb_typeof(provider_identity) = 'object'::text))),
    CONSTRAINT vendor_calls_state_check CHECK (((state)::text = ANY ((ARRAY['pending'::character varying, 'completed'::character varying, 'unknown'::character varying, 'not_sent'::character varying])::text[])))
);

-- TABLE: clef_settings
CREATE TABLE public.clef_settings (
    id boolean DEFAULT true NOT NULL,
    settings jsonb NOT NULL,
    revision bigint NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT clef_settings_singleton CHECK (id),
    CONSTRAINT clef_settings_shape CHECK (((jsonb_typeof(settings) = 'object'::text) AND (revision >= 1) AND ((settings ->> 'revision'::text) = (revision)::text)))
);

-- TABLE: credential_probes
CREATE TABLE public.credential_probes (
    id uuid NOT NULL,
    credential_id uuid NOT NULL,
    tested_revision bigint NOT NULL,
    secret_version bigint NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    finished_at timestamp with time zone,
    outcome character varying(20),
    CONSTRAINT credential_probes_outcome CHECK ((((finished_at IS NULL) AND (outcome IS NULL)) OR ((finished_at IS NOT NULL) AND ((outcome)::text = ANY ((ARRAY['passed'::character varying, 'auth_failed'::character varying, 'unsupported'::character varying, 'timeout'::character varying, 'rate_limited'::character varying, 'unavailable'::character varying, 'interrupted'::character varying])::text[])))))
);
