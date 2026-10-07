"""Immutable cloud annotation inputs, candidate receipts and approved releases."""

from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant
from app.models.screenshots import fk


def scope():
    return (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id"),
        fk(["task_id"], "tasks", ["id"]),
        fk(["extraction_job_id", "task_id"], "jobs", ["id", "task_id"]),
        fk(["job_id", "task_id"], "jobs", ["id", "task_id"]),
        fk(
            ["requirement_id", "task_id", "extraction_job_id"],
            "requirements",
            ["id", "task_id", "job_id"],
        ),
        fk(
            ["card_id", "task_id", "extraction_job_id", "requirement_id"],
            "response_cards",
            ["id", "task_id", "extraction_job_id", "requirement_id"],
        ),
    )


class AnnotationScope:
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    card_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()


class AnnotationRequest(AnnotationScope, Tenant, Base):
    __tablename__ = "annotation_requests"
    expected_card_revision: Mapped[int] = mapped_column(Integer)
    expected_card_revision_id: Mapped[UUID] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    request_sha256: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    reviewed_source_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONB)
    plan: Mapped[dict] = mapped_column(JSONB)
    renderer: Mapped[dict] = mapped_column(JSONB)
    privacy_attestation: Mapped[dict] = mapped_column(JSONB)
    source_kind: Mapped[str] = mapped_column(String(30))
    evidence_source_id: Mapped[UUID | None] = mapped_column()
    task_certificate_id: Mapped[UUID | None] = mapped_column()
    certificate_id: Mapped[UUID | None] = mapped_column()
    certificate_revision_id: Mapped[UUID | None] = mapped_column()
    certificate_file_id: Mapped[UUID | None] = mapped_column()
    asset_id: Mapped[UUID | None] = mapped_column()
    parent_rendition_id: Mapped[UUID | None] = mapped_column()
    task_resource_id: Mapped[UUID | None] = mapped_column()
    product_revision_id: Mapped[UUID | None] = mapped_column()
    vendor_archive_id: Mapped[UUID | None] = mapped_column()
    inherited_privacy_review_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        *scope(),
        Index("annotation_requests_listing", "org_id", "task_id", "created_at", "id"),
        UniqueConstraint("org_id", "task_id", "actor_user_id", "request_id"),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint(
            "org_id", "task_id", "extraction_job_id", "requirement_id", "card_id", "id"
        ),
        fk(["actor_user_id"], "memberships", ["user_id"]),
        fk(
            ["card_id", "expected_card_revision_id", "expected_card_revision"],
            "response_card_revisions",
            ["card_id", "id", "revision"],
        ),
        fk(
            [
                "evidence_source_id",
                "task_id",
                "task_certificate_id",
                "certificate_id",
                "certificate_revision_id",
                "certificate_file_id",
            ],
            "evidence_sources",
            [
                "id",
                "task_id",
                "task_certificate_id",
                "certificate_id",
                "certificate_revision_id",
                "certificate_file_id",
            ],
        ),
        fk(
            ["task_id", "extraction_job_id", "asset_id", "parent_rendition_id"],
            "screenshot_renditions",
            ["task_id", "extraction_job_id", "asset_id", "id"],
        ),
        fk(
            ["task_resource_id", "task_id", "product_revision_id"],
            "task_resources",
            ["id", "task_id", "product_revision_id"],
        ),
        fk(["task_id", "vendor_archive_id"], "screenshot_vendor_archives", ["task_id", "id"]),
        fk(
            ["asset_id", "inherited_privacy_review_id"],
            "screenshot_privacy_reviews",
            ["asset_id", "id"],
        ),
        CheckConstraint("expected_card_revision > 0"),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' AND plan_sha256 ~ '^[0-9a-f]{64}$' AND reviewed_source_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "(source_kind='certificate_page' AND evidence_source_id IS NOT NULL AND task_certificate_id IS NOT NULL AND certificate_id IS NOT NULL AND certificate_revision_id IS NOT NULL AND certificate_file_id IS NOT NULL AND asset_id IS NULL AND parent_rendition_id IS NULL AND task_resource_id IS NULL AND product_revision_id IS NULL AND vendor_archive_id IS NULL AND inherited_privacy_review_id IS NULL) OR (source_kind='vendor_rendition' AND evidence_source_id IS NULL AND task_certificate_id IS NULL AND certificate_id IS NULL AND certificate_revision_id IS NULL AND certificate_file_id IS NULL AND asset_id IS NOT NULL AND parent_rendition_id IS NOT NULL AND task_resource_id IS NOT NULL AND product_revision_id IS NOT NULL AND vendor_archive_id IS NOT NULL AND inherited_privacy_review_id IS NOT NULL)"
        ),
    )


class AnnotationMaterial(AnnotationScope, Tenant, Base):
    __tablename__ = "annotation_materials"
    request_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    asset_id: Mapped[UUID] = mapped_column()
    rendition_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONB)
    rendering: Mapped[dict] = mapped_column(JSONB)
    content_pixel_sha256: Mapped[str] = mapped_column(String(64))
    root_mapping_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope(),
        Index("annotation_materials_listing", "org_id", "task_id", "created_at", "id"),
        UniqueConstraint("org_id", "request_id"),
        UniqueConstraint("org_id", "rendition_id"),
        UniqueConstraint("org_id", "task_id", "extraction_job_id", "id"),
        fk(
            ["task_id", "extraction_job_id", "requirement_id", "card_id", "request_id"],
            "annotation_requests",
            ["task_id", "extraction_job_id", "requirement_id", "card_id", "id"],
        ),
        fk(
            ["task_id", "extraction_job_id", "asset_id", "rendition_id"],
            "screenshot_renditions",
            ["task_id", "extraction_job_id", "asset_id", "id"],
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND content_pixel_sha256 ~ '^[0-9a-f]{64}$' AND root_mapping_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class AnnotationRelease(AnnotationScope, Tenant, Base):
    __tablename__ = "annotation_releases"
    annotation_id: Mapped[UUID] = mapped_column()
    evidence_id: Mapped[UUID] = mapped_column()
    card_revision_id: Mapped[UUID] = mapped_column()
    confirmed_by: Mapped[UUID] = mapped_column()
    review_round_id: Mapped[UUID | None] = mapped_column()
    commercial_signature_id: Mapped[UUID | None] = mapped_column()
    technical_signature_id: Mapped[UUID | None] = mapped_column()
    requirement_review_id: Mapped[UUID] = mapped_column()
    requirement_review_revision: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[UUID] = mapped_column()
    asset_id: Mapped[UUID] = mapped_column()
    rendition_id: Mapped[UUID] = mapped_column()
    approval_sha256: Mapped[str] = mapped_column(String(64))
    approval: Mapped[dict] = mapped_column(JSONB)
    renderer: Mapped[dict] = mapped_column(JSONB)
    renderer_identity: Mapped[str] = mapped_column(String(64))
    rendering: Mapped[dict] = mapped_column(JSONB)
    content_pixel_sha256: Mapped[str] = mapped_column(String(64))
    root_mapping_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *scope(),
        Index(
            "annotation_releases_material_history", "org_id", "annotation_id", "created_at", "id"
        ),
        Index("annotation_releases_listing", "org_id", "task_id", "created_at", "id"),
        UniqueConstraint("org_id", "rendition_id"),
        UniqueConstraint(
            "org_id", "evidence_id", "card_revision_id", "approval_sha256", "renderer_identity"
        ),
        fk(
            ["task_id", "extraction_job_id", "annotation_id"],
            "annotation_materials",
            ["task_id", "extraction_job_id", "id"],
        ),
        fk(["card_id", "evidence_id"], "evidence", ["card_id", "id"]),
        fk(["card_id", "card_revision_id"], "response_card_revisions", ["card_id", "id"]),
        fk(
            ["card_revision_id", "evidence_id"],
            "card_evidence_links",
            ["revision_id", "evidence_id"],
        ),
        fk(
            ["requirement_review_id", "task_id", "extraction_job_id", "requirement_id"],
            "requirement_reviews",
            ["id", "task_id", "extraction_job_id", "requirement_id"],
        ),
        fk(
            ["task_id", "extraction_job_id", "asset_id", "rendition_id"],
            "screenshot_renditions",
            ["task_id", "extraction_job_id", "asset_id", "id"],
        ),
        fk(["confirmed_by"], "memberships", ["user_id"]),
        fk(
            ["task_id", "card_id", "review_round_id"],
            "card_review_rounds",
            ["task_id", "card_id", "id"],
        ),
        fk(
            ["task_id", "card_id", "review_round_id", "commercial_signature_id"],
            "card_review_signatures",
            ["task_id", "card_id", "round_id", "id"],
        ),
        fk(
            ["task_id", "card_id", "review_round_id", "technical_signature_id"],
            "card_review_signatures",
            ["task_id", "card_id", "round_id", "id"],
        ),
        CheckConstraint(
            "review_round_id IS NOT NULL OR (commercial_signature_id IS NULL AND technical_signature_id IS NULL)"
        ),
        CheckConstraint("requirement_review_revision > 0"),
        CheckConstraint(
            "approval_sha256 ~ '^[0-9a-f]{64}$' AND renderer_identity ~ '^[0-9a-f]{64}$' AND content_pixel_sha256 ~ '^[0-9a-f]{64}$' AND root_mapping_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )
