from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Identity):
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id"), nullable=False, index=True)


class Org(Identity, Base):
    __tablename__ = "orgs"
    org_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    review_authority_epoch: Mapped[int] = mapped_column(BigInteger, default=1)
    __table_args__ = (CheckConstraint("id = org_id", name="org_self_scope"),)


class User(Identity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    review_authority_epoch: Mapped[int] = mapped_column(BigInteger, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Tenant, Base):
    __tablename__ = "memberships"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "user_id", name="agent_membership_owner"),
        UniqueConstraint("org_id", "user_id"),
        CheckConstraint(
            "role IN ('admin', 'bidder', 'technical', 'viewer')", name="membership_role"
        ),
    )


class ApiToken(Tenant, Base):
    __tablename__ = "api_tokens"
    user_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint(
            "NOT (scopes ?| ARRAY['attachment:write','attachment:review','attachment:manage','attachment:original:read','task:attachment','attachment:privacy','attachment:page:read'])",
            name="token_forbidden_attachment_scopes",
        ),
        CheckConstraint(
            "NOT (scopes ?| ARRAY['agent:read','agent:run','agent:cancel'])",
            name="token_forbidden_agent_scopes",
        ),
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "digest"),
        ForeignKeyConstraint(["org_id", "user_id"], ["memberships.org_id", "memberships.user_id"]),
        CheckConstraint(
            "NOT (scopes ? 'evidence:confirm') AND NOT (scopes ? 'export') AND NOT (scopes ? 'screenshot:ingest')",
            name="token_forbidden_scopes",
        ),
        CheckConstraint(
            "NOT (scopes ?| ARRAY['task:budget:write','billing:alert:write'])",
            name="token_forbidden_budget_scopes",
        ),
        CheckConstraint("NOT (scopes ? 'provider:write')", name="token_no_provider_write"),
        CheckConstraint("NOT (scopes ? 'token:create')", name="token_forbidden_creation_scope"),
        CheckConstraint(
            "NOT (scopes ? 'template:file:read')", name="token_forbidden_template_file_scope"
        ),
        CheckConstraint(
            "NOT (scopes ? 'evidence:annotate')", name="token_forbidden_annotation_scope"
        ),
        CheckConstraint(
            "NOT (scopes ?| ARRAY['certificate:lifecycle','profile:lifecycle'])",
            name="token_forbidden_library_lifecycle_scopes",
        ),
        CheckConstraint("NOT (scopes ? 'check:decide')", name="token_forbidden_check_scopes"),
        CheckConstraint(
            "NOT (scopes ? 'score:rubric:review')", name="token_forbidden_score_scopes"
        ),
        CheckConstraint(
            "NOT (scopes ?| ARRAY['memory:approve','memory:manage','memory:eval:read','memory:eval:review'])",
            name="token_forbidden_memory_scopes",
        ),
        CheckConstraint(
            "NOT (scopes ? 'confidential:write') AND NOT (scopes ? 'confidential:reveal')",
            name="token_forbidden_confidential_scopes",
        ),
    )


class Task(Tenant, Base):
    __tablename__ = "tasks"
    model_redaction_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    model_redaction_revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    model_redaction_by: Mapped[UUID | None] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    tender_number: Mapped[str | None] = mapped_column(String(100))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    budget_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    budget_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    budget_state: Mapped[str] = mapped_column(String(30), default="active", server_default="active")
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        CheckConstraint("budget_limit IS NULL OR budget_limit >= 0", name="task_budget_amount"),
        CheckConstraint(
            "budget_currency ~ '^[A-Z]{3}$' AND budget_revision >= 1", name="task_budget_identity"
        ),
        CheckConstraint(
            "budget_state IN ('active','currency_review_required')", name="task_budget_state"
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "model_redaction_by"], ["memberships.org_id", "memberships.user_id"]
        ),
    )


class Document(Tenant, Base):
    __tablename__ = "documents"
    task_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(100))
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    citation_mode: Mapped[str | None] = mapped_column(String(10))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id", name="agent_document_task"),
        UniqueConstraint("org_id", "id", "task_id", name="sandbox_document_task"),
        UniqueConstraint("org_id", "task_id", "sha256"),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
    )


class Chunk(Tenant, Base):
    __tablename__ = "chunks"
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    page: Mapped[int | None] = mapped_column(Integer)
    # PDF chunks are ordered by page unless a sequence is given explicitly.
    seq: Mapped[int] = mapped_column(
        Integer, default=lambda context: context.get_current_parameters()["page"]
    )
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    citation_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # Word chunks: list of Block dicts; PDF chunks: null.
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "document_id", "seq"),
        ForeignKeyConstraint(["org_id", "document_id"], ["documents.org_id", "documents.id"]),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        CheckConstraint("page > 0", name="chunk_page_positive"),
    )


class Requirement(Tenant, Base):
    __tablename__ = "requirements"
    task_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    chunk_id: Mapped[UUID] = mapped_column()
    page: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    quote: Mapped[str] = mapped_column(Text)
    model_quote: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20))
    starred: Mapped[bool] = mapped_column(Boolean)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "job_id", "fingerprint"),
        ForeignKeyConstraint(["org_id", "job_id"], ["jobs.org_id", "jobs.id"]),
        ForeignKeyConstraint(["org_id", "document_id"], ["documents.org_id", "documents.id"]),
        ForeignKeyConstraint(["org_id", "chunk_id"], ["chunks.org_id", "chunks.id"]),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        CheckConstraint("length(quote) > 0", name="requirement_citation"),
    )


class UsageRecord(Tenant, Base):
    __tablename__ = "usage_records"
    image_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    image_price_revision: Mapped[str | None] = mapped_column(String(100))
    image_input_sha256: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[UUID | None] = mapped_column()
    provider_config_id: Mapped[UUID | None] = mapped_column()
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float | None] = mapped_column(Numeric(18, 8))
    capability: Mapped[str] = mapped_column(String(20), default="llm", server_default="llm")
    payer: Mapped[str] = mapped_column(
        String(30), default="org_direct", server_default="org_direct"
    )
    task_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    billing_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    price_revision: Mapped[str] = mapped_column(
        String(100), default="legacy_unknown", server_default="legacy_unknown"
    )
    search_requests: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    test_only: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    platform_model_id: Mapped[str | None] = mapped_column(String(40))
    charge: Mapped[float | None] = mapped_column(Numeric(18, 8))
    job_id: Mapped[UUID | None] = mapped_column()
    run_id: Mapped[UUID | None] = mapped_column()
    call_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "job_id", "run_id", "call_id"),
        UniqueConstraint(
            "org_id", "id", "job_id", "run_id", "call_id", name="memory_usage_call_binding"
        ),
        ForeignKeyConstraint(
            ["org_id", "provider_config_id"], ["provider_configs.org_id", "provider_configs.id"]
        ),
        CheckConstraint("call_id IS NULL OR (job_id IS NOT NULL AND run_id IS NOT NULL)"),
        Index("usage_records_job", "org_id", "job_id"),
        ForeignKeyConstraint(["org_id", "job_id"], ["jobs.org_id", "jobs.id"]),
        ForeignKeyConstraint(
            ["org_id", "job_id", "run_id", "call_id"],
            [
                "vendor_calls.org_id",
                "vendor_calls.job_id",
                "vendor_calls.run_id",
                "vendor_calls.id",
            ],
        ),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
    )


class VendorCall(Tenant, Base):
    """Durable admission, including unresolved requests whose reservation must survive a crash."""

    __tablename__ = "vendor_calls"
    task_id: Mapped[UUID | None] = mapped_column()
    capability: Mapped[str] = mapped_column(String(20), default="llm", server_default="llm")
    payer: Mapped[str] = mapped_column(
        String(30), default="org_direct", server_default="org_direct"
    )
    budget_revision: Mapped[int | None] = mapped_column(Integer)
    reserved_task_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    price_revision: Mapped[str] = mapped_column(
        String(100), default="legacy_unknown", server_default="legacy_unknown"
    )
    request_sha256: Mapped[str] = mapped_column(String(64), default="0" * 64)
    quote: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    job_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    reserved_charge: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    charge: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "task_id"], ["tasks.org_id", "tasks.id"], name="budget_call_task_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "task_id", "budget_revision"],
            [
                "task_budget_revisions.org_id",
                "task_budget_revisions.task_id",
                "task_budget_revisions.revision",
            ],
            name="budget_call_revision_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("reserved_task_amount >= 0", name="budget_call_amount"),
        CheckConstraint(
            "(task_id IS NULL) = (budget_revision IS NULL)", name="budget_call_task_revision"
        ),
        Index("vendor_calls_task_budget", "org_id", "task_id", "state"),
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "job_id", "run_id", "id"),
        ForeignKeyConstraint(["org_id", "job_id"], ["jobs.org_id", "jobs.id"]),
        CheckConstraint("reserved_charge >= 0 AND (charge IS NULL OR charge >= 0)"),
        CheckConstraint("state IN ('pending', 'completed', 'unknown', 'not_sent')"),
        CheckConstraint(
            "state <> 'not_sent' OR (reserved_charge = 0 AND charge IS NULL)",
            name="vendor_calls_not_sent_no_charge",
        ),
        CheckConstraint("(state = 'completed') = (charge IS NOT NULL)"),
        Index("vendor_calls_job", "org_id", "job_id"),
        Index("vendor_calls_unsettled", "org_id", "state"),
    )


def agent_origin_constraints(table: str):
    """Bind persisted origin to its complete tenant, owner and session chain."""
    return (
        ForeignKeyConstraint(
            ["org_id", "on_behalf_of_user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name=f"{table}_agent_origin_owner_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["org_id", "agent_principal_id", "on_behalf_of_user_id"],
            ["agent_principals.org_id", "agent_principals.id", "agent_principals.user_id"],
            name=f"{table}_agent_origin_principal_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["org_id", "agent_session_id", "agent_principal_id", "on_behalf_of_user_id"],
            [
                "agent_sessions.org_id",
                "agent_sessions.id",
                "agent_sessions.principal_id",
                "agent_sessions.owner_user_id",
            ],
            name=f"{table}_agent_origin_session_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["org_id", "agent_session_id", "agent_step_id"],
            ["agent_steps.org_id", "agent_steps.session_id", "agent_steps.id"],
            name=f"{table}_agent_origin_step_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "initiated_by IS NULL OR initiated_by IN ('human','legacy_unknown','builtin_agent','external_agent')",
            name=f"{table}_agent_origin_shape",
        ),
        CheckConstraint(
            "(initiated_by='builtin_agent' AND agent_principal_id IS NOT NULL AND agent_session_id IS NOT NULL "
            "AND on_behalf_of_user_id IS NOT NULL AND actor_token_id IS NULL) OR "
            "(initiated_by='external_agent' AND actor_token_id IS NOT NULL AND on_behalf_of_user_id IS NOT NULL "
            "AND agent_principal_id IS NULL AND agent_session_id IS NULL AND agent_step_id IS NULL) OR "
            "(COALESCE(initiated_by,'legacy_unknown') IN ('human','legacy_unknown') "
            "AND agent_principal_id IS NULL AND agent_session_id IS NULL AND agent_step_id IS NULL)",
            name=f"{table}_agent_origin_binding",
        ),
        Index(f"{table}_agent_session", "org_id", "agent_session_id"),
    )


class Job(Tenant, Base):
    __tablename__ = "jobs"
    vendor_cost_history_complete: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    task_id: Mapped[UUID | None] = mapped_column()
    document_id: Mapped[UUID | None] = mapped_column()
    provider_config_id: Mapped[UUID | None] = mapped_column()
    provider_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    kind: Mapped[str] = mapped_column(String(20))
    cache_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    queue_id: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[UUID | None] = mapped_column()
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reasoning: Mapped[str | None] = mapped_column(String(20))
    actor_user_id: Mapped[UUID | None] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str | None] = mapped_column(String(20))
    actor_scopes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    initiated_by: Mapped[str | None] = mapped_column(String(30))
    on_behalf_of_user_id: Mapped[UUID | None] = mapped_column()
    agent_principal_id: Mapped[UUID | None] = mapped_column()
    agent_session_id: Mapped[UUID | None] = mapped_column()
    agent_step_id: Mapped[UUID | None] = mapped_column()
    invocation_id: Mapped[UUID | None] = mapped_column()
    command: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        CheckConstraint(
            "kind NOT IN ('annotation_render','annotation_release') OR (task_id IS NOT NULL AND document_id IS NOT NULL AND actor_user_id IS NOT NULL AND actor_kind='session' AND actor_token_id IS NULL)",
            name="annotation_job_kind",
        ),
        *agent_origin_constraints("jobs"),
        UniqueConstraint("org_id", "agent_session_id", "id", name="agent_job_session"),
        ForeignKeyConstraint(
            ["org_id", "agent_session_id", "task_id", "document_id"],
            [
                "agent_sessions.org_id",
                "agent_sessions.id",
                "agent_sessions.task_id",
                "agent_sessions.document_id",
            ],
            name="job_agent_task_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name="budget_job_actor_user_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "actor_token_id"],
            ["api_tokens.org_id", "api_tokens.id"],
            name="budget_job_actor_token_fk",
        ),
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "document_id", "id", name="agent_job_document"),
        UniqueConstraint("org_id", "cache_key"),
        ForeignKeyConstraint(
            ["org_id", "provider_config_id"], ["provider_configs.org_id", "provider_configs.id"]
        ),
        CheckConstraint(
            "(kind = 'provider_test' AND task_id IS NULL AND document_id IS NULL) OR (kind <> 'provider_test' AND task_id IS NOT NULL AND document_id IS NOT NULL)",
            name="job_document_binding",
        ),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(["org_id", "document_id"], ["documents.org_id", "documents.id"]),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="job_status"
        ),
        Index("jobs_state", "org_id", "status"),
    )


class Product(Tenant, Base):
    __tablename__ = "products"
    created_by: Mapped[UUID] = mapped_column()
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8), default="active", server_default="active"
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision"],
            [
                "product_revisions.org_id",
                "product_revisions.product_id",
                "product_revisions.revision",
            ],
            name="product_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0", name="product_revision_positive"),
        CheckConstraint(
            "lifecycle_state IN ('active','inactive') AND lifecycle_revision >= 0 "
            "AND (lifecycle_revision > 0 OR lifecycle_state = 'active')",
            name="product_lifecycle_valid",
        ),
    )


class ProductRevision(Tenant, Base):
    __tablename__ = "product_revisions"
    product_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "product_id", "id"),
        UniqueConstraint("org_id", "product_id", "revision"),
        ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="revision_object"),
    )


class TaskResource(Tenant, Base):
    __tablename__ = "task_resources"
    task_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    product_revision_id: Mapped[UUID] = mapped_column()
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "product_id", "product_revision_id"],
            ["product_revisions.org_id", "product_revisions.product_id", "product_revisions.id"],
        ),
        Index(
            "task_product_active_slot",
            "org_id",
            "task_id",
            "product_id",
            "lot",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class AuditLog(Tenant, Base):
    __tablename__ = "audit_logs"
    actor_user_id: Mapped[UUID | None] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[UUID] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    resource_revision_id_text: Mapped[str | None] = mapped_column(
        Text, Computed("details->>'new_revision_id'", persisted=True)
    )
    initiated_by: Mapped[str | None] = mapped_column(String(30))
    on_behalf_of_user_id: Mapped[UUID | None] = mapped_column()
    agent_principal_id: Mapped[UUID | None] = mapped_column()
    agent_session_id: Mapped[UUID | None] = mapped_column()
    agent_step_id: Mapped[UUID | None] = mapped_column()
    invocation_id: Mapped[UUID | None] = mapped_column()
    command: Mapped[str | None] = mapped_column(String(100))
    actor_kind: Mapped[str | None] = mapped_column(String(20))
    job_id: Mapped[UUID | None] = mapped_column()
    run_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (
        *agent_origin_constraints("audit_logs"),
        Index(
            "management_product_audit_author",
            "org_id",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=text(
                "action IN ('resource.product.create','resource.product.update')"
            ),
        ),
        Index(
            "management_feature_audit_author",
            "org_id",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=text(
                "action IN ('resource.feature.create','resource.feature.update')"
            ),
        ),
        Index(
            "management_certificate_audit_author",
            "org_id",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=text(
                "action IN ('resource.certificate.create','resource.certificate.update','resource.certificate.file.create')"
            ),
        ),
        Index(
            "management_profile_audit_author",
            "org_id",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=text(
                "action IN ('resource.profile.create','resource.profile.update')"
            ),
        ),
        ForeignKeyConstraint(
            ["org_id", "agent_session_id", "job_id"],
            ["jobs.org_id", "jobs.agent_session_id", "jobs.id"],
            name="audit_agent_job_session_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["org_id", "job_id"],
            ["jobs.org_id", "jobs.id"],
            name="audit_agent_job_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(["org_id", "actor_token_id"], ["api_tokens.org_id", "api_tokens.id"]),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="audit_object"),
    )


class SimulatedResource(Tenant, Base):
    """A product or feature created by a product simulation; the mark covers every revision."""

    __tablename__ = "simulated_resources"
    job_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID | None] = mapped_column()
    feature_id: Mapped[UUID | None] = mapped_column()
    source_url: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "product_id"),
        UniqueConstraint("org_id", "feature_id"),
        ForeignKeyConstraint(["org_id", "job_id"], ["jobs.org_id", "jobs.id"]),
        ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        ForeignKeyConstraint(["org_id", "feature_id"], ["features.org_id", "features.id"]),
        CheckConstraint(
            "(product_id IS NULL) <> (feature_id IS NULL)", name="simulated_one_resource"
        ),
    )


class Feature(Tenant, Base):
    __tablename__ = "features"
    created_by: Mapped[UUID] = mapped_column()
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8), default="active", server_default="active"
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision"],
            [
                "feature_revisions.org_id",
                "feature_revisions.feature_id",
                "feature_revisions.revision",
            ],
            name="feature_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0", name="feature_revision_positive"),
        CheckConstraint(
            "lifecycle_state IN ('active','inactive') AND lifecycle_revision >= 0 "
            "AND (lifecycle_revision > 0 OR lifecycle_state = 'active')",
            name="feature_lifecycle_valid",
        ),
    )


class FeatureRevision(Tenant, Base):
    __tablename__ = "feature_revisions"
    feature_id: Mapped[UUID] = mapped_column()
    product_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "feature_id", "id"),
        UniqueConstraint("org_id", "feature_id", "revision"),
        ForeignKeyConstraint(["org_id", "feature_id"], ["features.org_id", "features.id"]),
        ForeignKeyConstraint(["org_id", "product_id"], ["products.org_id", "products.id"]),
        CheckConstraint("revision > 0", name="feature_revision_number_positive"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="feature_revision_object"),
        CheckConstraint(
            "data->>'product_id' IS NOT NULL AND data->>'product_id' = product_id::text",
            name="feature_product_matches",
        ),
        CheckConstraint(
            "data->>'status' IS NOT NULL AND data->>'status' IN ('implemented', 'developing', 'planned')",
            name="feature_declared_status",
        ),
    )


class TaskFeature(Tenant, Base):
    __tablename__ = "task_features"
    task_id: Mapped[UUID] = mapped_column()
    feature_id: Mapped[UUID] = mapped_column()
    feature_revision_id: Mapped[UUID] = mapped_column()
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "feature_id", "feature_revision_id"],
            ["feature_revisions.org_id", "feature_revisions.feature_id", "feature_revisions.id"],
        ),
        Index(
            "task_feature_active_slot",
            "org_id",
            "task_id",
            "feature_id",
            "lot",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class Certificate(Tenant, Base):
    __tablename__ = "certificates"
    created_by: Mapped[UUID] = mapped_column()
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8), default="active", server_default="active"
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision"],
            [
                "certificate_revisions.org_id",
                "certificate_revisions.certificate_id",
                "certificate_revisions.revision",
            ],
            name="certificate_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0", name="certificate_revision_positive"),
        CheckConstraint(
            "lifecycle_state IN ('active','inactive') AND lifecycle_revision >= 0 "
            "AND (lifecycle_revision > 0 OR lifecycle_state = 'active')",
            name="certificate_lifecycle_valid",
        ),
    )


class CertificateRevision(Tenant, Base):
    __tablename__ = "certificate_revisions"
    certificate_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "certificate_id", "id"),
        UniqueConstraint("org_id", "certificate_id", "revision"),
        ForeignKeyConstraint(
            ["org_id", "certificate_id"], ["certificates.org_id", "certificates.id"]
        ),
        CheckConstraint("revision > 0", name="certificate_revision_number_positive"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="certificate_revision_object"),
        CheckConstraint(
            "data->>'kind' IS NOT NULL AND data->>'kind' IN ('qualification', 'personnel')",
            name="certificate_declared_kind",
        ),
        CheckConstraint(
            "data->>'name' IS NOT NULL AND length(btrim(data->>'name')) BETWEEN 1 AND 200 "
            "AND data->>'number' IS NOT NULL AND length(btrim(data->>'number')) BETWEEN 1 AND 200",
            name="certificate_declared_identity",
        ),
        CheckConstraint(
            "(data->>'valid_from' IS NULL OR (data->>'valid_from' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND (data->>'valid_from')::date IS NOT NULL)) "
            "AND (data->>'valid_until' IS NULL OR (data->>'valid_until' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND (data->>'valid_until')::date IS NOT NULL)) "
            "AND (data->>'valid_from' IS NULL OR data->>'valid_until' IS NULL OR (data->>'valid_from')::date <= (data->>'valid_until')::date)",
            name="certificate_declared_dates",
        ),
    )


class TaskCertificate(Tenant, Base):
    __tablename__ = "task_certificates"
    task_id: Mapped[UUID] = mapped_column()
    certificate_id: Mapped[UUID] = mapped_column()
    certificate_revision_id: Mapped[UUID] = mapped_column()
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "id",
            "task_id",
            "certificate_id",
            "certificate_revision_id",
            name="task_certificate_source_binding",
        ),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_revisions.org_id",
                "certificate_revisions.certificate_id",
                "certificate_revisions.id",
            ],
        ),
        Index(
            "task_certificate_active_slot",
            "org_id",
            "task_id",
            "certificate_id",
            "lot",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class OrgProfile(Tenant, Base):
    __tablename__ = "org_profiles"
    created_by: Mapped[UUID] = mapped_column()
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8), default="active", server_default="active"
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision"],
            [
                "org_profile_revisions.org_id",
                "org_profile_revisions.profile_id",
                "org_profile_revisions.revision",
            ],
            name="profile_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0", name="profile_revision_positive"),
        CheckConstraint(
            "lifecycle_state IN ('active','inactive') AND lifecycle_revision >= 0 "
            "AND (lifecycle_revision > 0 OR lifecycle_state = 'active')",
            name="profile_lifecycle_valid",
        ),
    )


class OrgProfileRevision(Tenant, Base):
    __tablename__ = "org_profile_revisions"
    profile_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "profile_id", "id"),
        UniqueConstraint("org_id", "profile_id", "revision"),
        ForeignKeyConstraint(["org_id", "profile_id"], ["org_profiles.org_id", "org_profiles.id"]),
        CheckConstraint("revision > 0", name="profile_revision_number_positive"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="profile_revision_object"),
        CheckConstraint(
            "data->>'name' IS NOT NULL AND jsonb_typeof(data->'name') = 'string' "
            "AND length(btrim(data->>'name')) BETWEEN 1 AND 200",
            name="profile_declared_name",
        ),
        CheckConstraint(
            "data->>'registration_details' IS NULL OR (jsonb_typeof(data->'registration_details') = 'string' "
            "AND length(btrim(data->>'registration_details')) BETWEEN 1 AND 10000)",
            name="profile_registration_details_text",
        ),
        CheckConstraint(
            "data->>'performance_summary' IS NULL OR (jsonb_typeof(data->'performance_summary') = 'string' "
            "AND length(btrim(data->>'performance_summary')) BETWEEN 1 AND 20000)",
            name="profile_performance_summary_text",
        ),
        CheckConstraint(
            "data->>'standard_wording' IS NULL OR (jsonb_typeof(data->'standard_wording') = 'string' "
            "AND length(btrim(data->>'standard_wording')) BETWEEN 1 AND 20000)",
            name="profile_standard_wording_text",
        ),
    )


class TaskOrgProfile(Tenant, Base):
    __tablename__ = "task_org_profiles"
    task_id: Mapped[UUID] = mapped_column()
    profile_id: Mapped[UUID] = mapped_column()
    profile_revision_id: Mapped[UUID] = mapped_column()
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "id",
            "task_id",
            "profile_revision_id",
            "lot",
            name="attachment_task_profile_binding",
        ),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "profile_id", "profile_revision_id"],
            [
                "org_profile_revisions.org_id",
                "org_profile_revisions.profile_id",
                "org_profile_revisions.id",
            ],
        ),
        Index(
            "task_profile_active_slot",
            "org_id",
            "task_id",
            "profile_id",
            "lot",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class Template(Tenant, Base):
    __tablename__ = "templates"
    created_by: Mapped[UUID] = mapped_column()
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8), default="active", server_default="active"
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    search_vector: Mapped[str] = mapped_column(TSVECTOR, server_default=text("''::tsvector"))
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('active','inactive') AND lifecycle_revision>=0 "
            "AND (lifecycle_revision>0 OR lifecycle_state='active')",
            name="template_lifecycle_valid",
        ),
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision"],
            [
                "template_revisions.org_id",
                "template_revisions.template_id",
                "template_revisions.revision",
            ],
            name="template_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0", name="template_revision_positive"),
    )


class TemplateRevision(Tenant, Base):
    __tablename__ = "template_revisions"
    template_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    file: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(400))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "template_id", "id"),
        UniqueConstraint("org_id", "template_id", "revision"),
        ForeignKeyConstraint(["org_id", "template_id"], ["templates.org_id", "templates.id"]),
        CheckConstraint("revision > 0", name="template_revision_number_positive"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="template_revision_object"),
        CheckConstraint(
            "data->>'name' IS NOT NULL AND jsonb_typeof(data->'name') = 'string' "
            "AND length(btrim(data->>'name')) BETWEEN 1 AND 200",
            name="template_declared_name",
        ),
        CheckConstraint("jsonb_typeof(file) = 'object'", name="template_file_object"),
        CheckConstraint(
            "file->>'name' IS NOT NULL AND jsonb_typeof(file->'name') = 'string' "
            "AND length(btrim(file->>'name')) BETWEEN 1 AND 200",
            name="template_file_name",
        ),
        CheckConstraint(
            "file->>'sha256' IS NOT NULL AND (file->>'sha256') ~ '^[0-9a-f]{64}$'",
            name="template_file_hash",
        ),
        CheckConstraint(
            "file->>'size_bytes' IS NOT NULL AND jsonb_typeof(file->'size_bytes') = 'number' "
            "AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 "
            "AND (file->>'size_bytes')::numeric = trunc((file->>'size_bytes')::numeric)",
            name="template_file_size",
        ),
        CheckConstraint(
            "file->>'media_type' IS NOT NULL AND file->>'media_type' = "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document'",
            name="template_file_type",
        ),
        CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/template/' || template_id::text "
            "|| '/' || id::text || '/' || (file->>'sha256') || '.docx'",
            name="template_file_binding",
        ),
    )


class TaskTemplate(Tenant, Base):
    __tablename__ = "task_templates"
    task_id: Mapped[UUID] = mapped_column()
    template_id: Mapped[UUID] = mapped_column()
    template_revision_id: Mapped[UUID] = mapped_column()
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "template_id", "template_revision_id"],
            [
                "template_revisions.org_id",
                "template_revisions.template_id",
                "template_revisions.id",
            ],
        ),
        Index(
            "task_template_active_slot",
            "org_id",
            "task_id",
            "template_id",
            "lot",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class CertificateFile(Tenant, Base):
    __tablename__ = "certificate_files"
    certificate_id: Mapped[UUID] = mapped_column()
    certificate_revision_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    file: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(400))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "id",
            "certificate_id",
            "certificate_revision_id",
            name="certificate_file_source_binding",
        ),
        UniqueConstraint("org_id", "certificate_revision_id"),
        ForeignKeyConstraint(
            ["org_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_revisions.org_id",
                "certificate_revisions.certificate_id",
                "certificate_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        CheckConstraint("jsonb_typeof(file) = 'object'", name="certificate_file_object"),
        CheckConstraint(
            "file->>'name' IS NOT NULL AND jsonb_typeof(file->'name') = 'string' AND length(btrim(file->>'name')) BETWEEN 1 AND 200 AND position('/' in file->>'name') = 0 AND position(chr(92) in file->>'name') = 0 AND file->>'name' !~ '[[:cntrl:]]' AND lower(right(file->>'name',4)) = '.pdf'",
            name="certificate_file_name",
        ),
        CheckConstraint(
            "file->>'sha256' IS NOT NULL AND jsonb_typeof(file->'sha256') = 'string' AND (file->>'sha256') ~ '^[0-9a-f]{64}$'",
            name="certificate_file_hash",
        ),
        CheckConstraint(
            "file->>'size_bytes' IS NOT NULL AND jsonb_typeof(file->'size_bytes') = 'number' AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (file->>'size_bytes')::numeric = trunc((file->>'size_bytes')::numeric)",
            name="certificate_file_size",
        ),
        CheckConstraint(
            "file->>'page_count' IS NOT NULL AND jsonb_typeof(file->'page_count') = 'number' AND (file->>'page_count')::numeric BETWEEN 1 AND 200 AND (file->>'page_count')::numeric = trunc((file->>'page_count')::numeric)",
            name="certificate_file_pages",
        ),
        CheckConstraint(
            "file->>'media_type' IS NOT NULL AND file->>'media_type' = 'application/pdf'",
            name="certificate_file_type",
        ),
        CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/certificate/' || certificate_id::text || '/' || certificate_revision_id::text || '/' || (file->>'sha256') || '.pdf'",
            name="certificate_file_binding",
        ),
    )


class CertificateFilePart(Tenant, Base):
    """An uploaded file, kept unchanged, behind a composed certificate original."""

    __tablename__ = "certificate_file_parts"
    certificate_id: Mapped[UUID] = mapped_column()
    certificate_revision_id: Mapped[UUID] = mapped_column()
    certificate_file_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_start: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(400))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "certificate_file_id", "ordinal"),
        ForeignKeyConstraint(
            ["org_id", "certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.org_id",
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 20", name="certificate_part_ordinal"),
        CheckConstraint(
            "media_type IN ('application/pdf', 'image/png', 'image/jpeg')",
            name="certificate_part_type",
        ),
        CheckConstraint("rotation IN (0, 90, 180, 270)", name="certificate_part_rotation"),
    )


class EvidenceSource(Tenant, Base):
    __tablename__ = "evidence_sources"
    source_kind: Mapped[str] = mapped_column(
        String(40),
        default="user_supplied_certificate_pdf",
        server_default="user_supplied_certificate_pdf",
    )
    task_attachment_id: Mapped[UUID | None] = mapped_column()
    task_org_profile_id: Mapped[UUID | None] = mapped_column()
    profile_revision_id: Mapped[UUID | None] = mapped_column()
    profile_attachment_link_id: Mapped[UUID | None] = mapped_column()
    attachment_id: Mapped[UUID | None] = mapped_column()
    attachment_revision_id: Mapped[UUID | None] = mapped_column()
    attachment_file_id: Mapped[UUID | None] = mapped_column()
    approval_id: Mapped[UUID | None] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    task_certificate_id: Mapped[UUID | None] = mapped_column()
    certificate_id: Mapped[UUID | None] = mapped_column()
    certificate_revision_id: Mapped[UUID | None] = mapped_column()
    certificate_file_id: Mapped[UUID | None] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    page: Mapped[int] = mapped_column(Integer)
    render_profile: Mapped[str] = mapped_column(String(40), default="pdf-page-preview-v1")
    dpi: Mapped[int] = mapped_column(Integer, default=150)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(400))
    rendered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="unconfirmed_source")
    confirmed_by: Mapped[UUID | None] = mapped_column(nullable=True)
    eligible_for_draft_export: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id"),
        ForeignKeyConstraint(
            [
                "org_id",
                "task_attachment_id",
                "task_id",
                "task_org_profile_id",
                "profile_revision_id",
                "profile_attachment_link_id",
                "attachment_id",
                "attachment_revision_id",
                "attachment_file_id",
                "approval_id",
            ],
            [
                "task_attachments." + c
                for c in [
                    "org_id",
                    "id",
                    "task_id",
                    "task_org_profile_id",
                    "profile_revision_id",
                    "profile_attachment_link_id",
                    "attachment_id",
                    "attachment_revision_id",
                    "file_id",
                    "approval_id",
                ]
            ],
            name="attachment_source_exact_selection",
        ),
        CheckConstraint(
            "(source_kind='user_supplied_certificate_pdf' AND num_nonnulls(task_certificate_id,certificate_id,certificate_revision_id,certificate_file_id)=4 AND num_nonnulls(task_attachment_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,attachment_file_id,approval_id)=0) OR (source_kind='user_supplied_attachment_pdf' AND num_nonnulls(task_certificate_id,certificate_id,certificate_revision_id,certificate_file_id)=0 AND num_nonnulls(task_attachment_id,task_org_profile_id,profile_revision_id,profile_attachment_link_id,attachment_id,attachment_revision_id,attachment_file_id,approval_id)=8)",
            name="attachment_source_branches",
        ),
        Index(
            "attachment_source_identity",
            "org_id",
            "task_attachment_id",
            "page",
            "render_profile",
            unique=True,
            postgresql_where=text("source_kind='user_supplied_attachment_pdf'"),
        ),
        Index(
            "attachment_source_page",
            "org_id",
            "task_id",
            "created_at",
            "id",
            postgresql_where=text("source_kind='user_supplied_attachment_pdf'"),
        ),
        UniqueConstraint(
            "org_id",
            "id",
            "task_id",
            "task_certificate_id",
            "certificate_id",
            "certificate_revision_id",
            "certificate_file_id",
            name="annotation_certificate_source_scope",
        ),
        Index(
            "certificate_source_identity",
            "org_id",
            "task_certificate_id",
            "page",
            "render_profile",
            unique=True,
            postgresql_where=text("source_kind='user_supplied_certificate_pdf'"),
        ),
        ForeignKeyConstraint(
            [
                "org_id",
                "task_certificate_id",
                "task_id",
                "certificate_id",
                "certificate_revision_id",
            ],
            [
                "task_certificates.org_id",
                "task_certificates.id",
                "task_certificates.task_id",
                "task_certificates.certificate_id",
                "task_certificates.certificate_revision_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.org_id",
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
        ),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "created_by"], ["memberships.org_id", "memberships.user_id"]
        ),
        CheckConstraint("page BETWEEN 1 AND 200", name="source_page"),
        CheckConstraint(
            "render_profile = 'pdf-page-preview-v1' AND dpi = 150", name="source_profile"
        ),
        CheckConstraint(
            "status = 'unconfirmed_source' AND confirmed_by IS NULL AND eligible_for_draft_export = false",
            name="source_unconfirmed",
        ),
        CheckConstraint("jsonb_typeof(preview) = 'object'", name="source_preview_object"),
        CheckConstraint(
            "preview->>'name' IS NOT NULL AND jsonb_typeof(preview->'name') = 'string' AND length(btrim(preview->>'name')) BETWEEN 1 AND 200 AND position('/' in preview->>'name') = 0 AND position(chr(92) in preview->>'name') = 0 AND preview->>'name' !~ '[[:cntrl:]]' AND lower(right(preview->>'name',4)) = '.png'",
            name="source_preview_name",
        ),
        CheckConstraint(
            "preview->>'sha256' IS NOT NULL AND jsonb_typeof(preview->'sha256') = 'string' AND preview->>'sha256' ~ '^[0-9a-f]{64}$'",
            name="source_preview_hash",
        ),
        CheckConstraint(
            "preview->>'media_type' IS NOT NULL AND preview->>'media_type' = 'image/png'",
            name="source_preview_media",
        ),
        CheckConstraint(
            "preview->>'size_bytes' IS NOT NULL AND jsonb_typeof(preview->'size_bytes') = 'number' AND (preview->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (preview->>'size_bytes')::numeric = trunc((preview->>'size_bytes')::numeric)",
            name="source_preview_size",
        ),
        CheckConstraint(
            "preview->>'width_px' IS NOT NULL AND preview->>'height_px' IS NOT NULL AND jsonb_typeof(preview->'width_px') = 'number' AND jsonb_typeof(preview->'height_px') = 'number' AND (preview->>'width_px')::numeric BETWEEN 1 AND 8192 AND (preview->>'height_px')::numeric BETWEEN 1 AND 8192 AND (preview->>'width_px')::numeric = trunc((preview->>'width_px')::numeric) AND (preview->>'height_px')::numeric = trunc((preview->>'height_px')::numeric) AND (preview->>'width_px')::numeric * (preview->>'height_px')::numeric <= 20000000",
            name="source_preview_dimensions",
        ),
        CheckConstraint(
            "storage_key = 'org/' || org_id::text || '/evidence-source/' || id::text || '/' || (preview->>'sha256') || '.png'",
            name="source_preview_binding",
        ),
    )


class PlatformModel(Base):
    """Global catalog of platform-paid models; an approved exception to org scoping."""

    __tablename__ = "platform_models"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    capability: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str | None] = mapped_column(String(300))
    credential: Mapped[str] = mapped_column(String(40))
    vendor_input_usd_per_mtok: Mapped[float] = mapped_column(Numeric(12, 6))
    vendor_output_usd_per_mtok: Mapped[float] = mapped_column(Numeric(12, 6))
    sale_input_per_mtok: Mapped[float] = mapped_column(Numeric(12, 6))
    sale_output_per_mtok: Mapped[float] = mapped_column(Numeric(12, 6))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Vendor's official reasoning levels for this model; empty means not levelled.
    reasoning: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    default_reasoning: Mapped[str | None] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str] = mapped_column(String(254))


class PlatformAuditLog(Base):
    """Append-only record of platform operator actions; global like platform_models."""

    __tablename__ = "platform_audit_logs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_email: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(60))
    object_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)


class OrgBalance(Base):
    __tablename__ = "org_balances"
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    low_balance_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), default=0, server_default=text("0")
    )
    alert_revision: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    low_balance_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    alert_cycle: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class TaskBudgetRevision(Tenant, Base):
    __tablename__ = "task_budget_revisions"
    task_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(30))
    actor_user_id: Mapped[UUID | None] = mapped_column()
    origin: Mapped[str] = mapped_column(String(20))
    reason_sha256: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "revision"),
        ForeignKeyConstraint(["org_id", "task_id"], ["tasks.org_id", "tasks.id"]),
        ForeignKeyConstraint(
            ["org_id", "actor_user_id"], ["memberships.org_id", "memberships.user_id"]
        ),
        CheckConstraint('revision >= 1 AND ("limit" IS NULL OR "limit" >= 0)'),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$' AND state IN ('active','currency_review_required')"
        ),
        CheckConstraint("origin IN ('migration','create','human_update')"),
        CheckConstraint(
            "origin <> 'human_update' OR (actor_user_id IS NOT NULL AND reason_sha256 ~ '^[0-9a-f]{64}$')"
        ),
    )


class OrgBalanceNotice(Tenant, Base):
    __tablename__ = "org_balance_notices"
    policy_revision: Mapped[int] = mapped_column(Integer)
    cycle: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(3))
    available_balance: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "policy_revision", "cycle"),
        ForeignKeyConstraint(["org_id"], ["org_balances.org_id"]),
        CheckConstraint("policy_revision >= 1 AND cycle >= 1 AND threshold >= 0"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'"),
    )


class BalanceEntry(Tenant, Base):
    """Append-only ledger; the sum of amounts per org equals org_balances.balance."""

    __tablename__ = "balance_entries"
    kind: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    card_id: Mapped[UUID | None] = mapped_column()
    usage_record_id: Mapped[UUID | None] = mapped_column()
    actor: Mapped[str] = mapped_column(String(254))
    reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "usage_record_id"], ["usage_records.org_id", "usage_records.id"]
        ),
    )


class PlatformCard(Base):
    """Global recharge card; only the code hash and last four characters are stored."""

    __tablename__ = "platform_cards"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last4: Mapped[str] = mapped_column(String(4))
    face_value: Mapped[float] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(3))
    batch_id: Mapped[UUID] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="active")
    created_by: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    redeemed_org_id: Mapped[UUID | None] = mapped_column()
    redeemed_by: Mapped[UUID | None] = mapped_column()
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
