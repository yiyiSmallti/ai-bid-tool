"""Uploaded-bid upload, local preparation and page inventory contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.contracts import Contract, Cost
from app.schemas.screenshot_contracts import PNGDescriptor, Sha256

FILE_LIMIT = 20
FILE_BYTE_LIMIT = 100 * 1024 * 1024
SUBMISSION_BYTE_LIMIT = 500 * 1024 * 1024
PAGE_LIMIT = 1000
PREFLIGHT_TTL_SECONDS = 900

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Revision = Annotated[int, Field(strict=True, ge=1)]
PageNumber = Annotated[int, Field(strict=True, ge=1, le=PAGE_LIMIT)]
DocumentRole = Literal["tender", "bid"]
BidDocumentKind = Literal[
    "tender", "qualification", "commercial_technical", "price", "declaration", "other"
]

TOKEN_SCOPES = frozenset({"bid-review:run", "bid-review:read"})
HUMAN_ONLY_SCOPES = frozenset(
    {
        "bid-review:upload",
        "bid-review:prepare",
        "bid-review:report:render",
        "bid-review:original:read",
        "bid-review:source:read",
        "bid-review:report:read",
        "bid-review:report:download",
        "bid-review:decide",
        "bid-review:evidence:review",
        "bid-review:price:release",
        "bid-review:outbound:authorize",
        "bid-review:classify",
    }
)


class BidFileInput(Contract):
    role: DocumentRole
    kind: BidDocumentKind
    media_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    sha256: Sha256
    size_bytes: int = Field(strict=True, gt=0, le=FILE_BYTE_LIMIT)

    @model_validator(mode="after")
    def role_matches_kind(self) -> Self:
        if (self.role == "tender") != (self.kind == "tender"):
            raise ValueError("tender role and kind must agree")
        return self


class BidSubmissionCreate(Contract):
    """Multipart metadata; bytes are independently bounded and verified by service."""

    request_id: UUID
    files: list[BidFileInput] = Field(min_length=2, max_length=FILE_LIMIT)
    dry_run: bool = False

    @model_validator(mode="after")
    def complete_bounded_upload(self) -> Self:
        if {item.role for item in self.files} != {"tender", "bid"}:
            raise ValueError("submission requires both tender and bid files")
        if sum(item.size_bytes for item in self.files) > SUBMISSION_BYTE_LIMIT:
            raise ValueError("submission bytes exceed the aggregate limit")
        if len({(item.role, item.sha256) for item in self.files}) != len(self.files):
            raise ValueError("duplicate files in one role are not allowed")
        return self


class BidUploadedDocument(BidFileInput):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    file_id: UUID


class BidSubmissionUploaded(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    revision: Revision
    manifest_sha256: Sha256
    files: list[BidUploadedDocument] = Field(min_length=2, max_length=FILE_LIMIT)
    created_by: UUID
    created_at: AwareDatetime
    state: Literal["uploaded", "withdrawn"] = "uploaded"

    @model_validator(mode="after")
    def bounded_uploaded_parents(self) -> Self:
        if {item.role for item in self.files} != {"tender", "bid"}:
            raise ValueError("uploaded submission needs both file roles")
        if len({item.id for item in self.files}) != len(self.files):
            raise ValueError("uploaded document IDs must be unique")
        if sum(item.size_bytes for item in self.files) > SUBMISSION_BYTE_LIMIT:
            raise ValueError("uploaded submission exceeds aggregate bytes")
        if any(
            (item.org_id, item.task_id, item.submission_id)
            != (
                self.org_id,
                self.task_id,
                self.id,
            )
            for item in self.files
        ):
            raise ValueError("uploaded file parents must match the submission")
        return self


class BidUploadPreview(Contract):
    dry_run: Literal[True] = True
    files: list[BidFileInput] = Field(min_length=2, max_length=FILE_LIMIT)
    payload_sha256: Sha256
    limits: dict[Literal["files", "file_bytes", "submission_bytes"], int]
    cost: Cost


class BidPrepareRequest(Contract):
    request_id: UUID
    submission_id: UUID
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    preflight_token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)

    @model_validator(mode="after")
    def hash_bound_preparation(self) -> Self:
        if not self.dry_run and (self.expected_input_hash is None or self.preflight_token is None):
            raise ValueError("preparation submission requires the file-manifest preview hash")
        if self.dry_run and (
            self.retry or self.expected_input_hash is not None or self.preflight_token is not None
        ):
            raise ValueError("preparation dry-run cannot carry submission fields")
        return self


class BidPreparePreview(Contract):
    dry_run: Literal[True] = True
    submission_id: UUID
    input_hash: Sha256
    budget: BudgetPreflightData
    expires_at: AwareDatetime
    preflight_token: str = Field(min_length=1, max_length=4096, repr=False)
    local_only: Literal[True] = True
    admission_blockers: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def local_calls_and_exact_expiry(self) -> Self:
        if self.budget.input_hash != self.input_hash or self.budget.planned_calls is None:
            raise ValueError("local preparation needs the exact input hash and a bounded call plan")
        if not 0 <= self.budget.planned_calls <= PAGE_LIMIT:
            raise ValueError("local preparation OCR calls cannot exceed the page limit")
        if self.budget.maximum_calls is not None and self.budget.maximum_calls > PAGE_LIMIT:
            raise ValueError("local preparation maximum calls cannot exceed the page limit")
        quote = self.budget.next_call
        if quote is not None and (quote.payer != "local_free" or quote.capability != "ocr"):
            raise ValueError("preparation permits only metered local-free OCR calls")
        if self.budget.estimate.task_amount != 0 or self.budget.estimate.charge != 0:
            raise ValueError("local preparation cannot invent paid liability")
        if (self.expires_at - self.budget.as_of).total_seconds() != PREFLIGHT_TTL_SECONDS:
            raise ValueError("preparation preview expires after 15 minutes")
        return self


class BidDocumentView(BidUploadedDocument):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    file_id: UUID
    page_count: PageNumber
    # DOCX pages describe a fixed derived render, never pagination of the original.
    rendered_pdf_sha256: Sha256
    render_profile: NonBlank
    renderer_identity: NonBlank
    citation_mode: Literal["page", "block"]
    parsing_warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def original_or_derived_pages(self) -> Self:
        if self.media_type == "application/pdf":
            if self.citation_mode != "page" or self.rendered_pdf_sha256 != self.sha256:
                raise ValueError("PDF page references must retain the unchanged original hash")
        elif self.citation_mode != "block":
            raise ValueError("DOCX original citations use structural blocks")
        return self


class BidSubmissionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    revision: Revision
    manifest_sha256: Sha256
    documents: list[BidDocumentView] = Field(min_length=2, max_length=FILE_LIMIT)
    created_by: UUID
    created_at: AwareDatetime
    state: Literal["prepared", "withdrawn"]
    preparation_job_id: UUID
    preparation_input_hash: Sha256

    @model_validator(mode="after")
    def exact_document_parents_and_limits(self) -> Self:
        if len({document.id for document in self.documents}) != len(self.documents):
            raise ValueError("document IDs must be unique")
        if {document.role for document in self.documents} != {"tender", "bid"}:
            raise ValueError("both document roles are required")
        for document in self.documents:
            if (document.org_id, document.task_id, document.submission_id) != (
                self.org_id,
                self.task_id,
                self.id,
            ):
                raise ValueError("document parents must match the submission")
        if sum(document.size_bytes for document in self.documents) > SUBMISSION_BYTE_LIMIT:
            raise ValueError("submission bytes exceed the aggregate limit")
        if sum(document.page_count for document in self.documents) > PAGE_LIMIT:
            raise ValueError("tender and bid pages exceed the aggregate limit")
        return self


class BidPageRef(Contract):
    document_id: UUID
    role: DocumentRole
    original_sha256: Sha256
    rendered_pdf_sha256: Sha256
    page: PageNumber


class BidPageView(BidPageRef):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    image: PNGDescriptor
    render_profile: NonBlank
    renderer_identity: NonBlank
    text_sha256: Sha256 | None
    text_status: Literal["native", "ocr", "unavailable"]
    price_page: bool
    redaction_status: Literal["not_reviewed", "safe_derivative", "human_only"]

    @model_validator(mode="after")
    def text_hash_matches_availability(self) -> Self:
        if (self.text_status == "unavailable") != (self.text_sha256 is None):
            raise ValueError("text availability and hash must agree")
        return self


class BidReviewListQuery(Contract):
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class BidPreparationStatus(Contract):
    job_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    input_hash: Sha256


class BidDocumentInventory(Contract):
    document_id: UUID
    page_count: int = Field(strict=True, ge=1, le=PAGE_LIMIT)
    text_pages: int = Field(strict=True, ge=0, le=PAGE_LIMIT)
    image_pages: int = Field(strict=True, ge=0, le=PAGE_LIMIT)
    signature_fields: int = Field(strict=True, ge=0)
    parsing_warnings: list[str] = Field(default_factory=list, max_length=100)


class BidSubmissionDetail(Contract):
    submission: BidSubmissionUploaded | BidSubmissionView
    preparation: BidPreparationStatus | None = None
    inventory: list[BidDocumentInventory] = Field(default_factory=list, max_length=FILE_LIMIT)
