"""Immutable rubric snapshots and human-only append-only review histories."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(columns: list[str], table: str, targets: list[str]) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", *columns], [f"{table}.org_id", *[f"{table}.{c}" for c in targets]]
    )


def common() -> tuple[Any, ...]:
    return (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "task_id"),
        fk(["task_id"], "tasks", ["id"]),
    )


def rubric_fk() -> ForeignKeyConstraint:
    return fk(["rubric_id", "task_id"], "score_rubric_sets", ["id", "task_id"])


class ScoreRubricSet(Tenant, Base):
    __tablename__ = "score_rubric_sets"
    task_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    prior_rubric_id: Mapped[UUID | None] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    encrypted_input: Mapped[str] = mapped_column(Text)
    normalization_rule_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[str] = mapped_column(String(100))
    overall_aggregation: Mapped[str] = mapped_column(String(30))
    overall_rule_text: Mapped[str | None] = mapped_column(Text)
    overall_score_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    overall_cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    normalization_errors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "extraction_job_id", "version"),
        fk(["job_id", "task_id", "document_id"], "jobs", ["id", "task_id", "document_id"]),
        fk(
            ["extraction_job_id", "task_id", "document_id"],
            "jobs",
            ["id", "task_id", "document_id"],
        ),
        fk(["document_id", "task_id"], "documents", ["id", "task_id"]),
        fk(["prior_rubric_id", "task_id"], "score_rubric_sets", ["id", "task_id"]),
        fk(["actor_user_id"], "memberships", ["user_id"]),
        fk(["actor_token_id"], "api_tokens", ["id"]),
    )


class ScoreRubricSection(Tenant, Base):
    __tablename__ = "score_rubric_sections"
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer)
    aggregation: Mapped[str] = mapped_column(String(30))
    aggregation_rule_text: Mapped[str | None] = mapped_column(Text)
    score_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    included_in_overall_total: Mapped[bool] = mapped_column(Boolean)
    ambiguity_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    citation_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        *common(),
        rubric_fk(),
        UniqueConstraint("org_id", "id", "task_id", "rubric_id"),
        UniqueConstraint("org_id", "rubric_id", "key"),
        fk(["requirement_id", "task_id"], "requirements", ["id", "task_id"]),
    )


class ScoreRubricItem(Tenant, Base):
    __tablename__ = "score_rubric_items"
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    section_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    rule_text: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer)
    assessment_mode: Mapped[str] = mapped_column(String(30))
    score_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    ambiguity_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    citation_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        *common(),
        rubric_fk(),
        UniqueConstraint("org_id", "id", "task_id", "rubric_id"),
        UniqueConstraint("org_id", "rubric_id", "key"),
        UniqueConstraint("org_id", "rubric_id", "fingerprint"),
        fk(
            ["section_id", "task_id", "rubric_id"],
            "score_rubric_sections",
            ["id", "task_id", "rubric_id"],
        ),
        fk(["requirement_id", "task_id"], "requirements", ["id", "task_id"]),
    )


class ScoreRubricCoverage(Tenant, Base):
    __tablename__ = "score_rubric_coverage"
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        *common(),
        rubric_fk(),
        UniqueConstraint("org_id", "id", "task_id", "rubric_id"),
        UniqueConstraint("org_id", "rubric_id", "requirement_id"),
        fk(["requirement_id", "task_id"], "requirements", ["id", "task_id"]),
    )


class ReviewFields:
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    set_revision: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    reason_sha256: Mapped[str] = mapped_column(String(64))
    expected_input_hash: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[UUID] = mapped_column()
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(String(20), default="session")


def review_common() -> tuple[Any, ...]:
    return (*common(), rubric_fk(), fk(["decided_by"], "memberships", ["user_id"]))


def subject_fks() -> tuple[Any, ...]:
    return (
        fk(
            ["section_id", "task_id", "rubric_id"],
            "score_rubric_sections",
            ["id", "task_id", "rubric_id"],
        ),
        fk(
            ["item_id", "task_id", "rubric_id"],
            "score_rubric_items",
            ["id", "task_id", "rubric_id"],
        ),
    )


class ScoreRubricDecision(ReviewFields, Tenant, Base):
    __tablename__ = "score_rubric_decisions"
    section_id: Mapped[UUID | None] = mapped_column()
    item_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        *review_common(),
        *subject_fks(),
        UniqueConstraint("org_id", "rubric_id", "set_revision"),
    )


class ScoreRubricClassification(ReviewFields, Tenant, Base):
    __tablename__ = "score_rubric_classifications"
    section_id: Mapped[UUID | None] = mapped_column()
    item_id: Mapped[UUID | None] = mapped_column()
    review_domain: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        *review_common(),
        *subject_fks(),
        UniqueConstraint("org_id", "rubric_id", "set_revision"),
    )


class ScoreRubricCoverageDecision(ReviewFields, Tenant, Base):
    __tablename__ = "score_rubric_coverage_decisions"
    coverage_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    canonical_requirement_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        *review_common(),
        UniqueConstraint("org_id", "id", "task_id", "rubric_id"),
        UniqueConstraint("org_id", "coverage_id", "revision"),
        fk(
            ["coverage_id", "task_id", "rubric_id"],
            "score_rubric_coverage",
            ["id", "task_id", "rubric_id"],
        ),
        fk(["requirement_id", "task_id"], "requirements", ["id", "task_id"]),
        fk(["canonical_requirement_id", "task_id"], "requirements", ["id", "task_id"]),
    )


class ScoreRubricCoverageItem(Tenant, Base):
    __tablename__ = "score_rubric_coverage_items"
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    coverage_decision_id: Mapped[UUID] = mapped_column()
    rubric_item_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        rubric_fk(),
        UniqueConstraint("org_id", "coverage_decision_id", "rubric_item_id"),
        fk(
            ["coverage_decision_id", "task_id", "rubric_id"],
            "score_rubric_coverage_decisions",
            ["id", "task_id", "rubric_id"],
        ),
        fk(
            ["rubric_item_id", "task_id", "rubric_id"],
            "score_rubric_items",
            ["id", "task_id", "rubric_id"],
        ),
    )


class ScoreRubricRevisionEvent(Tenant, Base):
    __tablename__ = "score_rubric_revision_events"
    task_id: Mapped[UUID] = mapped_column()
    rubric_id: Mapped[UUID] = mapped_column()
    prior_rubric_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    expected_revision: Mapped[int] = mapped_column(Integer)
    expected_input_hash: Mapped[str] = mapped_column(String(64))
    replacement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    reason_sha256: Mapped[str] = mapped_column(String(64))
    revised_by: Mapped[UUID] = mapped_column()
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(String(20), default="session")
    __table_args__ = (
        *common(),
        rubric_fk(),
        UniqueConstraint("org_id", "rubric_id"),
        fk(["prior_rubric_id", "task_id"], "score_rubric_sets", ["id", "task_id"]),
        fk(["revised_by"], "memberships", ["user_id"]),
    )
