from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiToken(Base):
    __tablename__ = "api_tokens"
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Filled by the attribution trigger from the creating session's member.
    member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['agent:read'::text, 'agent:run'::text, 'agent:cancel'::text])))",
            name="token_forbidden_agent_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'evidence:annotate'::text)))", name="token_forbidden_annotation_scope"
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['attachment:write'::text, 'attachment:review'::text, 'attachment:manage'::text, 'attachment:original:read'::text, 'task:attachment'::text, 'attachment:privacy'::text, 'attachment:page:read'::text])))",
            name="token_forbidden_attachment_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['bid-review:upload'::text, 'bid-review:prepare'::text, 'bid-review:report:render'::text, 'bid-review:original:read'::text, 'bid-review:source:read'::text, 'bid-review:report:read'::text, 'bid-review:report:download'::text, 'bid-review:decide'::text, 'bid-review:evidence:review'::text, 'bid-review:price:release'::text, 'bid-review:outbound:authorize'::text, 'bid-review:classify'::text])))",
            name="token_forbidden_bid_review_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'check:decide'::text)))", name="token_forbidden_check_scopes"
        ),
        CheckConstraint(
            "(((NOT (scopes ? 'confidential:write'::text)) AND (NOT (scopes ? 'confidential:reveal'::text))))",
            name="token_forbidden_confidential_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['task:review-policy'::text, 'card:cosign'::text])))",
            name="token_forbidden_cosign_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'token:create'::text)))", name="token_forbidden_creation_scope"
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['certificate:lifecycle'::text, 'profile:lifecycle'::text])))",
            name="token_forbidden_library_lifecycle_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'member:manage'::text)))", name="token_forbidden_member_scope"
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['memory:approve'::text, 'memory:manage'::text, 'memory:eval:read'::text, 'memory:eval:review'::text])))",
            name="token_forbidden_memory_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['req:confirm'::text, 'req:manual'::text])))",
            name="token_forbidden_requirement_scopes",
        ),
        CheckConstraint(
            "(((NOT (scopes ? 'evidence:confirm'::text)) AND (NOT (scopes ? 'export'::text))))",
            name="token_forbidden_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'score:rubric:review'::text)))", name="token_forbidden_score_scopes"
        ),
        CheckConstraint(
            "((NOT (scopes ? 'screenshot:ingest'::text)))", name="token_forbidden_screenshot_scopes"
        ),
        CheckConstraint(
            "((NOT (scopes ? 'template:file:read'::text)))",
            name="token_forbidden_template_file_scope",
        ),
        CheckConstraint(
            "((NOT (scopes ?| ARRAY['task:members:write'::text, 'task:archive'::text, 'card:assign'::text, 'card:comment'::text, 'task:review-policy'::text, 'card:cosign'::text])))",
            name="token_forbidden_workflow_scopes",
        ),
        CheckConstraint(
            "((NOT (scopes ? 'provider:write'::text)))", name="token_no_provider_write"
        ),
        UniqueConstraint("digest", name="api_tokens_digest_key"),
        PrimaryKeyConstraint("id", name="api_tokens_pkey"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[UUID] = mapped_column(nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    initiated_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    agent_principal_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_session_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_step_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # Filled by the attribution trigger.
    actor_member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    invocation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    command: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_revision_id_text: Mapped[str | None] = mapped_column(
        Text, Computed("(details ->> 'new_revision_id'::text)", persisted=True), nullable=True
    )
    memory_revision_id_text: Mapped[str | None] = mapped_column(
        Text, Computed("(details ->> 'revision_id'::text)", persisted=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "((((initiated_by)::text = 'builtin_agent'::text) AND (agent_principal_id IS NOT NULL) AND (agent_session_id IS NOT NULL) AND (actor_token_id IS NULL)) OR (((initiated_by)::text = 'external_agent'::text) AND (actor_token_id IS NOT NULL) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)) OR (((COALESCE(initiated_by, 'legacy_unknown'::character varying))::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying])::text[])) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)))",
            name="audit_logs_agent_origin_binding",
        ),
        CheckConstraint(
            "(((initiated_by IS NULL) OR ((initiated_by)::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying, 'builtin_agent'::character varying, 'external_agent'::character varying])::text[]))))",
            name="audit_logs_agent_origin_shape",
        ),
        CheckConstraint("((jsonb_typeof(details) = 'object'::text))", name="audit_object"),
        PrimaryKeyConstraint("id", name="audit_logs_pkey"),
        ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="audit_agent_job_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["agent_session_id", "job_id"],
            ["jobs.agent_session_id", "jobs.id"],
            name="audit_agent_job_session_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["agent_session_id", "agent_step_id"],
            ["agent_steps.session_id", "agent_steps.id"],
            name="audit_logs_agent_origin_step_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="audit_logs_actor_token_id_fkey"
        ),
        Index(
            "annotation_invalidation_once",
            "object_id",
            unique=True,
            postgresql_where=sql_text("((action)::text = 'annotation.invalidate'::text)"),
        ),
        Index(
            "attachment_audit_request",
            "action",
            sql_text("((details ->> 'request_id'::text))"),
            unique=True,
            postgresql_where=sql_text(
                "(((action)::text = ANY ((ARRAY['attachment.create'::character varying, 'attachment.revise'::character varying, 'attachment.assign'::character varying, 'attachment.review.approve'::character varying, 'attachment.review.reject'::character varying, 'attachment.review.revoke'::character varying, 'attachment.deactivate'::character varying, 'profile.attachment.link'::character varying, 'profile.attachment.deactivate'::character varying, 'task.attachment.select'::character varying, 'task.attachment.deactivate'::character varying, 'attachment.source.create'::character varying, 'attachment.privacy.clear'::character varying, 'attachment.privacy.hold'::character varying, 'attachment.privacy.withdraw'::character varying])::text[])) AND ((details ->> 'request_id'::text) IS NOT NULL))"
            ),
        ),
        Index(
            "attachment_audit_revision",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['attachment.create'::character varying, 'attachment.revise'::character varying])::text[]))"
            ),
        ),
        Index("audit_logs_agent_session", "agent_session_id"),
        Index(
            "export_release_audit_once",
            "object_id",
            unique=True,
            postgresql_where=sql_text("((action)::text = 'export.released'::text)"),
        ),
        Index(
            "management_certificate_audit_author",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.certificate.create'::character varying, 'resource.certificate.update'::character varying, 'resource.certificate.file.create'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_certificate_lifecycle_audit",
            "object_id",
            sql_text("((details ->> 'event_id'::text))"),
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.certificate.deactivate'::character varying, 'resource.certificate.restore'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_feature_audit_author",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.feature.create'::character varying, 'resource.feature.update'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_feature_lifecycle_audit",
            "object_id",
            sql_text("((details ->> 'event_id'::text))"),
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.feature.deactivate'::character varying, 'resource.feature.restore'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_memory_audit_author",
            "memory_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['memory.create'::character varying, 'memory.update'::character varying, 'memory.approve'::character varying, 'memory.reject'::character varying, 'memory.disable'::character varying, 'memory.delete'::character varying, 'memory.candidate.publish'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_product_audit_author",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.product.create'::character varying, 'resource.product.update'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_product_lifecycle_audit",
            "object_id",
            sql_text("((details ->> 'event_id'::text))"),
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.product.deactivate'::character varying, 'resource.product.restore'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_profile_audit_author",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.profile.create'::character varying, 'resource.profile.update'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_profile_lifecycle_audit",
            "object_id",
            sql_text("((details ->> 'event_id'::text))"),
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.profile.deactivate'::character varying, 'resource.profile.restore'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_provider_audit_author",
            "object_id",
            postgresql_where=sql_text("((action)::text = 'provider.set'::text)"),
        ),
        Index(
            "management_template_audit_author",
            "resource_revision_id_text",
            "object_id",
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.template.create'::character varying, 'resource.template.update'::character varying])::text[]))"
            ),
        ),
        Index(
            "management_template_lifecycle_audit",
            "object_id",
            sql_text("((details ->> 'event_id'::text))"),
            postgresql_where=sql_text(
                "((action)::text = ANY ((ARRAY['resource.template.deactivate'::character varying, 'resource.template.restore'::character varying])::text[]))"
            ),
        ),
    )


class CertificateFilePart(Base):
    "An uploaded file, kept unchanged, behind a composed certificate original."

    __tablename__ = "certificate_file_parts"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_file_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    media_type: Mapped[str] = mapped_column(String(40), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rotation: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "(((storage_key)::text = (((((((('certificate/'::text) || (certificate_id)::text) || '/'::text) || (certificate_revision_id)::text) || '/parts/'::text) || (ordinal)::text) || '-'::text) || (sha256)::text)))",
            name="certificate_part_binding",
        ),
        CheckConstraint(
            "(((sha256)::text ~ '^[0-9a-f]{64}$'::text))", name="certificate_part_hash"
        ),
        CheckConstraint(
            "(((btrim((name)::text) <> ''::text) AND (POSITION(('/'::text) IN (name)) = 0) AND (POSITION((chr(92)) IN (name)) = 0) AND ((name)::text !~ '[[:cntrl:]]'::text)))",
            name="certificate_part_name",
        ),
        CheckConstraint("(((ordinal >= 1) AND (ordinal <= 20)))", name="certificate_part_ordinal"),
        CheckConstraint(
            "((((page_start >= 1) AND (page_start <= 200)) AND ((page_count >= 1) AND (page_count <= 200)) AND (((page_start + page_count) - 1) <= 200)))",
            name="certificate_part_pages",
        ),
        CheckConstraint(
            "((rotation = ANY (ARRAY[0, 90, 180, 270])))", name="certificate_part_rotation"
        ),
        CheckConstraint(
            "(((size_bytes >= 1) AND (size_bytes <= 41943040)))", name="certificate_part_size"
        ),
        CheckConstraint(
            "(((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'image/png'::character varying, 'image/jpeg'::character varying])::text[])))",
            name="certificate_part_type",
        ),
        UniqueConstraint(
            "certificate_file_id",
            "ordinal",
            name="certificate_file_parts_certificate_file_id_ordinal_key",
        ),
        PrimaryKeyConstraint("id", name="certificate_file_parts_pkey"),
        ForeignKeyConstraint(
            ["certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
            name="certificate_file_parts_certificate_file_id_certific_fkey",
        ),
    )


class CertificateFile(Base):
    __tablename__ = "certificate_files"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    file: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "(((storage_key)::text = ((((((('certificate/'::text) || (certificate_id)::text) || '/'::text) || (certificate_revision_id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.pdf'::text)))",
            name="certificate_file_binding",
        ),
        CheckConstraint(
            "((((file ->> 'sha256'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'sha256'::text)) = 'string'::text) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text)))",
            name="certificate_file_hash",
        ),
        CheckConstraint(
            "((((file ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((file ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((file ->> 'name'::text))) = 0) AND ((file ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND (lower(\"right\"((file ->> 'name'::text), 4)) = '.pdf'::text)))",
            name="certificate_file_name",
        ),
        CheckConstraint("((jsonb_typeof(file) = 'object'::text))", name="certificate_file_object"),
        CheckConstraint(
            "((((file ->> 'page_count'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'page_count'::text)) = 'number'::text) AND ((((file ->> 'page_count'::text))::numeric >= (1)::numeric) AND (((file ->> 'page_count'::text))::numeric <= (200)::numeric)) AND (((file ->> 'page_count'::text))::numeric = trunc(((file ->> 'page_count'::text))::numeric))))",
            name="certificate_file_pages",
        ),
        CheckConstraint(
            "((((file ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric))))",
            name="certificate_file_size",
        ),
        CheckConstraint(
            "((((file ->> 'media_type'::text) IS NOT NULL) AND ((file ->> 'media_type'::text) = 'application/pdf'::text)))",
            name="certificate_file_type",
        ),
        UniqueConstraint(
            "id",
            "certificate_id",
            "certificate_revision_id",
            name="certificate_file_source_binding",
        ),
        UniqueConstraint(
            "certificate_revision_id", name="certificate_files_certificate_revision_id_key"
        ),
        PrimaryKeyConstraint("id", name="certificate_files_pkey"),
        ForeignKeyConstraint(
            ["certificate_id", "certificate_revision_id"],
            ["certificate_revisions.certificate_id", "certificate_revisions.id"],
            name="certificate_files_certificate_id_certificate_revisi_fkey",
        ),
    )


class CertificateRevision(Base):
    __tablename__ = "certificate_revisions"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "(((((data ->> 'valid_from'::text) IS NULL) OR (((data ->> 'valid_from'::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text) AND (((data ->> 'valid_from'::text))::date IS NOT NULL))) AND (((data ->> 'valid_until'::text) IS NULL) OR (((data ->> 'valid_until'::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'::text) AND (((data ->> 'valid_until'::text))::date IS NOT NULL))) AND (((data ->> 'valid_from'::text) IS NULL) OR ((data ->> 'valid_until'::text) IS NULL) OR (((data ->> 'valid_from'::text))::date <= ((data ->> 'valid_until'::text))::date))))",
            name="certificate_declared_dates",
        ),
        CheckConstraint(
            "((((data ->> 'name'::text) IS NOT NULL) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200)) AND ((data ->> 'number'::text) IS NOT NULL) AND ((length(btrim((data ->> 'number'::text))) >= 1) AND (length(btrim((data ->> 'number'::text))) <= 200))))",
            name="certificate_declared_identity",
        ),
        CheckConstraint(
            "((((data ->> 'kind'::text) IS NOT NULL) AND ((data ->> 'kind'::text) = ANY (ARRAY['qualification'::text, 'personnel'::text]))))",
            name="certificate_declared_kind",
        ),
        CheckConstraint("((revision > 0))", name="certificate_revision_number_positive"),
        CheckConstraint(
            "((jsonb_typeof(data) = 'object'::text))", name="certificate_revision_object"
        ),
        UniqueConstraint(
            "certificate_id", "id", name="certificate_revisions_certificate_id_id_key"
        ),
        UniqueConstraint(
            "certificate_id", "revision", name="certificate_revisions_certificate_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="certificate_revisions_pkey"),
        ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name="certificate_revisions_certificate_id_fkey",
        ),
        Index(
            "management_certificate_history",
            "certificate_id",
            column("revision").desc(),
            column("id").desc(),
        ),
    )


class Certificate(Base):
    __tablename__ = "certificates"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="active",
        server_default=sql_text("'active'::character varying"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    __table_args__ = (
        CheckConstraint(
            "((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text))))",
            name="certificate_lifecycle_valid",
        ),
        CheckConstraint("((current_revision > 0))", name="certificate_revision_positive"),
        PrimaryKeyConstraint("id", name="certificates_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["certificate_revisions.certificate_id", "certificate_revisions.revision"],
            name="certificate_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_certificates_browse", column("created_at").desc(), column("id").desc()),
        Index("management_certificates_prefix", "search_vector", postgresql_using="gin"),
        Index(
            "management_certificates_state_browse",
            "lifecycle_state",
            column("created_at").desc(),
            column("id").desc(),
        ),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    citation_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=lambda context: context.get_current_parameters()["page"]
    )
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint("((page > 0))", name="chunk_page_positive"),
        CheckConstraint(
            "((((page IS NOT NULL) AND (blocks IS NULL)) OR ((page IS NULL) AND (jsonb_typeof(blocks) = 'array'::text) AND (jsonb_array_length(blocks) > 0))))",
            name="chunk_position",
        ),
        CheckConstraint("((seq > 0))", name="chunk_seq_positive"),
        UniqueConstraint("id", "task_id", "document_id", name="check_chunk_binding"),
        UniqueConstraint("document_id", "seq", name="chunks_document_seq"),
        PrimaryKeyConstraint("id", name="chunks_pkey"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], name="chunks_document_id_fkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="chunks_task_id_fkey"),
        Index(
            "chunks_document_page",
            "document_id",
            "page",
            unique=True,
            postgresql_where=sql_text("(page IS NOT NULL)"),
        ),
    )


class Document(Base):
    __tablename__ = "documents"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    citation_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((citation_mode)::text = ANY ((ARRAY['page'::character varying, 'block'::character varying])::text[])))",
            name="documents_citation_mode_check",
        ),
        UniqueConstraint("task_id", "id", name="agent_document_task"),
        UniqueConstraint("task_id", "sha256", name="documents_task_id_sha256_key"),
        PrimaryKeyConstraint("id", name="documents_pkey"),
        UniqueConstraint("id", "task_id", name="sandbox_document_task"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="documents_task_id_fkey"),
    )


class EvidenceSource(Base):
    __tablename__ = "evidence_sources"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    task_certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    certificate_file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    render_profile: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pdf-page-preview-v1"
    )
    dpi: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    rendered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unconfirmed_source")
    eligible_for_draft_export: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="user_supplied_certificate_pdf",
        server_default=sql_text("'user_supplied_certificate_pdf'::character varying"),
    )
    task_attachment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_org_profile_id: Mapped[UUID | None] = mapped_column(nullable=True)
    profile_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    profile_attachment_link_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attachment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attachment_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    attachment_file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    approval_id: Mapped[UUID | None] = mapped_column(nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((((source_kind)::text = 'user_supplied_certificate_pdf'::text) AND (num_nonnulls(task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) = 4) AND (num_nonnulls(task_attachment_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, attachment_file_id, approval_id) = 0)) OR (((source_kind)::text = 'user_supplied_attachment_pdf'::text) AND (num_nonnulls(task_certificate_id, certificate_id, certificate_revision_id, certificate_file_id) = 0) AND (num_nonnulls(task_attachment_id, task_org_profile_id, profile_revision_id, profile_attachment_link_id, attachment_id, attachment_revision_id, attachment_file_id, approval_id) = 8))))",
            name="attachment_source_branches",
        ),
        CheckConstraint("(((page >= 1) AND (page <= 200)))", name="source_page"),
        CheckConstraint(
            "(((storage_key)::text = ((((('evidence-source/'::text) || (id)::text) || '/'::text) || (preview ->> 'sha256'::text)) || '.png'::text)))",
            name="source_preview_binding",
        ),
        CheckConstraint(
            "((((preview ->> 'width_px'::text) IS NOT NULL) AND ((preview ->> 'height_px'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'width_px'::text)) = 'number'::text) AND (jsonb_typeof((preview -> 'height_px'::text)) = 'number'::text) AND ((((preview ->> 'width_px'::text))::numeric >= (1)::numeric) AND (((preview ->> 'width_px'::text))::numeric <= (8192)::numeric)) AND ((((preview ->> 'height_px'::text))::numeric >= (1)::numeric) AND (((preview ->> 'height_px'::text))::numeric <= (8192)::numeric)) AND (((preview ->> 'width_px'::text))::numeric = trunc(((preview ->> 'width_px'::text))::numeric)) AND (((preview ->> 'height_px'::text))::numeric = trunc(((preview ->> 'height_px'::text))::numeric)) AND ((((preview ->> 'width_px'::text))::numeric * ((preview ->> 'height_px'::text))::numeric) <= (20000000)::numeric)))",
            name="source_preview_dimensions",
        ),
        CheckConstraint(
            "((((preview ->> 'sha256'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'sha256'::text)) = 'string'::text) AND ((preview ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text)))",
            name="source_preview_hash",
        ),
        CheckConstraint(
            "((((preview ->> 'media_type'::text) IS NOT NULL) AND ((preview ->> 'media_type'::text) = 'image/png'::text)))",
            name="source_preview_media",
        ),
        CheckConstraint(
            "((((preview ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'name'::text)) = 'string'::text) AND ((length(btrim((preview ->> 'name'::text))) >= 1) AND (length(btrim((preview ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((preview ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((preview ->> 'name'::text))) = 0) AND ((preview ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND (lower(\"right\"((preview ->> 'name'::text), 4)) = '.png'::text)))",
            name="source_preview_name",
        ),
        CheckConstraint("((jsonb_typeof(preview) = 'object'::text))", name="source_preview_object"),
        CheckConstraint(
            "((((preview ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((preview -> 'size_bytes'::text)) = 'number'::text) AND ((((preview ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((preview ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((preview ->> 'size_bytes'::text))::numeric = trunc(((preview ->> 'size_bytes'::text))::numeric))))",
            name="source_preview_size",
        ),
        CheckConstraint(
            "((((render_profile)::text = 'pdf-page-preview-v1'::text) AND (dpi = 150)))",
            name="source_profile",
        ),
        CheckConstraint(
            "((((status)::text = 'unconfirmed_source'::text) AND (confirmed_at IS NULL) AND (eligible_for_draft_export = false)))",
            name="source_unconfirmed",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "task_certificate_id",
            "certificate_id",
            "certificate_revision_id",
            "certificate_file_id",
            name="annotation_certificate_source_scope",
        ),
        UniqueConstraint("task_id", "id", name="attachment_source_task"),
        PrimaryKeyConstraint("id", name="evidence_sources_pkey"),
        ForeignKeyConstraint(
            [
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
                "task_attachments.id",
                "task_attachments.task_id",
                "task_attachments.task_org_profile_id",
                "task_attachments.profile_revision_id",
                "task_attachments.profile_attachment_link_id",
                "task_attachments.attachment_id",
                "task_attachments.attachment_revision_id",
                "task_attachments.file_id",
                "task_attachments.approval_id",
            ],
            name="attachment_source_exact_selection",
        ),
        ForeignKeyConstraint(
            ["certificate_file_id", "certificate_id", "certificate_revision_id"],
            [
                "certificate_files.id",
                "certificate_files.certificate_id",
                "certificate_files.certificate_revision_id",
            ],
            name="evidence_sources_certificate_file_id_certificate_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_certificate_id", "task_id", "certificate_id", "certificate_revision_id"],
            [
                "task_certificates.id",
                "task_certificates.task_id",
                "task_certificates.certificate_id",
                "task_certificates.certificate_revision_id",
            ],
            name="evidence_sources_task_certificate_id_task_id_certif_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="evidence_sources_task_id_fkey"),
        Index(
            "attachment_source_identity",
            "task_attachment_id",
            "page",
            "render_profile",
            unique=True,
            postgresql_where=sql_text(
                "((source_kind)::text = 'user_supplied_attachment_pdf'::text)"
            ),
        ),
        Index(
            "attachment_source_page",
            "task_id",
            "created_at",
            "id",
            postgresql_where=sql_text(
                "((source_kind)::text = 'user_supplied_attachment_pdf'::text)"
            ),
        ),
        Index(
            "certificate_source_identity",
            "task_certificate_id",
            "page",
            "render_profile",
            unique=True,
            postgresql_where=sql_text(
                "((source_kind)::text = 'user_supplied_certificate_pdf'::text)"
            ),
        ),
    )


class FeatureRevision(Base):
    __tablename__ = "feature_revisions"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    feature_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "((((data ->> 'status'::text) IS NOT NULL) AND ((data ->> 'status'::text) = ANY (ARRAY['implemented'::text, 'developing'::text, 'planned'::text]))))",
            name="feature_declared_status",
        ),
        CheckConstraint(
            "((((data ->> 'product_id'::text) IS NOT NULL) AND ((data ->> 'product_id'::text) = (product_id)::text)))",
            name="feature_product_matches",
        ),
        CheckConstraint("((revision > 0))", name="feature_revision_number_positive"),
        CheckConstraint("((jsonb_typeof(data) = 'object'::text))", name="feature_revision_object"),
        UniqueConstraint("feature_id", "id", name="feature_revisions_feature_id_id_key"),
        UniqueConstraint(
            "feature_id", "revision", name="feature_revisions_feature_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="feature_revisions_pkey"),
        ForeignKeyConstraint(
            ["feature_id"], ["features.id"], name="feature_revisions_feature_id_fkey"
        ),
        ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="feature_revisions_product_id_fkey"
        ),
        Index(
            "management_feature_history",
            "feature_id",
            column("revision").desc(),
            column("id").desc(),
        ),
        Index("management_feature_parent", "product_id", "feature_id", "revision"),
        Index(
            "management_feature_status",
            sql_text("((data ->> 'status'::text))"),
            "feature_id",
            "revision",
        ),
    )


class Feature(Base):
    __tablename__ = "features"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="active",
        server_default=sql_text("'active'::character varying"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    __table_args__ = (
        CheckConstraint(
            "((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text))))",
            name="feature_lifecycle_valid",
        ),
        CheckConstraint("((current_revision > 0))", name="feature_revision_positive"),
        PrimaryKeyConstraint("id", name="features_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["feature_revisions.feature_id", "feature_revisions.revision"],
            name="feature_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_features_browse", column("created_at").desc(), column("id").desc()),
        Index("management_features_prefix", "search_vector", postgresql_using="gin"),
        Index(
            "management_features_state_browse",
            "lifecycle_state",
            column("created_at").desc(),
            column("id").desc(),
        ),
    )


class Job(Base):
    __tablename__ = "jobs"
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reasoning: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actor_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    initiated_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    agent_principal_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_session_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_step_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # Filled by the attribution trigger; the worker acts for this member.
    actor_member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    invocation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    command: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bid_submission_document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((((kind)::text <> ALL ((ARRAY['annotation_render'::character varying, 'annotation_release'::character varying])::text[])) OR ((task_id IS NOT NULL) AND (document_id IS NOT NULL) AND ((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL))))",
            name="annotation_job_kind",
        ),
        CheckConstraint(
            "((((kind)::text <> 'bid_review_prepare'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND (agent_principal_id IS NULL))))",
            name="bid_review_prepare_actor",
        ),
        CheckConstraint(
            "((((kind)::text <> 'bid_review_report'::text) OR (((actor_kind)::text = 'session'::text) AND (actor_token_id IS NULL) AND (agent_principal_id IS NULL))))",
            name="bid_review_report_actor",
        ),
        CheckConstraint(
            "((((kind)::text = ANY ((ARRAY['bid_review'::character varying, 'bid_review_report'::character varying])::text[])) = (bid_submission_document_id IS NOT NULL)))",
            name="bid_review_uploaded_document",
        ),
        CheckConstraint(
            "(((jsonb_typeof(actor_scopes) = 'array'::text) AND ((actor_kind IS NULL) OR ((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying, 'worker'::character varying])::text[]))) AND ((actor_token_id IS NULL) OR ((actor_kind)::text <> 'session'::text))))",
            name="job_actor_shape",
        ),
        CheckConstraint(
            "(((((kind)::text = 'provider_test'::text) AND (task_id IS NULL) AND (document_id IS NULL)) OR (((kind)::text = 'bid_review_prepare'::text) AND (task_id IS NOT NULL) AND (document_id IS NULL)) OR (((kind)::text = ANY ((ARRAY['bid_review'::character varying, 'bid_review_report'::character varying])::text[])) AND (task_id IS NOT NULL) AND (document_id IS NULL) AND (bid_submission_document_id IS NOT NULL)) OR (((kind)::text <> ALL ((ARRAY['provider_test'::character varying, 'bid_review_prepare'::character varying, 'bid_review'::character varying, 'bid_review_report'::character varying])::text[])) AND (task_id IS NOT NULL) AND (document_id IS NOT NULL))))",
            name="job_document_binding",
        ),
        CheckConstraint(
            "(((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))",
            name="job_status",
        ),
        CheckConstraint(
            "((((initiated_by)::text = 'builtin_agent'::text) AND (agent_principal_id IS NOT NULL) AND (agent_session_id IS NOT NULL) AND (actor_token_id IS NULL)) OR (((initiated_by)::text = 'external_agent'::text) AND (actor_token_id IS NOT NULL) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)) OR (((COALESCE(initiated_by, 'legacy_unknown'::character varying))::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying])::text[])) AND (agent_principal_id IS NULL) AND (agent_session_id IS NULL) AND (agent_step_id IS NULL)))",
            name="jobs_agent_origin_binding",
        ),
        CheckConstraint(
            "(((initiated_by IS NULL) OR ((initiated_by)::text = ANY ((ARRAY['human'::character varying, 'legacy_unknown'::character varying, 'builtin_agent'::character varying, 'external_agent'::character varying])::text[]))))",
            name="jobs_agent_origin_shape",
        ),
        UniqueConstraint("task_id", "document_id", "id", name="agent_job_document"),
        UniqueConstraint("agent_session_id", "id", name="agent_job_session"),
        UniqueConstraint("task_id", "id", name="bid_review_job_task"),
        UniqueConstraint("id", "task_id", "document_id", name="check_job_binding"),
        UniqueConstraint("cache_key", name="jobs_cache_key_key"),
        PrimaryKeyConstraint("id", name="jobs_pkey"),
        UniqueConstraint("id", "task_id", name="response_job_task_binding"),
        ForeignKeyConstraint(
            ["task_id", "bid_submission_document_id"],
            ["bid_submission_documents.task_id", "bid_submission_documents.id"],
            name="bid_review_job_source",
        ),
        ForeignKeyConstraint(["actor_token_id"], ["api_tokens.id"], name="job_actor_token_fk"),
        ForeignKeyConstraint(
            ["agent_session_id", "task_id", "document_id"],
            ["agent_sessions.id", "agent_sessions.task_id", "agent_sessions.document_id"],
            name="job_agent_task_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["provider_config_id"], ["provider_configs.id"], name="job_provider_config_fk"
        ),
        ForeignKeyConstraint(
            ["agent_session_id", "agent_step_id"],
            ["agent_steps.session_id", "agent_steps.id"],
            name="jobs_agent_origin_step_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(["document_id"], ["documents.id"], name="jobs_document_id_fkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="jobs_task_id_fkey"),
        Index("jobs_agent_session", "agent_session_id"),
        Index("jobs_state", "status"),
        Index(
            "management_memory_candidate_events",
            sql_text("(((result -> 'submission'::text) -> 'event_ids'::text))"),
            postgresql_using="gin",
            postgresql_where=sql_text("((kind)::text = 'memory_candidate'::text)"),
        ),
        Index(
            "management_memory_candidate_task",
            "task_id",
            column("created_at").desc(),
            column("id").desc(),
            postgresql_where=sql_text("((kind)::text = 'memory_candidate'::text)"),
        ),
        Index("task_jobs_lifecycle", "task_id", "status", "id"),
    )


class OrgProfileRevision(Base):
    __tablename__ = "org_profile_revisions"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "((((data ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((data -> 'name'::text)) = 'string'::text) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200))))",
            name="profile_declared_name",
        ),
        CheckConstraint(
            "((((data ->> 'performance_summary'::text) IS NULL) OR ((jsonb_typeof((data -> 'performance_summary'::text)) = 'string'::text) AND ((length(btrim((data ->> 'performance_summary'::text))) >= 1) AND (length(btrim((data ->> 'performance_summary'::text))) <= 20000)))))",
            name="profile_performance_summary_text",
        ),
        CheckConstraint(
            "((((data ->> 'registration_details'::text) IS NULL) OR ((jsonb_typeof((data -> 'registration_details'::text)) = 'string'::text) AND ((length(btrim((data ->> 'registration_details'::text))) >= 1) AND (length(btrim((data ->> 'registration_details'::text))) <= 10000)))))",
            name="profile_registration_details_text",
        ),
        CheckConstraint("((revision > 0))", name="profile_revision_number_positive"),
        CheckConstraint("((jsonb_typeof(data) = 'object'::text))", name="profile_revision_object"),
        CheckConstraint(
            "((((data ->> 'standard_wording'::text) IS NULL) OR ((jsonb_typeof((data -> 'standard_wording'::text)) = 'string'::text) AND ((length(btrim((data ->> 'standard_wording'::text))) >= 1) AND (length(btrim((data ->> 'standard_wording'::text))) <= 20000)))))",
            name="profile_standard_wording_text",
        ),
        UniqueConstraint("profile_id", "id", name="org_profile_revisions_profile_id_id_key"),
        UniqueConstraint(
            "profile_id", "revision", name="org_profile_revisions_profile_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="org_profile_revisions_pkey"),
        ForeignKeyConstraint(
            ["profile_id"], ["org_profiles.id"], name="org_profile_revisions_profile_id_fkey"
        ),
        Index(
            "management_profile_history",
            "profile_id",
            column("revision").desc(),
            column("id").desc(),
        ),
    )


class OrgProfile(Base):
    __tablename__ = "org_profiles"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="active",
        server_default=sql_text("'active'::character varying"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    __table_args__ = (
        CheckConstraint(
            "((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text))))",
            name="profile_lifecycle_valid",
        ),
        CheckConstraint("((current_revision > 0))", name="profile_revision_positive"),
        PrimaryKeyConstraint("id", name="org_profiles_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["org_profile_revisions.profile_id", "org_profile_revisions.revision"],
            name="profile_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_profiles_browse", column("created_at").desc(), column("id").desc()),
        Index("management_profiles_prefix", "search_vector", postgresql_using="gin"),
        Index(
            "management_profiles_state_browse",
            "lifecycle_state",
            column("created_at").desc(),
            column("id").desc(),
        ),
    )


class ProductRevision(Base):
    __tablename__ = "product_revisions"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        CheckConstraint("((jsonb_typeof(data) = 'object'::text))", name="revision_object"),
        CheckConstraint("((revision > 0))", name="revision_positive"),
        UniqueConstraint("product_id", "id", name="product_revisions_product_id_id_key"),
        UniqueConstraint(
            "product_id", "revision", name="product_revisions_product_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="product_revisions_pkey"),
        ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="product_revisions_product_id_fkey"
        ),
        Index(
            "management_product_history",
            "product_id",
            column("revision").desc(),
            column("id").desc(),
        ),
    )


class Product(Base):
    __tablename__ = "products"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="active",
        server_default=sql_text("'active'::character varying"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    __table_args__ = (
        CheckConstraint(
            "((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text))))",
            name="product_lifecycle_valid",
        ),
        CheckConstraint("((current_revision > 0))", name="product_revision_positive"),
        PrimaryKeyConstraint("id", name="products_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["product_revisions.product_id", "product_revisions.revision"],
            name="product_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_products_browse", column("created_at").desc(), column("id").desc()),
        Index("management_products_prefix", "search_vector", postgresql_using="gin"),
        Index(
            "management_products_state_browse",
            "lifecycle_state",
            column("created_at").desc(),
            column("id").desc(),
        ),
    )


class Requirement(Base):
    __tablename__ = "requirements"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    location: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    model_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((length(quote) > 0) AND (((page > 0) AND (location IS NULL)) OR ((page IS NULL) AND (jsonb_typeof(location) = 'object'::text) AND (length(COALESCE((location ->> 'block_id'::text), ''::text)) > 0) AND (length(COALESCE((location ->> 'label'::text), ''::text)) > 0)))))",
            name="requirement_citation",
        ),
        UniqueConstraint("job_id", "fingerprint", name="requirements_job_fingerprint"),
        PrimaryKeyConstraint("id", name="requirements_pkey"),
        UniqueConstraint("id", "task_id", "job_id", name="response_requirement_binding"),
        UniqueConstraint("id", "task_id", name="rubric_requirement_task"),
        ForeignKeyConstraint(
            ["job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="requirement_job_document_binding",
        ),
        ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="requirements_chunk_id_fkey"),
        ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="requirements_document_id_fkey"
        ),
        ForeignKeyConstraint(["job_id"], ["jobs.id"], name="requirements_job_id_fkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="requirements_task_id_fkey"),
    )


class SimulatedResource(Base):
    "A product or feature created by a product simulation; the mark covers every revision."

    __tablename__ = "simulated_resources"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feature_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((product_id IS NULL) <> (feature_id IS NULL)))", name="simulated_one_resource"
        ),
        UniqueConstraint("feature_id", name="simulated_resources_feature_id_key"),
        UniqueConstraint("product_id", name="simulated_resources_product_id_key"),
        PrimaryKeyConstraint("id", name="simulated_resources_pkey"),
        ForeignKeyConstraint(
            ["feature_id"], ["features.id"], name="simulated_resources_feature_id_fkey"
        ),
        ForeignKeyConstraint(["job_id"], ["jobs.id"], name="simulated_resources_job_id_fkey"),
        ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="simulated_resources_product_id_fkey"
        ),
    )


class TaskCertificate(Base):
    __tablename__ = "task_certificates"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    lot: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=sql_text("''::character varying")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "certificate_revision_id", name="check_certificate_selection"
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "certificate_id",
            "certificate_revision_id",
            name="task_certificate_source_binding",
        ),
        PrimaryKeyConstraint("id", name="task_certificates_pkey"),
        ForeignKeyConstraint(
            ["certificate_id", "certificate_revision_id"],
            ["certificate_revisions.certificate_id", "certificate_revisions.id"],
            name="task_certificates_certificate_id_certificate_revisi_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_certificates_task_id_fkey"),
        Index(
            "task_certificate_active_slot",
            "task_id",
            "certificate_id",
            "lot",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )


class TaskFeature(Base):
    __tablename__ = "task_features"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    feature_id: Mapped[UUID] = mapped_column(nullable=False)
    feature_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    lot: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=sql_text("''::character varying")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "feature_revision_id", name="task_features_evidence_binding"
        ),
        PrimaryKeyConstraint("id", name="task_features_pkey"),
        ForeignKeyConstraint(
            ["feature_id", "feature_revision_id"],
            ["feature_revisions.feature_id", "feature_revisions.id"],
            name="task_features_feature_id_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_features_task_id_fkey"),
        Index(
            "task_feature_active_slot",
            "task_id",
            "feature_id",
            "lot",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )


class TaskOrgProfile(Base):
    __tablename__ = "task_org_profiles"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    lot: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=sql_text("''::character varying")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "profile_revision_id", "lot", name="attachment_task_profile_binding"
        ),
        UniqueConstraint(
            "id", "task_id", "profile_revision_id", name="task_org_profiles_evidence_binding"
        ),
        PrimaryKeyConstraint("id", name="task_org_profiles_pkey"),
        ForeignKeyConstraint(
            ["profile_id", "profile_revision_id"],
            ["org_profile_revisions.profile_id", "org_profile_revisions.id"],
            name="task_org_profiles_profile_id_profile_revision_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_org_profiles_task_id_fkey"),
        Index(
            "task_profile_active_slot",
            "task_id",
            "profile_id",
            "lot",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )


class TaskResource(Base):
    __tablename__ = "task_resources"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    product_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    lot: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=sql_text("''::character varying")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "product_revision_id", name="task_resources_evidence_binding"
        ),
        PrimaryKeyConstraint("id", name="task_resources_pkey"),
        ForeignKeyConstraint(
            ["product_id", "product_revision_id"],
            ["product_revisions.product_id", "product_revisions.id"],
            name="task_resources_product_id_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_resources_task_id_fkey"),
        Index(
            "task_product_active_slot",
            "task_id",
            "product_id",
            "lot",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    template_id: Mapped[UUID] = mapped_column(nullable=False)
    template_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    lot: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=sql_text("''::character varying")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "template_revision_id", name="export_task_templates_parent_0"
        ),
        PrimaryKeyConstraint("id", name="task_templates_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_templates_task_id_fkey"),
        ForeignKeyConstraint(
            ["template_id", "template_revision_id"],
            ["template_revisions.template_id", "template_revisions.id"],
            name="task_templates_template_id_template_revision_id_fkey",
        ),
        Index(
            "task_template_active_slot",
            "task_id",
            "template_id",
            "lot",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )


class Task(Base):
    __tablename__ = "tasks"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tender_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    model_redaction_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    model_redaction_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    export_to_personal_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    __table_args__ = (
        CheckConstraint(
            "((model_redaction_revision > 0))", name="tasks_model_redaction_revision_check"
        ),
        PrimaryKeyConstraint("id", name="tasks_pkey"),
    )


class TemplateRevision(Base):
    __tablename__ = "template_revisions"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    template_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    file: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "((((data ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((data -> 'name'::text)) = 'string'::text) AND ((length(btrim((data ->> 'name'::text))) >= 1) AND (length(btrim((data ->> 'name'::text))) <= 200))))",
            name="template_declared_name",
        ),
        CheckConstraint(
            "(((storage_key)::text = ((((((('template/'::text) || (template_id)::text) || '/'::text) || (id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.docx'::text)))",
            name="template_file_binding",
        ),
        CheckConstraint(
            "((((file ->> 'sha256'::text) IS NOT NULL) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text)))",
            name="template_file_hash",
        ),
        CheckConstraint(
            "((((file ->> 'name'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200))))",
            name="template_file_name",
        ),
        CheckConstraint("((jsonb_typeof(file) = 'object'::text))", name="template_file_object"),
        CheckConstraint(
            "((((file ->> 'size_bytes'::text) IS NOT NULL) AND (jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric))))",
            name="template_file_size",
        ),
        CheckConstraint(
            "((((file ->> 'media_type'::text) IS NOT NULL) AND ((file ->> 'media_type'::text) = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::text)))",
            name="template_file_type",
        ),
        CheckConstraint("((revision > 0))", name="template_revision_number_positive"),
        CheckConstraint("((jsonb_typeof(data) = 'object'::text))", name="template_revision_object"),
        UniqueConstraint("template_id", "id", name="template_revisions_template_id_id_key"),
        UniqueConstraint(
            "template_id", "revision", name="template_revisions_template_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="template_revisions_pkey"),
        ForeignKeyConstraint(
            ["template_id"], ["templates.id"], name="template_revisions_template_id_fkey"
        ),
        Index(
            "management_template_history",
            "template_id",
            column("revision").desc(),
            column("id").desc(),
        ),
    )


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_state: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="active",
        server_default=sql_text("'active'::character varying"),
    )
    lifecycle_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, nullable=False, server_default=sql_text("''::tsvector")
    )
    __table_args__ = (
        CheckConstraint(
            "((((lifecycle_state)::text = ANY ((ARRAY['active'::character varying, 'inactive'::character varying])::text[])) AND (lifecycle_revision >= 0) AND ((lifecycle_revision > 0) OR ((lifecycle_state)::text = 'active'::text))))",
            name="template_lifecycle_valid",
        ),
        CheckConstraint("((current_revision > 0))", name="template_revision_positive"),
        PrimaryKeyConstraint("id", name="templates_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["template_revisions.template_id", "template_revisions.revision"],
            name="template_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("management_templates_browse", column("created_at").desc(), column("id").desc()),
        Index("management_templates_prefix", "search_vector", postgresql_using="gin"),
        Index(
            "management_templates_state_browse",
            "lifecycle_state",
            column("created_at").desc(),
            column("id").desc(),
        ),
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    call_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider_config_id: Mapped[UUID | None] = mapped_column(nullable=True)
    image_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    image_input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capability: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="llm",
        server_default=sql_text("'llm'::character varying"),
    )
    search_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    gateway_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gateway_trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser_units: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    # Filled by the attribution trigger: the member on whose behalf the call ran.
    member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((call_id IS NULL) OR ((job_id IS NOT NULL) AND (run_id IS NOT NULL))))",
            name="usage_call_identity",
        ),
        CheckConstraint(
            "(((capability)::text = ANY ((ARRAY['llm'::character varying, 'vision'::character varying, 'ocr'::character varying, 'search'::character varying, 'embedding'::character varying, 'browser'::character varying])::text[])))",
            name="usage_capability",
        ),
        CheckConstraint(
            "((((gateway_request_id IS NULL) OR ((gateway_request_id)::text ~ '^[A-Za-z0-9_-]{1,128}$'::text)) AND ((gateway_trace_id IS NULL) OR ((gateway_trace_id)::text ~ '^[A-Za-z0-9_-]{1,128}$'::text))))",
            name="usage_gateway_telemetry",
        ),
        CheckConstraint(
            "((((image_count = 0) AND (image_input_sha256 IS NULL)) OR ((image_count > 0) AND (image_input_sha256 IS NOT NULL) AND ((image_input_sha256)::text ~ '^[0-9a-f]{64}$'::text))))",
            name="usage_image_manifest",
        ),
        CheckConstraint("((browser_units >= 0))", name="usage_records_browser_units_check"),
        CheckConstraint(
            "(((image_count >= 0) AND (image_count <= 20)))", name="usage_records_image_count_check"
        ),
        CheckConstraint("((input_tokens >= 0))", name="usage_records_input_tokens_check"),
        CheckConstraint("((output_tokens >= 0))", name="usage_records_output_tokens_check"),
        CheckConstraint("((search_requests >= 0))", name="usage_search_requests_nonnegative"),
        UniqueConstraint("id", "job_id", "run_id", "call_id", name="memory_usage_call_binding"),
        UniqueConstraint("job_id", "run_id", "call_id", name="usage_call_once"),
        PrimaryKeyConstraint("id", name="usage_records_pkey"),
        ForeignKeyConstraint(
            ["job_id", "run_id", "call_id"],
            ["vendor_calls.job_id", "vendor_calls.run_id", "vendor_calls.id"],
            name="usage_call_fk",
        ),
        ForeignKeyConstraint(["job_id"], ["jobs.id"], name="usage_job_fk"),
        ForeignKeyConstraint(
            ["provider_config_id"], ["provider_configs.id"], name="usage_provider_config_fk"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="usage_records_task_id_fkey"),
        Index("usage_provider_month", "provider_config_id", "created_at"),
        Index("usage_records_job", "job_id"),
        Index("usage_records_task", "task_id"),
    )


class VendorCall(Base):
    "Durable admission, including unresolved requests whose admission must survive a crash."

    __tablename__ = "vendor_calls"
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=sql_text("'pending'::character varying"),
    )
    task_id: Mapped[UUID | None] = mapped_column(nullable=True)
    capability: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="llm",
        server_default=sql_text("'llm'::character varying"),
    )
    request_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default=sql_text("repeat('0'::text, 64)"),
    )
    provider_identity: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    # Filled by the attribution trigger: the member on whose behalf the call ran.
    member_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "((((capability)::text = ANY ((ARRAY['llm'::character varying, 'vision'::character varying, 'ocr'::character varying, 'search'::character varying, 'embedding'::character varying, 'browser'::character varying])::text[])) AND ((request_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (jsonb_typeof(provider_identity) = 'object'::text)))",
            name="vendor_call_metadata",
        ),
        CheckConstraint(
            "(((state)::text = ANY ((ARRAY['pending'::character varying, 'completed'::character varying, 'unknown'::character varying, 'not_sent'::character varying])::text[])))",
            name="vendor_calls_state_check",
        ),
        UniqueConstraint("job_id", "run_id", "id", name="vendor_calls_job_id_run_id_id_key"),
        PrimaryKeyConstraint("id", name="vendor_calls_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="vendor_call_task_fk"),
        ForeignKeyConstraint(["job_id"], ["jobs.id"], name="vendor_calls_job_id_fkey"),
        Index("vendor_calls_job", "job_id"),
        Index("vendor_calls_task", "task_id", "state"),
        Index("vendor_calls_unsettled", "state"),
    )
