from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
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


class ScoreRubricSection(Base):
    __tablename__ = "score_rubric_sections"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregation: Mapped[str] = mapped_column(String(30), nullable=False)
    aggregation_rule_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_range: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    included_in_overall_total: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ambiguity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "((cap <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))",
            name="rubric_candidate_section_cap_finite",
        ),
        CheckConstraint(
            "(public.rubric_candidate_range_valid(score_range))",
            name="rubric_candidate_section_range",
        ),
        CheckConstraint(
            "((weight <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))",
            name="rubric_candidate_section_weight_finite",
        ),
        CheckConstraint(
            "(((sources IS NULL) OR ((jsonb_typeof(sources) = 'array'::text) AND (jsonb_array_length(sources) > 0))))",
            name="rubric_section_sources_shape",
        ),
        CheckConstraint(
            "(((aggregation)::text = ANY ((ARRAY['sum'::character varying, 'weighted_sum'::character varying, 'capped_sum'::character varying, 'formula'::character varying, 'non_additive'::character varying])::text[])))",
            name="score_rubric_sections_aggregation_check",
        ),
        CheckConstraint("((key ~ '[^[:space:]]'::text))", name="score_rubric_sections_key_check"),
        CheckConstraint('(("order" >= 1))', name="score_rubric_sections_order_check"),
        CheckConstraint(
            "((title ~ '[^[:space:]]'::text))", name="score_rubric_sections_title_check"
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_sections_id_task_id_key"),
        UniqueConstraint(
            "id", "task_id", "rubric_id", name="score_rubric_sections_id_task_id_rubric_id_key"
        ),
        UniqueConstraint("rubric_id", "key", name="score_rubric_sections_rubric_id_key_key"),
        PrimaryKeyConstraint("id", name="score_rubric_sections_pkey"),
        ForeignKeyConstraint(
            ["requirement_id", "task_id"],
            ["requirements.id", "requirements.task_id"],
            name="score_rubric_sections_requirement_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_sections_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_rubric_sections_task_id_fkey"),
    )


class ScoreItemCitation(Base):
    __tablename__ = "score_item_citations"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    score_item_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    chunk_id: Mapped[UUID | None] = mapped_column(nullable=True)
    draft_id: Mapped[UUID | None] = mapped_column(nullable=True)
    response_item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    card_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    field: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((quote ~ '[^[:space:]]'::text) AND (((kind = 'tender'::text) AND (document_id IS NOT NULL) AND (chunk_id IS NOT NULL) AND (source IS NOT NULL) AND (draft_id IS NULL) AND (response_item_id IS NULL) AND (card_revision_id IS NULL) AND (field IS NULL)) OR ((kind = 'draft'::text) AND (document_id IS NULL) AND (chunk_id IS NULL) AND (source IS NULL) AND (draft_id IS NOT NULL) AND (response_item_id IS NOT NULL) AND (card_revision_id IS NOT NULL) AND (field IS NOT NULL) AND (field = ANY (ARRAY['response_text'::text, 'deviation_note'::text]))))))",
            name="score_citation_shape",
        ),
        UniqueConstraint("id", "task_id", name="score_item_citations_id_task_id_key"),
        PrimaryKeyConstraint("id", name="score_item_citations_pkey"),
        ForeignKeyConstraint(
            ["chunk_id", "task_id", "document_id"],
            ["chunks.id", "chunks.task_id", "chunks.document_id"],
            name="score_item_citations_chunk_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="score_item_citations_document_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["score_item_id", "task_id", "report_id"],
            ["score_report_items.id", "score_report_items.task_id", "score_report_items.report_id"],
            name="score_item_citations_score_item_id_task_id_report_i_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_item_citations_task_id_fkey"),
        ForeignKeyConstraint(
            [
                "task_id",
                "report_id",
                "score_item_id",
                "draft_id",
                "response_item_id",
                "card_revision_id",
            ],
            [
                "score_report_item_responses.task_id",
                "score_report_item_responses.report_id",
                "score_report_item_responses.score_item_id",
                "score_report_item_responses.draft_id",
                "score_report_item_responses.response_item_id",
                "score_report_item_responses.card_revision_id",
            ],
            name="score_item_citations_task_id_report_id_score_item_i_fkey",
        ),
        Index("ix_score_item_citations_item", "score_item_id"),
    )


class ScoreReportItemResponse(Base):
    __tablename__ = "score_report_item_responses"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    score_item_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    response_item_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    card_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("id", "task_id", name="score_report_item_responses_id_task_id_key"),
        UniqueConstraint(
            "score_item_id",
            "response_item_id",
            name="score_report_item_responses_score_item_id_response_i_key",
        ),
        UniqueConstraint(
            "task_id",
            "report_id",
            "score_item_id",
            "draft_id",
            "response_item_id",
            "card_revision_id",
            name="score_report_item_responses_task_id_report_id_score__key",
        ),
        PrimaryKeyConstraint("id", name="score_report_item_responses_pkey"),
        ForeignKeyConstraint(
            ["response_item_id", "draft_id", "requirement_id", "card_revision_id"],
            [
                "response_items.id",
                "response_items.draft_id",
                "response_items.requirement_id",
                "response_items.card_revision_id",
            ],
            name="score_report_item_responses_response_item_id_draft__fkey",
        ),
        ForeignKeyConstraint(
            ["score_item_id", "task_id", "report_id", "draft_id"],
            [
                "score_report_items.id",
                "score_report_items.task_id",
                "score_report_items.report_id",
                "score_report_items.draft_id",
            ],
            name="score_report_item_responses_score_item_id_task_id_r_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="score_report_item_responses_task_id_fkey"
        ),
    )


class ScoreReportItem(Base):
    __tablename__ = "score_report_items"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    report_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_item_id: Mapped[UUID] = mapped_column(nullable=False)
    section_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    anchor_response_item_id: Mapped[UUID] = mapped_column(nullable=False)
    anchor_partition: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    score_range: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    estimated_score: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    deduction_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    strengthening_actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((anchor_partition = ANY (ARRAY['response'::text, 'comply_only'::text, 'gap'::text])) AND (outcome = ANY (ARRAY['assessed'::text, 'unassessable'::text])) AND (reason_code ~ '[^[:space:]]'::text) AND (reason ~ '[^[:space:]]'::text) AND (jsonb_typeof(deduction_reasons) = 'array'::text) AND (jsonb_typeof(strengthening_actions) = 'array'::text) AND (((outcome = 'assessed'::text) AND (estimated_score IS NOT NULL) AND (score_range IS NOT NULL) AND (jsonb_typeof(score_range) = 'object'::text) AND (score_range ?& ARRAY['minimum'::text, 'maximum'::text]) AND (((score_range ->> 'minimum'::text))::numeric >= (0)::numeric) AND ((estimated_score)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND ((estimated_score >= ((score_range ->> 'minimum'::text))::numeric) AND (estimated_score <= ((score_range ->> 'maximum'::text))::numeric)) AND ((estimated_score = ((score_range ->> 'maximum'::text))::numeric) OR (jsonb_array_length(deduction_reasons) > 0))) OR ((outcome = 'unassessable'::text) AND (estimated_score IS NULL)))))",
            name="score_item_shape",
        ),
        UniqueConstraint("id", "task_id", name="score_report_items_id_task_id_key"),
        UniqueConstraint(
            "id",
            "task_id",
            "report_id",
            "draft_id",
            name="score_report_items_id_task_id_report_id_draft_id_key",
        ),
        UniqueConstraint(
            "id", "task_id", "report_id", name="score_report_items_id_task_id_report_id_key"
        ),
        UniqueConstraint(
            "report_id", "rubric_item_id", name="score_report_items_report_id_rubric_item_id_key"
        ),
        PrimaryKeyConstraint("id", name="score_report_items_pkey"),
        ForeignKeyConstraint(
            ["anchor_response_item_id", "draft_id", "requirement_id"],
            ["response_items.id", "response_items.draft_id", "response_items.requirement_id"],
            name="score_report_items_anchor_response_item_id_draft_id_fkey",
        ),
        ForeignKeyConstraint(
            ["report_id", "task_id", "draft_id", "extraction_job_id", "rubric_id"],
            [
                "score_reports.id",
                "score_reports.task_id",
                "score_reports.draft_id",
                "score_reports.extraction_job_id",
                "score_reports.rubric_id",
            ],
            name="score_report_items_report_id_task_id_draft_id_extra_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id", "extraction_job_id"],
            ["requirements.id", "requirements.task_id", "requirements.job_id"],
            name="score_report_items_requirement_id_task_id_extractio_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_item_id", "task_id", "rubric_id", "section_id", "requirement_id"],
            [
                "score_rubric_items.id",
                "score_rubric_items.task_id",
                "score_rubric_items.rubric_id",
                "score_rubric_items.section_id",
                "score_rubric_items.requirement_id",
            ],
            name="score_report_items_rubric_item_id_task_id_rubric_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_report_items_task_id_fkey"),
        Index("ix_score_report_items_report", "report_id"),
    )


class ScoreReport(Base):
    __tablename__ = "score_reports"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    rubric_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rubric_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    rubric_input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    draft_input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    scoring_rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_input: Mapped[str] = mapped_column(Text, nullable=False)
    completion: Mapped[str] = mapped_column(Text, nullable=False)
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((actor_kind = 'worker'::text) AND (rubric_version > 0) AND (rubric_revision > 0) AND (input_hash ~ '^[0-9a-f]{64}$'::text) AND (draft_input_hash ~ '^[0-9a-f]{64}$'::text) AND (rubric_input_hash ~ '^[0-9a-f]{64}$'::text) AND (completion = ANY (ARRAY['complete'::text, 'partial'::text])) AND (prompt_version ~ '[^[:space:]]'::text) AND (schema_version ~ '[^[:space:]]'::text) AND (scoring_rule_version ~ '[^[:space:]]'::text) AND (jsonb_typeof(input_manifest) = 'object'::text) AND (jsonb_typeof(summary) = 'object'::text) AND (jsonb_typeof(sections) = 'array'::text) AND (jsonb_typeof(limitations) = 'array'::text)))",
            name="score_report_shape",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "draft_id",
            "extraction_job_id",
            "rubric_id",
            name="score_reports_id_task_id_draft_id_extraction_job_id__key",
        ),
        UniqueConstraint("id", "task_id", name="score_reports_id_task_id_key"),
        UniqueConstraint("job_id", name="score_reports_job_id_key"),
        PrimaryKeyConstraint("id", name="score_reports_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="score_reports_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="score_reports_document_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["draft_id", "task_id", "extraction_job_id"],
            ["draft_runs.id", "draft_runs.task_id", "draft_runs.extraction_job_id"],
            name="score_reports_draft_id_task_id_extraction_job_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="score_reports_extraction_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="score_reports_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id", "extraction_job_id", "document_id"],
            [
                "score_rubric_sets.id",
                "score_rubric_sets.task_id",
                "score_rubric_sets.extraction_job_id",
                "score_rubric_sets.document_id",
            ],
            name="score_reports_rubric_id_task_id_extraction_job_id_d_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_reports_task_id_fkey"),
        Index("ix_score_reports_task", "task_id", "created_at", "id"),
    )


class ScoreRubricClassification(Base):
    __tablename__ = "score_rubric_classifications"
    section_id: Mapped[UUID | None] = mapped_column(nullable=True)
    item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    review_domain: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    set_revision: Mapped[int] = mapped_column(Integer, nullable=False)
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
            "(((actor_kind)::text = 'session'::text))",
            name="score_rubric_classifications_actor_kind_check",
        ),
        CheckConstraint(
            "(((section_id IS NULL) <> (item_id IS NULL)))",
            name="score_rubric_classifications_check",
        ),
        CheckConstraint(
            "((reason ~ '[^[:space:]]'::text))", name="score_rubric_classifications_reason_check"
        ),
        CheckConstraint(
            "(((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[])))",
            name="score_rubric_classifications_review_domain_check",
        ),
        CheckConstraint("((revision >= 2))", name="score_rubric_classifications_revision_check"),
        CheckConstraint(
            "((set_revision >= 2))", name="score_rubric_classifications_set_revision_check"
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_classifications_id_task_id_key"),
        UniqueConstraint(
            "rubric_id",
            "set_revision",
            name="score_rubric_classifications_rubric_id_set_revision_key",
        ),
        PrimaryKeyConstraint("id", name="score_rubric_classifications_pkey"),
        ForeignKeyConstraint(
            ["item_id", "task_id", "rubric_id"],
            ["score_rubric_items.id", "score_rubric_items.task_id", "score_rubric_items.rubric_id"],
            name="score_rubric_classifications_item_id_task_id_rubric_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_classifications_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["section_id", "task_id", "rubric_id"],
            [
                "score_rubric_sections.id",
                "score_rubric_sections.task_id",
                "score_rubric_sections.rubric_id",
            ],
            name="score_rubric_classifications_section_id_task_id_rub_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="score_rubric_classifications_task_id_fkey"
        ),
    )


class ScoreRubricCoverage(Base):
    __tablename__ = "score_rubric_coverage"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint("id", "task_id", name="score_rubric_coverage_id_task_id_key"),
        UniqueConstraint(
            "id", "task_id", "rubric_id", name="score_rubric_coverage_id_task_id_rubric_id_key"
        ),
        UniqueConstraint(
            "rubric_id", "requirement_id", name="score_rubric_coverage_rubric_id_requirement_id_key"
        ),
        PrimaryKeyConstraint("id", name="score_rubric_coverage_pkey"),
        ForeignKeyConstraint(
            ["requirement_id", "task_id"],
            ["requirements.id", "requirements.task_id"],
            name="score_rubric_coverage_requirement_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_coverage_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_rubric_coverage_task_id_fkey"),
    )


class ScoreRubricCoverageDecision(Base):
    __tablename__ = "score_rubric_coverage_decisions"
    coverage_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    canonical_requirement_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    set_revision: Mapped[int] = mapped_column(Integer, nullable=False)
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
            "(((action)::text = ANY ((ARRAY['mapped'::character varying, 'duplicate'::character varying, 'excluded'::character varying, 'reopen'::character varying])::text[])))",
            name="score_rubric_coverage_decisions_action_check",
        ),
        CheckConstraint(
            "(((actor_kind)::text = 'session'::text))",
            name="score_rubric_coverage_decisions_actor_kind_check",
        ),
        CheckConstraint(
            "((((action)::text = 'duplicate'::text) = (canonical_requirement_id IS NOT NULL)))",
            name="score_rubric_coverage_decisions_check",
        ),
        CheckConstraint(
            "((canonical_requirement_id <> requirement_id))",
            name="score_rubric_coverage_decisions_check1",
        ),
        CheckConstraint(
            "((reason ~ '[^[:space:]]'::text))", name="score_rubric_coverage_decisions_reason_check"
        ),
        CheckConstraint("((revision >= 2))", name="score_rubric_coverage_decisions_revision_check"),
        CheckConstraint(
            "((set_revision >= 2))", name="score_rubric_coverage_decisions_set_revision_check"
        ),
        UniqueConstraint(
            "coverage_id",
            "revision",
            name="score_rubric_coverage_decisions_coverage_id_revision_key",
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_coverage_decisions_id_task_id_key"),
        UniqueConstraint(
            "id",
            "task_id",
            "rubric_id",
            name="score_rubric_coverage_decisions_id_task_id_rubric_id_key",
        ),
        PrimaryKeyConstraint("id", name="score_rubric_coverage_decisions_pkey"),
        ForeignKeyConstraint(
            ["canonical_requirement_id", "task_id"],
            ["requirements.id", "requirements.task_id"],
            name="score_rubric_coverage_decisio_canonical_requirement_fkey",
        ),
        ForeignKeyConstraint(
            ["coverage_id", "task_id", "rubric_id"],
            [
                "score_rubric_coverage.id",
                "score_rubric_coverage.task_id",
                "score_rubric_coverage.rubric_id",
            ],
            name="score_rubric_coverage_decisio_coverage_id_task_id_r_fkey",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "task_id"],
            ["requirements.id", "requirements.task_id"],
            name="score_rubric_coverage_decisio_requirement_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_coverage_decisions_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="score_rubric_coverage_decisions_task_id_fkey"
        ),
    )


class ScoreRubricCoverageItem(Base):
    __tablename__ = "score_rubric_coverage_items"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    coverage_decision_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_item_id: Mapped[UUID] = mapped_column(nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        UniqueConstraint(
            "coverage_decision_id",
            "rubric_item_id",
            name="score_rubric_coverage_items_coverage_decision_id_rub_key",
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_coverage_items_id_task_id_key"),
        PrimaryKeyConstraint("id", name="score_rubric_coverage_items_pkey"),
        ForeignKeyConstraint(
            ["coverage_decision_id", "task_id", "rubric_id"],
            [
                "score_rubric_coverage_decisions.id",
                "score_rubric_coverage_decisions.task_id",
                "score_rubric_coverage_decisions.rubric_id",
            ],
            name="score_rubric_coverage_items_coverage_decision_id_ta_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_coverage_items_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_item_id", "task_id", "rubric_id"],
            ["score_rubric_items.id", "score_rubric_items.task_id", "score_rubric_items.rubric_id"],
            name="score_rubric_coverage_items_rubric_item_id_task_id__fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="score_rubric_coverage_items_task_id_fkey"
        ),
    )


class ScoreRubricDecision(Base):
    __tablename__ = "score_rubric_decisions"
    section_id: Mapped[UUID | None] = mapped_column(nullable=True)
    item_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    set_revision: Mapped[int] = mapped_column(Integer, nullable=False)
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
            "(((action)::text = ANY ((ARRAY['confirm'::character varying, 'reject'::character varying, 'reopen'::character varying])::text[])))",
            name="score_rubric_decisions_action_check",
        ),
        CheckConstraint(
            "(((actor_kind)::text = 'session'::text))",
            name="score_rubric_decisions_actor_kind_check",
        ),
        CheckConstraint(
            "((NOT ((section_id IS NOT NULL) AND (item_id IS NOT NULL))))",
            name="score_rubric_decisions_check",
        ),
        CheckConstraint(
            "((reason ~ '[^[:space:]]'::text))", name="score_rubric_decisions_reason_check"
        ),
        CheckConstraint("((revision >= 2))", name="score_rubric_decisions_revision_check"),
        CheckConstraint("((set_revision >= 2))", name="score_rubric_decisions_set_revision_check"),
        UniqueConstraint("id", "task_id", name="score_rubric_decisions_id_task_id_key"),
        UniqueConstraint(
            "rubric_id", "set_revision", name="score_rubric_decisions_rubric_id_set_revision_key"
        ),
        PrimaryKeyConstraint("id", name="score_rubric_decisions_pkey"),
        ForeignKeyConstraint(
            ["item_id", "task_id", "rubric_id"],
            ["score_rubric_items.id", "score_rubric_items.task_id", "score_rubric_items.rubric_id"],
            name="score_rubric_decisions_item_id_task_id_rubric_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_decisions_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["section_id", "task_id", "rubric_id"],
            [
                "score_rubric_sections.id",
                "score_rubric_sections.task_id",
                "score_rubric_sections.rubric_id",
            ],
            name="score_rubric_decisions_section_id_task_id_rubric_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_rubric_decisions_task_id_fkey"),
    )


class ScoreRubricItem(Base):
    __tablename__ = "score_rubric_items"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    section_id: Mapped[UUID] = mapped_column(nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    score_range: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    ambiguity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(public.rubric_candidate_range_valid(score_range))", name="rubric_candidate_item_range"
        ),
        CheckConstraint(
            "((weight <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))",
            name="rubric_candidate_item_weight_finite",
        ),
        CheckConstraint(
            "(((assessment_mode)::text = ANY ((ARRAY['model_assessable'::character varying, 'ambiguous'::character varying, 'price_comparison'::character varying, 'external_comparison'::character varying, 'manual_only'::character varying, 'unsupported_formula'::character varying])::text[])))",
            name="score_rubric_items_assessment_mode_check",
        ),
        CheckConstraint("((key ~ '[^[:space:]]'::text))", name="score_rubric_items_key_check"),
        CheckConstraint('(("order" >= 1))', name="score_rubric_items_order_check"),
        CheckConstraint(
            "((rule_text ~ '[^[:space:]]'::text))", name="score_rubric_items_rule_text_check"
        ),
        CheckConstraint("((title ~ '[^[:space:]]'::text))", name="score_rubric_items_title_check"),
        UniqueConstraint(
            "id",
            "task_id",
            "rubric_id",
            "section_id",
            "requirement_id",
            name="score_rubric_item_binding",
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_items_id_task_id_key"),
        UniqueConstraint(
            "id", "task_id", "rubric_id", name="score_rubric_items_id_task_id_rubric_id_key"
        ),
        UniqueConstraint(
            "rubric_id", "fingerprint", name="score_rubric_items_rubric_id_fingerprint_key"
        ),
        UniqueConstraint("rubric_id", "key", name="score_rubric_items_rubric_id_key_key"),
        PrimaryKeyConstraint("id", name="score_rubric_items_pkey"),
        ForeignKeyConstraint(
            ["requirement_id", "task_id"],
            ["requirements.id", "requirements.task_id"],
            name="score_rubric_items_requirement_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_items_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["section_id", "task_id", "rubric_id"],
            [
                "score_rubric_sections.id",
                "score_rubric_sections.task_id",
                "score_rubric_sections.rubric_id",
            ],
            name="score_rubric_items_section_id_task_id_rubric_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_rubric_items_task_id_fkey"),
    )


class ScoreRubricRevisionEvent(Base):
    __tablename__ = "score_rubric_revision_events"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    prior_rubric_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reason_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="session")
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((actor_kind)::text = 'session'::text))",
            name="score_rubric_revision_events_actor_kind_check",
        ),
        CheckConstraint(
            "((reason ~ '[^[:space:]]'::text))", name="score_rubric_revision_events_reason_check"
        ),
        CheckConstraint(
            "((jsonb_typeof(replacement_snapshot) = 'object'::text))",
            name="score_rubric_revision_events_replacement_snapshot_check",
        ),
        CheckConstraint(
            "((length((snapshot_sha256)::text) = 64))",
            name="score_rubric_revision_events_snapshot_sha256_check",
        ),
        CheckConstraint("((version >= 2))", name="score_rubric_revision_events_version_check"),
        UniqueConstraint("id", "task_id", name="score_rubric_revision_events_id_task_id_key"),
        UniqueConstraint("rubric_id", name="score_rubric_revision_events_rubric_id_key"),
        PrimaryKeyConstraint("id", name="score_rubric_revision_events_pkey"),
        ForeignKeyConstraint(
            ["prior_rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_revision_events_prior_rubric_id_task_i_fkey",
        ),
        ForeignKeyConstraint(
            ["rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_revision_events_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="score_rubric_revision_events_task_id_fkey"
        ),
    )


class ScoreRubricSet(Base):
    __tablename__ = "score_rubric_sets"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    prior_rubric_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    encrypted_input: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    overall_aggregation: Mapped[str] = mapped_column(String(30), nullable=False)
    overall_rule_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_score_range: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    overall_cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    normalization_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    actor_token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((overall_cap <> ALL (ARRAY['NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric])))",
            name="rubric_candidate_overall_cap_finite",
        ),
        CheckConstraint(
            "(public.rubric_candidate_range_valid(overall_score_range))",
            name="rubric_candidate_overall_range",
        ),
        CheckConstraint(
            "(((actor_kind)::text = ANY ((ARRAY['worker'::character varying, 'session'::character varying])::text[])))",
            name="score_rubric_sets_actor_kind_check",
        ),
        CheckConstraint(
            "((length((input_hash)::text) = 64))", name="score_rubric_sets_input_hash_check"
        ),
        CheckConstraint(
            "((jsonb_typeof(input_manifest) = 'object'::text))",
            name="score_rubric_sets_input_manifest_check",
        ),
        CheckConstraint(
            "(COALESCE((jsonb_typeof((input_manifest -> 'requirements'::text)) = 'array'::text), false))",
            name="score_rubric_sets_input_manifest_check1",
        ),
        CheckConstraint(
            "((jsonb_typeof(normalization_errors) = 'array'::text))",
            name="score_rubric_sets_normalization_errors_check",
        ),
        CheckConstraint(
            "(((overall_aggregation)::text = ANY ((ARRAY['sum'::character varying, 'weighted_sum'::character varying, 'capped_sum'::character varying, 'formula'::character varying, 'non_additive'::character varying])::text[])))",
            name="score_rubric_sets_overall_aggregation_check",
        ),
        CheckConstraint("((version >= 1))", name="score_rubric_sets_version_check"),
        UniqueConstraint(
            "id", "task_id", "extraction_job_id", "document_id", name="score_rubric_input_binding"
        ),
        UniqueConstraint(
            "extraction_job_id", "version", name="score_rubric_sets_extraction_job_id_version_key"
        ),
        UniqueConstraint("id", "task_id", name="score_rubric_sets_id_task_id_key"),
        PrimaryKeyConstraint("id", name="score_rubric_sets_pkey"),
        ForeignKeyConstraint(
            ["actor_token_id"], ["api_tokens.id"], name="score_rubric_sets_actor_token_id_fkey"
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="score_rubric_sets_document_id_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="score_rubric_sets_extraction_job_id_task_id_documen_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="score_rubric_sets_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["prior_rubric_id", "task_id"],
            ["score_rubric_sets.id", "score_rubric_sets.task_id"],
            name="score_rubric_sets_prior_rubric_id_task_id_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="score_rubric_sets_task_id_fkey"),
        Index(
            "rubric_generated_job_once",
            "job_id",
            unique=True,
            postgresql_where=sql_text("(prior_rubric_id IS NULL)"),
        ),
        Index(
            "rubric_revision_once",
            "prior_rubric_id",
            unique=True,
            postgresql_where=sql_text("(prior_rubric_id IS NOT NULL)"),
        ),
    )
