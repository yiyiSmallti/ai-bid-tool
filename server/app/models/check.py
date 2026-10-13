from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
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


class CheckCertificateItem(Base):
    __tablename__ = "check_certificate_items"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    check_item_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("id", "task_id", name="check_certificate_items_id_task_id_key"),
        UniqueConstraint(
            "report_id",
            "certificate_id",
            "check_item_id",
            name="check_certificate_items_report_id_certificate_id_che_key",
        ),
        PrimaryKeyConstraint("id", name="check_certificate_items_pkey"),
        ForeignKeyConstraint(
            ["certificate_id", "task_id", "report_id"],
            ["check_certificates.id", "check_certificates.task_id", "check_certificates.report_id"],
            name="check_certificate_items_certificate_id_task_id_repo_fkey",
        ),
        ForeignKeyConstraint(
            ["check_item_id", "task_id", "report_id"],
            ["check_items.id", "check_items.task_id", "check_items.report_id"],
            name="check_certificate_items_check_item_id_task_id_repor_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_certificate_items_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="check_certificate_items_task_id_fkey"
        ),
    )


class CheckCertificate(Base):
    __tablename__ = "check_certificates"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    task_certificate_id: Mapped[UUID] = mapped_column(nullable=False)
    certificate_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    date_status: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((date_status)::text = ANY ((ARRAY['valid'::character varying, 'expired'::character varying, 'not_yet_valid'::character varying, 'unknown'::character varying])::text[])))",
            name="check_certificate_state",
        ),
        UniqueConstraint("id", "task_id", name="check_certificates_id_task_id_key"),
        UniqueConstraint(
            "id", "task_id", "report_id", name="check_certificates_id_task_id_report_id_key"
        ),
        UniqueConstraint(
            "report_id",
            "task_certificate_id",
            name="check_certificates_report_id_task_certificate_id_key",
        ),
        PrimaryKeyConstraint("id", name="check_certificates_pkey"),
        ForeignKeyConstraint(
            ["certificate_revision_id"],
            ["certificate_revisions.id"],
            name="check_certificates_certificate_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_certificates_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_certificate_id", "task_id", "certificate_revision_id"],
            [
                "task_certificates.id",
                "task_certificates.task_id",
                "task_certificates.certificate_revision_id",
            ],
            name="check_certificates_task_certificate_id_task_id_cert_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="check_certificates_task_id_fkey"),
    )


class CheckDecision(Base):
    __tablename__ = "check_decisions"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    finding_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="session")
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((action)::text = ANY ((ARRAY['dismiss'::character varying, 'reopen'::character varying])::text[])) AND (revision >= 2) AND ((actor_kind)::text = 'session'::text)))",
            name="check_decision_action",
        ),
        CheckConstraint(
            "((((length(reason) >= 1) AND (length(reason) <= 20000)) AND (reason ~ '[^[:space:]]'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((expected_input_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="check_decision_reason",
        ),
        UniqueConstraint("finding_id", "revision", name="check_decisions_finding_id_revision_key"),
        UniqueConstraint("id", "task_id", name="check_decisions_id_task_id_key"),
        PrimaryKeyConstraint("id", name="check_decisions_pkey"),
        ForeignKeyConstraint(
            ["finding_id", "task_id", "report_id"],
            ["check_findings.id", "check_findings.task_id", "check_findings.report_id"],
            name="check_decisions_finding_id_task_id_report_id_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_decisions_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="check_decisions_task_id_fkey"),
    )


class CheckFindingCitation(Base):
    __tablename__ = "check_finding_citations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    finding_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    chunk_id: Mapped[UUID | None] = mapped_column(nullable=True)
    draft_id: Mapped[UUID | None] = mapped_column(nullable=True)
    response_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    field: Mapped[str | None] = mapped_column(String(20), nullable=True)
    evidence_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    check_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((((kind)::text = 'tender'::text) AND (document_id IS NOT NULL) AND (chunk_id IS NOT NULL) AND (source IS NOT NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL) AND (evidence_id IS NULL)) OR (((kind)::text = 'draft'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NOT NULL) AND (response_item_id IS NOT NULL) AND (card_revision_id IS NOT NULL) AND (field IS NOT NULL) AND ((field)::text = ANY ((ARRAY['response_text'::character varying, 'deviation_note'::character varying])::text[])) AND (evidence_id IS NULL)) OR (((kind)::text = 'evidence'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL) AND (evidence_id IS NOT NULL))))",
            name="check_citation_one_source",
        ),
        CheckConstraint(
            "((num_nonnulls(finding_id, check_item_id) = 1))", name="check_citation_one_target"
        ),
        CheckConstraint(
            "((((length(quote) >= 1) AND (length(quote) <= 20000)) AND (POSITION(('{{secret.'::text) IN (quote)) = 0) AND (POSITION(('[REDACTED_'::text) IN (quote)) = 0)))",
            name="check_citation_quote",
        ),
        UniqueConstraint("id", "task_id", name="check_finding_citations_id_task_id_key"),
        PrimaryKeyConstraint("id", name="check_finding_citations_pkey"),
        ForeignKeyConstraint(
            ["check_item_id", "task_id", "report_id"],
            ["check_items.id", "check_items.task_id", "check_items.report_id"],
            name="check_citation_item_binding",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "task_id", "document_id"],
            ["chunks.id", "chunks.task_id", "chunks.document_id"],
            name="check_finding_citations_chunk_id_task_id_document_i_fkey",
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="check_finding_citations_document_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["draft_id", "task_id"],
            ["draft_runs.id", "draft_runs.task_id"],
            name="check_finding_citations_draft_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "task_id"],
            ["evidence.id", "evidence.task_id"],
            name="check_finding_citations_evidence_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["finding_id", "task_id", "report_id"],
            ["check_findings.id", "check_findings.task_id", "check_findings.report_id"],
            name="check_finding_citations_finding_id_task_id_report_i_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_finding_citations_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["response_item_id", "draft_id", "card_revision_id"],
            ["response_items.id", "response_items.draft_id", "response_items.card_revision_id"],
            name="check_finding_citations_response_item_id_draft_id_c_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="check_finding_citations_task_id_fkey"
        ),
        Index(
            "ix_check_citations_item",
            "check_item_id",
            postgresql_where=sql_text("(check_item_id IS NOT NULL)"),
        ),
    )


class CheckFinding(Base):
    __tablename__ = "check_findings"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    check_item_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="deterministic")
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    review_domain: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[])))",
            name="check_finding_domain",
        ),
        CheckConstraint(
            "(((((method)::text = 'deterministic'::text) AND ((code)::text = ANY ((ARRAY['mandatory_response_missing'::character varying, 'negative_deviation'::character varying, 'unconfirmed_evidence'::character varying, 'certificate_expired'::character varying, 'certificate_not_yet_valid'::character varying, 'certificate_date_unknown'::character varying])::text[]))) OR (((method)::text = 'semantic'::text) AND ((code)::text = ANY ((ARRAY['semantic_contradiction'::character varying, 'insufficient_support'::character varying, 'obligation_coverage_uncertain'::character varying])::text[])))))",
            name="check_finding_kind",
        ),
        CheckConstraint(
            "((((length(reason) >= 1) AND (length(reason) <= 20000)) AND (reason ~ '[^[:space:]]'::text)))",
            name="check_finding_reason",
        ),
        CheckConstraint(
            "(((severity)::text = ANY ((ARRAY['disqualification_risk'::character varying, 'deduction_risk'::character varying, 'info'::character varying])::text[])))",
            name="check_finding_severity",
        ),
        UniqueConstraint("id", "task_id", name="check_findings_id_task_id_key"),
        UniqueConstraint(
            "id", "task_id", "report_id", name="check_findings_id_task_id_report_id_key"
        ),
        PrimaryKeyConstraint("id", name="check_findings_pkey"),
        ForeignKeyConstraint(
            ["check_item_id", "task_id", "report_id", "requirement_id"],
            [
                "check_items.id",
                "check_items.task_id",
                "check_items.report_id",
                "check_items.requirement_id",
            ],
            name="check_findings_check_item_id_task_id_report_id_requ_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_findings_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="check_findings_task_id_fkey"),
    )


class CheckItem(Base):
    __tablename__ = "check_items"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    response_item_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    partition: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    semantic_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_requested"
    )
    semantic_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    semantic_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((partition)::text = ANY ((ARRAY['response'::character varying, 'comply_only'::character varying, 'gap'::character varying])::text[])))",
            name="check_item_partition",
        ),
        CheckConstraint("((jsonb_typeof(rules) = 'array'::text))", name="check_item_rules"),
        CheckConstraint(
            "(((((semantic_status)::text = 'not_requested'::text) AND (semantic_outcome IS NULL) AND (semantic_reason_code IS NULL)) OR (((semantic_status)::text = 'assessed'::text) AND (semantic_outcome IS NOT NULL) AND ((semantic_outcome)::text = ANY ((ARRAY['no_risk_found'::character varying, 'risk'::character varying])::text[])) AND (semantic_reason_code IS NULL)) OR (((semantic_status)::text = 'unassessed'::text) AND (semantic_outcome IS NOT NULL) AND ((semantic_outcome)::text = 'unknown'::text) AND (semantic_reason_code IS NOT NULL) AND ((semantic_reason_code)::text = 'semantic_unknown'::text)) OR (((semantic_status)::text = 'unassessed'::text) AND (semantic_outcome IS NULL) AND (semantic_reason_code IS NOT NULL) AND ((semantic_reason_code)::text = ANY ((ARRAY['missing_requirement_id'::character varying, 'duplicate_requirement_id'::character varying, 'unknown_requirement_id'::character varying, 'cross_requirement_citation'::character varying, 'ref_not_sent'::character varying, 'quote_not_at_position'::character varying, 'ambiguous_quote'::character varying, 'redacted_input_unassessable'::character varying, 'sensitive_model_output'::character varying, 'unknown_placeholder'::character varying, 'insufficient_citations'::character varying, 'invalid_semantic_answer'::character varying, 'check_finding_limit'::character varying, 'semantic_provider_failed'::character varying])::text[])))))",
            name="check_item_semantic",
        ),
        UniqueConstraint("id", "task_id", name="check_items_id_task_id_key"),
        UniqueConstraint("id", "task_id", "report_id", name="check_items_id_task_id_report_id_key"),
        UniqueConstraint(
            "id",
            "task_id",
            "report_id",
            "requirement_id",
            name="check_items_id_task_id_report_id_requirement_id_key",
        ),
        UniqueConstraint(
            "report_id", "requirement_id", name="check_items_report_id_requirement_id_key"
        ),
        PrimaryKeyConstraint("id", name="check_items_pkey"),
        ForeignKeyConstraint(
            ["card_revision_id"],
            ["response_card_revisions.id"],
            name="check_items_card_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id", "draft_id", "extraction_job_id"],
            [
                "check_runs.id",
                "check_runs.task_id",
                "check_runs.draft_id",
                "check_runs.extraction_job_id",
            ],
            name="check_items_report_id_task_id_draft_id_extraction_j_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id"],
            ["check_runs.id", "check_runs.task_id"],
            name="check_items_report_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="check_items_requirement_id_task_id_extraction_job_i_fkey",
        ),
        ForeignKeyConstraint(
            ["response_item_id", "draft_id", "requirement_id"],
            ["response_items.id", "response_items.draft_id", "response_items.requirement_id"],
            name="check_items_response_item_id_draft_id_requirement_i_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="check_items_task_id_fkey"),
    )


class CheckRun(Base):
    __tablename__ = "check_runs"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="rules")
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    encrypted_input: Mapped[str] = mapped_column(Text, nullable=False)
    completion: Mapped[str] = mapped_column(String(20), nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="worker")
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text) AND (jsonb_typeof(limitations) = 'array'::text) AND (length(encrypted_input) > 0)))",
            name="check_run_data",
        ),
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((draft_input_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="check_run_hashes",
        ),
        CheckConstraint(
            "(((((mode)::text = 'rules'::text) AND (prompt_version IS NULL)) OR (((mode)::text = 'combined'::text) AND (prompt_version IS NOT NULL) AND ((prompt_version)::text ~ '[^[:space:]]'::text))))",
            name="check_run_mode",
        ),
        CheckConstraint(
            "((((completion)::text = ANY ((ARRAY['complete'::character varying, 'partial'::character varying])::text[])) AND ((actor_kind)::text = 'worker'::text)))",
            name="check_run_state",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "draft_id",
            "extraction_job_id",
            name="check_runs_id_task_id_draft_id_extraction_job_id_key",
        ),
        UniqueConstraint("id", "task_id", name="check_runs_id_task_id_key"),
        UniqueConstraint("job_id", name="check_runs_job_id_key"),
        PrimaryKeyConstraint("id", name="check_runs_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="check_runs_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="check_runs_document_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["draft_id", "task_id", "extraction_job_id"],
            ["draft_runs.id", "draft_runs.task_id", "draft_runs.extraction_job_id"],
            name="check_runs_draft_id_task_id_extraction_job_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="check_runs_extraction_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="check_runs_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="check_runs_task_id_fkey"),
    )
