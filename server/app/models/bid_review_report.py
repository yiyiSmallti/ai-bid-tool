from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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


class BidReviewReportArtifact(Base):
    __tablename__ = "bid_review_report_artifacts"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((format)::text = ANY ((ARRAY['docx'::character varying, 'console'::character varying])::text[])) AND ((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 104857600))))",
            name="bid_review_report_artifacts_check",
        ),
        CheckConstraint(
            "((storage_key ~~ ((((('bid-review/'::text) || (submission_id)::text) || '/reports/'::text) || (snapshot_id)::text) || '/%'::text)))",
            name="bid_review_report_artifacts_check1",
        ),
        UniqueConstraint(
            "snapshot_id", "format", name="bid_review_report_artifacts_snapshot_id_format_key"
        ),
        UniqueConstraint("task_id", "id", name="bid_review_report_artifacts_task_id_id_key"),
        PrimaryKeyConstraint("id", name="bid_review_report_artifacts_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_report_artifacts_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id", "snapshot_id"],
            [
                "bid_review_report_snapshots.task_id",
                "bid_review_report_snapshots.submission_id",
                "bid_review_report_snapshots.review_id",
                "bid_review_report_snapshots.id",
            ],
            name="bid_review_report_artifacts_task_id_submission_id_r_fkey",
        ),
    )


class BidReviewReportSnapshot(Base):
    __tablename__ = "bid_review_report_snapshots"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(nullable=False)
    publication_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decisions_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer_identity: Mapped[str] = mapped_column(Text, nullable=False)
    details_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((report_input_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((decisions_snapshot_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((details_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="bid_review_report_snapshots_check",
        ),
        CheckConstraint(
            "(((length(details_encrypted) > 0) AND (length(renderer_identity) > 0)))",
            name="bid_review_report_snapshots_check1",
        ),
        UniqueConstraint(
            "request_id", name="bid_review_report_snapshots_created_by_request_id_key"
        ),
        UniqueConstraint("job_id", name="bid_review_report_snapshots_job_id_key"),
        UniqueConstraint("task_id", "id", name="bid_review_report_snapshots_task_id_id_key"),
        UniqueConstraint(
            "task_id",
            "submission_id",
            "review_id",
            "id",
            name="bid_review_report_snapshots_task_id_submission_id_re_key",
        ),
        PrimaryKeyConstraint("id", name="bid_review_report_snapshots_pkey"),
        ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="bid_review_report_snapshots_task_id_fkey"
        ),
        ForeignKeyConstraint(
            ["task_id", "job_id"],
            ["jobs.task_id", "jobs.id"],
            name="bid_review_report_snapshots_task_id_job_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "publication_id"],
            ["bid_review_publications.task_id", "bid_review_publications.id"],
            name="bid_review_report_snapshots_task_id_publication_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_id", "submission_id", "review_id"],
            ["bid_review_runs.task_id", "bid_review_runs.submission_id", "bid_review_runs.id"],
            name="bid_review_report_snapshots_task_id_submission_id_r_fkey",
        ),
    )
