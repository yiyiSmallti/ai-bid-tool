-- ACL: FUNCTION actor_is_human()
REVOKE ALL ON FUNCTION public.actor_is_human() FROM PUBLIC;
GRANT ALL ON FUNCTION public.actor_is_human() TO bid_app;

-- ACL: FUNCTION agent_history_guard()
REVOKE ALL ON FUNCTION public.agent_history_guard() FROM PUBLIC;

-- ACL: FUNCTION agent_origin_guard()
REVOKE ALL ON FUNCTION public.agent_origin_guard() FROM PUBLIC;

-- ACL: FUNCTION agent_principal_guard()
REVOKE ALL ON FUNCTION public.agent_principal_guard() FROM PUBLIC;

-- ACL: FUNCTION agent_task_authority(p_task uuid)
REVOKE ALL ON FUNCTION public.agent_task_authority(p_task uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.agent_task_authority(p_task uuid) TO bid_app;

-- ACL: FUNCTION annotation_actor_live(p_task uuid)
REVOKE ALL ON FUNCTION public.annotation_actor_live(p_task uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_actor_live(p_task uuid) TO bid_app;

-- ACL: FUNCTION annotation_ascii(p_value text)
REVOKE ALL ON FUNCTION public.annotation_ascii(p_value text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_ascii(p_value text) TO bid_app;

-- ACL: FUNCTION annotation_binding_guard()
REVOKE ALL ON FUNCTION public.annotation_binding_guard() FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_binding_guard() TO bid_app;

-- ACL: FUNCTION annotation_certificate_asset(p_asset uuid, p_task uuid, p_extraction uuid, p_source uuid, p_hash text, p_request uuid)
REVOKE ALL ON FUNCTION public.annotation_certificate_asset(p_asset uuid, p_task uuid, p_extraction uuid, p_source uuid, p_hash text, p_request uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_certificate_asset(p_asset uuid, p_task uuid, p_extraction uuid, p_source uuid, p_hash text, p_request uuid) TO bid_app;

-- ACL: FUNCTION annotation_certificate_privacy(p_task uuid, p_asset uuid, p_rendition uuid, p_review uuid, p_request uuid, p_upload text, p_image text, p_rule text)
REVOKE ALL ON FUNCTION public.annotation_certificate_privacy(p_task uuid, p_asset uuid, p_rendition uuid, p_review uuid, p_request uuid, p_upload text, p_image text, p_rule text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_certificate_privacy(p_task uuid, p_asset uuid, p_rendition uuid, p_review uuid, p_request uuid, p_upload text, p_image text, p_rule text) TO bid_app;

-- ACL: FUNCTION annotation_dependency_event()
REVOKE ALL ON FUNCTION public.annotation_dependency_event() FROM PUBLIC;

-- ACL: FUNCTION annotation_digest(p_value jsonb)
REVOKE ALL ON FUNCTION public.annotation_digest(p_value jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_digest(p_value jsonb) TO bid_app;

-- ACL: FUNCTION annotation_event()
REVOKE ALL ON FUNCTION public.annotation_event() FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_event() TO bid_app;

-- ACL: FUNCTION annotation_evidence_gate()
REVOKE ALL ON FUNCTION public.annotation_evidence_gate() FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_evidence_gate() TO bid_app;

-- ACL: FUNCTION annotation_export_binding(p_evidence uuid, p_revision uuid, p_attachment jsonb)
REVOKE ALL ON FUNCTION public.annotation_export_binding(p_evidence uuid, p_revision uuid, p_attachment jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_export_binding(p_evidence uuid, p_revision uuid, p_attachment jsonb) TO bid_app;

-- ACL: FUNCTION annotation_invalidate_dependencies(p_changed jsonb)
REVOKE ALL ON FUNCTION public.annotation_invalidate_dependencies(p_changed jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_invalidate_dependencies(p_changed jsonb) TO bid_app;

-- ACL: FUNCTION annotation_invalidation_audit_valid(p_object uuid, p_details jsonb)
REVOKE ALL ON FUNCTION public.annotation_invalidation_audit_valid(p_object uuid, p_details jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_invalidation_audit_valid(p_object uuid, p_details jsonb) TO bid_app;

-- ACL: FUNCTION annotation_job_live(p_job uuid, p_run uuid, p_kind text)
REVOKE ALL ON FUNCTION public.annotation_job_live(p_job uuid, p_run uuid, p_kind text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_job_live(p_job uuid, p_run uuid, p_kind text) TO bid_app;

-- ACL: FUNCTION annotation_prior_evidence_active(p_evidence uuid)
REVOKE ALL ON FUNCTION public.annotation_prior_evidence_active(p_evidence uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_prior_evidence_active(p_evidence uuid) TO bid_app;

-- ACL: FUNCTION annotation_publication_complete()
REVOKE ALL ON FUNCTION public.annotation_publication_complete() FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_publication_complete() TO bid_app;

-- ACL: FUNCTION annotation_release_current(p_release uuid)
REVOKE ALL ON FUNCTION public.annotation_release_current(p_release uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_release_current(p_release uuid) TO bid_app;

-- ACL: FUNCTION annotation_rendition_valid(p_id uuid)
REVOKE ALL ON FUNCTION public.annotation_rendition_valid(p_id uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_rendition_valid(p_id uuid) TO bid_app;

-- ACL: FUNCTION annotation_source_current(p_request uuid)
REVOKE ALL ON FUNCTION public.annotation_source_current(p_request uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_source_current(p_request uuid) TO bid_app;

-- ACL: FUNCTION annotation_task_lock()
REVOKE ALL ON FUNCTION public.annotation_task_lock() FROM PUBLIC;
GRANT ALL ON FUNCTION public.annotation_task_lock() TO bid_app;

-- ACL: FUNCTION append_task_event(p_task uuid, p_kind text, p_payload jsonb, p_source uuid)
REVOKE ALL ON FUNCTION public.append_task_event(p_task uuid, p_kind text, p_payload jsonb, p_source uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.append_task_event(p_task uuid, p_kind text, p_payload jsonb, p_source uuid) TO bid_app;

-- ACL: FUNCTION check_current_inputs(p_report uuid)
REVOKE ALL ON FUNCTION public.check_current_inputs(p_report uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.check_current_inputs(p_report uuid) TO bid_app;

-- ACL: FUNCTION job_actor_guard()
REVOKE ALL ON FUNCTION public.job_actor_guard() FROM PUBLIC;

-- ACL: FUNCTION management_bidder_human(p_kind text)
REVOKE ALL ON FUNCTION public.management_bidder_human(p_kind text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_bidder_human(p_kind text) TO bid_app;

-- ACL: FUNCTION management_casefold(p_text text)
REVOKE ALL ON FUNCTION public.management_casefold(p_text text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_casefold(p_text text) TO bid_app;

-- ACL: FUNCTION management_certificate_revision_search()
REVOKE ALL ON FUNCTION public.management_certificate_revision_search() FROM PUBLIC;

-- ACL: FUNCTION management_certificate_root_guard()
REVOKE ALL ON FUNCTION public.management_certificate_root_guard() FROM PUBLIC;

-- ACL: FUNCTION management_certificate_selection_guard()
REVOKE ALL ON FUNCTION public.management_certificate_selection_guard() FROM PUBLIC;

-- ACL: FUNCTION management_certificate_vector(p_data jsonb)
REVOKE ALL ON FUNCTION public.management_certificate_vector(p_data jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_certificate_vector(p_data jsonb) TO bid_app;

-- ACL: FUNCTION management_confidential_tokens_guard()
REVOKE ALL ON FUNCTION public.management_confidential_tokens_guard() FROM PUBLIC;

-- ACL: FUNCTION management_confidential_tokens_refresh()
REVOKE ALL ON FUNCTION public.management_confidential_tokens_refresh() FROM PUBLIC;

-- ACL: FUNCTION management_feature_head_guard()
REVOKE ALL ON FUNCTION public.management_feature_head_guard() FROM PUBLIC;

-- ACL: FUNCTION management_feature_lock(p_feature uuid, p_parent uuid, p_write boolean)
REVOKE ALL ON FUNCTION public.management_feature_lock(p_feature uuid, p_parent uuid, p_write boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_feature_lock(p_feature uuid, p_parent uuid, p_write boolean) TO bid_app;

-- ACL: FUNCTION management_feature_revision_guard()
REVOKE ALL ON FUNCTION public.management_feature_revision_guard() FROM PUBLIC;

-- ACL: FUNCTION management_feature_revision_search()
REVOKE ALL ON FUNCTION public.management_feature_revision_search() FROM PUBLIC;

-- ACL: FUNCTION management_feature_root_guard()
REVOKE ALL ON FUNCTION public.management_feature_root_guard() FROM PUBLIC;

-- ACL: FUNCTION management_feature_selection_guard()
REVOKE ALL ON FUNCTION public.management_feature_selection_guard() FROM PUBLIC;

-- ACL: FUNCTION management_feature_task_lock()
REVOKE ALL ON FUNCTION public.management_feature_task_lock() FROM PUBLIC;

-- ACL: FUNCTION management_feature_vector(p_data jsonb)
REVOKE ALL ON FUNCTION public.management_feature_vector(p_data jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_feature_vector(p_data jsonb) TO bid_app;

-- ACL: FUNCTION management_lifecycle_apply()
REVOKE ALL ON FUNCTION public.management_lifecycle_apply() FROM PUBLIC;

-- ACL: FUNCTION management_lifecycle_audit_guard()
REVOKE ALL ON FUNCTION public.management_lifecycle_audit_guard() FROM PUBLIC;

-- ACL: FUNCTION management_lifecycle_guard()
REVOKE ALL ON FUNCTION public.management_lifecycle_guard() FROM PUBLIC;

-- ACL: FUNCTION management_memory_initial_projection()
REVOKE ALL ON FUNCTION public.management_memory_initial_projection() FROM PUBLIC;

-- ACL: FUNCTION management_memory_vector(p_text text, p_content jsonb, p_tags text[])
REVOKE ALL ON FUNCTION public.management_memory_vector(p_text text, p_content jsonb, p_tags text[]) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_memory_vector(p_text text, p_content jsonb, p_tags text[]) TO bid_app;

-- ACL: FUNCTION management_product_human()
REVOKE ALL ON FUNCTION public.management_product_human() FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_product_human() TO bid_app;

-- ACL: FUNCTION management_product_revision_search()
REVOKE ALL ON FUNCTION public.management_product_revision_search() FROM PUBLIC;

-- ACL: FUNCTION management_product_root_guard()
REVOKE ALL ON FUNCTION public.management_product_root_guard() FROM PUBLIC;

-- ACL: FUNCTION management_product_selection_guard()
REVOKE ALL ON FUNCTION public.management_product_selection_guard() FROM PUBLIC;

-- ACL: FUNCTION management_product_vector(p_data jsonb)
REVOKE ALL ON FUNCTION public.management_product_vector(p_data jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_product_vector(p_data jsonb) TO bid_app;

-- ACL: FUNCTION management_profile_revision_search()
REVOKE ALL ON FUNCTION public.management_profile_revision_search() FROM PUBLIC;

-- ACL: FUNCTION management_profile_root_guard()
REVOKE ALL ON FUNCTION public.management_profile_root_guard() FROM PUBLIC;

-- ACL: FUNCTION management_profile_selection_guard()
REVOKE ALL ON FUNCTION public.management_profile_selection_guard() FROM PUBLIC;

-- ACL: FUNCTION management_profile_vector(p_data jsonb)
REVOKE ALL ON FUNCTION public.management_profile_vector(p_data jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_profile_vector(p_data jsonb) TO bid_app;

-- ACL: FUNCTION management_template_human()
REVOKE ALL ON FUNCTION public.management_template_human() FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_template_human() TO bid_app;

-- ACL: FUNCTION management_template_revision_search()
REVOKE ALL ON FUNCTION public.management_template_revision_search() FROM PUBLIC;

-- ACL: FUNCTION management_template_root_guard()
REVOKE ALL ON FUNCTION public.management_template_root_guard() FROM PUBLIC;

-- ACL: FUNCTION management_template_selection_guard()
REVOKE ALL ON FUNCTION public.management_template_selection_guard() FROM PUBLIC;

-- ACL: FUNCTION management_template_vector(p_data jsonb)
REVOKE ALL ON FUNCTION public.management_template_vector(p_data jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.management_template_vector(p_data jsonb) TO bid_app;

-- ACL: FUNCTION platform_clef_manage(p_action text, p_body jsonb)
REVOKE ALL ON FUNCTION public.platform_clef_manage(p_action text, p_body jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_clef_manage(p_action text, p_body jsonb) TO bid_app;

-- ACL: FUNCTION platform_clef_read()
REVOKE ALL ON FUNCTION public.platform_clef_read() FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_clef_read() TO bid_app;

-- ACL: FUNCTION platform_credential_cipher(p_id uuid, p_allow_disabled boolean)
REVOKE ALL ON FUNCTION public.platform_credential_cipher(p_id uuid, p_allow_disabled boolean) FROM PUBLIC;

-- ACL: FUNCTION platform_credential_manage(p_action text, p_body jsonb)
REVOKE ALL ON FUNCTION public.platform_credential_manage(p_action text, p_body jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_credential_manage(p_action text, p_body jsonb) TO bid_app;

-- ACL: FUNCTION platform_credential_readiness(p_target jsonb)
REVOKE ALL ON FUNCTION public.platform_credential_readiness(p_target jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_credential_readiness(p_target jsonb) TO bid_app;

-- ACL: FUNCTION platform_credential_resolve_human(p_id uuid, p_revision bigint, p_purpose text)
REVOKE ALL ON FUNCTION public.platform_credential_resolve_human(p_id uuid, p_revision bigint, p_purpose text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_credential_resolve_human(p_id uuid, p_revision bigint, p_purpose text) TO bid_app;

-- ACL: FUNCTION platform_credential_resolve_probe(p_probe uuid)
REVOKE ALL ON FUNCTION public.platform_credential_resolve_probe(p_probe uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_credential_resolve_probe(p_probe uuid) TO bid_app;

-- ACL: FUNCTION platform_credential_resolve_service(p_service text, p_id uuid)
REVOKE ALL ON FUNCTION public.platform_credential_resolve_service(p_service text, p_id uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_credential_resolve_service(p_service text, p_id uuid) TO bid_app;

-- ACL: FUNCTION platform_credential_rewrap(p_id uuid, p_old text, p_new text)
REVOKE ALL ON FUNCTION public.platform_credential_rewrap(p_id uuid, p_old text, p_new text) FROM PUBLIC;

-- ACL: FUNCTION platform_credential_row_gate()
REVOKE ALL ON FUNCTION public.platform_credential_row_gate() FROM PUBLIC;

-- ACL: FUNCTION platform_credential_validate(p jsonb)
REVOKE ALL ON FUNCTION public.platform_credential_validate(p jsonb) FROM PUBLIC;

-- ACL: FUNCTION platform_credential_view(p_id uuid)
REVOKE ALL ON FUNCTION public.platform_credential_view(p_id uuid) FROM PUBLIC;

-- ACL: FUNCTION platform_trust_anchor_add(p_label text, p_der bytea, p_fingerprint text, p_subject text, p_issuer text, p_before timestamp with time zone, p_after timestamp with time zone)
REVOKE ALL ON FUNCTION public.platform_trust_anchor_add(p_label text, p_der bytea, p_fingerprint text, p_subject text, p_issuer text, p_before timestamp with time zone, p_after timestamp with time zone) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_trust_anchor_add(p_label text, p_der bytea, p_fingerprint text, p_subject text, p_issuer text, p_before timestamp with time zone, p_after timestamp with time zone) TO bid_app;

-- ACL: FUNCTION platform_trust_anchor_disable(p_id uuid)
REVOKE ALL ON FUNCTION public.platform_trust_anchor_disable(p_id uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_trust_anchor_disable(p_id uuid) TO bid_app;

-- ACL: FUNCTION platform_trust_anchor_list()
REVOKE ALL ON FUNCTION public.platform_trust_anchor_list() FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_trust_anchor_list() TO bid_app;

-- ACL: FUNCTION platform_trust_anchor_snapshot()
REVOKE ALL ON FUNCTION public.platform_trust_anchor_snapshot() FROM PUBLIC;
GRANT ALL ON FUNCTION public.platform_trust_anchor_snapshot() TO bid_app;

-- ACL: FUNCTION produce_task_events()
REVOKE ALL ON FUNCTION public.produce_task_events() FROM PUBLIC;

-- ACL: FUNCTION provider_credential_rewrap(p_id uuid, p_old text, p_new text)
REVOKE ALL ON FUNCTION public.provider_credential_rewrap(p_id uuid, p_old text, p_new text) FROM PUBLIC;

-- ACL: FUNCTION requirement_archived_guard()
REVOKE ALL ON FUNCTION public.requirement_archived_guard() FROM PUBLIC;

-- ACL: FUNCTION requirement_canonical(value jsonb)
REVOKE ALL ON FUNCTION public.requirement_canonical(value jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_canonical(value jsonb) TO bid_app;

-- ACL: FUNCTION requirement_content(p_req uuid)
REVOKE ALL ON FUNCTION public.requirement_content(p_req uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_content(p_req uuid) TO bid_app;

-- ACL: FUNCTION requirement_current_pin(p_req uuid)
REVOKE ALL ON FUNCTION public.requirement_current_pin(p_req uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_current_pin(p_req uuid) TO bid_app;

-- ACL: FUNCTION requirement_digest(value jsonb)
REVOKE ALL ON FUNCTION public.requirement_digest(value jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_digest(value jsonb) TO bid_app;

-- ACL: FUNCTION requirement_draft_current(p_draft uuid)
REVOKE ALL ON FUNCTION public.requirement_draft_current(p_draft uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_draft_current(p_draft uuid) TO bid_app;

-- ACL: FUNCTION requirement_draft_gate()
REVOKE ALL ON FUNCTION public.requirement_draft_gate() FROM PUBLIC;

-- ACL: FUNCTION requirement_event_pointer()
REVOKE ALL ON FUNCTION public.requirement_event_pointer() FROM PUBLIC;

-- ACL: FUNCTION requirement_hash(p_req uuid, p_pin jsonb)
REVOKE ALL ON FUNCTION public.requirement_hash(p_req uuid, p_pin jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_hash(p_req uuid, p_pin jsonb) TO bid_app;

-- ACL: FUNCTION requirement_human_authority(p_task uuid, p_scope text)
REVOKE ALL ON FUNCTION public.requirement_human_authority(p_task uuid, p_scope text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_human_authority(p_task uuid, p_scope text) TO bid_app;

-- ACL: FUNCTION requirement_invalidation_audit()
REVOKE ALL ON FUNCTION public.requirement_invalidation_audit() FROM PUBLIC;

-- ACL: FUNCTION requirement_manual_job_guard()
REVOKE ALL ON FUNCTION public.requirement_manual_job_guard() FROM PUBLIC;

-- ACL: FUNCTION requirement_pin_valid(p_req uuid, p_pin jsonb)
REVOKE ALL ON FUNCTION public.requirement_pin_valid(p_req uuid, p_pin jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_pin_valid(p_req uuid, p_pin jsonb) TO bid_app;

-- ACL: FUNCTION requirement_response_gate()
REVOKE ALL ON FUNCTION public.requirement_response_gate() FROM PUBLIC;

-- ACL: FUNCTION requirement_review_changed()
REVOKE ALL ON FUNCTION public.requirement_review_changed() FROM PUBLIC;

-- ACL: FUNCTION requirement_review_current(p_req uuid)
REVOKE ALL ON FUNCTION public.requirement_review_current(p_req uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_review_current(p_req uuid) TO bid_app;

-- ACL: FUNCTION requirement_review_fields(p_req uuid)
REVOKE ALL ON FUNCTION public.requirement_review_fields(p_req uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_review_fields(p_req uuid) TO bid_app;

-- ACL: FUNCTION requirement_review_guard()
REVOKE ALL ON FUNCTION public.requirement_review_guard() FROM PUBLIC;

-- ACL: FUNCTION requirement_review_state(p_req uuid)
REVOKE ALL ON FUNCTION public.requirement_review_state(p_req uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_review_state(p_req uuid) TO bid_app;

-- ACL: FUNCTION requirement_rubric_confirmed(p_rubric uuid)
REVOKE ALL ON FUNCTION public.requirement_rubric_confirmed(p_rubric uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_rubric_confirmed(p_rubric uuid) TO bid_app;

-- ACL: FUNCTION requirement_rubric_preparation_current(p_rubric uuid)
REVOKE ALL ON FUNCTION public.requirement_rubric_preparation_current(p_rubric uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_rubric_preparation_current(p_rubric uuid) TO bid_app;

-- ACL: FUNCTION requirement_seed_authority(p_task uuid, p_job uuid, p_manual boolean)
REVOKE ALL ON FUNCTION public.requirement_seed_authority(p_task uuid, p_job uuid, p_manual boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION public.requirement_seed_authority(p_task uuid, p_job uuid, p_manual boolean) TO bid_app;

-- ACL: FUNCTION requirement_source_invalidate()
REVOKE ALL ON FUNCTION public.requirement_source_invalidate() FROM PUBLIC;

-- ACL: FUNCTION requirement_source_lock()
REVOKE ALL ON FUNCTION public.requirement_source_lock() FROM PUBLIC;

-- ACL: FUNCTION rubric_candidate_range_valid(bounds jsonb)
REVOKE ALL ON FUNCTION public.rubric_candidate_range_valid(bounds jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_candidate_range_valid(bounds jsonb) TO bid_app;

-- ACL: FUNCTION rubric_complete(p_rubric uuid)
REVOKE ALL ON FUNCTION public.rubric_complete(p_rubric uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_complete(p_rubric uuid) TO bid_app;

-- ACL: FUNCTION rubric_current_inputs(p_rubric uuid)
REVOKE ALL ON FUNCTION public.rubric_current_inputs(p_rubric uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_current_inputs(p_rubric uuid) TO bid_app;

-- ACL: FUNCTION rubric_section_bindings_current(p_rubric uuid)
REVOKE ALL ON FUNCTION public.rubric_section_bindings_current(p_rubric uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_section_bindings_current(p_rubric uuid) TO bid_app;

-- ACL: FUNCTION rubric_section_source_bindings_valid(p_rubric uuid, p_sources jsonb)
REVOKE ALL ON FUNCTION public.rubric_section_source_bindings_valid(p_rubric uuid, p_sources jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_section_source_bindings_valid(p_rubric uuid, p_sources jsonb) TO bid_app;

-- ACL: TABLE score_rubric_sections
GRANT SELECT,INSERT ON TABLE public.score_rubric_sections TO bid_app;

-- ACL: FUNCTION rubric_section_sources(section_row score_rubric_sections)
REVOKE ALL ON FUNCTION public.rubric_section_sources(section_row score_rubric_sections) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_section_sources(section_row score_rubric_sections) TO bid_app;

-- ACL: FUNCTION rubric_section_sources_gate()
REVOKE ALL ON FUNCTION public.rubric_section_sources_gate() FROM PUBLIC;

-- ACL: FUNCTION rubric_section_sources_valid(p_rubric uuid, p_sources jsonb)
REVOKE ALL ON FUNCTION public.rubric_section_sources_valid(p_rubric uuid, p_sources jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_section_sources_valid(p_rubric uuid, p_sources jsonb) TO bid_app;

-- ACL: FUNCTION rubric_subject_values_gate()
REVOKE ALL ON FUNCTION public.rubric_subject_values_gate() FROM PUBLIC;

-- ACL: FUNCTION rubric_subject_values_valid(p_rubric uuid, p_section uuid, p_item uuid)
REVOKE ALL ON FUNCTION public.rubric_subject_values_valid(p_rubric uuid, p_section uuid, p_item uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.rubric_subject_values_valid(p_rubric uuid, p_section uuid, p_item uuid) TO bid_app;

-- ACL: FUNCTION score_inputs_current(p_draft uuid, p_rubric uuid, p_revision integer)
REVOKE ALL ON FUNCTION public.score_inputs_current(p_draft uuid, p_rubric uuid, p_revision integer) FROM PUBLIC;
GRANT ALL ON FUNCTION public.score_inputs_current(p_draft uuid, p_rubric uuid, p_revision integer) TO bid_app;

-- ACL: FUNCTION task_archived_guard()
REVOKE ALL ON FUNCTION public.task_archived_guard() FROM PUBLIC;

-- ACL: FUNCTION task_events_immutable()
REVOKE ALL ON FUNCTION public.task_events_immutable() FROM PUBLIC;

-- ACL: FUNCTION task_human_content_guard()
REVOKE ALL ON FUNCTION public.task_human_content_guard() FROM PUBLIC;

-- ACL: FUNCTION task_job_lifecycle_guard()
REVOKE ALL ON FUNCTION public.task_job_lifecycle_guard() FROM PUBLIC;

-- ACL: FUNCTION task_write_authority(p_task uuid, p_scope text, p_domain text, p_decision boolean, p_human boolean)
REVOKE ALL ON FUNCTION public.task_write_authority(p_task uuid, p_scope text, p_domain text, p_decision boolean, p_human boolean) FROM PUBLIC;
GRANT ALL ON FUNCTION public.task_write_authority(p_task uuid, p_scope text, p_domain text, p_decision boolean, p_human boolean) TO bid_app;

-- ACL: FUNCTION usage_admission_guard()
REVOKE ALL ON FUNCTION public.usage_admission_guard() FROM PUBLIC;

-- ACL: FUNCTION vendor_call_guard()
REVOKE ALL ON FUNCTION public.vendor_call_guard() FROM PUBLIC;

-- ACL: FUNCTION vendor_call_usage_consistency()
REVOKE ALL ON FUNCTION public.vendor_call_usage_consistency() FROM PUBLIC;

-- ACL: TABLE agent_job_links
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_job_links TO bid_app;

-- ACL: TABLE agent_messages
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_messages TO bid_app;

-- ACL: TABLE agent_pauses
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_pauses TO bid_app;

-- ACL: TABLE agent_principals
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_principals TO bid_app;

-- ACL: TABLE agent_sessions
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_sessions TO bid_app;

-- ACL: TABLE agent_steps
GRANT SELECT,INSERT,UPDATE ON TABLE public.agent_steps TO bid_app;

-- ACL: TABLE annotation_materials
GRANT SELECT,INSERT ON TABLE public.annotation_materials TO bid_app;

-- ACL: COLUMN annotation_materials.id
GRANT UPDATE(id) ON TABLE public.annotation_materials TO bid_app;

-- ACL: TABLE annotation_releases
GRANT SELECT,INSERT ON TABLE public.annotation_releases TO bid_app;

-- ACL: COLUMN annotation_releases.id
GRANT UPDATE(id) ON TABLE public.annotation_releases TO bid_app;

-- ACL: TABLE annotation_requests
GRANT SELECT,INSERT ON TABLE public.annotation_requests TO bid_app;

-- ACL: COLUMN annotation_requests.id
GRANT UPDATE(id) ON TABLE public.annotation_requests TO bid_app;

-- ACL: TABLE api_tokens
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.api_tokens TO bid_app;

-- ACL: TABLE attachment_archives
GRANT SELECT,INSERT,UPDATE ON TABLE public.attachment_archives TO bid_app;

-- ACL: TABLE attachment_file_parts
GRANT SELECT,INSERT ON TABLE public.attachment_file_parts TO bid_app;

-- ACL: TABLE attachment_files
GRANT SELECT,INSERT ON TABLE public.attachment_files TO bid_app;

-- ACL: TABLE attachment_privacy_holds
GRANT SELECT,INSERT ON TABLE public.attachment_privacy_holds TO bid_app;

-- ACL: TABLE attachment_reviews
GRANT SELECT,INSERT ON TABLE public.attachment_reviews TO bid_app;

-- ACL: TABLE attachment_revisions
GRANT SELECT,INSERT ON TABLE public.attachment_revisions TO bid_app;

-- ACL: TABLE audit_logs
GRANT SELECT,INSERT ON TABLE public.audit_logs TO bid_app;

-- ACL: TABLE bid_document_pages
GRANT SELECT,INSERT ON TABLE public.bid_document_pages TO bid_app;

-- ACL: TABLE bid_outbound_authorizations
GRANT SELECT,INSERT ON TABLE public.bid_outbound_authorizations TO bid_app;

-- ACL: TABLE bid_outbound_authorized_pages
GRANT SELECT,INSERT ON TABLE public.bid_outbound_authorized_pages TO bid_app;

-- ACL: TABLE bid_pdf_validations
GRANT SELECT,INSERT ON TABLE public.bid_pdf_validations TO bid_app;

-- ACL: TABLE bid_preparation_publications
GRANT SELECT,INSERT ON TABLE public.bid_preparation_publications TO bid_app;

-- ACL: TABLE bid_preparation_trust
GRANT SELECT,INSERT ON TABLE public.bid_preparation_trust TO bid_app;

-- ACL: TABLE bid_preparations
GRANT SELECT,INSERT ON TABLE public.bid_preparations TO bid_app;

-- ACL: TABLE bid_prepared_documents
GRANT SELECT,INSERT ON TABLE public.bid_prepared_documents TO bid_app;

-- ACL: TABLE bid_presence_authorizations
GRANT SELECT,INSERT ON TABLE public.bid_presence_authorizations TO bid_app;

-- ACL: TABLE bid_presence_authorized_images
GRANT SELECT,INSERT ON TABLE public.bid_presence_authorized_images TO bid_app;

-- ACL: TABLE bid_presence_images
GRANT SELECT,INSERT ON TABLE public.bid_presence_images TO bid_app;

-- ACL: TABLE bid_presence_preparations
GRANT SELECT,INSERT ON TABLE public.bid_presence_preparations TO bid_app;

-- ACL: TABLE bid_redacted_pages
GRANT SELECT,INSERT ON TABLE public.bid_redacted_pages TO bid_app;

-- ACL: TABLE bid_redaction_snapshots
GRANT SELECT,INSERT ON TABLE public.bid_redaction_snapshots TO bid_app;

-- ACL: TABLE bid_review_finding_events
GRANT SELECT,INSERT ON TABLE public.bid_review_finding_events TO bid_app;

-- ACL: TABLE bid_review_finding_sources
GRANT SELECT,INSERT ON TABLE public.bid_review_finding_sources TO bid_app;

-- ACL: TABLE bid_review_findings
GRANT SELECT,INSERT ON TABLE public.bid_review_findings TO bid_app;

-- ACL: TABLE bid_review_name_lists
GRANT SELECT,INSERT ON TABLE public.bid_review_name_lists TO bid_app;

-- ACL: TABLE bid_review_obligations
GRANT SELECT,INSERT ON TABLE public.bid_review_obligations TO bid_app;

-- ACL: TABLE bid_review_publications
GRANT SELECT,INSERT ON TABLE public.bid_review_publications TO bid_app;

-- ACL: TABLE bid_review_report_artifacts
GRANT SELECT,INSERT ON TABLE public.bid_review_report_artifacts TO bid_app;

-- ACL: TABLE bid_review_report_snapshots
GRANT SELECT,INSERT ON TABLE public.bid_review_report_snapshots TO bid_app;

-- ACL: TABLE bid_review_required_locations
GRANT SELECT,INSERT ON TABLE public.bid_review_required_locations TO bid_app;

-- ACL: TABLE bid_review_runs
GRANT SELECT,INSERT ON TABLE public.bid_review_runs TO bid_app;

-- ACL: TABLE bid_review_signing_requirements
GRANT SELECT,INSERT ON TABLE public.bid_review_signing_requirements TO bid_app;

-- ACL: TABLE bid_signing_candidates
GRANT SELECT,INSERT ON TABLE public.bid_signing_candidates TO bid_app;

-- ACL: TABLE bid_submission_documents
GRANT SELECT,INSERT ON TABLE public.bid_submission_documents TO bid_app;

-- ACL: TABLE bid_submissions
GRANT SELECT,INSERT,UPDATE ON TABLE public.bid_submissions TO bid_app;

-- ACL: TABLE card_evidence_links
GRANT SELECT,INSERT ON TABLE public.card_evidence_links TO bid_app;

-- ACL: TABLE card_generation_runs
GRANT SELECT,INSERT ON TABLE public.card_generation_runs TO bid_app;

-- ACL: TABLE certificate_file_parts
GRANT SELECT,INSERT ON TABLE public.certificate_file_parts TO bid_app;

-- ACL: TABLE certificate_files
GRANT SELECT,INSERT ON TABLE public.certificate_files TO bid_app;

-- ACL: TABLE certificate_revisions
GRANT SELECT,INSERT ON TABLE public.certificate_revisions TO bid_app;

-- ACL: TABLE certificates
GRANT SELECT,INSERT,UPDATE ON TABLE public.certificates TO bid_app;

-- ACL: COLUMN certificates.id
GRANT UPDATE(id) ON TABLE public.certificates TO bid_app;

-- ACL: COLUMN certificates.lifecycle_state
GRANT UPDATE(lifecycle_state) ON TABLE public.certificates TO bid_app;

-- ACL: COLUMN certificates.lifecycle_revision
GRANT UPDATE(lifecycle_revision) ON TABLE public.certificates TO bid_app;

-- ACL: COLUMN certificates.search_vector
GRANT UPDATE(search_vector) ON TABLE public.certificates TO bid_app;

-- ACL: TABLE check_certificate_items
GRANT SELECT,INSERT ON TABLE public.check_certificate_items TO bid_app;

-- ACL: TABLE check_certificates
GRANT SELECT,INSERT ON TABLE public.check_certificates TO bid_app;

-- ACL: TABLE check_decisions
GRANT SELECT,INSERT ON TABLE public.check_decisions TO bid_app;

-- ACL: TABLE check_finding_citations
GRANT SELECT,INSERT ON TABLE public.check_finding_citations TO bid_app;

-- ACL: TABLE check_findings
GRANT SELECT,INSERT ON TABLE public.check_findings TO bid_app;

-- ACL: COLUMN check_findings.id
GRANT UPDATE(id) ON TABLE public.check_findings TO bid_app;

-- ACL: TABLE check_items
GRANT SELECT,INSERT ON TABLE public.check_items TO bid_app;

-- ACL: TABLE check_runs
GRANT SELECT,INSERT ON TABLE public.check_runs TO bid_app;

-- ACL: TABLE chunks
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.chunks TO bid_app;

-- ACL: TABLE confidential_field_search_tokens
GRANT SELECT,INSERT,DELETE ON TABLE public.confidential_field_search_tokens TO bid_app;

-- ACL: TABLE confidential_fields
GRANT SELECT,INSERT ON TABLE public.confidential_fields TO bid_app;

-- ACL: COLUMN confidential_fields.label
GRANT UPDATE(label) ON TABLE public.confidential_fields TO bid_app;

-- ACL: COLUMN confidential_fields.archived
GRANT UPDATE(archived) ON TABLE public.confidential_fields TO bid_app;

-- ACL: COLUMN confidential_fields.revision
GRANT UPDATE(revision) ON TABLE public.confidential_fields TO bid_app;

-- ACL: TABLE confidential_values
GRANT SELECT,INSERT ON TABLE public.confidential_values TO bid_app;

-- ACL: TABLE documents
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.documents TO bid_app;

-- ACL: TABLE draft_runs
GRANT SELECT,INSERT ON TABLE public.draft_runs TO bid_app;

-- ACL: TABLE evidence
GRANT SELECT,INSERT ON TABLE public.evidence TO bid_app;

-- ACL: COLUMN evidence.quote_check
GRANT UPDATE(quote_check) ON TABLE public.evidence TO bid_app;

-- ACL: COLUMN evidence.confirmed_at
GRANT UPDATE(confirmed_at) ON TABLE public.evidence TO bid_app;

-- ACL: TABLE evidence_sources
GRANT SELECT,INSERT,UPDATE ON TABLE public.evidence_sources TO bid_app;

-- ACL: TABLE export_render_candidates
GRANT SELECT,INSERT ON TABLE public.export_render_candidates TO bid_app;

-- ACL: TABLE export_run_evidence
GRANT SELECT,INSERT ON TABLE public.export_run_evidence TO bid_app;

-- ACL: TABLE export_run_items
GRANT SELECT,INSERT ON TABLE public.export_run_items TO bid_app;

-- ACL: TABLE export_runs
GRANT SELECT,INSERT ON TABLE public.export_runs TO bid_app;

-- ACL: TABLE export_template_bindings
GRANT SELECT,INSERT ON TABLE public.export_template_bindings TO bid_app;

-- ACL: TABLE exports
GRANT SELECT,INSERT ON TABLE public.exports TO bid_app;

-- ACL: TABLE feature_revisions
GRANT SELECT,INSERT ON TABLE public.feature_revisions TO bid_app;

-- ACL: TABLE features
GRANT SELECT,INSERT,UPDATE ON TABLE public.features TO bid_app;

-- ACL: COLUMN features.id
GRANT UPDATE(id) ON TABLE public.features TO bid_app;

-- ACL: TABLE jobs
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.jobs TO bid_app;

-- ACL: TABLE memories
GRANT SELECT,INSERT,UPDATE ON TABLE public.memories TO bid_app;

-- ACL: TABLE memory_call_inputs
GRANT SELECT,INSERT,UPDATE ON TABLE public.memory_call_inputs TO bid_app;

-- ACL: TABLE memory_eval_samples
GRANT SELECT,INSERT,UPDATE ON TABLE public.memory_eval_samples TO bid_app;

-- ACL: TABLE memory_feedback_events
GRANT SELECT,INSERT ON TABLE public.memory_feedback_events TO bid_app;

-- ACL: TABLE memory_retrieval_items
GRANT SELECT,INSERT ON TABLE public.memory_retrieval_items TO bid_app;

-- ACL: TABLE memory_retrievals
GRANT SELECT,INSERT ON TABLE public.memory_retrievals TO bid_app;

-- ACL: TABLE memory_revisions
GRANT SELECT,INSERT ON TABLE public.memory_revisions TO bid_app;

-- ACL: TABLE memory_scope_epochs
GRANT SELECT,INSERT,UPDATE ON TABLE public.memory_scope_epochs TO bid_app;

-- ACL: TABLE org_profile_revisions
GRANT SELECT,INSERT ON TABLE public.org_profile_revisions TO bid_app;

-- ACL: TABLE org_profiles
GRANT SELECT,INSERT ON TABLE public.org_profiles TO bid_app;

-- ACL: COLUMN org_profiles.id
GRANT UPDATE(id) ON TABLE public.org_profiles TO bid_app;

-- ACL: COLUMN org_profiles.current_revision
GRANT UPDATE(current_revision) ON TABLE public.org_profiles TO bid_app;

-- ACL: COLUMN org_profiles.lifecycle_state
GRANT UPDATE(lifecycle_state) ON TABLE public.org_profiles TO bid_app;

-- ACL: COLUMN org_profiles.lifecycle_revision
GRANT UPDATE(lifecycle_revision) ON TABLE public.org_profiles TO bid_app;

-- ACL: COLUMN org_profiles.search_vector
GRANT UPDATE(search_vector) ON TABLE public.org_profiles TO bid_app;

-- ACL: TABLE product_revisions
GRANT SELECT,INSERT ON TABLE public.product_revisions TO bid_app;

-- ACL: TABLE products
GRANT SELECT,INSERT,UPDATE ON TABLE public.products TO bid_app;

-- ACL: COLUMN products.id
GRANT UPDATE(id) ON TABLE public.products TO bid_app;

-- ACL: TABLE profile_attachment_links
GRANT SELECT,INSERT,UPDATE ON TABLE public.profile_attachment_links TO bid_app;

-- ACL: TABLE prototype_decision_batches
GRANT SELECT,INSERT ON TABLE public.prototype_decision_batches TO bid_app;

-- ACL: TABLE prototype_evidence_decisions
GRANT SELECT,INSERT ON TABLE public.prototype_evidence_decisions TO bid_app;

-- ACL: TABLE provider_configs
GRANT SELECT,INSERT ON TABLE public.provider_configs TO bid_app;

-- ACL: TABLE requirement_review_events
GRANT SELECT,INSERT ON TABLE public.requirement_review_events TO bid_app;

-- ACL: TABLE requirement_review_requests
GRANT SELECT,INSERT ON TABLE public.requirement_review_requests TO bid_app;

-- ACL: TABLE requirement_review_sets
GRANT SELECT,INSERT,UPDATE ON TABLE public.requirement_review_sets TO bid_app;

-- ACL: TABLE requirement_reviews
GRANT SELECT,INSERT,UPDATE ON TABLE public.requirement_reviews TO bid_app;

-- ACL: TABLE requirements
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.requirements TO bid_app;

-- ACL: TABLE resource_lifecycle_events
GRANT SELECT,INSERT ON TABLE public.resource_lifecycle_events TO bid_app;

-- ACL: TABLE response_card_revisions
GRANT SELECT,INSERT ON TABLE public.response_card_revisions TO bid_app;

-- ACL: TABLE response_cards
GRANT SELECT,INSERT ON TABLE public.response_cards TO bid_app;

-- ACL: COLUMN response_cards.current_revision_id
GRANT UPDATE(current_revision_id) ON TABLE public.response_cards TO bid_app;

-- ACL: COLUMN response_cards.revision
GRANT UPDATE(revision) ON TABLE public.response_cards TO bid_app;

-- ACL: TABLE response_items
GRANT SELECT,INSERT ON TABLE public.response_items TO bid_app;

-- ACL: TABLE sandbox_artifacts
GRANT SELECT,INSERT ON TABLE public.sandbox_artifacts TO bid_app;

-- ACL: TABLE sandbox_attempts
GRANT SELECT,INSERT ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.ended_at
GRANT UPDATE(ended_at) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.termination_code
GRANT UPDATE(termination_code) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.cleanup_state
GRANT UPDATE(cleanup_state) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.metrics
GRANT UPDATE(metrics) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.runtime_versions
GRANT UPDATE(runtime_versions) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: COLUMN sandbox_attempts.issues
GRANT UPDATE(issues) ON TABLE public.sandbox_attempts TO bid_app;

-- ACL: TABLE sandbox_fetch_receipts
GRANT SELECT,INSERT ON TABLE public.sandbox_fetch_receipts TO bid_app;

-- ACL: TABLE sandbox_inputs
GRANT SELECT,INSERT ON TABLE public.sandbox_inputs TO bid_app;

-- ACL: TABLE sandbox_runs
GRANT SELECT,INSERT ON TABLE public.sandbox_runs TO bid_app;

-- ACL: TABLE score_item_citations
GRANT SELECT,INSERT ON TABLE public.score_item_citations TO bid_app;

-- ACL: TABLE score_report_item_responses
GRANT SELECT,INSERT ON TABLE public.score_report_item_responses TO bid_app;

-- ACL: TABLE score_report_items
GRANT SELECT,INSERT ON TABLE public.score_report_items TO bid_app;

-- ACL: TABLE score_reports
GRANT SELECT,INSERT ON TABLE public.score_reports TO bid_app;

-- ACL: TABLE score_rubric_classifications
GRANT SELECT,INSERT ON TABLE public.score_rubric_classifications TO bid_app;

-- ACL: TABLE score_rubric_coverage
GRANT SELECT,INSERT ON TABLE public.score_rubric_coverage TO bid_app;

-- ACL: TABLE score_rubric_coverage_decisions
GRANT SELECT,INSERT ON TABLE public.score_rubric_coverage_decisions TO bid_app;

-- ACL: TABLE score_rubric_coverage_items
GRANT SELECT,INSERT ON TABLE public.score_rubric_coverage_items TO bid_app;

-- ACL: TABLE score_rubric_decisions
GRANT SELECT,INSERT ON TABLE public.score_rubric_decisions TO bid_app;

-- ACL: TABLE score_rubric_items
GRANT SELECT,INSERT ON TABLE public.score_rubric_items TO bid_app;

-- ACL: TABLE score_rubric_revision_events
GRANT SELECT,INSERT ON TABLE public.score_rubric_revision_events TO bid_app;

-- ACL: TABLE score_rubric_sets
GRANT SELECT,INSERT ON TABLE public.score_rubric_sets TO bid_app;

-- ACL: COLUMN score_rubric_sets.id
GRANT UPDATE(id) ON TABLE public.score_rubric_sets TO bid_app;

-- ACL: TABLE screenshot_analysis_inputs
GRANT SELECT,INSERT ON TABLE public.screenshot_analysis_inputs TO bid_app;

-- ACL: TABLE screenshot_analysis_runs
GRANT SELECT,INSERT ON TABLE public.screenshot_analysis_runs TO bid_app;

-- ACL: TABLE screenshot_assets
GRANT SELECT,INSERT ON TABLE public.screenshot_assets TO bid_app;

-- ACL: TABLE screenshot_privacy_reviews
GRANT SELECT,INSERT ON TABLE public.screenshot_privacy_reviews TO bid_app;

-- ACL: TABLE screenshot_prototype_runs
GRANT SELECT,INSERT ON TABLE public.screenshot_prototype_runs TO bid_app;

-- ACL: TABLE screenshot_renditions
GRANT SELECT,INSERT ON TABLE public.screenshot_renditions TO bid_app;

-- ACL: TABLE screenshot_search_candidates
GRANT SELECT,INSERT ON TABLE public.screenshot_search_candidates TO bid_app;

-- ACL: TABLE screenshot_search_runs
GRANT SELECT,INSERT ON TABLE public.screenshot_search_runs TO bid_app;

-- ACL: TABLE screenshot_suggestions
GRANT SELECT,INSERT ON TABLE public.screenshot_suggestions TO bid_app;

-- ACL: TABLE screenshot_vendor_archives
GRANT SELECT,INSERT ON TABLE public.screenshot_vendor_archives TO bid_app;

-- ACL: TABLE screenshot_withdrawals
GRANT SELECT,INSERT ON TABLE public.screenshot_withdrawals TO bid_app;

-- ACL: TABLE simulated_resources
GRANT SELECT,INSERT ON TABLE public.simulated_resources TO bid_app;

-- ACL: TABLE task_attachments
GRANT SELECT,INSERT,UPDATE ON TABLE public.task_attachments TO bid_app;

-- ACL: TABLE task_certificates
GRANT SELECT,INSERT ON TABLE public.task_certificates TO bid_app;

-- ACL: COLUMN task_certificates.active
GRANT UPDATE(active) ON TABLE public.task_certificates TO bid_app;

-- ACL: TABLE task_event_heads
GRANT SELECT,INSERT ON TABLE public.task_event_heads TO bid_app;

-- ACL: TABLE task_events
GRANT SELECT ON TABLE public.task_events TO bid_app;

-- ACL: TABLE task_features
GRANT SELECT,INSERT ON TABLE public.task_features TO bid_app;

-- ACL: COLUMN task_features.active
GRANT UPDATE(active) ON TABLE public.task_features TO bid_app;

-- ACL: TABLE task_org_profiles
GRANT SELECT,INSERT ON TABLE public.task_org_profiles TO bid_app;

-- ACL: COLUMN task_org_profiles.active
GRANT UPDATE(active) ON TABLE public.task_org_profiles TO bid_app;

-- ACL: TABLE task_resources
GRANT SELECT,INSERT ON TABLE public.task_resources TO bid_app;

-- ACL: COLUMN task_resources.active
GRANT UPDATE(active) ON TABLE public.task_resources TO bid_app;

-- ACL: TABLE task_templates
GRANT SELECT,INSERT ON TABLE public.task_templates TO bid_app;

-- ACL: COLUMN task_templates.active
GRANT UPDATE(active) ON TABLE public.task_templates TO bid_app;

-- ACL: TABLE tasks
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.tasks TO bid_app;

-- ACL: TABLE template_revisions
GRANT SELECT,INSERT ON TABLE public.template_revisions TO bid_app;

-- ACL: TABLE templates
GRANT SELECT,INSERT ON TABLE public.templates TO bid_app;

-- ACL: COLUMN templates.id
GRANT UPDATE(id) ON TABLE public.templates TO bid_app;

-- ACL: COLUMN templates.current_revision
GRANT UPDATE(current_revision) ON TABLE public.templates TO bid_app;

-- ACL: COLUMN templates.lifecycle_state
GRANT UPDATE(lifecycle_state) ON TABLE public.templates TO bid_app;

-- ACL: COLUMN templates.lifecycle_revision
GRANT UPDATE(lifecycle_revision) ON TABLE public.templates TO bid_app;

-- ACL: COLUMN templates.search_vector
GRANT UPDATE(search_vector) ON TABLE public.templates TO bid_app;

-- ACL: TABLE usage_records
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.usage_records TO bid_app;

-- ACL: TABLE vendor_calls
GRANT SELECT,INSERT,UPDATE ON TABLE public.vendor_calls TO bid_app;


--
-- PostgreSQL database dump complete
--

