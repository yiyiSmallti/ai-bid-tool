"""Tenant-scoped response review history and immutable draft snapshots."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def tenant_fk(column: str, table: str, target: str = "id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["org_id", column], [f"{table}.org_id", f"{table}.{target}"])


class ResponseCard(Tenant, Base):
    __tablename__ = "response_cards"
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    current_revision_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "task_id"),
        UniqueConstraint(
            "org_id",
            "id",
            "task_id",
            "extraction_job_id",
            "requirement_id",
            name="annotation_card_scope",
        ),
        UniqueConstraint("org_id", "task_id", "extraction_job_id", "requirement_id"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("extraction_job_id", "jobs"),
        tenant_fk("requirement_id", "requirements"),
        ForeignKeyConstraint(
            ["org_id", "requirement_id", "task_id", "extraction_job_id"],
            [
                "requirements.org_id",
                "requirements.id",
                "requirements.task_id",
                "requirements.job_id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "extraction_job_id", "task_id"], ["jobs.org_id", "jobs.id", "jobs.task_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "id", "current_revision_id", "revision"],
            [
                "response_card_revisions.org_id",
                "response_card_revisions.card_id",
                "response_card_revisions.id",
                "response_card_revisions.revision",
            ],
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
            name="response_card_current_revision",
        ),
    )


class ResponseCardRevision(Tenant, Base):
    __tablename__ = "response_card_revisions"
    card_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    quote_sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30))
    review_domain: Mapped[str | None] = mapped_column(String(20))
    disposition: Mapped[str | None] = mapped_column(String(20))
    disposition_by: Mapped[UUID | None] = mapped_column()
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_kind: Mapped[str | None] = mapped_column(String(20))
    suggested_disposition: Mapped[str | None] = mapped_column(String(20))
    response_text: Mapped[str | None] = mapped_column(Text)
    deviation: Mapped[str | None] = mapped_column(String(20))
    deviation_note: Mapped[str | None] = mapped_column(Text)
    review_hint: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    reviewed_warning_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    confirmed_by: Mapped[UUID | None] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(20))
    model_job_id: Mapped[UUID | None] = mapped_column()
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "card_id", "revision"),
        UniqueConstraint("org_id", "card_id", "id", "revision"),
        UniqueConstraint("org_id", "card_id", "id"),
        tenant_fk("card_id", "response_cards"),
        tenant_fk("disposition_by", "memberships", "user_id"),
        tenant_fk("confirmed_by", "memberships", "user_id"),
        tenant_fk("actor_user_id", "memberships", "user_id"),
        tenant_fk("actor_token_id", "api_tokens"),
        tenant_fk("model_job_id", "jobs"),
    )


class Evidence(Tenant, Base):
    __tablename__ = "evidence"
    task_id: Mapped[UUID] = mapped_column()
    card_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(30))
    task_resource_id: Mapped[UUID | None] = mapped_column()
    product_revision_id: Mapped[UUID | None] = mapped_column()
    task_feature_id: Mapped[UUID | None] = mapped_column()
    feature_revision_id: Mapped[UUID | None] = mapped_column()
    task_certificate_id: Mapped[UUID | None] = mapped_column()
    certificate_revision_id: Mapped[UUID | None] = mapped_column()
    task_org_profile_id: Mapped[UUID | None] = mapped_column()
    profile_revision_id: Mapped[UUID | None] = mapped_column()
    evidence_source_id: Mapped[UUID | None] = mapped_column()
    field_path: Mapped[str | None] = mapped_column(String(200))
    quote: Mapped[str | None] = mapped_column(Text)
    material_kind: Mapped[str] = mapped_column(String(40))
    quote_check: Mapped[str] = mapped_column(String(40))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    page: Mapped[int | None] = mapped_column(Integer)
    screenshot_asset_id: Mapped[UUID | None] = mapped_column()
    screenshot_rendition_id: Mapped[UUID | None] = mapped_column()
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    claim_scope: Mapped[str | None] = mapped_column(String(40))
    visual_observation: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[UUID | None] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "card_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "task_id"],
            ["response_cards.org_id", "response_cards.id", "response_cards.task_id"],
        ),
        tenant_fk("confirmed_by", "memberships", "user_id"),
        tenant_fk("task_resource_id", "task_resources"),
        tenant_fk("product_revision_id", "product_revisions"),
        tenant_fk("task_feature_id", "task_features"),
        tenant_fk("feature_revision_id", "feature_revisions"),
        tenant_fk("task_certificate_id", "task_certificates"),
        tenant_fk("certificate_revision_id", "certificate_revisions"),
        tenant_fk("task_org_profile_id", "task_org_profiles"),
        tenant_fk("profile_revision_id", "org_profile_revisions"),
        tenant_fk("evidence_source_id", "evidence_sources"),
        ForeignKeyConstraint(
            ["org_id", "task_id", "screenshot_asset_id"],
            ["screenshot_assets.org_id", "screenshot_assets.task_id", "screenshot_assets.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "screenshot_asset_id", "screenshot_rendition_id", "image_sha256"],
            [
                "screenshot_renditions.org_id",
                "screenshot_renditions.asset_id",
                "screenshot_renditions.id",
                "screenshot_renditions.image_sha256",
            ],
        ),
    )


class CardEvidenceLink(Tenant, Base):
    __tablename__ = "card_evidence_links"
    card_id: Mapped[UUID] = mapped_column()
    revision_id: Mapped[UUID] = mapped_column()
    evidence_id: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "revision_id", "evidence_id"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "revision_id"],
            [
                "response_card_revisions.org_id",
                "response_card_revisions.card_id",
                "response_card_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["org_id", "card_id", "evidence_id"],
            ["evidence.org_id", "evidence.card_id", "evidence.id"],
        ),
    )


class RunFields:
    task_id: Mapped[UUID] = mapped_column()
    extraction_job_id: Mapped[UUID] = mapped_column()
    generation_job_id: Mapped[UUID] = mapped_column()
    generation_run_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[UUID] = mapped_column()
    actor_token_id: Mapped[UUID | None] = mapped_column()
    actor_kind: Mapped[str] = mapped_column(String(20))
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)


def run_constraints() -> tuple[Any, ...]:
    return (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "generation_job_id"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("extraction_job_id", "jobs"),
        tenant_fk("generation_job_id", "jobs"),
        ForeignKeyConstraint(
            ["org_id", "extraction_job_id", "task_id"], ["jobs.org_id", "jobs.id", "jobs.task_id"]
        ),
        ForeignKeyConstraint(
            ["org_id", "generation_job_id", "task_id"], ["jobs.org_id", "jobs.id", "jobs.task_id"]
        ),
        tenant_fk("actor_user_id", "memberships", "user_id"),
        tenant_fk("actor_token_id", "api_tokens"),
    )


class CardGenerationRun(RunFields, Tenant, Base):
    __tablename__ = "card_generation_runs"
    target_revisions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    platform_model_id: Mapped[str | None] = mapped_column(String(40))
    model_revision: Mapped[int | None] = mapped_column(Integer)
    reasoning: Mapped[str | None] = mapped_column(String(20))
    model_redaction_enabled: Mapped[bool] = mapped_column()
    model_redaction_revision: Mapped[int] = mapped_column(Integer)
    redaction_rule_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[str] = mapped_column(String(40))
    adapter_version: Mapped[str] = mapped_column(String(100))
    encrypted_input: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = run_constraints()


class DraftRun(RunFields, Tenant, Base):
    __tablename__ = "draft_runs"
    completion: Mapped[str] = mapped_column(String(20))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        *run_constraints(),
        UniqueConstraint("org_id", "task_id", "extraction_job_id", "input_hash"),
    )


class ResponseItem(Tenant, Base):
    __tablename__ = "response_items"
    draft_id: Mapped[UUID] = mapped_column()
    requirement_id: Mapped[UUID] = mapped_column()
    category: Mapped[str] = mapped_column(String(20))
    starred: Mapped[bool] = mapped_column()
    card_id: Mapped[UUID | None] = mapped_column()
    card_revision_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    table: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    location_label: Mapped[str] = mapped_column(Text)
    response_kind: Mapped[str | None] = mapped_column(String(20))
    response_text: Mapped[str | None] = mapped_column(Text)
    deviation: Mapped[str | None] = mapped_column(String(20))
    deviation_note: Mapped[str | None] = mapped_column(Text)
    disposition_by: Mapped[UUID | None] = mapped_column()
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gap_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "draft_id", "requirement_id"),
        tenant_fk("draft_id", "draft_runs"),
        tenant_fk("requirement_id", "requirements"),
        tenant_fk("card_id", "response_cards"),
        tenant_fk("card_revision_id", "response_card_revisions"),
        ForeignKeyConstraint(
            ["org_id", "card_id", "card_revision_id"],
            [
                "response_card_revisions.org_id",
                "response_card_revisions.card_id",
                "response_card_revisions.id",
            ],
        ),
        tenant_fk("disposition_by", "memberships", "user_id"),
    )
