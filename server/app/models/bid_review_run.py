from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
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


class BidReviewObligation(Base):
    __tablename__ = "bid_review_obligations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 2000)) AND (length(details_encrypted) > 0)))",
            name="bid_review_obligations_check",
        ),
        UniqueConstraint(
            "task_id", "submission_id", "review_id", "id", name="bid_review_obligation_run_key"
        ),
        UniqueConstraint(
            "review_id", "ordinal", name="bid_review_obligations_review_id_ordinal_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_obligations_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_obligations_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_review_obligations_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_review_obligations_task_id_submission_id_page_i_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_obligations_task_id_submission_id_review_fkey",
        ),
    )


class BidReviewPublication(Base):
    __tablename__ = "bid_review_publications"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    completion: Mapped[str] = mapped_column(String(20), nullable=False)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    uncovered_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])) AND ((output_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_review_publications_check",
        ),
        CheckConstraint(
            "(((jsonb_typeof(coverage) = 'object'::text) AND (coverage ?& ARRAY['obligations'::text, 'signing_requirements'::text, 'required_locations'::text]) AND (jsonb_typeof(uncovered_codes) = 'array'::text) AND (jsonb_array_length(uncovered_codes) <= 100)))",
            name="bid_review_publications_check1",
        ),
        UniqueConstraint("review_id", name="bid_review_publications_review_id_key"),
        UniqueConstraint("task_id", "id", name="bid_review_publications_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_publications_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_publications_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_publications_task_id_submission_id_revie_fkey",
        ),
    )


class BidReviewRequiredLocation(Base):
    __tablename__ = "bid_review_required_locations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID | None] = mapped_column(nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unresolved")
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 10000)) AND ((status)::text = ANY ((ARRAY['unresolved'::character varying, 'triage_present'::character varying, 'triage_absent'::character varying, 'triage_uncertain'::character varying])::text[]))))",
            name="bid_review_required_locations_check",
        ),
        UniqueConstraint(
            "review_id", "ordinal", name="bid_review_required_locations_review_id_ordinal_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_required_locations_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_required_locations_pkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "requirement_id"],
            [
                "bid_review_signing_requirements.task_id",
                "bid_review_signing_requirements.submission_id",
                "bid_review_signing_requirements.review_id",
                "bid_review_signing_requirements.id",
            ],
            name="bid_review_required_location_task_id_submission_id_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_review_required_location_task_id_submission_id_fkey2",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_required_locations_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_required_locations_task_id_submission_id_fkey",
        ),
    )


class BidReviewRun(Base):
    __tablename__ = "bid_review_runs"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    tender_document_id: Mapped[UUID] = mapped_column(nullable=False)
    authorization_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_review_runs_check",
        ),
        UniqueConstraint("request_id", name="bid_review_runs_created_by_request_id_key"),
        UniqueConstraint("job_id", name="bid_review_runs_job_id_key"),
        UniqueConstraint("task_id", "id", name="bid_review_runs_task_id_id_key"),
        UniqueConstraint(
            "task_id", "submission_id", "id", name="bid_review_runs_task_id_submission_id_id_key"
        ),
        PrimaryKeyConstraint("id", name="bid_review_runs_pkey"),
        ForeignKeyConstraint(
            ["task_id", "authorization_id"],
            ["bid_outbound_authorizations.task_id", "bid_outbound_authorizations.id"],
            name="bid_review_runs_task_id_authorization_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_review_runs_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "job_id"],
            ["jobs.task_id", "jobs.id"],
            name="bid_review_runs_task_id_job_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id"],
            ["bid_preparations.task_id", "bid_preparations.submission_id", "bid_preparations.id"],
            name="bid_review_runs_task_id_submission_id_preparation_i_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "tender_document_id"],
            [
                "bid_submission_documents.task_id",
                "bid_submission_documents.submission_id",
                "bid_submission_documents.id",
            ],
            name="bid_review_runs_task_id_submission_id_tender_docume_fkey",
        ),
    )


class BidReviewSigningRequirement(Base):
    __tablename__ = "bid_review_signing_requirements"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    applicability: Mapped[str] = mapped_column(String(20), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((applicability)::text = ANY ((ARRAY['applies'::character varying, 'not_applicable'::character varying, 'alternative'::character varying, 'unknown'::character varying])::text[])))",
            name="bid_review_signing_requirements_applicability_check",
        ),
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 10000)) AND (length(details_encrypted) > 0)))",
            name="bid_review_signing_requirements_check",
        ),
        UniqueConstraint(
            "review_id",
            "candidate_id",
            name="bid_review_signing_requiremen_review_id_candidate_id_key",
        ),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "review_id",
            "id",
            name="bid_review_signing_requiremen_task_id_submission_id__key",
        ),
        UniqueConstraint(
            "review_id", "ordinal", name="bid_review_signing_requirements_review_id_ordinal_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_signing_requirements_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_signing_requirements_pkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_review_signing_requireme_task_id_submission_id_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_signing_requiremen_task_id_submission_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "candidate_id"],
            ["bid_signing_candidates.task_id", "bid_signing_candidates.id"],
            name="bid_review_signing_requirement_task_id_candidate_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_signing_requirements_task_id_fkey"
        ),
    )
