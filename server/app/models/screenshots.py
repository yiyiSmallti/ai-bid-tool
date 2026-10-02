"""Append-only image provenance; phase B archives use the same tenant graph."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(columns, table, targets=None, **kwargs):
    targets = targets or columns
    return ForeignKeyConstraint(
        ["org_id", *columns], [f"{table}.org_id", *[f"{table}.{c}" for c in targets]], **kwargs
    )


def scope():
    return (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id"),
        fk(["task_id"], "tasks", ["id"]),
        fk(["extraction_job_id", "task_id"], "jobs", ["id", "task_id"]),
    )


class Scoped:
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()


class ScreenshotSearchRun(Scoped, Tenant, Base):
    __tablename__ = "screenshot_search_runs"
    task_resource_id: Mapped[UUID] = mapped_column()
    product_revision_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        *scope(),
        fk(["job_id", "task_id"], "jobs", ["id", "task_id"]),
        fk(
            ["task_resource_id", "task_id", "product_revision_id"],
            "task_resources",
            ["id", "task_id", "product_revision_id"],
        ),
        fk(["product_revision_id"], "product_revisions", ["id"]),
    )


class ScreenshotSearchCandidate(Scoped, Tenant, Base):
    __tablename__ = "screenshot_search_candidates"
    search_run_id: Mapped[UUID] = mapped_column()
    ref: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *scope(),
        fk(["task_id", "search_run_id"], "screenshot_search_runs", ["task_id", "id"]),
        UniqueConstraint("org_id", "search_run_id", "ref"),
    )


class ScreenshotVendorArchive(Scoped, Tenant, Base):
    __tablename__ = "screenshot_vendor_archives"
    task_resource_id: Mapped[UUID] = mapped_column()
    product_revision_id: Mapped[UUID] = mapped_column()
    search_candidate_id: Mapped[UUID | None] = mapped_column()
    content_sha256: Mapped[str] = mapped_column(String(64))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    descriptor: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        *scope(),
        fk(
            ["task_resource_id", "task_id", "product_revision_id"],
            "task_resources",
            ["id", "task_id", "product_revision_id"],
        ),
        fk(["product_revision_id"], "product_revisions", ["id"]),
        fk(["task_id", "search_candidate_id"], "screenshot_search_candidates", ["task_id", "id"]),
    )


class ScreenshotPrototypeRun(Scoped, Tenant, Base):
    __tablename__ = "screenshot_prototype_runs"
    requirement_id: Mapped[UUID] = mapped_column()
    task_feature_id: Mapped[UUID] = mapped_column()
    feature_revision_id: Mapped[UUID] = mapped_column()
    generation_job_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    html_sha256: Mapped[str] = mapped_column(String(64))
    html_storage_key: Mapped[str] = mapped_column(Text)
    source_image_sha256: Mapped[str] = mapped_column(String(64))
    source_image_key: Mapped[str] = mapped_column(Text)
    sandbox_receipt_id: Mapped[str] = mapped_column(String(200))
    sandbox_receipt_sha256: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        *scope(),
        fk(
            ["requirement_id", "task_id", "extraction_job_id"],
            "requirements",
            ["id", "task_id", "job_id"],
        ),
        fk(
            ["task_feature_id", "task_id", "feature_revision_id"],
            "task_features",
            ["id", "task_id", "feature_revision_id"],
        ),
        fk(["feature_revision_id"], "feature_revisions", ["id"]),
        fk(["generation_job_id", "task_id"], "jobs", ["id", "task_id"]),
    )


class ScreenshotAsset(Scoped, Tenant, Base):
    __tablename__ = "screenshot_assets"
    source_kind: Mapped[str] = mapped_column(String(30))
    image_kind: Mapped[str] = mapped_column(String(30))
    origin: Mapped[str] = mapped_column(String(30))
    task_feature_id: Mapped[UUID | None] = mapped_column()
    feature_revision_id: Mapped[UUID | None] = mapped_column()
    task_resource_id: Mapped[UUID | None] = mapped_column()
    product_revision_id: Mapped[UUID | None] = mapped_column()
    evidence_source_id: Mapped[UUID | None] = mapped_column()
    vendor_archive_id: Mapped[UUID | None] = mapped_column()
    prototype_run_id: Mapped[UUID | None] = mapped_column()
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_hash_assurance: Mapped[str] = mapped_column(String(30))
    source_width: Mapped[int] = mapped_column(Integer)
    source_height: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict] = mapped_column(JSONB)
    received_by: Mapped[UUID] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "task_id", "idempotency_key"),
        fk(["received_by"], "memberships", ["user_id"]),
        fk(
            ["task_feature_id", "task_id", "feature_revision_id"],
            "task_features",
            ["id", "task_id", "feature_revision_id"],
        ),
        fk(["feature_revision_id"], "feature_revisions", ["id"]),
        fk(
            ["task_resource_id", "task_id", "product_revision_id"],
            "task_resources",
            ["id", "task_id", "product_revision_id"],
        ),
        fk(["product_revision_id"], "product_revisions", ["id"]),
        fk(["evidence_source_id"], "evidence_sources", ["id"]),
        fk(["task_id", "vendor_archive_id"], "screenshot_vendor_archives", ["task_id", "id"]),
        fk(["task_id", "prototype_run_id"], "screenshot_prototype_runs", ["task_id", "id"]),
        CheckConstraint(
            "source_width BETWEEN 1 AND 8192 AND source_height BETWEEN 1 AND 8192 AND source_width::bigint * source_height <= 20000000"
        ),
        CheckConstraint("source_hash_assurance IN ('client_declared','server_verified')"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'"),
    )


class ScreenshotRendition(Scoped, Tenant, Base):
    __tablename__ = "screenshot_renditions"
    asset_id: Mapped[UUID] = mapped_column()
    parent_rendition_id: Mapped[UUID | None] = mapped_column()
    privacy_review_id: Mapped[UUID] = mapped_column()
    source_sha256: Mapped[str] = mapped_column(String(64))
    upload_sha256: Mapped[str] = mapped_column(String(64))
    image_sha256: Mapped[str] = mapped_column(String(64))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSONB)
    mapping: Mapped[dict] = mapped_column(JSONB)
    image: Mapped[dict] = mapped_column(JSONB)
    profile: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(Text)
    generation_job_id: Mapped[UUID | None] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "asset_id", "id"),
        UniqueConstraint("org_id", "asset_id", "id", "image_sha256"),
        UniqueConstraint("org_id", "asset_id", "parent_rendition_id", "plan_sha256", "profile"),
        fk(["task_id", "asset_id"], "screenshot_assets", ["task_id", "id"]),
        fk(["asset_id", "parent_rendition_id"], "screenshot_renditions", ["asset_id", "id"]),
        fk(
            ["asset_id", "privacy_review_id"],
            "screenshot_privacy_reviews",
            ["asset_id", "id"],
            use_alter=True,
            name="screenshot_rendition_review_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        fk(["actor_user_id"], "memberships", ["user_id"]),
        fk(["generation_job_id", "task_id"], "jobs", ["id", "task_id"]),
        CheckConstraint(
            "image_sha256 ~ '^[0-9a-f]{64}$' AND upload_sha256 ~ '^[0-9a-f]{64}$' AND plan_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint("profile IN ('screenshot-markup-v1','prototype-clean-v1')"),
    )


class ScreenshotPrivacyReview(Scoped, Tenant, Base):
    __tablename__ = "screenshot_privacy_reviews"
    asset_id: Mapped[UUID] = mapped_column()
    rendition_id: Mapped[UUID] = mapped_column()
    reviewed_upload_sha256: Mapped[str] = mapped_column(String(64))
    stored_image_sha256: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[UUID] = mapped_column()
    rule_version: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "asset_id"),
        UniqueConstraint("org_id", "asset_id", "id"),
        fk(["task_id", "asset_id"], "screenshot_assets", ["task_id", "id"]),
        fk(
            ["asset_id", "rendition_id", "stored_image_sha256"],
            "screenshot_renditions",
            ["asset_id", "id", "image_sha256"],
        ),
        fk(["reviewed_by"], "memberships", ["user_id"]),
    )


class ScreenshotWithdrawal(Scoped, Tenant, Base):
    __tablename__ = "screenshot_withdrawals"
    asset_id: Mapped[UUID] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    withdrawn_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "asset_id"),
        fk(["task_id", "asset_id"], "screenshot_assets", ["task_id", "id"]),
        fk(["withdrawn_by"], "memberships", ["user_id"]),
    )


class ScreenshotAnalysisRun(Scoped, Tenant, Base):
    __tablename__ = "screenshot_analysis_runs"
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    input_manifest: Mapped[dict] = mapped_column(JSONB)
    completion: Mapped[str] = mapped_column(String(20))
    rejected: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "job_id"),
        fk(["job_id", "task_id"], "jobs", ["id", "task_id"]),
        CheckConstraint("completion IN ('complete','partial')"),
    )


class ScreenshotAnalysisInput(Scoped, Tenant, Base):
    __tablename__ = "screenshot_analysis_inputs"
    analysis_run_id: Mapped[UUID] = mapped_column()
    ref: Mapped[str] = mapped_column(String(80))
    requirement_id: Mapped[UUID | None] = mapped_column()
    asset_id: Mapped[UUID | None] = mapped_column()
    rendition_id: Mapped[UUID | None] = mapped_column()
    privacy_review_id: Mapped[UUID | None] = mapped_column()
    content_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "analysis_run_id", "ref"),
        UniqueConstraint("org_id", "analysis_run_id", "id"),
        fk(["task_id", "analysis_run_id"], "screenshot_analysis_runs", ["task_id", "id"]),
        fk(
            ["requirement_id", "task_id", "extraction_job_id"],
            "requirements",
            ["id", "task_id", "job_id"],
        ),
        fk(["asset_id", "rendition_id"], "screenshot_renditions", ["asset_id", "id"]),
        fk(["asset_id", "privacy_review_id"], "screenshot_privacy_reviews", ["asset_id", "id"]),
        CheckConstraint(
            "(requirement_id IS NOT NULL AND rendition_id IS NULL AND asset_id IS NULL AND privacy_review_id IS NULL) OR (requirement_id IS NULL AND rendition_id IS NOT NULL AND asset_id IS NOT NULL AND privacy_review_id IS NOT NULL)"
        ),
    )


class ScreenshotSuggestion(Scoped, Tenant, Base):
    __tablename__ = "screenshot_suggestions"
    analysis_run_id: Mapped[UUID] = mapped_column()
    image_input_id: Mapped[UUID] = mapped_column()
    requirement_input_id: Mapped[UUID | None] = mapped_column()
    proposal: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        *scope(),
        fk(["task_id", "analysis_run_id"], "screenshot_analysis_runs", ["task_id", "id"]),
        fk(
            ["analysis_run_id", "image_input_id"],
            "screenshot_analysis_inputs",
            ["analysis_run_id", "id"],
        ),
        fk(
            ["analysis_run_id", "requirement_input_id"],
            "screenshot_analysis_inputs",
            ["analysis_run_id", "id"],
        ),
    )


class PrototypeDecisionBatch(Scoped, Tenant, Base):
    __tablename__ = "prototype_decision_batches"
    module_label: Mapped[str] = mapped_column(String(200))
    task_feature_ids: Mapped[list] = mapped_column(JSONB)
    target_manifest: Mapped[list] = mapped_column(JSONB)
    input_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[UUID] = mapped_column()
    decided_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "task_id", "idempotency_key"),
        fk(["decided_by"], "memberships", ["user_id"]),
    )


class PrototypeEvidenceDecision(Scoped, Tenant, Base):
    __tablename__ = "prototype_evidence_decisions"
    batch_id: Mapped[UUID] = mapped_column()
    evidence_id: Mapped[UUID] = mapped_column()
    card_id: Mapped[UUID] = mapped_column()
    card_revision_id: Mapped[UUID] = mapped_column()
    asset_id: Mapped[UUID] = mapped_column()
    rendition_id: Mapped[UUID] = mapped_column()
    image_sha256: Mapped[str] = mapped_column(String(64))
    html_sha256: Mapped[str] = mapped_column(String(64))
    task_feature_id: Mapped[UUID] = mapped_column()
    feature_revision_id: Mapped[UUID] = mapped_column()
    previous_decision_id: Mapped[UUID | None] = mapped_column()
    decision: Mapped[str] = mapped_column(String(10))
    keep_basis: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *scope(),
        UniqueConstraint("org_id", "evidence_id", "id"),
        UniqueConstraint("org_id", "batch_id", "evidence_id"),
        fk(["task_id", "batch_id"], "prototype_decision_batches", ["task_id", "id"]),
        fk(["card_id", "evidence_id"], "evidence", ["card_id", "id"]),
        fk(["card_id", "card_revision_id"], "response_card_revisions", ["card_id", "id"]),
        fk(
            ["asset_id", "rendition_id", "image_sha256"],
            "screenshot_renditions",
            ["asset_id", "id", "image_sha256"],
        ),
        fk(
            ["task_feature_id", "task_id", "feature_revision_id"],
            "task_features",
            ["id", "task_id", "feature_revision_id"],
        ),
        fk(["feature_revision_id"], "feature_revisions", ["id"]),
        fk(
            ["evidence_id", "previous_decision_id"],
            "prototype_evidence_decisions",
            ["evidence_id", "id"],
        ),
        fk(["decided_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "(decision = 'keep' AND keep_basis IS NOT NULL AND keep_basis IN ('already_delivered','will_deliver')) OR (decision = 'replace' AND keep_basis IS NULL)"
        ),
        CheckConstraint(
            "previous_decision_id IS NULL OR (reason IS NOT NULL AND length(btrim(reason)) > 0)"
        ),
    )
