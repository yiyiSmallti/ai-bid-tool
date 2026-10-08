"""Immutable uploaded originals and atomically published local page inventories."""

from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(columns: list[str], table: str, targets: list[str]) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", *columns], [f"{table}.org_id", *[f"{table}.{c}" for c in targets]]
    )


def common() -> tuple[Any, ...]:
    return (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id"),
        fk(["task_id"], "tasks", ["id"]),
    )


def submission_fk() -> ForeignKeyConstraint:
    return fk(["task_id", "submission_id"], "bid_submissions", ["task_id", "id"])


def preparation_fk() -> ForeignKeyConstraint:
    return fk(
        ["task_id", "submission_id", "preparation_id"],
        "bid_preparations",
        ["task_id", "submission_id", "id"],
    )


class BidSubmission(Tenant, Base):
    __tablename__ = "bid_submissions"
    task_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    request_id: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded")
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "task_id", "revision"),
        UniqueConstraint("org_id", "created_by", "request_id"),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint("revision > 0 AND state IN ('uploaded','withdrawn')"),
        CheckConstraint("file_count BETWEEN 2 AND 20 AND total_bytes BETWEEN 2 AND 524288000"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'"),
        Index("bid_submission_page", "org_id", "task_id", "created_at", "id"),
    )


class BidSubmissionDocument(Tenant, Base):
    __tablename__ = "bid_submission_documents"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(30))
    media_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(500))
    upload_name_encrypted: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "task_id", "submission_id", "id"),
        UniqueConstraint("org_id", "submission_id", "ordinal"),
        UniqueConstraint("org_id", "submission_id", "role", "sha256"),
        UniqueConstraint("org_id", "file_id"),
        submission_fk(),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint("ordinal BETWEEN 1 AND 20 AND size_bytes BETWEEN 1 AND 104857600"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND length(upload_name_encrypted)>0"),
        CheckConstraint("role IN ('tender','bid') AND ((role='tender')=(kind='tender'))"),
        CheckConstraint(
            "kind IN ('tender','qualification','commercial_technical','price','declaration','other')"
        ),
        CheckConstraint(
            "media_type IN ('application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
        ),
        CheckConstraint(
            "storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text "
            "|| '/originals/' || file_id::text || '/' || sha256 "
            "|| CASE media_type WHEN 'application/pdf' THEN '.pdf' ELSE '.docx' END"
        ),
    )


class BidPreparation(Tenant, Base):
    __tablename__ = "bid_preparations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "task_id", "submission_id", "id"),
        UniqueConstraint("org_id", "task_id", "submission_id", "id", "job_id", "input_hash"),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint("org_id", "created_by", "request_id"),
        submission_fk(),
        fk(["task_id", "job_id"], "jobs", ["task_id", "id"]),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'"),
        Index("bid_preparation_submission", "org_id", "submission_id", "created_at", "id"),
    )


class BidPreparedDocument(Tenant, Base):
    __tablename__ = "bid_prepared_documents"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    page_count: Mapped[int] = mapped_column(Integer)
    rendered_pdf_sha256: Mapped[str] = mapped_column(String(64))
    rendered_pdf_storage_key: Mapped[str] = mapped_column(String(500))
    render_profile: Mapped[str] = mapped_column(String(100))
    renderer_identity: Mapped[str] = mapped_column(String(200))
    citation_mode: Mapped[str] = mapped_column(String(10))
    parsing_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    signature_field_count: Mapped[int] = mapped_column(Integer, default=0)
    structure_encrypted: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "task_id", "submission_id", "preparation_id", "document_id"),
        preparation_fk(),
        fk(
            ["task_id", "submission_id", "document_id"],
            "bid_submission_documents",
            ["task_id", "submission_id", "id"],
        ),
        CheckConstraint("page_count BETWEEN 1 AND 1000 AND signature_field_count>=0"),
        CheckConstraint(
            "rendered_pdf_sha256 ~ '^[0-9a-f]{64}$' AND citation_mode IN ('page','block')"
        ),
        CheckConstraint("length(render_profile)>0 AND length(renderer_identity)>0"),
        CheckConstraint(
            "jsonb_typeof(parsing_warnings)='array' AND jsonb_array_length(parsing_warnings)<=100"
        ),
    )


class BidDocumentPage(Tenant, Base):
    __tablename__ = "bid_document_pages"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    page: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    original_sha256: Mapped[str] = mapped_column(String(64))
    rendered_pdf_sha256: Mapped[str] = mapped_column(String(64))
    image: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(500))
    render_profile: Mapped[str] = mapped_column(String(100))
    renderer_identity: Mapped[str] = mapped_column(String(200))
    page_kind: Mapped[str] = mapped_column(String(10))
    text_status: Mapped[str] = mapped_column(String(20))
    text_sha256: Mapped[str | None] = mapped_column(String(64))
    text_encrypted: Mapped[str | None] = mapped_column(Text)
    structure_encrypted: Mapped[str | None] = mapped_column(Text)
    price_page: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    redaction_status: Mapped[str] = mapped_column(
        String(20), default="human_only", server_default="human_only"
    )
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "task_id", "submission_id", "id"),
        UniqueConstraint("org_id", "preparation_id", "document_id", "page"),
        fk(
            ["task_id", "submission_id", "preparation_id", "document_id"],
            "bid_prepared_documents",
            ["task_id", "submission_id", "preparation_id", "document_id"],
        ),
        CheckConstraint("page BETWEEN 1 AND 1000 AND role IN ('tender','bid')"),
        CheckConstraint(
            "original_sha256 ~ '^[0-9a-f]{64}$' AND rendered_pdf_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "page_kind IN ('text','image') AND text_status IN ('native','unavailable')"
        ),
        CheckConstraint(
            "(text_status='unavailable' AND text_sha256 IS NULL AND text_encrypted IS NULL) "
            "OR (text_status='native' AND text_sha256 IS NOT NULL AND text_encrypted IS NOT NULL AND text_sha256 ~ '^[0-9a-f]{64}$' AND length(text_encrypted)>0)"
        ),
        CheckConstraint("redaction_status='human_only'"),
        CheckConstraint(
            "jsonb_typeof(image)='object' AND image ?& ARRAY['sha256','size_bytes','width_px','height_px','media_type'] AND image->>'sha256' ~ '^[0-9a-f]{64}$' "
            "AND image->>'media_type'='image/png' "
            "AND (image->>'size_bytes')::bigint>0 "
            "AND (image->>'width_px')::integer>0 AND (image->>'height_px')::integer>0"
        ),
        CheckConstraint(
            "storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text "
            "|| '/preparations/' || preparation_id::text || '/pages/' || document_id::text "
            "|| '/' || page::text || '/' || (image->>'sha256') || '.png'"
        ),
        Index("bid_document_page_inventory", "org_id", "submission_id", "document_id", "page"),
    )


class BidPreparationPublication(Tenant, Base):
    __tablename__ = "bid_preparation_publications"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer)
    published_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "submission_id"),
        UniqueConstraint("org_id", "preparation_id"),
        fk(
            ["task_id", "submission_id", "preparation_id", "job_id", "input_hash"],
            "bid_preparations",
            ["task_id", "submission_id", "id", "job_id", "input_hash"],
        ),
        fk(["published_by"], "memberships", ["user_id"]),
        CheckConstraint("page_count BETWEEN 2 AND 1000 AND input_hash ~ '^[0-9a-f]{64}$'"),
    )
