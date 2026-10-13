from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class Memory(Base):
    __tablename__ = "memories"
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_feedback_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    generator_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    search_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    search_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "((((scope)::text = 'library'::text) AND (task_id IS NULL)))", name="memories_check"
        ),
        CheckConstraint(
            "(((source_feedback_event_id IS NULL) = (generator_version IS NULL)))",
            name="memories_check1",
        ),
        CheckConstraint("((revision >= 1))", name="memories_revision_check"),
        UniqueConstraint(
            "source_feedback_event_id",
            "generator_version",
            name="memories_source_feedback_event_id_generator_version_key",
        ),
        PrimaryKeyConstraint("id", name="memories_pkey"),
        ForeignKeyConstraint(
            ["source_feedback_event_id"],
            ["memory_feedback_events.id"],
            name="memories_source_feedback_event_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memories_task_id_fkey"),
        ForeignKeyConstraint(
            ["id", "current_revision_id", "revision"],
            ["memory_revisions.memory_id", "memory_revisions.id", "memory_revisions.revision"],
            name="memory_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_memory_browse", column("created_at").desc(), column("id").desc()),
        Index(
            "management_memory_filter",
            "search_status",
            "search_kind",
            "search_expires_at",
            column("created_at").desc(),
            column("id").desc(),
        ),
        Index("management_memory_prefix", "search_vector", postgresql_using="gin"),
        Index("management_memory_tags", "search_tags", postgresql_using="gin"),
        Index("memory_scope_lookup", "scope"),
    )


class MemoryCallInput(Base):
    __tablename__ = "memory_call_inputs"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    call_id: Mapped[UUID] = mapped_column(nullable=False)
    retrieval_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    memories: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_record_id: Mapped[UUID | None] = mapped_column(nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((state)::text = 'completed'::text) = (usage_record_id IS NOT NULL)))",
            name="memory_call_inputs_check",
        ),
        CheckConstraint(
            "((((manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((prompt_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="memory_call_inputs_check1",
        ),
        CheckConstraint(
            "(((jsonb_typeof(memories) = 'array'::text) AND (jsonb_array_length(memories) <= 50)))",
            name="memory_call_inputs_memories_check",
        ),
        CheckConstraint(
            "(((jsonb_typeof(requirement_ids) = 'array'::text) AND (jsonb_array_length(requirement_ids) > 0)))",
            name="memory_call_inputs_requirement_ids_check",
        ),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['admitted'::character varying, 'completed'::character varying, 'unknown'::character varying])::text[])))",
            name="memory_call_inputs_state_check",
        ),
        UniqueConstraint(
            "job_id", "run_id", "call_id", name="memory_call_inputs_job_id_run_id_call_id_key"
        ),
        PrimaryKeyConstraint("id", name="memory_call_inputs_pkey"),
        ForeignKeyConstraint(
            ["job_id", "run_id", "call_id"],
            ["vendor_calls.job_id", "vendor_calls.run_id", "vendor_calls.id"],
            name="memory_call_inputs_job_id_run_id_call_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id"],
            ["jobs.id", "jobs.task_id"],
            name="memory_call_inputs_job_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["retrieval_id"], ["memory_retrievals.id"], name="memory_call_inputs_retrieval_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memory_call_inputs_task_id_fkey"),
        ForeignKeyConstraint(
            ["usage_record_id", "job_id", "run_id", "call_id"],
            [
                "usage_records.id",
                "usage_records.job_id",
                "usage_records.run_id",
                "usage_records.call_id",
            ],
            name="memory_call_inputs_usage_record_id_job_id_run_id_ca_fkey",
        ),
        Index("memory_calls_job", "job_id"),
    )


class MemoryEvalSample(Base):
    __tablename__ = "memory_eval_samples"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    feedback_event_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    before_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    after_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(100), nullable=False)
    sanitized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_summary: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[str] = mapped_column(String(20), nullable=False, default="unreviewed")
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((review_state)::text = 'unreviewed'::text) = (reviewed_at IS NULL)))",
            name="memory_eval_samples_check1",
        ),
        CheckConstraint(
            "((review_revision >= 1))", name="memory_eval_samples_review_revision_check"
        ),
        CheckConstraint(
            "(((review_state)::text = ANY ((ARRAY['unreviewed'::character varying, 'accepted'::character varying, 'excluded'::character varying])::text[])))",
            name="memory_eval_samples_review_state_check",
        ),
        CheckConstraint(
            "(((sanitized_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="memory_eval_samples_sanitized_sha256_check",
        ),
        UniqueConstraint(
            "feedback_event_id",
            "generator_version",
            name="memory_eval_samples_feedback_event_id_generator_vers_key",
        ),
        PrimaryKeyConstraint("id", name="memory_eval_samples_pkey"),
        ForeignKeyConstraint(
            ["card_id", "after_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="memory_eval_samples_card_id_after_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "before_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="memory_eval_samples_card_id_before_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id"],
            ["response_cards.id", "response_cards.task_id"],
            name="memory_eval_samples_card_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["feedback_event_id"],
            ["memory_feedback_events.id"],
            name="memory_eval_samples_feedback_event_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memory_eval_samples_task_id_fkey"),
        Index("memory_eval_task", "task_id", "created_at", "id"),
    )


class MemoryFeedbackEvent(Base):
    __tablename__ = "memory_feedback_events"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    model_job_id: Mapped[UUID] = mapped_column(nullable=False)
    card_id: Mapped[UUID] = mapped_column(nullable=False)
    before_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    after_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="session")
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    review_domain: Mapped[str | None] = mapped_column(String(20), nullable=True)
    encrypted_summary: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sanitizer_version: Mapped[str] = mapped_column(String(100), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((actor_kind)::text = 'session'::text))",
            name="memory_feedback_events_actor_kind_check",
        ),
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['card_rejected'::character varying, 'card_edited'::character varying, 'card_confirmed'::character varying])::text[])))",
            name="memory_feedback_events_kind_check",
        ),
        CheckConstraint(
            "(((sanitized_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="memory_feedback_events_sanitized_sha256_check",
        ),
        UniqueConstraint(
            "card_id",
            "after_revision_id",
            "kind",
            name="memory_feedback_events_card_id_after_revision_id_kin_key",
        ),
        PrimaryKeyConstraint("id", name="memory_feedback_events_pkey"),
        ForeignKeyConstraint(
            ["card_id", "after_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="memory_feedback_events_card_id_after_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "before_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="memory_feedback_events_card_id_before_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id"],
            ["response_cards.id", "response_cards.task_id"],
            name="memory_feedback_events_card_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="memory_feedback_events_document_id_fkey"
        ),
        ForeignKeyConstraint(
            ["model_job_id"], ["jobs.id"], name="memory_feedback_events_model_job_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memory_feedback_events_task_id_fkey"),
        Index(
            "management_memory_feedback_browse",
            "task_id",
            column("created_at").desc(),
            column("id").desc(),
        ),
        Index("memory_feedback_task", "task_id", "created_at", "id"),
    )


class MemoryRetrievalItem(Base):
    __tablename__ = "memory_retrieval_items"
    retrieval_id: Mapped[UUID] = mapped_column(nullable=False)
    memory_id: Mapped[UUID] = mapped_column(nullable=False)
    memory_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    omission_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint("(((rank >= 1) AND (revision >= 1)))", name="memory_retrieval_items_check"),
        CheckConstraint(
            "((selected = (omission_reason IS NULL)))", name="memory_retrieval_items_check1"
        ),
        CheckConstraint(
            "((((content_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((sent_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="memory_retrieval_items_check2",
        ),
        CheckConstraint(
            "(((omission_reason IS NULL) OR ((omission_reason)::text = ANY ((ARRAY['shadowed'::character varying, 'same_priority_conflict'::character varying, 'context_limit'::character varying, 'top_k_limit'::character varying])::text[]))))",
            name="memory_retrieval_items_omission_reason_check",
        ),
        UniqueConstraint(
            "retrieval_id",
            "memory_revision_id",
            name="memory_retrieval_items_retrieval_id_memory_revision__key",
        ),
        PrimaryKeyConstraint("id", name="memory_retrieval_items_pkey"),
        ForeignKeyConstraint(
            ["memory_id", "memory_revision_id", "revision"],
            ["memory_revisions.memory_id", "memory_revisions.id", "memory_revisions.revision"],
            name="memory_retrieval_items_memory_id_memory_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["retrieval_id"],
            ["memory_retrievals.id"],
            name="memory_retrieval_items_retrieval_id_fkey",
        ),
    )


class MemoryRetrieval(Base):
    __tablename__ = "memory_retrievals"
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    encrypted_query: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((jsonb_typeof(data) = 'object'::text))", name="memory_retrievals_data_check"
        ),
        PrimaryKeyConstraint("id", name="memory_retrievals_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="memory_retrievals_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memory_retrievals_task_id_fkey"),
    )


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"
    memory_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_reason_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    card_id: Mapped[UUID | None] = mapped_column(nullable=True)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feedback_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    proposal_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    proposal_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'worker'::character varying, 'agent'::character varying])::text[])))",
            name="memory_revisions_actor_kind_check",
        ),
        CheckConstraint(
            "((((status)::text = 'active'::text) = (confirmed_at IS NOT NULL)))",
            name="memory_revisions_check1",
        ),
        CheckConstraint(
            "((((status)::text <> 'active'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND ((decision)::text = 'approve'::text))))",
            name="memory_revisions_check2",
        ),
        CheckConstraint(
            "((((status)::text <> 'candidate'::text) OR (decision IS NULL)))",
            name="memory_revisions_check3",
        ),
        CheckConstraint(
            "(((decision IS NULL) = (decision_reason_sha256 IS NULL)))",
            name="memory_revisions_check4",
        ),
        CheckConstraint(
            "(((card_id IS NULL) = (card_revision_id IS NULL)))", name="memory_revisions_check5"
        ),
        CheckConstraint(
            "(((card_id IS NULL) OR (task_id IS NOT NULL)))", name="memory_revisions_check6"
        ),
        CheckConstraint(
            "(((proposal_job_id IS NULL) = (proposal_run_id IS NULL)))",
            name="memory_revisions_check7",
        ),
        CheckConstraint(
            "(((jsonb_typeof(content) = 'object'::text) AND COALESCE(((content ->> 'kind'::text) = ANY (ARRAY['rule'::text, 'preference'::text])), false) AND COALESCE(((content ->> 'conflict_key'::text) ~ '^[a-z0-9][a-z0-9_.-]{0,99}$'::text), false) AND COALESCE(((length((content ->> 'text'::text)) >= 1) AND (length((content ->> 'text'::text)) <= 2000)), false) AND COALESCE(((content ->> 'text'::text) ~ '[^[:space:]]'::text), false) AND COALESCE((jsonb_typeof((content -> 'tags'::text)) = 'array'::text), false) AND (jsonb_array_length((content -> 'tags'::text)) <= 20)))",
            name="memory_revisions_content_check",
        ),
        CheckConstraint(
            "(((content_sha256)::text ~ '^[0-9a-f]{64}$'::text))",
            name="memory_revisions_content_sha256_check",
        ),
        CheckConstraint(
            "(((decision IS NULL) OR ((decision)::text = ANY ((ARRAY['approve'::character varying, 'reject'::character varying, 'disable'::character varying, 'delete'::character varying])::text[]))))",
            name="memory_revisions_decision_check",
        ),
        CheckConstraint(
            "(((decision_reason_sha256 IS NULL) OR ((decision_reason_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="memory_revisions_decision_reason_sha256_check",
        ),
        CheckConstraint("((revision >= 1))", name="memory_revisions_revision_check"),
        CheckConstraint(
            "(((jsonb_typeof(source) = 'object'::text) AND COALESCE(((source ->> 'origin'::text) = ANY (ARRAY['human'::text, 'system'::text])), false)))",
            name="memory_revisions_source_check",
        ),
        CheckConstraint(
            "(((status)::text = ANY ((ARRAY['candidate'::character varying, 'active'::character varying, 'disabled'::character varying])::text[])))",
            name="memory_revisions_status_check",
        ),
        UniqueConstraint("memory_id", "id", name="memory_revisions_memory_id_id_key"),
        UniqueConstraint(
            "memory_id", "id", "revision", name="memory_revisions_memory_id_id_revision_key"
        ),
        UniqueConstraint("memory_id", "revision", name="memory_revisions_memory_id_revision_key"),
        PrimaryKeyConstraint("id", name="memory_revisions_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="memory_revisions_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["card_id", "card_revision_id"],
            ["response_card_revisions.card_id", "response_card_revisions.id"],
            name="memory_revisions_card_id_card_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["card_id", "task_id"],
            ["response_cards.id", "response_cards.task_id"],
            name="memory_revisions_card_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["feedback_event_id"],
            ["memory_feedback_events.id"],
            name="memory_revisions_feedback_event_id_fkey",
        ),
        ForeignKeyConstraint(
            ["memory_id"], ["memories.id"], name="memory_revisions_memory_id_fkey"
        ),
        ForeignKeyConstraint(
            ["proposal_job_id"], ["jobs.id"], name="memory_revisions_proposal_job_id_fkey"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="memory_revisions_task_id_fkey"),
        Index(
            "management_memory_active_history",
            "memory_id",
            postgresql_where=sql_text("((status)::text = 'active'::text)"),
        ),
        Index(
            "management_memory_history", "memory_id", column("revision").desc(), column("id").desc()
        ),
        Index("memory_revision_search", "status", "memory_id"),
        Index("memory_revision_tags", "normalized_tags", postgresql_using="gin"),
    )


class MemoryScopeEpoch(Base):
    __tablename__ = "memory_scope_epochs"
    scope: Mapped[str] = mapped_column(String(20), nullable=False, primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint(
            "((((scope)::text = 'library'::text) AND (epoch >= 0)))",
            name="memory_scope_epochs_check",
        ),
        PrimaryKeyConstraint("scope", name="memory_scope_epochs_pkey"),
    )
