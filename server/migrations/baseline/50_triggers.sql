-- TRIGGER: chunks aa_requirement_chunk_lock
CREATE TRIGGER aa_requirement_chunk_lock BEFORE UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();

-- TRIGGER: documents aa_requirement_document_lock
CREATE TRIGGER aa_requirement_document_lock BEFORE UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();

-- TRIGGER: requirement_review_events aa_requirement_invalidation_audit
CREATE TRIGGER aa_requirement_invalidation_audit AFTER INSERT ON public.requirement_review_events FOR EACH ROW EXECUTE FUNCTION public.requirement_invalidation_audit();

-- TRIGGER: requirements aa_requirement_source_lock
CREATE TRIGGER aa_requirement_source_lock BEFORE UPDATE ON public.requirements FOR EACH ROW EXECUTE FUNCTION public.requirement_source_lock();

-- TRIGGER: export_render_candidates aaa_export_actor
CREATE TRIGGER aaa_export_actor BEFORE INSERT ON public.export_render_candidates FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: export_run_evidence aaa_export_actor
CREATE TRIGGER aaa_export_actor BEFORE INSERT ON public.export_run_evidence FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: export_run_items aaa_export_actor
CREATE TRIGGER aaa_export_actor BEFORE INSERT ON public.export_run_items FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: export_runs aaa_export_actor
CREATE TRIGGER aaa_export_actor BEFORE INSERT ON public.export_runs FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: export_template_bindings aaa_export_actor
CREATE TRIGGER aaa_export_actor AFTER INSERT ON public.export_template_bindings FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: exports aaa_export_actor
CREATE TRIGGER aaa_export_actor BEFORE INSERT ON public.exports FOR EACH ROW EXECUTE FUNCTION public.export_insert_gate();

-- TRIGGER: card_evidence_links aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.card_evidence_links FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: card_generation_runs aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.card_generation_runs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: draft_runs aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.draft_runs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: evidence aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: response_card_revisions aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: response_cards aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.response_cards FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: response_items aaa_response_tenant_gate
CREATE TRIGGER aaa_response_tenant_gate BEFORE INSERT ON public.response_items FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: prototype_decision_batches aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: prototype_evidence_decisions aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_analysis_inputs aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_analysis_inputs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_analysis_runs aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_analysis_runs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_assets aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_privacy_reviews aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_prototype_runs aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_prototype_runs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_renditions aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_search_candidates aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_search_candidates FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_search_runs aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_search_runs FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_suggestions aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_suggestions FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_vendor_archives aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_vendor_archives FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: screenshot_withdrawals aaa_screenshot_tenant_gate
CREATE TRIGGER aaa_screenshot_tenant_gate BEFORE INSERT ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.response_tenant_insert_gate();

-- TRIGGER: check_decisions aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.check_decisions FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: evidence aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE UPDATE ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: prototype_decision_batches aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: prototype_evidence_decisions aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: response_card_revisions aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: score_rubric_classifications aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.score_rubric_classifications FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: score_rubric_coverage_decisions aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.score_rubric_coverage_decisions FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: score_rubric_decisions aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: score_rubric_revision_events aaa_task_human_authority
CREATE TRIGGER aaa_task_human_authority BEFORE INSERT ON public.score_rubric_revision_events FOR EACH ROW EXECUTE FUNCTION public.task_human_content_guard();

-- TRIGGER: usage_records admission_usage_guard
CREATE TRIGGER admission_usage_guard BEFORE INSERT OR DELETE OR UPDATE ON public.usage_records FOR EACH ROW EXECUTE FUNCTION public.usage_admission_guard();

-- TRIGGER: audit_logs agent_audit_origin
CREATE TRIGGER agent_audit_origin BEFORE INSERT OR DELETE OR UPDATE ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.agent_origin_guard();

-- TRIGGER: agent_job_links agent_link_guard
CREATE TRIGGER agent_link_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_job_links FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();

-- TRIGGER: agent_messages agent_message_guard
CREATE TRIGGER agent_message_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_messages FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();

-- TRIGGER: agent_pauses agent_pause_guard
CREATE TRIGGER agent_pause_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_pauses FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();

-- TRIGGER: agent_principals agent_principal_guard
CREATE TRIGGER agent_principal_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_principals FOR EACH ROW EXECUTE FUNCTION public.agent_principal_guard();

-- TRIGGER: agent_sessions agent_session_guard
CREATE TRIGGER agent_session_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_sessions FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();

-- TRIGGER: agent_steps agent_step_guard
CREATE TRIGGER agent_step_guard BEFORE INSERT OR DELETE OR UPDATE ON public.agent_steps FOR EACH ROW EXECUTE FUNCTION public.agent_history_guard();

-- TRIGGER: annotation_materials annotation_binding_guard
CREATE TRIGGER annotation_binding_guard AFTER INSERT ON public.annotation_materials FOR EACH ROW EXECUTE FUNCTION public.annotation_binding_guard();

-- TRIGGER: annotation_releases annotation_binding_guard
CREATE TRIGGER annotation_binding_guard AFTER INSERT ON public.annotation_releases FOR EACH ROW EXECUTE FUNCTION public.annotation_binding_guard();

-- TRIGGER: annotation_requests annotation_binding_guard
CREATE TRIGGER annotation_binding_guard AFTER INSERT ON public.annotation_requests FOR EACH ROW EXECUTE FUNCTION public.annotation_binding_guard();

-- TRIGGER: evidence annotation_evidence_binding
CREATE TRIGGER annotation_evidence_binding AFTER INSERT OR UPDATE ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.annotation_evidence_gate();

-- TRIGGER: annotation_materials annotation_immutable
CREATE TRIGGER annotation_immutable BEFORE DELETE OR UPDATE ON public.annotation_materials FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: annotation_releases annotation_immutable
CREATE TRIGGER annotation_immutable BEFORE DELETE OR UPDATE ON public.annotation_releases FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: annotation_requests annotation_immutable
CREATE TRIGGER annotation_immutable BEFORE DELETE OR UPDATE ON public.annotation_requests FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: annotation_materials annotation_task_lock
CREATE TRIGGER annotation_task_lock BEFORE INSERT ON public.annotation_materials FOR EACH ROW EXECUTE FUNCTION public.annotation_task_lock();

-- TRIGGER: annotation_releases annotation_task_lock
CREATE TRIGGER annotation_task_lock BEFORE INSERT ON public.annotation_releases FOR EACH ROW EXECUTE FUNCTION public.annotation_task_lock();

-- TRIGGER: annotation_requests annotation_task_lock
CREATE TRIGGER annotation_task_lock BEFORE INSERT ON public.annotation_requests FOR EACH ROW EXECUTE FUNCTION public.annotation_task_lock();

-- TRIGGER: attachment_revisions attachment_file_complete
CREATE CONSTRAINT TRIGGER attachment_file_complete AFTER INSERT ON public.attachment_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.attachment_file_complete();

-- TRIGGER: attachment_file_parts attachment_immutable
CREATE TRIGGER attachment_immutable BEFORE DELETE OR UPDATE ON public.attachment_file_parts FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate();

-- TRIGGER: attachment_files attachment_immutable
CREATE TRIGGER attachment_immutable BEFORE DELETE OR UPDATE ON public.attachment_files FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate();

-- TRIGGER: attachment_privacy_holds attachment_immutable
CREATE TRIGGER attachment_immutable BEFORE DELETE OR UPDATE ON public.attachment_privacy_holds FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate();

-- TRIGGER: attachment_reviews attachment_immutable
CREATE TRIGGER attachment_immutable BEFORE DELETE OR UPDATE ON public.attachment_reviews FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate();

-- TRIGGER: attachment_revisions attachment_immutable
CREATE TRIGGER attachment_immutable BEFORE DELETE OR UPDATE ON public.attachment_revisions FOR EACH ROW EXECUTE FUNCTION public.attachment_immutable_gate();

-- TRIGGER: evidence_sources attachment_source_immutable
CREATE TRIGGER attachment_source_immutable BEFORE DELETE OR UPDATE ON public.evidence_sources FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: bid_submission_documents bid_file_complete
CREATE CONSTRAINT TRIGGER bid_file_complete AFTER INSERT ON public.bid_submission_documents DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_upload_complete();

-- TRIGGER: bid_document_pages bid_page_complete
CREATE CONSTRAINT TRIGGER bid_page_complete AFTER INSERT ON public.bid_document_pages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_complete();

-- TRIGGER: bid_prepared_documents bid_prepared_complete
CREATE CONSTRAINT TRIGGER bid_prepared_complete AFTER INSERT ON public.bid_prepared_documents DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_complete();

-- TRIGGER: bid_presence_authorizations bid_presence_complete
CREATE CONSTRAINT TRIGGER bid_presence_complete AFTER INSERT ON public.bid_presence_authorizations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_presence_complete();

-- TRIGGER: bid_presence_authorized_images bid_presence_complete
CREATE CONSTRAINT TRIGGER bid_presence_complete AFTER INSERT ON public.bid_presence_authorized_images DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_presence_complete();

-- TRIGGER: bid_presence_images bid_presence_complete
CREATE CONSTRAINT TRIGGER bid_presence_complete AFTER INSERT ON public.bid_presence_images DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_presence_complete();

-- TRIGGER: bid_presence_preparations bid_presence_complete
CREATE CONSTRAINT TRIGGER bid_presence_complete AFTER INSERT ON public.bid_presence_preparations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_presence_complete();

-- TRIGGER: bid_presence_authorizations bid_presence_immutable
CREATE TRIGGER bid_presence_immutable BEFORE DELETE OR UPDATE ON public.bid_presence_authorizations FOR EACH ROW EXECUTE FUNCTION public.bid_presence_immutable();

-- TRIGGER: bid_presence_authorized_images bid_presence_immutable
CREATE TRIGGER bid_presence_immutable BEFORE DELETE OR UPDATE ON public.bid_presence_authorized_images FOR EACH ROW EXECUTE FUNCTION public.bid_presence_immutable();

-- TRIGGER: bid_presence_images bid_presence_immutable
CREATE TRIGGER bid_presence_immutable BEFORE DELETE OR UPDATE ON public.bid_presence_images FOR EACH ROW EXECUTE FUNCTION public.bid_presence_immutable();

-- TRIGGER: bid_presence_preparations bid_presence_immutable
CREATE TRIGGER bid_presence_immutable BEFORE DELETE OR UPDATE ON public.bid_presence_preparations FOR EACH ROW EXECUTE FUNCTION public.bid_presence_immutable();

-- TRIGGER: bid_presence_authorizations bid_presence_insert_guard
CREATE TRIGGER bid_presence_insert_guard BEFORE INSERT ON public.bid_presence_authorizations FOR EACH ROW EXECUTE FUNCTION public.bid_presence_insert_guard();

-- TRIGGER: bid_presence_authorized_images bid_presence_insert_guard
CREATE TRIGGER bid_presence_insert_guard BEFORE INSERT ON public.bid_presence_authorized_images FOR EACH ROW EXECUTE FUNCTION public.bid_presence_insert_guard();

-- TRIGGER: bid_presence_images bid_presence_insert_guard
CREATE TRIGGER bid_presence_insert_guard BEFORE INSERT ON public.bid_presence_images FOR EACH ROW EXECUTE FUNCTION public.bid_presence_insert_guard();

-- TRIGGER: bid_presence_preparations bid_presence_insert_guard
CREATE TRIGGER bid_presence_insert_guard BEFORE INSERT ON public.bid_presence_preparations FOR EACH ROW EXECUTE FUNCTION public.bid_presence_insert_guard();

-- TRIGGER: bid_outbound_authorizations bid_privacy_grant_complete
CREATE CONSTRAINT TRIGGER bid_privacy_grant_complete AFTER INSERT ON public.bid_outbound_authorizations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_complete();

-- TRIGGER: bid_outbound_authorized_pages bid_privacy_grant_page_complete
CREATE CONSTRAINT TRIGGER bid_privacy_grant_page_complete AFTER INSERT ON public.bid_outbound_authorized_pages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_complete();

-- TRIGGER: bid_outbound_authorizations bid_privacy_immutable
CREATE TRIGGER bid_privacy_immutable BEFORE DELETE OR UPDATE ON public.bid_outbound_authorizations FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable();

-- TRIGGER: bid_outbound_authorized_pages bid_privacy_immutable
CREATE TRIGGER bid_privacy_immutable BEFORE DELETE OR UPDATE ON public.bid_outbound_authorized_pages FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable();

-- TRIGGER: bid_redacted_pages bid_privacy_immutable
CREATE TRIGGER bid_privacy_immutable BEFORE DELETE OR UPDATE ON public.bid_redacted_pages FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable();

-- TRIGGER: bid_redaction_snapshots bid_privacy_immutable
CREATE TRIGGER bid_privacy_immutable BEFORE DELETE OR UPDATE ON public.bid_redaction_snapshots FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable();

-- TRIGGER: bid_review_name_lists bid_privacy_immutable
CREATE TRIGGER bid_privacy_immutable BEFORE DELETE OR UPDATE ON public.bid_review_name_lists FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_immutable();

-- TRIGGER: bid_redaction_snapshots bid_privacy_snapshot_complete
CREATE CONSTRAINT TRIGGER bid_privacy_snapshot_complete AFTER INSERT ON public.bid_redaction_snapshots DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_complete();

-- TRIGGER: bid_preparation_publications bid_publication_complete
CREATE CONSTRAINT TRIGGER bid_publication_complete AFTER INSERT ON public.bid_preparation_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_complete();

-- TRIGGER: bid_review_finding_events bid_review_finding_event_guard
CREATE TRIGGER bid_review_finding_event_guard BEFORE INSERT ON public.bid_review_finding_events FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_event_guard();

-- TRIGGER: bid_review_finding_events bid_review_finding_immutable
CREATE TRIGGER bid_review_finding_immutable BEFORE DELETE OR UPDATE ON public.bid_review_finding_events FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_immutable();

-- TRIGGER: bid_review_finding_sources bid_review_finding_immutable
CREATE TRIGGER bid_review_finding_immutable BEFORE DELETE OR UPDATE ON public.bid_review_finding_sources FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_immutable();

-- TRIGGER: bid_review_findings bid_review_finding_immutable
CREATE TRIGGER bid_review_finding_immutable BEFORE DELETE OR UPDATE ON public.bid_review_findings FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_immutable();

-- TRIGGER: bid_review_finding_sources bid_review_finding_output_guard
CREATE TRIGGER bid_review_finding_output_guard BEFORE INSERT ON public.bid_review_finding_sources FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_output_guard();

-- TRIGGER: bid_review_findings bid_review_finding_output_guard
CREATE TRIGGER bid_review_finding_output_guard BEFORE INSERT ON public.bid_review_findings FOR EACH ROW EXECUTE FUNCTION public.bid_review_finding_output_guard();

-- TRIGGER: bid_review_finding_sources bid_review_findings_complete
CREATE CONSTRAINT TRIGGER bid_review_findings_complete AFTER INSERT ON public.bid_review_finding_sources DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_findings_complete();

-- TRIGGER: bid_review_findings bid_review_findings_complete
CREATE CONSTRAINT TRIGGER bid_review_findings_complete AFTER INSERT ON public.bid_review_findings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_findings_complete();

-- TRIGGER: bid_review_publications bid_review_findings_complete
CREATE CONSTRAINT TRIGGER bid_review_findings_complete AFTER INSERT ON public.bid_review_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_findings_complete();

-- TRIGGER: bid_document_pages bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_document_pages FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: bid_preparation_publications bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_preparation_publications FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: bid_preparations bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_preparations FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: bid_prepared_documents bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_prepared_documents FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: bid_submission_documents bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_submission_documents FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: bid_submissions bid_review_immutable
CREATE TRIGGER bid_review_immutable BEFORE DELETE OR UPDATE ON public.bid_submissions FOR EACH ROW EXECUTE FUNCTION public.bid_review_immutable_gate();

-- TRIGGER: jobs bid_review_job_immutable
CREATE TRIGGER bid_review_job_immutable BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.bid_review_job_immutable();

-- TRIGGER: bid_review_obligations bid_review_output_complete
CREATE CONSTRAINT TRIGGER bid_review_output_complete AFTER INSERT ON public.bid_review_obligations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_complete();

-- TRIGGER: bid_review_publications bid_review_output_complete
CREATE CONSTRAINT TRIGGER bid_review_output_complete AFTER INSERT ON public.bid_review_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_complete();

-- TRIGGER: bid_review_required_locations bid_review_output_complete
CREATE CONSTRAINT TRIGGER bid_review_output_complete AFTER INSERT ON public.bid_review_required_locations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_complete();

-- TRIGGER: bid_review_signing_requirements bid_review_output_complete
CREATE CONSTRAINT TRIGGER bid_review_output_complete AFTER INSERT ON public.bid_review_signing_requirements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_complete();

-- TRIGGER: bid_review_obligations bid_review_output_guard
CREATE TRIGGER bid_review_output_guard BEFORE INSERT ON public.bid_review_obligations FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_guard();

-- TRIGGER: bid_review_publications bid_review_output_guard
CREATE TRIGGER bid_review_output_guard BEFORE INSERT ON public.bid_review_publications FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_guard();

-- TRIGGER: bid_review_required_locations bid_review_output_guard
CREATE TRIGGER bid_review_output_guard BEFORE INSERT ON public.bid_review_required_locations FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_guard();

-- TRIGGER: bid_review_signing_requirements bid_review_output_guard
CREATE TRIGGER bid_review_output_guard BEFORE INSERT ON public.bid_review_signing_requirements FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_guard();

-- TRIGGER: bid_review_obligations bid_review_output_immutable
CREATE TRIGGER bid_review_output_immutable BEFORE DELETE OR UPDATE ON public.bid_review_obligations FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable();

-- TRIGGER: bid_review_publications bid_review_output_immutable
CREATE TRIGGER bid_review_output_immutable BEFORE DELETE OR UPDATE ON public.bid_review_publications FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable();

-- TRIGGER: bid_review_required_locations bid_review_output_immutable
CREATE TRIGGER bid_review_output_immutable BEFORE DELETE OR UPDATE ON public.bid_review_required_locations FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable();

-- TRIGGER: bid_review_runs bid_review_output_immutable
CREATE TRIGGER bid_review_output_immutable BEFORE DELETE OR UPDATE ON public.bid_review_runs FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable();

-- TRIGGER: bid_review_signing_requirements bid_review_output_immutable
CREATE TRIGGER bid_review_output_immutable BEFORE DELETE OR UPDATE ON public.bid_review_signing_requirements FOR EACH ROW EXECUTE FUNCTION public.bid_review_output_immutable();

-- TRIGGER: bid_review_report_artifacts bid_review_report_immutable
CREATE TRIGGER bid_review_report_immutable BEFORE DELETE OR UPDATE ON public.bid_review_report_artifacts FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_immutable();

-- TRIGGER: bid_review_report_snapshots bid_review_report_immutable
CREATE TRIGGER bid_review_report_immutable BEFORE DELETE OR UPDATE ON public.bid_review_report_snapshots FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_immutable();

-- TRIGGER: bid_review_report_artifacts bid_review_report_insert_guard
CREATE TRIGGER bid_review_report_insert_guard BEFORE INSERT ON public.bid_review_report_artifacts FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_insert_guard();

-- TRIGGER: bid_review_report_snapshots bid_review_report_insert_guard
CREATE TRIGGER bid_review_report_insert_guard BEFORE INSERT ON public.bid_review_report_snapshots FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_insert_guard();

-- TRIGGER: jobs bid_review_report_job_guard
CREATE TRIGGER bid_review_report_job_guard BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_job_guard();

-- TRIGGER: bid_review_report_artifacts bid_review_report_pair_complete
CREATE CONSTRAINT TRIGGER bid_review_report_pair_complete AFTER INSERT ON public.bid_review_report_artifacts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_report_pair_complete();

-- TRIGGER: bid_review_runs bid_review_run_insert
CREATE TRIGGER bid_review_run_insert AFTER INSERT ON public.bid_review_runs FOR EACH ROW EXECUTE FUNCTION public.bid_review_run_guard();

-- TRIGGER: bid_preparations bid_signing_admission_complete
CREATE CONSTRAINT TRIGGER bid_signing_admission_complete AFTER INSERT ON public.bid_preparations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_signing_admission_complete();

-- TRIGGER: bid_signing_candidates bid_signing_candidate_complete
CREATE CONSTRAINT TRIGGER bid_signing_candidate_complete AFTER INSERT ON public.bid_signing_candidates DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_signing_complete();

-- TRIGGER: bid_pdf_validations bid_signing_immutable
CREATE TRIGGER bid_signing_immutable BEFORE DELETE OR UPDATE ON public.bid_pdf_validations FOR EACH ROW EXECUTE FUNCTION public.bid_signing_immutable();

-- TRIGGER: bid_preparation_trust bid_signing_immutable
CREATE TRIGGER bid_signing_immutable BEFORE DELETE OR UPDATE ON public.bid_preparation_trust FOR EACH ROW EXECUTE FUNCTION public.bid_signing_immutable();

-- TRIGGER: bid_signing_candidates bid_signing_immutable
CREATE TRIGGER bid_signing_immutable BEFORE DELETE OR UPDATE ON public.bid_signing_candidates FOR EACH ROW EXECUTE FUNCTION public.bid_signing_immutable();

-- TRIGGER: bid_preparation_publications bid_signing_publication_complete
CREATE CONSTRAINT TRIGGER bid_signing_publication_complete AFTER INSERT ON public.bid_preparation_publications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_signing_complete();

-- TRIGGER: bid_pdf_validations bid_signing_validation_complete
CREATE CONSTRAINT TRIGGER bid_signing_validation_complete AFTER INSERT ON public.bid_pdf_validations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_signing_complete();

-- TRIGGER: bid_submissions bid_upload_complete
CREATE CONSTRAINT TRIGGER bid_upload_complete AFTER INSERT ON public.bid_submissions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bid_review_upload_complete();

-- TRIGGER: card_evidence_links card_evidence_links_immutable
CREATE TRIGGER card_evidence_links_immutable BEFORE DELETE OR UPDATE ON public.card_evidence_links FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: card_generation_runs card_generation_runs_immutable
CREATE TRIGGER card_generation_runs_immutable BEFORE DELETE OR UPDATE ON public.card_generation_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: certificate_files certificate_file_new_revision_only
CREATE TRIGGER certificate_file_new_revision_only BEFORE INSERT ON public.certificate_files FOR EACH ROW EXECUTE FUNCTION public.certificate_file_new_revision_only();

-- TRIGGER: certificate_file_parts certificate_part_new_file_only
CREATE TRIGGER certificate_part_new_file_only BEFORE INSERT ON public.certificate_file_parts FOR EACH ROW EXECUTE FUNCTION public.certificate_part_new_file_only();

-- TRIGGER: check_decisions check_decision_gate
CREATE TRIGGER check_decision_gate BEFORE INSERT ON public.check_decisions FOR EACH ROW EXECUTE FUNCTION public.check_decision_gate();

-- TRIGGER: check_certificate_items check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_certificate_items FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_certificates check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_certificates FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_decisions check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_decisions FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_finding_citations check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_finding_citations FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_findings check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_findings FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_items check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_items FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_runs check_immutable
CREATE TRIGGER check_immutable BEFORE DELETE OR UPDATE ON public.check_runs FOR EACH ROW EXECUTE FUNCTION public.check_immutable_gate();

-- TRIGGER: check_certificate_items check_publication_gate
CREATE TRIGGER check_publication_gate BEFORE INSERT ON public.check_certificate_items FOR EACH ROW EXECUTE FUNCTION public.check_child_gate();

-- TRIGGER: check_certificates check_publication_gate
CREATE TRIGGER check_publication_gate BEFORE INSERT ON public.check_certificates FOR EACH ROW EXECUTE FUNCTION public.check_child_gate();

-- TRIGGER: check_finding_citations check_publication_gate
CREATE TRIGGER check_publication_gate BEFORE INSERT ON public.check_finding_citations FOR EACH ROW EXECUTE FUNCTION public.check_child_gate();

-- TRIGGER: check_findings check_publication_gate
CREATE TRIGGER check_publication_gate BEFORE INSERT ON public.check_findings FOR EACH ROW EXECUTE FUNCTION public.check_child_gate();

-- TRIGGER: check_items check_publication_gate
CREATE TRIGGER check_publication_gate BEFORE INSERT ON public.check_items FOR EACH ROW EXECUTE FUNCTION public.check_child_gate();

-- TRIGGER: check_runs check_run_complete
CREATE CONSTRAINT TRIGGER check_run_complete AFTER INSERT ON public.check_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.check_run_complete();

-- TRIGGER: check_runs check_run_gate
CREATE TRIGGER check_run_gate BEFORE INSERT ON public.check_runs FOR EACH ROW EXECUTE FUNCTION public.check_run_gate();

-- TRIGGER: draft_runs draft_runs_immutable
CREATE TRIGGER draft_runs_immutable BEFORE DELETE OR UPDATE ON public.draft_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: evidence_sources evidence_source_page_bounds
CREATE TRIGGER evidence_source_page_bounds BEFORE INSERT ON public.evidence_sources FOR EACH ROW EXECUTE FUNCTION public.evidence_source_page_bounds();

-- TRIGGER: audit_logs export_audit_actor
CREATE TRIGGER export_audit_actor BEFORE INSERT ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.export_audit_gate();

-- TRIGGER: export_render_candidates export_complete
CREATE CONSTRAINT TRIGGER export_complete AFTER INSERT ON public.export_render_candidates DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.export_complete_gate();

-- TRIGGER: export_runs export_complete
CREATE CONSTRAINT TRIGGER export_complete AFTER INSERT ON public.export_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.export_complete_gate();

-- TRIGGER: exports export_complete
CREATE CONSTRAINT TRIGGER export_complete AFTER INSERT ON public.exports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.export_complete_gate();

-- TRIGGER: export_render_candidates export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.export_render_candidates FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: export_run_evidence export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.export_run_evidence FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: export_run_items export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.export_run_items FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: export_runs export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.export_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: export_template_bindings export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.export_template_bindings FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: exports export_immutable
CREATE TRIGGER export_immutable BEFORE DELETE OR UPDATE ON public.exports FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: jobs job_actor_guard
CREATE TRIGGER job_actor_guard BEFORE INSERT OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.job_actor_guard();

-- TRIGGER: certificate_revisions management_certificate_revision_search
CREATE TRIGGER management_certificate_revision_search AFTER INSERT ON public.certificate_revisions FOR EACH ROW EXECUTE FUNCTION public.management_certificate_revision_search();

-- TRIGGER: certificates management_certificate_root_guard
CREATE TRIGGER management_certificate_root_guard BEFORE INSERT OR UPDATE ON public.certificates FOR EACH ROW EXECUTE FUNCTION public.management_certificate_root_guard();

-- TRIGGER: task_certificates management_certificate_selection_guard
CREATE TRIGGER management_certificate_selection_guard AFTER INSERT OR UPDATE ON public.task_certificates FOR EACH ROW EXECUTE FUNCTION public.management_certificate_selection_guard();

-- TRIGGER: task_certificates management_certificate_task_lock
CREATE TRIGGER management_certificate_task_lock BEFORE INSERT OR DELETE OR UPDATE ON public.task_certificates FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();

-- TRIGGER: features management_feature_head_guard
CREATE TRIGGER management_feature_head_guard AFTER UPDATE ON public.features FOR EACH ROW EXECUTE FUNCTION public.management_feature_head_guard();

-- TRIGGER: feature_revisions management_feature_revision_guard
CREATE TRIGGER management_feature_revision_guard AFTER INSERT ON public.feature_revisions FOR EACH ROW EXECUTE FUNCTION public.management_feature_revision_guard();

-- TRIGGER: feature_revisions management_feature_revision_search
CREATE TRIGGER management_feature_revision_search AFTER INSERT ON public.feature_revisions FOR EACH ROW EXECUTE FUNCTION public.management_feature_revision_search();

-- TRIGGER: features management_feature_root_guard
CREATE TRIGGER management_feature_root_guard BEFORE INSERT OR UPDATE ON public.features FOR EACH ROW EXECUTE FUNCTION public.management_feature_root_guard();

-- TRIGGER: task_features management_feature_selection_guard
CREATE TRIGGER management_feature_selection_guard AFTER INSERT OR UPDATE ON public.task_features FOR EACH ROW EXECUTE FUNCTION public.management_feature_selection_guard();

-- TRIGGER: task_features management_feature_task_lock
CREATE TRIGGER management_feature_task_lock BEFORE INSERT OR DELETE OR UPDATE ON public.task_features FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();

-- TRIGGER: resource_lifecycle_events management_lifecycle_audit_guard
CREATE CONSTRAINT TRIGGER management_lifecycle_audit_guard AFTER INSERT ON public.resource_lifecycle_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_audit_guard();

-- TRIGGER: resource_lifecycle_events management_lifecycle_guard
CREATE TRIGGER management_lifecycle_guard AFTER INSERT OR DELETE OR UPDATE ON public.resource_lifecycle_events FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_guard();

-- TRIGGER: resource_lifecycle_events management_lifecycle_z_apply
CREATE TRIGGER management_lifecycle_z_apply AFTER INSERT ON public.resource_lifecycle_events FOR EACH ROW EXECUTE FUNCTION public.management_lifecycle_apply();

-- TRIGGER: product_revisions management_product_revision_search
CREATE TRIGGER management_product_revision_search AFTER INSERT ON public.product_revisions FOR EACH ROW EXECUTE FUNCTION public.management_product_revision_search();

-- TRIGGER: products management_product_root_guard
CREATE TRIGGER management_product_root_guard BEFORE INSERT OR UPDATE ON public.products FOR EACH ROW EXECUTE FUNCTION public.management_product_root_guard();

-- TRIGGER: task_resources management_product_selection_guard
CREATE TRIGGER management_product_selection_guard AFTER INSERT OR UPDATE ON public.task_resources FOR EACH ROW EXECUTE FUNCTION public.management_product_selection_guard();

-- TRIGGER: task_org_profiles management_profile_no_reactivation
CREATE TRIGGER management_profile_no_reactivation AFTER UPDATE OF active ON public.task_org_profiles FOR EACH ROW EXECUTE FUNCTION public.screenshot_selection_no_reactivation();

-- TRIGGER: org_profile_revisions management_profile_revision_search
CREATE TRIGGER management_profile_revision_search AFTER INSERT ON public.org_profile_revisions FOR EACH ROW EXECUTE FUNCTION public.management_profile_revision_search();

-- TRIGGER: org_profiles management_profile_root_guard
CREATE TRIGGER management_profile_root_guard BEFORE INSERT OR UPDATE ON public.org_profiles FOR EACH ROW EXECUTE FUNCTION public.management_profile_root_guard();

-- TRIGGER: task_org_profiles management_profile_selection_guard
CREATE TRIGGER management_profile_selection_guard AFTER INSERT OR UPDATE ON public.task_org_profiles FOR EACH ROW EXECUTE FUNCTION public.management_profile_selection_guard();

-- TRIGGER: task_org_profiles management_profile_task_lock
CREATE TRIGGER management_profile_task_lock BEFORE INSERT OR DELETE OR UPDATE ON public.task_org_profiles FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();

-- TRIGGER: template_revisions management_template_revision_search
CREATE TRIGGER management_template_revision_search AFTER INSERT ON public.template_revisions FOR EACH ROW EXECUTE FUNCTION public.management_template_revision_search();

-- TRIGGER: templates management_template_root_guard
CREATE TRIGGER management_template_root_guard BEFORE INSERT OR UPDATE ON public.templates FOR EACH ROW EXECUTE FUNCTION public.management_template_root_guard();

-- TRIGGER: task_templates management_template_selection_guard
CREATE TRIGGER management_template_selection_guard AFTER INSERT OR UPDATE ON public.task_templates FOR EACH ROW EXECUTE FUNCTION public.management_template_selection_guard();

-- TRIGGER: task_templates management_template_task_lock
CREATE TRIGGER management_template_task_lock BEFORE INSERT OR DELETE OR UPDATE ON public.task_templates FOR EACH ROW EXECUTE FUNCTION public.management_feature_task_lock();

-- TRIGGER: memory_call_inputs memory_call_gate
CREATE TRIGGER memory_call_gate BEFORE INSERT OR DELETE OR UPDATE ON public.memory_call_inputs FOR EACH ROW EXECUTE FUNCTION public.memory_call_gate();

-- TRIGGER: response_card_revisions memory_card_confirmation_gate
CREATE CONSTRAINT TRIGGER memory_card_confirmation_gate AFTER INSERT ON public.response_card_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.memory_card_confirmation_gate();

-- TRIGGER: memory_scope_epochs memory_epoch_gate
CREATE TRIGGER memory_epoch_gate BEFORE DELETE OR UPDATE ON public.memory_scope_epochs FOR EACH ROW EXECUTE FUNCTION public.memory_epoch_gate();

-- TRIGGER: memory_eval_samples memory_eval_gate
CREATE TRIGGER memory_eval_gate BEFORE INSERT OR DELETE OR UPDATE ON public.memory_eval_samples FOR EACH ROW EXECUTE FUNCTION public.memory_eval_gate();

-- TRIGGER: memory_feedback_events memory_feedback_gate
CREATE TRIGGER memory_feedback_gate BEFORE INSERT ON public.memory_feedback_events FOR EACH ROW EXECUTE FUNCTION public.memory_feedback_gate();

-- TRIGGER: memory_feedback_events memory_immutable
CREATE TRIGGER memory_immutable BEFORE DELETE OR UPDATE ON public.memory_feedback_events FOR EACH ROW EXECUTE FUNCTION public.memory_immutable_gate();

-- TRIGGER: memory_retrieval_items memory_immutable
CREATE TRIGGER memory_immutable BEFORE DELETE OR UPDATE ON public.memory_retrieval_items FOR EACH ROW EXECUTE FUNCTION public.memory_immutable_gate();

-- TRIGGER: memory_retrievals memory_immutable
CREATE TRIGGER memory_immutable BEFORE DELETE OR UPDATE ON public.memory_retrievals FOR EACH ROW EXECUTE FUNCTION public.memory_immutable_gate();

-- TRIGGER: memory_revisions memory_immutable
CREATE TRIGGER memory_immutable BEFORE DELETE OR UPDATE ON public.memory_revisions FOR EACH ROW EXECUTE FUNCTION public.memory_immutable_gate();

-- TRIGGER: memories memory_parent_gate
CREATE TRIGGER memory_parent_gate AFTER INSERT OR DELETE OR UPDATE ON public.memories FOR EACH ROW EXECUTE FUNCTION public.memory_parent_gate();

-- TRIGGER: memory_retrievals memory_retrieval_gate
CREATE TRIGGER memory_retrieval_gate BEFORE INSERT ON public.memory_retrievals FOR EACH ROW EXECUTE FUNCTION public.memory_retrieval_gate();

-- TRIGGER: memory_retrieval_items memory_retrieval_item_gate
CREATE TRIGGER memory_retrieval_item_gate BEFORE INSERT ON public.memory_retrieval_items FOR EACH ROW EXECUTE FUNCTION public.memory_retrieval_item_gate();

-- TRIGGER: memory_revisions memory_revision_complete
CREATE CONSTRAINT TRIGGER memory_revision_complete AFTER INSERT ON public.memory_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.memory_revision_complete();

-- TRIGGER: memory_revisions memory_revision_gate
CREATE TRIGGER memory_revision_gate BEFORE INSERT ON public.memory_revisions FOR EACH ROW EXECUTE FUNCTION public.memory_revision_gate();

-- TRIGGER: platform_credentials platform_credential_row_gate
CREATE TRIGGER platform_credential_row_gate BEFORE DELETE OR UPDATE ON public.platform_credentials FOR EACH ROW EXECUTE FUNCTION public.platform_credential_row_gate();

-- TRIGGER: jobs provider_job_gate
CREATE TRIGGER provider_job_gate BEFORE INSERT OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.provider_job_gate();

-- TRIGGER: provider_configs provider_revision_gate
CREATE TRIGGER provider_revision_gate AFTER INSERT OR DELETE OR UPDATE ON public.provider_configs FOR EACH ROW EXECUTE FUNCTION public.provider_revision_gate();

-- TRIGGER: usage_records provider_usage_gate
CREATE TRIGGER provider_usage_gate BEFORE INSERT OR UPDATE ON public.usage_records FOR EACH ROW EXECUTE FUNCTION public.provider_usage_gate();

-- TRIGGER: chunks requirement_chunk_invalidate
CREATE TRIGGER requirement_chunk_invalidate AFTER UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();

-- TRIGGER: documents requirement_document_invalidate
CREATE TRIGGER requirement_document_invalidate AFTER UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();

-- TRIGGER: draft_runs requirement_draft_gate
CREATE CONSTRAINT TRIGGER requirement_draft_gate AFTER INSERT ON public.draft_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.requirement_draft_gate();

-- TRIGGER: requirement_reviews requirement_event_pointer
CREATE CONSTRAINT TRIGGER requirement_event_pointer AFTER INSERT OR UPDATE ON public.requirement_reviews DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.requirement_event_pointer();

-- TRIGGER: jobs requirement_manual_job_guard
CREATE TRIGGER requirement_manual_job_guard BEFORE INSERT OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.requirement_manual_job_guard();

-- TRIGGER: response_card_revisions requirement_response_gate
CREATE TRIGGER requirement_response_gate BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.requirement_response_gate();

-- TRIGGER: requirement_review_events requirement_review_guard
CREATE TRIGGER requirement_review_guard BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_events FOR EACH ROW EXECUTE FUNCTION public.requirement_review_guard();

-- TRIGGER: requirement_review_requests requirement_review_guard
CREATE TRIGGER requirement_review_guard BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_requests FOR EACH ROW EXECUTE FUNCTION public.requirement_review_guard();

-- TRIGGER: requirement_review_sets requirement_review_guard
CREATE TRIGGER requirement_review_guard BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_sets FOR EACH ROW EXECUTE FUNCTION public.requirement_review_guard();

-- TRIGGER: requirement_reviews requirement_review_guard
CREATE TRIGGER requirement_review_guard BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_reviews FOR EACH ROW EXECUTE FUNCTION public.requirement_review_guard();

-- TRIGGER: requirements requirement_source_invalidate
CREATE TRIGGER requirement_source_invalidate AFTER UPDATE ON public.requirements FOR EACH ROW EXECUTE FUNCTION public.requirement_source_invalidate();

-- TRIGGER: response_cards response_card_gate
CREATE TRIGGER response_card_gate BEFORE INSERT OR DELETE OR UPDATE ON public.response_cards FOR EACH ROW EXECUTE FUNCTION public.response_card_gate();

-- TRIGGER: response_card_revisions response_card_revisions_immutable
CREATE TRIGGER response_card_revisions_immutable BEFORE DELETE OR UPDATE ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: draft_runs response_draft_complete
CREATE CONSTRAINT TRIGGER response_draft_complete AFTER INSERT ON public.draft_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.response_draft_complete();

-- TRIGGER: draft_runs response_draft_run_gate
CREATE TRIGGER response_draft_run_gate BEFORE INSERT ON public.draft_runs FOR EACH ROW EXECUTE FUNCTION public.response_run_gate();

-- TRIGGER: evidence response_evidence_confirmation_complete
CREATE CONSTRAINT TRIGGER response_evidence_confirmation_complete AFTER UPDATE ON public.evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.response_evidence_confirmation_complete();

-- TRIGGER: evidence response_evidence_gate
CREATE TRIGGER response_evidence_gate BEFORE INSERT OR DELETE OR UPDATE ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.response_evidence_gate();

-- TRIGGER: response_card_revisions response_generation_revision_gate
CREATE CONSTRAINT TRIGGER response_generation_revision_gate AFTER INSERT ON public.response_card_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.response_generation_revision_gate();

-- TRIGGER: response_items response_item_complete
CREATE CONSTRAINT TRIGGER response_item_complete AFTER INSERT ON public.response_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.response_item_gate();

-- TRIGGER: response_items response_item_gate
CREATE TRIGGER response_item_gate BEFORE INSERT ON public.response_items FOR EACH ROW EXECUTE FUNCTION public.response_item_gate();

-- TRIGGER: response_items response_items_immutable
CREATE TRIGGER response_items_immutable BEFORE DELETE OR UPDATE ON public.response_items FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: card_evidence_links response_link_gate
CREATE TRIGGER response_link_gate BEFORE INSERT ON public.card_evidence_links FOR EACH ROW EXECUTE FUNCTION public.response_link_gate();

-- TRIGGER: card_generation_runs response_model_run_gate
CREATE TRIGGER response_model_run_gate BEFORE INSERT ON public.card_generation_runs FOR EACH ROW EXECUTE FUNCTION public.response_run_gate();

-- TRIGGER: response_card_revisions response_revision_complete
CREATE CONSTRAINT TRIGGER response_revision_complete AFTER INSERT ON public.response_card_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.response_revision_complete();

-- TRIGGER: response_card_revisions response_revision_gate
CREATE TRIGGER response_revision_gate BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.response_revision_gate();

-- TRIGGER: response_card_revisions response_revision_quote_gate
CREATE TRIGGER response_revision_quote_gate BEFORE INSERT ON public.response_card_revisions FOR EACH ROW EXECUTE FUNCTION public.response_revision_quote_gate();

-- TRIGGER: score_rubric_coverage rubric_child_gate
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON public.score_rubric_coverage FOR EACH ROW EXECUTE FUNCTION public.rubric_child_gate();

-- TRIGGER: score_rubric_items rubric_child_gate
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON public.score_rubric_items FOR EACH ROW EXECUTE FUNCTION public.rubric_child_gate();

-- TRIGGER: score_rubric_sections rubric_child_gate
CREATE TRIGGER rubric_child_gate BEFORE INSERT ON public.score_rubric_sections FOR EACH ROW EXECUTE FUNCTION public.rubric_child_gate();

-- TRIGGER: score_rubric_sets rubric_complete_publication
CREATE CONSTRAINT TRIGGER rubric_complete_publication AFTER INSERT ON public.score_rubric_sets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rubric_publication_complete();

-- TRIGGER: score_rubric_coverage_items rubric_coverage_item_gate
CREATE TRIGGER rubric_coverage_item_gate BEFORE INSERT ON public.score_rubric_coverage_items FOR EACH ROW EXECUTE FUNCTION public.rubric_coverage_item_gate();

-- TRIGGER: score_rubric_classifications rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_classifications FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_coverage rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_coverage FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_coverage_decisions rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_coverage_decisions FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_coverage_items rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_coverage_items FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_decisions rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_items rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_items FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_revision_events rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_revision_events FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_sections rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_sections FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_sets rubric_immutable
CREATE TRIGGER rubric_immutable BEFORE DELETE OR UPDATE ON public.score_rubric_sets FOR EACH ROW EXECUTE FUNCTION public.rubric_immutable_gate();

-- TRIGGER: score_rubric_classifications rubric_review_complete
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON public.score_rubric_classifications DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rubric_review_complete();

-- TRIGGER: score_rubric_coverage_decisions rubric_review_complete
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON public.score_rubric_coverage_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rubric_review_complete();

-- TRIGGER: score_rubric_decisions rubric_review_complete
CREATE CONSTRAINT TRIGGER rubric_review_complete AFTER INSERT ON public.score_rubric_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rubric_review_complete();

-- TRIGGER: score_rubric_classifications rubric_review_gate
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON public.score_rubric_classifications FOR EACH ROW EXECUTE FUNCTION public.rubric_review_gate();

-- TRIGGER: score_rubric_coverage_decisions rubric_review_gate
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON public.score_rubric_coverage_decisions FOR EACH ROW EXECUTE FUNCTION public.rubric_review_gate();

-- TRIGGER: score_rubric_decisions rubric_review_gate
CREATE TRIGGER rubric_review_gate BEFORE INSERT ON public.score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION public.rubric_review_gate();

-- TRIGGER: score_rubric_revision_events rubric_revision_event_gate
CREATE TRIGGER rubric_revision_event_gate BEFORE INSERT ON public.score_rubric_revision_events FOR EACH ROW EXECUTE FUNCTION public.rubric_revision_event_gate();

-- TRIGGER: score_rubric_sections rubric_section_sources_gate
CREATE TRIGGER rubric_section_sources_gate BEFORE INSERT ON public.score_rubric_sections FOR EACH ROW EXECUTE FUNCTION public.rubric_section_sources_gate();

-- TRIGGER: score_rubric_sets rubric_set_gate
CREATE TRIGGER rubric_set_gate BEFORE INSERT ON public.score_rubric_sets FOR EACH ROW EXECUTE FUNCTION public.rubric_set_gate();

-- TRIGGER: score_rubric_decisions rubric_subject_values_gate
CREATE TRIGGER rubric_subject_values_gate BEFORE INSERT ON public.score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION public.rubric_subject_values_gate();

-- TRIGGER: sandbox_artifacts sandbox_artifact_gate
CREATE TRIGGER sandbox_artifact_gate BEFORE INSERT ON public.sandbox_artifacts FOR EACH ROW EXECUTE FUNCTION public.sandbox_artifact_gate();

-- TRIGGER: sandbox_attempts sandbox_attempt_gate
CREATE TRIGGER sandbox_attempt_gate BEFORE INSERT OR UPDATE ON public.sandbox_attempts FOR EACH ROW EXECUTE FUNCTION public.sandbox_attempt_gate();

-- TRIGGER: sandbox_attempts sandbox_complete_gate
CREATE CONSTRAINT TRIGGER sandbox_complete_gate AFTER UPDATE ON public.sandbox_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.sandbox_complete_gate();

-- TRIGGER: sandbox_inputs sandbox_input_gate
CREATE TRIGGER sandbox_input_gate BEFORE INSERT ON public.sandbox_inputs FOR EACH ROW EXECUTE FUNCTION public.sandbox_input_gate();

-- TRIGGER: sandbox_fetch_receipts sandbox_receipt_gate
CREATE TRIGGER sandbox_receipt_gate BEFORE INSERT ON public.sandbox_fetch_receipts FOR EACH ROW EXECUTE FUNCTION public.sandbox_receipt_gate();

-- TRIGGER: sandbox_runs sandbox_run_gate
CREATE TRIGGER sandbox_run_gate BEFORE INSERT ON public.sandbox_runs FOR EACH ROW EXECUTE FUNCTION public.sandbox_run_gate();

-- TRIGGER: score_item_citations score_child_gate
CREATE TRIGGER score_child_gate BEFORE INSERT ON public.score_item_citations FOR EACH ROW EXECUTE FUNCTION public.score_child_gate();

-- TRIGGER: score_report_item_responses score_child_gate
CREATE TRIGGER score_child_gate BEFORE INSERT ON public.score_report_item_responses FOR EACH ROW EXECUTE FUNCTION public.score_child_gate();

-- TRIGGER: score_report_items score_child_gate
CREATE TRIGGER score_child_gate BEFORE INSERT ON public.score_report_items FOR EACH ROW EXECUTE FUNCTION public.score_child_gate();

-- TRIGGER: score_item_citations score_immutable
CREATE TRIGGER score_immutable BEFORE DELETE OR UPDATE ON public.score_item_citations FOR EACH ROW EXECUTE FUNCTION public.score_immutable_gate();

-- TRIGGER: score_report_item_responses score_immutable
CREATE TRIGGER score_immutable BEFORE DELETE OR UPDATE ON public.score_report_item_responses FOR EACH ROW EXECUTE FUNCTION public.score_immutable_gate();

-- TRIGGER: score_report_items score_immutable
CREATE TRIGGER score_immutable BEFORE DELETE OR UPDATE ON public.score_report_items FOR EACH ROW EXECUTE FUNCTION public.score_immutable_gate();

-- TRIGGER: score_reports score_immutable
CREATE TRIGGER score_immutable BEFORE DELETE OR UPDATE ON public.score_reports FOR EACH ROW EXECUTE FUNCTION public.score_immutable_gate();

-- TRIGGER: score_reports score_publication_complete
CREATE CONSTRAINT TRIGGER score_publication_complete AFTER INSERT ON public.score_reports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.score_publication_complete();

-- TRIGGER: score_reports score_report_gate
CREATE TRIGGER score_report_gate BEFORE INSERT ON public.score_reports FOR EACH ROW EXECUTE FUNCTION public.score_report_gate();

-- TRIGGER: prototype_decision_batches screenshot_batch_complete
CREATE CONSTRAINT TRIGGER screenshot_batch_complete AFTER INSERT ON public.prototype_decision_batches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.screenshot_batch_complete();

-- TRIGGER: prototype_evidence_decisions screenshot_batch_complete
CREATE CONSTRAINT TRIGGER screenshot_batch_complete AFTER INSERT ON public.prototype_evidence_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.screenshot_batch_complete();

-- TRIGGER: response_card_revisions screenshot_card_warning_gate
CREATE CONSTRAINT TRIGGER screenshot_card_warning_gate AFTER INSERT ON public.response_card_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.screenshot_card_warning_gate();

-- TRIGGER: prototype_decision_batches screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: prototype_evidence_decisions screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_analysis_inputs screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_analysis_inputs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_analysis_runs screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_analysis_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_assets screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_privacy_reviews screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_prototype_runs screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_prototype_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_renditions screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_search_candidates screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_search_candidates FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_search_runs screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_search_runs FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_suggestions screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_suggestions FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_vendor_archives screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_vendor_archives FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: screenshot_withdrawals screenshot_immutable
CREATE TRIGGER screenshot_immutable BEFORE DELETE OR UPDATE ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.response_immutable_gate();

-- TRIGGER: prototype_decision_batches screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: prototype_evidence_decisions screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_analysis_inputs screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_analysis_inputs FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_analysis_runs screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_analysis_runs FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_assets screenshot_scope
CREATE TRIGGER screenshot_scope AFTER INSERT ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_privacy_reviews screenshot_scope
CREATE TRIGGER screenshot_scope AFTER INSERT ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_prototype_runs screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_prototype_runs FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_renditions screenshot_scope
CREATE TRIGGER screenshot_scope AFTER INSERT ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_search_candidates screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_search_candidates FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_search_runs screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_search_runs FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_suggestions screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_suggestions FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_vendor_archives screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_vendor_archives FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: screenshot_withdrawals screenshot_scope
CREATE TRIGGER screenshot_scope BEFORE INSERT ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.screenshot_scope_gate();

-- TRIGGER: task_certificates screenshot_selection_no_reactivation
CREATE TRIGGER screenshot_selection_no_reactivation BEFORE UPDATE OF active ON public.task_certificates FOR EACH ROW EXECUTE FUNCTION public.screenshot_selection_no_reactivation();

-- TRIGGER: task_features screenshot_selection_no_reactivation
CREATE TRIGGER screenshot_selection_no_reactivation BEFORE UPDATE OF active ON public.task_features FOR EACH ROW EXECUTE FUNCTION public.screenshot_selection_no_reactivation();

-- TRIGGER: task_resources screenshot_selection_no_reactivation
CREATE TRIGGER screenshot_selection_no_reactivation BEFORE UPDATE OF active ON public.task_resources FOR EACH ROW EXECUTE FUNCTION public.screenshot_selection_no_reactivation();

-- TRIGGER: prototype_decision_batches screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.screenshot_batch_gate();

-- TRIGGER: prototype_evidence_decisions screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.screenshot_decision_gate();

-- TRIGGER: screenshot_analysis_inputs screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.screenshot_analysis_inputs FOR EACH ROW EXECUTE FUNCTION public.screenshot_analysis_input_gate();

-- TRIGGER: screenshot_assets screenshot_shape
CREATE TRIGGER screenshot_shape AFTER INSERT ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.screenshot_asset_gate();

-- TRIGGER: screenshot_privacy_reviews screenshot_shape
CREATE TRIGGER screenshot_shape AFTER INSERT ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.screenshot_privacy_gate();

-- TRIGGER: screenshot_prototype_runs screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.screenshot_prototype_runs FOR EACH ROW EXECUTE FUNCTION public.screenshot_prototype_gate();

-- TRIGGER: screenshot_renditions screenshot_shape
CREATE TRIGGER screenshot_shape AFTER INSERT ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.screenshot_rendition_gate();

-- TRIGGER: screenshot_suggestions screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.screenshot_suggestions FOR EACH ROW EXECUTE FUNCTION public.screenshot_suggestion_gate();

-- TRIGGER: screenshot_withdrawals screenshot_shape
CREATE TRIGGER screenshot_shape BEFORE INSERT ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.screenshot_withdraw_gate();

-- TRIGGER: screenshot_vendor_archives screenshot_vendor_shape
CREATE TRIGGER screenshot_vendor_shape BEFORE INSERT ON public.screenshot_vendor_archives FOR EACH ROW EXECUTE FUNCTION public.screenshot_vendor_archive_gate();

-- TRIGGER: screenshot_assets screenshot_vendor_source
CREATE TRIGGER screenshot_vendor_source BEFORE INSERT ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.screenshot_vendor_asset_gate();

-- TRIGGER: card_generation_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.card_generation_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_certificate_items task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_certificate_items FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_certificates task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_certificates FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_decisions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_decisions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_finding_citations task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_finding_citations FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_findings task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_findings FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_items task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_items FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: check_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.check_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: chunks task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: confidential_values task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.confidential_values FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: documents task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: draft_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.draft_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: evidence task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.evidence FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: evidence_sources task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.evidence_sources FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: export_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.export_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: exports task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.exports FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memories task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memories FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memory_call_inputs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memory_call_inputs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memory_eval_samples task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memory_eval_samples FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memory_feedback_events task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memory_feedback_events FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memory_retrievals task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memory_retrievals FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: memory_revisions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.memory_revisions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: prototype_decision_batches task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.prototype_decision_batches FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: prototype_evidence_decisions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.prototype_evidence_decisions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: requirement_review_events task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_events FOR EACH ROW EXECUTE FUNCTION public.requirement_archived_guard();

-- TRIGGER: requirement_review_requests task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_requests FOR EACH ROW EXECUTE FUNCTION public.requirement_archived_guard();

-- TRIGGER: requirement_review_sets task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_review_sets FOR EACH ROW EXECUTE FUNCTION public.requirement_archived_guard();

-- TRIGGER: requirement_reviews task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.requirement_reviews FOR EACH ROW EXECUTE FUNCTION public.requirement_archived_guard();

-- TRIGGER: requirements task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.requirements FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: response_cards task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.response_cards FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: sandbox_artifacts task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.sandbox_artifacts FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: sandbox_attempts task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.sandbox_attempts FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: sandbox_fetch_receipts task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.sandbox_fetch_receipts FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: sandbox_inputs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.sandbox_inputs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: sandbox_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.sandbox_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_item_citations task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_item_citations FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_report_item_responses task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_report_item_responses FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_report_items task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_report_items FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_reports task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_reports FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_classifications task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_classifications FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_coverage task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_coverage FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_coverage_decisions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_coverage_decisions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_coverage_items task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_coverage_items FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_decisions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_decisions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_items task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_items FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_revision_events task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_revision_events FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_sections task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_sections FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: score_rubric_sets task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.score_rubric_sets FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_analysis_inputs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_analysis_inputs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_analysis_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_analysis_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_assets task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_assets FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_privacy_reviews task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_prototype_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_prototype_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_renditions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_search_candidates task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_search_candidates FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_search_runs task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_search_runs FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_suggestions task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_suggestions FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_vendor_archives task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_vendor_archives FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: screenshot_withdrawals task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: task_certificates task_archived_write
CREATE TRIGGER task_archived_write AFTER INSERT OR DELETE OR UPDATE ON public.task_certificates FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: task_features task_archived_write
CREATE TRIGGER task_archived_write AFTER INSERT OR DELETE OR UPDATE ON public.task_features FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: task_org_profiles task_archived_write
CREATE TRIGGER task_archived_write AFTER INSERT OR DELETE OR UPDATE ON public.task_org_profiles FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: task_resources task_archived_write
CREATE TRIGGER task_archived_write BEFORE INSERT OR DELETE OR UPDATE ON public.task_resources FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: task_templates task_archived_write
CREATE TRIGGER task_archived_write AFTER INSERT OR DELETE OR UPDATE ON public.task_templates FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: tasks task_archived_write
CREATE TRIGGER task_archived_write BEFORE DELETE OR UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.task_archived_guard();

-- TRIGGER: attachment_privacy_holds task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.attachment_privacy_holds REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: card_evidence_links task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.card_evidence_links REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_link');

-- TRIGGER: card_generation_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.card_generation_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: certificate_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.certificate_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'certificate_id');

-- TRIGGER: certificates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.certificates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'id');

-- TRIGGER: check_certificate_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_certificate_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_certificates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_certificates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_decisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_decisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_finding_citations task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_finding_citations REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_findings task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_findings REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.check_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: chunks task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.chunks REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: confidential_values task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.confidential_values REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: documents task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.documents REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: draft_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.draft_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: evidence task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.evidence REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: evidence_sources task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.evidence_sources REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: export_render_candidates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.export_render_candidates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_evidence task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.export_run_evidence REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.export_run_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.export_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'render_job_id');

-- TRIGGER: exports task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.exports REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: feature_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.feature_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'feature_id');

-- TRIGGER: features task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.features REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'id');

-- TRIGGER: jobs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.jobs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memories task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memories REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_call_inputs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memory_call_inputs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: memory_eval_samples task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memory_eval_samples REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_feedback_events task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memory_feedback_events REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_retrievals task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memory_retrievals REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.memory_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: org_profile_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.org_profile_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'profile_id');

-- TRIGGER: org_profiles task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.org_profiles REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'id');

-- TRIGGER: product_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.product_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'product_id');

-- TRIGGER: products task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.products REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'id');

-- TRIGGER: prototype_decision_batches task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.prototype_decision_batches REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: prototype_evidence_decisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.prototype_evidence_decisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: requirements task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.requirements REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: response_card_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.response_card_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_revision');

-- TRIGGER: response_cards task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.response_cards REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: sandbox_artifacts task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.sandbox_artifacts REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_attempts task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.sandbox_attempts REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: sandbox_fetch_receipts task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.sandbox_fetch_receipts REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_inputs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.sandbox_inputs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: sandbox_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.sandbox_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_item_citations task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_item_citations REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_item_responses task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_report_item_responses REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_report_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_reports task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_reports REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_rubric_classifications task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_classifications REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_coverage REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_decisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_coverage_decisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_coverage_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_decisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_decisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_items task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_revision_events task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_revision_events REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sections task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_sections REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sets task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.score_rubric_sets REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_analysis_inputs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_analysis_inputs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_analysis_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_analysis_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_assets task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_assets REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_privacy_reviews task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_privacy_reviews REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_prototype_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_prototype_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_renditions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_renditions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_search_candidates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_search_candidates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_search_runs', 'search_run_id', 'job_id');

-- TRIGGER: screenshot_search_runs task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_search_runs REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_suggestions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_suggestions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_vendor_archives task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_vendor_archives REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: screenshot_withdrawals task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.screenshot_withdrawals REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_attachments task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_attachments REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_certificates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_certificates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_features task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_features REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_org_profiles task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_org_profiles REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_resources task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_resources REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_templates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.task_templates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: tasks task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.tasks REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('id');

-- TRIGGER: template_revisions task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.template_revisions REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'template_id');

-- TRIGGER: templates task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.templates REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'id');

-- TRIGGER: usage_records task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.usage_records REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: vendor_calls task_event_delete
CREATE TRIGGER task_event_delete AFTER DELETE ON public.vendor_calls REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: attachment_privacy_holds task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.attachment_privacy_holds REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: bid_preparation_publications task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.bid_preparation_publications REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: bid_submissions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.bid_submissions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: card_evidence_links task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.card_evidence_links REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_link');

-- TRIGGER: card_generation_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.card_generation_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: certificate_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.certificate_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'certificate_id');

-- TRIGGER: certificates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.certificates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'id');

-- TRIGGER: check_certificate_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_certificate_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_certificates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_certificates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_decisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_decisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_finding_citations task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_finding_citations REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_findings task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_findings REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.check_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: chunks task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.chunks REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: confidential_values task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.confidential_values REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: documents task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.documents REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: draft_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.draft_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: evidence task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.evidence REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: evidence_sources task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.evidence_sources REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: export_render_candidates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.export_render_candidates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_evidence task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.export_run_evidence REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.export_run_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.export_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'render_job_id');

-- TRIGGER: exports task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.exports REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: feature_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.feature_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'feature_id');

-- TRIGGER: features task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.features REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'id');

-- TRIGGER: jobs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.jobs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memories task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memories REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_call_inputs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memory_call_inputs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: memory_eval_samples task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memory_eval_samples REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_feedback_events task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memory_feedback_events REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_retrievals task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memory_retrievals REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.memory_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: org_profile_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.org_profile_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'profile_id');

-- TRIGGER: org_profiles task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.org_profiles REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'id');

-- TRIGGER: product_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.product_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'product_id');

-- TRIGGER: products task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.products REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'id');

-- TRIGGER: prototype_decision_batches task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.prototype_decision_batches REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: prototype_evidence_decisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.prototype_evidence_decisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: requirements task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.requirements REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: response_card_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.response_card_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_revision');

-- TRIGGER: response_cards task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.response_cards REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: sandbox_artifacts task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.sandbox_artifacts REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_attempts task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.sandbox_attempts REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: sandbox_fetch_receipts task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.sandbox_fetch_receipts REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_inputs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.sandbox_inputs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: sandbox_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.sandbox_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_item_citations task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_item_citations REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_item_responses task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_report_item_responses REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_report_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_reports task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_reports REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_rubric_classifications task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_classifications REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_coverage REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_decisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_coverage_decisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_coverage_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_decisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_decisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_items task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_revision_events task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_revision_events REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sections task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_sections REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sets task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.score_rubric_sets REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_analysis_inputs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_analysis_inputs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_analysis_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_analysis_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_assets task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_assets REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_privacy_reviews task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_privacy_reviews REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_prototype_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_prototype_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_renditions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_renditions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_search_candidates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_search_candidates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_search_runs', 'search_run_id', 'job_id');

-- TRIGGER: screenshot_search_runs task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_search_runs REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_suggestions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_suggestions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_vendor_archives task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_vendor_archives REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: screenshot_withdrawals task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.screenshot_withdrawals REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_attachments task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_attachments REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_certificates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_certificates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_features task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_features REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_org_profiles task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_org_profiles REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_resources task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_resources REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_templates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.task_templates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: template_revisions task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.template_revisions REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'template_id');

-- TRIGGER: templates task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.templates REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'id');

-- TRIGGER: usage_records task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.usage_records REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: vendor_calls task_event_insert
CREATE TRIGGER task_event_insert AFTER INSERT ON public.vendor_calls REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: attachment_privacy_holds task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.attachment_privacy_holds REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: card_evidence_links task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.card_evidence_links REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_link');

-- TRIGGER: card_generation_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.card_generation_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: certificate_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.certificate_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'certificate_id');

-- TRIGGER: certificates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.certificates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_certificates', 'certificate_id', 'id');

-- TRIGGER: check_certificate_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_certificate_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_certificates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_certificates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_decisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_decisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_finding_citations task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_finding_citations REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_findings task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_findings REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'check_runs', 'report_id', 'job_id');

-- TRIGGER: check_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.check_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: chunks task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.chunks REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: confidential_values task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.confidential_values REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: documents task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.documents REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: draft_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.draft_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: evidence task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.evidence REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: evidence_sources task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.evidence_sources REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: export_render_candidates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.export_render_candidates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_evidence task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.export_run_evidence REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_run_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.export_run_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'export_runs', 'run_id', 'render_job_id');

-- TRIGGER: export_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.export_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'render_job_id');

-- TRIGGER: exports task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.exports REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: feature_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.feature_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'feature_id');

-- TRIGGER: features task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.features REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_features', 'feature_id', 'id');

-- TRIGGER: jobs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.jobs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memories task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memories REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_call_inputs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memory_call_inputs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: memory_eval_samples task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memory_eval_samples REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_feedback_events task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memory_feedback_events REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_retrievals task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memory_retrievals REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: memory_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.memory_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: org_profile_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.org_profile_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'profile_id');

-- TRIGGER: org_profiles task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.org_profiles REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_org_profiles', 'profile_id', 'id');

-- TRIGGER: product_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.product_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'product_id');

-- TRIGGER: products task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.products REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_resources', 'product_id', 'id');

-- TRIGGER: prototype_decision_batches task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.prototype_decision_batches REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: prototype_evidence_decisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.prototype_evidence_decisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: requirements task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.requirements REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: response_card_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.response_card_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('card_revision');

-- TRIGGER: response_cards task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.response_cards REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: sandbox_artifacts task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.sandbox_artifacts REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_attempts task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.sandbox_attempts REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: sandbox_fetch_receipts task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.sandbox_fetch_receipts REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: sandbox_inputs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.sandbox_inputs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: sandbox_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.sandbox_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_item_citations task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_item_citations REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_item_responses task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_report_item_responses REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_report_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_report_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_reports', 'report_id', 'job_id');

-- TRIGGER: score_reports task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_reports REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: score_rubric_classifications task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_classifications REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_coverage REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_decisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_coverage_decisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_coverage_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_coverage_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_decisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_decisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_items task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_revision_events task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_revision_events REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sections task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_sections REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'score_rubric_sets', 'rubric_id', 'job_id');

-- TRIGGER: score_rubric_sets task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.score_rubric_sets REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_analysis_inputs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_analysis_inputs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_analysis_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_analysis_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_assets task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_assets REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_privacy_reviews task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_privacy_reviews REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: screenshot_prototype_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_prototype_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_renditions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_renditions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'generation_job_id');

-- TRIGGER: screenshot_search_candidates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_search_candidates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_search_runs', 'search_run_id', 'job_id');

-- TRIGGER: screenshot_search_runs task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_search_runs REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: screenshot_suggestions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_suggestions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'screenshot_analysis_runs', 'analysis_run_id', 'job_id');

-- TRIGGER: screenshot_vendor_archives task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_vendor_archives REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('derived_job', 'sandbox_runs', 'sandbox_run_id', 'job_id');

-- TRIGGER: screenshot_withdrawals task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.screenshot_withdrawals REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_attachments task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_attachments REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_certificates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_certificates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_features task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_features REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_org_profiles task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_org_profiles REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_resources task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_resources REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: task_templates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.task_templates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', '');

-- TRIGGER: tasks task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.tasks REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('id');

-- TRIGGER: template_revisions task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.template_revisions REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'template_id');

-- TRIGGER: templates task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.templates REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('library', 'task_templates', 'template_id', 'id');

-- TRIGGER: usage_records task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.usage_records REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: vendor_calls task_event_update
CREATE TRIGGER task_event_update AFTER UPDATE ON public.vendor_calls REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.produce_task_events('task_id', 'job_id');

-- TRIGGER: task_events task_events_immutable
CREATE TRIGGER task_events_immutable BEFORE DELETE OR UPDATE ON public.task_events FOR EACH ROW EXECUTE FUNCTION public.task_events_immutable();

-- TRIGGER: jobs task_job_lifecycle
CREATE TRIGGER task_job_lifecycle BEFORE INSERT OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.task_job_lifecycle_guard();

-- TRIGGER: tasks task_redaction_gate
CREATE TRIGGER task_redaction_gate BEFORE INSERT OR UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.response_redaction_gate();

-- TRIGGER: vendor_calls task_vendor_lifecycle
CREATE TRIGGER task_vendor_lifecycle BEFORE INSERT ON public.vendor_calls FOR EACH ROW EXECUTE FUNCTION public.task_job_lifecycle_guard();

-- TRIGGER: usage_records usage_call_consistency
CREATE CONSTRAINT TRIGGER usage_call_consistency AFTER INSERT ON public.usage_records DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.vendor_call_usage_consistency();

-- TRIGGER: vendor_calls vendor_call_guard
CREATE TRIGGER vendor_call_guard BEFORE INSERT OR DELETE OR UPDATE ON public.vendor_calls FOR EACH ROW EXECUTE FUNCTION public.vendor_call_guard();

-- TRIGGER: vendor_calls vendor_call_usage_consistency
CREATE CONSTRAINT TRIGGER vendor_call_usage_consistency AFTER INSERT OR UPDATE ON public.vendor_calls DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.vendor_call_usage_consistency();

-- TRIGGER: jobs z_agent_job_origin
CREATE TRIGGER z_agent_job_origin BEFORE INSERT OR DELETE OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.agent_origin_guard();

-- TRIGGER: annotation_materials zz_annotation_event
CREATE TRIGGER zz_annotation_event AFTER INSERT ON public.annotation_materials FOR EACH ROW EXECUTE FUNCTION public.annotation_event();

-- TRIGGER: annotation_releases zz_annotation_event
CREATE TRIGGER zz_annotation_event AFTER INSERT ON public.annotation_releases FOR EACH ROW EXECUTE FUNCTION public.annotation_event();

-- TRIGGER: screenshot_renditions zz_annotation_publication_complete
CREATE CONSTRAINT TRIGGER zz_annotation_publication_complete AFTER INSERT ON public.screenshot_renditions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.annotation_publication_complete();

-- TRIGGER: requirement_review_events zz_annotation_requirement_dependency
CREATE TRIGGER zz_annotation_requirement_dependency AFTER INSERT ON public.requirement_review_events FOR EACH ROW EXECUTE FUNCTION public.annotation_dependency_event();

-- TRIGGER: attachment_files zz_attachment_file_guard
CREATE TRIGGER zz_attachment_file_guard AFTER INSERT ON public.attachment_files FOR EACH ROW EXECUTE FUNCTION public.attachment_revision_guard();

-- TRIGGER: attachment_privacy_holds zz_attachment_hold_guard
CREATE TRIGGER zz_attachment_hold_guard AFTER INSERT ON public.attachment_privacy_holds FOR EACH ROW EXECUTE FUNCTION public.attachment_hold_guard();

-- TRIGGER: profile_attachment_links zz_attachment_link_guard
CREATE TRIGGER zz_attachment_link_guard AFTER INSERT OR DELETE OR UPDATE ON public.profile_attachment_links FOR EACH ROW EXECUTE FUNCTION public.attachment_link_guard();

-- TRIGGER: attachment_file_parts zz_attachment_parts_disabled
CREATE TRIGGER zz_attachment_parts_disabled AFTER INSERT ON public.attachment_file_parts FOR EACH ROW EXECUTE FUNCTION public.attachment_parts_disabled();

-- TRIGGER: attachment_reviews zz_attachment_review_guard
CREATE TRIGGER zz_attachment_review_guard AFTER INSERT ON public.attachment_reviews FOR EACH ROW EXECUTE FUNCTION public.attachment_review_guard();

-- TRIGGER: attachment_revisions zz_attachment_revision_guard
CREATE TRIGGER zz_attachment_revision_guard AFTER INSERT ON public.attachment_revisions FOR EACH ROW EXECUTE FUNCTION public.attachment_revision_guard();

-- TRIGGER: attachment_archives zz_attachment_root_guard
CREATE TRIGGER zz_attachment_root_guard AFTER INSERT OR DELETE OR UPDATE ON public.attachment_archives FOR EACH ROW EXECUTE FUNCTION public.attachment_root_guard();

-- TRIGGER: screenshot_renditions zz_attachment_screenshot_rendition
CREATE TRIGGER zz_attachment_screenshot_rendition AFTER INSERT ON public.screenshot_renditions FOR EACH ROW EXECUTE FUNCTION public.attachment_screenshot_rendition_guard();

-- TRIGGER: screenshot_privacy_reviews zz_attachment_screenshot_review
CREATE TRIGGER zz_attachment_screenshot_review AFTER INSERT ON public.screenshot_privacy_reviews FOR EACH ROW EXECUTE FUNCTION public.attachment_screenshot_binding_guard();

-- TRIGGER: screenshot_withdrawals zz_attachment_screenshot_withdraw
CREATE TRIGGER zz_attachment_screenshot_withdraw AFTER INSERT ON public.screenshot_withdrawals FOR EACH ROW EXECUTE FUNCTION public.attachment_screenshot_withdraw_guard();

-- TRIGGER: task_attachments zz_attachment_selection_guard
CREATE TRIGGER zz_attachment_selection_guard AFTER INSERT OR DELETE OR UPDATE ON public.task_attachments FOR EACH ROW EXECUTE FUNCTION public.attachment_link_guard();

-- TRIGGER: evidence_sources zz_attachment_source_guard
CREATE TRIGGER zz_attachment_source_guard AFTER INSERT ON public.evidence_sources FOR EACH ROW EXECUTE FUNCTION public.attachment_source_guard();

-- TRIGGER: bid_submission_documents zz_bid_document_actor
CREATE TRIGGER zz_bid_document_actor AFTER INSERT ON public.bid_submission_documents FOR EACH ROW EXECUTE FUNCTION public.bid_review_submission_guard();

-- TRIGGER: bid_document_pages zz_bid_document_page
CREATE TRIGGER zz_bid_document_page AFTER INSERT ON public.bid_document_pages FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_guard();

-- TRIGGER: bid_pdf_validations zz_bid_pdf_validation
CREATE TRIGGER zz_bid_pdf_validation AFTER INSERT ON public.bid_pdf_validations FOR EACH ROW EXECUTE FUNCTION public.bid_signing_inventory_guard();

-- TRIGGER: bid_preparations zz_bid_preparation_actor
CREATE TRIGGER zz_bid_preparation_actor AFTER INSERT ON public.bid_preparations FOR EACH ROW EXECUTE FUNCTION public.bid_review_preparation_guard();

-- TRIGGER: bid_preparation_publications zz_bid_preparation_publication
CREATE TRIGGER zz_bid_preparation_publication AFTER INSERT ON public.bid_preparation_publications FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_guard();

-- TRIGGER: bid_prepared_documents zz_bid_prepared_document
CREATE TRIGGER zz_bid_prepared_document AFTER INSERT ON public.bid_prepared_documents FOR EACH ROW EXECUTE FUNCTION public.bid_review_inventory_guard();

-- TRIGGER: bid_outbound_authorizations zz_bid_privacy_insert
CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.bid_outbound_authorizations FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard();

-- TRIGGER: bid_outbound_authorized_pages zz_bid_privacy_insert
CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.bid_outbound_authorized_pages FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard();

-- TRIGGER: bid_redacted_pages zz_bid_privacy_insert
CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.bid_redacted_pages FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard();

-- TRIGGER: bid_redaction_snapshots zz_bid_privacy_insert
CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.bid_redaction_snapshots FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard();

-- TRIGGER: bid_review_name_lists zz_bid_privacy_insert
CREATE TRIGGER zz_bid_privacy_insert AFTER INSERT ON public.bid_review_name_lists FOR EACH ROW EXECUTE FUNCTION public.bid_privacy_insert_guard();

-- TRIGGER: bid_signing_candidates zz_bid_signing_candidate
CREATE TRIGGER zz_bid_signing_candidate AFTER INSERT ON public.bid_signing_candidates FOR EACH ROW EXECUTE FUNCTION public.bid_signing_inventory_guard();

-- TRIGGER: bid_preparation_trust zz_bid_signing_trust
CREATE TRIGGER zz_bid_signing_trust AFTER INSERT ON public.bid_preparation_trust FOR EACH ROW EXECUTE FUNCTION public.bid_signing_trust_guard();

-- TRIGGER: bid_submissions zz_bid_submission_actor
CREATE TRIGGER zz_bid_submission_actor AFTER INSERT ON public.bid_submissions FOR EACH ROW EXECUTE FUNCTION public.bid_review_submission_guard();

-- TRIGGER: confidential_fields zz_management_confidential_field_guard
CREATE TRIGGER zz_management_confidential_field_guard AFTER UPDATE ON public.confidential_fields FOR EACH ROW EXECUTE FUNCTION public.management_confidential_field_guard();

-- TRIGGER: confidential_field_search_tokens zz_management_confidential_tokens_guard
CREATE TRIGGER zz_management_confidential_tokens_guard AFTER INSERT OR DELETE OR UPDATE ON public.confidential_field_search_tokens FOR EACH ROW EXECUTE FUNCTION public.management_confidential_tokens_guard();

-- TRIGGER: memory_revisions zz_management_memory_initial_projection
CREATE TRIGGER zz_management_memory_initial_projection AFTER INSERT ON public.memory_revisions FOR EACH ROW EXECUTE FUNCTION public.management_memory_initial_projection();

-- TRIGGER: requirement_reviews zz_requirement_review_changed
CREATE TRIGGER zz_requirement_review_changed AFTER INSERT OR UPDATE ON public.requirement_reviews FOR EACH ROW EXECUTE FUNCTION public.requirement_review_changed();

-- TRIGGER: confidential_fields zzz_management_confidential_tokens_refresh
CREATE TRIGGER zzz_management_confidential_tokens_refresh AFTER INSERT OR UPDATE OF label ON public.confidential_fields FOR EACH ROW EXECUTE FUNCTION public.management_confidential_tokens_refresh();

-- TRIGGER: clef_settings platform_clef_row_gate
CREATE TRIGGER platform_clef_row_gate BEFORE INSERT OR DELETE OR UPDATE ON public.clef_settings FOR EACH ROW EXECUTE FUNCTION public.platform_clef_row_gate();
