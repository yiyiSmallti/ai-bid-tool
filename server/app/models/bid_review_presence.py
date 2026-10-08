"""Immutable encrypted presence images and independent human disclosure receipts."""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk, preparation_fk, submission_fk
from app.models.entities import Base, Tenant


class BidPresencePreparation(Tenant, Base):
    __tablename__ = "bid_presence_preparations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    source_review_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    expected_page_ids: Mapped[list[str]] = mapped_column(JSONB)
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        preparation_fk(),
        fk(
            ["task_id", "submission_id", "source_review_id"],
            "bid_review_runs",
            ["task_id", "submission_id", "id"],
        ),
        fk(["created_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "task_id", "submission_id", "id"),
        UniqueConstraint("org_id", "submission_id", "manifest_sha256"),
        CheckConstraint(
            "jsonb_typeof(expected_page_ids)='array' AND jsonb_array_length(expected_page_ids) BETWEEN 1 AND 40"
        ),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$' AND length(details_encrypted)>0"),
    )


class BidPresenceImage(Tenant, Base):
    __tablename__ = "bid_presence_images"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    presence_preparation_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    blur: Mapped[dict] = mapped_column(JSONB)
    privacy_receipt_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        fk(
            ["task_id", "submission_id", "presence_preparation_id"],
            "bid_presence_preparations",
            ["task_id", "submission_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        UniqueConstraint("org_id", "presence_preparation_id", "page_id"),
        UniqueConstraint("org_id", "task_id", "submission_id", "presence_preparation_id", "id"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$' AND privacy_receipt_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 4194304 AND width_px BETWEEN 1 AND 8192 AND height_px BETWEEN 1 AND 8192 AND width_px*height_px<=16000000"
        ),
        CheckConstraint(
            "storage_key='org/' || org_id::text || '/bid-review/' || submission_id::text || '/presence/' || presence_preparation_id::text || '/' || sha256 || '.jpg'"
        ),
    )


class BidPresenceAuthorization(Tenant, Base):
    __tablename__ = "bid_presence_authorizations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    presence_preparation_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    prior_authorization_id: Mapped[UUID | None] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    authorized_by: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    image_ids: Mapped[list[str]] = mapped_column(JSONB)
    allow_external: Mapped[bool] = mapped_column(Boolean)
    reason_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        submission_fk(),
        fk(
            ["task_id", "submission_id", "presence_preparation_id"],
            "bid_presence_preparations",
            ["task_id", "submission_id", "id"],
        ),
        fk(["task_id", "prior_authorization_id"], "bid_presence_authorizations", ["task_id", "id"]),
        fk(["authorized_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "submission_id", "revision"),
        UniqueConstraint("org_id", "authorized_by", "request_id"),
        UniqueConstraint("org_id", "task_id", "submission_id", "presence_preparation_id", "id"),
        CheckConstraint(
            "jsonb_typeof(image_ids)='array' AND jsonb_array_length(image_ids) BETWEEN 1 AND 40"
        ),
        CheckConstraint(
            "revision>0 AND payload_hash ~ '^[0-9a-f]{64}$' AND manifest_sha256 ~ '^[0-9a-f]{64}$' AND length(reason_encrypted)>0"
        ),
    )


class BidPresenceAuthorizedImage(Tenant, Base):
    __tablename__ = "bid_presence_authorized_images"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    presence_preparation_id: Mapped[UUID] = mapped_column()
    authorization_id: Mapped[UUID] = mapped_column()
    image_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        fk(
            ["task_id", "submission_id", "presence_preparation_id", "authorization_id"],
            "bid_presence_authorizations",
            ["task_id", "submission_id", "presence_preparation_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "presence_preparation_id", "image_id"],
            "bid_presence_images",
            ["task_id", "submission_id", "presence_preparation_id", "id"],
        ),
        UniqueConstraint("org_id", "authorization_id", "image_id"),
    )
