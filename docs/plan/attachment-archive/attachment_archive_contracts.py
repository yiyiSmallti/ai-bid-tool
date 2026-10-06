"""Pending approval, not implemented: org attachment archive contracts.

Review-only Pydantic v2 models and service protocols. No runtime registration,
database/storage writes, renderer execution, grants or confirmation occur here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal, Protocol
from uuid import UUID

from app.schemas.certificate_file_contracts import (
    MAX_PARTS,
    CertificatePart,
    CertificatePartOptions,
    CertificateScanFile,
)
from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Result
from app.schemas.evidence_source_contracts import EvidenceSourcePreview
from app.schemas.profile_contracts import OrgProfileRevision, TaskOrgProfileSnapshot
from app.schemas.screenshot_contracts import (
    ContentMapping,
    PixelRect,
    PNGDescriptor,
    RenditionView,
    Sha256,
)
from pydantic import AwareDatetime, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.providers.storage import Storage
    from app.services.auth import Identity
    from sqlalchemy.ext.asyncio import AsyncSession

HTTP_JSON_LIMIT = 128 * 1024
LIST_JSON_LIMIT = 1024 * 1024
FILE_BYTE_LIMIT = 40 * 1024 * 1024
PAGE_LIMIT = 200
PNG_PIXEL_LIMIT = 20_000_000

Revision = Annotated[int, Field(strict=True, ge=1)]
PageNumber = Annotated[int, Field(strict=True, ge=1, le=PAGE_LIMIT)]
AttachmentKind = Literal["business_licence", "qualification_scan", "contract", "performance_record"]
DeclarationField = Literal["registration_details", "performance_summary", "standard_wording"]
ReviewState = Literal["pending", "approved", "rejected", "revoked"]
ReasonCode = Literal[
    "accepted_for_internal_use",
    "wrong_document",
    "unreadable_document",
    "metadata_mismatch",
    "privacy_concern",
    "superseded",
    "withdrawn_by_org",
    "reviewer_unavailable",
    "responsibility_changed",
    "declaration_changed",
    "selection_replaced",
]
NextAction = Literal[
    "review_uploaded_file",
    "upload_corrected_revision",
    "assign_reviewer",
    "link_declaration",
    "select_for_task",
    "archive_page",
    "review_page_privacy",
    "redact_page",
    "replace_source",
    "annotate_page",
    "wait_for_annotation_enablement",
    "inspect_history",
]
AttachmentBlocker = Literal[
    "archive_inactive",
    "archive_review_pending",
    "archive_review_rejected",
    "archive_review_revoked",
    "reviewer_unavailable",
    "profile_link_missing",
    "profile_link_inactive",
    "profile_selection_changed",
    "attachment_selection_inactive",
    "task_archived",
    "source_not_archived",
    "source_integrity_failure",
    "privacy_pending",
    "needs_redaction",
    "privacy_withdrawn",
    "extraction_required",
    "annotation_adapter_not_enabled",
]

# Metadata read is the only proposed token scope. These are declarations for review,
# not changes to auth.SCOPES, task ceilings or database token constraints.
TOKEN_SCOPES = frozenset({"attachment:read"})
HUMAN_ONLY_SCOPES = frozenset(
    {
        "attachment:write",
        "attachment:review",
        "attachment:manage",
        "attachment:original:read",
        "attachment:page:read",
        "attachment:privacy",
        "task:attachment",
    }
)


class AttachmentMetadata(Contract):
    kind: AttachmentKind
    label: str = Field(min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def nonblank_label(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("a nonblank display label without control characters is required")
        return value


class AttachmentCreate(Contract):
    request_id: UUID
    metadata: AttachmentMetadata
    reviewer_user_id: UUID
    parts: list[CertificatePartOptions] = Field(default_factory=list, max_length=MAX_PARTS)
    dry_run: bool = False


class AttachmentRevise(AttachmentCreate):
    expected_state_version: Revision


class AttachmentAssignment(Contract):
    request_id: UUID
    expected_state_version: Revision
    custodian_user_id: UUID
    reviewer_user_id: UUID
    reason: Literal["reviewer_unavailable", "responsibility_changed"]


class AttachmentDeactivate(Contract):
    request_id: UUID
    expected_state_version: Revision
    reason: Literal[
        "superseded",
        "withdrawn_by_org",
        "privacy_concern",
        "declaration_changed",
        "selection_replaced",
    ]


class PageQuery(Contract):
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    history: bool = False


class AttachmentListQuery(PageQuery):
    kind: AttachmentKind | None = None


class AttachmentSourceListQuery(PageQuery):
    selection_id: UUID | None = None


class PageData(Contract):
    next_cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    history: bool = False


class AttachmentReadiness(Contract):
    blockers: list[AttachmentBlocker] = Field(default_factory=list, max_length=20)
    next_action: NextAction
    responsible_user_id: UUID | None
    can_annotate: bool = False
    eligible_for_draft_export: Literal[False] = False

    @model_validator(mode="after")
    def no_blocked_annotation(self):
        if self.can_annotate and (self.blockers or self.next_action != "annotate_page"):
            raise ValueError("annotation readiness requires no blockers and the annotation action")
        return self


class AttachmentSummary(Contract):
    """Safe metadata projection; no user-entered labels, names or document text."""

    id: UUID
    org_id: UUID
    current_revision_id: UUID
    current_revision: Revision
    state_version: Revision
    kind: AttachmentKind
    active: bool
    custodian_user_id: UUID
    reviewer_user_id: UUID
    review_state: ReviewState
    latest_review_id: UUID | None
    created_at: AwareDatetime
    readiness: AttachmentReadiness


class AttachmentShowData(Contract):
    attachment: AttachmentSummary


class AttachmentRevisionSummary(Contract):
    id: UUID
    org_id: UUID
    attachment_id: UUID
    revision: Revision
    file_id: UUID
    kind: AttachmentKind
    original_sha256: Sha256
    metadata_sha256: Sha256
    page_count: PageNumber
    size_bytes: int = Field(strict=True, gt=0, le=FILE_BYTE_LIMIT)
    review_state: ReviewState
    latest_review_id: UUID | None
    created_by: UUID
    created_at: AwareDatetime


class AttachmentRevisionView(AttachmentRevisionSummary):
    """Restricted original-reader details; descriptors use generated neutral names."""

    metadata: AttachmentMetadata
    original: CertificateScanFile
    parts: list[CertificatePart] = Field(default_factory=list, max_length=MAX_PARTS)

    @model_validator(mode="after")
    def exact_file_and_parts(self):
        if (
            self.kind != self.metadata.kind
            or self.original.sha256 != self.original_sha256
            or self.original.page_count != self.page_count
            or self.original.size_bytes != self.size_bytes
        ):
            raise ValueError("revision must describe the fixed file and metadata")
        next_page = 1
        for ordinal, part in enumerate(self.parts, 1):
            if part.ordinal != ordinal or part.page_start != next_page:
                raise ValueError("parts must have contiguous ordinals and page spans")
            next_page += part.page_count
        if self.parts and next_page - 1 != self.page_count:
            raise ValueError("part page spans must cover the composed PDF")
        if sum(part.size_bytes for part in self.parts) > FILE_BYTE_LIMIT:
            raise ValueError("combined uploads exceed the file limit")
        return self


class AttachmentUploadPreview(Contract):
    dry_run: Literal[True] = True
    original: CertificateScanFile
    parts: list[CertificatePart] = Field(default_factory=list, max_length=MAX_PARTS)
    metadata_sha256: Sha256
    enabled_upload_mode: Literal["single_pdf", "ordered_parts"]
    next_action: Literal["submit_upload"] = "submit_upload"


class AttachmentWriteReceipt(Contract):
    attachment_id: UUID
    revision_id: UUID
    file_id: UUID
    revision: Revision
    state_version: Revision
    review_state: ReviewState
    duplicate: bool
    next_action: NextAction


class AttachmentReviewInput(Contract):
    request_id: UUID
    expected_state_version: Revision
    # Null means no decision exists; this is an optimistic-lock value, not omission.
    expected_review_id: UUID | None
    file_id: UUID
    original_sha256: Sha256
    metadata_sha256: Sha256
    decision: Literal["approve", "reject", "revoke"]
    reason: ReasonCode

    @model_validator(mode="after")
    def approval_reason(self):
        if (self.decision == "approve") != (self.reason == "accepted_for_internal_use"):
            raise ValueError("approval requires the explicit internal-use acceptance reason")
        if self.decision == "revoke" and self.expected_review_id is None:
            raise ValueError("revocation must name the prior decision")
        return self


class AttachmentReviewView(Contract):
    id: UUID
    org_id: UUID
    attachment_id: UUID
    attachment_revision_id: UUID
    file_id: UUID
    original_sha256: Sha256
    metadata_sha256: Sha256
    prior_review_id: UUID | None
    decision: Literal["approve", "reject", "revoke"]
    reason: ReasonCode
    reviewed_by: UUID
    reviewed_at: AwareDatetime


class ProfileAttachmentLinkInput(Contract):
    request_id: UUID
    field: DeclarationField
    attachment_revision_id: UUID
    approval_id: UUID


class ProfileAttachmentLinkView(Contract):
    id: UUID
    org_id: UUID
    profile_id: UUID
    profile_revision_id: UUID
    field: DeclarationField
    attachment_id: UUID
    attachment_revision_id: UUID
    file_id: UUID
    approval_id: UUID
    active: bool
    state_version: Revision
    created_by: UUID
    created_at: AwareDatetime


class TaskAttachmentSelect(Contract):
    request_id: UUID
    task_org_profile_id: UUID
    profile_attachment_link_id: UUID
    # Null permits only an empty active slot; replacement must name the old selection.
    expected_selection_id: UUID | None


class TaskAttachmentView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    task_org_profile_id: UUID
    profile_revision_id: UUID
    profile_attachment_link_id: UUID
    field: DeclarationField
    attachment_id: UUID
    attachment_revision_id: UUID
    file_id: UUID
    approval_id: UUID
    original_sha256: Sha256
    lot: str | None = Field(default=None, max_length=100)
    active: bool
    state_version: Revision
    selected_by: UUID
    selected_at: AwareDatetime
    readiness: AttachmentReadiness


class AttachmentLinkContext(Contract):
    """Internal authorized resolver output, never a token/list payload."""

    profile: OrgProfileRevision
    task_profile: TaskOrgProfileSnapshot
    link: ProfileAttachmentLinkView
    selection: TaskAttachmentView

    @model_validator(mode="after")
    def exact_profile_binding(self):
        if (
            len(
                {
                    self.profile.org_id,
                    self.task_profile.org_id,
                    self.link.org_id,
                    self.selection.org_id,
                }
            )
            != 1
        ):
            raise ValueError("all link parents must belong to one org")
        if (
            self.link.profile_id != self.profile.profile_id
            or self.link.profile_revision_id != self.profile.id
            or self.task_profile.profile_revision_id != self.profile.id
            or self.selection.profile_revision_id != self.profile.id
            or self.selection.task_org_profile_id != self.task_profile.id
            or self.selection.task_id != self.task_profile.task_id
            or self.selection.profile_attachment_link_id != self.link.id
            or self.selection.lot != self.task_profile.lot
            or self.selection.field != self.link.field
            or self.selection.attachment_revision_id != self.link.attachment_revision_id
            or self.selection.attachment_id != self.link.attachment_id
            or self.selection.file_id != self.link.file_id
            or self.selection.approval_id != self.link.approval_id
        ):
            raise ValueError("declaration link and selection must pin the same parents")
        if not getattr(self.profile.data, self.link.field):
            raise ValueError("linked declaration field must contain a value")
        return self


class AttachmentSourceCreate(Contract):
    request_id: UUID
    task_attachment_id: UUID
    page: PageNumber


class AttachmentSourceSummary(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    task_attachment_id: UUID
    attachment_revision_id: UUID
    page: PageNumber
    source_kind: Literal["user_supplied_attachment_pdf"] = "user_supplied_attachment_pdf"
    original_sha256: Sha256
    source_png_sha256: Sha256
    rendered_at: AwareDatetime
    active_selection: bool
    latest_privacy_hold_id: UUID | None = None
    readiness: AttachmentReadiness
    status: Literal["unconfirmed_source"] = "unconfirmed_source"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False


class AttachmentSourceArchive(AttachmentSourceSummary):
    """Exact source resolver view; current readiness is derived, never stored authority."""

    task_org_profile_id: UUID
    profile_revision_id: UUID
    profile_attachment_link_id: UUID
    field: DeclarationField
    attachment_id: UUID
    file_id: UUID
    approval_id: UUID
    original: CertificateScanFile
    preview: EvidenceSourcePreview
    render_profile: Literal["pdf-page-preview-v1"] = "pdf-page-preview-v1"
    dpi: Literal[150] = 150
    created_by: UUID

    @model_validator(mode="after")
    def exact_page(self):
        if self.page > self.original.page_count:
            raise ValueError("page is outside the immutable original")
        if self.original_sha256 != self.original.sha256:
            raise ValueError("original hash differs from fixed file")
        if self.source_png_sha256 != self.preview.sha256:
            raise ValueError("source hash differs from archived PNG")
        return self


class AttachmentSourceReceipt(Contract):
    source: AttachmentSourceArchive
    duplicate: bool


class AttachmentPrivacyClear(Contract):
    mode: Literal["clear"]
    request_id: UUID
    extraction_job_id: UUID
    reviewed_source_png_sha256: Sha256
    clearance: Literal["safe_for_task_team"]
    expected_hold_id: Literal[None] = None


class AttachmentPrivacyRedacted(Contract):
    """Later adoption of an already human-reviewed derivative from the privacy chain."""

    mode: Literal["redacted"]
    request_id: UUID
    extraction_job_id: UUID
    reviewed_source_png_sha256: Sha256
    asset_id: UUID
    rendition_id: UUID
    privacy_review_id: UUID
    expected_image_sha256: Sha256
    expected_hold_id: UUID | None
    clearance: Literal["safe_for_task_team"]


class AttachmentPrivacyHoldInput(Contract):
    """A durable negative decision; it creates no image or privacy clearance."""

    mode: Literal["needs_redaction"]
    request_id: UUID
    reviewed_source_png_sha256: Sha256
    expected_hold_id: UUID | None
    reason: Literal["sensitive_content"] = "sensitive_content"


AttachmentPrivacyInput = Annotated[
    AttachmentPrivacyClear | AttachmentPrivacyRedacted | AttachmentPrivacyHoldInput,
    Field(discriminator="mode"),
]


class AttachmentPrivacyHoldView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    evidence_source_id: UUID
    source_png_sha256: Sha256
    prior_hold_id: UUID | None
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    reason: Literal["sensitive_content"] = "sensitive_content"
    state: Literal["needs_redaction"] = "needs_redaction"
    next_action: Literal["redact_page"] = "redact_page"
    responsible_user_id: UUID
    eligible_for_draft_export: Literal[False] = False


class AttachmentPrivacyReceipt(Contract):
    org_id: UUID
    evidence_source_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    source_png_sha256: Sha256
    rendition: RenditionView
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    reviewed_upload_sha256: Sha256
    resolved_hold_id: UUID | None
    clearance: Literal["safe_for_task_team"] = "safe_for_task_team"
    duplicate: bool
    status: Literal["unconfirmed_material"] = "unconfirmed_material"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False

    @model_validator(mode="after")
    def existing_privacy_profile(self):
        image = self.rendition.image
        if self.rendition.profile != "screenshot-markup-v1":
            raise ValueError("attachment privacy cannot use a prototype rendition")
        if image.width_px * image.height_px > PNG_PIXEL_LIMIT:
            raise ValueError("privacy rendition exceeds pixel limit")
        # The prepared pixels are reviewed before the existing renderer adds a footer;
        # the server verifies their hash against the saved review, not the footer PNG.
        return self


class AttachmentAnnotationSource(Contract):
    kind: Literal["attachment_page"] = "attachment_page"
    evidence_source_id: UUID
    asset_id: UUID
    rendition_id: UUID
    expected_image_sha256: Sha256


class AttachmentAnnotationBinding(Contract):
    """Proposed additive B05 branch; importing it does not enable annotation."""

    kind: Literal["attachment_page"] = "attachment_page"
    archive: AttachmentSourceArchive
    privacy: AttachmentPrivacyReceipt
    root_source_png: PNGDescriptor
    # Same meaning as B05's vendor binding: the actual sanitized stored PNG
    # supplied to the renderer, including generated padding/footer outside content.
    source_png: PNGDescriptor
    source_time_kind: Literal["server_page_rendered_at"] = "server_page_rendered_at"
    source_time: AwareDatetime
    page_kind: Literal["pdf_page"] = "pdf_page"
    authorized_content: PixelRect
    mapping: ContentMapping
    privacy_lineage_sha256: Sha256
    root_mapping_sha256: Sha256

    @model_validator(mode="after")
    def same_source_and_content(self):
        if (
            self.privacy.org_id != self.archive.org_id
            or self.privacy.evidence_source_id != self.archive.id
            or self.privacy.task_id != self.archive.task_id
            or self.privacy.source_png_sha256 != self.archive.source_png_sha256
            or self.source_time != self.archive.rendered_at
        ):
            raise ValueError("privacy and annotation must bind the same archive")
        preview = self.archive.preview
        expected = PNGDescriptor(
            sha256=preview.sha256,
            size_bytes=preview.size_bytes,
            width_px=preview.width_px,
            height_px=preview.height_px,
        )
        if self.root_source_png != expected:
            raise ValueError("root source descriptor must match the archived page")
        if self.source_png != self.privacy.rendition.image:
            raise ValueError("renderer input must be the privacy-cleared stored rendition")
        if self.mapping != self.privacy.rendition.mapping:
            raise ValueError("content mapping must match the privacy-cleared rendition")
        if self.authorized_content != PixelRect(
            x=self.mapping.content_offset_x,
            y=self.mapping.content_offset_y,
            width=self.mapping.content_width,
            height=self.mapping.content_height,
        ):
            raise ValueError("annotation addresses the privacy-cleared content plane")
        if not self.authorized_content.within(self.source_png.width_px, self.source_png.height_px):
            raise ValueError("authorized content must fit inside the sanitized input PNG")
        return self


class AttachmentDownloadLink(Contract):
    url: str = Field(min_length=1, max_length=4096)
    expires_in: Literal[300] = 300


class AttachmentDownloadReceipt(Contract):
    attachment_revision_id: UUID
    file_id: UUID
    part_ordinal: int | None = Field(default=None, strict=True, ge=1, le=MAX_PARTS)
    output_path: str = Field(min_length=1, max_length=4096)
    file: CertificateScanFile | CertificatePart

    @model_validator(mode="after")
    def exact_variant(self):
        if isinstance(self.file, CertificatePart):
            if self.part_ordinal != self.file.ordinal:
                raise ValueError("part download must name the descriptor ordinal")
        elif self.part_ordinal is not None:
            raise ValueError("original download cannot name a part")
        return self


class AttachmentErrorData(Contract):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=300)
    next_action: NextAction | None = None
    resource_id: UUID | None = None


class AttachmentUploadBytes(Contract):
    """Internal bounded transport input only; never a CLI/API Result payload."""

    name: str = Field(min_length=1, max_length=200)
    content: bytes = Field(min_length=1, max_length=FILE_BYTE_LIMIT, repr=False)


class AttachmentPreparedFile(Contract):
    """Internal processor output; reuse immutable put, never return bytes in JSON."""

    original: CertificateScanFile
    content: bytes = Field(min_length=1, max_length=FILE_BYTE_LIMIT, repr=False)
    parts: list[CertificatePart] = Field(default_factory=list, max_length=MAX_PARTS)
    part_contents: list[bytes] = Field(default_factory=list, max_length=MAX_PARTS, repr=False)

    @model_validator(mode="after")
    def bounded_parts(self):
        if len(self.parts) != len(self.part_contents):
            raise ValueError("each retained part needs exactly one byte payload")
        if sum(len(content) for content in self.part_contents) > FILE_BYTE_LIMIT:
            raise ValueError("combined part bytes exceed the upload bound")
        if len(self.content) != self.original.size_bytes:
            raise ValueError("original byte size differs from its descriptor")
        for part, content in zip(self.parts, self.part_contents, strict=True):
            if len(content) != part.size_bytes:
                raise ValueError("part byte size differs from its descriptor")
        return self


class AttachmentFileProcessor(Protocol):
    """Reuse certificate validation/composition through the existing PDF subprocess.

    No external model Provider, new storage adapter or conversion service is needed.
    Implementations must enforce aggregate bounds before decode, kill timed-out work,
    and validate all returned bytes/hashes. The protocol itself executes nothing.
    """

    async def prepare(
        self,
        uploads: Sequence[AttachmentUploadBytes],
        options: Sequence[CertificatePartOptions],
    ) -> AttachmentPreparedFile: ...

    async def render_source(
        self, content: bytes, original: CertificateScanFile, page: int, neutral_name: str
    ) -> tuple[bytes, EvidenceSourcePreview, datetime]: ...


class AttachmentArchiveService(Protocol):
    """Service boundary shared by authenticated HTTP and CLI; no local bypass.

    Actor and session come from verified context. Every method rechecks live grants;
    Pydantic IDs and type annotations are not evidence of authorization.
    """

    async def upload(
        self,
        session: AsyncSession,
        actor: Identity,
        body: AttachmentCreate | AttachmentRevise,
        uploads: Sequence[AttachmentUploadBytes],
        storage: Storage,
        *,
        attachment_id: UUID | None = None,
    ) -> AttachmentWriteReceipt | AttachmentUploadPreview: ...

    async def list_archives(
        self, session: AsyncSession, actor: Identity, query: AttachmentListQuery
    ) -> tuple[PageData, list[AttachmentSummary]]: ...

    async def show(
        self, session: AsyncSession, actor: Identity, attachment_id: UUID
    ) -> AttachmentShowData: ...

    async def revisions(
        self, session: AsyncSession, actor: Identity, attachment_id: UUID, query: PageQuery
    ) -> tuple[PageData, list[AttachmentRevisionSummary]]: ...

    async def revision(
        self, session: AsyncSession, actor: Identity, revision_id: UUID
    ) -> AttachmentRevisionView: ...

    async def assign(
        self,
        session: AsyncSession,
        actor: Identity,
        attachment_id: UUID,
        body: AttachmentAssignment,
    ) -> AttachmentShowData: ...

    async def review(
        self, session: AsyncSession, actor: Identity, revision_id: UUID, body: AttachmentReviewInput
    ) -> AttachmentReviewView: ...

    async def reviews(
        self, session: AsyncSession, actor: Identity, revision_id: UUID, query: PageQuery
    ) -> tuple[PageData, list[AttachmentReviewView]]: ...

    async def deactivate_archive(
        self,
        session: AsyncSession,
        actor: Identity,
        attachment_id: UUID,
        body: AttachmentDeactivate,
    ) -> AttachmentShowData: ...

    async def link_profile(
        self,
        session: AsyncSession,
        actor: Identity,
        profile_revision_id: UUID,
        body: ProfileAttachmentLinkInput,
    ) -> ProfileAttachmentLinkView: ...

    async def profile_links(
        self, session: AsyncSession, actor: Identity, profile_revision_id: UUID, query: PageQuery
    ) -> tuple[PageData, list[ProfileAttachmentLinkView]]: ...

    async def deactivate_link(
        self, session: AsyncSession, actor: Identity, link_id: UUID, body: AttachmentDeactivate
    ) -> ProfileAttachmentLinkView: ...

    async def select(
        self, session: AsyncSession, actor: Identity, task_id: UUID, body: TaskAttachmentSelect
    ) -> TaskAttachmentView: ...

    async def selections(
        self, session: AsyncSession, actor: Identity, task_id: UUID, query: PageQuery
    ) -> tuple[PageData, list[TaskAttachmentView]]: ...

    async def deactivate_selection(
        self, session: AsyncSession, actor: Identity, selection_id: UUID, body: AttachmentDeactivate
    ) -> TaskAttachmentView: ...

    async def resolve_link(
        self, session: AsyncSession, actor: Identity, selection_id: UUID
    ) -> AttachmentLinkContext: ...

    async def create_source(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        body: AttachmentSourceCreate,
        storage: Storage,
    ) -> AttachmentSourceReceipt: ...

    async def sources(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        query: AttachmentSourceListQuery,
    ) -> tuple[PageData, list[AttachmentSourceSummary]]: ...

    async def source(
        self, session: AsyncSession, actor: Identity, source_id: UUID
    ) -> AttachmentSourceSummary: ...

    async def privacy_review(
        self,
        session: AsyncSession,
        actor: Identity,
        source_id: UUID,
        body: AttachmentPrivacyInput,
        storage: Storage,
    ) -> AttachmentPrivacyReceipt | AttachmentPrivacyHoldView: ...

    async def resolve_annotation(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        source: AttachmentAnnotationSource,
    ) -> AttachmentAnnotationBinding: ...

    async def read_original(
        self,
        session: AsyncSession,
        actor: Identity,
        revision_id: UUID,
        storage: Storage,
        *,
        part_ordinal: int | None = None,
    ) -> tuple[bytes, CertificateScanFile | CertificatePart]: ...

    async def preview_page(
        self,
        session: AsyncSession,
        actor: Identity,
        revision_id: UUID,
        page: int,
        zoom: Literal[1, 2],
        storage: Storage,
    ) -> bytes: ...

    async def read_source(
        self, session: AsyncSession, actor: Identity, source_id: UUID, storage: Storage
    ) -> tuple[bytes, EvidenceSourcePreview]: ...


# These aliases deliberately preserve the shipped seven-key envelope and decimal
# cost fields; typed data/items models above define each command's inner payload.
AttachmentResult = Result
AttachmentCost = Cost
RESULT_VERSION = CONTRACT_VERSION
