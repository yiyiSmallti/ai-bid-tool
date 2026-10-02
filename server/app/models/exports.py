"""Immutable human export decisions, bound worker candidates and released files."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant
from app.models.response_cards import tenant_fk


class ExportTemplateBinding(Tenant, Base):
    __tablename__ = "export_template_bindings"
    template_revision_id: Mapped[UUID] = mapped_column()
    template_sha256: Mapped[str] = mapped_column(String(64))
    binding_hash: Mapped[str] = mapped_column(String(64))
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    static_content_hash: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(100))
    reviewed_by: Mapped[UUID] = mapped_column()
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "template_revision_id"),
        UniqueConstraint("org_id", "template_revision_id", "binding_hash"),
        tenant_fk("template_revision_id", "template_revisions"),
        tenant_fk("reviewed_by", "memberships", "user_id"),
    )


class ExportRun(Tenant, Base):
    __tablename__ = "export_runs"
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    draft_run_id: Mapped[UUID] = mapped_column()
    task_template_id: Mapped[UUID] = mapped_column()
    template_revision_id: Mapped[UUID] = mapped_column()
    binding_id: Mapped[UUID] = mapped_column()
    render_job_id: Mapped[UUID] = mapped_column()
    mode: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    renderer_profile: Mapped[str] = mapped_column(String(200))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    issue_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    acknowledged_issue_ids: Mapped[list[str]] = mapped_column(JSONB)
    initiated_by: Mapped[UUID] = mapped_column()
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "render_job_id"),
        UniqueConstraint("org_id", "id", "draft_run_id"),
        UniqueConstraint("org_id", "initiated_by", "input_hash", "manifest_hash"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("extraction_job_id", "jobs"),
        tenant_fk("document_id", "documents"),
        tenant_fk("draft_run_id", "draft_runs"),
        tenant_fk("task_template_id", "task_templates"),
        tenant_fk("template_revision_id", "template_revisions"),
        tenant_fk("render_job_id", "jobs"),
        tenant_fk("initiated_by", "memberships", "user_id"),
        ForeignKeyConstraint(
            ["org_id", "binding_id", "template_revision_id"],
            [
                "export_template_bindings.org_id",
                "export_template_bindings.id",
                "export_template_bindings.template_revision_id",
            ],
        ),
    )


class ExportRunItem(Tenant, Base):
    __tablename__ = "export_run_items"
    run_id: Mapped[UUID] = mapped_column()
    draft_run_id: Mapped[UUID] = mapped_column()
    response_item_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    card_revision_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "run_id", "requirement_id"),
        UniqueConstraint("org_id", "run_id", "ordinal"),
        UniqueConstraint("org_id", "run_id", "id", "card_revision_id"),
        ForeignKeyConstraint(
            ["org_id", "run_id", "draft_run_id"],
            ["export_runs.org_id", "export_runs.id", "export_runs.draft_run_id"],
        ),
        tenant_fk("response_item_id", "response_items"),
        tenant_fk("requirement_id", "requirements"),
        tenant_fk("card_revision_id", "response_card_revisions"),
    )


class ExportRunEvidence(Tenant, Base):
    __tablename__ = "export_run_evidence"
    run_id: Mapped[UUID] = mapped_column()
    run_item_id: Mapped[UUID] = mapped_column()
    card_revision_id: Mapped[UUID] = mapped_column()
    evidence_id: Mapped[UUID] = mapped_column()
    attachment_ordinal: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "run_item_id", "evidence_id"),
        ForeignKeyConstraint(
            ["org_id", "run_id", "run_item_id", "card_revision_id"],
            [
                "export_run_items.org_id",
                "export_run_items.run_id",
                "export_run_items.id",
                "export_run_items.card_revision_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "card_revision_id", "evidence_id"],
            [
                "card_evidence_links.org_id",
                "card_evidence_links.revision_id",
                "card_evidence_links.evidence_id",
            ],
        ),
        tenant_fk("evidence_id", "evidence"),
    )


class ExportRenderCandidate(Tenant, Base):
    __tablename__ = "export_render_candidates"
    run_id: Mapped[UUID] = mapped_column()
    render_job_id: Mapped[UUID] = mapped_column()
    attempt_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    plaintext_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    object_key: Mapped[str] = mapped_column(String(500))
    renderer_profile: Mapped[str] = mapped_column(String(200))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "run_id", "attempt_id"),
        UniqueConstraint("org_id", "run_id", "id"),
        tenant_fk("run_id", "export_runs"),
        tenant_fk("render_job_id", "jobs"),
    )


class Export(Tenant, Base):
    __tablename__ = "exports"
    run_id: Mapped[UUID] = mapped_column()
    candidate_id: Mapped[UUID] = mapped_column()
    task_id: Mapped[UUID] = mapped_column()
    mode: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    file_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(100))
    object_key: Mapped[str] = mapped_column(String(500))
    released_by: Mapped[UUID] = mapped_column()
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "run_id"),
        ForeignKeyConstraint(
            ["org_id", "run_id", "candidate_id"],
            [
                "export_render_candidates.org_id",
                "export_render_candidates.run_id",
                "export_render_candidates.id",
            ],
        ),
        tenant_fk("run_id", "export_runs"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("released_by", "memberships", "user_id"),
    )
