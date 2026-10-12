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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class BidPresenceAuthorization(Base):
    __tablename__ = "bid_presence_authorizations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    presence_preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_authorization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allow_external: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((revision > 0) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(reason_encrypted) > 0)))",
            name="bid_presence_authorizations_check",
        ),
        CheckConstraint(
            "(((jsonb_typeof(image_ids) = 'array'::text) AND ((jsonb_array_length(image_ids) >= 1) AND (jsonb_array_length(image_ids) <= 40))))",
            name="bid_presence_authorizations_image_ids_check",
        ),
        UniqueConstraint(
            "request_id", name="bid_presence_authorizations_authorized_by_request_id_key"
        ),
        UniqueConstraint(
            "submission_id",
            "revision",
            name="bid_presence_authorizations_submission_id_revision_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_presence_authorizations_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "presence_preparation_id",
            "id",
            name="bid_presence_authorizations_task_id_submission_id_pr_key",
        ),
        PrimaryKeyConstraint("id", name="bid_presence_authorizations_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_presence_authorizations_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "prior_authorization_id"],
            ["bid_presence_authorizations.task_id", "bid_presence_authorizations.id"],
            name="bid_presence_authorizations_task_id_prior_authoriza_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id"],
            ["bid_submissions.task_id", "bid_submissions.id"],
            name="bid_presence_authorizations_task_id_submission_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "presence_preparation_id"],
            [
                "bid_presence_preparations.task_id",
                "bid_presence_preparations.submission_id",
                "bid_presence_preparations.id",
            ],
            name="bid_presence_authorizations_task_id_submission_id_p_fkey",
        ),
    )


class BidPresenceAuthorizedImage(Base):
    __tablename__ = "bid_presence_authorized_images"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    presence_preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    authorization_id: Mapped[UUID] = mapped_column(nullable=False)
    image_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint(
            "authorization_id",
            "image_id",
            name="bid_presence_authorized_image_authorization_id_image_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_presence_authorized_images_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_presence_authorized_images_pkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "presence_preparation_id", "image_id"],
            [
                "bid_presence_images.task_id",
                "bid_presence_images.submission_id",
                "bid_presence_images.presence_preparation_id",
                "bid_presence_images.id",
            ],
            name="bid_presence_authorized_imag_task_id_submission_id_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "presence_preparation_id", "authorization_id"],
            [
                "bid_presence_authorizations.task_id",
                "bid_presence_authorizations.submission_id",
                "bid_presence_authorizations.presence_preparation_id",
                "bid_presence_authorizations.id",
            ],
            name="bid_presence_authorized_image_task_id_submission_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_presence_authorized_images_task_id_fkey"
        ),
    )


class BidPresenceImage(Base):
    __tablename__ = "bid_presence_images"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    presence_preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    blur: Mapped[dict] = mapped_column(JSONB, nullable=False)
    privacy_receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((source_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((privacy_receipt_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_presence_images_check",
        ),
        CheckConstraint(
            "((((size_bytes >= 1) AND (size_bytes <= 4194304)) AND ((width_px >= 1) AND (width_px <= 8192)) AND ((height_px >= 1) AND (height_px <= 8192)) AND ((width_px * height_px) <= 16000000)))",
            name="bid_presence_images_check1",
        ),
        CheckConstraint(
            "((storage_key = ((((((('bid-review/'::text) || (submission_id)::text) || '/presence/'::text) || (presence_preparation_id)::text) || '/'::text) || (sha256)::text) || '.jpg'::text)))",
            name="bid_presence_images_check2",
        ),
        UniqueConstraint(
            "presence_preparation_id",
            "page_id",
            name="bid_presence_images_presence_preparation_id_page_id_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_presence_images_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "presence_preparation_id",
            "id",
            name="bid_presence_images_task_id_submission_id_presence_p_key",
        ),
        PrimaryKeyConstraint("id", name="bid_presence_images_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_presence_images_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_presence_images_task_id_submission_id_page_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "presence_preparation_id"],
            [
                "bid_presence_preparations.task_id",
                "bid_presence_preparations.submission_id",
                "bid_presence_preparations.id",
            ],
            name="bid_presence_images_task_id_submission_id_presence__fkey",
        ),
    )


class BidPresencePreparation(Base):
    __tablename__ = "bid_presence_preparations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    source_review_id: Mapped[UUID] = mapped_column(nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_page_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(details_encrypted) > 0)))",
            name="bid_presence_preparations_check",
        ),
        CheckConstraint(
            "(((jsonb_typeof(expected_page_ids) = 'array'::text) AND ((jsonb_array_length(expected_page_ids) >= 1) AND (jsonb_array_length(expected_page_ids) <= 40))))",
            name="bid_presence_preparations_expected_page_ids_check",
        ),
        UniqueConstraint(
            "submission_id",
            "manifest_sha256",
            name="bid_presence_preparations_submission_id_manifest_sha_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_presence_preparations_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "id",
            name="bid_presence_preparations_task_id_submission_id_id_key",
        ),
        PrimaryKeyConstraint("id", name="bid_presence_preparations_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_presence_preparations_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_presence_preparations_task_id_submission_id_pre_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "source_review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_presence_preparations_task_id_submission_id_sou_fkey",
        ),
    )
