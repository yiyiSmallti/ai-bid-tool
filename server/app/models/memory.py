"""Tenant memory revisions and durable retrieval, feedback and call lineage."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant
from app.models.response_cards import tenant_fk


class Memory(Tenant, Base):
    __tablename__ = "memories"
    scope: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[UUID | None] = mapped_column()
    task_id: Mapped[UUID | None] = mapped_column()
    current_revision_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_feedback_event_id: Mapped[UUID | None] = mapped_column()
    generator_version: Mapped[str | None] = mapped_column(String(100))
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    search_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    search_kind: Mapped[str | None] = mapped_column(Text)
    search_status: Mapped[str | None] = mapped_column(Text)
    search_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "source_feedback_event_id", "generator_version"),
        tenant_fk("user_id", "memberships", "user_id"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("source_feedback_event_id", "memory_feedback_events"),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision_id", "revision"],
            [
                "memory_revisions.org_id",
                "memory_revisions.memory_id",
                "memory_revisions.id",
                "memory_revisions.revision",
            ],
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
            name="memory_current_revision",
        ),
    )


class MemoryRevision(Tenant, Base):
    __tablename__ = "memory_revisions"
    memory_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    normalized_text: Mapped[str] = mapped_column(Text)
    normalized_tags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[str] = mapped_column(String(20))
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_token_id: Mapped[UUID | None] = mapped_column()
    confirmed_by: Mapped[UUID | None] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(String(20))
    decision_reason_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[UUID | None] = mapped_column()
    card_id: Mapped[UUID | None] = mapped_column()
    card_revision_id: Mapped[UUID | None] = mapped_column()
    feedback_event_id: Mapped[UUID | None] = mapped_column()
    proposal_job_id: Mapped[UUID | None] = mapped_column()
    proposal_run_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "memory_id", "revision"),
        UniqueConstraint("org_id", "memory_id", "id"),
        UniqueConstraint("org_id", "memory_id", "id", "revision"),
        tenant_fk("memory_id", "memories"),
        tenant_fk("created_by", "memberships", "user_id"),
        tenant_fk("confirmed_by", "memberships", "user_id"),
        tenant_fk("actor_token_id", "api_tokens"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("feedback_event_id", "memory_feedback_events"),
        tenant_fk("proposal_job_id", "jobs"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "task_id"],
            ["response_cards.org_id", "response_cards.id", "response_cards.task_id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "card_id", "card_revision_id"],
            [
                "response_card_revisions.org_id",
                "response_card_revisions.card_id",
                "response_card_revisions.id",
            ],
        ),
    )


class MemoryScopeEpoch(Base):
    __tablename__ = "memory_scope_epochs"
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, default=0)


class MemoryFeedbackEvent(Tenant, Base):
    __tablename__ = "memory_feedback_events"
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    model_job_id: Mapped[UUID] = mapped_column()
    card_id: Mapped[UUID] = mapped_column()
    before_revision_id: Mapped[UUID] = mapped_column()
    after_revision_id: Mapped[UUID] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20), default="session")
    kind: Mapped[str] = mapped_column(String(30))
    review_domain: Mapped[str | None] = mapped_column(String(20))
    encrypted_summary: Mapped[str] = mapped_column(Text)
    sanitized_sha256: Mapped[str] = mapped_column(String(64))
    sanitizer_version: Mapped[str] = mapped_column(String(100))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "card_id", "after_revision_id", "kind"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("document_id", "documents"),
        tenant_fk("model_job_id", "jobs"),
        tenant_fk("actor_user_id", "memberships", "user_id"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "task_id"],
            ["response_cards.org_id", "response_cards.id", "response_cards.task_id"],
        ),
        *[
            ForeignKeyConstraint(
                ["org_id", "card_id", column],
                [
                    "response_card_revisions.org_id",
                    "response_card_revisions.card_id",
                    "response_card_revisions.id",
                ],
            )
            for column in ("before_revision_id", "after_revision_id")
        ],
    )


class MemoryEvalSample(Tenant, Base):
    __tablename__ = "memory_eval_samples"
    task_id: Mapped[UUID] = mapped_column()
    feedback_event_id: Mapped[UUID] = mapped_column()
    card_id: Mapped[UUID] = mapped_column()
    before_revision_id: Mapped[UUID] = mapped_column()
    after_revision_id: Mapped[UUID] = mapped_column()
    label: Mapped[str] = mapped_column(String(30))
    actor_user_id: Mapped[UUID] = mapped_column()
    generator_version: Mapped[str] = mapped_column(String(100))
    sanitized_sha256: Mapped[str] = mapped_column(String(64))
    encrypted_summary: Mapped[str] = mapped_column(Text)
    review_state: Mapped[str] = mapped_column(String(20), default="unreviewed")
    review_revision: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_by: Mapped[UUID | None] = mapped_column()
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "feedback_event_id", "generator_version"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("feedback_event_id", "memory_feedback_events"),
        tenant_fk("actor_user_id", "memberships", "user_id"),
        tenant_fk("reviewed_by", "memberships", "user_id"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "task_id"],
            ["response_cards.org_id", "response_cards.id", "response_cards.task_id"],
        ),
        *[
            ForeignKeyConstraint(
                ["org_id", "card_id", column],
                [
                    "response_card_revisions.org_id",
                    "response_card_revisions.card_id",
                    "response_card_revisions.id",
                ],
            )
            for column in ("before_revision_id", "after_revision_id")
        ],
    )


class MemoryRetrieval(Tenant, Base):
    __tablename__ = "memory_retrievals"
    user_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID | None] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    encrypted_query: Mapped[str] = mapped_column(Text)
    encrypted_snapshot: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("user_id", "memberships", "user_id"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("actor_token_id", "api_tokens"),
    )


class MemoryRetrievalItem(Tenant, Base):
    __tablename__ = "memory_retrieval_items"
    retrieval_id: Mapped[UUID] = mapped_column()
    memory_id: Mapped[UUID] = mapped_column()
    memory_revision_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    sent_sha256: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(Boolean)
    omission_reason: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "retrieval_id", "memory_revision_id"),
        tenant_fk("retrieval_id", "memory_retrievals"),
        ForeignKeyConstraint(
            ["org_id", "memory_id", "memory_revision_id", "revision"],
            [
                "memory_revisions.org_id",
                "memory_revisions.memory_id",
                "memory_revisions.id",
                "memory_revisions.revision",
            ],
        ),
    )


class MemoryCallInput(Tenant, Base):
    __tablename__ = "memory_call_inputs"
    task_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    call_id: Mapped[UUID] = mapped_column()
    retrieval_id: Mapped[UUID] = mapped_column()
    requirement_ids: Mapped[list[str]] = mapped_column(JSONB)
    memories: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(100))
    encrypted_prompt: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    usage_record_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "job_id", "run_id", "call_id"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("retrieval_id", "memory_retrievals"),
        ForeignKeyConstraint(
            ["org_id", "job_id", "task_id"], ["jobs.org_id", "jobs.id", "jobs.task_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "job_id", "run_id", "call_id"],
            [
                "vendor_calls.org_id",
                "vendor_calls.job_id",
                "vendor_calls.run_id",
                "vendor_calls.id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "usage_record_id", "job_id", "run_id", "call_id"],
            [
                "usage_records.org_id",
                "usage_records.id",
                "usage_records.job_id",
                "usage_records.run_id",
                "usage_records.call_id",
            ],
        ),
    )
