"""Encrypted immutable report snapshots and atomically published artifact pairs."""

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.bid_review import common, fk
from app.models.bid_review_run import run_fk
from app.models.entities import Base, Tenant


class BidReviewReportSnapshot(Tenant, Base):
    __tablename__ = "bid_review_report_snapshots"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    publication_id: Mapped[UUID] = mapped_column()
    job_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    report_input_hash: Mapped[str] = mapped_column(String(64))
    decisions_snapshot_sha256: Mapped[str] = mapped_column(String(64))
    renderer_identity: Mapped[str] = mapped_column(Text)
    details_sha256: Mapped[str] = mapped_column(String(64))
    details_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        run_fk(),
        fk(["task_id", "job_id"], "jobs", ["task_id", "id"]),
        fk(["task_id", "publication_id"], "bid_review_publications", ["task_id", "id"]),
        fk(["created_by"], "memberships", ["user_id"]),
        UniqueConstraint("org_id", "job_id"),
        UniqueConstraint("org_id", "created_by", "request_id"),
        UniqueConstraint("org_id", "task_id", "submission_id", "review_id", "id"),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND report_input_hash ~ '^[0-9a-f]{64}$' AND decisions_snapshot_sha256 ~ '^[0-9a-f]{64}$' AND details_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint("length(details_encrypted)>0 AND length(renderer_identity)>0"),
    )


class BidReviewReportArtifact(Tenant, Base):
    __tablename__ = "bid_review_report_artifacts"
    task_id: Mapped[UUID] = mapped_column()
    submission_id: Mapped[UUID] = mapped_column()
    review_id: Mapped[UUID] = mapped_column()
    snapshot_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID] = mapped_column()
    format: Mapped[str] = mapped_column(String(10))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        *common(),
        fk(
            ["task_id", "submission_id", "review_id", "snapshot_id"],
            "bid_review_report_snapshots",
            ["task_id", "submission_id", "review_id", "id"],
        ),
        UniqueConstraint("org_id", "snapshot_id", "format"),
        CheckConstraint(
            "format IN ('docx','console') AND sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 104857600"
        ),
        CheckConstraint(
            "storage_key LIKE 'org/' || org_id::text || '/bid-review/' || submission_id::text || '/reports/' || snapshot_id::text || '/%'"
        ),
    )
