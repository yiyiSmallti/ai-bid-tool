from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
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


class BidDocumentPage(Base):
    __tablename__ = "bid_document_pages"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    render_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    renderer_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    page_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    text_status: Mapped[str] = mapped_column(String(20), nullable=False)
    text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_page: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    redaction_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="human_only",
        server_default=sql_text("'human_only'::character varying"),
    )
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((page >= 1) AND (page <= 1000)) AND ((role)::text = ANY ((ARRAY['tender'::character varying, 'bid'::character varying])::text[]))))",
            name="bid_document_pages_check",
        ),
        CheckConstraint(
            "((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((rendered_pdf_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_document_pages_check1",
        ),
        CheckConstraint(
            "((((page_kind)::text = ANY ((ARRAY['text'::character varying, 'image'::character varying])::text[])) AND ((text_status)::text = ANY ((ARRAY['native'::character varying, 'unavailable'::character varying])::text[]))))",
            name="bid_document_pages_check2",
        ),
        CheckConstraint(
            "(((((text_status)::text = 'unavailable'::text) AND (text_sha256 IS NULL) AND (text_encrypted IS NULL)) OR (((text_status)::text = 'native'::text) AND (text_sha256 IS NOT NULL) AND (text_encrypted IS NOT NULL) AND ((text_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(text_encrypted) > 0))))",
            name="bid_document_pages_check3",
        ),
        CheckConstraint(
            "(((storage_key)::text = ((((((((((('bid-review/'::text) || (submission_id)::text) || '/preparations/'::text) || (preparation_id)::text) || '/pages/'::text) || (document_id)::text) || '/'::text) || (page)::text) || '/'::text) || (image ->> 'sha256'::text)) || '.png'::text)))",
            name="bid_document_pages_check4",
        ),
        CheckConstraint(
            "(((jsonb_typeof(image) = 'object'::text) AND (image ?& ARRAY['sha256'::text, 'size_bytes'::text, 'width_px'::text, 'height_px'::text, 'media_type'::text]) AND ((image ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND ((image ->> 'media_type'::text) = 'image/png'::text) AND (((image ->> 'size_bytes'::text))::bigint > 0) AND (((image ->> 'width_px'::text))::integer > 0) AND (((image ->> 'height_px'::text))::integer > 0)))",
            name="bid_document_pages_image_check",
        ),
        CheckConstraint(
            "(((redaction_status)::text = 'human_only'::text))",
            name="bid_document_pages_redaction_status_check",
        ),
        UniqueConstraint(
            "preparation_id",
            "document_id",
            "page",
            name="bid_document_pages_preparation_id_document_id_page_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_document_pages_task_id_id_key"),
        UniqueConstraint(
            "task_id", "submission_id", "id", name="bid_document_pages_task_id_submission_id_id_key"
        ),
        PrimaryKeyConstraint("id", name="bid_document_pages_pkey"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "preparation_id",
            "document_id",
            "page",
            "id",
            name="bid_signing_page_identity",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_document_pages_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "document_id"],
            [
                "bid_prepared_documents.task_id",
                "bid_prepared_documents.submission_id",
                "bid_prepared_documents.preparation_id",
                "bid_prepared_documents.document_id",
            ],
            name="bid_document_pages_task_id_submission_id_preparatio_fkey",
        ),
        Index("bid_document_page_inventory", "submission_id", "document_id", "page"),
    )


class BidPreparationPublication(Base):
    __tablename__ = "bid_preparation_publications"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((page_count >= 2) AND (page_count <= 1000)) AND ((input_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_preparation_publications_check",
        ),
        UniqueConstraint("preparation_id", name="bid_preparation_publications_preparation_id_key"),
        UniqueConstraint("submission_id", name="bid_preparation_publications_submission_id_key"),
        UniqueConstraint("task_id", "id", name="bid_preparation_publications_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_preparation_publications_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_preparation_publications_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "job_id", "input_hash"],
            [
                "bid_preparations.task_id",
                "bid_preparations.submission_id",
                "bid_preparations.id",
                "bid_preparations.job_id",
                "bid_preparations.input_hash",
            ],
            name="bid_preparation_publications_task_id_submission_id__fkey",
        ),
    )


class BidPreparation(Base):
    __tablename__ = "bid_preparations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_preparations_check",
        ),
        UniqueConstraint("request_id", name="bid_preparations_created_by_request_id_key"),
        UniqueConstraint("job_id", name="bid_preparations_job_id_key"),
        UniqueConstraint("task_id", "id", name="bid_preparations_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "id",
            "job_id",
            "input_hash",
            name="bid_preparations_task_id_submission_id_id_job_id_inp_key",
        ),
        UniqueConstraint(
            "task_id", "submission_id", "id", name="bid_preparations_task_id_submission_id_id_key"
        ),
        PrimaryKeyConstraint("id", name="bid_preparations_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_preparations_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "job_id"],
            ["jobs.task_id", "jobs.id"],
            name="bid_preparations_task_id_job_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id"],
            ["bid_submissions.task_id", "bid_submissions.id"],
            name="bid_preparations_task_id_submission_id_fkey",
        ),
        Index("bid_preparation_submission", "submission_id", "created_at", "id"),
    )


class BidPreparedDocument(Base):
    __tablename__ = "bid_prepared_documents"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rendered_pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_pdf_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    render_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    renderer_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    citation_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    parsing_warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    signature_field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    structure_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((page_count >= 1) AND (page_count <= 1000)) AND (signature_field_count >= 0)))",
            name="bid_prepared_documents_check",
        ),
        CheckConstraint(
            "((((rendered_pdf_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((citation_mode)::text = ANY ((ARRAY['page'::character varying, 'block'::character varying])::text[]))))",
            name="bid_prepared_documents_check1",
        ),
        CheckConstraint(
            "(((length((render_profile)::text) > 0) AND (length((renderer_identity)::text) > 0)))",
            name="bid_prepared_documents_check2",
        ),
        CheckConstraint(
            "(((jsonb_typeof(parsing_warnings) = 'array'::text) AND (jsonb_array_length(parsing_warnings) <= 100)))",
            name="bid_prepared_documents_parsing_warnings_check",
        ),
        UniqueConstraint("task_id", "id", name="bid_prepared_documents_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "preparation_id",
            "document_id",
            name="bid_prepared_documents_task_id_submission_id_prepara_key",
        ),
        PrimaryKeyConstraint("id", name="bid_prepared_documents_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_prepared_documents_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "document_id"],
            [
                "bid_submission_documents.task_id",
                "bid_submission_documents.submission_id",
                "bid_submission_documents.id",
            ],
            name="bid_prepared_documents_task_id_submission_id_docume_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_prepared_documents_task_id_submission_id_prepar_fkey",
        ),
    )


class BidSubmissionDocument(Base):
    __tablename__ = "bid_submission_documents"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    upload_name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 20)) AND ((size_bytes >= 1) AND (size_bytes <= 104857600))))",
            name="bid_submission_documents_check",
        ),
        CheckConstraint(
            "((((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(upload_name_encrypted) > 0)))",
            name="bid_submission_documents_check1",
        ),
        CheckConstraint(
            "((((role)::text = ANY ((ARRAY['tender'::character varying, 'bid'::character varying])::text[])) AND (((role)::text = 'tender'::text) = ((kind)::text = 'tender'::text))))",
            name="bid_submission_documents_check2",
        ),
        CheckConstraint(
            "(((storage_key)::text = ((((((('bid-review/'::text) || (submission_id)::text) || '/originals/'::text) || (file_id)::text) || '/'::text) || (sha256)::text) || CASE media_type WHEN 'application/pdf'::text THEN '.pdf'::text ELSE '.docx'::text END)))",
            name="bid_submission_documents_check3",
        ),
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['tender'::character varying, 'qualification'::character varying, 'commercial_technical'::character varying, 'price'::character varying, 'declaration'::character varying, 'other'::character varying])::text[])))",
            name="bid_submission_documents_kind_check",
        ),
        CheckConstraint(
            "(((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::character varying])::text[])))",
            name="bid_submission_documents_media_type_check",
        ),
        UniqueConstraint("file_id", name="bid_submission_documents_file_id_key"),
        UniqueConstraint(
            "submission_id", "ordinal", name="bid_submission_documents_submission_id_ordinal_key"
        ),
        UniqueConstraint(
            "submission_id",
            "role",
            "sha256",
            name="bid_submission_documents_submission_id_role_sha256_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_submission_documents_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "id",
            name="bid_submission_documents_task_id_submission_id_id_key",
        ),
        PrimaryKeyConstraint("id", name="bid_submission_documents_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_submission_documents_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id"],
            ["bid_submissions.task_id", "bid_submissions.id"],
            name="bid_submission_documents_task_id_submission_id_fkey",
        ),
    )


class BidSubmission(Base):
    __tablename__ = "bid_submissions"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="uploaded",
        server_default=sql_text("'uploaded'::character varying"),
    )
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((revision > 0) AND ((state)::text = ANY ((ARRAY['uploaded'::character varying, 'withdrawn'::character varying])::text[]))))",
            name="bid_submissions_check",
        ),
        CheckConstraint(
            "((((file_count >= 2) AND (file_count <= 20)) AND ((total_bytes >= 2) AND (total_bytes <= 524288000))))",
            name="bid_submissions_check1",
        ),
        CheckConstraint(
            "((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_submissions_check2",
        ),
        UniqueConstraint("request_id", name="bid_submissions_created_by_request_id_key"),
        UniqueConstraint("task_id", "id", name="bid_submissions_task_id_id_key"),
        UniqueConstraint("task_id", "revision", name="bid_submissions_task_id_revision_key"),
        PrimaryKeyConstraint("id", name="bid_submissions_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_submissions_task_id_fkey"),
        Index("bid_submission_page", "task_id", "created_at", "id"),
    )
