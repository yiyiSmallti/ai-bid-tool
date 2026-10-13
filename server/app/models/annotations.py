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
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class AnnotationMaterial(Base):
    __tablename__ = "annotation_materials"
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    rendition_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rendering: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_pixel_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    root_mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((content_pixel_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((root_mapping_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="annotation_materials_check",
        ),
        UniqueConstraint("rendition_id", name="annotation_materials_rendition_id_key"),
        UniqueConstraint("request_id", name="annotation_materials_request_id_key"),
        UniqueConstraint(
            "task_id",
            "extraction_job_id",
            "id",
            name="annotation_materials_task_id_extraction_job_id_id_key",
        ),
        UniqueConstraint("task_id", "id", name="annotation_materials_task_id_id_key"),
        PrimaryKeyConstraint("id", name="annotation_materials_pkey"),
        ForeignKeyConstraint(
            ["card_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "response_cards.id",
                "response_cards.task_id",
                "response_cards.extraction_job_id",
                "response_cards.requirement_id",
            ],
            name="annotation_materials_card_id_task_id_extraction_job_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_materials_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_materials_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="annotation_materials_requirement_id_task_id_extract_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id", "asset_id", "rendition_id"],
            [
                "screenshot_renditions.task_id",
                "screenshot_renditions.extraction_job_id",
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
            ],
            name="annotation_materials_task_id_extraction_job_id_asse_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id", "requirement_id", "card_id", "request_id"],
            [
                "annotation_requests.task_id",
                "annotation_requests.extraction_job_id",
                "annotation_requests.requirement_id",
                "annotation_requests.card_id",
                "annotation_requests.id",
            ],
            name="annotation_materials_task_id_extraction_job_id_requ_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="annotation_materials_task_id_fkey"),
        Index("annotation_materials_listing", "task_id", "created_at", "id"),
    )


class AnnotationRelease(Base):
    __tablename__ = "annotation_releases"
    annotation_id: Mapped[UUID] = mapped_column(nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_review_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_review_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    rendition_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval: Mapped[dict] = mapped_column(JSONB, nullable=False)
    renderer: Mapped[dict] = mapped_column(JSONB, nullable=False)
    renderer_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    rendering: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_pixel_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    root_mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((approval_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((renderer_identity)::text ~ '^[0-9a-f]{64}$'::text) AND ((content_pixel_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((root_mapping_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="annotation_releases_check1",
        ),
        CheckConstraint(
            "((requirement_review_revision > 0))",
            name="annotation_releases_requirement_review_revision_check",
        ),
        UniqueConstraint(
            "evidence_id",
            "card_revision_id",
            "approval_sha256",
            "renderer_identity",
            name="annotation_releases_evidence_id_card_revision_id_app_key",
        ),
        UniqueConstraint("rendition_id", name="annotation_releases_rendition_id_key"),
        UniqueConstraint("task_id", "id", name="annotation_releases_task_id_id_key"),
        PrimaryKeyConstraint("id", name="annotation_releases_pkey"),
        ForeignKeyConstraint(
            ["card_id", "card_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="annotation_releases_card_id_card_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "evidence_id"],
            ["evidence.card_id", "evidence.id"],
            name="annotation_releases_card_id_evidence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "response_cards.id",
                "response_cards.task_id",
                "response_cards.extraction_job_id",
                "response_cards.requirement_id",
            ],
            name="annotation_releases_card_id_task_id_extraction_job__fkey",
        ),
        ForeignKeyConstraint(
            ["card_revision_id", "evidence_id"],
            ["card_evidence_links.revision_id", "card_evidence_links.evidence_id"],
            name="annotation_releases_card_revision_id_evidence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_releases_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_releases_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="annotation_releases_requirement_id_task_id_extracti_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_review_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "requirement_reviews.id",
                "requirement_reviews.task_id",
                "requirement_reviews.extraction_job_id",
                "requirement_reviews.requirement_id",
            ],
            name="annotation_releases_requirement_review_id_task_id_e_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id", "annotation_id"],
            [
                "annotation_materials.task_id",
                "annotation_materials.extraction_job_id",
                "annotation_materials.id",
            ],
            name="annotation_releases_task_id_extraction_job_id_annot_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id", "asset_id", "rendition_id"],
            [
                "screenshot_renditions.task_id",
                "screenshot_renditions.extraction_job_id",
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
            ],
            name="annotation_releases_task_id_extraction_job_id_asset_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="annotation_releases_task_id_fkey"),
        Index("annotation_releases_listing", "task_id", "created_at", "id"),
        Index("annotation_releases_material_history", "annotation_id", "created_at", "id"),
    )


class AnnotationRequest(Base):
    __tablename__ = "annotation_requests"
    expected_card_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_card_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    renderer: Mapped[dict] = mapped_column(JSONB, nullable=False)
    privacy_attestation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_source_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    asset_id: Mapped[UUID | None] = mapped_column(nullable=True)
    parent_rendition_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    product_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    vendor_archive_id: Mapped[UUID | None] = mapped_column(nullable=True)
    inherited_privacy_review_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((request_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((plan_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((reviewed_source_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="annotation_requests_check",
        ),
        CheckConstraint(
            "(((((source_kind)::text = 'certificate_page'::text) AND (evidence_source_id IS NOT NULL) AND (task_certificate_id IS NOT NULL) AND (certificate_id IS NOT NULL) AND (certificate_revision_id IS NOT NULL) AND (certificate_file_id IS NOT NULL) AND (asset_id IS NULL) AND (parent_rendition_id IS NULL) AND (task_resource_id IS NULL) AND (product_revision_id IS NULL) AND (vendor_archive_id IS NULL) AND (inherited_privacy_review_id IS NULL)) OR (((source_kind)::text = 'vendor_rendition'::text) AND (evidence_source_id IS NULL) AND (task_certificate_id IS NULL) AND (certificate_id IS NULL) AND (certificate_revision_id IS NULL) AND (certificate_file_id IS NULL) AND (asset_id IS NOT NULL) AND (parent_rendition_id IS NOT NULL) AND (task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL) AND (vendor_archive_id IS NOT NULL) AND (inherited_privacy_review_id IS NOT NULL))))",
            name="annotation_requests_check1",
        ),
        CheckConstraint(
            "((expected_card_revision > 0))",
            name="annotation_requests_expected_card_revision_check",
        ),
        UniqueConstraint("job_id", name="annotation_requests_job_id_key"),
        UniqueConstraint("task_id", "request_id"),
        UniqueConstraint(
            "task_id",
            "extraction_job_id",
            "requirement_id",
            "card_id",
            "id",
            name="annotation_requests_task_id_extraction_job_id_requir_key",
        ),
        UniqueConstraint("task_id", "id", name="annotation_requests_task_id_id_key"),
        PrimaryKeyConstraint("id", name="annotation_requests_pkey"),
        ForeignKeyConstraint(
            ["asset_id", "inherited_privacy_review_id"],
            ["screenshot_privacy_reviews.asset_id", "screenshot_privacy_reviews.id"],
            name="annotation_requests_asset_id_inherited_privacy_revi_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "expected_card_revision_id", "expected_card_revision"],
            [
                "response_card_revisions.card_id",
                "response_card_revisions.id",
                "response_card_revisions.revision",
            ],
            name="annotation_requests_card_id_expected_card_revision__fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "response_cards.id",
                "response_cards.task_id",
                "response_cards.extraction_job_id",
                "response_cards.requirement_id",
            ],
            name="annotation_requests_card_id_task_id_extraction_job__fkey",
        ),
        ForeignKeyConstraint(
            [
                "evidence_source_id",
                "task_id",
                "task_certificate_id",
                "certificate_id",
                "certificate_revision_id",
                "certificate_file_id",
            ],
            [
                "evidence_sources.id",
                "evidence_sources.task_id",
                "evidence_sources.task_certificate_id",
                "evidence_sources.certificate_id",
                "evidence_sources.certificate_revision_id",
                "evidence_sources.certificate_file_id",
            ],
            name="annotation_requests_evidence_source_id_task_id_task_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_requests_extraction_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="annotation_requests_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="annotation_requests_requirement_id_task_id_extracti_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id", "asset_id", "parent_rendition_id"],
            [
                "screenshot_renditions.task_id",
                "screenshot_renditions.extraction_job_id",
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
            ],
            name="annotation_requests_task_id_extraction_job_id_asset_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="annotation_requests_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "vendor_archive_id"],
            ["screenshot_vendor_archives.task_id", "screenshot_vendor_archives.id"],
            name="annotation_requests_task_id_vendor_archive_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="annotation_requests_task_resource_id_task_id_produc_fkey",
        ),
        Index("annotation_requests_listing", "task_id", "created_at", "id"),
    )
