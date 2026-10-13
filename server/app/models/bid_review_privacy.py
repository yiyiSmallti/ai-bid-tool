from datetime import datetime
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class BidOutboundAuthorization(Base):
    __tablename__ = "bid_outbound_authorizations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_authorization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preparation_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redaction_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_sanitized_context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_bindings_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    allow_external: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((revision > 0) AND (length(reason_encrypted) > 0)))",
            name="bid_outbound_authorizations_check",
        ),
        CheckConstraint(
            "((((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((submission_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((preparation_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((redaction_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((authorized_sanitized_context_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provider_bindings_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_outbound_authorizations_check1",
        ),
        UniqueConstraint(
            "request_id", name="bid_outbound_authorizations_authorized_by_request_id_key"
        ),
        UniqueConstraint(
            "submission_id",
            "revision",
            name="bid_outbound_authorizations_submission_id_revision_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_outbound_authorizations_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "snapshot_id",
            "id",
            name="bid_outbound_authorizations_task_id_submission_id_sn_key",
        ),
        PrimaryKeyConstraint("id", name="bid_outbound_authorizations_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_outbound_authorizations_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "prior_authorization_id"],
            ["bid_outbound_authorizations.task_id", "bid_outbound_authorizations.id"],
            name="bid_outbound_authorizations_task_id_prior_authoriza_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "snapshot_id"],
            [
                "bid_redaction_snapshots.task_id",
                "bid_redaction_snapshots.submission_id",
                "bid_redaction_snapshots.preparation_id",
                "bid_redaction_snapshots.id",
            ],
            name="bid_outbound_authorizations_task_id_submission_id__fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_outbound_authorizations_task_id_submission_id_p_fkey",
        ),
    )


class BidOutboundAuthorizedPage(Base):
    __tablename__ = "bid_outbound_authorized_pages"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False)
    authorization_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    sanitized_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((sanitized_text_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="bid_outbound_authorized_pages_sanitized_text_sha256_check",
        ),
        UniqueConstraint(
            "authorization_id",
            "page_id",
            name="bid_outbound_authorized_pages_authorization_id_page__key",
        ),
        UniqueConstraint("task_id", "id", name="bid_outbound_authorized_pages_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_outbound_authorized_pages_pkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "snapshot_id", "page_id", "sanitized_text_sha256"],
            [
                "bid_redacted_pages.task_id",
                "bid_redacted_pages.submission_id",
                "bid_redacted_pages.snapshot_id",
                "bid_redacted_pages.page_id",
                "bid_redacted_pages.sanitized_text_sha256",
            ],
            name="bid_outbound_authorized_page_task_id_submission_id_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_outbound_authorized_pages_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "snapshot_id", "authorization_id"],
            [
                "bid_outbound_authorizations.task_id",
                "bid_outbound_authorizations.submission_id",
                "bid_outbound_authorizations.snapshot_id",
                "bid_outbound_authorizations.id",
            ],
            name="bid_outbound_authorized_pages_task_id_submission_id_fkey",
        ),
    )


class BidRedactedPage(Base):
    __tablename__ = "bid_redacted_pages"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    sanitized_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sanitized_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    price_page: Mapped[bool] = mapped_column(Boolean, nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    notes_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((sanitized_text_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(sanitized_text_encrypted) > 0) AND (length(notes_encrypted) > 0)))",
            name="bid_redacted_pages_check",
        ),
        CheckConstraint(
            "((((classification)::text = ANY ((ARRAY['price'::character varying, 'non_price'::character varying, 'uncertain'::character varying])::text[])) AND (price_page = ((classification)::text <> 'non_price'::text))))",
            name="bid_redacted_pages_check1",
        ),
        UniqueConstraint(
            "snapshot_id", "page_id", name="bid_redacted_pages_snapshot_id_page_id_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_redacted_pages_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "snapshot_id",
            "page_id",
            "sanitized_text_sha256",
            name="bid_redacted_pages_task_id_submission_id_snapshot_id_key",
        ),
        PrimaryKeyConstraint("id", name="bid_redacted_pages_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_redacted_pages_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_redacted_pages_task_id_submission_id_page_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "snapshot_id"],
            [
                "bid_redaction_snapshots.task_id",
                "bid_redaction_snapshots.submission_id",
                "bid_redaction_snapshots.preparation_id",
                "bid_redaction_snapshots.id",
            ],
            name="bid_redacted_pages_task_id_submission_id_preparatio_fkey",
        ),
    )


class BidRedactionSnapshot(Base):
    __tablename__ = "bid_redaction_snapshots"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    redaction_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confidential_binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    derived_name_lists_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_bindings_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((redaction_revision > 0) AND (length((policy_version)::text) > 0) AND (length(details_encrypted) > 0)))",
            name="bid_redaction_snapshots_check",
        ),
        CheckConstraint(
            "((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((confidential_binding_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((derived_name_lists_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provider_bindings_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_redaction_snapshots_check1",
        ),
        UniqueConstraint(
            "submission_id",
            "manifest_sha256",
            name="bid_redaction_snapshots_submission_id_manifest_sha25_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_redaction_snapshots_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "preparation_id",
            "id",
            name="bid_redaction_snapshots_task_id_submission_id_prepar_key",
        ),
        PrimaryKeyConstraint("id", name="bid_redaction_snapshots_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_redaction_snapshots_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_redaction_snapshots_task_id_submission_id_prepa_fkey",
        ),
    )


class BidReviewNameList(Base):
    __tablename__ = "bid_review_name_lists"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    names_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    names_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((revision > 0) AND ((names_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(names_encrypted) > 0)))",
            name="bid_review_name_lists_check",
        ),
        UniqueConstraint("request_id", name="bid_review_name_lists_created_by_request_id_key"),
        UniqueConstraint(
            "submission_id", "revision", name="bid_review_name_lists_submission_id_revision_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_name_lists_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_name_lists_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_review_name_lists_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id"],
            ["bid_submissions.task_id", "bid_submissions.id"],
            name="bid_review_name_lists_task_id_submission_id_fkey",
        ),
    )
