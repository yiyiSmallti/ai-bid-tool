"""Immutable confirmed-draft assessments and append-only human decisions."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
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


def report_fk() -> ForeignKeyConstraint:
    return fk(["report_id", "task_id"], "check_runs", ["id", "task_id"])


class CheckRun(Tenant, Base):
    __tablename__ = "check_runs"
    task_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    draft_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    draft_input_hash: Mapped[str] = mapped_column(String(64))
    assessment_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(20), default="rules")
    rule_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    schema_version: Mapped[str] = mapped_column(String(100))
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    encrypted_input: Mapped[str] = mapped_column(Text)
    completion: Mapped[str] = mapped_column(String(20))
    limitations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20), default="worker")
    __table_args__ = (
        *common(),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint("org_id", "id", "task_id", "draft_id", "extraction_job_id"),
        fk(["job_id", "task_id", "document_id"], "jobs", ["id", "task_id", "document_id"]),
        fk(
            ["extraction_job_id", "task_id", "document_id"],
            "jobs",
            ["id", "task_id", "document_id"],
        ),
        fk(
            ["draft_id", "task_id", "extraction_job_id"],
            "draft_runs",
            ["id", "task_id", "extraction_job_id"],
        ),
        fk(["document_id", "task_id"], "documents", ["id", "task_id"]),
        fk(["actor_user_id"], "memberships", ["user_id"]),
        fk(["actor_token_id"], "api_tokens", ["id"]),
    )


class CheckItem(Tenant, Base):
    __tablename__ = "check_items"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    draft_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    response_item_id: Mapped[UUID] = mapped_column()
    card_revision_id: Mapped[UUID | None] = mapped_column()
    partition: Mapped[str] = mapped_column(String(20))
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    semantic_status: Mapped[str] = mapped_column(String(20), default="not_requested")
    semantic_reason_code: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        *common(),
        report_fk(),
        UniqueConstraint("org_id", "report_id", "requirement_id"),
        UniqueConstraint("org_id", "id", "task_id", "report_id"),
        UniqueConstraint("org_id", "id", "task_id", "report_id", "requirement_id"),
        fk(
            ["report_id", "task_id", "draft_id", "extraction_job_id"],
            "check_runs",
            ["id", "task_id", "draft_id", "extraction_job_id"],
        ),
        fk(
            ["requirement_id", "task_id", "extraction_job_id"],
            "requirements",
            ["id", "task_id", "job_id"],
        ),
        fk(
            ["response_item_id", "draft_id", "requirement_id"],
            "response_items",
            ["id", "draft_id", "requirement_id"],
        ),
        fk(["card_revision_id"], "response_card_revisions", ["id"]),
    )


class CheckCertificate(Tenant, Base):
    __tablename__ = "check_certificates"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    task_certificate_id: Mapped[UUID] = mapped_column()
    certificate_revision_id: Mapped[UUID] = mapped_column()
    assessment_date: Mapped[date] = mapped_column(Date)
    date_status: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        *common(),
        report_fk(),
        UniqueConstraint("org_id", "report_id", "task_certificate_id"),
        UniqueConstraint("org_id", "id", "task_id", "report_id"),
        fk(
            ["task_certificate_id", "task_id", "certificate_revision_id"],
            "task_certificates",
            ["id", "task_id", "certificate_revision_id"],
        ),
        fk(["certificate_revision_id"], "certificate_revisions", ["id"]),
    )


class CheckCertificateItem(Tenant, Base):
    __tablename__ = "check_certificate_items"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    certificate_id: Mapped[UUID] = mapped_column()
    check_item_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        report_fk(),
        UniqueConstraint("org_id", "report_id", "certificate_id", "check_item_id"),
        fk(
            ["certificate_id", "task_id", "report_id"],
            "check_certificates",
            ["id", "task_id", "report_id"],
        ),
        fk(
            ["check_item_id", "task_id", "report_id"], "check_items", ["id", "task_id", "report_id"]
        ),
    )


class CheckFinding(Tenant, Base):
    __tablename__ = "check_findings"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    check_item_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    method: Mapped[str] = mapped_column(String(20), default="deterministic")
    code: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(30))
    review_domain: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        *common(),
        report_fk(),
        UniqueConstraint("org_id", "id", "task_id", "report_id"),
        fk(
            ["check_item_id", "task_id", "report_id", "requirement_id"],
            "check_items",
            ["id", "task_id", "report_id", "requirement_id"],
        ),
    )


class CheckFindingCitation(Tenant, Base):
    __tablename__ = "check_finding_citations"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    finding_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    quote: Mapped[str] = mapped_column(Text)
    document_id: Mapped[UUID | None] = mapped_column()
    chunk_id: Mapped[UUID | None] = mapped_column()
    draft_id: Mapped[UUID | None] = mapped_column()
    response_item_id: Mapped[UUID | None] = mapped_column()
    card_revision_id: Mapped[UUID | None] = mapped_column()
    field: Mapped[str | None] = mapped_column(String(20))
    evidence_id: Mapped[UUID | None] = mapped_column()
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __table_args__ = (
        *common(),
        report_fk(),
        fk(
            ["finding_id", "task_id", "report_id"], "check_findings", ["id", "task_id", "report_id"]
        ),
        fk(["chunk_id", "task_id", "document_id"], "chunks", ["id", "task_id", "document_id"]),
        fk(["document_id", "task_id"], "documents", ["id", "task_id"]),
        fk(["draft_id", "task_id"], "draft_runs", ["id", "task_id"]),
        fk(
            ["response_item_id", "draft_id", "card_revision_id"],
            "response_items",
            ["id", "draft_id", "card_revision_id"],
        ),
        fk(["evidence_id", "task_id"], "evidence", ["id", "task_id"]),
    )


class CheckDecision(Tenant, Base):
    __tablename__ = "check_decisions"
    task_id: Mapped[UUID] = mapped_column()
    report_id: Mapped[UUID] = mapped_column()
    finding_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    reason_sha256: Mapped[str] = mapped_column(String(64))
    expected_input_hash: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[UUID] = mapped_column()
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(String(20), default="session")
    __table_args__ = (
        *common(),
        report_fk(),
        UniqueConstraint("org_id", "finding_id", "revision"),
        fk(
            ["finding_id", "task_id", "report_id"], "check_findings", ["id", "task_id", "report_id"]
        ),
        fk(["decided_by"], "memberships", ["user_id"]),
    )
