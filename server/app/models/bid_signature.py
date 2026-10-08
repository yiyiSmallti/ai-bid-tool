"""Pinned offline trust and encrypted, immutable local signing observations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk, preparation_fk
from app.models.entities import Base, Identity, Tenant


class PlatformTrustAnchor(Identity, Base):
    __tablename__ = "platform_trust_anchors"
    label: Mapped[str] = mapped_column(String(100))
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    certificate_der: Mapped[bytes] = mapped_column(LargeBinary)
    subject: Mapped[str] = mapped_column(String(2000))
    issuer: Mapped[str] = mapped_column(String(2000))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    created_by: Mapped[str] = mapped_column(String(254))
    disabled_by: Mapped[str | None] = mapped_column(String(254))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("fingerprint_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("octet_length(certificate_der) BETWEEN 1 AND 65536"),
        CheckConstraint("length(btrim(label)) BETWEEN 1 AND 100 AND length(btrim(created_by))>0"),
        CheckConstraint("not_after>=not_before AND revision>=1"),
        CheckConstraint(
            "(enabled AND disabled_by IS NULL AND disabled_at IS NULL) OR (NOT enabled AND disabled_by IS NOT NULL AND disabled_at IS NOT NULL)"
        ),
    )


class BidPreparationTrust(Tenant, Base):
    __tablename__ = "bid_preparation_trust"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    trust_store_sha256: Mapped[str] = mapped_column(String(64))
    anchors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    __table_args__ = (
        *common(),
        preparation_fk(),
        fk(["created_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "preparation_id"),
        UniqueConstraint(
            "org_id", "task_id", "submission_id", "preparation_id", "trust_store_sha256"
        ),
        CheckConstraint("trust_store_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("jsonb_typeof(anchors)='array' AND jsonb_array_length(anchors)<=128"),
    )


class BidPDFValidation(Tenant, Base):
    __tablename__ = "bid_pdf_validations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    original_sha256: Mapped[str] = mapped_column(String(64))
    validator_identity: Mapped[str] = mapped_column(String(200))
    trust_store_sha256: Mapped[str] = mapped_column(String(64))
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        preparation_fk(),
        UniqueConstraint("org_id", "preparation_id", "document_id"),
        fk(
            ["task_id", "submission_id", "preparation_id", "document_id"],
            "bid_prepared_documents",
            ["task_id", "submission_id", "preparation_id", "document_id"],
        ),
        fk(
            ["task_id", "submission_id", "preparation_id", "trust_store_sha256"],
            "bid_preparation_trust",
            ["task_id", "submission_id", "preparation_id", "trust_store_sha256"],
        ),
        CheckConstraint(
            "original_sha256 ~ '^[0-9a-f]{64}$' AND trust_store_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint("length(validator_identity)>0 AND length(details_encrypted)>0"),
    )


class BidSigningCandidate(Tenant, Base):
    __tablename__ = "bid_signing_candidates"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    page: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    candidate_kind: Mapped[str] = mapped_column(String(100))
    applicability: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        preparation_fk(),
        UniqueConstraint("org_id", "preparation_id", "ordinal"),
        fk(
            ["task_id", "submission_id", "preparation_id", "document_id", "page", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "preparation_id", "document_id", "page", "id"],
        ),
        CheckConstraint("page BETWEEN 1 AND 1000 AND ordinal BETWEEN 1 AND 10000"),
        CheckConstraint(
            "applicability='unknown' AND length(candidate_kind)>0 AND length(details_encrypted)>0"
        ),
    )
