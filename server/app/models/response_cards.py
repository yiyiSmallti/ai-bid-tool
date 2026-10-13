from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class CardEvidenceLink(Base):
    __tablename__ = "card_evidence_links"
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    revision_id: Mapped[UUID] = mapped_column(nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint(
            "revision_id", "evidence_id", name="card_evidence_links_revision_id_evidence_id_key"
        ),
        PrimaryKeyConstraint("id", name="card_evidence_links_pkey"),
        ForeignKeyConstraint(
            ["card_id", "evidence_id"],
            ["evidence.card_id", "evidence.id"],
            name="card_evidence_links_card_id_evidence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="card_evidence_links_card_id_revision_id_fkey",
        ),
    )


class CardGenerationRun(Base):
    __tablename__ = "card_generation_runs"
    target_revisions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_redaction_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_redaction_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    redaction_rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    generation_job_id: Mapped[UUID] = mapped_column(nullable=False)
    generation_run_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((input_hash)::text ~ '^[0-9a-f]{64}$'::text))", name="card_generation_runs_gate_0"
        ),
        CheckConstraint("((model_redaction_revision > 0))", name="card_generation_runs_gate_1"),
        CheckConstraint(
            "(((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(target_revisions) = 'object'::text) AND (jsonb_typeof(result) = 'object'::text)))",
            name="card_generation_runs_gate_2",
        ),
        CheckConstraint(
            "(((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[])))",
            name="card_generation_runs_gate_3",
        ),
        UniqueConstraint("generation_job_id", name="card_generation_runs_generation_job_id_key"),
        PrimaryKeyConstraint("id", name="card_generation_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="card_generation_runs_extraction_job_id_binding",
        ),
        ForeignKeyConstraint(
            ["generation_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="card_generation_runs_generation_job_id_binding",
        ),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="card_generation_runs_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id"], ["jobs.id"], name="card_generation_runs_extraction_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["generation_job_id"], ["jobs.id"], name="card_generation_runs_generation_job_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="card_generation_runs_task_id_fkey"),
    )


class DraftRun(Base):
    __tablename__ = "draft_runs"
    completion: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    generation_job_id: Mapped[UUID] = mapped_column(nullable=False)
    generation_run_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((input_hash)::text ~ '^[0-9a-f]{64}$'::text))", name="draft_runs_gate_0"
        ),
        CheckConstraint(
            "(((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])))",
            name="draft_runs_gate_1",
        ),
        CheckConstraint(
            "(((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text)))",
            name="draft_runs_gate_2",
        ),
        CheckConstraint(
            "(((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[])))",
            name="draft_runs_gate_3",
        ),
        UniqueConstraint("id", "task_id", "extraction_job_id", name="check_draft_extraction"),
        UniqueConstraint("id", "task_id", name="check_draft_task"),
        UniqueConstraint("generation_job_id", name="draft_runs_generation_job_id_key"),
        UniqueConstraint(
            "task_id",
            "extraction_job_id",
            "input_hash",
            name="draft_runs_task_id_extraction_job_id_input_hash_key",
        ),
        PrimaryKeyConstraint("id", name="draft_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="draft_runs_extraction_job_id_binding",
        ),
        ForeignKeyConstraint(
            ["generation_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="draft_runs_generation_job_id_binding",
        ),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="draft_runs_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id"], ["jobs.id"], name="draft_runs_extraction_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["generation_job_id"], ["jobs.id"], name="draft_runs_generation_job_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="draft_runs_task_id_fkey"),
    )


class Evidence(Base):
    __tablename__ = "evidence"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    task_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    product_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_feature_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feature_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_org_profile_id: Mapped[UUID | None] = mapped_column(nullable=True)
    profile_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    evidence_source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    quote_check: Mapped[str] = mapped_column(String(40), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    screenshot_asset_id: Mapped[UUID | None] = mapped_column(nullable=True)
    screenshot_rendition_id: Mapped[UUID | None] = mapped_column(nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)
    claim_scope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    visual_observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['product'::character varying, 'feature'::character varying, 'certificate'::character varying, 'org_profile'::character varying, 'certificate_pdf_page'::character varying, 'image_region'::character varying])::text[])))",
            name="evidence_gate_0",
        ),
        CheckConstraint(
            "(((((kind)::text = 'image_region'::text) AND (quote IS NULL)) OR (((kind)::text <> 'image_region'::text) AND (quote IS NOT NULL) AND (length(quote) <= 20000) AND (quote ~ '[^[:space:]]'::text))))",
            name="evidence_gate_1",
        ),
        CheckConstraint(
            "((((((kind)::text = 'certificate_pdf_page'::text) AND (evidence_source_id IS NOT NULL) AND (field_path IS NULL) AND (page IS NOT NULL) AND (page > 0) AND (source_sha256 IS NOT NULL) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((material_kind)::text = 'user_supplied_pdf_page'::text) AND ((quote_check)::text = ANY ((ARRAY['unreviewed_page'::character varying, 'human_page_review'::character varying])::text[])) AND (screenshot_asset_id IS NULL) AND (screenshot_rendition_id IS NULL) AND (image_sha256 IS NULL) AND (region IS NULL) AND (claim_scope IS NULL) AND (visual_observation IS NULL)) OR (((kind)::text = ANY ((ARRAY['product'::character varying, 'feature'::character varying, 'certificate'::character varying, 'org_profile'::character varying])::text[])) AND (evidence_source_id IS NULL) AND (field_path IS NOT NULL) AND ((field_path)::text ~ '[^[:space:]]'::text) AND (page IS NULL) AND (source_sha256 IS NULL) AND ((material_kind)::text = 'declaration'::text) AND ((quote_check)::text = 'exact_field_match'::text) AND (screenshot_asset_id IS NULL) AND (screenshot_rendition_id IS NULL) AND (image_sha256 IS NULL) AND (region IS NULL) AND (claim_scope IS NULL) AND (visual_observation IS NULL)) OR (((kind)::text = 'image_region'::text) AND (field_path IS NULL) AND (screenshot_asset_id IS NOT NULL) AND (screenshot_rendition_id IS NOT NULL) AND (image_sha256 IS NOT NULL) AND ((image_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (source_sha256 IS NOT NULL) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((((material_kind)::text = 'certificate_image'::text) AND (evidence_source_id IS NOT NULL) AND (page IS NOT NULL) AND (page > 0)) OR (((material_kind)::text <> 'certificate_image'::text) AND (evidence_source_id IS NULL) AND (page IS NULL))) AND (region IS NOT NULL) AND (jsonb_typeof(region) = 'object'::text) AND (region ?& ARRAY['x'::text, 'y'::text, 'width'::text, 'height'::text]) AND ((region - ARRAY['x'::text, 'y'::text, 'width'::text, 'height'::text]) = '{}'::jsonb) AND ((region ->> 'x'::text) ~ '^[0-9]+$'::text) AND ((region ->> 'y'::text) ~ '^[0-9]+$'::text) AND ((region ->> 'width'::text) ~ '^[1-9][0-9]*$'::text) AND ((region ->> 'height'::text) ~ '^[1-9][0-9]*$'::text) AND (((region ->> 'x'::text))::integer <= 8192) AND (((region ->> 'y'::text))::integer <= 8192) AND (((region ->> 'width'::text))::integer <= 8192) AND (((region ->> 'height'::text))::integer <= 8192) AND (claim_scope IS NOT NULL) AND ((claim_scope)::text = ANY ((ARRAY['functional_observation'::character varying, 'design_explanation'::character varying, 'document_excerpt'::character varying, 'hardware_documentation'::character varying])::text[])) AND (visual_observation IS NOT NULL) AND (length(visual_observation) <= 4000) AND (visual_observation ~ '[^[:space:]]'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'user_diagram'::character varying, 'certificate_image'::character varying, 'vendor_web'::character varying, 'vendor_pdf'::character varying, 'prototype'::character varying])::text[])) AND ((quote_check)::text = ANY ((ARRAY['unreviewed_image'::character varying, 'human_image_review'::character varying])::text[])) AND ((((claim_scope)::text = 'functional_observation'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'prototype'::character varying])::text[]))) OR (((claim_scope)::text = 'design_explanation'::text) AND ((material_kind)::text = 'user_diagram'::text)) OR (((claim_scope)::text = 'document_excerpt'::text) AND ((material_kind)::text = 'certificate_image'::text)) OR (((claim_scope)::text = 'hardware_documentation'::text) AND ((material_kind)::text = ANY ((ARRAY['vendor_web'::character varying, 'vendor_pdf'::character varying])::text[])))))) AND (((quote_check)::text <> ALL ((ARRAY['human_page_review'::character varying, 'human_image_review'::character varying])::text[])) OR (confirmed_at IS NOT NULL)) AND ((confirmed_at IS NULL) OR ((quote_check)::text <> ALL ((ARRAY['unreviewed_page'::character varying, 'unreviewed_image'::character varying])::text[])))))",
            name="evidence_gate_10",
        ),
        CheckConstraint(
            "(((((kind)::text = 'product'::text) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = ANY ((ARRAY['vendor_web'::character varying, 'vendor_pdf'::character varying])::text[])))) = ((task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL))))",
            name="evidence_gate_3",
        ),
        CheckConstraint(
            "(((task_resource_id IS NULL) = (product_revision_id IS NULL)))", name="evidence_gate_4"
        ),
        CheckConstraint(
            "(((((kind)::text = 'feature'::text) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = ANY ((ARRAY['user_screenshot'::character varying, 'browser_screenshot'::character varying, 'user_diagram'::character varying, 'prototype'::character varying])::text[])))) = ((task_feature_id IS NOT NULL) AND (feature_revision_id IS NOT NULL))))",
            name="evidence_gate_5",
        ),
        CheckConstraint(
            "(((task_feature_id IS NULL) = (feature_revision_id IS NULL)))", name="evidence_gate_6"
        ),
        CheckConstraint(
            "(((((kind)::text = ANY ((ARRAY['certificate'::character varying, 'certificate_pdf_page'::character varying])::text[])) OR (((kind)::text = 'image_region'::text) AND ((material_kind)::text = 'certificate_image'::text))) = ((task_certificate_id IS NOT NULL) AND (certificate_revision_id IS NOT NULL))))",
            name="evidence_gate_7",
        ),
        CheckConstraint(
            "(((task_certificate_id IS NULL) = (certificate_revision_id IS NULL)))",
            name="evidence_gate_8",
        ),
        CheckConstraint(
            "(((((kind)::text = 'org_profile'::text) = ((task_org_profile_id IS NOT NULL) AND (profile_revision_id IS NOT NULL))) AND ((task_org_profile_id IS NULL) = (profile_revision_id IS NULL))))",
            name="evidence_gate_9",
        ),
        UniqueConstraint("id", "task_id", name="check_evidence_task"),
        UniqueConstraint("card_id", "id", name="evidence_card_id_id_key"),
        PrimaryKeyConstraint("id", name="evidence_pkey"),
        ForeignKeyConstraint(
            ["task_id", "screenshot_asset_id"],
            ["screenshot_assets.task_id", "screenshot_assets.id"],
            name="evidence_image_asset_fk",
        ),
        ForeignKeyConstraint(
            ["screenshot_asset_id", "screenshot_rendition_id", "image_sha256"],
            [
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
                "screenshot_renditions.image_sha256",
            ],
            name="evidence_image_rendition_fk",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id"],
            ["response_cards.id", "response_cards.task_id"],
            name="evidence_card_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["certificate_revision_id"],
            ["certificate_revisions.id"],
            name="evidence_certificate_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["evidence_source_id"], ["evidence_sources.id"], name="evidence_evidence_source_id_fkey"
        ),
        ForeignKeyConstraint(
            ["feature_revision_id"],
            ["feature_revisions.id"],
            name="evidence_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="evidence_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["profile_revision_id"],
            ["org_profile_revisions.id"],
            name="evidence_profile_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_certificate_id"],
            ["task_certificates.id"],
            name="evidence_task_certificate_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_feature_id"], ["task_features.id"], name="evidence_task_feature_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_org_profile_id"],
            ["task_org_profiles.id"],
            name="evidence_task_org_profile_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_resource_id"], ["task_resources.id"], name="evidence_task_resource_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_certificate_id", "task_id", "certificate_revision_id"],
            [
                "task_certificates.id",
                "task_certificates.task_id",
                "task_certificates.certificate_revision_id",
            ],
            name="evidence_task_certificates_binding",
        ),
        ForeignKeyConstraint(
            ["task_feature_id", "task_id", "feature_revision_id"],
            ["task_features.id", "task_features.task_id", "task_features.feature_revision_id"],
            name="evidence_task_features_binding",
        ),
        ForeignKeyConstraint(
            ["task_org_profile_id", "task_id", "profile_revision_id"],
            [
                "task_org_profiles.id",
                "task_org_profiles.task_id",
                "task_org_profiles.profile_revision_id",
            ],
            name="evidence_task_org_profiles_binding",
        ),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="evidence_task_resources_binding",
        ),
    )


class ResponseCardRevision(Base):
    __tablename__ = "response_card_revisions"
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    review_domain: Mapped[str | None] = mapped_column(String(20), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    suggested_disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    deviation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deviation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_hint: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_warning_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    model_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    quote_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        CheckConstraint("((revision > 0))", name="response_card_revisions_gate_0"),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['draft'::character varying, 'pending_review'::character varying, 'confirmed'::character varying, 'rejected'::character varying, 'needs_material'::character varying])::text[])))",
            name="response_card_revisions_gate_1",
        ),
        CheckConstraint(
            "(((deviation_note IS NULL) OR ((length(deviation_note) <= 10000) AND (deviation_note ~ '[^[:space:]]'::text))))",
            name="response_card_revisions_gate_10",
        ),
        CheckConstraint(
            "(((reason IS NULL) OR ((length(reason) <= 10000) AND (reason ~ '[^[:space:]]'::text))))",
            name="response_card_revisions_gate_11",
        ),
        CheckConstraint(
            "((jsonb_typeof(reviewed_warning_codes) = 'array'::text))",
            name="response_card_revisions_gate_12",
        ),
        CheckConstraint(
            "((((origin)::text = ANY ((ARRAY['human'::character varying, 'agent'::character varying, 'model'::character varying])::text[])) AND ((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[]))))",
            name="response_card_revisions_gate_13",
        ),
        CheckConstraint(
            "(((origin)::text = CASE actor_kind WHEN 'session'::text THEN 'human'::text WHEN 'worker'::text THEN 'model'::text ELSE 'agent'::text END))",
            name="response_card_revisions_gate_14",
        ),
        CheckConstraint(
            "((((origin)::text <> 'model'::text) OR (((actor_kind)::text = 'worker'::text) AND (model_job_id IS NOT NULL))))",
            name="response_card_revisions_gate_15",
        ),
        CheckConstraint(
            "(((((state)::text = 'confirmed'::text) AND (confirmed_at IS NOT NULL) AND ((disposition)::text = 'respond'::text) AND (response_kind IS NOT NULL) AND (response_text IS NOT NULL) AND (deviation IS NOT NULL) AND (deviation_note IS NOT NULL) AND (review_domain IS NOT NULL)) OR (((state)::text <> 'confirmed'::text) AND (confirmed_at IS NULL))))",
            name="response_card_revisions_gate_16",
        ),
        CheckConstraint(
            "(((review_domain IS NULL) OR ((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[]))))",
            name="response_card_revisions_gate_2",
        ),
        CheckConstraint(
            "(((disposition IS NULL) OR ((disposition)::text = ANY ((ARRAY['respond'::character varying, 'comply_only'::character varying])::text[]))))",
            name="response_card_revisions_gate_3",
        ),
        CheckConstraint(
            "((((disposition IS NULL) AND (disposition_at IS NULL)) OR ((disposition IS NOT NULL) AND (disposition_at IS NOT NULL))))",
            name="response_card_revisions_gate_4",
        ),
        CheckConstraint(
            "(((response_kind IS NULL) OR ((response_kind)::text = ANY ((ARRAY['evidence'::character varying, 'commitment'::character varying])::text[]))))",
            name="response_card_revisions_gate_5",
        ),
        CheckConstraint(
            "(((suggested_disposition IS NULL) OR ((suggested_disposition)::text = ANY ((ARRAY['respond'::character varying, 'comply_only'::character varying])::text[]))))",
            name="response_card_revisions_gate_6",
        ),
        CheckConstraint(
            "(((deviation IS NULL) OR ((deviation)::text = ANY ((ARRAY['none'::character varying, 'positive'::character varying, 'negative'::character varying])::text[]))))",
            name="response_card_revisions_gate_7",
        ),
        CheckConstraint(
            "(((review_hint IS NULL) OR ((review_hint)::text = 'needs_material'::text)))",
            name="response_card_revisions_gate_8",
        ),
        CheckConstraint(
            "(((response_text IS NULL) OR ((length(response_text) <= 20000) AND (response_text ~ '[^[:space:]]'::text))))",
            name="response_card_revisions_gate_9",
        ),
        CheckConstraint(
            "(((quote_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="response_card_revisions_quote_sha256_check",
        ),
        UniqueConstraint("card_id", "id", name="response_card_revisions_card_id_id_key"),
        UniqueConstraint(
            "card_id", "id", "revision", name="response_card_revisions_card_id_id_revision_key"
        ),
        UniqueConstraint(
            "card_id", "revision", name="response_card_revisions_card_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="response_card_revisions_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"],
            ["api_tokens.id"],
            name="response_card_revisions_actor_token_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id"], ["response_cards.id"], name="response_card_revisions_card_id_fkey"
        ),
        ForeignKeyConstraint(
            ["model_job_id"], ["jobs.id"], name="response_card_revisions_model_job_id_fkey"
        ),
    )


class ResponseCard(Base):
    __tablename__ = "response_cards"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    current_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint("((revision > 0))", name="response_cards_gate_0"),
        UniqueConstraint(
            "id", "task_id", "extraction_job_id", "requirement_id", name="annotation_card_scope"
        ),
        UniqueConstraint("id", "task_id", name="response_cards_id_task_id_key"),
        UniqueConstraint(
            "task_id",
            "extraction_job_id",
            "requirement_id",
            name="response_cards_task_id_extraction_job_id_requirement_key",
        ),
        PrimaryKeyConstraint("id", name="response_cards_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision_id", "revision"],
            [
                "response_card_revisions.card_id",
                "response_card_revisions.id",
                "response_card_revisions.revision",
            ],
            name="response_card_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="response_card_job_binding",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="response_card_requirement_binding",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id"], ["jobs.id"], name="response_cards_extraction_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="response_cards_requirement_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="response_cards_task_id_fkey"),
    )


class ResponseItem(Base):
    __tablename__ = "response_items"
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False)
    card_id: Mapped[UUID | None] = mapped_column(nullable=True)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    table: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    location_label: Mapped[str] = mapped_column(Text, nullable=False)
    response_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    deviation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deviation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gap_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((category)::text = ANY ((ARRAY['technical'::character varying, 'qualification'::character varying, 'substantive'::character varying, 'scoring'::character varying])::text[])))",
            name="response_items_gate_0",
        ),
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['row'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[])))",
            name="response_items_gate_1",
        ),
        CheckConstraint(
            "(((jsonb_typeof(source) = 'object'::text) AND (location_label ~ '[^[:space:]]'::text) AND (jsonb_typeof(gap_reasons) = 'array'::text)))",
            name="response_items_gate_2",
        ),
        CheckConstraint(
            "(((card_id IS NULL) = (card_revision_id IS NULL)))", name="response_items_gate_3"
        ),
        CheckConstraint(
            "(((((kind)::text = 'row'::text) AND (card_id IS NOT NULL) AND (\"table\" IS NOT NULL) AND ((\"table\")::text = ANY ((ARRAY['substantive'::character varying, 'commercial'::character varying, 'technical'::character varying])::text[])) AND (response_kind IS NOT NULL) AND ((response_kind)::text = ANY ((ARRAY['evidence'::character varying, 'commitment'::character varying])::text[])) AND (response_text IS NOT NULL) AND (deviation IS NOT NULL) AND ((deviation)::text = ANY ((ARRAY['none'::character varying, 'positive'::character varying, 'negative'::character varying])::text[])) AND (deviation_note IS NOT NULL) AND (disposition_at IS NULL) AND (jsonb_array_length(gap_reasons) = 0)) OR (((kind)::text = 'comply_only'::text) AND (card_id IS NOT NULL) AND (\"table\" IS NULL) AND (response_kind IS NULL) AND (response_text IS NULL) AND (deviation IS NULL) AND (deviation_note IS NULL) AND (disposition_at IS NOT NULL) AND (jsonb_array_length(gap_reasons) = 0)) OR (((kind)::text = 'gap'::text) AND (\"table\" IS NULL) AND (response_kind IS NULL) AND (response_text IS NULL) AND (deviation IS NULL) AND (deviation_note IS NULL) AND (disposition_at IS NULL) AND (jsonb_array_length(gap_reasons) > 0))))",
            name="response_items_gate_4",
        ),
        UniqueConstraint("id", "draft_id", "requirement_id", name="check_response_requirement"),
        UniqueConstraint("id", "draft_id", "card_revision_id", name="check_response_revision"),
        UniqueConstraint(
            "id", "draft_id", "requirement_id", "kind", name="export_response_items_parent_0"
        ),
        UniqueConstraint(
            "draft_id", "requirement_id", name="response_items_draft_id_requirement_id_key"
        ),
        PrimaryKeyConstraint("id", name="response_items_pkey"),
        UniqueConstraint(
            "id",
            "draft_id",
            "requirement_id",
            "card_revision_id",
            name="score_response_revision_binding",
        ),
        ForeignKeyConstraint(
            ["card_id", "card_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="response_item_card_revision_binding",
        ),
        ForeignKeyConstraint(
            ["card_id"], ["response_cards.id"], name="response_items_card_id_fkey"
        ),
        ForeignKeyConstraint(
            ["card_revision_id"],
            ["response_card_revisions.id"],
            name="response_items_card_revision_id_fkey",
        ),
        ForeignKeyConstraint(["draft_id"], ["draft_runs.id"], name="response_items_draft_id_fkey"),
        ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="response_items_requirement_id_fkey"
        ),
    )
