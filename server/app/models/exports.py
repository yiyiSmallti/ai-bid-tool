from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class ExportRenderCandidate(Base):
    __tablename__ = "export_render_candidates"
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    render_job_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plaintext_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    renderer_profile: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((plaintext_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="export_render_candidates_check_0",
        ),
        CheckConstraint(
            "(((size_bytes >= 1) AND (size_bytes <= 536870912)))",
            name="export_render_candidates_check_1",
        ),
        CheckConstraint(
            "(((object_key)::text = ((((((('export-candidates/'::text) || (run_id)::text) || '/'::text) || (attempt_id)::text) || '/'::text) || (plaintext_sha256)::text) || '.docx'::text)))",
            name="export_render_candidates_check_2",
        ),
        UniqueConstraint(
            "run_id",
            "id",
            "plaintext_sha256",
            "size_bytes",
            name="export_export_render_candidates_parent_0",
        ),
        UniqueConstraint(
            "run_id", "attempt_id", name="export_render_candidates_run_id_attempt_id_key"
        ),
        UniqueConstraint("run_id", "id", name="export_render_candidates_run_id_id_key"),
        PrimaryKeyConstraint("id", name="export_render_candidates_pkey"),
        ForeignKeyConstraint(
            ["run_id", "render_job_id", "input_hash", "manifest_hash", "renderer_profile"],
            [
                "export_runs.id",
                "export_runs.render_job_id",
                "export_runs.input_hash",
                "export_runs.manifest_hash",
                "export_runs.renderer_profile",
            ],
            name="export_relation_5",
        ),
        ForeignKeyConstraint(
            ["render_job_id"], ["jobs.id"], name="export_render_candidates_render_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["run_id"], ["export_runs.id"], name="export_render_candidates_run_id_fkey"
        ),
    )


class ExportRunEvidence(Base):
    __tablename__ = "export_run_evidence"
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    run_item_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    prototype_decision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((attachment_ordinal IS NULL) OR (attachment_ordinal > 0)))",
            name="export_run_evidence_check_0",
        ),
        UniqueConstraint(
            "run_item_id", "evidence_id", name="export_run_evidence_run_item_id_evidence_id_key"
        ),
        PrimaryKeyConstraint("id", name="export_run_evidence_pkey"),
        ForeignKeyConstraint(
            ["card_revision_id", "evidence_id"],
            ["card_evidence_links.revision_id", "card_evidence_links.evidence_id"],
            name="export_run_evidence_card_revision_id_evidence_id_fkey",
        ),
        ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="export_run_evidence_evidence_id_fkey"
        ),
        ForeignKeyConstraint(
            ["run_id", "run_item_id", "card_revision_id"],
            ["export_run_items.run_id", "export_run_items.id", "export_run_items.card_revision_id"],
            name="export_run_evidence_run_id_run_item_id_card_revisio_fkey",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "prototype_decision_id"],
            ["prototype_evidence_decisions.evidence_id", "prototype_evidence_decisions.id"],
            name="export_run_evidence_prototype_decision",
        ),
    )


class ExportRunItem(Base):
    __tablename__ = "export_run_items"
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_run_id: Mapped[UUID] = mapped_column(nullable=False)
    response_item_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['row'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[])))",
            name="export_run_items_check_0",
        ),
        CheckConstraint("((ordinal >= 0))", name="export_run_items_check_1"),
        UniqueConstraint(
            "run_id",
            "id",
            "card_revision_id",
            name="export_run_items_run_id_id_card_revision_id_key",
        ),
        UniqueConstraint("run_id", "ordinal", name="export_run_items_run_id_ordinal_key"),
        UniqueConstraint(
            "run_id", "requirement_id", name="export_run_items_run_id_requirement_id_key"
        ),
        PrimaryKeyConstraint("id", name="export_run_items_pkey"),
        ForeignKeyConstraint(
            ["response_item_id", "draft_run_id", "requirement_id", "kind"],
            [
                "response_items.id",
                "response_items.draft_id",
                "response_items.requirement_id",
                "response_items.kind",
            ],
            name="export_relation_4",
        ),
        ForeignKeyConstraint(
            ["card_revision_id"],
            ["response_card_revisions.id"],
            name="export_run_items_card_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id"], ["requirements.id"], name="export_run_items_requirement_id_fkey"
        ),
        ForeignKeyConstraint(
            ["response_item_id"],
            ["response_items.id"],
            name="export_run_items_response_item_id_fkey",
        ),
        ForeignKeyConstraint(
            ["run_id", "draft_run_id"],
            ["export_runs.id", "export_runs.draft_run_id"],
            name="export_run_items_run_id_draft_run_id_fkey",
        ),
    )


class ExportRun(Base):
    __tablename__ = "export_runs"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_run_id: Mapped[UUID] = mapped_column(nullable=False)
    task_template_id: Mapped[UUID] = mapped_column(nullable=False)
    template_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    binding_id: Mapped[UUID] = mapped_column(nullable=False)
    render_job_id: Mapped[UUID] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer_profile: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    issue_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    acknowledged_issue_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((mode)::text = ANY ((ARRAY['final_section'::character varying, 'review_copy'::character varying])::text[])))",
            name="export_runs_check_0",
        ),
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="export_runs_check_1",
        ),
        CheckConstraint(
            "(((jsonb_typeof(manifest) = 'object'::text) AND (jsonb_typeof(issue_snapshot) = 'array'::text) AND (jsonb_typeof(acknowledged_issue_ids) = 'array'::text)))",
            name="export_runs_check_2",
        ),
        UniqueConstraint(
            "id",
            "render_job_id",
            "input_hash",
            "manifest_hash",
            "renderer_profile",
            name="export_export_runs_parent_0",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "mode",
            "input_hash",
            "manifest_hash",
            name="export_export_runs_parent_1",
        ),
        UniqueConstraint("id", "draft_run_id", name="export_runs_id_draft_run_id_key"),
        UniqueConstraint(
            "input_hash", "manifest_hash", name="export_runs_input_hash_manifest_hash_key"
        ),
        UniqueConstraint("render_job_id", name="export_runs_render_job_id_key"),
        PrimaryKeyConstraint("id", name="export_runs_pkey"),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="export_relation_0",
        ),
        ForeignKeyConstraint(
            ["render_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="export_relation_1",
        ),
        ForeignKeyConstraint(
            ["draft_run_id", "task_id", "extraction_job_id"],
            ["draft_runs.id", "draft_runs.task_id", "draft_runs.extraction_job_id"],
            name="export_relation_2",
        ),
        ForeignKeyConstraint(
            ["task_template_id", "task_id", "template_revision_id"],
            ["task_templates.id", "task_templates.task_id", "task_templates.template_revision_id"],
            name="export_relation_3",
        ),
        ForeignKeyConstraint(
            ["binding_id", "template_revision_id"],
            ["export_template_bindings.id", "export_template_bindings.template_revision_id"],
            name="export_runs_binding_id_template_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="export_runs_document_id_fkey"
        ),
        ForeignKeyConstraint(
            ["draft_run_id"], ["draft_runs.id"], name="export_runs_draft_run_id_fkey"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id"], ["jobs.id"], name="export_runs_extraction_job_id_fkey"
        ),
        ForeignKeyConstraint(["render_job_id"], ["jobs.id"], name="export_runs_render_job_id_fkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="export_runs_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_template_id"], ["task_templates.id"], name="export_runs_task_template_id_fkey"
        ),
        ForeignKeyConstraint(
            ["template_revision_id"],
            ["template_revisions.id"],
            name="export_runs_template_revision_id_fkey",
        ),
    )


class ExportTemplateBinding(Base):
    __tablename__ = "export_template_bindings"
    template_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    static_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((template_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((binding_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((static_content_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="export_template_bindings_check_0",
        ),
        CheckConstraint(
            "(((jsonb_typeof(sections) = 'array'::text) AND (jsonb_array_length(sections) = 6)))",
            name="export_template_bindings_check_1",
        ),
        UniqueConstraint(
            "id",
            "template_revision_id",
            name="export_template_bindings_id_template_revision_id_key",
        ),
        UniqueConstraint(
            "template_revision_id",
            "binding_hash",
            name="export_template_bindings_template_revision_id_bindin_key",
        ),
        PrimaryKeyConstraint("id", name="export_template_bindings_pkey"),
        ForeignKeyConstraint(
            ["template_revision_id"],
            ["template_revisions.id"],
            name="export_template_bindings_template_revision_id_fkey",
        ),
        Index(
            "management_bindings_browse",
            "template_revision_id",
            column("reviewed_at").desc(),
            column("id").desc(),
        ),
    )


class Export(Base):
    __tablename__ = "exports"
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((mode)::text = ANY ((ARRAY['final_section'::character varying, 'review_copy'::character varying])::text[])))",
            name="exports_check_0",
        ),
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((manifest_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((file_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="exports_check_1",
        ),
        CheckConstraint(
            "(((size_bytes >= 1) AND (size_bytes <= 536870912)))", name="exports_check_2"
        ),
        CheckConstraint(
            "(((media_type)::text = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'::text))",
            name="exports_check_3",
        ),
        CheckConstraint(
            "(((object_key)::text = ((((('exports/'::text) || (id)::text) || '/'::text) || (file_sha256)::text) || '.docx'::text)))",
            name="exports_check_4",
        ),
        UniqueConstraint("run_id", name="exports_run_id_key"),
        PrimaryKeyConstraint("id", name="exports_pkey"),
        ForeignKeyConstraint(
            ["run_id", "task_id", "mode", "input_hash", "manifest_hash"],
            [
                "export_runs.id",
                "export_runs.task_id",
                "export_runs.mode",
                "export_runs.input_hash",
                "export_runs.manifest_hash",
            ],
            name="export_relation_6",
        ),
        ForeignKeyConstraint(
            ["run_id", "candidate_id", "file_sha256", "size_bytes"],
            [
                "export_render_candidates.run_id",
                "export_render_candidates.id",
                "export_render_candidates.plaintext_sha256",
                "export_render_candidates.size_bytes",
            ],
            name="export_relation_7",
        ),
        ForeignKeyConstraint(
            ["run_id", "candidate_id"],
            ["export_render_candidates.run_id", "export_render_candidates.id"],
            name="exports_run_id_candidate_id_fkey",
        ),
        ForeignKeyConstraint(["run_id"], ["export_runs.id"], name="exports_run_id_fkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="exports_task_id_fkey"),
    )
