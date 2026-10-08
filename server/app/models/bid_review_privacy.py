"""Encrypted local derivatives and append-only exact human disclosure receipts."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk, preparation_fk, submission_fk
from app.models.entities import Base, Tenant


class BidReviewNameList(Tenant, Base):
    __tablename__ = "bid_review_name_lists"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    names_sha256: Mapped[str] = mapped_column(String(64))
    names_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        submission_fk(),
        fk(["created_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "submission_id", "revision"),
        UniqueConstraint("org_id", "created_by", "request_id"),
        CheckConstraint(
            "revision>0 AND names_sha256 ~ '^[0-9a-f]{64}$' AND length(names_encrypted)>0"
        ),
    )


class BidRedactionSnapshot(Tenant, Base):
    __tablename__ = "bid_redaction_snapshots"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    redaction_revision: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(String(100))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    confidential_binding_sha256: Mapped[str] = mapped_column(String(64))
    derived_name_lists_sha256: Mapped[str] = mapped_column(String(64))
    provider_bindings_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column()
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        preparation_fk(),
        fk(["created_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "task_id", "submission_id", "preparation_id", "id"),
        UniqueConstraint("org_id", "submission_id", "manifest_sha256"),
        CheckConstraint(
            "redaction_revision>0 AND length(policy_version)>0 AND length(details_encrypted)>0"
        ),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' AND confidential_binding_sha256 ~ '^[0-9a-f]{64}$' AND derived_name_lists_sha256 ~ '^[0-9a-f]{64}$' AND provider_bindings_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class BidRedactedPage(Tenant, Base):
    __tablename__ = "bid_redacted_pages"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    sanitized_text_sha256: Mapped[str] = mapped_column(String(64))
    sanitized_text_encrypted: Mapped[str] = mapped_column(Text)
    price_page: Mapped[bool] = mapped_column(Boolean)
    classification: Mapped[str] = mapped_column(String(20))
    notes_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        UniqueConstraint(
            "org_id", "task_id", "submission_id", "snapshot_id", "page_id", "sanitized_text_sha256"
        ),
        UniqueConstraint("org_id", "snapshot_id", "page_id"),
        fk(
            ["task_id", "submission_id", "preparation_id", "snapshot_id"],
            "bid_redaction_snapshots",
            ["task_id", "submission_id", "preparation_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        CheckConstraint(
            "sanitized_text_sha256 ~ '^[0-9a-f]{64}$' AND length(sanitized_text_encrypted)>0 AND length(notes_encrypted)>0"
        ),
        CheckConstraint(
            "classification IN ('price','non_price','uncertain') AND (price_page=(classification<>'non_price'))"
        ),
    )


class BidOutboundAuthorization(Tenant, Base):
    __tablename__ = "bid_outbound_authorizations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    prior_authorization_id: Mapped[UUID | None] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    submission_manifest_sha256: Mapped[str] = mapped_column(String(64))
    preparation_input_hash: Mapped[str] = mapped_column(String(64))
    redaction_manifest_sha256: Mapped[str] = mapped_column(String(64))
    authorized_sanitized_context_sha256: Mapped[str] = mapped_column(String(64))
    provider_bindings_sha256: Mapped[str] = mapped_column(String(64))
    allow_external: Mapped[bool] = mapped_column(Boolean)
    authorized_by: Mapped[UUID] = mapped_column()
    reason_sha256: Mapped[str] = mapped_column(String(64))
    reason_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        preparation_fk(),
        UniqueConstraint("org_id", "task_id", "submission_id", "snapshot_id", "id"),
        UniqueConstraint("org_id", "submission_id", "revision"),
        UniqueConstraint("org_id", "authorized_by", "request_id"),
        fk(
            ["task_id", "submission_id", "preparation_id", "snapshot_id"],
            "bid_redaction_snapshots",
            ["task_id", "submission_id", "preparation_id", "id"],
        ),
        fk(["task_id", "prior_authorization_id"], "bid_outbound_authorizations", ["task_id", "id"]),
        fk(["authorized_by"], "memberships", ["user_id"]),
        CheckConstraint("revision>0 AND length(reason_encrypted)>0"),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$' AND submission_manifest_sha256 ~ '^[0-9a-f]{64}$' AND preparation_input_hash ~ '^[0-9a-f]{64}$' AND redaction_manifest_sha256 ~ '^[0-9a-f]{64}$' AND authorized_sanitized_context_sha256 ~ '^[0-9a-f]{64}$' AND provider_bindings_sha256 ~ '^[0-9a-f]{64}$' AND reason_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )


class BidOutboundAuthorizedPage(Tenant, Base):
    __tablename__ = "bid_outbound_authorized_pages"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column()
    authorization_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    sanitized_text_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "authorization_id", "page_id"),
        fk(
            ["task_id", "submission_id", "snapshot_id", "authorization_id"],
            "bid_outbound_authorizations",
            ["task_id", "submission_id", "snapshot_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "snapshot_id", "page_id", "sanitized_text_sha256"],
            "bid_redacted_pages",
            ["task_id", "submission_id", "snapshot_id", "page_id", "sanitized_text_sha256"],
        ),
        CheckConstraint("sanitized_text_sha256 ~ '^[0-9a-f]{64}$'"),
    )
