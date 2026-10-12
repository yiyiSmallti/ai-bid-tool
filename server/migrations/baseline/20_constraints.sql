-- CONSTRAINT: documents agent_document_task
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT agent_document_task UNIQUE (task_id, id);

-- CONSTRAINT: jobs agent_job_document
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT agent_job_document UNIQUE (task_id, document_id, id);

-- CONSTRAINT: agent_job_links agent_job_links_session_id_job_id_key
ALTER TABLE ONLY public.agent_job_links
    ADD CONSTRAINT agent_job_links_session_id_job_id_key UNIQUE (session_id, job_id);

-- CONSTRAINT: agent_job_links agent_job_links_pkey
ALTER TABLE ONLY public.agent_job_links
    ADD CONSTRAINT agent_job_links_pkey PRIMARY KEY (id);

-- CONSTRAINT: jobs agent_job_session
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT agent_job_session UNIQUE (agent_session_id, id);

-- CONSTRAINT: agent_messages agent_messages_session_id_idempotency_key_key
ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_session_id_idempotency_key_key UNIQUE (session_id, idempotency_key);

-- CONSTRAINT: agent_messages agent_messages_session_id_ordinal_key
ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_session_id_ordinal_key UNIQUE (session_id, ordinal);

-- CONSTRAINT: agent_messages agent_messages_pkey
ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);

-- CONSTRAINT: agent_pauses agent_pauses_session_id_id_key
ALTER TABLE ONLY public.agent_pauses
    ADD CONSTRAINT agent_pauses_session_id_id_key UNIQUE (session_id, id);

-- CONSTRAINT: agent_pauses agent_pauses_session_id_resume_idempotency_key_key
ALTER TABLE ONLY public.agent_pauses
    ADD CONSTRAINT agent_pauses_session_id_resume_idempotency_key_key UNIQUE (session_id, resume_idempotency_key);

-- CONSTRAINT: agent_pauses agent_pauses_pkey
ALTER TABLE ONLY public.agent_pauses
    ADD CONSTRAINT agent_pauses_pkey PRIMARY KEY (id);

-- CONSTRAINT: agent_principals agent_principals_pkey
ALTER TABLE ONLY public.agent_principals
    ADD CONSTRAINT agent_principals_pkey PRIMARY KEY (id);

-- CONSTRAINT: agent_sessions agent_sessions_id_principal_id_owner_user_id_key
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_id_principal_id_owner_user_id_key UNIQUE (id, principal_id);

-- CONSTRAINT: agent_sessions agent_sessions_id_task_id_document_id_key
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_id_task_id_document_id_key UNIQUE (id, task_id, document_id);

-- CONSTRAINT: agent_sessions agent_sessions_task_id_owner_user_id_start_idempoten_key
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_task_id_owner_user_id_start_idempoten_key UNIQUE (task_id, start_idempotency_key);

-- CONSTRAINT: agent_sessions agent_sessions_pkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_pkey PRIMARY KEY (id);

-- CONSTRAINT: agent_steps agent_steps_invocation_id_key
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_invocation_id_key UNIQUE (invocation_id);

-- CONSTRAINT: agent_steps agent_steps_session_id_id_key
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_session_id_id_key UNIQUE (session_id, id);

-- CONSTRAINT: agent_steps agent_steps_session_id_ordinal_key
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_session_id_ordinal_key UNIQUE (session_id, ordinal);

-- CONSTRAINT: agent_steps agent_steps_pkey
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_cards annotation_card_scope
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT annotation_card_scope UNIQUE (id, task_id, extraction_job_id, requirement_id);

-- CONSTRAINT: evidence_sources annotation_certificate_source_scope
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT annotation_certificate_source_scope UNIQUE (id, task_id, task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id);

-- CONSTRAINT: annotation_materials annotation_materials_rendition_id_key
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_rendition_id_key UNIQUE (rendition_id);

-- CONSTRAINT: annotation_materials annotation_materials_request_id_key
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_request_id_key UNIQUE (request_id);

-- CONSTRAINT: annotation_materials annotation_materials_task_id_extraction_job_id_id_key
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_task_id_extraction_job_id_id_key UNIQUE (task_id, extraction_job_id, id);

-- CONSTRAINT: annotation_materials annotation_materials_task_id_id_key
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: annotation_materials annotation_materials_pkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_pkey PRIMARY KEY (id);

-- CONSTRAINT: annotation_releases annotation_releases_evidence_id_card_revision_id_app_key
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_evidence_id_card_revision_id_app_key UNIQUE (evidence_id, card_revision_id, approval_sha256, renderer_identity);

-- CONSTRAINT: annotation_releases annotation_releases_rendition_id_key
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_rendition_id_key UNIQUE (rendition_id);

-- CONSTRAINT: annotation_releases annotation_releases_task_id_id_key
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: annotation_releases annotation_releases_pkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_renditions annotation_rendition_scope
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT annotation_rendition_scope UNIQUE (task_id, extraction_job_id, asset_id, id);

-- CONSTRAINT: annotation_requests annotation_requests_job_id_key
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_job_id_key UNIQUE (job_id);

-- CONSTRAINT: annotation_requests annotation_requests_task_id_actor_user_id_request_id_key
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_actor_user_id_request_id_key UNIQUE (task_id, request_id);

-- CONSTRAINT: annotation_requests annotation_requests_task_id_extraction_job_id_requir_key
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_extraction_job_id_requir_key UNIQUE (task_id, extraction_job_id, requirement_id, card_id, id);

-- CONSTRAINT: annotation_requests annotation_requests_task_id_id_key
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: annotation_requests annotation_requests_pkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_pkey PRIMARY KEY (id);

-- CONSTRAINT: api_tokens api_tokens_digest_key
ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_digest_key UNIQUE (digest);

-- CONSTRAINT: api_tokens api_tokens_pkey
ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id);

-- CONSTRAINT: attachment_archives attachment_archives_pkey
ALTER TABLE ONLY public.attachment_archives
    ADD CONSTRAINT attachment_archives_pkey PRIMARY KEY (id);

-- CONSTRAINT: attachment_file_parts attachment_file_parts_attachment_file_id_ordinal_key
ALTER TABLE ONLY public.attachment_file_parts
    ADD CONSTRAINT attachment_file_parts_attachment_file_id_ordinal_key UNIQUE (attachment_file_id, ordinal);

-- CONSTRAINT: attachment_file_parts attachment_file_parts_pkey
ALTER TABLE ONLY public.attachment_file_parts
    ADD CONSTRAINT attachment_file_parts_pkey PRIMARY KEY (id);

-- CONSTRAINT: attachment_files attachment_files_attachment_revision_id_key
ALTER TABLE ONLY public.attachment_files
    ADD CONSTRAINT attachment_files_attachment_revision_id_key UNIQUE (attachment_revision_id);

-- CONSTRAINT: attachment_files attachment_files_id_attachment_id_attachment_revisio_key
ALTER TABLE ONLY public.attachment_files
    ADD CONSTRAINT attachment_files_id_attachment_id_attachment_revisio_key UNIQUE (id, attachment_id, attachment_revision_id);

-- CONSTRAINT: attachment_files attachment_files_pkey
ALTER TABLE ONLY public.attachment_files
    ADD CONSTRAINT attachment_files_pkey PRIMARY KEY (id);

-- CONSTRAINT: profile_attachment_links attachment_link_binding
ALTER TABLE ONLY public.profile_attachment_links
    ADD CONSTRAINT attachment_link_binding UNIQUE (id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id);

-- CONSTRAINT: attachment_privacy_holds attachment_privacy_holds_task_id_evidence_source_id__key
ALTER TABLE ONLY public.attachment_privacy_holds
    ADD CONSTRAINT attachment_privacy_holds_task_id_evidence_source_id__key UNIQUE (task_id, evidence_source_id, id);

-- CONSTRAINT: attachment_privacy_holds attachment_privacy_holds_pkey
ALTER TABLE ONLY public.attachment_privacy_holds
    ADD CONSTRAINT attachment_privacy_holds_pkey PRIMARY KEY (id);

-- CONSTRAINT: attachment_reviews attachment_reviews_attachment_id_attachment_revision_key
ALTER TABLE ONLY public.attachment_reviews
    ADD CONSTRAINT attachment_reviews_attachment_id_attachment_revision_key UNIQUE (attachment_id, attachment_revision_id, file_id, id);

-- CONSTRAINT: attachment_reviews attachment_reviews_attachment_revision_id_id_key
ALTER TABLE ONLY public.attachment_reviews
    ADD CONSTRAINT attachment_reviews_attachment_revision_id_id_key UNIQUE (attachment_revision_id, id);

-- CONSTRAINT: attachment_reviews attachment_reviews_pkey
ALTER TABLE ONLY public.attachment_reviews
    ADD CONSTRAINT attachment_reviews_pkey PRIMARY KEY (id);

-- CONSTRAINT: attachment_revisions attachment_revisions_attachment_id_id_key
ALTER TABLE ONLY public.attachment_revisions
    ADD CONSTRAINT attachment_revisions_attachment_id_id_key UNIQUE (attachment_id, id);

-- CONSTRAINT: attachment_revisions attachment_revisions_attachment_id_revision_key
ALTER TABLE ONLY public.attachment_revisions
    ADD CONSTRAINT attachment_revisions_attachment_id_revision_key UNIQUE (attachment_id, revision);

-- CONSTRAINT: attachment_revisions attachment_revisions_pkey
ALTER TABLE ONLY public.attachment_revisions
    ADD CONSTRAINT attachment_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_attachments attachment_selection_source_binding
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT attachment_selection_source_binding UNIQUE (id, task_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, file_id, approval_id);

-- CONSTRAINT: evidence_sources attachment_source_task
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT attachment_source_task UNIQUE (task_id, id);

-- CONSTRAINT: task_org_profiles attachment_task_profile_binding
ALTER TABLE ONLY public.task_org_profiles
    ADD CONSTRAINT attachment_task_profile_binding UNIQUE (id, task_id, profile_revision_id, lot);

-- CONSTRAINT: audit_logs audit_logs_pkey
ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_document_pages bid_document_pages_preparation_id_document_id_page_key
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_preparation_id_document_id_page_key UNIQUE (preparation_id, document_id, page);

-- CONSTRAINT: bid_document_pages bid_document_pages_task_id_id_key
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_document_pages bid_document_pages_task_id_submission_id_id_key
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_task_id_submission_id_id_key UNIQUE (task_id, submission_id, id);

-- CONSTRAINT: bid_document_pages bid_document_pages_pkey
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_authorized_by_request_id_key
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_authorized_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_submission_id_revision_key
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_submission_id_revision_key UNIQUE (submission_id, revision);

-- CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_id_key
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_submission_id_sn_key
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_submission_id_sn_key UNIQUE (task_id, submission_id, snapshot_id, id);

-- CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_pkey
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_pages_authorization_id_page__key
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_pages_authorization_id_page__key UNIQUE (authorization_id, page_id);

-- CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_pages_task_id_id_key
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_pages_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_pages_pkey
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_pages_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_pdf_validations bid_pdf_validations_preparation_id_document_id_key
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_preparation_id_document_id_key UNIQUE (preparation_id, document_id);

-- CONSTRAINT: bid_pdf_validations bid_pdf_validations_task_id_id_key
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_pdf_validations bid_pdf_validations_pkey
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_preparation_publications bid_preparation_publications_preparation_id_key
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_preparation_id_key UNIQUE (preparation_id);

-- CONSTRAINT: bid_preparation_publications bid_preparation_publications_submission_id_key
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_submission_id_key UNIQUE (submission_id);

-- CONSTRAINT: bid_preparation_publications bid_preparation_publications_task_id_id_key
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_preparation_publications bid_preparation_publications_pkey
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_preparation_trust bid_preparation_trust_preparation_id_key
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_preparation_id_key UNIQUE (preparation_id);

-- CONSTRAINT: bid_preparation_trust bid_preparation_trust_task_id_id_key
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_preparation_trust bid_preparation_trust_task_id_submission_id_preparat_key
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_task_id_submission_id_preparat_key UNIQUE (task_id, submission_id, preparation_id, trust_store_sha256);

-- CONSTRAINT: bid_preparation_trust bid_preparation_trust_pkey
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_preparations bid_preparations_created_by_request_id_key
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_created_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_preparations bid_preparations_job_id_key
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_job_id_key UNIQUE (job_id);

-- CONSTRAINT: bid_preparations bid_preparations_task_id_id_key
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_preparations bid_preparations_task_id_submission_id_id_job_id_inp_key
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_submission_id_id_job_id_inp_key UNIQUE (task_id, submission_id, id, job_id, input_hash);

-- CONSTRAINT: bid_preparations bid_preparations_task_id_submission_id_id_key
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_submission_id_id_key UNIQUE (task_id, submission_id, id);

-- CONSTRAINT: bid_preparations bid_preparations_pkey
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_prepared_documents bid_prepared_documents_task_id_id_key
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_prepared_documents bid_prepared_documents_task_id_submission_id_prepara_key
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_task_id_submission_id_prepara_key UNIQUE (task_id, submission_id, preparation_id, document_id);

-- CONSTRAINT: bid_prepared_documents bid_prepared_documents_pkey
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_authorized_by_request_id_key
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_authorized_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_submission_id_revision_key
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_submission_id_revision_key UNIQUE (submission_id, revision);

-- CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_id_key
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_submission_id_pr_key
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_submission_id_pr_key UNIQUE (task_id, submission_id, presence_preparation_id, id);

-- CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_pkey
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_image_authorization_id_image_key
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_image_authorization_id_image_key UNIQUE (authorization_id, image_id);

-- CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_images_task_id_id_key
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_images_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_images_pkey
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_images_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_presence_images bid_presence_images_presence_preparation_id_page_id_key
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_presence_preparation_id_page_id_key UNIQUE (presence_preparation_id, page_id);

-- CONSTRAINT: bid_presence_images bid_presence_images_task_id_id_key
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_presence_images bid_presence_images_task_id_submission_id_presence_p_key
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_task_id_submission_id_presence_p_key UNIQUE (task_id, submission_id, presence_preparation_id, id);

-- CONSTRAINT: bid_presence_images bid_presence_images_pkey
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_presence_preparations bid_presence_preparations_submission_id_manifest_sha_key
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_submission_id_manifest_sha_key UNIQUE (submission_id, manifest_sha256);

-- CONSTRAINT: bid_presence_preparations bid_presence_preparations_task_id_id_key
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_presence_preparations bid_presence_preparations_task_id_submission_id_id_key
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_task_id_submission_id_id_key UNIQUE (task_id, submission_id, id);

-- CONSTRAINT: bid_presence_preparations bid_presence_preparations_pkey
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_redacted_pages bid_redacted_pages_snapshot_id_page_id_key
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_snapshot_id_page_id_key UNIQUE (snapshot_id, page_id);

-- CONSTRAINT: bid_redacted_pages bid_redacted_pages_task_id_id_key
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_redacted_pages bid_redacted_pages_task_id_submission_id_snapshot_id_key
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_task_id_submission_id_snapshot_id_key UNIQUE (task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256);

-- CONSTRAINT: bid_redacted_pages bid_redacted_pages_pkey
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_submission_id_manifest_sha25_key
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_submission_id_manifest_sha25_key UNIQUE (submission_id, manifest_sha256);

-- CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_task_id_id_key
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_task_id_submission_id_prepar_key
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_task_id_submission_id_prepar_key UNIQUE (task_id, submission_id, preparation_id, id);

-- CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_pkey
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_finding_events bid_review_finding_events_decided_by_request_id_key
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_decided_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_review_finding_events bid_review_finding_events_finding_id_revision_key
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_finding_id_revision_key UNIQUE (finding_id, revision);

-- CONSTRAINT: bid_review_finding_events bid_review_finding_events_task_id_id_key
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_finding_events bid_review_finding_events_task_id_submission_id_revi_key
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_task_id_submission_id_revi_key UNIQUE (task_id, submission_id, review_id, finding_id, id);

-- CONSTRAINT: bid_review_finding_events bid_review_finding_events_pkey
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_finding_id_kind_ordinal_key
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_finding_id_kind_ordinal_key UNIQUE (finding_id, kind, ordinal);

-- CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_id_key
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_pkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_findings bid_review_findings_review_id_ordinal_key
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_review_id_ordinal_key UNIQUE (review_id, ordinal);

-- CONSTRAINT: bid_review_findings bid_review_findings_task_id_id_key
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_findings bid_review_findings_task_id_submission_id_review_id__key
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_task_id_submission_id_review_id__key UNIQUE (task_id, submission_id, review_id, id);

-- CONSTRAINT: bid_review_findings bid_review_findings_pkey
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_pkey PRIMARY KEY (id);

-- CONSTRAINT: jobs bid_review_job_task
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT bid_review_job_task UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_name_lists bid_review_name_lists_created_by_request_id_key
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_created_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_review_name_lists bid_review_name_lists_submission_id_revision_key
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_submission_id_revision_key UNIQUE (submission_id, revision);

-- CONSTRAINT: bid_review_name_lists bid_review_name_lists_task_id_id_key
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_name_lists bid_review_name_lists_pkey
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_obligations bid_review_obligation_run_key
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligation_run_key UNIQUE (task_id, submission_id, review_id, id);

-- CONSTRAINT: bid_review_obligations bid_review_obligations_review_id_ordinal_key
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_review_id_ordinal_key UNIQUE (review_id, ordinal);

-- CONSTRAINT: bid_review_obligations bid_review_obligations_task_id_id_key
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_obligations bid_review_obligations_pkey
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_pdf_validations bid_review_pdf_validation_key
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_review_pdf_validation_key UNIQUE (task_id, submission_id, preparation_id, document_id, id);

-- CONSTRAINT: bid_review_publications bid_review_publications_review_id_key
ALTER TABLE ONLY public.bid_review_publications
    ADD CONSTRAINT bid_review_publications_review_id_key UNIQUE (review_id);

-- CONSTRAINT: bid_review_publications bid_review_publications_task_id_id_key
ALTER TABLE ONLY public.bid_review_publications
    ADD CONSTRAINT bid_review_publications_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_publications bid_review_publications_pkey
ALTER TABLE ONLY public.bid_review_publications
    ADD CONSTRAINT bid_review_publications_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_report_artifacts bid_review_report_artifacts_snapshot_id_format_key
ALTER TABLE ONLY public.bid_review_report_artifacts
    ADD CONSTRAINT bid_review_report_artifacts_snapshot_id_format_key UNIQUE (snapshot_id, format);

-- CONSTRAINT: bid_review_report_artifacts bid_review_report_artifacts_task_id_id_key
ALTER TABLE ONLY public.bid_review_report_artifacts
    ADD CONSTRAINT bid_review_report_artifacts_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_report_artifacts bid_review_report_artifacts_pkey
ALTER TABLE ONLY public.bid_review_report_artifacts
    ADD CONSTRAINT bid_review_report_artifacts_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_created_by_request_id_key
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_created_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_job_id_key
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_job_id_key UNIQUE (job_id);

-- CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_id_key
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_submission_id_re_key
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_submission_id_re_key UNIQUE (task_id, submission_id, review_id, id);

-- CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_pkey
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_required_locations bid_review_required_locations_review_id_ordinal_key
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_locations_review_id_ordinal_key UNIQUE (review_id, ordinal);

-- CONSTRAINT: bid_review_required_locations bid_review_required_locations_task_id_id_key
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_locations_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_required_locations bid_review_required_locations_pkey
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_locations_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_runs bid_review_runs_created_by_request_id_key
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_created_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_review_runs bid_review_runs_job_id_key
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_job_id_key UNIQUE (job_id);

-- CONSTRAINT: bid_review_runs bid_review_runs_task_id_id_key
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_runs bid_review_runs_task_id_submission_id_id_key
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_submission_id_id_key UNIQUE (task_id, submission_id, id);

-- CONSTRAINT: bid_review_runs bid_review_runs_pkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_review_signing_requirements bid_review_signing_requiremen_review_id_candidate_id_key
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requiremen_review_id_candidate_id_key UNIQUE (review_id, candidate_id);

-- CONSTRAINT: bid_review_signing_requirements bid_review_signing_requiremen_task_id_submission_id__key
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requiremen_task_id_submission_id__key UNIQUE (task_id, submission_id, review_id, id);

-- CONSTRAINT: bid_review_signing_requirements bid_review_signing_requirements_review_id_ordinal_key
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requirements_review_id_ordinal_key UNIQUE (review_id, ordinal);

-- CONSTRAINT: bid_review_signing_requirements bid_review_signing_requirements_task_id_id_key
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requirements_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_review_signing_requirements bid_review_signing_requirements_pkey
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requirements_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_signing_candidates bid_signing_candidates_preparation_id_ordinal_key
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_preparation_id_ordinal_key UNIQUE (preparation_id, ordinal);

-- CONSTRAINT: bid_signing_candidates bid_signing_candidates_task_id_id_key
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_signing_candidates bid_signing_candidates_pkey
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_document_pages bid_signing_page_identity
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_signing_page_identity UNIQUE (task_id, submission_id, preparation_id, document_id, page, id);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_file_id_key
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_file_id_key UNIQUE (file_id);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_submission_id_ordinal_key
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_submission_id_ordinal_key UNIQUE (submission_id, ordinal);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_submission_id_role_sha256_key
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_submission_id_role_sha256_key UNIQUE (submission_id, role, sha256);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_task_id_id_key
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_task_id_submission_id_id_key
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_task_id_submission_id_id_key UNIQUE (task_id, submission_id, id);

-- CONSTRAINT: bid_submission_documents bid_submission_documents_pkey
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_pkey PRIMARY KEY (id);

-- CONSTRAINT: bid_submissions bid_submissions_created_by_request_id_key
ALTER TABLE ONLY public.bid_submissions
    ADD CONSTRAINT bid_submissions_created_by_request_id_key UNIQUE (request_id);

-- CONSTRAINT: bid_submissions bid_submissions_task_id_id_key
ALTER TABLE ONLY public.bid_submissions
    ADD CONSTRAINT bid_submissions_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: bid_submissions bid_submissions_task_id_revision_key
ALTER TABLE ONLY public.bid_submissions
    ADD CONSTRAINT bid_submissions_task_id_revision_key UNIQUE (task_id, revision);

-- CONSTRAINT: bid_submissions bid_submissions_pkey
ALTER TABLE ONLY public.bid_submissions
    ADD CONSTRAINT bid_submissions_pkey PRIMARY KEY (id);

-- CONSTRAINT: card_evidence_links card_evidence_links_revision_id_evidence_id_key
ALTER TABLE ONLY public.card_evidence_links
    ADD CONSTRAINT card_evidence_links_revision_id_evidence_id_key UNIQUE (revision_id, evidence_id);

-- CONSTRAINT: card_evidence_links card_evidence_links_pkey
ALTER TABLE ONLY public.card_evidence_links
    ADD CONSTRAINT card_evidence_links_pkey PRIMARY KEY (id);

-- CONSTRAINT: card_generation_runs card_generation_runs_generation_job_id_key
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_generation_job_id_key UNIQUE (generation_job_id);

-- CONSTRAINT: card_generation_runs card_generation_runs_pkey
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: certificate_file_parts certificate_file_parts_certificate_file_id_ordinal_key
ALTER TABLE ONLY public.certificate_file_parts
    ADD CONSTRAINT certificate_file_parts_certificate_file_id_ordinal_key UNIQUE (certificate_file_id, ordinal);

-- CONSTRAINT: certificate_file_parts certificate_file_parts_pkey
ALTER TABLE ONLY public.certificate_file_parts
    ADD CONSTRAINT certificate_file_parts_pkey PRIMARY KEY (id);

-- CONSTRAINT: certificate_files certificate_file_source_binding
ALTER TABLE ONLY public.certificate_files
    ADD CONSTRAINT certificate_file_source_binding UNIQUE (id, certificate_id, certificate_revision_id);

-- CONSTRAINT: certificate_files certificate_files_certificate_revision_id_key
ALTER TABLE ONLY public.certificate_files
    ADD CONSTRAINT certificate_files_certificate_revision_id_key UNIQUE (certificate_revision_id);

-- CONSTRAINT: certificate_files certificate_files_pkey
ALTER TABLE ONLY public.certificate_files
    ADD CONSTRAINT certificate_files_pkey PRIMARY KEY (id);

-- CONSTRAINT: certificate_revisions certificate_revisions_certificate_id_id_key
ALTER TABLE ONLY public.certificate_revisions
    ADD CONSTRAINT certificate_revisions_certificate_id_id_key UNIQUE (certificate_id, id);

-- CONSTRAINT: certificate_revisions certificate_revisions_certificate_id_revision_key
ALTER TABLE ONLY public.certificate_revisions
    ADD CONSTRAINT certificate_revisions_certificate_id_revision_key UNIQUE (certificate_id, revision);

-- CONSTRAINT: certificate_revisions certificate_revisions_pkey
ALTER TABLE ONLY public.certificate_revisions
    ADD CONSTRAINT certificate_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: certificates certificates_pkey
ALTER TABLE ONLY public.certificates
    ADD CONSTRAINT certificates_pkey PRIMARY KEY (id);

-- CONSTRAINT: check_certificate_items check_certificate_items_id_task_id_key
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_certificate_items check_certificate_items_report_id_certificate_id_che_key
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_report_id_certificate_id_che_key UNIQUE (report_id, certificate_id, check_item_id);

-- CONSTRAINT: check_certificate_items check_certificate_items_pkey
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_certificates check_certificate_selection
ALTER TABLE ONLY public.task_certificates
    ADD CONSTRAINT check_certificate_selection UNIQUE (id, task_id, certificate_revision_id);

-- CONSTRAINT: check_certificates check_certificates_id_task_id_key
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_certificates check_certificates_id_task_id_report_id_key
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_id_task_id_report_id_key UNIQUE (id, task_id, report_id);

-- CONSTRAINT: check_certificates check_certificates_report_id_task_certificate_id_key
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_report_id_task_certificate_id_key UNIQUE (report_id, task_certificate_id);

-- CONSTRAINT: check_certificates check_certificates_pkey
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_pkey PRIMARY KEY (id);

-- CONSTRAINT: chunks check_chunk_binding
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT check_chunk_binding UNIQUE (id, task_id, document_id);

-- CONSTRAINT: check_decisions check_decisions_finding_id_revision_key
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_finding_id_revision_key UNIQUE (finding_id, revision);

-- CONSTRAINT: check_decisions check_decisions_id_task_id_key
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_decisions check_decisions_pkey
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: draft_runs check_draft_extraction
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT check_draft_extraction UNIQUE (id, task_id, extraction_job_id);

-- CONSTRAINT: draft_runs check_draft_task
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT check_draft_task UNIQUE (id, task_id);

-- CONSTRAINT: evidence check_evidence_task
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT check_evidence_task UNIQUE (id, task_id);

-- CONSTRAINT: check_finding_citations check_finding_citations_id_task_id_key
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_finding_citations check_finding_citations_pkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_pkey PRIMARY KEY (id);

-- CONSTRAINT: check_findings check_findings_id_task_id_key
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_findings check_findings_id_task_id_report_id_key
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_id_task_id_report_id_key UNIQUE (id, task_id, report_id);

-- CONSTRAINT: check_findings check_findings_pkey
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_pkey PRIMARY KEY (id);

-- CONSTRAINT: check_items check_items_id_task_id_key
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_items check_items_id_task_id_report_id_key
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_id_task_id_report_id_key UNIQUE (id, task_id, report_id);

-- CONSTRAINT: check_items check_items_id_task_id_report_id_requirement_id_key
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_id_task_id_report_id_requirement_id_key UNIQUE (id, task_id, report_id, requirement_id);

-- CONSTRAINT: check_items check_items_report_id_requirement_id_key
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_report_id_requirement_id_key UNIQUE (report_id, requirement_id);

-- CONSTRAINT: check_items check_items_pkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: jobs check_job_binding
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT check_job_binding UNIQUE (id, task_id, document_id);

-- CONSTRAINT: response_items check_response_requirement
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT check_response_requirement UNIQUE (id, draft_id, requirement_id);

-- CONSTRAINT: response_items check_response_revision
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT check_response_revision UNIQUE (id, draft_id, card_revision_id);

-- CONSTRAINT: check_runs check_runs_id_task_id_draft_id_extraction_job_id_key
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_id_task_id_draft_id_extraction_job_id_key UNIQUE (id, task_id, draft_id, extraction_job_id);

-- CONSTRAINT: check_runs check_runs_id_task_id_key
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: check_runs check_runs_job_id_key
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_job_id_key UNIQUE (job_id);

-- CONSTRAINT: check_runs check_runs_pkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: chunks chunks_document_seq
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_document_seq UNIQUE (document_id, seq);

-- CONSTRAINT: chunks chunks_pkey
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);

-- CONSTRAINT: confidential_fields confidential_fields_key_key
ALTER TABLE ONLY public.confidential_fields
    ADD CONSTRAINT confidential_fields_key_key UNIQUE (key);

-- CONSTRAINT: confidential_fields confidential_fields_pkey
ALTER TABLE ONLY public.confidential_fields
    ADD CONSTRAINT confidential_fields_pkey PRIMARY KEY (id);

-- CONSTRAINT: confidential_values confidential_values_pkey
ALTER TABLE ONLY public.confidential_values
    ADD CONSTRAINT confidential_values_pkey PRIMARY KEY (id);

-- CONSTRAINT: documents documents_task_id_sha256_key
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_task_id_sha256_key UNIQUE (task_id, sha256);

-- CONSTRAINT: documents documents_pkey
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

-- CONSTRAINT: draft_runs draft_runs_generation_job_id_key
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_generation_job_id_key UNIQUE (generation_job_id);

-- CONSTRAINT: draft_runs draft_runs_task_id_extraction_job_id_input_hash_key
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_task_id_extraction_job_id_input_hash_key UNIQUE (task_id, extraction_job_id, input_hash);

-- CONSTRAINT: draft_runs draft_runs_pkey
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: evidence evidence_card_id_id_key
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_card_id_id_key UNIQUE (card_id, id);

-- CONSTRAINT: evidence evidence_pkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);

-- CONSTRAINT: evidence_sources evidence_sources_pkey
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT evidence_sources_pkey PRIMARY KEY (id);

-- CONSTRAINT: export_render_candidates export_export_render_candidates_parent_0
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_export_render_candidates_parent_0 UNIQUE (run_id, id, plaintext_sha256, size_bytes);

-- CONSTRAINT: export_runs export_export_runs_parent_0
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_export_runs_parent_0 UNIQUE (id, render_job_id, input_hash, manifest_hash, renderer_profile);

-- CONSTRAINT: export_runs export_export_runs_parent_1
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_export_runs_parent_1 UNIQUE (id, task_id, mode, input_hash, manifest_hash);

-- CONSTRAINT: export_render_candidates export_render_candidates_run_id_attempt_id_key
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_render_candidates_run_id_attempt_id_key UNIQUE (run_id, attempt_id);

-- CONSTRAINT: export_render_candidates export_render_candidates_run_id_id_key
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_render_candidates_run_id_id_key UNIQUE (run_id, id);

-- CONSTRAINT: export_render_candidates export_render_candidates_pkey
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_render_candidates_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_items export_response_items_parent_0
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT export_response_items_parent_0 UNIQUE (id, draft_id, requirement_id, kind);

-- CONSTRAINT: export_run_evidence export_run_evidence_run_item_id_evidence_id_key
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_run_item_id_evidence_id_key UNIQUE (run_item_id, evidence_id);

-- CONSTRAINT: export_run_evidence export_run_evidence_pkey
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_pkey PRIMARY KEY (id);

-- CONSTRAINT: export_run_items export_run_items_run_id_id_card_revision_id_key
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_run_id_id_card_revision_id_key UNIQUE (run_id, id, card_revision_id);

-- CONSTRAINT: export_run_items export_run_items_run_id_ordinal_key
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_run_id_ordinal_key UNIQUE (run_id, ordinal);

-- CONSTRAINT: export_run_items export_run_items_run_id_requirement_id_key
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_run_id_requirement_id_key UNIQUE (run_id, requirement_id);

-- CONSTRAINT: export_run_items export_run_items_pkey
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: export_runs export_runs_id_draft_run_id_key
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_id_draft_run_id_key UNIQUE (id, draft_run_id);

-- CONSTRAINT: export_runs export_runs_input_hash_manifest_hash_key
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_input_hash_manifest_hash_key UNIQUE (input_hash, manifest_hash);

-- CONSTRAINT: export_runs export_runs_render_job_id_key
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_render_job_id_key UNIQUE (render_job_id);

-- CONSTRAINT: export_runs export_runs_pkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_templates export_task_templates_parent_0
ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT export_task_templates_parent_0 UNIQUE (id, task_id, template_revision_id);

-- CONSTRAINT: export_template_bindings export_template_bindings_id_template_revision_id_key
ALTER TABLE ONLY public.export_template_bindings
    ADD CONSTRAINT export_template_bindings_id_template_revision_id_key UNIQUE (id, template_revision_id);

-- CONSTRAINT: export_template_bindings export_template_bindings_template_revision_id_bindin_key
ALTER TABLE ONLY public.export_template_bindings
    ADD CONSTRAINT export_template_bindings_template_revision_id_bindin_key UNIQUE (template_revision_id, binding_hash);

-- CONSTRAINT: export_template_bindings export_template_bindings_pkey
ALTER TABLE ONLY public.export_template_bindings
    ADD CONSTRAINT export_template_bindings_pkey PRIMARY KEY (id);

-- CONSTRAINT: exports exports_run_id_key
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_run_id_key UNIQUE (run_id);

-- CONSTRAINT: exports exports_pkey
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_pkey PRIMARY KEY (id);

-- CONSTRAINT: feature_revisions feature_revisions_feature_id_id_key
ALTER TABLE ONLY public.feature_revisions
    ADD CONSTRAINT feature_revisions_feature_id_id_key UNIQUE (feature_id, id);

-- CONSTRAINT: feature_revisions feature_revisions_feature_id_revision_key
ALTER TABLE ONLY public.feature_revisions
    ADD CONSTRAINT feature_revisions_feature_id_revision_key UNIQUE (feature_id, revision);

-- CONSTRAINT: feature_revisions feature_revisions_pkey
ALTER TABLE ONLY public.feature_revisions
    ADD CONSTRAINT feature_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: features features_pkey
ALTER TABLE ONLY public.features
    ADD CONSTRAINT features_pkey PRIMARY KEY (id);

-- CONSTRAINT: jobs jobs_cache_key_key
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_cache_key_key UNIQUE (cache_key);

-- CONSTRAINT: jobs jobs_pkey
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

-- CONSTRAINT: confidential_field_search_tokens management_confidential_token_prefix
ALTER TABLE ONLY public.confidential_field_search_tokens
    ADD CONSTRAINT management_confidential_token_prefix PRIMARY KEY (token, field_id);

-- CONSTRAINT: memories memories_source_feedback_event_id_generator_version_key
ALTER TABLE ONLY public.memories
    ADD CONSTRAINT memories_source_feedback_event_id_generator_version_key UNIQUE (source_feedback_event_id, generator_version);

-- CONSTRAINT: memories memories_pkey
ALTER TABLE ONLY public.memories
    ADD CONSTRAINT memories_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_call_inputs memory_call_inputs_job_id_run_id_call_id_key
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_job_id_run_id_call_id_key UNIQUE (job_id, run_id, call_id);

-- CONSTRAINT: memory_call_inputs memory_call_inputs_pkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_eval_samples memory_eval_samples_feedback_event_id_generator_vers_key
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_feedback_event_id_generator_vers_key UNIQUE (feedback_event_id, generator_version);

-- CONSTRAINT: memory_eval_samples memory_eval_samples_pkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_feedback_events memory_feedback_events_card_id_after_revision_id_kin_key
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_card_id_after_revision_id_kin_key UNIQUE (card_id, after_revision_id, kind);

-- CONSTRAINT: memory_feedback_events memory_feedback_events_pkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_retrieval_items memory_retrieval_items_retrieval_id_memory_revision__key
ALTER TABLE ONLY public.memory_retrieval_items
    ADD CONSTRAINT memory_retrieval_items_retrieval_id_memory_revision__key UNIQUE (retrieval_id, memory_revision_id);

-- CONSTRAINT: memory_retrieval_items memory_retrieval_items_pkey
ALTER TABLE ONLY public.memory_retrieval_items
    ADD CONSTRAINT memory_retrieval_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_retrievals memory_retrievals_pkey
ALTER TABLE ONLY public.memory_retrievals
    ADD CONSTRAINT memory_retrievals_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_revisions memory_revisions_memory_id_id_key
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_memory_id_id_key UNIQUE (memory_id, id);

-- CONSTRAINT: memory_revisions memory_revisions_memory_id_id_revision_key
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_memory_id_id_revision_key UNIQUE (memory_id, id, revision);

-- CONSTRAINT: memory_revisions memory_revisions_memory_id_revision_key
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_memory_id_revision_key UNIQUE (memory_id, revision);

-- CONSTRAINT: memory_revisions memory_revisions_pkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: memory_scope_epochs memory_scope_epochs_pkey
ALTER TABLE ONLY public.memory_scope_epochs
    ADD CONSTRAINT memory_scope_epochs_pkey PRIMARY KEY (scope);

-- CONSTRAINT: usage_records memory_usage_call_binding
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT memory_usage_call_binding UNIQUE (id, job_id, run_id, call_id);

-- CONSTRAINT: org_profile_revisions org_profile_revisions_profile_id_id_key
ALTER TABLE ONLY public.org_profile_revisions
    ADD CONSTRAINT org_profile_revisions_profile_id_id_key UNIQUE (profile_id, id);

-- CONSTRAINT: org_profile_revisions org_profile_revisions_profile_id_revision_key
ALTER TABLE ONLY public.org_profile_revisions
    ADD CONSTRAINT org_profile_revisions_profile_id_revision_key UNIQUE (profile_id, revision);

-- CONSTRAINT: org_profile_revisions org_profile_revisions_pkey
ALTER TABLE ONLY public.org_profile_revisions
    ADD CONSTRAINT org_profile_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: org_profiles org_profiles_pkey
ALTER TABLE ONLY public.org_profiles
    ADD CONSTRAINT org_profiles_pkey PRIMARY KEY (id);

-- CONSTRAINT: platform_credentials platform_credentials_name_key
ALTER TABLE ONLY public.platform_credentials
    ADD CONSTRAINT platform_credentials_name_key UNIQUE (name);

-- CONSTRAINT: platform_credentials platform_credentials_pkey
ALTER TABLE ONLY public.platform_credentials
    ADD CONSTRAINT platform_credentials_pkey PRIMARY KEY (id);

-- CONSTRAINT: platform_trust_anchors platform_trust_anchors_fingerprint_sha256_key
ALTER TABLE ONLY public.platform_trust_anchors
    ADD CONSTRAINT platform_trust_anchors_fingerprint_sha256_key UNIQUE (fingerprint_sha256);

-- CONSTRAINT: platform_trust_anchors platform_trust_anchors_pkey
ALTER TABLE ONLY public.platform_trust_anchors
    ADD CONSTRAINT platform_trust_anchors_pkey PRIMARY KEY (id);

-- CONSTRAINT: product_revisions product_revisions_product_id_id_key
ALTER TABLE ONLY public.product_revisions
    ADD CONSTRAINT product_revisions_product_id_id_key UNIQUE (product_id, id);

-- CONSTRAINT: product_revisions product_revisions_product_id_revision_key
ALTER TABLE ONLY public.product_revisions
    ADD CONSTRAINT product_revisions_product_id_revision_key UNIQUE (product_id, revision);

-- CONSTRAINT: product_revisions product_revisions_pkey
ALTER TABLE ONLY public.product_revisions
    ADD CONSTRAINT product_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: products products_pkey
ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (id);

-- CONSTRAINT: profile_attachment_links profile_attachment_links_pkey
ALTER TABLE ONLY public.profile_attachment_links
    ADD CONSTRAINT profile_attachment_links_pkey PRIMARY KEY (id);

-- CONSTRAINT: prototype_decision_batches prototype_decision_batches_task_id_id_key
ALTER TABLE ONLY public.prototype_decision_batches
    ADD CONSTRAINT prototype_decision_batches_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: prototype_decision_batches prototype_decision_batches_task_id_idempotency_key_key
ALTER TABLE ONLY public.prototype_decision_batches
    ADD CONSTRAINT prototype_decision_batches_task_id_idempotency_key_key UNIQUE (task_id, idempotency_key);

-- CONSTRAINT: prototype_decision_batches prototype_decision_batches_pkey
ALTER TABLE ONLY public.prototype_decision_batches
    ADD CONSTRAINT prototype_decision_batches_pkey PRIMARY KEY (id);

-- CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_batch_id_evidence_id_key
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_batch_id_evidence_id_key UNIQUE (batch_id, evidence_id);

-- CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_evidence_id_id_key
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_evidence_id_id_key UNIQUE (evidence_id, id);

-- CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_task_id_id_key
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_pkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: provider_configs provider_configs_capability_revision_key
ALTER TABLE ONLY public.provider_configs
    ADD CONSTRAINT provider_configs_capability_revision_key UNIQUE (capability, revision);

-- CONSTRAINT: provider_configs provider_configs_pkey
ALTER TABLE ONLY public.provider_configs
    ADD CONSTRAINT provider_configs_pkey PRIMARY KEY (id);

-- CONSTRAINT: requirement_review_events requirement_review_events_review_id_id_revision_key
ALTER TABLE ONLY public.requirement_review_events
    ADD CONSTRAINT requirement_review_events_review_id_id_revision_key UNIQUE (review_id, id, revision);

-- CONSTRAINT: requirement_review_events requirement_review_events_review_id_revision_key
ALTER TABLE ONLY public.requirement_review_events
    ADD CONSTRAINT requirement_review_events_review_id_revision_key UNIQUE (review_id, revision);

-- CONSTRAINT: requirement_review_events requirement_review_events_pkey
ALTER TABLE ONLY public.requirement_review_events
    ADD CONSTRAINT requirement_review_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: requirement_review_requests requirement_review_requests_actor_user_id_request_id_key
ALTER TABLE ONLY public.requirement_review_requests
    ADD CONSTRAINT requirement_review_requests_actor_user_id_request_id_key UNIQUE (request_id);

-- CONSTRAINT: requirement_review_requests requirement_review_requests_pkey
ALTER TABLE ONLY public.requirement_review_requests
    ADD CONSTRAINT requirement_review_requests_pkey PRIMARY KEY (id);

-- CONSTRAINT: requirement_review_sets requirement_review_sets_extraction_job_id_key
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_extraction_job_id_key UNIQUE (extraction_job_id);

-- CONSTRAINT: requirement_review_sets requirement_review_sets_id_task_id_extraction_job_id_key
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_id_task_id_extraction_job_id_key UNIQUE (id, task_id, extraction_job_id);

-- CONSTRAINT: requirement_review_sets requirement_review_sets_task_id_extraction_job_id_key
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_task_id_extraction_job_id_key UNIQUE (task_id, extraction_job_id);

-- CONSTRAINT: requirement_review_sets requirement_review_sets_pkey
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_pkey PRIMARY KEY (id);

-- CONSTRAINT: requirement_reviews requirement_reviews_id_task_id_extraction_job_id_req_key
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_id_task_id_extraction_job_id_req_key UNIQUE (id, task_id, extraction_job_id, requirement_id);

-- CONSTRAINT: requirement_reviews requirement_reviews_requirement_id_key
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_requirement_id_key UNIQUE (requirement_id);

-- CONSTRAINT: requirement_reviews requirement_reviews_pkey
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_pkey PRIMARY KEY (id);

-- CONSTRAINT: requirements requirements_job_fingerprint
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_job_fingerprint UNIQUE (job_id, fingerprint);

-- CONSTRAINT: requirements requirements_pkey
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_pkey PRIMARY KEY (id);

-- CONSTRAINT: resource_lifecycle_events resource_lifecycle_events_pkey
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT resource_lifecycle_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_card_revisions response_card_revisions_card_id_id_key
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_card_id_id_key UNIQUE (card_id, id);

-- CONSTRAINT: response_card_revisions response_card_revisions_card_id_id_revision_key
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_card_id_id_revision_key UNIQUE (card_id, id, revision);

-- CONSTRAINT: response_card_revisions response_card_revisions_card_id_revision_key
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_card_id_revision_key UNIQUE (card_id, revision);

-- CONSTRAINT: response_card_revisions response_card_revisions_pkey
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_cards response_cards_id_task_id_key
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: response_cards response_cards_task_id_extraction_job_id_requirement_key
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_task_id_extraction_job_id_requirement_key UNIQUE (task_id, extraction_job_id, requirement_id);

-- CONSTRAINT: response_cards response_cards_pkey
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_items response_items_draft_id_requirement_id_key
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_draft_id_requirement_id_key UNIQUE (draft_id, requirement_id);

-- CONSTRAINT: response_items response_items_pkey
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: jobs response_job_task_binding
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT response_job_task_binding UNIQUE (id, task_id);

-- CONSTRAINT: requirements response_requirement_binding
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT response_requirement_binding UNIQUE (id, task_id, job_id);

-- CONSTRAINT: requirements rubric_requirement_task
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT rubric_requirement_task UNIQUE (id, task_id);

-- CONSTRAINT: sandbox_artifacts sandbox_artifacts_attempt_record_id_ordinal_key
ALTER TABLE ONLY public.sandbox_artifacts
    ADD CONSTRAINT sandbox_artifacts_attempt_record_id_ordinal_key UNIQUE (attempt_record_id, ordinal);

-- CONSTRAINT: sandbox_artifacts sandbox_artifacts_id_sandbox_run_id_input_id_task_id_key
ALTER TABLE ONLY public.sandbox_artifacts
    ADD CONSTRAINT sandbox_artifacts_id_sandbox_run_id_input_id_task_id_key UNIQUE (id, sandbox_run_id, input_id, task_id, attempt_record_id);

-- CONSTRAINT: sandbox_artifacts sandbox_artifacts_pkey
ALTER TABLE ONLY public.sandbox_artifacts
    ADD CONSTRAINT sandbox_artifacts_pkey PRIMARY KEY (id);

-- CONSTRAINT: sandbox_attempts sandbox_attempts_id_sandbox_run_id_input_id_task_id_key
ALTER TABLE ONLY public.sandbox_attempts
    ADD CONSTRAINT sandbox_attempts_id_sandbox_run_id_input_id_task_id_key UNIQUE (id, sandbox_run_id, input_id, task_id);

-- CONSTRAINT: sandbox_attempts sandbox_attempts_job_id_attempt_id_key
ALTER TABLE ONLY public.sandbox_attempts
    ADD CONSTRAINT sandbox_attempts_job_id_attempt_id_key UNIQUE (job_id, attempt_id);

-- CONSTRAINT: sandbox_attempts sandbox_attempts_pkey
ALTER TABLE ONLY public.sandbox_attempts
    ADD CONSTRAINT sandbox_attempts_pkey PRIMARY KEY (id);

-- CONSTRAINT: documents sandbox_document_task
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT sandbox_document_task UNIQUE (id, task_id);

-- CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_attempt_record_id_request_ord_key
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_attempt_record_id_request_ord_key UNIQUE (attempt_record_id, request_ordinal);

-- CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_id_attempt_record_id_key
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_id_attempt_record_id_key UNIQUE (id, attempt_record_id);

-- CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_pkey
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_pkey PRIMARY KEY (id);

-- CONSTRAINT: sandbox_inputs sandbox_inputs_id_task_id_document_id_key
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_id_task_id_document_id_key UNIQUE (id, task_id, document_id);

-- CONSTRAINT: sandbox_inputs sandbox_inputs_id_task_id_key
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: sandbox_inputs sandbox_inputs_pkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_pkey PRIMARY KEY (id);

-- CONSTRAINT: sandbox_runs sandbox_runs_capture_key_key
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_capture_key_key UNIQUE (capture_key);

-- CONSTRAINT: sandbox_runs sandbox_runs_id_input_id_task_id_job_id_key
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_id_input_id_task_id_job_id_key UNIQUE (id, input_id, task_id, job_id);

-- CONSTRAINT: sandbox_runs sandbox_runs_job_id_key
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_job_id_key UNIQUE (job_id);

-- CONSTRAINT: sandbox_runs sandbox_runs_request_hash_key
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_request_hash_key UNIQUE (request_hash);

-- CONSTRAINT: sandbox_runs sandbox_runs_pkey
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_item_citations score_item_citations_id_task_id_key
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_item_citations score_item_citations_pkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_report_item_responses score_report_item_responses_id_task_id_key
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_report_item_responses score_report_item_responses_score_item_id_response_i_key
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_score_item_id_response_i_key UNIQUE (score_item_id, response_item_id);

-- CONSTRAINT: score_report_item_responses score_report_item_responses_task_id_report_id_score__key
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_task_id_report_id_score__key UNIQUE (task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id);

-- CONSTRAINT: score_report_item_responses score_report_item_responses_pkey
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_report_items score_report_items_id_task_id_key
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_report_items score_report_items_id_task_id_report_id_draft_id_key
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_id_task_id_report_id_draft_id_key UNIQUE (id, task_id, report_id, draft_id);

-- CONSTRAINT: score_report_items score_report_items_id_task_id_report_id_key
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_id_task_id_report_id_key UNIQUE (id, task_id, report_id);

-- CONSTRAINT: score_report_items score_report_items_report_id_rubric_item_id_key
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_report_id_rubric_item_id_key UNIQUE (report_id, rubric_item_id);

-- CONSTRAINT: score_report_items score_report_items_pkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_reports score_reports_id_task_id_draft_id_extraction_job_id__key
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_id_task_id_draft_id_extraction_job_id__key UNIQUE (id, task_id, draft_id, extraction_job_id, rubric_id);

-- CONSTRAINT: score_reports score_reports_id_task_id_key
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_reports score_reports_job_id_key
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_job_id_key UNIQUE (job_id);

-- CONSTRAINT: score_reports score_reports_pkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_pkey PRIMARY KEY (id);

-- CONSTRAINT: response_items score_response_revision_binding
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT score_response_revision_binding UNIQUE (id, draft_id, requirement_id, card_revision_id);

-- CONSTRAINT: score_rubric_classifications score_rubric_classifications_id_task_id_key
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_classifications score_rubric_classifications_rubric_id_set_revision_key
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_rubric_id_set_revision_key UNIQUE (rubric_id, set_revision);

-- CONSTRAINT: score_rubric_classifications score_rubric_classifications_pkey
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_coverage_id_revision_key
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_coverage_id_revision_key UNIQUE (coverage_id, revision);

-- CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_id_task_id_key
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_id_task_id_rubric_id_key
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_id_task_id_rubric_id_key UNIQUE (id, task_id, rubric_id);

-- CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_pkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_coverage_decision_id_rub_key
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_coverage_decision_id_rub_key UNIQUE (coverage_decision_id, rubric_item_id);

-- CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_id_task_id_key
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_pkey
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_coverage score_rubric_coverage_id_task_id_key
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_coverage score_rubric_coverage_id_task_id_rubric_id_key
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_id_task_id_rubric_id_key UNIQUE (id, task_id, rubric_id);

-- CONSTRAINT: score_rubric_coverage score_rubric_coverage_rubric_id_requirement_id_key
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_rubric_id_requirement_id_key UNIQUE (rubric_id, requirement_id);

-- CONSTRAINT: score_rubric_coverage score_rubric_coverage_pkey
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_decisions score_rubric_decisions_id_task_id_key
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_decisions score_rubric_decisions_rubric_id_set_revision_key
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_rubric_id_set_revision_key UNIQUE (rubric_id, set_revision);

-- CONSTRAINT: score_rubric_decisions score_rubric_decisions_pkey
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_sets score_rubric_input_binding
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_input_binding UNIQUE (id, task_id, extraction_job_id, document_id);

-- CONSTRAINT: score_rubric_items score_rubric_item_binding
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_item_binding UNIQUE (id, task_id, rubric_id, section_id, requirement_id);

-- CONSTRAINT: score_rubric_items score_rubric_items_id_task_id_key
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_items score_rubric_items_id_task_id_rubric_id_key
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_id_task_id_rubric_id_key UNIQUE (id, task_id, rubric_id);

-- CONSTRAINT: score_rubric_items score_rubric_items_rubric_id_fingerprint_key
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_rubric_id_fingerprint_key UNIQUE (rubric_id, fingerprint);

-- CONSTRAINT: score_rubric_items score_rubric_items_rubric_id_key_key
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_rubric_id_key_key UNIQUE (rubric_id, key);

-- CONSTRAINT: score_rubric_items score_rubric_items_pkey
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_id_task_id_key
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_rubric_id_key
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_rubric_id_key UNIQUE (rubric_id);

-- CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_pkey
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_sections score_rubric_sections_id_task_id_key
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_sections score_rubric_sections_id_task_id_rubric_id_key
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_id_task_id_rubric_id_key UNIQUE (id, task_id, rubric_id);

-- CONSTRAINT: score_rubric_sections score_rubric_sections_rubric_id_key_key
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_rubric_id_key_key UNIQUE (rubric_id, key);

-- CONSTRAINT: score_rubric_sections score_rubric_sections_pkey
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_pkey PRIMARY KEY (id);

-- CONSTRAINT: score_rubric_sets score_rubric_sets_extraction_job_id_version_key
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_extraction_job_id_version_key UNIQUE (extraction_job_id, version);

-- CONSTRAINT: score_rubric_sets score_rubric_sets_id_task_id_key
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_id_task_id_key UNIQUE (id, task_id);

-- CONSTRAINT: score_rubric_sets score_rubric_sets_pkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_analysis_run_id_id_key
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_analysis_run_id_id_key UNIQUE (analysis_run_id, id);

-- CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_analysis_run_id_ref_key
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_analysis_run_id_ref_key UNIQUE (analysis_run_id, ref);

-- CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_task_id_id_key
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_pkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_job_id_key
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_job_id_key UNIQUE (job_id);

-- CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_task_id_id_key
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_pkey
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_assets screenshot_assets_task_id_id_key
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_assets screenshot_assets_task_id_idempotency_key_key
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_id_idempotency_key_key UNIQUE (task_id, idempotency_key);

-- CONSTRAINT: screenshot_assets screenshot_assets_pkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_asset_id_id_key
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_asset_id_id_key UNIQUE (asset_id, id);

-- CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_asset_id_key
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_asset_id_key UNIQUE (asset_id);

-- CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_task_id_id_key
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_pkey
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_task_id_id_key
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_pkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_renditions screenshot_renditions_asset_id_id_image_sha256_key
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_asset_id_id_image_sha256_key UNIQUE (asset_id, id, image_sha256);

-- CONSTRAINT: screenshot_renditions screenshot_renditions_asset_id_id_key
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_asset_id_id_key UNIQUE (asset_id, id);

-- CONSTRAINT: screenshot_renditions screenshot_renditions_task_id_id_key
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_renditions screenshot_renditions_pkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_search_run_id_ref_key
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_search_run_id_ref_key UNIQUE (search_run_id, ref);

-- CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_task_id_id_key
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_pkey
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_search_runs screenshot_search_runs_task_id_id_key
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_search_runs screenshot_search_runs_pkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_suggestions screenshot_suggestions_task_id_id_key
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_suggestions screenshot_suggestions_pkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archive_single
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archive_single UNIQUE (sandbox_run_id);

-- CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_task_id_id_key
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_pkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_pkey PRIMARY KEY (id);

-- CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_asset_id_key
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_asset_id_key UNIQUE (asset_id);

-- CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_task_id_id_key
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_pkey
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_pkey PRIMARY KEY (id);

-- CONSTRAINT: simulated_resources simulated_resources_feature_id_key
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_feature_id_key UNIQUE (feature_id);

-- CONSTRAINT: simulated_resources simulated_resources_product_id_key
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_product_id_key UNIQUE (product_id);

-- CONSTRAINT: simulated_resources simulated_resources_pkey
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_attachments task_attachments_task_id_id_key
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT task_attachments_task_id_id_key UNIQUE (task_id, id);

-- CONSTRAINT: task_attachments task_attachments_pkey
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT task_attachments_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_certificates task_certificate_source_binding
ALTER TABLE ONLY public.task_certificates
    ADD CONSTRAINT task_certificate_source_binding UNIQUE (id, task_id, certificate_id, certificate_revision_id);

-- CONSTRAINT: task_certificates task_certificates_pkey
ALTER TABLE ONLY public.task_certificates
    ADD CONSTRAINT task_certificates_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_event_heads task_event_heads_task_id_key
ALTER TABLE ONLY public.task_event_heads
    ADD CONSTRAINT task_event_heads_task_id_key UNIQUE (task_id);

-- CONSTRAINT: task_event_heads task_event_heads_pkey
ALTER TABLE ONLY public.task_event_heads
    ADD CONSTRAINT task_event_heads_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_events task_events_task_id_seq_key
ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_task_id_seq_key UNIQUE (task_id, seq);

-- CONSTRAINT: task_events task_events_pkey
ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_features task_features_evidence_binding
ALTER TABLE ONLY public.task_features
    ADD CONSTRAINT task_features_evidence_binding UNIQUE (id, task_id, feature_revision_id);

-- CONSTRAINT: task_features task_features_pkey
ALTER TABLE ONLY public.task_features
    ADD CONSTRAINT task_features_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_org_profiles task_org_profiles_evidence_binding
ALTER TABLE ONLY public.task_org_profiles
    ADD CONSTRAINT task_org_profiles_evidence_binding UNIQUE (id, task_id, profile_revision_id);

-- CONSTRAINT: task_org_profiles task_org_profiles_pkey
ALTER TABLE ONLY public.task_org_profiles
    ADD CONSTRAINT task_org_profiles_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_resources task_resources_evidence_binding
ALTER TABLE ONLY public.task_resources
    ADD CONSTRAINT task_resources_evidence_binding UNIQUE (id, task_id, product_revision_id);

-- CONSTRAINT: task_resources task_resources_pkey
ALTER TABLE ONLY public.task_resources
    ADD CONSTRAINT task_resources_pkey PRIMARY KEY (id);

-- CONSTRAINT: task_templates task_templates_pkey
ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT task_templates_pkey PRIMARY KEY (id);

-- CONSTRAINT: tasks tasks_pkey
ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);

-- CONSTRAINT: template_revisions template_revisions_template_id_id_key
ALTER TABLE ONLY public.template_revisions
    ADD CONSTRAINT template_revisions_template_id_id_key UNIQUE (template_id, id);

-- CONSTRAINT: template_revisions template_revisions_template_id_revision_key
ALTER TABLE ONLY public.template_revisions
    ADD CONSTRAINT template_revisions_template_id_revision_key UNIQUE (template_id, revision);

-- CONSTRAINT: template_revisions template_revisions_pkey
ALTER TABLE ONLY public.template_revisions
    ADD CONSTRAINT template_revisions_pkey PRIMARY KEY (id);

-- CONSTRAINT: templates templates_pkey
ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);

-- CONSTRAINT: usage_records usage_call_once
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_call_once UNIQUE (job_id, run_id, call_id);

-- CONSTRAINT: usage_records usage_records_pkey
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_pkey PRIMARY KEY (id);

-- CONSTRAINT: vendor_calls vendor_calls_job_id_run_id_id_key
ALTER TABLE ONLY public.vendor_calls
    ADD CONSTRAINT vendor_calls_job_id_run_id_id_key UNIQUE (job_id, run_id, id);

-- CONSTRAINT: vendor_calls vendor_calls_pkey
ALTER TABLE ONLY public.vendor_calls
    ADD CONSTRAINT vendor_calls_pkey PRIMARY KEY (id);

-- CONSTRAINT: clef_settings clef_settings_pkey
ALTER TABLE ONLY public.clef_settings
    ADD CONSTRAINT clef_settings_pkey PRIMARY KEY (id);

-- CONSTRAINT: credential_probes credential_probes_pkey
ALTER TABLE ONLY public.credential_probes
    ADD CONSTRAINT credential_probes_pkey PRIMARY KEY (id);
