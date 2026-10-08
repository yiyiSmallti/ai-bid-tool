"""Immutable findings, normalized inspected sources and append-only human events."""

from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk
from app.models.bid_review_run import run_fk
from app.models.entities import Base, Tenant


class BidReviewFinding(Tenant, Base):
    __tablename__ = "bid_review_findings"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    obligation_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    impact: Mapped[str] = mapped_column(String(20))
    tender_support_count: Mapped[int] = mapped_column(Integer)
    bid_support_count: Mapped[int] = mapped_column(Integer)
    search_source_count: Mapped[int] = mapped_column(Integer)
    rule_document_count: Mapped[int] = mapped_column(Integer)
    absence_kind: Mapped[str | None] = mapped_column(String(30))
    absence_coverage: Mapped[str | None] = mapped_column(String(30))
    absence_method: Mapped[str | None] = mapped_column(String(30))
    absence_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    limitation_count: Mapped[int] = mapped_column(Integer)
    details_encrypted: Mapped[str] = mapped_column(Text)
    details_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        *common(),
        run_fk(),
        UniqueConstraint("org_id", "task_id", "submission_id", "review_id", "id"),
        UniqueConstraint("org_id", "review_id", "ordinal"),
        fk(
            ["task_id", "submission_id", "review_id", "obligation_id"],
            "bid_review_obligations",
            ["task_id", "submission_id", "review_id", "id"],
        ),
        CheckConstraint(
            "tender_support_count BETWEEN 1 AND 20 AND bid_support_count BETWEEN 0 AND 20 AND search_source_count BETWEEN 0 AND 1000 AND rule_document_count BETWEEN 0 AND 19 AND limitation_count BETWEEN 0 AND 100"
        ),
        CheckConstraint(
            "(absence_kind IS NULL AND absence_coverage IS NULL AND absence_method IS NULL AND absence_manifest_sha256 IS NULL AND search_source_count=0 AND (bid_support_count>0 OR rule_document_count>0)) OR (absence_kind='locations' AND absence_coverage IN ('required_locations','all_bid_pages','partial') AND absence_method IN ('local_text','local_pdf','ocr','llm','human') AND absence_manifest_sha256 IS NULL AND (search_source_count>0 OR (outcome='unknown' AND absence_coverage='partial'))) OR (absence_kind='submission_inventory' AND absence_coverage IN ('complete_inventory','partial') AND absence_method IN ('manifest_rule','llm_mapping','human') AND absence_manifest_sha256 ~ '^[0-9a-f]{64}$' AND search_source_count BETWEEN 1 AND 19)"
        ),
        CheckConstraint(
            "(absence_coverage IS DISTINCT FROM 'partial' OR (outcome='unknown' AND limitation_count>0)) AND (outcome<>'unknown' OR limitation_count>0)"
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 10000 AND code ~ '^[a-z][a-z0-9_]{0,99}$'"),
        CheckConstraint(
            "outcome IN ('responded','deviation','missing','unknown') AND severity IN ('fatal','high','medium') AND impact IN ('rejection','lost_points','both','uncertain')"
        ),
        CheckConstraint(
            "(outcome<>'responded' OR bid_support_count>0) AND (outcome<>'deviation' OR bid_support_count>0 OR rule_document_count>0) AND (outcome NOT IN ('missing','unknown') OR absence_kind IS NOT NULL) AND (outcome<>'missing' OR bid_support_count=0) AND (rule_document_count=0 OR (code='signature_validation_invalid' AND outcome='deviation'))"
        ),
        CheckConstraint("length(details_encrypted)>0 AND details_sha256 ~ '^[0-9a-f]{64}$'"),
    )


def finding_fk():
    return fk(
        ["task_id", "submission_id", "review_id", "finding_id"],
        "bid_review_findings",
        ["task_id", "submission_id", "review_id", "id"],
    )


class BidReviewFindingSource(Tenant, Base):
    __tablename__ = "bid_review_finding_sources"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    finding_id: Mapped[UUID] = mapped_column()
    preparation_id: Mapped[UUID] = mapped_column()
    validation_id: Mapped[UUID | None] = mapped_column()
    document_id: Mapped[UUID] = mapped_column()
    page_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(30))
    ordinal: Mapped[int] = mapped_column(Integer)
    quote_sha256: Mapped[str | None] = mapped_column(String(64))
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        *common(),
        finding_fk(),
        fk(
            ["task_id", "submission_id", "preparation_id", "document_id", "validation_id"],
            "bid_pdf_validations",
            ["task_id", "submission_id", "preparation_id", "document_id", "id"],
        ),
        CheckConstraint("(kind='rule_document')=(validation_id IS NOT NULL)"),
        fk(
            ["task_id", "submission_id", "document_id"],
            "bid_submission_documents",
            ["task_id", "submission_id", "id"],
        ),
        fk(
            ["task_id", "submission_id", "page_id"],
            "bid_document_pages",
            ["task_id", "submission_id", "id"],
        ),
        UniqueConstraint("org_id", "finding_id", "kind", "ordinal"),
        Index(
            "bid_review_absence_page_unique",
            "org_id",
            "finding_id",
            "page_id",
            unique=True,
            postgresql_where=text("kind='absence_page'"),
        ),
        Index(
            "bid_review_inventory_source_unique",
            "org_id",
            "finding_id",
            "kind",
            "document_id",
            unique=True,
            postgresql_where=text("kind IN ('inventory_document','rule_document')"),
        ),
        CheckConstraint(
            "kind IN ('tender_support','bid_support','absence_page','inventory_document','rule_document') AND ordinal BETWEEN 1 AND 1000"
        ),
        CheckConstraint("(kind IN ('inventory_document','rule_document'))=(page_id IS NULL)"),
        CheckConstraint("(kind IN ('tender_support','bid_support'))=(quote_sha256 IS NOT NULL)"),
        CheckConstraint("quote_sha256 IS NULL OR quote_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint(
            "(quote_sha256 IS NULL AND start_offset IS NULL AND end_offset IS NULL) OR (quote_sha256 IS NOT NULL AND start_offset IS NOT NULL AND end_offset IS NOT NULL AND start_offset>=0 AND end_offset>start_offset)"
        ),
    )


class BidReviewFindingEvent(Tenant, Base):
    __tablename__ = "bid_review_finding_events"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    finding_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))
    prior_decision_id: Mapped[UUID | None] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    review_domain: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    expected_input_hash: Mapped[str] = mapped_column(String(64))
    reason_sha256: Mapped[str] = mapped_column(String(64))
    reason_encrypted: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        *common(),
        finding_fk(),
        UniqueConstraint("org_id", "task_id", "submission_id", "review_id", "finding_id", "id"),
        UniqueConstraint("org_id", "finding_id", "revision"),
        UniqueConstraint("org_id", "decided_by", "request_id"),
        fk(["decided_by"], "memberships", ["user_id"]),
        fk(
            ["task_id", "submission_id", "review_id", "finding_id", "prior_decision_id"],
            "bid_review_finding_events",
            ["task_id", "submission_id", "review_id", "finding_id", "id"],
        ),
        CheckConstraint(
            "revision BETWEEN 2 AND 100000 AND action IN ('classify','dismiss','reopen','confirm') AND review_domain IN ('commercial','technical') AND state IN ('open','dismissed','confirmed')"
        ),
        CheckConstraint(
            "expected_input_hash ~ '^[0-9a-f]{64}$' AND reason_sha256 ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}$' AND length(reason_encrypted)>0"
        ),
    )
