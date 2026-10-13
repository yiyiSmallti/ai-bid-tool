from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
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


class BidPDFValidation(Base):
    __tablename__ = "bid_pdf_validations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    trust_store_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((trust_store_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_pdf_validations_check",
        ),
        CheckConstraint(
            "(((length((validator_identity)::text) > 0) AND (length(details_encrypted) > 0)))",
            name="bid_pdf_validations_check1",
        ),
        UniqueConstraint(
            "preparation_id",
            "document_id",
            name="bid_pdf_validations_preparation_id_document_id_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_pdf_validations_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_pdf_validations_pkey"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "preparation_id",
            "document_id",
            "id",
            name="bid_review_pdf_validation_key",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_pdf_validations_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "document_id"],
            [
                "bid_prepared_documents.task_id",
                "bid_prepared_documents.submission_id",
                "bid_prepared_documents.preparation_id",
                "bid_prepared_documents.document_id",
            ],
            name="bid_pdf_validations_task_id_submission_id_preparat_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "trust_store_sha256"],
            [
                "bid_preparation_trust.task_id",
                "bid_preparation_trust.submission_id",
                "bid_preparation_trust.preparation_id",
                "bid_preparation_trust.trust_store_sha256",
            ],
            name="bid_pdf_validations_task_id_submission_id_preparat_fkey2",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_pdf_validations_task_id_submission_id_preparati_fkey",
        ),
    )


class BidPreparationTrust(Base):
    __tablename__ = "bid_preparation_trust"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    trust_store_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    anchors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((jsonb_typeof(anchors) = 'array'::text) AND (jsonb_array_length(anchors) <= 128)))",
            name="bid_preparation_trust_anchors_check",
        ),
        CheckConstraint(
            "(((trust_store_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="bid_preparation_trust_trust_store_sha256_check",
        ),
        UniqueConstraint("preparation_id", name="bid_preparation_trust_preparation_id_key"),
        UniqueConstraint("task_id", "id", name="bid_preparation_trust_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "preparation_id",
            "trust_store_sha256",
            name="bid_preparation_trust_task_id_submission_id_preparat_key",
        ),
        PrimaryKeyConstraint("id", name="bid_preparation_trust_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_preparation_trust_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_preparation_trust_task_id_submission_id_prepara_fkey",
        ),
    )


class BidSigningCandidate(Base):
    __tablename__ = "bid_signing_candidates"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    applicability: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unknown",
        server_default=sql_text("'unknown'::character varying"),
    )
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((page >= 1) AND (page <= 1000)) AND ((ordinal >= 1) AND (ordinal <= 10000))))",
            name="bid_signing_candidates_check",
        ),
        CheckConstraint(
            "((((applicability)::text = 'unknown'::text) AND (length((candidate_kind)::text) > 0) AND (length(details_encrypted) > 0)))",
            name="bid_signing_candidates_check1",
        ),
        UniqueConstraint(
            "preparation_id", "ordinal", name="bid_signing_candidates_preparation_id_ordinal_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_signing_candidates_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_signing_candidates_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_signing_candidates_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "document_id", "page", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.preparation_id",
                "bid_document_pages.document_id",
                "bid_document_pages.page",
                "bid_document_pages.id",
            ],
            name="bid_signing_candidates_task_id_submission_id_prepa_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_signing_candidates_task_id_submission_id_prepar_fkey",
        ),
    )


class PlatformTrustAnchor(Base):
    __tablename__ = "platform_trust_anchors"
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_der: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    subject: Mapped[str] = mapped_column(String(2000), nullable=False)
    issuer: Mapped[str] = mapped_column(String(2000), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((octet_length(certificate_der) >= 1) AND (octet_length(certificate_der) <= 65536)))",
            name="platform_trust_anchors_certificate_der_check",
        ),
        CheckConstraint(
            "(((length(btrim((label)::text)) >= 1) AND (length(btrim((label)::text)) <= 100)))",
            name="platform_trust_anchors_check",
        ),
        CheckConstraint(
            "(((not_after >= not_before) AND (revision >= 1)))",
            name="platform_trust_anchors_check1",
        ),
        CheckConstraint(
            "(((enabled AND (disabled_at IS NULL)) OR ((NOT enabled) AND (disabled_at IS NOT NULL))))",
            name="platform_trust_anchors_check2",
        ),
        CheckConstraint(
            "(((fingerprint_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="platform_trust_anchors_fingerprint_sha256_check",
        ),
        UniqueConstraint(
            "fingerprint_sha256", name="platform_trust_anchors_fingerprint_sha256_key"
        ),
        PrimaryKeyConstraint("id", name="platform_trust_anchors_pkey"),
    )
