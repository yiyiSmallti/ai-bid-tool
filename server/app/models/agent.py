from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
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
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class AgentJobLink(Base):
    __tablename__ = "agent_job_links"
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    step_id: Mapped[UUID | None] = mapped_column(nullable=True)
    owned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((role)::text = ANY ((ARRAY['controller'::character varying, 'tool'::character varying])::text[])) AND (((role)::text <> 'tool'::text) OR (step_id IS NOT NULL))))",
            name="agent_link_role",
        ),
        UniqueConstraint("session_id", "job_id", name="agent_job_links_session_id_job_id_key"),
        PrimaryKeyConstraint("id", name="agent_job_links_pkey"),
        ForeignKeyConstraint(
            ["session_id", "step_id"],
            ["agent_steps.session_id", "agent_steps.id"],
            name="agent_job_links_session_id_step_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["session_id", "task_id", "document_id"],
            ["agent_sessions.id", "agent_sessions.task_id", "agent_sessions.document_id"],
            name="agent_job_links_session_id_task_id_document_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_job_links_task_id_document_id_job_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("agent_owned_job", "job_id", unique=True, postgresql_where=sql_text("owned")),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    step_id: Mapped[UUID | None] = mapped_column(nullable=True)
    content_enc: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[UUID | None] = mapped_column(nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((idempotency_key IS NULL) AND (request_sha256 IS NULL) AND (receipt_enc IS NULL)) OR ((idempotency_key IS NOT NULL) AND (request_sha256 IS NOT NULL) AND (receipt_enc IS NOT NULL) AND ((role)::text = 'human'::text))))",
            name="agent_message_receipt",
        ),
        CheckConstraint(
            "(((ordinal >= 1) AND ((role)::text = ANY ((ARRAY['human'::character varying, 'assistant'::character varying, 'tool'::character varying, 'system'::character varying])::text[])) AND (length(content) <= 8000)))",
            name="agent_message_shape",
        ),
        UniqueConstraint(
            "session_id", "idempotency_key", name="agent_messages_session_id_idempotency_key_key"
        ),
        UniqueConstraint("session_id", "ordinal", name="agent_messages_session_id_ordinal_key"),
        PrimaryKeyConstraint("id", name="agent_messages_pkey"),
        ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="agent_messages_session_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["session_id", "step_id"],
            ["agent_steps.session_id", "agent_steps.id"],
            name="agent_messages_session_id_step_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class AgentPause(Base):
    __tablename__ = "agent_pauses"
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    step_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=sql_text("'pending'::character varying"),
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_enc: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resource_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_idempotency_key: Mapped[UUID | None] = mapped_column(nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((resume_idempotency_key IS NULL) AND (request_sha256 IS NULL) AND (receipt_enc IS NULL)) OR ((resume_idempotency_key IS NOT NULL) AND (request_sha256 IS NOT NULL) AND (receipt_enc IS NOT NULL) AND ((status)::text = 'resolved'::text))))",
            name="agent_pause_receipt",
        ),
        CheckConstraint(
            "((((kind)::text = ANY ((ARRAY['human_action'::character varying, 'authority'::character varying, 'recovery'::character varying])::text[])) AND ((status)::text = ANY ((ARRAY['pending'::character varying, 'resolved'::character varying, 'cancelled'::character varying, 'expired'::character varying])::text[])) AND ((length(question) >= 1) AND (length(question) <= 2000))))",
            name="agent_pause_shape",
        ),
        UniqueConstraint("session_id", "id", name="agent_pauses_session_id_id_key"),
        UniqueConstraint(
            "session_id",
            "resume_idempotency_key",
            name="agent_pauses_session_id_resume_idempotency_key_key",
        ),
        PrimaryKeyConstraint("id", name="agent_pauses_pkey"),
        ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="agent_pauses_session_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["session_id", "step_id"],
            ["agent_steps.session_id", "agent_steps.id"],
            name="agent_pauses_session_id_step_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "agent_one_pending_pause",
            "session_id",
            unique=True,
            postgresql_where=sql_text("((status)::text = 'pending'::text)"),
        ),
    )


class AgentPrincipal(Base):
    __tablename__ = "agent_principals"
    initial_grants: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    authority_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((jsonb_typeof(initial_grants) = 'array'::text) AND (jsonb_typeof(scopes) = 'array'::text) AND (initial_grants @> scopes)))",
            name="agent_principal_scopes",
        ),
        PrimaryKeyConstraint("id", name="agent_principals_pkey"),
    )


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    principal_id: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        server_default=sql_text("'queued'::character varying"),
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    steps_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    active_seconds_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    vendor_calls_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pause_id: Mapped[UUID | None] = mapped_column(nullable=True)
    tool_schema_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    start_idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    start_request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    start_receipt_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_idempotency_key: Mapped[UUID | None] = mapped_column(nullable=True)
    cancel_request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_receipt_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((cancel_idempotency_key IS NULL) AND (cancel_request_sha256 IS NULL) AND (cancel_receipt_enc IS NULL)) OR ((cancel_idempotency_key IS NOT NULL) AND (cancel_request_sha256 IS NOT NULL) AND (cancel_receipt_enc IS NOT NULL) AND ((state)::text = 'cancelled'::text))))",
            name="agent_cancel_receipt",
        ),
        CheckConstraint(
            "(((revision >= 1) AND (steps_used >= 0) AND (active_seconds_used >= 0) AND (vendor_calls_used >= 0)))",
            name="agent_session_counters",
        ),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'waiting_job'::character varying, 'paused'::character varying, 'completed'::character varying, 'partial'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))",
            name="agent_session_state",
        ),
        UniqueConstraint(
            "id", "principal_id", name="agent_sessions_id_principal_id_owner_user_id_key"
        ),
        UniqueConstraint(
            "id", "task_id", "document_id", name="agent_sessions_id_task_id_document_id_key"
        ),
        UniqueConstraint(
            "task_id",
            "start_idempotency_key",
            name="agent_sessions_task_id_owner_user_id_start_idempoten_key",
        ),
        PrimaryKeyConstraint("id", name="agent_sessions_pkey"),
        ForeignKeyConstraint(
            ["id", "pause_id"],
            ["agent_pauses.session_id", "agent_pauses.id"],
            name="agent_sessions_id_pause_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "current_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_sessions_task_id_document_id_current_job_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "extraction_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_sessions_task_id_document_id_extraction_job_i_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id"],
            ["documents.task_id", "documents.id"],
            name="agent_sessions_task_id_document_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="agent_sessions_task_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="planned",
        server_default=sql_text("'planned'::character varying"),
    )
    command: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_job_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_run_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    last_transition_job_id: Mapped[UUID] = mapped_column(nullable=False)
    last_transition_run_id: Mapped[UUID] = mapped_column(nullable=False)
    invocation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    arguments_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    arguments_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    schema_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    child_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    result_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    usage_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(
        nullable=False,
        primary_key=True,
        default=uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint("((exit_code = ANY (ARRAY[0, 2, 3, 4, 5])))", name="agent_step_exit"),
        CheckConstraint(
            "(((ordinal >= 1) AND (revision >= 1) AND ((kind)::text = ANY ((ARRAY['decision'::character varying, 'tool'::character varying])::text[])) AND ((state)::text = ANY ((ARRAY['planned'::character varying, 'submitted'::character varying, 'waiting_job'::character varying, 'completed'::character varying, 'failed'::character varying, 'uncertain'::character varying])::text[]))))",
            name="agent_step_state",
        ),
        UniqueConstraint("invocation_id", name="agent_steps_invocation_id_key"),
        UniqueConstraint("session_id", "id", name="agent_steps_session_id_id_key"),
        UniqueConstraint("session_id", "ordinal", name="agent_steps_session_id_ordinal_key"),
        PrimaryKeyConstraint("id", name="agent_steps_pkey"),
        ForeignKeyConstraint(
            ["session_id", "task_id", "document_id"],
            ["agent_sessions.id", "agent_sessions.task_id", "agent_sessions.document_id"],
            name="agent_steps_session_id_task_id_document_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "child_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_steps_task_id_document_id_child_job_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "created_by_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_steps_task_id_document_id_created_by_job_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id", "document_id", "last_transition_job_id"],
            ["jobs.task_id", "jobs.document_id", "jobs.id"],
            name="agent_steps_task_id_document_id_last_transition_job_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
