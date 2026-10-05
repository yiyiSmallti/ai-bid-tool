"""Tenant-scoped durable agent checkpoints and encrypted, append-only history."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(columns, target, parent=None):
    return ForeignKeyConstraint(
        ["org_id", *columns],
        [f"{target}.{c}" for c in ["org_id", *(parent or columns)]],
        deferrable=True,
        initially="DEFERRED",
    )


class AgentPrincipal(Tenant, Base):
    __tablename__ = "agent_principals"
    user_id: Mapped[UUID] = mapped_column()
    membership_id: Mapped[UUID] = mapped_column()
    initial_grants: Mapped[list[str]] = mapped_column(JSONB)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    authority_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "user_id"),
        fk(["membership_id", "user_id"], "memberships", ["id", "user_id"]),
        CheckConstraint(
            "jsonb_typeof(initial_grants)='array' AND jsonb_typeof(scopes)='array' AND initial_grants @> scopes",
            name="agent_principal_scopes",
        ),
    )


class AgentSession(Tenant, Base):
    __tablename__ = "agent_sessions"
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    principal_id: Mapped[UUID] = mapped_column()
    owner_user_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    steps_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_seconds_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vendor_calls_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_job_id: Mapped[UUID | None] = mapped_column()
    current_run_id: Mapped[UUID | None] = mapped_column()
    pause_id: Mapped[UUID | None] = mapped_column()
    tool_schema_sha256: Mapped[str] = mapped_column(String(64))
    model_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    start_idempotency_key: Mapped[UUID] = mapped_column()
    start_request_sha256: Mapped[str] = mapped_column(String(64))
    start_receipt_enc: Mapped[str | None] = mapped_column(Text)
    cancel_idempotency_key: Mapped[UUID | None] = mapped_column()
    cancel_request_sha256: Mapped[str | None] = mapped_column(String(64))
    cancel_receipt_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "task_id", "document_id"),
        UniqueConstraint("org_id", "id", "principal_id", "owner_user_id"),
        UniqueConstraint("org_id", "task_id", "owner_user_id", "start_idempotency_key"),
        fk(["task_id"], "tasks", ["id"]),
        fk(["task_id", "document_id"], "documents", ["task_id", "id"]),
        fk(
            ["task_id", "document_id", "extraction_job_id"],
            "jobs",
            ["task_id", "document_id", "id"],
        ),
        fk(["principal_id", "owner_user_id"], "agent_principals", ["id", "user_id"]),
        fk(["task_id", "document_id", "current_job_id"], "jobs", ["task_id", "document_id", "id"]),
        fk(["id", "pause_id"], "agent_pauses", ["session_id", "id"]),
        CheckConstraint(
            "(cancel_idempotency_key IS NULL AND cancel_request_sha256 IS NULL AND cancel_receipt_enc IS NULL) OR "
            "(cancel_idempotency_key IS NOT NULL AND cancel_request_sha256 IS NOT NULL AND cancel_receipt_enc IS NOT NULL AND state='cancelled')",
            name="agent_cancel_receipt",
        ),
        CheckConstraint(
            "state IN ('queued','running','waiting_job','paused','completed','partial','failed','cancelled')",
            name="agent_session_state",
        ),
        CheckConstraint(
            "revision>=1 AND steps_used>=0 AND active_seconds_used>=0 AND vendor_calls_used>=0",
            name="agent_session_counters",
        ),
    )


class AgentStep(Tenant, Base):
    __tablename__ = "agent_steps"
    session_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="planned", server_default="planned")
    command: Mapped[str | None] = mapped_column(String(100))
    created_by_job_id: Mapped[UUID] = mapped_column()
    created_by_run_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_transition_job_id: Mapped[UUID] = mapped_column()
    last_transition_run_id: Mapped[UUID] = mapped_column()
    invocation_id: Mapped[UUID | None] = mapped_column()
    arguments_enc: Mapped[str | None] = mapped_column(Text)
    arguments_sha256: Mapped[str | None] = mapped_column(String(64))
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    schema_sha256: Mapped[str] = mapped_column(String(64))
    child_job_id: Mapped[UUID | None] = mapped_column()
    result_enc: Mapped[str | None] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    usage_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "session_id", "id"),
        UniqueConstraint("org_id", "session_id", "ordinal"),
        UniqueConstraint("org_id", "invocation_id"),
        fk(
            ["session_id", "task_id", "document_id"],
            "agent_sessions",
            ["id", "task_id", "document_id"],
        ),
        fk(
            ["task_id", "document_id", "created_by_job_id"],
            "jobs",
            ["task_id", "document_id", "id"],
        ),
        fk(
            ["task_id", "document_id", "last_transition_job_id"],
            "jobs",
            ["task_id", "document_id", "id"],
        ),
        fk(["task_id", "document_id", "child_job_id"], "jobs", ["task_id", "document_id", "id"]),
        CheckConstraint(
            "ordinal>=1 AND revision>=1 AND kind IN ('decision','tool') AND state IN ('planned','submitted','waiting_job','completed','failed','uncertain')",
            name="agent_step_state",
        ),
        CheckConstraint("exit_code IN (0,2,3,4,5)", name="agent_step_exit"),
    )


class AgentMessage(Tenant, Base):
    __tablename__ = "agent_messages"
    session_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    author_user_id: Mapped[UUID | None] = mapped_column()
    step_id: Mapped[UUID | None] = mapped_column()
    content_enc: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[UUID | None] = mapped_column()
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    receipt_enc: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "session_id", "ordinal"),
        UniqueConstraint("org_id", "session_id", "idempotency_key"),
        fk(["session_id"], "agent_sessions", ["id"]),
        fk(["session_id", "step_id"], "agent_steps", ["session_id", "id"]),
        fk(["author_user_id"], "memberships", ["user_id"]),
        CheckConstraint(
            "(idempotency_key IS NULL AND request_sha256 IS NULL AND receipt_enc IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_sha256 IS NOT NULL AND receipt_enc IS NOT NULL AND role='human')",
            name="agent_message_receipt",
        ),
        CheckConstraint(
            "ordinal>=1 AND role IN ('human','assistant','tool','system') AND length(content)<=8000",
            name="agent_message_shape",
        ),
    )


class AgentPause(Tenant, Base):
    __tablename__ = "agent_pauses"
    session_id: Mapped[UUID] = mapped_column()
    step_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    question: Mapped[str] = mapped_column(Text)
    question_enc: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(30))
    resource_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    budget_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    input_sha256: Mapped[str] = mapped_column(String(64))
    resolved_by: Mapped[UUID | None] = mapped_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_idempotency_key: Mapped[UUID | None] = mapped_column()
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    receipt_enc: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "session_id", "id"),
        UniqueConstraint("org_id", "session_id", "resume_idempotency_key"),
        fk(["session_id"], "agent_sessions", ["id"]),
        fk(["session_id", "step_id"], "agent_steps", ["session_id", "id"]),
        fk(["resolved_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "(resume_idempotency_key IS NULL AND request_sha256 IS NULL AND receipt_enc IS NULL) OR "
            "(resume_idempotency_key IS NOT NULL AND request_sha256 IS NOT NULL AND receipt_enc IS NOT NULL AND status='resolved')",
            name="agent_pause_receipt",
        ),
        CheckConstraint(
            "kind IN ('budget','human_action','authority','recovery') AND status IN ('pending','resolved','cancelled','expired') AND length(question) BETWEEN 1 AND 2000",
            name="agent_pause_shape",
        ),
        CheckConstraint("(kind='budget')=(budget_ref IS NOT NULL)", name="agent_pause_budget"),
        Index(
            "agent_one_pending_pause",
            "org_id",
            "session_id",
            unique=True,
            postgresql_where=text("status='pending'"),
        ),
    )


class AgentJobLink(Tenant, Base):
    __tablename__ = "agent_job_links"
    session_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    role: Mapped[str] = mapped_column(String(20))
    step_id: Mapped[UUID | None] = mapped_column()
    owned: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "session_id", "job_id"),
        fk(
            ["session_id", "task_id", "document_id"],
            "agent_sessions",
            ["id", "task_id", "document_id"],
        ),
        fk(["task_id", "document_id", "job_id"], "jobs", ["task_id", "document_id", "id"]),
        fk(["session_id", "step_id"], "agent_steps", ["session_id", "id"]),
        CheckConstraint(
            "role IN ('controller','tool') AND (role<>'tool' OR step_id IS NOT NULL)",
            name="agent_link_role",
        ),
        Index("agent_owned_job", "org_id", "job_id", unique=True, postgresql_where=text("owned")),
    )
