"""Immutable review inputs, normalized source parents and fenced publication."""

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk, preparation_fk
from app.models.entities import Base, Tenant


class BidReviewRun(Tenant, Base):
    __tablename__ = "bid_review_runs"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    tender_document_id: Mapped[UUID] = mapped_column()
    authorization_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        *common(),
        preparation_fk(),
        UniqueConstraint("org_id", "task_id", "submission_id", "id"),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint("org_id", "created_by", "request_id"),
        fk(["task_id", "job_id"], "jobs", ["task_id", "id"]),
        fk(["created_by"], "memberships", ["user_id"]),
        fk(["task_id", "authorization_id"], "bid_outbound_authorizations", ["task_id", "id"]),
        fk(
            ["task_id", "submission_id", "tender_document_id"],
            "bid_submission_documents",
            ["task_id", "submission_id", "id"],
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$'"),
    )


def run_fk():
    return fk(
        ["task_id", "submission_id", "review_id"],
        "bid_review_runs",
        ["task_id", "submission_id", "id"],
    )


class BidReviewObligation(Tenant, Base):
    __tablename__ = "bid_review_obligations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        run_fk(),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        UniqueConstraint("org_id", "review_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 2000 AND length(details_encrypted)>0"),
    )


class BidReviewSigningRequirement(Tenant, Base):
    __tablename__ = "bid_review_signing_requirements"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID] = mapped_column()
    candidate_id: Mapped[UUID | None] = mapped_column()
    applicability: Mapped[str] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        run_fk(),
        UniqueConstraint("org_id", "task_id", "submission_id", "review_id", "id"),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        fk(["task_id", "candidate_id"], "bid_signing_candidates", ["task_id", "id"]),
        UniqueConstraint("org_id", "review_id", "candidate_id"),
        CheckConstraint("applicability IN ('applies','not_applicable','alternative','unknown')"),
        UniqueConstraint("org_id", "review_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 10000 AND length(details_encrypted)>0"),
    )


class BidReviewRequiredLocation(Tenant, Base):
    __tablename__ = "bid_review_required_locations"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID | None] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    group_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    __table_args__ = (
        *common(),
        run_fk(),
        fk(
            ["task_id", "submission_id", "review_id", "requirement_id"],
            "bid_review_signing_requirements",
            ["task_id", "submission_id", "review_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        UniqueConstraint("org_id", "review_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 10000 AND status='unresolved'"),
    )


class BidReviewPublication(Tenant, Base):
    __tablename__ = "bid_review_publications"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    completion: Mapped[str] = mapped_column(String(20))
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    uncovered_codes: Mapped[list[str]] = mapped_column(JSONB)
    output_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *common(),
        run_fk(),
        UniqueConstraint("org_id", "review_id"),
        CheckConstraint("completion IN ('complete','partial') AND output_hash ~ '^[0-9a-f]{64}$'"),
        CheckConstraint(
            "jsonb_typeof(coverage)='object' AND coverage ?& ARRAY['obligations','signing_requirements','required_locations'] AND jsonb_typeof(uncovered_codes)='array' AND jsonb_array_length(uncovered_codes)<=100"
        ),
    )
