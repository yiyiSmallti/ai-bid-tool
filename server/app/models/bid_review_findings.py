from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class BidReviewFindingEvent(Base):
    __tablename__ = "bid_review_finding_events"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    finding_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_decision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    review_domain: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((revision >= 2) AND (revision <= 100000)) AND ((action)::text = ANY ((ARRAY['classify'::character varying, 'dismiss'::character varying, 'reopen'::character varying, 'confirm'::character varying])::text[])) AND ((review_domain)::text = ANY ((ARRAY['commercial'::character varying, 'technical'::character varying])::text[])) AND ((state)::text = ANY ((ARRAY['open'::character varying, 'dismissed'::character varying, 'confirmed'::character varying])::text[]))))",
            name="bid_review_finding_events_check",
        ),
        CheckConstraint(
            "((((expected_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((reason_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((payload_hash)::text ~ '^[0-9a-f]{64}$'::text) AND (length(reason_encrypted) > 0)))",
            name="bid_review_finding_events_check1",
        ),
        UniqueConstraint("request_id", name="bid_review_finding_events_decided_by_request_id_key"),
        UniqueConstraint(
            "finding_id", "revision", name="bid_review_finding_events_finding_id_revision_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_finding_events_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "review_id",
            "finding_id",
            "id",
            name="bid_review_finding_events_task_id_submission_id_revi_key",
        ),
        PrimaryKeyConstraint("id", name="bid_review_finding_events_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_finding_events_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "finding_id", "prior_decision_id"],
            [
                "bid_review_finding_events.task_id",
                "bid_review_finding_events.submission_id",
                "bid_review_finding_events.review_id",
                "bid_review_finding_events.finding_id",
                "bid_review_finding_events.id",
            ],
            name="bid_review_finding_events_task_id_submission_id_re_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "finding_id"],
            [
                "bid_review_findings.task_id",
                "bid_review_findings.submission_id",
                "bid_review_findings.review_id",
                "bid_review_findings.id",
            ],
            name="bid_review_finding_events_task_id_submission_id_rev_fkey",
        ),
    )


class BidReviewFindingSource(Base):
    __tablename__ = "bid_review_finding_sources"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    finding_id: Mapped[UUID] = mapped_column(nullable=False)
    preparation_id: Mapped[UUID] = mapped_column(nullable=False)
    validation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    page_id: Mapped[UUID | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((kind)::text = 'rule_document'::text) = (validation_id IS NOT NULL)))",
            name="bid_review_finding_sources_check",
        ),
        CheckConstraint(
            "((((kind)::text = ANY ((ARRAY['tender_support'::character varying, 'bid_support'::character varying, 'absence_page'::character varying, 'inventory_document'::character varying, 'rule_document'::character varying])::text[])) AND ((ordinal >= 1) AND (ordinal <= 1000))))",
            name="bid_review_finding_sources_check1",
        ),
        CheckConstraint(
            "((((kind)::text = ANY ((ARRAY['inventory_document'::character varying, 'rule_document'::character varying])::text[])) = (page_id IS NULL)))",
            name="bid_review_finding_sources_check2",
        ),
        CheckConstraint(
            "((((kind)::text = ANY ((ARRAY['tender_support'::character varying, 'bid_support'::character varying])::text[])) = (quote_sha256 IS NOT NULL)))",
            name="bid_review_finding_sources_check3",
        ),
        CheckConstraint(
            "((((quote_sha256 IS NULL) AND (start_offset IS NULL) AND (end_offset IS NULL)) OR ((quote_sha256 IS NOT NULL) AND (start_offset IS NOT NULL) AND (end_offset IS NOT NULL) AND (start_offset >= 0) AND (end_offset > start_offset))))",
            name="bid_review_finding_sources_check4",
        ),
        CheckConstraint(
            "(((quote_sha256 IS NULL) OR ((quote_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_review_finding_sources_quote_sha256_check",
        ),
        UniqueConstraint(
            "finding_id",
            "kind",
            "ordinal",
            name="bid_review_finding_sources_finding_id_kind_ordinal_key",
        ),
        UniqueConstraint("task_id", "id", name="bid_review_finding_sources_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_finding_sources_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_finding_sources_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "document_id"],
            [
                "bid_submission_documents.task_id",
                "bid_submission_documents.submission_id",
                "bid_submission_documents.id",
            ],
            name="bid_review_finding_sources_task_id_submission_id_do_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "page_id"],
            [
                "bid_document_pages.task_id",
                "bid_document_pages.submission_id",
                "bid_document_pages.id",
            ],
            name="bid_review_finding_sources_task_id_submission_id_pa_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "preparation_id", "document_id", "validation_id"],
            [
                "bid_pdf_validations.task_id",
                "bid_pdf_validations.submission_id",
                "bid_pdf_validations.preparation_id",
                "bid_pdf_validations.document_id",
                "bid_pdf_validations.id",
            ],
            name="bid_review_finding_sources_task_id_submission_id_pr_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "finding_id"],
            [
                "bid_review_findings.task_id",
                "bid_review_findings.submission_id",
                "bid_review_findings.review_id",
                "bid_review_findings.id",
            ],
            name="bid_review_finding_sources_task_id_submission_id_re_fkey",
        ),
        Index(
            "bid_review_absence_page_unique",
            "finding_id",
            "page_id",
            unique=True,
            postgresql_where=sql_text("((kind)::text = 'absence_page'::text)"),
        ),
        Index(
            "bid_review_inventory_source_unique",
            "finding_id",
            "kind",
            "document_id",
            unique=True,
            postgresql_where=sql_text(
                "((kind)::text = ANY ((ARRAY['inventory_document'::character varying, 'rule_document'::character varying])::text[]))"
            ),
        ),
    )


class BidReviewFinding(Base):
    __tablename__ = "bid_review_findings"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    impact: Mapped[str] = mapped_column(String(20), nullable=False)
    tender_support_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bid_support_count: Mapped[int] = mapped_column(Integer, nullable=False)
    search_source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    absence_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    absence_coverage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    absence_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    absence_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    limitation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    details_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((tender_support_count >= 1) AND (tender_support_count <= 20)) AND ((bid_support_count >= 0) AND (bid_support_count <= 20)) AND ((search_source_count >= 0) AND (search_source_count <= 1000)) AND ((rule_document_count >= 0) AND (rule_document_count <= 19)) AND ((limitation_count >= 0) AND (limitation_count <= 100))))",
            name="bid_review_findings_check",
        ),
        CheckConstraint(
            "((((absence_kind IS NULL) AND (absence_coverage IS NULL) AND (absence_method IS NULL) AND (absence_manifest_sha256 IS NULL) AND (search_source_count = 0) AND ((bid_support_count > 0) OR (rule_document_count > 0))) OR (((absence_kind)::text = 'locations'::text) AND ((absence_coverage)::text = ANY ((ARRAY['required_locations'::character varying, 'all_bid_pages'::character varying, 'partial'::character varying])::text[])) AND ((absence_method)::text = ANY ((ARRAY['local_text'::character varying, 'local_pdf'::character varying, 'ocr'::character varying, 'llm'::character varying, 'human'::character varying])::text[])) AND (absence_manifest_sha256 IS NULL) AND ((search_source_count > 0) OR (((outcome)::text = 'unknown'::text) AND ((absence_coverage)::text = 'partial'::text)))) OR (((absence_kind)::text = 'submission_inventory'::text) AND ((absence_coverage)::text = ANY ((ARRAY['complete_inventory'::character varying, 'partial'::character varying])::text[])) AND ((absence_method)::text = ANY ((ARRAY['manifest_rule'::character varying, 'llm_mapping'::character varying, 'human'::character varying])::text[])) AND ((absence_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((search_source_count >= 1) AND (search_source_count <= 19)))))",
            name="bid_review_findings_check1",
        ),
        CheckConstraint(
            "(((((absence_coverage)::text IS DISTINCT FROM 'partial'::text) OR (((outcome)::text = 'unknown'::text) AND (limitation_count > 0))) AND (((outcome)::text <> 'unknown'::text) OR (limitation_count > 0))))",
            name="bid_review_findings_check2",
        ),
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 10000)) AND ((code)::text ~ '^[a-z][a-z0-9_]{0,99}$'::text)))",
            name="bid_review_findings_check3",
        ),
        CheckConstraint(
            "((((outcome)::text = ANY ((ARRAY['responded'::character varying, 'deviation'::character varying, 'missing'::character varying, 'unknown'::character varying])::text[])) AND ((severity)::text = ANY ((ARRAY['fatal'::character varying, 'high'::character varying, 'medium'::character varying])::text[])) AND ((impact)::text = ANY ((ARRAY['rejection'::character varying, 'lost_points'::character varying, 'both'::character varying, 'uncertain'::character varying])::text[]))))",
            name="bid_review_findings_check4",
        ),
        CheckConstraint(
            "(((((outcome)::text <> 'responded'::text) OR (bid_support_count > 0)) AND (((outcome)::text <> 'deviation'::text) OR (bid_support_count > 0) OR (rule_document_count > 0)) AND (((outcome)::text <> ALL ((ARRAY['missing'::character varying, 'unknown'::character varying])::text[])) OR (absence_kind IS NOT NULL)) AND (((outcome)::text <> 'missing'::text) OR (bid_support_count = 0)) AND ((rule_document_count = 0) OR (((code)::text = 'signature_validation_invalid'::text) AND ((outcome)::text = 'deviation'::text)))))",
            name="bid_review_findings_check5",
        ),
        CheckConstraint(
            "(((length(details_encrypted) > 0) AND ((details_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_review_findings_check6",
        ),
        UniqueConstraint("review_id", "ordinal", name="bid_review_findings_review_id_ordinal_key"),
        UniqueConstraint("task_id", "id", name="bid_review_findings_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "review_id",
            "id",
            name="bid_review_findings_task_id_submission_id_review_id__key",
        ),
        PrimaryKeyConstraint("id", name="bid_review_findings_pkey"),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="bid_review_findings_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "obligation_id"],
            [
                "bid_review_obligations.task_id",
                "bid_review_obligations.submission_id",
                "bid_review_obligations.review_id",
                "bid_review_obligations.id",
            ],
            name="bid_review_findings_task_id_submission_id_review_i_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_findings_task_id_submission_id_review_id_fkey",
        ),
        Index("bid_review_findings_page", "review_id", "ordinal"),
    )
