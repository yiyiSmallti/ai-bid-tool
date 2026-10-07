"""Immutable attachment bytes, exact declaration pins and human review history."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, Tenant


def fk(columns, table, targets=None, **kwargs):
    return ForeignKeyConstraint(
        ["org_id", *columns], [f"{table}.{c}" for c in ["org_id", *(targets or columns)]], **kwargs
    )


def request_constraints():
    return (CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'"),)


class Request:
    request_id: Mapped[UUID] = mapped_column()
    payload_hash: Mapped[str] = mapped_column(String(64))


class AttachmentArchive(Tenant, Base):
    __tablename__ = "attachment_archives"
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    custodian_user_id: Mapped[UUID] = mapped_column()
    reviewer_user_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        fk(["custodian_user_id"], "memberships", ["user_id"]),
        fk(["reviewer_user_id"], "memberships", ["user_id"]),
        fk(["created_by"], "memberships", ["user_id"]),
        fk(
            ["id", "current_revision"],
            "attachment_revisions",
            ["attachment_id", "revision"],
            name="attachment_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("current_revision > 0 AND state_version > 0"),
        Index("attachment_archive_page", "org_id", "created_at", "id"),
    )


class AttachmentRevision(Request, Tenant, Base):
    __tablename__ = "attachment_revisions"
    attachment_id: Mapped[UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    label_encrypted: Mapped[str] = mapped_column(Text)
    metadata_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "attachment_id", "id"),
        UniqueConstraint("org_id", "attachment_id", "revision"),
        fk(["attachment_id"], "attachment_archives", ["id"]),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "revision > 0 AND metadata_sha256 ~ '^[0-9a-f]{64}$' AND length(label_encrypted)>0"
        ),
        CheckConstraint(
            "kind IN ('business_licence','qualification_scan','contract','performance_record')"
        ),
        *request_constraints(),
        Index("attachment_revision_page", "org_id", "attachment_id", "created_at", "id"),
    )


class AttachmentFile(Tenant, Base):
    __tablename__ = "attachment_files"
    attachment_id: Mapped[UUID] = mapped_column()
    attachment_revision_id: Mapped[UUID] = mapped_column()
    created_by: Mapped[UUID] = mapped_column()
    file: Mapped[dict] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(400))
    upload_name_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "id", "attachment_id", "attachment_revision_id"),
        UniqueConstraint("org_id", "attachment_revision_id"),
        fk(
            ["attachment_id", "attachment_revision_id"],
            "attachment_revisions",
            ["attachment_id", "id"],
        ),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "jsonb_typeof(file)='object' AND file ?& ARRAY['name','sha256','size_bytes','page_count','media_type'] AND jsonb_typeof(file->'media_type')='string' AND file->>'media_type'='application/pdf' AND jsonb_typeof(file->'sha256')='string' AND file->>'sha256' ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint(
            "jsonb_typeof(file->'size_bytes')='number' AND (file->>'size_bytes')::numeric BETWEEN 1 AND 41943040 AND (file->>'size_bytes')::numeric=trunc((file->>'size_bytes')::numeric) AND jsonb_typeof(file->'page_count')='number' AND (file->>'page_count')::numeric BETWEEN 1 AND 200 AND (file->>'page_count')::numeric=trunc((file->>'page_count')::numeric)"
        ),
        CheckConstraint(
            "jsonb_typeof(file->'name')='string' AND length(btrim(file->>'name')) BETWEEN 1 AND 200 AND position('/' in file->>'name')=0 AND position(chr(92) in file->>'name')=0 AND file->>'name' !~ '[[:cntrl:]]' AND right(file->>'name',4)='.pdf' AND length(upload_name_encrypted)>0"
        ),
        CheckConstraint(
            "storage_key='org/' || org_id::text || '/attachment/' || attachment_id::text || '/' || attachment_revision_id::text || '/' || (file->>'sha256') || '.pdf'"
        ),
    )


class AttachmentFilePart(Tenant, Base):
    __tablename__ = "attachment_file_parts"
    attachment_id: Mapped[UUID] = mapped_column()
    attachment_revision_id: Mapped[UUID] = mapped_column()
    attachment_file_id: Mapped[UUID] = mapped_column()
    ordinal: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_start: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(400))
    upload_name_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "attachment_file_id", "ordinal"),
        fk(
            ["attachment_file_id", "attachment_id", "attachment_revision_id"],
            "attachment_files",
            ["id", "attachment_id", "attachment_revision_id"],
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 20 AND rotation IN (0,90,180,270)"),
        CheckConstraint(
            "media_type IN ('application/pdf','image/png','image/jpeg') AND sha256 ~ '^[0-9a-f]{64}$' AND size_bytes BETWEEN 1 AND 41943040"
        ),
        CheckConstraint(
            "page_start BETWEEN 1 AND 200 AND page_count BETWEEN 1 AND 200 AND page_start+page_count-1<=200"
        ),
        CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 200 AND position('/' in name)=0 AND position(chr(92) in name)=0 AND name !~ '[[:cntrl:]]' AND length(upload_name_encrypted)>0"
        ),
        CheckConstraint(
            "storage_key='org/' || org_id::text || '/attachment/' || attachment_id::text || '/' || attachment_revision_id::text || '/parts/' || ordinal::text || '/' || sha256 || CASE media_type WHEN 'application/pdf' THEN '.pdf' WHEN 'image/png' THEN '.png' ELSE '.jpg' END"
        ),
    )


class AttachmentReview(Request, Tenant, Base):
    __tablename__ = "attachment_reviews"
    attachment_id: Mapped[UUID] = mapped_column()
    attachment_revision_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    original_sha256: Mapped[str] = mapped_column(String(64))
    metadata_sha256: Mapped[str] = mapped_column(String(64))
    prior_review_id: Mapped[UUID | None] = mapped_column()
    decision: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(40))
    reviewed_by: Mapped[UUID] = mapped_column()
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "attachment_revision_id", "id"),
        UniqueConstraint("org_id", "attachment_id", "attachment_revision_id", "file_id", "id"),
        fk(
            ["file_id", "attachment_id", "attachment_revision_id"],
            "attachment_files",
            ["id", "attachment_id", "attachment_revision_id"],
        ),
        fk(
            ["attachment_revision_id", "prior_review_id"],
            "attachment_reviews",
            ["attachment_revision_id", "id"],
        ),
        fk(["reviewed_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "decision IN ('approve','reject','revoke') AND (decision='approve')=(reason='accepted_for_internal_use') AND (decision<>'revoke' OR prior_review_id IS NOT NULL)"
        ),
        CheckConstraint(
            "reason IN ('accepted_for_internal_use','wrong_document','unreadable_document','metadata_mismatch','privacy_concern','superseded','withdrawn_by_org','reviewer_unavailable','responsibility_changed','declaration_changed','selection_replaced')"
        ),
        CheckConstraint(
            "original_sha256 ~ '^[0-9a-f]{64}$' AND metadata_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        *request_constraints(),
        Index(
            "attachment_review_prior",
            "org_id",
            "attachment_revision_id",
            "prior_review_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("attachment_review_page", "org_id", "attachment_revision_id", "created_at", "id"),
    )


class ProfileAttachmentLink(Request, Tenant, Base):
    __tablename__ = "profile_attachment_links"
    profile_id: Mapped[UUID] = mapped_column()
    profile_revision_id: Mapped[UUID] = mapped_column()
    field: Mapped[str] = mapped_column(String(30))
    attachment_id: Mapped[UUID] = mapped_column()
    attachment_revision_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    approval_id: Mapped[UUID] = mapped_column()
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "id",
            "profile_revision_id",
            "field",
            "attachment_id",
            "attachment_revision_id",
            "file_id",
            "approval_id",
            name="attachment_link_binding",
        ),
        fk(["profile_id", "profile_revision_id"], "org_profile_revisions", ["profile_id", "id"]),
        fk(
            ["attachment_id", "attachment_revision_id", "file_id", "approval_id"],
            "attachment_reviews",
            ["attachment_id", "attachment_revision_id", "file_id", "id"],
        ),
        fk(["created_by"], "memberships", ["user_id"]),
        CheckConstraint(
            "field IN ('registration_details','performance_summary','standard_wording') AND state_version>0"
        ),
        *request_constraints(),
        Index(
            "attachment_link_active_slot",
            "org_id",
            "profile_revision_id",
            "field",
            "attachment_revision_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("attachment_link_page", "org_id", "profile_revision_id", "created_at", "id"),
    )


class TaskAttachment(Request, Tenant, Base):
    __tablename__ = "task_attachments"
    task_id: Mapped[UUID] = mapped_column()
    task_org_profile_id: Mapped[UUID] = mapped_column()
    profile_revision_id: Mapped[UUID] = mapped_column()
    profile_attachment_link_id: Mapped[UUID] = mapped_column()
    field: Mapped[str] = mapped_column(String(30))
    attachment_id: Mapped[UUID] = mapped_column()
    attachment_revision_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[UUID] = mapped_column()
    approval_id: Mapped[UUID] = mapped_column()
    original_sha256: Mapped[str] = mapped_column(String(64))
    lot: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    selected_by: Mapped[UUID] = mapped_column()
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "id"),
        UniqueConstraint(
            "org_id",
            "id",
            "task_id",
            "task_org_profile_id",
            "profile_revision_id",
            "profile_attachment_link_id",
            "attachment_id",
            "attachment_revision_id",
            "file_id",
            "approval_id",
            name="attachment_selection_source_binding",
        ),
        fk(["task_id"], "tasks", ["id"]),
        fk(
            ["task_org_profile_id", "task_id", "profile_revision_id", "lot"],
            "task_org_profiles",
            ["id", "task_id", "profile_revision_id", "lot"],
        ),
        fk(
            [
                "profile_attachment_link_id",
                "profile_revision_id",
                "field",
                "attachment_id",
                "attachment_revision_id",
                "file_id",
                "approval_id",
            ],
            "profile_attachment_links",
            [
                "id",
                "profile_revision_id",
                "field",
                "attachment_id",
                "attachment_revision_id",
                "file_id",
                "approval_id",
            ],
        ),
        fk(["selected_by"], "memberships", ["user_id"]),
        CheckConstraint("original_sha256 ~ '^[0-9a-f]{64}$' AND state_version>0"),
        *request_constraints(),
        Index("attachment_selection_archive_tasks", "org_id", "attachment_id", "task_id"),
        Index("attachment_selection_link_tasks", "org_id", "profile_attachment_link_id", "task_id"),
        Index(
            "attachment_selection_active_slot",
            "org_id",
            "task_id",
            "task_org_profile_id",
            "attachment_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("attachment_selection_page", "org_id", "task_id", "created_at", "id"),
    )


class AttachmentPrivacyHold(Request, Tenant, Base):
    __tablename__ = "attachment_privacy_holds"
    task_id: Mapped[UUID] = mapped_column()
    evidence_source_id: Mapped[UUID] = mapped_column()
    source_png_sha256: Mapped[str] = mapped_column(String(64))
    prior_hold_id: Mapped[UUID | None] = mapped_column()
    reviewed_by: Mapped[UUID] = mapped_column()
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(30), default="sensitive_content")
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "task_id", "evidence_source_id", "id"),
        fk(["task_id", "evidence_source_id"], "evidence_sources", ["task_id", "id"]),
        fk(
            ["task_id", "evidence_source_id", "prior_hold_id"],
            "attachment_privacy_holds",
            ["task_id", "evidence_source_id", "id"],
        ),
        fk(["reviewed_by"], "memberships", ["user_id"]),
        CheckConstraint("reason='sensitive_content' AND source_png_sha256 ~ '^[0-9a-f]{64}$'"),
        *request_constraints(),
        Index(
            "attachment_hold_prior",
            "org_id",
            "evidence_source_id",
            "prior_hold_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "attachment_hold_latest",
            "org_id",
            "evidence_source_id",
            reviewed_at.desc(),
            text("id DESC"),
        ),
        Index("attachment_hold_page", "org_id", "evidence_source_id", "created_at", "id"),
    )
