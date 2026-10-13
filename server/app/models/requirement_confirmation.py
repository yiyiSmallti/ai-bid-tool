from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
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


class RequirementReviewEvent(Base):
    __tablename__ = "requirement_review_events"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    state_after: Mapped[str] = mapped_column(String(30), nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    reason_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_pin: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((action)::text = ANY ((ARRAY['seed'::character varying, 'manual_add'::character varying, 'confirm'::character varying, 'reopen'::character varying, 'invalidate'::character varying, 'source_repair'::character varying])::text[])))",
            name="requirement_review_events_action_check",
        ),
        CheckConstraint(
            "(((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'worker'::character varying, 'migration'::character varying])::text[])))",
            name="requirement_review_events_actor_kind_check",
        ),
        CheckConstraint("((revision >= 1))", name="requirement_review_events_revision_check"),
        CheckConstraint(
            "(((length(snapshot_ciphertext) >= 80) AND (snapshot_ciphertext ~ '^gAAAA'::text)))",
            name="requirement_review_events_snapshot_ciphertext_check",
        ),
        CheckConstraint(
            "(((state_after)::text = ANY ((ARRAY['unconfirmed'::character varying, 'legacy_unconfirmed'::character varying, 'confirmed'::character varying, 'invalidated'::character varying])::text[])))",
            name="requirement_review_events_state_after_check",
        ),
        UniqueConstraint(
            "review_id",
            "id",
            "revision",
            name="requirement_review_events_review_id_id_revision_key",
        ),
        UniqueConstraint(
            "review_id", "revision", name="requirement_review_events_review_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="requirement_review_events_pkey"),
        ForeignKeyConstraint(
            ["review_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "requirement_reviews.id",
                "requirement_reviews.task_id",
                "requirement_reviews.extraction_job_id",
                "requirement_reviews.requirement_id",
            ],
            name="requirement_review_events_review_id_task_id_extract_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="requirement_review_events_task_id_fkey"
        ),
        Index("requirement_review_events_scope", "requirement_id", "revision"),
    )


class RequirementReviewRequest(Base):
    __tablename__ = "requirement_review_requests"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    __table_args__ = (
        UniqueConstraint("request_id"),
        PrimaryKeyConstraint("id", name="requirement_review_requests_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="requirement_review_requests_task_id_fkey"
        ),
    )


class RequirementReviewSet(Base):
    __tablename__ = "requirement_review_sets"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    membership_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((origin)::text = ANY ((ARRAY['model'::character varying, 'manual'::character varying])::text[])))",
            name="requirement_review_sets_origin_check",
        ),
        CheckConstraint("((revision >= 1))", name="requirement_review_sets_revision_check"),
        UniqueConstraint("extraction_job_id", name="requirement_review_sets_extraction_job_id_key"),
        UniqueConstraint(
            "id",
            "task_id",
            "extraction_job_id",
            name="requirement_review_sets_id_task_id_extraction_job_id_key",
        ),
        UniqueConstraint(
            "task_id",
            "extraction_job_id",
            name="requirement_review_sets_task_id_extraction_job_id_key",
        ),
        PrimaryKeyConstraint("id", name="requirement_review_sets_pkey"),
        ForeignKeyConstraint(
            ["task_id", "document_id", "extraction_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="requirement_review_sets_task_id_document_id_extract_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="requirement_review_sets_task_id_fkey"
        ),
    )


class RequirementReview(Base):
    __tablename__ = "requirement_reviews"
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_event_id: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_pin: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    rejected_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejected_summary_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((((state)::text = 'confirmed'::text) AND (confirmed_at IS NOT NULL) AND (source_pin IS NOT NULL)) OR (((state)::text <> 'confirmed'::text) AND (confirmed_at IS NULL))))",
            name="requirement_reviews_check",
        ),
        CheckConstraint(
            "((((rejected_job_id IS NULL) AND (rejected_index IS NULL) AND (rejected_summary_sha256 IS NULL)) OR ((rejected_job_id IS NOT NULL) AND (rejected_index IS NOT NULL) AND (rejected_summary_sha256 IS NOT NULL))))",
            name="requirement_reviews_check1",
        ),
        CheckConstraint(
            "(((origin)::text = ANY ((ARRAY['extracted'::character varying, 'legacy'::character varying, 'manual_missing'::character varying, 'manual_rejected'::character varying])::text[])))",
            name="requirement_reviews_origin_check",
        ),
        CheckConstraint("((rejected_index >= 0))", name="requirement_reviews_rejected_index_check"),
        CheckConstraint("((revision >= 1))", name="requirement_reviews_revision_check"),
        CheckConstraint(
            "(((length(snapshot_ciphertext) >= 80) AND (snapshot_ciphertext ~ '^gAAAA'::text)))",
            name="requirement_reviews_snapshot_ciphertext_check",
        ),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['unconfirmed'::character varying, 'legacy_unconfirmed'::character varying, 'confirmed'::character varying, 'invalidated'::character varying])::text[])))",
            name="requirement_reviews_state_check",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "extraction_job_id",
            "requirement_id",
            name="requirement_reviews_id_task_id_extraction_job_id_req_key",
        ),
        UniqueConstraint("requirement_id", name="requirement_reviews_requirement_id_key"),
        PrimaryKeyConstraint("id", name="requirement_reviews_pkey"),
        ForeignKeyConstraint(
            ["id", "current_event_id", "revision"],
            [
                "requirement_review_events.review_id",
                "requirement_review_events.id",
                "requirement_review_events.revision",
            ],
            name="requirement_current_event",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["rejected_job_id"], ["jobs.id"], name="requirement_reviews_rejected_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="requirement_reviews_requirement_id_task_id_extracti_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "extraction_job_id"],
            ["requirement_review_sets.task_id", "requirement_review_sets.extraction_job_id"],
            name="requirement_reviews_task_id_extraction_job_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="requirement_reviews_task_id_fkey"),
        Index("requirement_reviews_rejected", "rejected_job_id", "rejected_index"),
        Index("requirement_reviews_scope", "task_id", "extraction_job_id", "state"),
    )
