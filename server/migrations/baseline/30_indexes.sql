-- INDEX: agent_one_pending_pause
CREATE UNIQUE INDEX agent_one_pending_pause ON public.agent_pauses USING btree (session_id) WHERE ((status)::text = 'pending'::text);

-- INDEX: agent_owned_job
CREATE UNIQUE INDEX agent_owned_job ON public.agent_job_links USING btree (job_id) WHERE owned;

-- INDEX: annotation_invalidation_once
CREATE UNIQUE INDEX annotation_invalidation_once ON public.audit_logs USING btree (object_id) WHERE ((action)::text = 'annotation.invalidate'::text);

-- INDEX: annotation_materials_listing
CREATE INDEX annotation_materials_listing ON public.annotation_materials USING btree (task_id, created_at, id);

-- INDEX: annotation_releases_listing
CREATE INDEX annotation_releases_listing ON public.annotation_releases USING btree (task_id, created_at, id);

-- INDEX: annotation_releases_material_history
CREATE INDEX annotation_releases_material_history ON public.annotation_releases USING btree (annotation_id, created_at, id);

-- INDEX: annotation_requests_listing
CREATE INDEX annotation_requests_listing ON public.annotation_requests USING btree (task_id, created_at, id);

-- INDEX: attachment_archive_page
CREATE INDEX attachment_archive_page ON public.attachment_archives USING btree (created_at, id);

-- INDEX: attachment_asset_latest
CREATE INDEX attachment_asset_latest ON public.screenshot_assets USING btree (evidence_source_id, received_at DESC, id DESC) WHERE ((source_kind)::text = 'attachment_page'::text);

-- INDEX: attachment_audit_request
CREATE UNIQUE INDEX attachment_audit_request ON public.audit_logs USING btree (action, ((details ->> 'request_id'::text))) WHERE (((action)::text = ANY ((ARRAY['attachment.create'::character varying, 'attachment.revise'::character varying, 'attachment.assign'::character varying, 'attachment.review.approve'::character varying, 'attachment.review.reject'::character varying, 'attachment.review.revoke'::character varying, 'attachment.deactivate'::character varying, 'profile.attachment.link'::character varying, 'profile.attachment.deactivate'::character varying, 'task.attachment.select'::character varying, 'task.attachment.deactivate'::character varying, 'attachment.source.create'::character varying, 'attachment.privacy.clear'::character varying, 'attachment.privacy.hold'::character varying, 'attachment.privacy.withdraw'::character varying])::text[])) AND ((details ->> 'request_id'::text) IS NOT NULL));

-- INDEX: attachment_audit_revision
CREATE INDEX attachment_audit_revision ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['attachment.create'::character varying, 'attachment.revise'::character varying])::text[]));

-- INDEX: attachment_hold_latest
CREATE INDEX attachment_hold_latest ON public.attachment_privacy_holds USING btree (evidence_source_id, reviewed_at DESC, id DESC);

-- INDEX: attachment_hold_page
CREATE INDEX attachment_hold_page ON public.attachment_privacy_holds USING btree (evidence_source_id, created_at, id);

-- INDEX: attachment_hold_prior
CREATE UNIQUE INDEX attachment_hold_prior ON public.attachment_privacy_holds USING btree (evidence_source_id, prior_hold_id) NULLS NOT DISTINCT;

-- INDEX: attachment_link_active_slot
CREATE UNIQUE INDEX attachment_link_active_slot ON public.profile_attachment_links USING btree (profile_revision_id, field, attachment_revision_id) WHERE active;

-- INDEX: attachment_link_page
CREATE INDEX attachment_link_page ON public.profile_attachment_links USING btree (profile_revision_id, created_at, id);

-- INDEX: attachment_review_latest
CREATE INDEX attachment_review_latest ON public.attachment_reviews USING btree (attachment_revision_id, reviewed_at DESC, id DESC);

-- INDEX: attachment_review_page
CREATE INDEX attachment_review_page ON public.attachment_reviews USING btree (attachment_revision_id, created_at, id);

-- INDEX: attachment_review_prior
CREATE UNIQUE INDEX attachment_review_prior ON public.attachment_reviews USING btree (attachment_revision_id, prior_review_id) NULLS NOT DISTINCT;

-- INDEX: attachment_revision_page
CREATE INDEX attachment_revision_page ON public.attachment_revisions USING btree (attachment_id, created_at, id);

-- INDEX: attachment_selection_active_slot
CREATE UNIQUE INDEX attachment_selection_active_slot ON public.task_attachments USING btree (task_id, task_org_profile_id, attachment_id) WHERE active;

-- INDEX: attachment_selection_archive_tasks
CREATE INDEX attachment_selection_archive_tasks ON public.task_attachments USING btree (attachment_id, task_id);

-- INDEX: attachment_selection_link_tasks
CREATE INDEX attachment_selection_link_tasks ON public.task_attachments USING btree (profile_attachment_link_id, task_id);

-- INDEX: attachment_selection_page
CREATE INDEX attachment_selection_page ON public.task_attachments USING btree (task_id, created_at, id);

-- INDEX: attachment_source_identity
CREATE UNIQUE INDEX attachment_source_identity ON public.evidence_sources USING btree (task_attachment_id, page, render_profile) WHERE ((source_kind)::text = 'user_supplied_attachment_pdf'::text);

-- INDEX: attachment_source_page
CREATE INDEX attachment_source_page ON public.evidence_sources USING btree (task_id, created_at, id) WHERE ((source_kind)::text = 'user_supplied_attachment_pdf'::text);

-- INDEX: audit_logs_agent_session
CREATE INDEX audit_logs_agent_session ON public.audit_logs USING btree (agent_session_id);

-- INDEX: bid_document_page_inventory
CREATE INDEX bid_document_page_inventory ON public.bid_document_pages USING btree (submission_id, document_id, page);

-- INDEX: bid_preparation_submission
CREATE INDEX bid_preparation_submission ON public.bid_preparations USING btree (submission_id, created_at, id);

-- INDEX: bid_review_absence_page_unique
CREATE UNIQUE INDEX bid_review_absence_page_unique ON public.bid_review_finding_sources USING btree (finding_id, page_id) WHERE ((kind)::text = 'absence_page'::text);

-- INDEX: bid_review_findings_page
CREATE INDEX bid_review_findings_page ON public.bid_review_findings USING btree (review_id, ordinal);

-- INDEX: bid_review_inventory_source_unique
CREATE UNIQUE INDEX bid_review_inventory_source_unique ON public.bid_review_finding_sources USING btree (finding_id, kind, document_id) WHERE ((kind)::text = ANY ((ARRAY['inventory_document'::character varying, 'rule_document'::character varying])::text[]));

-- INDEX: bid_submission_page
CREATE INDEX bid_submission_page ON public.bid_submissions USING btree (task_id, created_at, id);

-- INDEX: certificate_source_identity
CREATE UNIQUE INDEX certificate_source_identity ON public.evidence_sources USING btree (task_certificate_id, page, render_profile) WHERE ((source_kind)::text = 'user_supplied_certificate_pdf'::text);

-- INDEX: chunks_document_page
CREATE UNIQUE INDEX chunks_document_page ON public.chunks USING btree (document_id, page) WHERE (page IS NOT NULL);

-- INDEX: export_release_audit_once
CREATE UNIQUE INDEX export_release_audit_once ON public.audit_logs USING btree (object_id) WHERE ((action)::text = 'export.released'::text);

-- INDEX: ix_check_citations_item
CREATE INDEX ix_check_citations_item ON public.check_finding_citations USING btree (check_item_id) WHERE (check_item_id IS NOT NULL);

-- INDEX: ix_score_item_citations_item
CREATE INDEX ix_score_item_citations_item ON public.score_item_citations USING btree (score_item_id);

-- INDEX: ix_score_report_items_report
CREATE INDEX ix_score_report_items_report ON public.score_report_items USING btree (report_id);

-- INDEX: ix_score_reports_task
CREATE INDEX ix_score_reports_task ON public.score_reports USING btree (task_id, created_at, id);

-- INDEX: jobs_agent_session
CREATE INDEX jobs_agent_session ON public.jobs USING btree (agent_session_id);

-- INDEX: jobs_state
CREATE INDEX jobs_state ON public.jobs USING btree (status);

-- INDEX: lifecycle_certificate_sequence
CREATE UNIQUE INDEX lifecycle_certificate_sequence ON public.resource_lifecycle_events USING btree (certificate_id, revision) WHERE (certificate_id IS NOT NULL);

-- INDEX: lifecycle_feature_sequence
CREATE UNIQUE INDEX lifecycle_feature_sequence ON public.resource_lifecycle_events USING btree (feature_id, revision) WHERE (feature_id IS NOT NULL);

-- INDEX: lifecycle_product_sequence
CREATE UNIQUE INDEX lifecycle_product_sequence ON public.resource_lifecycle_events USING btree (product_id, revision) WHERE (product_id IS NOT NULL);

-- INDEX: lifecycle_profile_sequence
CREATE UNIQUE INDEX lifecycle_profile_sequence ON public.resource_lifecycle_events USING btree (profile_id, revision) WHERE (profile_id IS NOT NULL);

-- INDEX: lifecycle_template_sequence
CREATE UNIQUE INDEX lifecycle_template_sequence ON public.resource_lifecycle_events USING btree (template_id, revision) WHERE (template_id IS NOT NULL);

-- INDEX: management_bindings_browse
CREATE INDEX management_bindings_browse ON public.export_template_bindings USING btree (template_revision_id, reviewed_at DESC, id DESC);

-- INDEX: management_certificate_audit_author
CREATE INDEX management_certificate_audit_author ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['resource.certificate.create'::character varying, 'resource.certificate.update'::character varying, 'resource.certificate.file.create'::character varying])::text[]));

-- INDEX: management_certificate_history
CREATE INDEX management_certificate_history ON public.certificate_revisions USING btree (certificate_id, revision DESC, id DESC);

-- INDEX: management_certificate_lifecycle_audit
CREATE INDEX management_certificate_lifecycle_audit ON public.audit_logs USING btree (object_id, ((details ->> 'event_id'::text))) WHERE ((action)::text = ANY ((ARRAY['resource.certificate.deactivate'::character varying, 'resource.certificate.restore'::character varying])::text[]));

-- INDEX: management_certificate_lifecycle_history
CREATE INDEX management_certificate_lifecycle_history ON public.resource_lifecycle_events USING btree (certificate_id, revision DESC, id DESC) WHERE (certificate_id IS NOT NULL);

-- INDEX: management_certificates_browse
CREATE INDEX management_certificates_browse ON public.certificates USING btree (created_at DESC, id DESC);

-- INDEX: management_certificates_prefix
CREATE INDEX management_certificates_prefix ON public.certificates USING gin (search_vector);

-- INDEX: management_certificates_state_browse
CREATE INDEX management_certificates_state_browse ON public.certificates USING btree (lifecycle_state, created_at DESC, id DESC);

-- INDEX: management_confidential_fields_active_browse
CREATE INDEX management_confidential_fields_active_browse ON public.confidential_fields USING btree (archived, key, id);

-- INDEX: management_confidential_fields_browse
CREATE INDEX management_confidential_fields_browse ON public.confidential_fields USING btree (key, id);

-- INDEX: management_confidential_fields_org_browse
CREATE INDEX management_confidential_fields_org_browse ON public.confidential_fields USING btree (scope, archived, key, id);

-- INDEX: management_confidential_org_history
CREATE INDEX management_confidential_org_history ON public.confidential_values USING btree (field_id, version DESC, id DESC) INCLUDE (created_at, tail) WHERE (task_id IS NULL);

-- INDEX: management_confidential_task_history
CREATE INDEX management_confidential_task_history ON public.confidential_values USING btree (field_id, task_id, version DESC, id DESC) INCLUDE (created_at, tail) WHERE (task_id IS NOT NULL);

-- INDEX: management_confidential_token_owner
CREATE INDEX management_confidential_token_owner ON public.confidential_field_search_tokens USING btree (field_id, token);

-- INDEX: management_feature_audit_author
CREATE INDEX management_feature_audit_author ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['resource.feature.create'::character varying, 'resource.feature.update'::character varying])::text[]));

-- INDEX: management_feature_history
CREATE INDEX management_feature_history ON public.feature_revisions USING btree (feature_id, revision DESC, id DESC);

-- INDEX: management_feature_lifecycle_audit
CREATE INDEX management_feature_lifecycle_audit ON public.audit_logs USING btree (object_id, ((details ->> 'event_id'::text))) WHERE ((action)::text = ANY ((ARRAY['resource.feature.deactivate'::character varying, 'resource.feature.restore'::character varying])::text[]));

-- INDEX: management_feature_lifecycle_history
CREATE INDEX management_feature_lifecycle_history ON public.resource_lifecycle_events USING btree (feature_id, revision DESC, id DESC) WHERE (feature_id IS NOT NULL);

-- INDEX: management_feature_parent
CREATE INDEX management_feature_parent ON public.feature_revisions USING btree (product_id, feature_id, revision);

-- INDEX: management_feature_status
CREATE INDEX management_feature_status ON public.feature_revisions USING btree (((data ->> 'status'::text)), feature_id, revision);

-- INDEX: management_features_browse
CREATE INDEX management_features_browse ON public.features USING btree (created_at DESC, id DESC);

-- INDEX: management_features_prefix
CREATE INDEX management_features_prefix ON public.features USING gin (search_vector);

-- INDEX: management_features_state_browse
CREATE INDEX management_features_state_browse ON public.features USING btree (lifecycle_state, created_at DESC, id DESC);

-- INDEX: management_lifecycle_history
CREATE INDEX management_lifecycle_history ON public.resource_lifecycle_events USING btree (product_id, revision DESC, id DESC);

-- INDEX: management_memory_active_history
CREATE INDEX management_memory_active_history ON public.memory_revisions USING btree (memory_id) WHERE ((status)::text = 'active'::text);

-- INDEX: management_memory_audit_author
CREATE INDEX management_memory_audit_author ON public.audit_logs USING btree (memory_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['memory.create'::character varying, 'memory.update'::character varying, 'memory.approve'::character varying, 'memory.reject'::character varying, 'memory.disable'::character varying, 'memory.delete'::character varying, 'memory.candidate.publish'::character varying])::text[]));

-- INDEX: management_memory_browse
CREATE INDEX management_memory_browse ON public.memories USING btree (created_at DESC, id DESC);

-- INDEX: management_memory_candidate_events
CREATE INDEX management_memory_candidate_events ON public.jobs USING gin ((((result -> 'submission'::text) -> 'event_ids'::text))) WHERE ((kind)::text = 'memory_candidate'::text);

-- INDEX: management_memory_candidate_task
CREATE INDEX management_memory_candidate_task ON public.jobs USING btree (task_id, created_at DESC, id DESC) WHERE ((kind)::text = 'memory_candidate'::text);

-- INDEX: management_memory_feedback_browse
CREATE INDEX management_memory_feedback_browse ON public.memory_feedback_events USING btree (task_id, created_at DESC, id DESC);

-- INDEX: management_memory_filter
CREATE INDEX management_memory_filter ON public.memories USING btree (search_status, search_kind, search_expires_at, created_at DESC, id DESC);

-- INDEX: management_memory_history
CREATE INDEX management_memory_history ON public.memory_revisions USING btree (memory_id, revision DESC, id DESC);

-- INDEX: management_memory_prefix
CREATE INDEX management_memory_prefix ON public.memories USING gin (search_vector);

-- INDEX: management_memory_tags
CREATE INDEX management_memory_tags ON public.memories USING gin (search_tags);

-- INDEX: management_product_audit_author
CREATE INDEX management_product_audit_author ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['resource.product.create'::character varying, 'resource.product.update'::character varying])::text[]));

-- INDEX: management_product_history
CREATE INDEX management_product_history ON public.product_revisions USING btree (product_id, revision DESC, id DESC);

-- INDEX: management_product_lifecycle_audit
CREATE INDEX management_product_lifecycle_audit ON public.audit_logs USING btree (object_id, ((details ->> 'event_id'::text))) WHERE ((action)::text = ANY ((ARRAY['resource.product.deactivate'::character varying, 'resource.product.restore'::character varying])::text[]));

-- INDEX: management_products_browse
CREATE INDEX management_products_browse ON public.products USING btree (created_at DESC, id DESC);

-- INDEX: management_products_prefix
CREATE INDEX management_products_prefix ON public.products USING gin (search_vector);

-- INDEX: management_products_state_browse
CREATE INDEX management_products_state_browse ON public.products USING btree (lifecycle_state, created_at DESC, id DESC);

-- INDEX: management_profile_audit_author
CREATE INDEX management_profile_audit_author ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['resource.profile.create'::character varying, 'resource.profile.update'::character varying])::text[]));

-- INDEX: management_profile_history
CREATE INDEX management_profile_history ON public.org_profile_revisions USING btree (profile_id, revision DESC, id DESC);

-- INDEX: management_profile_lifecycle_audit
CREATE INDEX management_profile_lifecycle_audit ON public.audit_logs USING btree (object_id, ((details ->> 'event_id'::text))) WHERE ((action)::text = ANY ((ARRAY['resource.profile.deactivate'::character varying, 'resource.profile.restore'::character varying])::text[]));

-- INDEX: management_profile_lifecycle_history
CREATE INDEX management_profile_lifecycle_history ON public.resource_lifecycle_events USING btree (profile_id, revision DESC, id DESC) WHERE (profile_id IS NOT NULL);

-- INDEX: management_profiles_browse
CREATE INDEX management_profiles_browse ON public.org_profiles USING btree (created_at DESC, id DESC);

-- INDEX: management_profiles_prefix
CREATE INDEX management_profiles_prefix ON public.org_profiles USING gin (search_vector);

-- INDEX: management_profiles_state_browse
CREATE INDEX management_profiles_state_browse ON public.org_profiles USING btree (lifecycle_state, created_at DESC, id DESC);

-- INDEX: management_provider_audit_author
CREATE INDEX management_provider_audit_author ON public.audit_logs USING btree (object_id) WHERE ((action)::text = 'provider.set'::text);

-- INDEX: management_provider_history
CREATE INDEX management_provider_history ON public.provider_configs USING btree (capability, revision DESC, id DESC);

-- INDEX: management_template_audit_author
CREATE INDEX management_template_audit_author ON public.audit_logs USING btree (resource_revision_id_text, object_id) WHERE ((action)::text = ANY ((ARRAY['resource.template.create'::character varying, 'resource.template.update'::character varying])::text[]));

-- INDEX: management_template_history
CREATE INDEX management_template_history ON public.template_revisions USING btree (template_id, revision DESC, id DESC);

-- INDEX: management_template_lifecycle_audit
CREATE INDEX management_template_lifecycle_audit ON public.audit_logs USING btree (object_id, ((details ->> 'event_id'::text))) WHERE ((action)::text = ANY ((ARRAY['resource.template.deactivate'::character varying, 'resource.template.restore'::character varying])::text[]));

-- INDEX: management_template_lifecycle_history
CREATE INDEX management_template_lifecycle_history ON public.resource_lifecycle_events USING btree (template_id, revision DESC, id DESC) WHERE (template_id IS NOT NULL);

-- INDEX: management_templates_browse
CREATE INDEX management_templates_browse ON public.templates USING btree (created_at DESC, id DESC);

-- INDEX: management_templates_prefix
CREATE INDEX management_templates_prefix ON public.templates USING gin (search_vector);

-- INDEX: management_templates_state_browse
CREATE INDEX management_templates_state_browse ON public.templates USING btree (lifecycle_state, created_at DESC, id DESC);

-- INDEX: memory_calls_job
CREATE INDEX memory_calls_job ON public.memory_call_inputs USING btree (job_id);

-- INDEX: memory_eval_task
CREATE INDEX memory_eval_task ON public.memory_eval_samples USING btree (task_id, created_at, id);

-- INDEX: memory_feedback_task
CREATE INDEX memory_feedback_task ON public.memory_feedback_events USING btree (task_id, created_at, id);

-- INDEX: memory_revision_search
CREATE INDEX memory_revision_search ON public.memory_revisions USING btree (status, memory_id);

-- INDEX: memory_revision_tags
CREATE INDEX memory_revision_tags ON public.memory_revisions USING gin (normalized_tags);

-- INDEX: memory_scope_lookup
CREATE INDEX memory_scope_lookup ON public.memories USING btree (scope);

-- INDEX: platform_credentials_one_service
CREATE UNIQUE INDEX platform_credentials_one_service ON public.platform_credentials USING btree (purpose) WHERE ((state)::text <> 'removed'::text);

-- INDEX: requirement_review_events_scope
CREATE INDEX requirement_review_events_scope ON public.requirement_review_events USING btree (requirement_id, revision);

-- INDEX: requirement_reviews_rejected
CREATE INDEX requirement_reviews_rejected ON public.requirement_reviews USING btree (rejected_job_id, rejected_index);

-- INDEX: requirement_reviews_scope
CREATE INDEX requirement_reviews_scope ON public.requirement_reviews USING btree (task_id, extraction_job_id, state);

-- INDEX: rubric_generated_job_once
CREATE UNIQUE INDEX rubric_generated_job_once ON public.score_rubric_sets USING btree (job_id) WHERE (prior_rubric_id IS NULL);

-- INDEX: rubric_revision_once
CREATE UNIQUE INDEX rubric_revision_once ON public.score_rubric_sets USING btree (prior_rubric_id) WHERE (prior_rubric_id IS NOT NULL);

-- INDEX: sandbox_artifacts_attempt
CREATE INDEX sandbox_artifacts_attempt ON public.sandbox_artifacts USING btree (attempt_record_id, ordinal);

-- INDEX: sandbox_attempts_run
CREATE INDEX sandbox_attempts_run ON public.sandbox_attempts USING btree (sandbox_run_id, started_at);

-- INDEX: sandbox_runs_task
CREATE INDEX sandbox_runs_task ON public.sandbox_runs USING btree (task_id, created_at);

-- INDEX: screenshot_asset_listing
CREATE INDEX screenshot_asset_listing ON public.screenshot_assets USING btree (task_id, extraction_job_id, created_at, id);

-- INDEX: screenshot_rendition_annotation_identity
CREATE UNIQUE INDEX screenshot_rendition_annotation_identity ON public.screenshot_renditions USING btree (asset_id, parent_rendition_id, plan_sha256, profile, renderer_identity, provenance_sha256) WHERE ((profile)::text = ANY ((ARRAY['annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[]));

-- INDEX: screenshot_rendition_legacy_identity
CREATE UNIQUE INDEX screenshot_rendition_legacy_identity ON public.screenshot_renditions USING btree (asset_id, parent_rendition_id, plan_sha256, profile) WHERE ((profile)::text = ANY ((ARRAY['screenshot-markup-v1'::character varying, 'prototype-clean-v1'::character varying])::text[]));

-- INDEX: screenshot_single_root
CREATE UNIQUE INDEX screenshot_single_root ON public.screenshot_renditions USING btree (asset_id) WHERE (parent_rendition_id IS NULL);

-- INDEX: screenshot_suggestion_listing
CREATE INDEX screenshot_suggestion_listing ON public.screenshot_suggestions USING btree (analysis_run_id, created_at, id);

-- INDEX: task_certificate_active_slot
CREATE UNIQUE INDEX task_certificate_active_slot ON public.task_certificates USING btree (task_id, certificate_id, lot) WHERE active;

-- INDEX: task_feature_active_slot
CREATE UNIQUE INDEX task_feature_active_slot ON public.task_features USING btree (task_id, feature_id, lot) WHERE active;

-- INDEX: task_jobs_lifecycle
CREATE INDEX task_jobs_lifecycle ON public.jobs USING btree (task_id, status, id);

-- INDEX: task_product_active_slot
CREATE UNIQUE INDEX task_product_active_slot ON public.task_resources USING btree (task_id, product_id, lot) WHERE active;

-- INDEX: task_profile_active_slot
CREATE UNIQUE INDEX task_profile_active_slot ON public.task_org_profiles USING btree (task_id, profile_id, lot) WHERE active;

-- INDEX: task_template_active_slot
CREATE UNIQUE INDEX task_template_active_slot ON public.task_templates USING btree (task_id, template_id, lot) WHERE active;

-- INDEX: uq_confidential_values_org_version
CREATE UNIQUE INDEX uq_confidential_values_org_version ON public.confidential_values USING btree (field_id, version) WHERE (task_id IS NULL);

-- INDEX: uq_confidential_values_task_version
CREATE UNIQUE INDEX uq_confidential_values_task_version ON public.confidential_values USING btree (field_id, task_id, version) WHERE (task_id IS NOT NULL);

-- INDEX: usage_provider_month
CREATE INDEX usage_provider_month ON public.usage_records USING btree (provider_config_id, created_at);

-- INDEX: usage_records_job
CREATE INDEX usage_records_job ON public.usage_records USING btree (job_id);

-- INDEX: usage_records_task
CREATE INDEX usage_records_task ON public.usage_records USING btree (task_id);

-- INDEX: vendor_calls_job
CREATE INDEX vendor_calls_job ON public.vendor_calls USING btree (job_id);

-- INDEX: vendor_calls_task
CREATE INDEX vendor_calls_task ON public.vendor_calls USING btree (task_id, state);

-- INDEX: vendor_calls_unsettled
CREATE INDEX vendor_calls_unsettled ON public.vendor_calls USING btree (state);

-- INDEX: credential_probes credential_probes_started
CREATE INDEX credential_probes_started ON public.credential_probes USING btree (started_at);

-- INDEX: credential_probes credential_probes_credential_started
CREATE INDEX credential_probes_credential_started ON public.credential_probes USING btree (credential_id, started_at);
