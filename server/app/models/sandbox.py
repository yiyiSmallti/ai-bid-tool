"""Append-only authorized sandbox inputs, runs, attempts and original artifacts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(column: str, table: str, target: str = "id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["org_id", column], [f"{table}.org_id", f"{table}.{target}"])


class SandboxInput(Tenant, Base):
    __tablename__ = "sandbox_inputs"
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(30))
    task_feature_id: Mapped[UUID | None] = mapped_column()
    feature_revision_id: Mapped[UUID | None] = mapped_column()
    html_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    object_key: Mapped[str | None] = mapped_column(Text)
    generation_job_id: Mapped[UUID | None] = mapped_column()
    task_resource_id: Mapped[UUID | None] = mapped_column()
    product_revision_id: Mapped[UUID | None] = mapped_column()
    source_field: Mapped[str | None] = mapped_column(String(30))
    source_url_sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted_source_url: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "task_id", "document_id"),
        UniqueConstraint("org_id", "id", "task_id"),
        fk("task_id", "tasks"),
        fk("document_id", "documents"),
        fk("extraction_job_id", "jobs"),
        fk("generation_job_id", "jobs"),
        fk("feature_revision_id", "feature_revisions"),
        fk("product_revision_id", "product_revisions"),
        ForeignKeyConstraint(
            ["org_id", "extraction_job_id", "task_id", "document_id"],
            ["jobs.org_id", "jobs.id", "jobs.task_id", "jobs.document_id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "task_feature_id", "task_id", "feature_revision_id"],
            [
                "task_features.org_id",
                "task_features.id",
                "task_features.task_id",
                "task_features.feature_revision_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "task_resource_id", "task_id", "product_revision_id"],
            [
                "task_resources.org_id",
                "task_resources.id",
                "task_resources.task_id",
                "task_resources.product_revision_id",
            ],
        ),
    )


class SandboxRun(Tenant, Base):
    __tablename__ = "sandbox_runs"
    input_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(40))
    policy_revision: Mapped[str] = mapped_column(String(100))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    runtime_profile_digest: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[UUID] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20))
    token_id: Mapped[UUID | None] = mapped_column()
    scope_snapshot: Mapped[list[str]] = mapped_column(JSONB)
    capture_key: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "request_hash"),
        UniqueConstraint("org_id", "capture_key"),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint("org_id", "id", "input_id", "task_id", "job_id"),
        ForeignKeyConstraint(
            ["org_id", "input_id", "task_id", "document_id"],
            [
                "sandbox_inputs.org_id",
                "sandbox_inputs.id",
                "sandbox_inputs.task_id",
                "sandbox_inputs.document_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "job_id", "task_id", "document_id"],
            ["jobs.org_id", "jobs.id", "jobs.task_id", "jobs.document_id"],
        ),
        fk("requested_by", "memberships", "user_id"),
        fk("token_id", "api_tokens"),
    )


class SandboxAttempt(Tenant, Base):
    __tablename__ = "sandbox_attempts"
    sandbox_run_id: Mapped[UUID] = mapped_column()
    input_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    attempt_id: Mapped[UUID] = mapped_column()
    instance_group_ref_hash: Mapped[str] = mapped_column(String(64))
    descriptor: Mapped[dict[str, Any]] = mapped_column(JSONB)
    completion_txid: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_code: Mapped[str | None] = mapped_column(String(100))
    cleanup_state: Mapped[str] = mapped_column(String(20), default="pending")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    runtime_versions: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "job_id", "attempt_id"),
        UniqueConstraint("org_id", "id", "sandbox_run_id", "input_id", "task_id"),
        ForeignKeyConstraint(
            ["org_id", "sandbox_run_id", "input_id", "task_id", "job_id"],
            [
                "sandbox_runs.org_id",
                "sandbox_runs.id",
                "sandbox_runs.input_id",
                "sandbox_runs.task_id",
                "sandbox_runs.job_id",
            ],
        ),
    )


class SandboxArtifact(Tenant, Base):
    __tablename__ = "sandbox_artifacts"
    sandbox_run_id: Mapped[UUID] = mapped_column()
    attempt_record_id: Mapped[UUID] = mapped_column()
    input_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(30))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_artifact_id: Mapped[UUID | None] = mapped_column()
    plaintext_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(80))
    object_key: Mapped[str] = mapped_column(Text)
    provenance_manifest_hash: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(30))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "attempt_record_id", "ordinal"),
        UniqueConstraint(
            "org_id", "id", "sandbox_run_id", "input_id", "task_id", "attempt_record_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "attempt_record_id", "sandbox_run_id", "input_id", "task_id"],
            [
                "sandbox_attempts.org_id",
                "sandbox_attempts.id",
                "sandbox_attempts.sandbox_run_id",
                "sandbox_attempts.input_id",
                "sandbox_attempts.task_id",
            ],
        ),
        ForeignKeyConstraint(
            [
                "org_id",
                "parent_artifact_id",
                "sandbox_run_id",
                "input_id",
                "task_id",
                "attempt_record_id",
            ],
            [
                "sandbox_artifacts.org_id",
                "sandbox_artifacts.id",
                "sandbox_artifacts.sandbox_run_id",
                "sandbox_artifacts.input_id",
                "sandbox_artifacts.task_id",
                "sandbox_artifacts.attempt_record_id",
            ],
        ),
    )


class SandboxFetchReceipt(Tenant, Base):
    __tablename__ = "sandbox_fetch_receipts"
    sandbox_run_id: Mapped[UUID] = mapped_column()
    attempt_record_id: Mapped[UUID] = mapped_column()
    input_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    request_ordinal: Mapped[int] = mapped_column(Integer)
    parent_redirect_id: Mapped[UUID | None] = mapped_column()
    url_sha256: Mapped[str] = mapped_column(String(64))
    encrypted_request_metadata: Mapped[str | None] = mapped_column(Text)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    response_bytes: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    decision_code: Mapped[str] = mapped_column(String(100))
    policy_revision: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bundle_artifact_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "attempt_record_id", "request_ordinal"),
        UniqueConstraint("org_id", "id", "attempt_record_id"),
        ForeignKeyConstraint(
            ["org_id", "attempt_record_id", "sandbox_run_id", "input_id", "task_id"],
            [
                "sandbox_attempts.org_id",
                "sandbox_attempts.id",
                "sandbox_attempts.sandbox_run_id",
                "sandbox_attempts.input_id",
                "sandbox_attempts.task_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "parent_redirect_id", "attempt_record_id"],
            [
                "sandbox_fetch_receipts.org_id",
                "sandbox_fetch_receipts.id",
                "sandbox_fetch_receipts.attempt_record_id",
            ],
        ),
        ForeignKeyConstraint(
            [
                "org_id",
                "bundle_artifact_id",
                "sandbox_run_id",
                "input_id",
                "task_id",
                "attempt_record_id",
            ],
            [
                "sandbox_artifacts.org_id",
                "sandbox_artifacts.id",
                "sandbox_artifacts.sandbox_run_id",
                "sandbox_artifacts.input_id",
                "sandbox_artifacts.task_id",
                "sandbox_artifacts.attempt_record_id",
            ],
        ),
    )
