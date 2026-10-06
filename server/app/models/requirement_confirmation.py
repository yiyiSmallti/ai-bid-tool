"""Independent requirement review state and append-only decisions and receipts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def task_fk():
    return ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"])


def member_fk(column):
    return ForeignKeyConstraint(["org_id", column], ["memberships.org_id", "memberships.user_id"])


class RequirementReviewSet(Tenant, Base):
    __tablename__ = "requirement_review_sets"
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    origin: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    membership_sha256: Mapped[str] = mapped_column(String(64))
    confirmation_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "extraction_job_id"),
        UniqueConstraint("org_id", "id", "task_id", "extraction_job_id"),
        UniqueConstraint("org_id", "task_id", "extraction_job_id"),
        task_fk(),
        ForeignKeyConstraint(
            ["org_id", "task_id", "document_id", "extraction_job_id"],
            ["jobs.org_id", "jobs.task_id", "jobs.document_id", "jobs.id"],
        ),
    )


class RequirementReview(Tenant, Base):
    __tablename__ = "requirement_reviews"
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    origin: Mapped[str] = mapped_column(String(30))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_event_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(30))
    review_hash: Mapped[str] = mapped_column(String(64))
    source_pin: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    snapshot_ciphertext: Mapped[str] = mapped_column(Text)
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_job_id: Mapped[UUID | None] = mapped_column()
    rejected_index: Mapped[int | None] = mapped_column(Integer)
    rejected_summary_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "requirement_id"),
        UniqueConstraint("org_id", "id", "task_id", "extraction_job_id", "requirement_id"),
        task_fk(),
        member_fk("confirmed_by_user_id"),
        ForeignKeyConstraint(
            ["org_id", "requirement_id", "task_id", "extraction_job_id"],
            [
                "requirements.org_id",
                "requirements.id",
                "requirements.task_id",
                "requirements.job_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "task_id", "extraction_job_id"],
            [
                "requirement_review_sets.org_id",
                "requirement_review_sets.task_id",
                "requirement_review_sets.extraction_job_id",
            ],
        ),
        ForeignKeyConstraint(["org_id", "rejected_job_id"], ["jobs.org_id", "jobs.id"]),
        ForeignKeyConstraint(
            ["org_id", "id", "current_event_id", "revision"],
            [
                "requirement_review_events.org_id",
                "requirement_review_events.review_id",
                "requirement_review_events.id",
                "requirement_review_events.revision",
            ],
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
            name="requirement_current_event",
        ),
    )


class RequirementReviewEvent(Tenant, Base):
    __tablename__ = "requirement_review_events"
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))
    state_after: Mapped[str] = mapped_column(String(30))
    review_hash: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    reason_ciphertext: Mapped[str | None] = mapped_column(Text)
    reason_sha256: Mapped[str | None] = mapped_column(String(64))
    source_pin: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[UUID | None] = mapped_column()
    request_id: Mapped[UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "review_id", "revision"),
        UniqueConstraint("org_id", "review_id", "id", "revision"),
        task_fk(),
        member_fk("actor_user_id"),
        ForeignKeyConstraint(
            ["org_id", "review_id", "task_id", "extraction_job_id", "requirement_id"],
            [
                "requirement_reviews.org_id",
                "requirement_reviews.id",
                "requirement_reviews.task_id",
                "requirement_reviews.extraction_job_id",
                "requirement_reviews.requirement_id",
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class RequirementReviewRequest(Tenant, Base):
    __tablename__ = "requirement_review_requests"
    task_id: Mapped[UUID] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    action: Mapped[str] = mapped_column(String(30))
    request_sha256: Mapped[str] = mapped_column(String(64))
    receipt_ciphertext: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "actor_user_id", "request_id"),
        task_fk(),
        member_fk("actor_user_id"),
    )
