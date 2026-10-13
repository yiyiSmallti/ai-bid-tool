from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class PrototypeDecisionBatch(Base):
    __tablename__ = "prototype_decision_batches"
    module_label: Mapped[str] = mapped_column(String(200), nullable=False)
    task_feature_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    target_manifest: Mapped[list] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("task_id", "id", name="prototype_decision_batches_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "idempotency_key",
            name="prototype_decision_batches_task_id_idempotency_key_key",
        ),
        PrimaryKeyConstraint("id", name="prototype_decision_batches_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="prototype_decision_batches_extraction_job_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="prototype_decision_batches_task_id_fkey"
        ),
    )


class PrototypeEvidenceDecision(Base):
    __tablename__ = "prototype_evidence_decisions"
    batch_id: Mapped[UUID] = mapped_column(nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    rendition_id: Mapped[UUID] = mapped_column(nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    html_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_feature_id: Mapped[UUID] = mapped_column(nullable=False)
    feature_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    previous_decision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    keep_basis: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((((decision)::text = 'keep'::text) AND (keep_basis IS NOT NULL) AND ((keep_basis)::text = ANY ((ARRAY['already_delivered'::character varying, 'will_deliver'::character varying])::text[]))) OR (((decision)::text = 'replace'::text) AND (keep_basis IS NULL))))",
            name="prototype_evidence_decisions_check",
        ),
        CheckConstraint(
            "(((previous_decision_id IS NULL) OR ((reason IS NOT NULL) AND (length(btrim(reason)) > 0))))",
            name="prototype_evidence_decisions_check1",
        ),
        UniqueConstraint(
            "batch_id", "evidence_id", name="prototype_evidence_decisions_batch_id_evidence_id_key"
        ),
        UniqueConstraint(
            "evidence_id", "id", name="prototype_evidence_decisions_evidence_id_id_key"
        ),
        UniqueConstraint("task_id", "id", name="prototype_evidence_decisions_task_id_id_key"),
        PrimaryKeyConstraint("id", name="prototype_evidence_decisions_pkey"),
        ForeignKeyConstraint(
            ["asset_id", "rendition_id", "image_sha256"],
            [
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
                "screenshot_renditions.image_sha256",
            ],
            name="prototype_evidence_decisions_asset_id_rendition_id__fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "card_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="prototype_evidence_decisions_card_id_card_revision__fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "evidence_id"],
            ["evidence.card_id", "evidence.id"],
            name="prototype_evidence_decisions_card_id_evidence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "previous_decision_id"],
            ["prototype_evidence_decisions.evidence_id", "prototype_evidence_decisions.id"],
            name="prototype_evidence_decisions_evidence_id_previous_d_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="prototype_evidence_decisions_extraction_job_id_task_fkey",
        ),
        ForeignKeyConstraint(
            ["feature_revision_id"],
            ["feature_revisions.id"],
            name="prototype_evidence_decisions_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_feature_id", "task_id", "feature_revision_id"],
            ["task_features.id", "task_features.task_id", "task_features.feature_revision_id"],
            name="prototype_evidence_decisions_task_feature_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "batch_id"],
            ["prototype_decision_batches.task_id", "prototype_decision_batches.id"],
            name="prototype_evidence_decisions_task_id_batch_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="prototype_evidence_decisions_task_id_fkey"
        ),
    )


class ScreenshotAnalysisInput(Base):
    __tablename__ = "screenshot_analysis_inputs"
    analysis_run_id: Mapped[UUID] = mapped_column(nullable=False)
    ref: Mapped[str] = mapped_column(String(80), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(nullable=True)
    asset_id: Mapped[UUID | None] = mapped_column(nullable=True)
    rendition_id: Mapped[UUID | None] = mapped_column(nullable=True)
    privacy_review_id: Mapped[UUID | None] = mapped_column(nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((requirement_id IS NOT NULL) AND (rendition_id IS NULL) AND (asset_id IS NULL) AND (privacy_review_id IS NULL)) OR ((requirement_id IS NULL) AND (rendition_id IS NOT NULL) AND (asset_id IS NOT NULL) AND (privacy_review_id IS NOT NULL))))",
            name="screenshot_analysis_inputs_check",
        ),
        UniqueConstraint(
            "analysis_run_id", "id", name="screenshot_analysis_inputs_analysis_run_id_id_key"
        ),
        UniqueConstraint(
            "analysis_run_id", "ref", name="screenshot_analysis_inputs_analysis_run_id_ref_key"
        ),
        UniqueConstraint("task_id", "id", name="screenshot_analysis_inputs_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_analysis_inputs_pkey"),
        ForeignKeyConstraint(
            ["asset_id", "privacy_review_id"],
            ["screenshot_privacy_reviews.asset_id", "screenshot_privacy_reviews.id"],
            name="screenshot_analysis_inputs_asset_id_privacy_review__fkey",
        ),
        ForeignKeyConstraint(
            ["asset_id", "rendition_id"],
            ["screenshot_renditions.asset_id", "screenshot_renditions.id"],
            name="screenshot_analysis_inputs_asset_id_rendition_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_analysis_inputs_extraction_job_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="screenshot_analysis_inputs_requirement_id_task_id_e_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "analysis_run_id"],
            ["screenshot_analysis_runs.task_id", "screenshot_analysis_runs.id"],
            name="screenshot_analysis_inputs_task_id_analysis_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_analysis_inputs_task_id_fkey"
        ),
    )


class ScreenshotAnalysisRun(Base):
    __tablename__ = "screenshot_analysis_runs"
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    completion: Mapped[str] = mapped_column(String(20), nullable=False)
    rejected: Mapped[list] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])))",
            name="screenshot_analysis_runs_completion_check",
        ),
        UniqueConstraint("job_id", name="screenshot_analysis_runs_job_id_key"),
        UniqueConstraint("task_id", "id", name="screenshot_analysis_runs_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_analysis_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_analysis_runs_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_analysis_runs_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_analysis_runs_task_id_fkey"
        ),
    )


class ScreenshotAsset(Base):
    __tablename__ = "screenshot_assets"
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    image_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    task_feature_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feature_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    product_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    evidence_source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    vendor_archive_id: Mapped[UUID | None] = mapped_column(nullable=True)
    prototype_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash_assurance: Mapped[str] = mapped_column(String(30), nullable=False)
    source_width: Mapped[int] = mapped_column(Integer, nullable=False)
    source_height: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((source_width >= 1) AND (source_width <= 8192)) AND ((source_height >= 1) AND (source_height <= 8192)) AND (((source_width)::bigint * source_height) <= 20000000)))",
            name="screenshot_assets_check",
        ),
        CheckConstraint(
            "(((source_hash_assurance)::text = ANY ((ARRAY['client_declared'::character varying, 'server_verified'::character varying])::text[])))",
            name="screenshot_assets_source_hash_assurance_check",
        ),
        CheckConstraint(
            "(((source_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="screenshot_assets_source_sha256_check",
        ),
        UniqueConstraint("task_id", "id", name="screenshot_assets_task_id_id_key"),
        UniqueConstraint(
            "task_id", "idempotency_key", name="screenshot_assets_task_id_idempotency_key_key"
        ),
        PrimaryKeyConstraint("id", name="screenshot_assets_pkey"),
        ForeignKeyConstraint(
            ["task_id", "evidence_source_id"],
            ["evidence_sources.task_id", "evidence_sources.id"],
            name="attachment_asset_source_task",
        ),
        ForeignKeyConstraint(
            ["evidence_source_id"],
            ["evidence_sources.id"],
            name="screenshot_assets_evidence_source_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_assets_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["feature_revision_id"],
            ["feature_revisions.id"],
            name="screenshot_assets_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="screenshot_assets_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_feature_id", "task_id", "feature_revision_id"],
            ["task_features.id", "task_features.task_id", "task_features.feature_revision_id"],
            name="screenshot_assets_task_feature_id_task_id_feature_r_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="screenshot_assets_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "prototype_run_id"],
            ["screenshot_prototype_runs.task_id", "screenshot_prototype_runs.id"],
            name="screenshot_assets_task_id_prototype_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "vendor_archive_id"],
            ["screenshot_vendor_archives.task_id", "screenshot_vendor_archives.id"],
            name="screenshot_assets_task_id_vendor_archive_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="screenshot_assets_task_resource_id_task_id_product__fkey",
        ),
        Index(
            "attachment_asset_latest",
            "evidence_source_id",
            column("received_at").desc(),
            column("id").desc(),
            postgresql_where=sql_text("((source_kind)::text = 'attachment_page'::text)"),
        ),
        Index("screenshot_asset_listing", "task_id", "extraction_job_id", "created_at", "id"),
    )


class ScreenshotPrivacyReview(Base):
    __tablename__ = "screenshot_privacy_reviews"
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    rendition_id: Mapped[UUID] = mapped_column(nullable=False)
    reviewed_upload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    annotation_request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attachment_source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    resolved_hold_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((((resolved_hold_id IS NULL) OR (attachment_source_id IS NOT NULL)) AND ((attachment_source_id IS NULL) OR (annotation_request_id IS NULL))))",
            name="attachment_privacy_branch",
        ),
        UniqueConstraint("asset_id", "id", name="screenshot_privacy_reviews_asset_id_id_key"),
        UniqueConstraint("asset_id", name="screenshot_privacy_reviews_asset_id_key"),
        UniqueConstraint("task_id", "id", name="screenshot_privacy_reviews_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_privacy_reviews_pkey"),
        ForeignKeyConstraint(
            ["task_id", "annotation_request_id"],
            ["annotation_requests.task_id", "annotation_requests.id"],
            name="annotation_privacy_request",
        ),
        ForeignKeyConstraint(
            ["task_id", "attachment_source_id", "resolved_hold_id"],
            [
                "attachment_privacy_holds.task_id",
                "attachment_privacy_holds.evidence_source_id",
                "attachment_privacy_holds.id",
            ],
            name="attachment_privacy_hold",
        ),
        ForeignKeyConstraint(
            ["task_id", "attachment_source_id"],
            ["evidence_sources.task_id", "evidence_sources.id"],
            name="attachment_privacy_source",
        ),
        ForeignKeyConstraint(
            ["asset_id", "rendition_id", "stored_image_sha256"],
            [
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
                "screenshot_renditions.image_sha256",
            ],
            name="screenshot_privacy_reviews_asset_id_rendition_id_st_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_privacy_reviews_extraction_job_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "asset_id"],
            ["screenshot_assets.task_id", "screenshot_assets.id"],
            name="screenshot_privacy_reviews_task_id_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_privacy_reviews_task_id_fkey"
        ),
    )


class ScreenshotPrototypeRun(Base):
    __tablename__ = "screenshot_prototype_runs"
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    task_feature_id: Mapped[UUID] = mapped_column(nullable=False)
    feature_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    generation_job_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    html_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    html_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    sandbox_receipt_id: Mapped[str] = mapped_column(String(200), nullable=False)
    sandbox_receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("task_id", "id", name="screenshot_prototype_runs_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_prototype_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_prototype_runs_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["feature_revision_id"],
            ["feature_revisions.id"],
            name="screenshot_prototype_runs_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["generation_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_prototype_runs_generation_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="screenshot_prototype_runs_requirement_id_task_id_ex_fkey",
        ),
        ForeignKeyConstraint(
            ["task_feature_id", "task_id", "feature_revision_id"],
            ["task_features.id", "task_features.task_id", "task_features.feature_revision_id"],
            name="screenshot_prototype_runs_task_feature_id_task_id_f_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_prototype_runs_task_id_fkey"
        ),
    )


class ScreenshotRendition(Base):
    __tablename__ = "screenshot_renditions"
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    parent_rendition_id: Mapped[UUID | None] = mapped_column(nullable=True)
    privacy_review_id: Mapped[UUID] = mapped_column(nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    upload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)
    image: Mapped[dict] = mapped_column(JSONB, nullable=False)
    profile: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    generation_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    renderer_identity: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    provenance_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((((profile)::text <> ALL ((ARRAY['annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[])) OR ((renderer_identity IS NOT NULL) AND (provenance_sha256 IS NOT NULL) AND ((provenance_sha256)::text ~ '^[0-9a-f]{64}$'::text))))",
            name="annotation_profile_identity",
        ),
        CheckConstraint(
            "(((profile)::text = ANY ((ARRAY['screenshot-markup-v1'::character varying, 'prototype-clean-v1'::character varying, 'annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[])))",
            name="annotation_profiles",
        ),
        CheckConstraint(
            "((((image_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((upload_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((plan_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="screenshot_renditions_check",
        ),
        UniqueConstraint(
            "task_id", "extraction_job_id", "asset_id", "id", name="annotation_rendition_scope"
        ),
        UniqueConstraint(
            "asset_id",
            "id",
            "image_sha256",
            name="screenshot_renditions_asset_id_id_image_sha256_key",
        ),
        UniqueConstraint("asset_id", "id", name="screenshot_renditions_asset_id_id_key"),
        UniqueConstraint("task_id", "id", name="screenshot_renditions_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_renditions_pkey"),
        ForeignKeyConstraint(
            ["asset_id", "privacy_review_id"],
            ["screenshot_privacy_reviews.asset_id", "screenshot_privacy_reviews.id"],
            name="screenshot_rendition_review_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["asset_id", "parent_rendition_id"],
            ["screenshot_renditions.asset_id", "screenshot_renditions.id"],
            name="screenshot_renditions_asset_id_parent_rendition_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_renditions_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["generation_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_renditions_generation_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "asset_id"],
            ["screenshot_assets.task_id", "screenshot_assets.id"],
            name="screenshot_renditions_task_id_asset_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="screenshot_renditions_task_id_fkey"),
        Index(
            "screenshot_rendition_annotation_identity",
            "asset_id",
            "parent_rendition_id",
            "plan_sha256",
            "profile",
            "renderer_identity",
            "provenance_sha256",
            unique=True,
            postgresql_where=sql_text(
                "((profile)::text = ANY ((ARRAY['annotation-candidate-v1'::character varying, 'annotation-release-v1'::character varying])::text[]))"
            ),
        ),
        Index(
            "screenshot_rendition_legacy_identity",
            "asset_id",
            "parent_rendition_id",
            "plan_sha256",
            "profile",
            unique=True,
            postgresql_where=sql_text(
                "((profile)::text = ANY ((ARRAY['screenshot-markup-v1'::character varying, 'prototype-clean-v1'::character varying])::text[]))"
            ),
        ),
        Index(
            "screenshot_single_root",
            "asset_id",
            unique=True,
            postgresql_where=sql_text("(parent_rendition_id IS NULL)"),
        ),
    )


class ScreenshotSearchCandidate(Base):
    __tablename__ = "screenshot_search_candidates"
    search_run_id: Mapped[UUID] = mapped_column(nullable=False)
    ref: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint(
            "search_run_id", "ref", name="screenshot_search_candidates_search_run_id_ref_key"
        ),
        UniqueConstraint("task_id", "id", name="screenshot_search_candidates_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_search_candidates_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_search_candidates_extraction_job_id_task_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_search_candidates_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "search_run_id"],
            ["screenshot_search_runs.task_id", "screenshot_search_runs.id"],
            name="screenshot_search_candidates_task_id_search_run_id_fkey",
        ),
    )


class ScreenshotSearchRun(Base):
    __tablename__ = "screenshot_search_runs"
    task_resource_id: Mapped[UUID] = mapped_column(nullable=False)
    product_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("task_id", "id", name="screenshot_search_runs_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_search_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_search_runs_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_search_runs_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="screenshot_search_runs_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="screenshot_search_runs_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="screenshot_search_runs_task_resource_id_task_id_pro_fkey",
        ),
    )


class ScreenshotSuggestion(Base):
    __tablename__ = "screenshot_suggestions"
    analysis_run_id: Mapped[UUID] = mapped_column(nullable=False)
    image_input_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_input_id: Mapped[UUID | None] = mapped_column(nullable=True)
    proposal: Mapped[dict] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("task_id", "id", name="screenshot_suggestions_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_suggestions_pkey"),
        ForeignKeyConstraint(
            ["analysis_run_id", "image_input_id"],
            ["screenshot_analysis_inputs.analysis_run_id", "screenshot_analysis_inputs.id"],
            name="screenshot_suggestions_analysis_run_id_image_input__fkey",
        ),
        ForeignKeyConstraint(
            ["analysis_run_id", "requirement_input_id"],
            ["screenshot_analysis_inputs.analysis_run_id", "screenshot_analysis_inputs.id"],
            name="screenshot_suggestions_analysis_run_id_requirement__fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_suggestions_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "analysis_run_id"],
            ["screenshot_analysis_runs.task_id", "screenshot_analysis_runs.id"],
            name="screenshot_suggestions_task_id_analysis_run_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="screenshot_suggestions_task_id_fkey"),
        Index("screenshot_suggestion_listing", "analysis_run_id", "created_at", "id"),
    )


class ScreenshotVendorArchive(Base):
    __tablename__ = "screenshot_vendor_archives"
    task_resource_id: Mapped[UUID] = mapped_column(nullable=False)
    product_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    search_candidate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    sandbox_run_id: Mapped[UUID] = mapped_column(nullable=False)
    archive_artifact_id: Mapped[UUID] = mapped_column(nullable=False)
    entry_receipt_id: Mapped[UUID] = mapped_column(nullable=False)
    __table_args__ = (
        UniqueConstraint("sandbox_run_id", name="screenshot_vendor_archive_single"),
        UniqueConstraint("task_id", "id", name="screenshot_vendor_archives_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_vendor_archives_pkey"),
        ForeignKeyConstraint(
            ["archive_artifact_id"],
            ["sandbox_artifacts.id"],
            name="screenshot_vendor_archive_artifact",
        ),
        ForeignKeyConstraint(
            ["entry_receipt_id"],
            ["sandbox_fetch_receipts.id"],
            name="screenshot_vendor_archive_entry",
        ),
        ForeignKeyConstraint(
            ["sandbox_run_id"], ["sandbox_runs.id"], name="screenshot_vendor_archive_run"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_vendor_archives_extraction_job_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="screenshot_vendor_archives_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="screenshot_vendor_archives_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "search_candidate_id"],
            ["screenshot_search_candidates.task_id", "screenshot_search_candidates.id"],
            name="screenshot_vendor_archives_task_id_search_candidate_fkey",
        ),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="screenshot_vendor_archives_task_resource_id_task_id_fkey",
        ),
    )


class ScreenshotWithdrawal(Base):
    __tablename__ = "screenshot_withdrawals"
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("asset_id", name="screenshot_withdrawals_asset_id_key"),
        UniqueConstraint("task_id", "id", name="screenshot_withdrawals_task_id_id_key"),
        PrimaryKeyConstraint("id", name="screenshot_withdrawals_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="screenshot_withdrawals_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "asset_id"],
            ["screenshot_assets.task_id", "screenshot_assets.id"],
            name="screenshot_withdrawals_task_id_asset_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="screenshot_withdrawals_task_id_fkey"),
    )
