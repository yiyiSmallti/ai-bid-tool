from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    column,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class AttachmentArchive(Base):
    __tablename__ = "attachment_archives"
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((current_revision > 0) AND (state_version > 0)))", name="attachment_archives_check"
        ),
        PrimaryKeyConstraint("id", name="attachment_archives_pkey"),
        ForeignKeyConstraint(
            ["id", "current_revision"],
            ["attachment_revisions.attachment_id", "attachment_revisions.revision"],
            name="attachment_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("attachment_archive_page", "created_at", "id"),
    )


class AttachmentFilePart(Base):
    __tablename__ = "attachment_file_parts"
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_file_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    media_type: Mapped[str] = mapped_column(String(40), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rotation: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    upload_name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((ordinal >= 1) AND (ordinal <= 20)) AND (rotation = ANY (ARRAY[0, 90, 180, 270]))))",
            name="attachment_file_parts_check",
        ),
        CheckConstraint(
            "((((media_type)::text = ANY ((ARRAY['application/pdf'::character varying, 'image/png'::character varying, 'image/jpeg'::character varying])::text[])) AND ((sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 41943040))))",
            name="attachment_file_parts_check1",
        ),
        CheckConstraint(
            "((((page_start >= 1) AND (page_start <= 200)) AND ((page_count >= 1) AND (page_count <= 200)) AND (((page_start + page_count) - 1) <= 200)))",
            name="attachment_file_parts_check2",
        ),
        CheckConstraint(
            "((((length(btrim((name)::text)) >= 1) AND (length(btrim((name)::text)) <= 200)) AND (POSITION(('/'::text) IN (name)) = 0) AND (POSITION((chr(92)) IN (name)) = 0) AND ((name)::text !~ '[[:cntrl:]]'::text) AND (length(upload_name_encrypted) > 0)))",
            name="attachment_file_parts_check3",
        ),
        CheckConstraint(
            "(((storage_key)::text = ((((((((('attachment/'::text) || (attachment_id)::text) || '/'::text) || (attachment_revision_id)::text) || '/parts/'::text) || (ordinal)::text) || '/'::text) || (sha256)::text) || CASE media_type WHEN 'application/pdf'::text THEN '.pdf'::text WHEN 'image/png'::text THEN '.png'::text ELSE '.jpg'::text END)))",
            name="attachment_file_parts_check4",
        ),
        UniqueConstraint(
            "attachment_file_id",
            "ordinal",
            name="attachment_file_parts_attachment_file_id_ordinal_key",
        ),
        PrimaryKeyConstraint("id", name="attachment_file_parts_pkey"),
        ForeignKeyConstraint(
            ["attachment_file_id", "attachment_id", "attachment_revision_id"],
            [
                "attachment_files.id",
                "attachment_files.attachment_id",
                "attachment_files.attachment_revision_id",
            ],
            name="attachment_file_parts_attachment_file_id_attachment_fkey",
        ),
    )


class AttachmentFile(Base):
    __tablename__ = "attachment_files"
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    file: Mapped[dict] = mapped_column(JSONB, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    upload_name_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((jsonb_typeof((file -> 'name'::text)) = 'string'::text) AND ((length(btrim((file ->> 'name'::text))) >= 1) AND (length(btrim((file ->> 'name'::text))) <= 200)) AND (POSITION(('/'::text) IN ((file ->> 'name'::text))) = 0) AND (POSITION((chr(92)) IN ((file ->> 'name'::text))) = 0) AND ((file ->> 'name'::text) !~ '[[:cntrl:]]'::text) AND (\"right\"((file ->> 'name'::text), 4) = '.pdf'::text) AND (length(upload_name_encrypted) > 0)))",
            name="attachment_files_check",
        ),
        CheckConstraint(
            "(((storage_key)::text = ((((((('attachment/'::text) || (attachment_id)::text) || '/'::text) || (attachment_revision_id)::text) || '/'::text) || (file ->> 'sha256'::text)) || '.pdf'::text)))",
            name="attachment_files_check1",
        ),
        CheckConstraint(
            "(((jsonb_typeof(file) = 'object'::text) AND (file ?& ARRAY['name'::text, 'sha256'::text, 'size_bytes'::text, 'page_count'::text, 'media_type'::text]) AND (jsonb_typeof((file -> 'media_type'::text)) = 'string'::text) AND ((file ->> 'media_type'::text) = 'application/pdf'::text) AND (jsonb_typeof((file -> 'sha256'::text)) = 'string'::text) AND ((file ->> 'sha256'::text) ~ '^[0-9a-f]{64}$'::text)))",
            name="attachment_files_file_check",
        ),
        CheckConstraint(
            "(((jsonb_typeof((file -> 'size_bytes'::text)) = 'number'::text) AND ((((file ->> 'size_bytes'::text))::numeric >= (1)::numeric) AND (((file ->> 'size_bytes'::text))::numeric <= (41943040)::numeric)) AND (((file ->> 'size_bytes'::text))::numeric = trunc(((file ->> 'size_bytes'::text))::numeric)) AND (jsonb_typeof((file -> 'page_count'::text)) = 'number'::text) AND ((((file ->> 'page_count'::text))::numeric >= (1)::numeric) AND (((file ->> 'page_count'::text))::numeric <= (200)::numeric)) AND (((file ->> 'page_count'::text))::numeric = trunc(((file ->> 'page_count'::text))::numeric))))",
            name="attachment_files_file_check1",
        ),
        UniqueConstraint(
            "attachment_revision_id", name="attachment_files_attachment_revision_id_key"
        ),
        UniqueConstraint(
            "id",
            "attachment_id",
            "attachment_revision_id",
            name="attachment_files_id_attachment_id_attachment_revisio_key",
        ),
        PrimaryKeyConstraint("id", name="attachment_files_pkey"),
        ForeignKeyConstraint(
            ["attachment_id", "attachment_revision_id"],
            ["attachment_revisions.attachment_id", "attachment_revisions.id"],
            name="attachment_files_attachment_id_attachment_revision__fkey",
        ),
    )


class AttachmentPrivacyHold(Base):
    __tablename__ = "attachment_privacy_holds"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    evidence_source_id: Mapped[UUID] = mapped_column(nullable=False)
    source_png_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_hold_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False, default="sensitive_content")
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((reason)::text = 'sensitive_content'::text) AND ((source_png_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="attachment_privacy_holds_check",
        ),
        CheckConstraint(
            "(((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="attachment_privacy_holds_payload_hash_check",
        ),
        UniqueConstraint(
            "task_id",
            "evidence_source_id",
            "id",
            name="attachment_privacy_holds_task_id_evidence_source_id__key",
        ),
        PrimaryKeyConstraint("id", name="attachment_privacy_holds_pkey"),
        ForeignKeyConstraint(
            ["task_id", "evidence_source_id", "prior_hold_id"],
            [
                "attachment_privacy_holds.task_id",
                "attachment_privacy_holds.evidence_source_id",
                "attachment_privacy_holds.id",
            ],
            name="attachment_privacy_holds_task_id_evidence_source_i_fkey1",
        ),
        ForeignKeyConstraint(
            ["task_id", "evidence_source_id"],
            ["evidence_sources.task_id", "evidence_sources.id"],
            name="attachment_privacy_holds_task_id_evidence_source_id_fkey",
        ),
        Index(
            "attachment_hold_latest",
            "evidence_source_id",
            column("reviewed_at").desc(),
            column("id").desc(),
        ),
        Index("attachment_hold_page", "evidence_source_id", "created_at", "id"),
        Index(
            "attachment_hold_prior",
            "evidence_source_id",
            "prior_hold_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class AttachmentReview(Base):
    __tablename__ = "attachment_reviews"
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prior_review_id: Mapped[UUID | None] = mapped_column(nullable=True)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((decision)::text = ANY ((ARRAY['approve'::character varying, 'reject'::character varying, 'revoke'::character varying])::text[])) AND (((decision)::text = 'approve'::text) = ((reason)::text = 'accepted_for_internal_use'::text)) AND (((decision)::text <> 'revoke'::text) OR (prior_review_id IS NOT NULL))))",
            name="attachment_reviews_check",
        ),
        CheckConstraint(
            "((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((metadata_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="attachment_reviews_check1",
        ),
        CheckConstraint(
            "(((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="attachment_reviews_payload_hash_check",
        ),
        CheckConstraint(
            "(((reason)::text = ANY ((ARRAY['accepted_for_internal_use'::character varying, 'wrong_document'::character varying, 'unreadable_document'::character varying, 'metadata_mismatch'::character varying, 'privacy_concern'::character varying, 'superseded'::character varying, 'withdrawn_by_org'::character varying, 'reviewer_unavailable'::character varying, 'responsibility_changed'::character varying, 'declaration_changed'::character varying, 'selection_replaced'::character varying])::text[])))",
            name="attachment_reviews_reason_check",
        ),
        UniqueConstraint(
            "attachment_id",
            "attachment_revision_id",
            "file_id",
            "id",
            name="attachment_reviews_attachment_id_attachment_revision_key",
        ),
        UniqueConstraint(
            "attachment_revision_id", "id", name="attachment_reviews_attachment_revision_id_id_key"
        ),
        PrimaryKeyConstraint("id", name="attachment_reviews_pkey"),
        ForeignKeyConstraint(
            ["attachment_revision_id", "prior_review_id"],
            ["attachment_reviews.attachment_revision_id", "attachment_reviews.id"],
            name="attachment_reviews_attachment_revision_id_prior_rev_fkey",
        ),
        ForeignKeyConstraint(
            ["file_id", "attachment_id", "attachment_revision_id"],
            [
                "attachment_files.id",
                "attachment_files.attachment_id",
                "attachment_files.attachment_revision_id",
            ],
            name="attachment_reviews_file_id_attachment_id_attachment_fkey",
        ),
        Index(
            "attachment_review_latest",
            "attachment_revision_id",
            column("reviewed_at").desc(),
            column("id").desc(),
        ),
        Index("attachment_review_page", "attachment_revision_id", "created_at", "id"),
        Index(
            "attachment_review_prior",
            "attachment_revision_id",
            "prior_review_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class AttachmentRevision(Base):
    __tablename__ = "attachment_revisions"
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    label_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((revision > 0) AND ((metadata_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (length(label_encrypted) > 0)))",
            name="attachment_revisions_check",
        ),
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['business_licence'::character varying, 'qualification_scan'::character varying, 'contract'::character varying, 'performance_record'::character varying])::text[])))",
            name="attachment_revisions_kind_check",
        ),
        CheckConstraint(
            "(((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="attachment_revisions_payload_hash_check",
        ),
        UniqueConstraint("attachment_id", "id", name="attachment_revisions_attachment_id_id_key"),
        UniqueConstraint(
            "attachment_id", "revision", name="attachment_revisions_attachment_id_revision_key"
        ),
        PrimaryKeyConstraint("id", name="attachment_revisions_pkey"),
        ForeignKeyConstraint(
            ["attachment_id"],
            ["attachment_archives.id"],
            name="attachment_revisions_attachment_id_fkey",
        ),
        Index("attachment_revision_page", "attachment_id", "created_at", "id"),
    )


class ProfileAttachmentLink(Base):
    __tablename__ = "profile_attachment_links"
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_id: Mapped[UUID] = mapped_column(nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((field)::text = ANY ((ARRAY['registration_details'::character varying, 'performance_summary'::character varying, 'standard_wording'::character varying])::text[])) AND (state_version > 0)))",
            name="profile_attachment_links_check",
        ),
        CheckConstraint(
            "(((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="profile_attachment_links_payload_hash_check",
        ),
        UniqueConstraint(
            "id",
            "profile_revision_id",
            "field",
            "attachment_id",
            "attachment_revision_id",
            "file_id",
            "approval_id",
            name="attachment_link_binding",
        ),
        PrimaryKeyConstraint("id", name="profile_attachment_links_pkey"),
        ForeignKeyConstraint(
            ["attachment_id", "attachment_revision_id", "file_id", "approval_id"],
            [
                "attachment_reviews.attachment_id",
                "attachment_reviews.attachment_revision_id",
                "attachment_reviews.file_id",
                "attachment_reviews.id",
            ],
            name="profile_attachment_links_attachment_id_attachment_r_fkey",
        ),
        ForeignKeyConstraint(
            ["profile_id", "profile_revision_id"],
            ["org_profile_revisions.profile_id", "org_profile_revisions.id"],
            name="profile_attachment_links_profile_id_profile_revisio_fkey",
        ),
        Index(
            "attachment_link_active_slot",
            "profile_revision_id",
            "field",
            "attachment_revision_id",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
        Index("attachment_link_page", "profile_revision_id", "created_at", "id"),
    )


class TaskAttachment(Base):
    __tablename__ = "task_attachments"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    task_org_profile_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_attachment_link_id: Mapped[UUID] = mapped_column(nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(nullable=False)
    attachment_revision_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_id: Mapped[UUID] = mapped_column(nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lot: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((original_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND (state_version > 0)))",
            name="task_attachments_check",
        ),
        CheckConstraint(
            "(((payload_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="task_attachments_payload_hash_check",
        ),
        UniqueConstraint(
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
        UniqueConstraint("task_id", "id", name="task_attachments_task_id_id_key"),
        PrimaryKeyConstraint("id", name="task_attachments_pkey"),
        ForeignKeyConstraint(
            [
                "profile_attachment_link_id",
                "profile_revision_id",
                "field",
                "attachment_id",
                "attachment_revision_id",
                "file_id",
                "approval_id",
            ],
            [
                "profile_attachment_links.id",
                "profile_attachment_links.profile_revision_id",
                "profile_attachment_links.field",
                "profile_attachment_links.attachment_id",
                "profile_attachment_links.attachment_revision_id",
                "profile_attachment_links.file_id",
                "profile_attachment_links.approval_id",
            ],
            name="task_attachments_profile_attachment_link_id_profile_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="task_attachments_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_org_profile_id", "task_id", "profile_revision_id", "lot"],
            [
                "task_org_profiles.id",
                "task_org_profiles.task_id",
                "task_org_profiles.profile_revision_id",
                "task_org_profiles.lot",
            ],
            name="task_attachments_task_org_profile_id_task_id_profil_fkey",
        ),
        Index(
            "attachment_selection_active_slot",
            "task_id",
            "task_org_profile_id",
            "attachment_id",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
        Index("attachment_selection_archive_tasks", "attachment_id", "task_id"),
        Index("attachment_selection_link_tasks", "profile_attachment_link_id", "task_id"),
        Index("attachment_selection_page", "task_id", "created_at", "id"),
    )
