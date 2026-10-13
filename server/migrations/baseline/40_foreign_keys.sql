-- FK CONSTRAINT: agent_job_links agent_job_links_session_id_step_id_fkey
ALTER TABLE ONLY public.agent_job_links
    ADD CONSTRAINT agent_job_links_session_id_step_id_fkey FOREIGN KEY (session_id, step_id) REFERENCES public.agent_steps(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_job_links agent_job_links_session_id_task_id_document_id_fkey
ALTER TABLE ONLY public.agent_job_links
    ADD CONSTRAINT agent_job_links_session_id_task_id_document_id_fkey FOREIGN KEY (session_id, task_id, document_id) REFERENCES public.agent_sessions(id, task_id, document_id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_job_links agent_job_links_task_id_document_id_job_id_fkey
ALTER TABLE ONLY public.agent_job_links
    ADD CONSTRAINT agent_job_links_task_id_document_id_job_id_fkey FOREIGN KEY (task_id, document_id, job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_messages agent_messages_session_id_fkey
ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_messages agent_messages_session_id_step_id_fkey
ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_session_id_step_id_fkey FOREIGN KEY (session_id, step_id) REFERENCES public.agent_steps(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_pauses agent_pauses_session_id_fkey
ALTER TABLE ONLY public.agent_pauses
    ADD CONSTRAINT agent_pauses_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_pauses agent_pauses_session_id_step_id_fkey
ALTER TABLE ONLY public.agent_pauses
    ADD CONSTRAINT agent_pauses_session_id_step_id_fkey FOREIGN KEY (session_id, step_id) REFERENCES public.agent_steps(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_sessions agent_sessions_id_pause_id_fkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_id_pause_id_fkey FOREIGN KEY (id, pause_id) REFERENCES public.agent_pauses(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_sessions agent_sessions_task_id_document_id_current_job_id_fkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_task_id_document_id_current_job_id_fkey FOREIGN KEY (task_id, document_id, current_job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_sessions agent_sessions_task_id_document_id_extraction_job_i_fkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_task_id_document_id_extraction_job_i_fkey FOREIGN KEY (task_id, document_id, extraction_job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_sessions agent_sessions_task_id_document_id_fkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_task_id_document_id_fkey FOREIGN KEY (task_id, document_id) REFERENCES public.documents(task_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_sessions agent_sessions_task_id_fkey
ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_steps agent_steps_session_id_task_id_document_id_fkey
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_session_id_task_id_document_id_fkey FOREIGN KEY (session_id, task_id, document_id) REFERENCES public.agent_sessions(id, task_id, document_id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_steps agent_steps_task_id_document_id_child_job_id_fkey
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_task_id_document_id_child_job_id_fkey FOREIGN KEY (task_id, document_id, child_job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_steps agent_steps_task_id_document_id_created_by_job_id_fkey
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_task_id_document_id_created_by_job_id_fkey FOREIGN KEY (task_id, document_id, created_by_job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: agent_steps agent_steps_task_id_document_id_last_transition_job_fkey
ALTER TABLE ONLY public.agent_steps
    ADD CONSTRAINT agent_steps_task_id_document_id_last_transition_job_fkey FOREIGN KEY (task_id, document_id, last_transition_job_id) REFERENCES public.jobs(task_id, document_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: annotation_materials annotation_materials_card_id_task_id_extraction_job_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_card_id_task_id_extraction_job_fkey FOREIGN KEY (card_id, task_id, extraction_job_id, requirement_id) REFERENCES public.response_cards(id, task_id, extraction_job_id, requirement_id);

-- FK CONSTRAINT: annotation_materials annotation_materials_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_materials annotation_materials_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_materials annotation_materials_requirement_id_task_id_extract_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_requirement_id_task_id_extract_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: annotation_materials annotation_materials_task_id_extraction_job_id_asse_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_task_id_extraction_job_id_asse_fkey FOREIGN KEY (task_id, extraction_job_id, asset_id, rendition_id) REFERENCES public.screenshot_renditions(task_id, extraction_job_id, asset_id, id);

-- FK CONSTRAINT: annotation_materials annotation_materials_task_id_extraction_job_id_requ_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_task_id_extraction_job_id_requ_fkey FOREIGN KEY (task_id, extraction_job_id, requirement_id, card_id, request_id) REFERENCES public.annotation_requests(task_id, extraction_job_id, requirement_id, card_id, id);

-- FK CONSTRAINT: annotation_materials annotation_materials_task_id_fkey
ALTER TABLE ONLY public.annotation_materials
    ADD CONSTRAINT annotation_materials_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_privacy_reviews annotation_privacy_request
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT annotation_privacy_request FOREIGN KEY (task_id, annotation_request_id) REFERENCES public.annotation_requests(task_id, id);

-- FK CONSTRAINT: annotation_releases annotation_releases_card_id_card_revision_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_card_id_card_revision_id_fkey FOREIGN KEY (card_id, card_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: annotation_releases annotation_releases_card_id_evidence_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_card_id_evidence_id_fkey FOREIGN KEY (card_id, evidence_id) REFERENCES public.evidence(card_id, id);

-- FK CONSTRAINT: annotation_releases annotation_releases_card_id_task_id_extraction_job__fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_card_id_task_id_extraction_job__fkey FOREIGN KEY (card_id, task_id, extraction_job_id, requirement_id) REFERENCES public.response_cards(id, task_id, extraction_job_id, requirement_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_card_revision_id_evidence_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_card_revision_id_evidence_id_fkey FOREIGN KEY (card_revision_id, evidence_id) REFERENCES public.card_evidence_links(revision_id, evidence_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_requirement_id_task_id_extracti_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_requirement_id_task_id_extracti_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_requirement_review_id_task_id_e_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_requirement_review_id_task_id_e_fkey FOREIGN KEY (requirement_review_id, task_id, extraction_job_id, requirement_id) REFERENCES public.requirement_reviews(id, task_id, extraction_job_id, requirement_id);

-- FK CONSTRAINT: annotation_releases annotation_releases_task_id_extraction_job_id_annot_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_task_id_extraction_job_id_annot_fkey FOREIGN KEY (task_id, extraction_job_id, annotation_id) REFERENCES public.annotation_materials(task_id, extraction_job_id, id);

-- FK CONSTRAINT: annotation_releases annotation_releases_task_id_extraction_job_id_asset_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_task_id_extraction_job_id_asset_fkey FOREIGN KEY (task_id, extraction_job_id, asset_id, rendition_id) REFERENCES public.screenshot_renditions(task_id, extraction_job_id, asset_id, id);

-- FK CONSTRAINT: annotation_releases annotation_releases_task_id_fkey
ALTER TABLE ONLY public.annotation_releases
    ADD CONSTRAINT annotation_releases_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: annotation_requests annotation_requests_asset_id_inherited_privacy_revi_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_asset_id_inherited_privacy_revi_fkey FOREIGN KEY (asset_id, inherited_privacy_review_id) REFERENCES public.screenshot_privacy_reviews(asset_id, id);

-- FK CONSTRAINT: annotation_requests annotation_requests_card_id_expected_card_revision__fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_card_id_expected_card_revision__fkey FOREIGN KEY (card_id, expected_card_revision_id, expected_card_revision) REFERENCES public.response_card_revisions(card_id, id, revision);

-- FK CONSTRAINT: annotation_requests annotation_requests_card_id_task_id_extraction_job__fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_card_id_task_id_extraction_job__fkey FOREIGN KEY (card_id, task_id, extraction_job_id, requirement_id) REFERENCES public.response_cards(id, task_id, extraction_job_id, requirement_id);

-- FK CONSTRAINT: annotation_requests annotation_requests_evidence_source_id_task_id_task_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_evidence_source_id_task_id_task_fkey FOREIGN KEY (evidence_source_id, task_id, task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) REFERENCES public.evidence_sources(id, task_id, task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id);

-- FK CONSTRAINT: annotation_requests annotation_requests_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_requests annotation_requests_job_id_task_id_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: annotation_requests annotation_requests_requirement_id_task_id_extracti_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_requirement_id_task_id_extracti_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: annotation_requests annotation_requests_task_id_extraction_job_id_asset_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_extraction_job_id_asset_fkey FOREIGN KEY (task_id, extraction_job_id, asset_id, parent_rendition_id) REFERENCES public.screenshot_renditions(task_id, extraction_job_id, asset_id, id);

-- FK CONSTRAINT: annotation_requests annotation_requests_task_id_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: annotation_requests annotation_requests_task_id_vendor_archive_id_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_id_vendor_archive_id_fkey FOREIGN KEY (task_id, vendor_archive_id) REFERENCES public.screenshot_vendor_archives(task_id, id);

-- FK CONSTRAINT: annotation_requests annotation_requests_task_resource_id_task_id_produc_fkey
ALTER TABLE ONLY public.annotation_requests
    ADD CONSTRAINT annotation_requests_task_resource_id_task_id_produc_fkey FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: screenshot_assets attachment_asset_source_task
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT attachment_asset_source_task FOREIGN KEY (task_id, evidence_source_id) REFERENCES public.evidence_sources(task_id, id);

-- FK CONSTRAINT: attachment_archives attachment_current_revision
ALTER TABLE ONLY public.attachment_archives
    ADD CONSTRAINT attachment_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.attachment_revisions(attachment_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: attachment_file_parts attachment_file_parts_attachment_file_id_attachment_fkey
ALTER TABLE ONLY public.attachment_file_parts
    ADD CONSTRAINT attachment_file_parts_attachment_file_id_attachment_fkey FOREIGN KEY (attachment_file_id, attachment_id, attachment_revision_id) REFERENCES public.attachment_files(id, attachment_id, attachment_revision_id);

-- FK CONSTRAINT: attachment_files attachment_files_attachment_id_attachment_revision__fkey
ALTER TABLE ONLY public.attachment_files
    ADD CONSTRAINT attachment_files_attachment_id_attachment_revision__fkey FOREIGN KEY (attachment_id, attachment_revision_id) REFERENCES public.attachment_revisions(attachment_id, id);

-- FK CONSTRAINT: screenshot_privacy_reviews attachment_privacy_hold
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT attachment_privacy_hold FOREIGN KEY (task_id, attachment_source_id, resolved_hold_id) REFERENCES public.attachment_privacy_holds(task_id, evidence_source_id, id);

-- FK CONSTRAINT: attachment_privacy_holds attachment_privacy_holds_task_id_evidence_source_i_fkey1
ALTER TABLE ONLY public.attachment_privacy_holds
    ADD CONSTRAINT attachment_privacy_holds_task_id_evidence_source_i_fkey1 FOREIGN KEY (task_id, evidence_source_id, prior_hold_id) REFERENCES public.attachment_privacy_holds(task_id, evidence_source_id, id);

-- FK CONSTRAINT: attachment_privacy_holds attachment_privacy_holds_task_id_evidence_source_id_fkey
ALTER TABLE ONLY public.attachment_privacy_holds
    ADD CONSTRAINT attachment_privacy_holds_task_id_evidence_source_id_fkey FOREIGN KEY (task_id, evidence_source_id) REFERENCES public.evidence_sources(task_id, id);

-- FK CONSTRAINT: screenshot_privacy_reviews attachment_privacy_source
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT attachment_privacy_source FOREIGN KEY (task_id, attachment_source_id) REFERENCES public.evidence_sources(task_id, id);

-- FK CONSTRAINT: attachment_reviews attachment_reviews_attachment_revision_id_prior_rev_fkey
ALTER TABLE ONLY public.attachment_reviews
    ADD CONSTRAINT attachment_reviews_attachment_revision_id_prior_rev_fkey FOREIGN KEY (attachment_revision_id, prior_review_id) REFERENCES public.attachment_reviews(attachment_revision_id, id);

-- FK CONSTRAINT: attachment_reviews attachment_reviews_file_id_attachment_id_attachment_fkey
ALTER TABLE ONLY public.attachment_reviews
    ADD CONSTRAINT attachment_reviews_file_id_attachment_id_attachment_fkey FOREIGN KEY (file_id, attachment_id, attachment_revision_id) REFERENCES public.attachment_files(id, attachment_id, attachment_revision_id);

-- FK CONSTRAINT: attachment_revisions attachment_revisions_attachment_id_fkey
ALTER TABLE ONLY public.attachment_revisions
    ADD CONSTRAINT attachment_revisions_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.attachment_archives(id);

-- FK CONSTRAINT: evidence_sources attachment_source_exact_selection
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT attachment_source_exact_selection FOREIGN KEY (task_attachment_id, task_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, attachment_file_id, approval_id) REFERENCES public.task_attachments(id, task_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, file_id, approval_id);

-- FK CONSTRAINT: audit_logs audit_agent_job_fk
ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_agent_job_fk FOREIGN KEY (job_id) REFERENCES public.jobs(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: audit_logs audit_agent_job_session_fk
ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_agent_job_session_fk FOREIGN KEY (agent_session_id, job_id) REFERENCES public.jobs(agent_session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: audit_logs audit_logs_agent_origin_step_fk
ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_agent_origin_step_fk FOREIGN KEY (agent_session_id, agent_step_id) REFERENCES public.agent_steps(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: audit_logs audit_logs_actor_token_id_fkey
ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: bid_document_pages bid_document_pages_task_id_fkey
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_document_pages bid_document_pages_task_id_submission_id_preparatio_fkey
ALTER TABLE ONLY public.bid_document_pages
    ADD CONSTRAINT bid_document_pages_task_id_submission_id_preparatio_fkey FOREIGN KEY (task_id, submission_id, preparation_id, document_id) REFERENCES public.bid_prepared_documents(task_id, submission_id, preparation_id, document_id);

-- FK CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_fkey
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_prior_authoriza_fkey
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_prior_authoriza_fkey FOREIGN KEY (task_id, prior_authorization_id) REFERENCES public.bid_outbound_authorizations(task_id, id);

-- FK CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_submission_id__fkey1
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_submission_id__fkey1 FOREIGN KEY (task_id, submission_id, preparation_id, snapshot_id) REFERENCES public.bid_redaction_snapshots(task_id, submission_id, preparation_id, id);

-- FK CONSTRAINT: bid_outbound_authorizations bid_outbound_authorizations_task_id_submission_id_p_fkey
ALTER TABLE ONLY public.bid_outbound_authorizations
    ADD CONSTRAINT bid_outbound_authorizations_task_id_submission_id_p_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_page_task_id_submission_id_fkey1
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_page_task_id_submission_id_fkey1 FOREIGN KEY (task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256) REFERENCES public.bid_redacted_pages(task_id, submission_id, snapshot_id, page_id, sanitized_text_sha256);

-- FK CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_pages_task_id_fkey
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_pages_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_outbound_authorized_pages bid_outbound_authorized_pages_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_outbound_authorized_pages
    ADD CONSTRAINT bid_outbound_authorized_pages_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id, snapshot_id, authorization_id) REFERENCES public.bid_outbound_authorizations(task_id, submission_id, snapshot_id, id);

-- FK CONSTRAINT: bid_pdf_validations bid_pdf_validations_task_id_fkey
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_pdf_validations bid_pdf_validations_task_id_submission_id_preparat_fkey1
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_task_id_submission_id_preparat_fkey1 FOREIGN KEY (task_id, submission_id, preparation_id, document_id) REFERENCES public.bid_prepared_documents(task_id, submission_id, preparation_id, document_id);

-- FK CONSTRAINT: bid_pdf_validations bid_pdf_validations_task_id_submission_id_preparat_fkey2
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_task_id_submission_id_preparat_fkey2 FOREIGN KEY (task_id, submission_id, preparation_id, trust_store_sha256) REFERENCES public.bid_preparation_trust(task_id, submission_id, preparation_id, trust_store_sha256);

-- FK CONSTRAINT: bid_pdf_validations bid_pdf_validations_task_id_submission_id_preparati_fkey
ALTER TABLE ONLY public.bid_pdf_validations
    ADD CONSTRAINT bid_pdf_validations_task_id_submission_id_preparati_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_preparation_publications bid_preparation_publications_task_id_fkey
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_preparation_publications bid_preparation_publications_task_id_submission_id__fkey
ALTER TABLE ONLY public.bid_preparation_publications
    ADD CONSTRAINT bid_preparation_publications_task_id_submission_id__fkey FOREIGN KEY (task_id, submission_id, preparation_id, job_id, input_hash) REFERENCES public.bid_preparations(task_id, submission_id, id, job_id, input_hash);

-- FK CONSTRAINT: bid_preparation_trust bid_preparation_trust_task_id_fkey
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_preparation_trust bid_preparation_trust_task_id_submission_id_prepara_fkey
ALTER TABLE ONLY public.bid_preparation_trust
    ADD CONSTRAINT bid_preparation_trust_task_id_submission_id_prepara_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_preparations bid_preparations_task_id_fkey
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_preparations bid_preparations_task_id_job_id_fkey
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_job_id_fkey FOREIGN KEY (task_id, job_id) REFERENCES public.jobs(task_id, id);

-- FK CONSTRAINT: bid_preparations bid_preparations_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_preparations
    ADD CONSTRAINT bid_preparations_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id) REFERENCES public.bid_submissions(task_id, id);

-- FK CONSTRAINT: bid_prepared_documents bid_prepared_documents_task_id_fkey
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_prepared_documents bid_prepared_documents_task_id_submission_id_docume_fkey
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_task_id_submission_id_docume_fkey FOREIGN KEY (task_id, submission_id, document_id) REFERENCES public.bid_submission_documents(task_id, submission_id, id);

-- FK CONSTRAINT: bid_prepared_documents bid_prepared_documents_task_id_submission_id_prepar_fkey
ALTER TABLE ONLY public.bid_prepared_documents
    ADD CONSTRAINT bid_prepared_documents_task_id_submission_id_prepar_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_fkey
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_prior_authoriza_fkey
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_prior_authoriza_fkey FOREIGN KEY (task_id, prior_authorization_id) REFERENCES public.bid_presence_authorizations(task_id, id);

-- FK CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id) REFERENCES public.bid_submissions(task_id, id);

-- FK CONSTRAINT: bid_presence_authorizations bid_presence_authorizations_task_id_submission_id_p_fkey
ALTER TABLE ONLY public.bid_presence_authorizations
    ADD CONSTRAINT bid_presence_authorizations_task_id_submission_id_p_fkey FOREIGN KEY (task_id, submission_id, presence_preparation_id) REFERENCES public.bid_presence_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_imag_task_id_submission_id_fkey1
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_imag_task_id_submission_id_fkey1 FOREIGN KEY (task_id, submission_id, presence_preparation_id, image_id) REFERENCES public.bid_presence_images(task_id, submission_id, presence_preparation_id, id);

-- FK CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_image_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_image_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id, presence_preparation_id, authorization_id) REFERENCES public.bid_presence_authorizations(task_id, submission_id, presence_preparation_id, id);

-- FK CONSTRAINT: bid_presence_authorized_images bid_presence_authorized_images_task_id_fkey
ALTER TABLE ONLY public.bid_presence_authorized_images
    ADD CONSTRAINT bid_presence_authorized_images_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_presence_images bid_presence_images_task_id_fkey
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_presence_images bid_presence_images_task_id_submission_id_page_id_fkey
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_task_id_submission_id_page_id_fkey FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_presence_images bid_presence_images_task_id_submission_id_presence__fkey
ALTER TABLE ONLY public.bid_presence_images
    ADD CONSTRAINT bid_presence_images_task_id_submission_id_presence__fkey FOREIGN KEY (task_id, submission_id, presence_preparation_id) REFERENCES public.bid_presence_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_presence_preparations bid_presence_preparations_task_id_fkey
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_presence_preparations bid_presence_preparations_task_id_submission_id_pre_fkey
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_task_id_submission_id_pre_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_presence_preparations bid_presence_preparations_task_id_submission_id_sou_fkey
ALTER TABLE ONLY public.bid_presence_preparations
    ADD CONSTRAINT bid_presence_preparations_task_id_submission_id_sou_fkey FOREIGN KEY (task_id, submission_id, source_review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_redacted_pages bid_redacted_pages_task_id_fkey
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_redacted_pages bid_redacted_pages_task_id_submission_id_page_id_fkey
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_task_id_submission_id_page_id_fkey FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_redacted_pages bid_redacted_pages_task_id_submission_id_preparatio_fkey
ALTER TABLE ONLY public.bid_redacted_pages
    ADD CONSTRAINT bid_redacted_pages_task_id_submission_id_preparatio_fkey FOREIGN KEY (task_id, submission_id, preparation_id, snapshot_id) REFERENCES public.bid_redaction_snapshots(task_id, submission_id, preparation_id, id);

-- FK CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_task_id_fkey
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_redaction_snapshots bid_redaction_snapshots_task_id_submission_id_prepa_fkey
ALTER TABLE ONLY public.bid_redaction_snapshots
    ADD CONSTRAINT bid_redaction_snapshots_task_id_submission_id_prepa_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_finding_events bid_review_finding_events_task_id_fkey
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_finding_events bid_review_finding_events_task_id_submission_id_re_fkey1
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_task_id_submission_id_re_fkey1 FOREIGN KEY (task_id, submission_id, review_id, finding_id, prior_decision_id) REFERENCES public.bid_review_finding_events(task_id, submission_id, review_id, finding_id, id);

-- FK CONSTRAINT: bid_review_finding_events bid_review_finding_events_task_id_submission_id_rev_fkey
ALTER TABLE ONLY public.bid_review_finding_events
    ADD CONSTRAINT bid_review_finding_events_task_id_submission_id_rev_fkey FOREIGN KEY (task_id, submission_id, review_id, finding_id) REFERENCES public.bid_review_findings(task_id, submission_id, review_id, id);

-- FK CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_fkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_submission_id_do_fkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_submission_id_do_fkey FOREIGN KEY (task_id, submission_id, document_id) REFERENCES public.bid_submission_documents(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_submission_id_pa_fkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_submission_id_pa_fkey FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_submission_id_pr_fkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_submission_id_pr_fkey FOREIGN KEY (task_id, submission_id, preparation_id, document_id, validation_id) REFERENCES public.bid_pdf_validations(task_id, submission_id, preparation_id, document_id, id);

-- FK CONSTRAINT: bid_review_finding_sources bid_review_finding_sources_task_id_submission_id_re_fkey
ALTER TABLE ONLY public.bid_review_finding_sources
    ADD CONSTRAINT bid_review_finding_sources_task_id_submission_id_re_fkey FOREIGN KEY (task_id, submission_id, review_id, finding_id) REFERENCES public.bid_review_findings(task_id, submission_id, review_id, id);

-- FK CONSTRAINT: bid_review_findings bid_review_findings_task_id_fkey
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_findings bid_review_findings_task_id_submission_id_review_i_fkey1
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_task_id_submission_id_review_i_fkey1 FOREIGN KEY (task_id, submission_id, review_id, obligation_id) REFERENCES public.bid_review_obligations(task_id, submission_id, review_id, id);

-- FK CONSTRAINT: bid_review_findings bid_review_findings_task_id_submission_id_review_id_fkey
ALTER TABLE ONLY public.bid_review_findings
    ADD CONSTRAINT bid_review_findings_task_id_submission_id_review_id_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: jobs bid_review_job_source
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT bid_review_job_source FOREIGN KEY (task_id, bid_submission_document_id) REFERENCES public.bid_submission_documents(task_id, id);

-- FK CONSTRAINT: bid_review_name_lists bid_review_name_lists_task_id_fkey
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_name_lists bid_review_name_lists_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_review_name_lists
    ADD CONSTRAINT bid_review_name_lists_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id) REFERENCES public.bid_submissions(task_id, id);

-- FK CONSTRAINT: bid_review_obligations bid_review_obligations_task_id_fkey
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_obligations bid_review_obligations_task_id_submission_id_page_i_fkey
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_task_id_submission_id_page_i_fkey FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_obligations bid_review_obligations_task_id_submission_id_review_fkey
ALTER TABLE ONLY public.bid_review_obligations
    ADD CONSTRAINT bid_review_obligations_task_id_submission_id_review_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_publications bid_review_publications_task_id_fkey
ALTER TABLE ONLY public.bid_review_publications
    ADD CONSTRAINT bid_review_publications_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_publications bid_review_publications_task_id_submission_id_revie_fkey
ALTER TABLE ONLY public.bid_review_publications
    ADD CONSTRAINT bid_review_publications_task_id_submission_id_revie_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_report_artifacts bid_review_report_artifacts_task_id_fkey
ALTER TABLE ONLY public.bid_review_report_artifacts
    ADD CONSTRAINT bid_review_report_artifacts_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_report_artifacts bid_review_report_artifacts_task_id_submission_id_r_fkey
ALTER TABLE ONLY public.bid_review_report_artifacts
    ADD CONSTRAINT bid_review_report_artifacts_task_id_submission_id_r_fkey FOREIGN KEY (task_id, submission_id, review_id, snapshot_id) REFERENCES public.bid_review_report_snapshots(task_id, submission_id, review_id, id);

-- FK CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_fkey
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_job_id_fkey
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_job_id_fkey FOREIGN KEY (task_id, job_id) REFERENCES public.jobs(task_id, id);

-- FK CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_publication_id_fkey
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_publication_id_fkey FOREIGN KEY (task_id, publication_id) REFERENCES public.bid_review_publications(task_id, id);

-- FK CONSTRAINT: bid_review_report_snapshots bid_review_report_snapshots_task_id_submission_id_r_fkey
ALTER TABLE ONLY public.bid_review_report_snapshots
    ADD CONSTRAINT bid_review_report_snapshots_task_id_submission_id_r_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_required_locations bid_review_required_location_task_id_submission_id_fkey1
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_location_task_id_submission_id_fkey1 FOREIGN KEY (task_id, submission_id, review_id, requirement_id) REFERENCES public.bid_review_signing_requirements(task_id, submission_id, review_id, id);

-- FK CONSTRAINT: bid_review_required_locations bid_review_required_location_task_id_submission_id_fkey2
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_location_task_id_submission_id_fkey2 FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_required_locations bid_review_required_locations_task_id_fkey
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_locations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_required_locations bid_review_required_locations_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_review_required_locations
    ADD CONSTRAINT bid_review_required_locations_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_runs bid_review_runs_task_id_authorization_id_fkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_authorization_id_fkey FOREIGN KEY (task_id, authorization_id) REFERENCES public.bid_outbound_authorizations(task_id, id);

-- FK CONSTRAINT: bid_review_runs bid_review_runs_task_id_fkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_review_runs bid_review_runs_task_id_job_id_fkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_job_id_fkey FOREIGN KEY (task_id, job_id) REFERENCES public.jobs(task_id, id);

-- FK CONSTRAINT: bid_review_runs bid_review_runs_task_id_submission_id_preparation_i_fkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_submission_id_preparation_i_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_runs bid_review_runs_task_id_submission_id_tender_docume_fkey
ALTER TABLE ONLY public.bid_review_runs
    ADD CONSTRAINT bid_review_runs_task_id_submission_id_tender_docume_fkey FOREIGN KEY (task_id, submission_id, tender_document_id) REFERENCES public.bid_submission_documents(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_signing_requirements bid_review_signing_requireme_task_id_submission_id_fkey1
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requireme_task_id_submission_id_fkey1 FOREIGN KEY (task_id, submission_id, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_signing_requirements bid_review_signing_requiremen_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requiremen_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id, review_id) REFERENCES public.bid_review_runs(task_id, submission_id, id);

-- FK CONSTRAINT: bid_review_signing_requirements bid_review_signing_requirement_task_id_candidate_id_fkey
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requirement_task_id_candidate_id_fkey FOREIGN KEY (task_id, candidate_id) REFERENCES public.bid_signing_candidates(task_id, id);

-- FK CONSTRAINT: bid_review_signing_requirements bid_review_signing_requirements_task_id_fkey
ALTER TABLE ONLY public.bid_review_signing_requirements
    ADD CONSTRAINT bid_review_signing_requirements_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_signing_candidates bid_signing_candidates_task_id_fkey
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_signing_candidates bid_signing_candidates_task_id_submission_id_prepa_fkey1
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_task_id_submission_id_prepa_fkey1 FOREIGN KEY (task_id, submission_id, preparation_id, document_id, page, page_id) REFERENCES public.bid_document_pages(task_id, submission_id, preparation_id, document_id, page, id);

-- FK CONSTRAINT: bid_signing_candidates bid_signing_candidates_task_id_submission_id_prepar_fkey
ALTER TABLE ONLY public.bid_signing_candidates
    ADD CONSTRAINT bid_signing_candidates_task_id_submission_id_prepar_fkey FOREIGN KEY (task_id, submission_id, preparation_id) REFERENCES public.bid_preparations(task_id, submission_id, id);

-- FK CONSTRAINT: bid_submission_documents bid_submission_documents_task_id_fkey
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: bid_submission_documents bid_submission_documents_task_id_submission_id_fkey
ALTER TABLE ONLY public.bid_submission_documents
    ADD CONSTRAINT bid_submission_documents_task_id_submission_id_fkey FOREIGN KEY (task_id, submission_id) REFERENCES public.bid_submissions(task_id, id);

-- FK CONSTRAINT: bid_submissions bid_submissions_task_id_fkey
ALTER TABLE ONLY public.bid_submissions
    ADD CONSTRAINT bid_submissions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: card_evidence_links card_evidence_links_card_id_evidence_id_fkey
ALTER TABLE ONLY public.card_evidence_links
    ADD CONSTRAINT card_evidence_links_card_id_evidence_id_fkey FOREIGN KEY (card_id, evidence_id) REFERENCES public.evidence(card_id, id);

-- FK CONSTRAINT: card_evidence_links card_evidence_links_card_id_revision_id_fkey
ALTER TABLE ONLY public.card_evidence_links
    ADD CONSTRAINT card_evidence_links_card_id_revision_id_fkey FOREIGN KEY (card_id, revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_extraction_job_id_binding
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_extraction_job_id_binding FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_generation_job_id_binding
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_generation_job_id_binding FOREIGN KEY (generation_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_actor_token_id_fkey
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_extraction_job_id_fkey
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_extraction_job_id_fkey FOREIGN KEY (extraction_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_generation_job_id_fkey
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_generation_job_id_fkey FOREIGN KEY (generation_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: card_generation_runs card_generation_runs_task_id_fkey
ALTER TABLE ONLY public.card_generation_runs
    ADD CONSTRAINT card_generation_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: certificates certificate_current_revision
ALTER TABLE ONLY public.certificates
    ADD CONSTRAINT certificate_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.certificate_revisions(certificate_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: certificate_file_parts certificate_file_parts_certificate_file_id_certific_fkey
ALTER TABLE ONLY public.certificate_file_parts
    ADD CONSTRAINT certificate_file_parts_certificate_file_id_certific_fkey FOREIGN KEY (certificate_file_id, certificate_id, certificate_revision_id) REFERENCES public.certificate_files(id, certificate_id, certificate_revision_id);

-- FK CONSTRAINT: certificate_files certificate_files_certificate_id_certificate_revisi_fkey
ALTER TABLE ONLY public.certificate_files
    ADD CONSTRAINT certificate_files_certificate_id_certificate_revisi_fkey FOREIGN KEY (certificate_id, certificate_revision_id) REFERENCES public.certificate_revisions(certificate_id, id);

-- FK CONSTRAINT: certificate_revisions certificate_revisions_certificate_id_fkey
ALTER TABLE ONLY public.certificate_revisions
    ADD CONSTRAINT certificate_revisions_certificate_id_fkey FOREIGN KEY (certificate_id) REFERENCES public.certificates(id);

-- FK CONSTRAINT: check_certificate_items check_certificate_items_certificate_id_task_id_repo_fkey
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_certificate_id_task_id_repo_fkey FOREIGN KEY (certificate_id, task_id, report_id) REFERENCES public.check_certificates(id, task_id, report_id);

-- FK CONSTRAINT: check_certificate_items check_certificate_items_check_item_id_task_id_repor_fkey
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_check_item_id_task_id_repor_fkey FOREIGN KEY (check_item_id, task_id, report_id) REFERENCES public.check_items(id, task_id, report_id);

-- FK CONSTRAINT: check_certificate_items check_certificate_items_report_id_task_id_fkey
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_certificate_items check_certificate_items_task_id_fkey
ALTER TABLE ONLY public.check_certificate_items
    ADD CONSTRAINT check_certificate_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_certificates check_certificates_certificate_revision_id_fkey
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_certificate_revision_id_fkey FOREIGN KEY (certificate_revision_id) REFERENCES public.certificate_revisions(id);

-- FK CONSTRAINT: check_certificates check_certificates_report_id_task_id_fkey
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_certificates check_certificates_task_certificate_id_task_id_cert_fkey
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_task_certificate_id_task_id_cert_fkey FOREIGN KEY (task_certificate_id, task_id, certificate_revision_id) REFERENCES public.task_certificates(id, task_id, certificate_revision_id);

-- FK CONSTRAINT: check_certificates check_certificates_task_id_fkey
ALTER TABLE ONLY public.check_certificates
    ADD CONSTRAINT check_certificates_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_finding_citations check_citation_item_binding
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_citation_item_binding FOREIGN KEY (check_item_id, task_id, report_id) REFERENCES public.check_items(id, task_id, report_id);

-- FK CONSTRAINT: check_decisions check_decisions_finding_id_task_id_report_id_fkey
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_finding_id_task_id_report_id_fkey FOREIGN KEY (finding_id, task_id, report_id) REFERENCES public.check_findings(id, task_id, report_id);

-- FK CONSTRAINT: check_decisions check_decisions_report_id_task_id_fkey
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_decisions check_decisions_task_id_fkey
ALTER TABLE ONLY public.check_decisions
    ADD CONSTRAINT check_decisions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_chunk_id_task_id_document_i_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_chunk_id_task_id_document_i_fkey FOREIGN KEY (chunk_id, task_id, document_id) REFERENCES public.chunks(id, task_id, document_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_document_id_task_id_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_document_id_task_id_fkey FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_draft_id_task_id_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_draft_id_task_id_fkey FOREIGN KEY (draft_id, task_id) REFERENCES public.draft_runs(id, task_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_evidence_id_task_id_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_evidence_id_task_id_fkey FOREIGN KEY (evidence_id, task_id) REFERENCES public.evidence(id, task_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_finding_id_task_id_report_i_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_finding_id_task_id_report_i_fkey FOREIGN KEY (finding_id, task_id, report_id) REFERENCES public.check_findings(id, task_id, report_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_report_id_task_id_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_response_item_id_draft_id_c_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_response_item_id_draft_id_c_fkey FOREIGN KEY (response_item_id, draft_id, card_revision_id) REFERENCES public.response_items(id, draft_id, card_revision_id);

-- FK CONSTRAINT: check_finding_citations check_finding_citations_task_id_fkey
ALTER TABLE ONLY public.check_finding_citations
    ADD CONSTRAINT check_finding_citations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_findings check_findings_check_item_id_task_id_report_id_requ_fkey
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_check_item_id_task_id_report_id_requ_fkey FOREIGN KEY (check_item_id, task_id, report_id, requirement_id) REFERENCES public.check_items(id, task_id, report_id, requirement_id);

-- FK CONSTRAINT: check_findings check_findings_report_id_task_id_fkey
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_findings check_findings_task_id_fkey
ALTER TABLE ONLY public.check_findings
    ADD CONSTRAINT check_findings_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_items check_items_card_revision_id_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_card_revision_id_fkey FOREIGN KEY (card_revision_id) REFERENCES public.response_card_revisions(id);

-- FK CONSTRAINT: check_items check_items_report_id_task_id_draft_id_extraction_j_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_report_id_task_id_draft_id_extraction_j_fkey FOREIGN KEY (report_id, task_id, draft_id, extraction_job_id) REFERENCES public.check_runs(id, task_id, draft_id, extraction_job_id);

-- FK CONSTRAINT: check_items check_items_report_id_task_id_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_report_id_task_id_fkey FOREIGN KEY (report_id, task_id) REFERENCES public.check_runs(id, task_id);

-- FK CONSTRAINT: check_items check_items_requirement_id_task_id_extraction_job_i_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_requirement_id_task_id_extraction_job_i_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: check_items check_items_response_item_id_draft_id_requirement_i_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_response_item_id_draft_id_requirement_i_fkey FOREIGN KEY (response_item_id, draft_id, requirement_id) REFERENCES public.response_items(id, draft_id, requirement_id);

-- FK CONSTRAINT: check_items check_items_task_id_fkey
ALTER TABLE ONLY public.check_items
    ADD CONSTRAINT check_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: check_runs check_runs_actor_token_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: check_runs check_runs_document_id_task_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_document_id_task_id_fkey FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: check_runs check_runs_draft_id_task_id_extraction_job_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_draft_id_task_id_extraction_job_id_fkey FOREIGN KEY (draft_id, task_id, extraction_job_id) REFERENCES public.draft_runs(id, task_id, extraction_job_id);

-- FK CONSTRAINT: check_runs check_runs_extraction_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_extraction_job_id_task_id_document_id_fkey FOREIGN KEY (extraction_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: check_runs check_runs_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_job_id_task_id_document_id_fkey FOREIGN KEY (job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: check_runs check_runs_task_id_fkey
ALTER TABLE ONLY public.check_runs
    ADD CONSTRAINT check_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: chunks chunks_document_id_fkey
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: chunks chunks_task_id_fkey
ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: confidential_values confidential_values_field_id_fkey
ALTER TABLE ONLY public.confidential_values
    ADD CONSTRAINT confidential_values_field_id_fkey FOREIGN KEY (field_id) REFERENCES public.confidential_fields(id);

-- FK CONSTRAINT: confidential_values confidential_values_task_id_fkey
ALTER TABLE ONLY public.confidential_values
    ADD CONSTRAINT confidential_values_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: documents documents_task_id_fkey
ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: draft_runs draft_runs_extraction_job_id_binding
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_extraction_job_id_binding FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: draft_runs draft_runs_generation_job_id_binding
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_generation_job_id_binding FOREIGN KEY (generation_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: draft_runs draft_runs_actor_token_id_fkey
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: draft_runs draft_runs_extraction_job_id_fkey
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_extraction_job_id_fkey FOREIGN KEY (extraction_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: draft_runs draft_runs_generation_job_id_fkey
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_generation_job_id_fkey FOREIGN KEY (generation_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: draft_runs draft_runs_task_id_fkey
ALTER TABLE ONLY public.draft_runs
    ADD CONSTRAINT draft_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: evidence evidence_image_asset_fk
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_image_asset_fk FOREIGN KEY (task_id, screenshot_asset_id) REFERENCES public.screenshot_assets(task_id, id);

-- FK CONSTRAINT: evidence evidence_image_rendition_fk
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_image_rendition_fk FOREIGN KEY (screenshot_asset_id, screenshot_rendition_id, image_sha256) REFERENCES public.screenshot_renditions(asset_id, id, image_sha256);

-- FK CONSTRAINT: evidence evidence_card_id_task_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_card_id_task_id_fkey FOREIGN KEY (card_id, task_id) REFERENCES public.response_cards(id, task_id);

-- FK CONSTRAINT: evidence evidence_certificate_revision_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_certificate_revision_id_fkey FOREIGN KEY (certificate_revision_id) REFERENCES public.certificate_revisions(id);

-- FK CONSTRAINT: evidence evidence_evidence_source_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_evidence_source_id_fkey FOREIGN KEY (evidence_source_id) REFERENCES public.evidence_sources(id);

-- FK CONSTRAINT: evidence evidence_feature_revision_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_feature_revision_id_fkey FOREIGN KEY (feature_revision_id) REFERENCES public.feature_revisions(id);

-- FK CONSTRAINT: evidence evidence_product_revision_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_product_revision_id_fkey FOREIGN KEY (product_revision_id) REFERENCES public.product_revisions(id);

-- FK CONSTRAINT: evidence evidence_profile_revision_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_profile_revision_id_fkey FOREIGN KEY (profile_revision_id) REFERENCES public.org_profile_revisions(id);

-- FK CONSTRAINT: evidence evidence_task_certificate_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_certificate_id_fkey FOREIGN KEY (task_certificate_id) REFERENCES public.task_certificates(id);

-- FK CONSTRAINT: evidence evidence_task_feature_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_feature_id_fkey FOREIGN KEY (task_feature_id) REFERENCES public.task_features(id);

-- FK CONSTRAINT: evidence evidence_task_org_profile_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_org_profile_id_fkey FOREIGN KEY (task_org_profile_id) REFERENCES public.task_org_profiles(id);

-- FK CONSTRAINT: evidence evidence_task_resource_id_fkey
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_resource_id_fkey FOREIGN KEY (task_resource_id) REFERENCES public.task_resources(id);

-- FK CONSTRAINT: evidence_sources evidence_sources_certificate_file_id_certificate_id_fkey
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT evidence_sources_certificate_file_id_certificate_id_fkey FOREIGN KEY (certificate_file_id, certificate_id, certificate_revision_id) REFERENCES public.certificate_files(id, certificate_id, certificate_revision_id);

-- FK CONSTRAINT: evidence_sources evidence_sources_task_certificate_id_task_id_certif_fkey
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT evidence_sources_task_certificate_id_task_id_certif_fkey FOREIGN KEY (task_certificate_id, task_id, certificate_id, certificate_revision_id) REFERENCES public.task_certificates(id, task_id, certificate_id, certificate_revision_id);

-- FK CONSTRAINT: evidence_sources evidence_sources_task_id_fkey
ALTER TABLE ONLY public.evidence_sources
    ADD CONSTRAINT evidence_sources_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: evidence evidence_task_certificates_binding
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_certificates_binding FOREIGN KEY (task_certificate_id, task_id, certificate_revision_id) REFERENCES public.task_certificates(id, task_id, certificate_revision_id);

-- FK CONSTRAINT: evidence evidence_task_features_binding
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_features_binding FOREIGN KEY (task_feature_id, task_id, feature_revision_id) REFERENCES public.task_features(id, task_id, feature_revision_id);

-- FK CONSTRAINT: evidence evidence_task_org_profiles_binding
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_org_profiles_binding FOREIGN KEY (task_org_profile_id, task_id, profile_revision_id) REFERENCES public.task_org_profiles(id, task_id, profile_revision_id);

-- FK CONSTRAINT: evidence evidence_task_resources_binding
ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_resources_binding FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: export_runs export_relation_0
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_relation_0 FOREIGN KEY (extraction_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: export_runs export_relation_1
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_relation_1 FOREIGN KEY (render_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: export_runs export_relation_2
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_relation_2 FOREIGN KEY (draft_run_id, task_id, extraction_job_id) REFERENCES public.draft_runs(id, task_id, extraction_job_id);

-- FK CONSTRAINT: export_runs export_relation_3
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_relation_3 FOREIGN KEY (task_template_id, task_id, template_revision_id) REFERENCES public.task_templates(id, task_id, template_revision_id);

-- FK CONSTRAINT: export_run_items export_relation_4
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_relation_4 FOREIGN KEY (response_item_id, draft_run_id, requirement_id, kind) REFERENCES public.response_items(id, draft_id, requirement_id, kind);

-- FK CONSTRAINT: export_render_candidates export_relation_5
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_relation_5 FOREIGN KEY (run_id, render_job_id, input_hash, manifest_hash, renderer_profile) REFERENCES public.export_runs(id, render_job_id, input_hash, manifest_hash, renderer_profile);

-- FK CONSTRAINT: exports export_relation_6
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT export_relation_6 FOREIGN KEY (run_id, task_id, mode, input_hash, manifest_hash) REFERENCES public.export_runs(id, task_id, mode, input_hash, manifest_hash);

-- FK CONSTRAINT: exports export_relation_7
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT export_relation_7 FOREIGN KEY (run_id, candidate_id, file_sha256, size_bytes) REFERENCES public.export_render_candidates(run_id, id, plaintext_sha256, size_bytes);

-- FK CONSTRAINT: export_render_candidates export_render_candidates_render_job_id_fkey
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_render_candidates_render_job_id_fkey FOREIGN KEY (render_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: export_render_candidates export_render_candidates_run_id_fkey
ALTER TABLE ONLY public.export_render_candidates
    ADD CONSTRAINT export_render_candidates_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.export_runs(id);

-- FK CONSTRAINT: export_run_evidence export_run_evidence_card_revision_id_evidence_id_fkey
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_card_revision_id_evidence_id_fkey FOREIGN KEY (card_revision_id, evidence_id) REFERENCES public.card_evidence_links(revision_id, evidence_id);

-- FK CONSTRAINT: export_run_evidence export_run_evidence_evidence_id_fkey
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES public.evidence(id);

-- FK CONSTRAINT: export_run_evidence export_run_evidence_run_id_run_item_id_card_revisio_fkey
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_run_id_run_item_id_card_revisio_fkey FOREIGN KEY (run_id, run_item_id, card_revision_id) REFERENCES public.export_run_items(run_id, id, card_revision_id);

-- FK CONSTRAINT: export_run_evidence export_run_evidence_prototype_decision
ALTER TABLE ONLY public.export_run_evidence
    ADD CONSTRAINT export_run_evidence_prototype_decision FOREIGN KEY (evidence_id, prototype_decision_id) REFERENCES public.prototype_evidence_decisions(evidence_id, id);

-- FK CONSTRAINT: export_run_items export_run_items_card_revision_id_fkey
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_card_revision_id_fkey FOREIGN KEY (card_revision_id) REFERENCES public.response_card_revisions(id);

-- FK CONSTRAINT: export_run_items export_run_items_requirement_id_fkey
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES public.requirements(id);

-- FK CONSTRAINT: export_run_items export_run_items_response_item_id_fkey
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_response_item_id_fkey FOREIGN KEY (response_item_id) REFERENCES public.response_items(id);

-- FK CONSTRAINT: export_run_items export_run_items_run_id_draft_run_id_fkey
ALTER TABLE ONLY public.export_run_items
    ADD CONSTRAINT export_run_items_run_id_draft_run_id_fkey FOREIGN KEY (run_id, draft_run_id) REFERENCES public.export_runs(id, draft_run_id);

-- FK CONSTRAINT: export_runs export_runs_binding_id_template_revision_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_binding_id_template_revision_id_fkey FOREIGN KEY (binding_id, template_revision_id) REFERENCES public.export_template_bindings(id, template_revision_id);

-- FK CONSTRAINT: export_runs export_runs_document_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: export_runs export_runs_draft_run_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_draft_run_id_fkey FOREIGN KEY (draft_run_id) REFERENCES public.draft_runs(id);

-- FK CONSTRAINT: export_runs export_runs_extraction_job_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_extraction_job_id_fkey FOREIGN KEY (extraction_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: export_runs export_runs_render_job_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_render_job_id_fkey FOREIGN KEY (render_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: export_runs export_runs_task_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: export_runs export_runs_task_template_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_task_template_id_fkey FOREIGN KEY (task_template_id) REFERENCES public.task_templates(id);

-- FK CONSTRAINT: export_runs export_runs_template_revision_id_fkey
ALTER TABLE ONLY public.export_runs
    ADD CONSTRAINT export_runs_template_revision_id_fkey FOREIGN KEY (template_revision_id) REFERENCES public.template_revisions(id);

-- FK CONSTRAINT: export_template_bindings export_template_bindings_template_revision_id_fkey
ALTER TABLE ONLY public.export_template_bindings
    ADD CONSTRAINT export_template_bindings_template_revision_id_fkey FOREIGN KEY (template_revision_id) REFERENCES public.template_revisions(id);

-- FK CONSTRAINT: exports exports_run_id_candidate_id_fkey
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_run_id_candidate_id_fkey FOREIGN KEY (run_id, candidate_id) REFERENCES public.export_render_candidates(run_id, id);

-- FK CONSTRAINT: exports exports_run_id_fkey
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.export_runs(id);

-- FK CONSTRAINT: exports exports_task_id_fkey
ALTER TABLE ONLY public.exports
    ADD CONSTRAINT exports_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: features feature_current_revision
ALTER TABLE ONLY public.features
    ADD CONSTRAINT feature_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.feature_revisions(feature_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: feature_revisions feature_revisions_feature_id_fkey
ALTER TABLE ONLY public.feature_revisions
    ADD CONSTRAINT feature_revisions_feature_id_fkey FOREIGN KEY (feature_id) REFERENCES public.features(id);

-- FK CONSTRAINT: feature_revisions feature_revisions_product_id_fkey
ALTER TABLE ONLY public.feature_revisions
    ADD CONSTRAINT feature_revisions_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);

-- FK CONSTRAINT: jobs job_actor_token_fk
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT job_actor_token_fk FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: jobs job_agent_task_fk
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT job_agent_task_fk FOREIGN KEY (agent_session_id, task_id, document_id) REFERENCES public.agent_sessions(id, task_id, document_id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: jobs job_provider_config_fk
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT job_provider_config_fk FOREIGN KEY (provider_config_id) REFERENCES public.provider_configs(id);

-- FK CONSTRAINT: jobs jobs_agent_origin_step_fk
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_agent_origin_step_fk FOREIGN KEY (agent_session_id, agent_step_id) REFERENCES public.agent_steps(session_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: jobs jobs_document_id_fkey
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: jobs jobs_task_id_fkey
ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_certificate_revision
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_certificate_revision FOREIGN KEY (certificate_id, resource_revision) REFERENCES public.certificate_revisions(certificate_id, revision);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_certificate_root
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_certificate_root FOREIGN KEY (certificate_id) REFERENCES public.certificates(id);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_feature_revision
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_feature_revision FOREIGN KEY (feature_id, resource_revision) REFERENCES public.feature_revisions(feature_id, revision);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_feature_root
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_feature_root FOREIGN KEY (feature_id) REFERENCES public.features(id);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_profile_revision
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_profile_revision FOREIGN KEY (profile_id, resource_revision) REFERENCES public.org_profile_revisions(profile_id, revision);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_profile_root
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_profile_root FOREIGN KEY (profile_id) REFERENCES public.org_profiles(id);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_template_revision
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_template_revision FOREIGN KEY (template_id, resource_revision) REFERENCES public.template_revisions(template_id, revision);

-- FK CONSTRAINT: resource_lifecycle_events lifecycle_template_root
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT lifecycle_template_root FOREIGN KEY (template_id) REFERENCES public.templates(id);

-- FK CONSTRAINT: confidential_field_search_tokens management_confidential_token_field
ALTER TABLE ONLY public.confidential_field_search_tokens
    ADD CONSTRAINT management_confidential_token_field FOREIGN KEY (field_id) REFERENCES public.confidential_fields(id) ON DELETE CASCADE;

-- FK CONSTRAINT: memories memories_source_feedback_event_id_fkey
ALTER TABLE ONLY public.memories
    ADD CONSTRAINT memories_source_feedback_event_id_fkey FOREIGN KEY (source_feedback_event_id) REFERENCES public.memory_feedback_events(id);

-- FK CONSTRAINT: memories memories_task_id_fkey
ALTER TABLE ONLY public.memories
    ADD CONSTRAINT memories_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: memory_call_inputs memory_call_inputs_job_id_run_id_call_id_fkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_job_id_run_id_call_id_fkey FOREIGN KEY (job_id, run_id, call_id) REFERENCES public.vendor_calls(job_id, run_id, id);

-- FK CONSTRAINT: memory_call_inputs memory_call_inputs_job_id_task_id_fkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: memory_call_inputs memory_call_inputs_retrieval_id_fkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_retrieval_id_fkey FOREIGN KEY (retrieval_id) REFERENCES public.memory_retrievals(id);

-- FK CONSTRAINT: memory_call_inputs memory_call_inputs_task_id_fkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: memory_call_inputs memory_call_inputs_usage_record_id_job_id_run_id_ca_fkey
ALTER TABLE ONLY public.memory_call_inputs
    ADD CONSTRAINT memory_call_inputs_usage_record_id_job_id_run_id_ca_fkey FOREIGN KEY (usage_record_id, job_id, run_id, call_id) REFERENCES public.usage_records(id, job_id, run_id, call_id);

-- FK CONSTRAINT: memories memory_current_revision
ALTER TABLE ONLY public.memories
    ADD CONSTRAINT memory_current_revision FOREIGN KEY (id, current_revision_id, revision) REFERENCES public.memory_revisions(memory_id, id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: memory_eval_samples memory_eval_samples_card_id_after_revision_id_fkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_card_id_after_revision_id_fkey FOREIGN KEY (card_id, after_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: memory_eval_samples memory_eval_samples_card_id_before_revision_id_fkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_card_id_before_revision_id_fkey FOREIGN KEY (card_id, before_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: memory_eval_samples memory_eval_samples_card_id_task_id_fkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_card_id_task_id_fkey FOREIGN KEY (card_id, task_id) REFERENCES public.response_cards(id, task_id);

-- FK CONSTRAINT: memory_eval_samples memory_eval_samples_feedback_event_id_fkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_feedback_event_id_fkey FOREIGN KEY (feedback_event_id) REFERENCES public.memory_feedback_events(id);

-- FK CONSTRAINT: memory_eval_samples memory_eval_samples_task_id_fkey
ALTER TABLE ONLY public.memory_eval_samples
    ADD CONSTRAINT memory_eval_samples_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_card_id_after_revision_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_card_id_after_revision_id_fkey FOREIGN KEY (card_id, after_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_card_id_before_revision_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_card_id_before_revision_id_fkey FOREIGN KEY (card_id, before_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_card_id_task_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_card_id_task_id_fkey FOREIGN KEY (card_id, task_id) REFERENCES public.response_cards(id, task_id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_document_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_model_job_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_model_job_id_fkey FOREIGN KEY (model_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: memory_feedback_events memory_feedback_events_task_id_fkey
ALTER TABLE ONLY public.memory_feedback_events
    ADD CONSTRAINT memory_feedback_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: memory_retrieval_items memory_retrieval_items_memory_id_memory_revision_id_fkey
ALTER TABLE ONLY public.memory_retrieval_items
    ADD CONSTRAINT memory_retrieval_items_memory_id_memory_revision_id_fkey FOREIGN KEY (memory_id, memory_revision_id, revision) REFERENCES public.memory_revisions(memory_id, id, revision);

-- FK CONSTRAINT: memory_retrieval_items memory_retrieval_items_retrieval_id_fkey
ALTER TABLE ONLY public.memory_retrieval_items
    ADD CONSTRAINT memory_retrieval_items_retrieval_id_fkey FOREIGN KEY (retrieval_id) REFERENCES public.memory_retrievals(id);

-- FK CONSTRAINT: memory_retrievals memory_retrievals_actor_token_id_fkey
ALTER TABLE ONLY public.memory_retrievals
    ADD CONSTRAINT memory_retrievals_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: memory_retrievals memory_retrievals_task_id_fkey
ALTER TABLE ONLY public.memory_retrievals
    ADD CONSTRAINT memory_retrievals_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: memory_revisions memory_revisions_actor_token_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: memory_revisions memory_revisions_card_id_card_revision_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_card_id_card_revision_id_fkey FOREIGN KEY (card_id, card_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: memory_revisions memory_revisions_card_id_task_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_card_id_task_id_fkey FOREIGN KEY (card_id, task_id) REFERENCES public.response_cards(id, task_id);

-- FK CONSTRAINT: memory_revisions memory_revisions_feedback_event_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_feedback_event_id_fkey FOREIGN KEY (feedback_event_id) REFERENCES public.memory_feedback_events(id);

-- FK CONSTRAINT: memory_revisions memory_revisions_memory_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_memory_id_fkey FOREIGN KEY (memory_id) REFERENCES public.memories(id);

-- FK CONSTRAINT: memory_revisions memory_revisions_proposal_job_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_proposal_job_id_fkey FOREIGN KEY (proposal_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: memory_revisions memory_revisions_task_id_fkey
ALTER TABLE ONLY public.memory_revisions
    ADD CONSTRAINT memory_revisions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: org_profile_revisions org_profile_revisions_profile_id_fkey
ALTER TABLE ONLY public.org_profile_revisions
    ADD CONSTRAINT org_profile_revisions_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.org_profiles(id);

-- FK CONSTRAINT: products product_current_revision
ALTER TABLE ONLY public.products
    ADD CONSTRAINT product_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.product_revisions(product_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: product_revisions product_revisions_product_id_fkey
ALTER TABLE ONLY public.product_revisions
    ADD CONSTRAINT product_revisions_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);

-- FK CONSTRAINT: profile_attachment_links profile_attachment_links_attachment_id_attachment_r_fkey
ALTER TABLE ONLY public.profile_attachment_links
    ADD CONSTRAINT profile_attachment_links_attachment_id_attachment_r_fkey FOREIGN KEY (attachment_id, attachment_revision_id, file_id, approval_id) REFERENCES public.attachment_reviews(attachment_id, attachment_revision_id, file_id, id);

-- FK CONSTRAINT: profile_attachment_links profile_attachment_links_profile_id_profile_revisio_fkey
ALTER TABLE ONLY public.profile_attachment_links
    ADD CONSTRAINT profile_attachment_links_profile_id_profile_revisio_fkey FOREIGN KEY (profile_id, profile_revision_id) REFERENCES public.org_profile_revisions(profile_id, id);

-- FK CONSTRAINT: org_profiles profile_current_revision
ALTER TABLE ONLY public.org_profiles
    ADD CONSTRAINT profile_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.org_profile_revisions(profile_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: prototype_decision_batches prototype_decision_batches_extraction_job_id_task_i_fkey
ALTER TABLE ONLY public.prototype_decision_batches
    ADD CONSTRAINT prototype_decision_batches_extraction_job_id_task_i_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: prototype_decision_batches prototype_decision_batches_task_id_fkey
ALTER TABLE ONLY public.prototype_decision_batches
    ADD CONSTRAINT prototype_decision_batches_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_asset_id_rendition_id__fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_asset_id_rendition_id__fkey FOREIGN KEY (asset_id, rendition_id, image_sha256) REFERENCES public.screenshot_renditions(asset_id, id, image_sha256);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_card_id_card_revision__fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_card_id_card_revision__fkey FOREIGN KEY (card_id, card_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_card_id_evidence_id_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_card_id_evidence_id_fkey FOREIGN KEY (card_id, evidence_id) REFERENCES public.evidence(card_id, id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_evidence_id_previous_d_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_evidence_id_previous_d_fkey FOREIGN KEY (evidence_id, previous_decision_id) REFERENCES public.prototype_evidence_decisions(evidence_id, id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_extraction_job_id_task_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_extraction_job_id_task_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_feature_revision_id_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_feature_revision_id_fkey FOREIGN KEY (feature_revision_id) REFERENCES public.feature_revisions(id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_task_feature_id_task_i_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_task_feature_id_task_i_fkey FOREIGN KEY (task_feature_id, task_id, feature_revision_id) REFERENCES public.task_features(id, task_id, feature_revision_id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_task_id_batch_id_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_task_id_batch_id_fkey FOREIGN KEY (task_id, batch_id) REFERENCES public.prototype_decision_batches(task_id, id);

-- FK CONSTRAINT: prototype_evidence_decisions prototype_evidence_decisions_task_id_fkey
ALTER TABLE ONLY public.prototype_evidence_decisions
    ADD CONSTRAINT prototype_evidence_decisions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: requirement_reviews requirement_current_event
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_current_event FOREIGN KEY (id, current_event_id, revision) REFERENCES public.requirement_review_events(review_id, id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: requirements requirement_job_document_binding
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirement_job_document_binding FOREIGN KEY (job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: requirement_review_events requirement_review_events_review_id_task_id_extract_fkey
ALTER TABLE ONLY public.requirement_review_events
    ADD CONSTRAINT requirement_review_events_review_id_task_id_extract_fkey FOREIGN KEY (review_id, task_id, extraction_job_id, requirement_id) REFERENCES public.requirement_reviews(id, task_id, extraction_job_id, requirement_id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: requirement_review_events requirement_review_events_task_id_fkey
ALTER TABLE ONLY public.requirement_review_events
    ADD CONSTRAINT requirement_review_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: requirement_review_requests requirement_review_requests_task_id_fkey
ALTER TABLE ONLY public.requirement_review_requests
    ADD CONSTRAINT requirement_review_requests_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: requirement_review_sets requirement_review_sets_task_id_document_id_extract_fkey
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_task_id_document_id_extract_fkey FOREIGN KEY (task_id, document_id, extraction_job_id) REFERENCES public.jobs(task_id, document_id, id);

-- FK CONSTRAINT: requirement_review_sets requirement_review_sets_task_id_fkey
ALTER TABLE ONLY public.requirement_review_sets
    ADD CONSTRAINT requirement_review_sets_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: requirement_reviews requirement_reviews_rejected_job_id_fkey
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_rejected_job_id_fkey FOREIGN KEY (rejected_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: requirement_reviews requirement_reviews_requirement_id_task_id_extracti_fkey
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_requirement_id_task_id_extracti_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: requirement_reviews requirement_reviews_task_id_extraction_job_id_fkey
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_task_id_extraction_job_id_fkey FOREIGN KEY (task_id, extraction_job_id) REFERENCES public.requirement_review_sets(task_id, extraction_job_id);

-- FK CONSTRAINT: requirement_reviews requirement_reviews_task_id_fkey
ALTER TABLE ONLY public.requirement_reviews
    ADD CONSTRAINT requirement_reviews_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: requirements requirements_chunk_id_fkey
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.chunks(id);

-- FK CONSTRAINT: requirements requirements_document_id_fkey
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: requirements requirements_job_id_fkey
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: requirements requirements_task_id_fkey
ALTER TABLE ONLY public.requirements
    ADD CONSTRAINT requirements_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: resource_lifecycle_events resource_lifecycle_events_product_id_fkey
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT resource_lifecycle_events_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);

-- FK CONSTRAINT: resource_lifecycle_events resource_lifecycle_events_product_id_resource_revis_fkey
ALTER TABLE ONLY public.resource_lifecycle_events
    ADD CONSTRAINT resource_lifecycle_events_product_id_resource_revis_fkey FOREIGN KEY (product_id, resource_revision) REFERENCES public.product_revisions(product_id, revision);

-- FK CONSTRAINT: response_cards response_card_current_revision
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_card_current_revision FOREIGN KEY (id, current_revision_id, revision) REFERENCES public.response_card_revisions(card_id, id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: response_cards response_card_job_binding
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_card_job_binding FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: response_cards response_card_requirement_binding
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_card_requirement_binding FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: response_card_revisions response_card_revisions_actor_token_id_fkey
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: response_card_revisions response_card_revisions_card_id_fkey
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_card_id_fkey FOREIGN KEY (card_id) REFERENCES public.response_cards(id);

-- FK CONSTRAINT: response_card_revisions response_card_revisions_model_job_id_fkey
ALTER TABLE ONLY public.response_card_revisions
    ADD CONSTRAINT response_card_revisions_model_job_id_fkey FOREIGN KEY (model_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: response_cards response_cards_extraction_job_id_fkey
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_extraction_job_id_fkey FOREIGN KEY (extraction_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: response_cards response_cards_requirement_id_fkey
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES public.requirements(id);

-- FK CONSTRAINT: response_cards response_cards_task_id_fkey
ALTER TABLE ONLY public.response_cards
    ADD CONSTRAINT response_cards_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: response_items response_item_card_revision_binding
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_item_card_revision_binding FOREIGN KEY (card_id, card_revision_id) REFERENCES public.response_card_revisions(card_id, id);

-- FK CONSTRAINT: response_items response_items_card_id_fkey
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_card_id_fkey FOREIGN KEY (card_id) REFERENCES public.response_cards(id);

-- FK CONSTRAINT: response_items response_items_card_revision_id_fkey
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_card_revision_id_fkey FOREIGN KEY (card_revision_id) REFERENCES public.response_card_revisions(id);

-- FK CONSTRAINT: response_items response_items_draft_id_fkey
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.draft_runs(id);

-- FK CONSTRAINT: response_items response_items_requirement_id_fkey
ALTER TABLE ONLY public.response_items
    ADD CONSTRAINT response_items_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES public.requirements(id);

-- FK CONSTRAINT: sandbox_artifacts sandbox_artifacts_attempt_record_id_sandbox_run_id__fkey
ALTER TABLE ONLY public.sandbox_artifacts
    ADD CONSTRAINT sandbox_artifacts_attempt_record_id_sandbox_run_id__fkey FOREIGN KEY (attempt_record_id, sandbox_run_id, input_id, task_id) REFERENCES public.sandbox_attempts(id, sandbox_run_id, input_id, task_id);

-- FK CONSTRAINT: sandbox_artifacts sandbox_artifacts_parent_artifact_id_sandbox_run_id_fkey
ALTER TABLE ONLY public.sandbox_artifacts
    ADD CONSTRAINT sandbox_artifacts_parent_artifact_id_sandbox_run_id_fkey FOREIGN KEY (parent_artifact_id, sandbox_run_id, input_id, task_id, attempt_record_id) REFERENCES public.sandbox_artifacts(id, sandbox_run_id, input_id, task_id, attempt_record_id);

-- FK CONSTRAINT: sandbox_attempts sandbox_attempts_sandbox_run_id_input_id_task_id_jo_fkey
ALTER TABLE ONLY public.sandbox_attempts
    ADD CONSTRAINT sandbox_attempts_sandbox_run_id_input_id_task_id_jo_fkey FOREIGN KEY (sandbox_run_id, input_id, task_id, job_id) REFERENCES public.sandbox_runs(id, input_id, task_id, job_id);

-- FK CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_attempt_record_id_sandbox_ru_fkey
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_attempt_record_id_sandbox_ru_fkey FOREIGN KEY (attempt_record_id, sandbox_run_id, input_id, task_id) REFERENCES public.sandbox_attempts(id, sandbox_run_id, input_id, task_id);

-- FK CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_bundle_artifact_id_sandbox_r_fkey
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_bundle_artifact_id_sandbox_r_fkey FOREIGN KEY (bundle_artifact_id, sandbox_run_id, input_id, task_id, attempt_record_id) REFERENCES public.sandbox_artifacts(id, sandbox_run_id, input_id, task_id, attempt_record_id);

-- FK CONSTRAINT: sandbox_fetch_receipts sandbox_fetch_receipts_parent_redirect_id_attempt_r_fkey
ALTER TABLE ONLY public.sandbox_fetch_receipts
    ADD CONSTRAINT sandbox_fetch_receipts_parent_redirect_id_attempt_r_fkey FOREIGN KEY (parent_redirect_id, attempt_record_id) REFERENCES public.sandbox_fetch_receipts(id, attempt_record_id);

-- FK CONSTRAINT: sandbox_inputs sandbox_generation_binding
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_generation_binding FOREIGN KEY (generation_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: sandbox_inputs sandbox_input_document
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_input_document FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_document_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_extraction_job_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_extraction_job_id_fkey FOREIGN KEY (extraction_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_extraction_job_id_task_id_document_i_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_extraction_job_id_task_id_document_i_fkey FOREIGN KEY (extraction_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_feature_revision_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_feature_revision_id_fkey FOREIGN KEY (feature_revision_id) REFERENCES public.feature_revisions(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_generation_job_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_generation_job_id_fkey FOREIGN KEY (generation_job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_product_revision_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_product_revision_id_fkey FOREIGN KEY (product_revision_id) REFERENCES public.product_revisions(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_task_feature_id_task_id_feature_revi_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_task_feature_id_task_id_feature_revi_fkey FOREIGN KEY (task_feature_id, task_id, feature_revision_id) REFERENCES public.task_features(id, task_id, feature_revision_id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_task_id_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: sandbox_inputs sandbox_inputs_task_resource_id_task_id_product_rev_fkey
ALTER TABLE ONLY public.sandbox_inputs
    ADD CONSTRAINT sandbox_inputs_task_resource_id_task_id_product_rev_fkey FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: sandbox_runs sandbox_runs_input_id_task_id_document_id_fkey
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_input_id_task_id_document_id_fkey FOREIGN KEY (input_id, task_id, document_id) REFERENCES public.sandbox_inputs(id, task_id, document_id);

-- FK CONSTRAINT: sandbox_runs sandbox_runs_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_job_id_task_id_document_id_fkey FOREIGN KEY (job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: sandbox_runs sandbox_runs_token_id_fkey
ALTER TABLE ONLY public.sandbox_runs
    ADD CONSTRAINT sandbox_runs_token_id_fkey FOREIGN KEY (token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: score_item_citations score_item_citations_chunk_id_task_id_document_id_fkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_chunk_id_task_id_document_id_fkey FOREIGN KEY (chunk_id, task_id, document_id) REFERENCES public.chunks(id, task_id, document_id);

-- FK CONSTRAINT: score_item_citations score_item_citations_document_id_task_id_fkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_document_id_task_id_fkey FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: score_item_citations score_item_citations_score_item_id_task_id_report_i_fkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_score_item_id_task_id_report_i_fkey FOREIGN KEY (score_item_id, task_id, report_id) REFERENCES public.score_report_items(id, task_id, report_id);

-- FK CONSTRAINT: score_item_citations score_item_citations_task_id_fkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_item_citations score_item_citations_task_id_report_id_score_item_i_fkey
ALTER TABLE ONLY public.score_item_citations
    ADD CONSTRAINT score_item_citations_task_id_report_id_score_item_i_fkey FOREIGN KEY (task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id) REFERENCES public.score_report_item_responses(task_id, report_id, score_item_id, draft_id, response_item_id, card_revision_id);

-- FK CONSTRAINT: score_report_item_responses score_report_item_responses_response_item_id_draft__fkey
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_response_item_id_draft__fkey FOREIGN KEY (response_item_id, draft_id, requirement_id, card_revision_id) REFERENCES public.response_items(id, draft_id, requirement_id, card_revision_id);

-- FK CONSTRAINT: score_report_item_responses score_report_item_responses_score_item_id_task_id_r_fkey
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_score_item_id_task_id_r_fkey FOREIGN KEY (score_item_id, task_id, report_id, draft_id) REFERENCES public.score_report_items(id, task_id, report_id, draft_id);

-- FK CONSTRAINT: score_report_item_responses score_report_item_responses_task_id_fkey
ALTER TABLE ONLY public.score_report_item_responses
    ADD CONSTRAINT score_report_item_responses_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_report_items score_report_items_anchor_response_item_id_draft_id_fkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_anchor_response_item_id_draft_id_fkey FOREIGN KEY (anchor_response_item_id, draft_id, requirement_id) REFERENCES public.response_items(id, draft_id, requirement_id);

-- FK CONSTRAINT: score_report_items score_report_items_report_id_task_id_draft_id_extra_fkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_report_id_task_id_draft_id_extra_fkey FOREIGN KEY (report_id, task_id, draft_id, extraction_job_id, rubric_id) REFERENCES public.score_reports(id, task_id, draft_id, extraction_job_id, rubric_id);

-- FK CONSTRAINT: score_report_items score_report_items_requirement_id_task_id_extractio_fkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_requirement_id_task_id_extractio_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: score_report_items score_report_items_rubric_item_id_task_id_rubric_id_fkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_rubric_item_id_task_id_rubric_id_fkey FOREIGN KEY (rubric_item_id, task_id, rubric_id, section_id, requirement_id) REFERENCES public.score_rubric_items(id, task_id, rubric_id, section_id, requirement_id);

-- FK CONSTRAINT: score_report_items score_report_items_task_id_fkey
ALTER TABLE ONLY public.score_report_items
    ADD CONSTRAINT score_report_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_reports score_reports_actor_token_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: score_reports score_reports_document_id_task_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_document_id_task_id_fkey FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: score_reports score_reports_draft_id_task_id_extraction_job_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_draft_id_task_id_extraction_job_id_fkey FOREIGN KEY (draft_id, task_id, extraction_job_id) REFERENCES public.draft_runs(id, task_id, extraction_job_id);

-- FK CONSTRAINT: score_reports score_reports_extraction_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_extraction_job_id_task_id_document_id_fkey FOREIGN KEY (extraction_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: score_reports score_reports_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_job_id_task_id_document_id_fkey FOREIGN KEY (job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: score_reports score_reports_rubric_id_task_id_extraction_job_id_d_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_rubric_id_task_id_extraction_job_id_d_fkey FOREIGN KEY (rubric_id, task_id, extraction_job_id, document_id) REFERENCES public.score_rubric_sets(id, task_id, extraction_job_id, document_id);

-- FK CONSTRAINT: score_reports score_reports_task_id_fkey
ALTER TABLE ONLY public.score_reports
    ADD CONSTRAINT score_reports_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_classifications score_rubric_classifications_item_id_task_id_rubric_fkey
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_item_id_task_id_rubric_fkey FOREIGN KEY (item_id, task_id, rubric_id) REFERENCES public.score_rubric_items(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_classifications score_rubric_classifications_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_classifications score_rubric_classifications_section_id_task_id_rub_fkey
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_section_id_task_id_rub_fkey FOREIGN KEY (section_id, task_id, rubric_id) REFERENCES public.score_rubric_sections(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_classifications score_rubric_classifications_task_id_fkey
ALTER TABLE ONLY public.score_rubric_classifications
    ADD CONSTRAINT score_rubric_classifications_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisio_canonical_requirement_fkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisio_canonical_requirement_fkey FOREIGN KEY (canonical_requirement_id, task_id) REFERENCES public.requirements(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisio_coverage_id_task_id_r_fkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisio_coverage_id_task_id_r_fkey FOREIGN KEY (coverage_id, task_id, rubric_id) REFERENCES public.score_rubric_coverage(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisio_requirement_id_task_i_fkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisio_requirement_id_task_i_fkey FOREIGN KEY (requirement_id, task_id) REFERENCES public.requirements(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage_decisions score_rubric_coverage_decisions_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage_decisions
    ADD CONSTRAINT score_rubric_coverage_decisions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_coverage_decision_id_ta_fkey
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_coverage_decision_id_ta_fkey FOREIGN KEY (coverage_decision_id, task_id, rubric_id) REFERENCES public.score_rubric_coverage_decisions(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_rubric_item_id_task_id__fkey
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_rubric_item_id_task_id__fkey FOREIGN KEY (rubric_item_id, task_id, rubric_id) REFERENCES public.score_rubric_items(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_coverage_items score_rubric_coverage_items_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage_items
    ADD CONSTRAINT score_rubric_coverage_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_coverage score_rubric_coverage_requirement_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_requirement_id_task_id_fkey FOREIGN KEY (requirement_id, task_id) REFERENCES public.requirements(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage score_rubric_coverage_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_coverage score_rubric_coverage_task_id_fkey
ALTER TABLE ONLY public.score_rubric_coverage
    ADD CONSTRAINT score_rubric_coverage_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_decisions score_rubric_decisions_item_id_task_id_rubric_id_fkey
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_item_id_task_id_rubric_id_fkey FOREIGN KEY (item_id, task_id, rubric_id) REFERENCES public.score_rubric_items(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_decisions score_rubric_decisions_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_decisions score_rubric_decisions_section_id_task_id_rubric_id_fkey
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_section_id_task_id_rubric_id_fkey FOREIGN KEY (section_id, task_id, rubric_id) REFERENCES public.score_rubric_sections(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_decisions score_rubric_decisions_task_id_fkey
ALTER TABLE ONLY public.score_rubric_decisions
    ADD CONSTRAINT score_rubric_decisions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_items score_rubric_items_requirement_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_requirement_id_task_id_fkey FOREIGN KEY (requirement_id, task_id) REFERENCES public.requirements(id, task_id);

-- FK CONSTRAINT: score_rubric_items score_rubric_items_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_items score_rubric_items_section_id_task_id_rubric_id_fkey
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_section_id_task_id_rubric_id_fkey FOREIGN KEY (section_id, task_id, rubric_id) REFERENCES public.score_rubric_sections(id, task_id, rubric_id);

-- FK CONSTRAINT: score_rubric_items score_rubric_items_task_id_fkey
ALTER TABLE ONLY public.score_rubric_items
    ADD CONSTRAINT score_rubric_items_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_prior_rubric_id_task_i_fkey
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_prior_rubric_id_task_i_fkey FOREIGN KEY (prior_rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_revision_events score_rubric_revision_events_task_id_fkey
ALTER TABLE ONLY public.score_rubric_revision_events
    ADD CONSTRAINT score_rubric_revision_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_sections score_rubric_sections_requirement_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_requirement_id_task_id_fkey FOREIGN KEY (requirement_id, task_id) REFERENCES public.requirements(id, task_id);

-- FK CONSTRAINT: score_rubric_sections score_rubric_sections_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_rubric_id_task_id_fkey FOREIGN KEY (rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_sections score_rubric_sections_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sections
    ADD CONSTRAINT score_rubric_sections_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_actor_token_id_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_actor_token_id_fkey FOREIGN KEY (actor_token_id) REFERENCES public.api_tokens(id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_document_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_document_id_task_id_fkey FOREIGN KEY (document_id, task_id) REFERENCES public.documents(id, task_id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_extraction_job_id_task_id_documen_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_extraction_job_id_task_id_documen_fkey FOREIGN KEY (extraction_job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_job_id_task_id_document_id_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_job_id_task_id_document_id_fkey FOREIGN KEY (job_id, task_id, document_id) REFERENCES public.jobs(id, task_id, document_id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_prior_rubric_id_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_prior_rubric_id_task_id_fkey FOREIGN KEY (prior_rubric_id, task_id) REFERENCES public.score_rubric_sets(id, task_id);

-- FK CONSTRAINT: score_rubric_sets score_rubric_sets_task_id_fkey
ALTER TABLE ONLY public.score_rubric_sets
    ADD CONSTRAINT score_rubric_sets_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_asset_id_privacy_review__fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_asset_id_privacy_review__fkey FOREIGN KEY (asset_id, privacy_review_id) REFERENCES public.screenshot_privacy_reviews(asset_id, id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_asset_id_rendition_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_asset_id_rendition_id_fkey FOREIGN KEY (asset_id, rendition_id) REFERENCES public.screenshot_renditions(asset_id, id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_extraction_job_id_task_i_fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_extraction_job_id_task_i_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_requirement_id_task_id_e_fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_requirement_id_task_id_e_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_task_id_analysis_run_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_task_id_analysis_run_id_fkey FOREIGN KEY (task_id, analysis_run_id) REFERENCES public.screenshot_analysis_runs(task_id, id);

-- FK CONSTRAINT: screenshot_analysis_inputs screenshot_analysis_inputs_task_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_inputs
    ADD CONSTRAINT screenshot_analysis_inputs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_analysis_runs screenshot_analysis_runs_task_id_fkey
ALTER TABLE ONLY public.screenshot_analysis_runs
    ADD CONSTRAINT screenshot_analysis_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_evidence_source_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_evidence_source_id_fkey FOREIGN KEY (evidence_source_id) REFERENCES public.evidence_sources(id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_feature_revision_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_feature_revision_id_fkey FOREIGN KEY (feature_revision_id) REFERENCES public.feature_revisions(id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_product_revision_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_product_revision_id_fkey FOREIGN KEY (product_revision_id) REFERENCES public.product_revisions(id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_task_feature_id_task_id_feature_r_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_feature_id_task_id_feature_r_fkey FOREIGN KEY (task_feature_id, task_id, feature_revision_id) REFERENCES public.task_features(id, task_id, feature_revision_id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_task_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_task_id_prototype_run_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_id_prototype_run_id_fkey FOREIGN KEY (task_id, prototype_run_id) REFERENCES public.screenshot_prototype_runs(task_id, id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_task_id_vendor_archive_id_fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_id_vendor_archive_id_fkey FOREIGN KEY (task_id, vendor_archive_id) REFERENCES public.screenshot_vendor_archives(task_id, id);

-- FK CONSTRAINT: screenshot_assets screenshot_assets_task_resource_id_task_id_product__fkey
ALTER TABLE ONLY public.screenshot_assets
    ADD CONSTRAINT screenshot_assets_task_resource_id_task_id_product__fkey FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_asset_id_rendition_id_st_fkey
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_asset_id_rendition_id_st_fkey FOREIGN KEY (asset_id, rendition_id, stored_image_sha256) REFERENCES public.screenshot_renditions(asset_id, id, image_sha256);

-- FK CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_extraction_job_id_task_i_fkey
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_extraction_job_id_task_i_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_task_id_asset_id_fkey
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_task_id_asset_id_fkey FOREIGN KEY (task_id, asset_id) REFERENCES public.screenshot_assets(task_id, id);

-- FK CONSTRAINT: screenshot_privacy_reviews screenshot_privacy_reviews_task_id_fkey
ALTER TABLE ONLY public.screenshot_privacy_reviews
    ADD CONSTRAINT screenshot_privacy_reviews_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_feature_revision_id_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_feature_revision_id_fkey FOREIGN KEY (feature_revision_id) REFERENCES public.feature_revisions(id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_generation_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_generation_job_id_task_id_fkey FOREIGN KEY (generation_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_requirement_id_task_id_ex_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_requirement_id_task_id_ex_fkey FOREIGN KEY (requirement_id, task_id, extraction_job_id) REFERENCES public.requirements(id, task_id, job_id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_task_feature_id_task_id_f_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_task_feature_id_task_id_f_fkey FOREIGN KEY (task_feature_id, task_id, feature_revision_id) REFERENCES public.task_features(id, task_id, feature_revision_id);

-- FK CONSTRAINT: screenshot_prototype_runs screenshot_prototype_runs_task_id_fkey
ALTER TABLE ONLY public.screenshot_prototype_runs
    ADD CONSTRAINT screenshot_prototype_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_renditions screenshot_rendition_review_fk
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_rendition_review_fk FOREIGN KEY (asset_id, privacy_review_id) REFERENCES public.screenshot_privacy_reviews(asset_id, id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: screenshot_renditions screenshot_renditions_asset_id_parent_rendition_id_fkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_asset_id_parent_rendition_id_fkey FOREIGN KEY (asset_id, parent_rendition_id) REFERENCES public.screenshot_renditions(asset_id, id);

-- FK CONSTRAINT: screenshot_renditions screenshot_renditions_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_renditions screenshot_renditions_generation_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_generation_job_id_task_id_fkey FOREIGN KEY (generation_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_renditions screenshot_renditions_task_id_asset_id_fkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_task_id_asset_id_fkey FOREIGN KEY (task_id, asset_id) REFERENCES public.screenshot_assets(task_id, id);

-- FK CONSTRAINT: screenshot_renditions screenshot_renditions_task_id_fkey
ALTER TABLE ONLY public.screenshot_renditions
    ADD CONSTRAINT screenshot_renditions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_extraction_job_id_task_fkey
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_extraction_job_id_task_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_task_id_fkey
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_search_candidates screenshot_search_candidates_task_id_search_run_id_fkey
ALTER TABLE ONLY public.screenshot_search_candidates
    ADD CONSTRAINT screenshot_search_candidates_task_id_search_run_id_fkey FOREIGN KEY (task_id, search_run_id) REFERENCES public.screenshot_search_runs(task_id, id);

-- FK CONSTRAINT: screenshot_search_runs screenshot_search_runs_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_search_runs screenshot_search_runs_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_job_id_task_id_fkey FOREIGN KEY (job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_search_runs screenshot_search_runs_product_revision_id_fkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_product_revision_id_fkey FOREIGN KEY (product_revision_id) REFERENCES public.product_revisions(id);

-- FK CONSTRAINT: screenshot_search_runs screenshot_search_runs_task_id_fkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_search_runs screenshot_search_runs_task_resource_id_task_id_pro_fkey
ALTER TABLE ONLY public.screenshot_search_runs
    ADD CONSTRAINT screenshot_search_runs_task_resource_id_task_id_pro_fkey FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: screenshot_suggestions screenshot_suggestions_analysis_run_id_image_input__fkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_analysis_run_id_image_input__fkey FOREIGN KEY (analysis_run_id, image_input_id) REFERENCES public.screenshot_analysis_inputs(analysis_run_id, id);

-- FK CONSTRAINT: screenshot_suggestions screenshot_suggestions_analysis_run_id_requirement__fkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_analysis_run_id_requirement__fkey FOREIGN KEY (analysis_run_id, requirement_input_id) REFERENCES public.screenshot_analysis_inputs(analysis_run_id, id);

-- FK CONSTRAINT: screenshot_suggestions screenshot_suggestions_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_suggestions screenshot_suggestions_task_id_analysis_run_id_fkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_task_id_analysis_run_id_fkey FOREIGN KEY (task_id, analysis_run_id) REFERENCES public.screenshot_analysis_runs(task_id, id);

-- FK CONSTRAINT: screenshot_suggestions screenshot_suggestions_task_id_fkey
ALTER TABLE ONLY public.screenshot_suggestions
    ADD CONSTRAINT screenshot_suggestions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archive_artifact
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archive_artifact FOREIGN KEY (archive_artifact_id) REFERENCES public.sandbox_artifacts(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archive_entry
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archive_entry FOREIGN KEY (entry_receipt_id) REFERENCES public.sandbox_fetch_receipts(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archive_run
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archive_run FOREIGN KEY (sandbox_run_id) REFERENCES public.sandbox_runs(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_extraction_job_id_task_i_fkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_extraction_job_id_task_i_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_product_revision_id_fkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_product_revision_id_fkey FOREIGN KEY (product_revision_id) REFERENCES public.product_revisions(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_task_id_fkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_task_id_search_candidate_fkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_task_id_search_candidate_fkey FOREIGN KEY (task_id, search_candidate_id) REFERENCES public.screenshot_search_candidates(task_id, id);

-- FK CONSTRAINT: screenshot_vendor_archives screenshot_vendor_archives_task_resource_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_vendor_archives
    ADD CONSTRAINT screenshot_vendor_archives_task_resource_id_task_id_fkey FOREIGN KEY (task_resource_id, task_id, product_revision_id) REFERENCES public.task_resources(id, task_id, product_revision_id);

-- FK CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_extraction_job_id_task_id_fkey
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_extraction_job_id_task_id_fkey FOREIGN KEY (extraction_job_id, task_id) REFERENCES public.jobs(id, task_id);

-- FK CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_task_id_asset_id_fkey
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_task_id_asset_id_fkey FOREIGN KEY (task_id, asset_id) REFERENCES public.screenshot_assets(task_id, id);

-- FK CONSTRAINT: screenshot_withdrawals screenshot_withdrawals_task_id_fkey
ALTER TABLE ONLY public.screenshot_withdrawals
    ADD CONSTRAINT screenshot_withdrawals_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: simulated_resources simulated_resources_feature_id_fkey
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_feature_id_fkey FOREIGN KEY (feature_id) REFERENCES public.features(id);

-- FK CONSTRAINT: simulated_resources simulated_resources_job_id_fkey
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: simulated_resources simulated_resources_product_id_fkey
ALTER TABLE ONLY public.simulated_resources
    ADD CONSTRAINT simulated_resources_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(id);

-- FK CONSTRAINT: task_attachments task_attachments_profile_attachment_link_id_profile_fkey
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT task_attachments_profile_attachment_link_id_profile_fkey FOREIGN KEY (profile_attachment_link_id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id) REFERENCES public.profile_attachment_links(id, profile_revision_id, field, attachment_id, attachment_revision_id, file_id, approval_id);

-- FK CONSTRAINT: task_attachments task_attachments_task_id_fkey
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT task_attachments_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_attachments task_attachments_task_org_profile_id_task_id_profil_fkey
ALTER TABLE ONLY public.task_attachments
    ADD CONSTRAINT task_attachments_task_org_profile_id_task_id_profil_fkey FOREIGN KEY (task_org_profile_id, task_id, profile_revision_id, lot) REFERENCES public.task_org_profiles(id, task_id, profile_revision_id, lot);

-- FK CONSTRAINT: task_certificates task_certificates_certificate_id_certificate_revisi_fkey
ALTER TABLE ONLY public.task_certificates
    ADD CONSTRAINT task_certificates_certificate_id_certificate_revisi_fkey FOREIGN KEY (certificate_id, certificate_revision_id) REFERENCES public.certificate_revisions(certificate_id, id);

-- FK CONSTRAINT: task_certificates task_certificates_task_id_fkey
ALTER TABLE ONLY public.task_certificates
    ADD CONSTRAINT task_certificates_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_event_heads task_event_heads_task_id_fkey
ALTER TABLE ONLY public.task_event_heads
    ADD CONSTRAINT task_event_heads_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_events task_events_task_id_fkey
ALTER TABLE ONLY public.task_events
    ADD CONSTRAINT task_events_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_features task_features_feature_id_feature_revision_id_fkey
ALTER TABLE ONLY public.task_features
    ADD CONSTRAINT task_features_feature_id_feature_revision_id_fkey FOREIGN KEY (feature_id, feature_revision_id) REFERENCES public.feature_revisions(feature_id, id);

-- FK CONSTRAINT: task_features task_features_task_id_fkey
ALTER TABLE ONLY public.task_features
    ADD CONSTRAINT task_features_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_org_profiles task_org_profiles_profile_id_profile_revision_id_fkey
ALTER TABLE ONLY public.task_org_profiles
    ADD CONSTRAINT task_org_profiles_profile_id_profile_revision_id_fkey FOREIGN KEY (profile_id, profile_revision_id) REFERENCES public.org_profile_revisions(profile_id, id);

-- FK CONSTRAINT: task_org_profiles task_org_profiles_task_id_fkey
ALTER TABLE ONLY public.task_org_profiles
    ADD CONSTRAINT task_org_profiles_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_resources task_resources_product_id_product_revision_id_fkey
ALTER TABLE ONLY public.task_resources
    ADD CONSTRAINT task_resources_product_id_product_revision_id_fkey FOREIGN KEY (product_id, product_revision_id) REFERENCES public.product_revisions(product_id, id);

-- FK CONSTRAINT: task_resources task_resources_task_id_fkey
ALTER TABLE ONLY public.task_resources
    ADD CONSTRAINT task_resources_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_templates task_templates_task_id_fkey
ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT task_templates_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: task_templates task_templates_template_id_template_revision_id_fkey
ALTER TABLE ONLY public.task_templates
    ADD CONSTRAINT task_templates_template_id_template_revision_id_fkey FOREIGN KEY (template_id, template_revision_id) REFERENCES public.template_revisions(template_id, id);

-- FK CONSTRAINT: templates template_current_revision
ALTER TABLE ONLY public.templates
    ADD CONSTRAINT template_current_revision FOREIGN KEY (id, current_revision) REFERENCES public.template_revisions(template_id, revision) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: template_revisions template_revisions_template_id_fkey
ALTER TABLE ONLY public.template_revisions
    ADD CONSTRAINT template_revisions_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.templates(id);

-- FK CONSTRAINT: usage_records usage_call_fk
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_call_fk FOREIGN KEY (job_id, run_id, call_id) REFERENCES public.vendor_calls(job_id, run_id, id);

-- FK CONSTRAINT: usage_records usage_job_fk
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_job_fk FOREIGN KEY (job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: usage_records usage_provider_config_fk
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_provider_config_fk FOREIGN KEY (provider_config_id) REFERENCES public.provider_configs(id);

-- FK CONSTRAINT: usage_records usage_records_task_id_fkey
ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: vendor_calls vendor_call_task_fk
ALTER TABLE ONLY public.vendor_calls
    ADD CONSTRAINT vendor_call_task_fk FOREIGN KEY (task_id) REFERENCES public.tasks(id);

-- FK CONSTRAINT: vendor_calls vendor_calls_job_id_fkey
ALTER TABLE ONLY public.vendor_calls
    ADD CONSTRAINT vendor_calls_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);

-- FK CONSTRAINT: credential_probes credential_probes_credential_id_fkey
ALTER TABLE ONLY public.credential_probes
    ADD CONSTRAINT credential_probes_credential_id_fkey FOREIGN KEY (credential_id) REFERENCES public.platform_credentials(id);
