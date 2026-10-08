"""Pending uploaded-bid review contracts; no runtime registration or implementation.

Types describe an independent uploaded_bid path. Pydantic validates structure, not
org authorization, source truth, redaction safety or human review authority.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, Self
from uuid import UUID

from app.schemas.budget_contracts import (
    BudgetCallQuote,
    BudgetJobResult,
    BudgetPreflightData,
    Money,
)
from app.schemas.check_contracts import (
    AssessmentJobAccepted,
    AssessmentJobResult,
    AssessmentListData,
    FindingDecisionRequest,
    VerifiedCitation,
)
from app.schemas.contracts import (
    CONTRACT_VERSION,
    Category,
    Contract,
    Cost,
    ProviderUsage,
    Result,
    Source,
)
from app.schemas.response_card_contracts import ReviewDomain
from app.schemas.screenshot_contracts import PixelRect, PNGDescriptor, Sha256
from pydantic import AwareDatetime, Field, JsonValue, StringConstraints, model_validator

if TYPE_CHECKING:
    from app.providers.storage import Storage
    from app.services.auth import Identity
    from sqlalchemy.ext.asyncio import AsyncSession

FILE_LIMIT = 20
FILE_BYTE_LIMIT = 100 * 1024 * 1024
SUBMISSION_BYTE_LIMIT = 500 * 1024 * 1024
PAGE_LIMIT = 1000
CALL_LIMIT = 200
PREFLIGHT_TTL_SECONDS = 900
CLEF_IMAGE_LIMIT = 4
CLEF_IMAGE_BYTE_LIMIT = 4 * 1024 * 1024
CLEF_IMAGES_BYTE_LIMIT = 8 * 1024 * 1024
CLEF_IMAGE_PIXEL_LIMIT = 16_000_000
CLEF_REQUEST_BYTE_LIMIT = 13 * 1024 * 1024
CLEF_CONTEXT_TOKENS = 65_536
JOB_KINDS = frozenset({"bid_review_prepare", "bid_review", "bid_review_report"})

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
Revision = Annotated[int, Field(strict=True, ge=1)]
PageNumber = Annotated[int, Field(strict=True, ge=1, le=PAGE_LIMIT)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Points = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8, allow_inf_nan=False)]
DocumentRole = Literal["tender", "bid"]
BidDocumentKind = Literal[
    "tender", "qualification", "commercial_technical", "price", "declaration", "other"
]
ReviewSlice = Literal["compliance", "with_scoring"]
Observation = Literal["present", "missing", "unknown", "not_applicable"]
ReportSection = Literal[
    "一、总体结论",
    "二、基本信息",
    "三、废标判定",
    "签章校验",
    "四、高风险缺陷",
    "五、得分预估",
    "六、证据核对",
    "七、补救清单",
    "八、检验说明",
]

# Proposed declarations only. Importing never changes auth.py or token grants.
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


class BidPageRegion(Contract):
    kind: Literal["page_region"] = "page_region"
    page: BidPageRef
    image: PNGDescriptor
    box: PixelRect
    observation: NonBlank

    @model_validator(mode="after")
    def box_inside_image(self) -> Self:
        if not self.box.within(self.image.width_px, self.image.height_px):
            raise ValueError("observation box must be inside the archived page image")
        return self


class UploadedBidCitation(Contract):
    kind: Literal["uploaded_bid"] = "uploaded_bid"
    source: Source
    rendered_page: BidPageRef

    @model_validator(mode="after")
    def original_and_render_agree(self) -> Self:
        if self.rendered_page.role != "bid":
            raise ValueError("uploaded bid citation must address a bid document")
        if self.source.document_id != self.rendered_page.document_id:
            raise ValueError("Source and page must bind the same original document")
        if self.source.page is not None and self.source.page != self.rendered_page.page:
            raise ValueError("native PDF Source page must equal the displayed page")
        return self


class TenderReviewCitation(Contract):
    kind: Literal["tender_review"] = "tender_review"
    # Reuse the verified citation union; this branch permits tender only.
    citation: VerifiedCitation
    rendered_page: BidPageRef

    @model_validator(mode="after")
    def fixed_tender_source(self) -> Self:
        if self.citation.kind != "tender" or self.rendered_page.role != "tender":
            raise ValueError("tender review must contain a verified tender citation")
        if self.citation.source.document_id != self.rendered_page.document_id:
            raise ValueError("tender source and rendered page must share the document")
        if (
            self.citation.source.page is not None
            and self.citation.source.page != self.rendered_page.page
        ):
            raise ValueError("native tender PDF page must match the display reference")
        return self


BidSupport = Annotated[UploadedBidCitation | BidPageRegion, Field(discriminator="kind")]
TenderSupport = Annotated[TenderReviewCitation | BidPageRegion, Field(discriminator="kind")]


class BidAbsenceSearch(Contract):
    """An absent item has no invented quote; retain the exact inspected locations."""

    kind: Literal["locations"] = "locations"
    searched_pages: list[BidPageRef] = Field(min_length=1, max_length=PAGE_LIMIT)
    searched_regions: list[BidPageRegion] = Field(default_factory=list, max_length=100)
    method: Literal["local_text", "local_pdf", "ocr", "llm", "human"]
    coverage: Literal["required_locations", "all_bid_pages", "partial"]
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_bid_search(self) -> Self:
        keys = [(page.document_id, page.page) for page in self.searched_pages]
        if len(set(keys)) != len(keys) or any(page.role != "bid" for page in self.searched_pages):
            raise ValueError("absence search needs unique bid pages")
        if any(region.page not in self.searched_pages for region in self.searched_regions):
            raise ValueError("searched regions must belong to the inspected pages")
        if self.coverage == "partial" and not self.limitation_codes:
            raise ValueError("partial absence search must state its limitations")
        return self


class BidManifestAbsenceSearch(Contract):
    """Missing whole files cite the inspected inventory, never a fabricated page."""

    kind: Literal["submission_inventory"] = "submission_inventory"
    submission_id: UUID
    submission_manifest_sha256: Sha256
    inspected_bid_document_ids: list[UUID] = Field(min_length=1, max_length=FILE_LIMIT - 1)
    required_document_description: NonBlank
    method: Literal["manifest_rule", "llm_mapping", "human"]
    coverage: Literal["complete_inventory", "partial"]
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def exact_inventory(self) -> Self:
        if len(set(self.inspected_bid_document_ids)) != len(self.inspected_bid_document_ids):
            raise ValueError("inspected inventory IDs must be unique")
        if self.coverage == "partial" and not self.limitation_codes:
            raise ValueError("partial inventory search must state its limitations")
        return self


AbsenceSearch = Annotated[BidAbsenceSearch | BidManifestAbsenceSearch, Field(discriminator="kind")]


class FindingBasis(Contract):
    kind: Literal["rule", "model", "human_reviewed"]
    rule_or_prompt_version: NonBlank
    confidence: Probability | None = None
    provider_config_id: UUID | None = None
    platform_model_id: str | None = Field(default=None, min_length=1, max_length=100)
    provider_revision: Revision | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    human_decision_id: UUID | None = None

    @model_validator(mode="after")
    def attributable_basis(self) -> Self:
        if self.kind == "model":
            if (
                self.confidence is None
                or (self.provider_config_id is None and self.platform_model_id is None)
                or self.provider_revision is None
                or self.model is None
            ):
                raise ValueError("model basis needs pinned identity and confidence")
        elif (
            self.confidence is not None
            or self.provider_config_id is not None
            or self.platform_model_id is not None
            or self.provider_revision is not None
            or self.model
        ):
            raise ValueError("nonmodel basis cannot inherit model confidence or identity")
        if (self.kind == "human_reviewed") != (self.human_decision_id is not None):
            raise ValueError("human basis needs its exact recorded review decision")
        return self


class BidReviewFinding(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    code: NonBlank
    title: NonBlank
    severity: Literal["fatal", "high", "medium"]
    impact: Literal["rejection", "lost_points", "both", "uncertain"]
    review_domain: ReviewDomain | None = None
    classification_id: UUID | None = None
    basis: FindingBasis
    tender_support: list[TenderSupport] = Field(min_length=1, max_length=20)
    bid_support: list[BidSupport] = Field(default_factory=list, max_length=20)
    absence_search: AbsenceSearch | None = None
    explanation: NonBlank
    remediation: NonBlank
    deadline: AwareDatetime | None = None
    status: Literal["open", "dismissed", "confirmed"] = "open"
    revision: Revision = 1
    latest_decision_id: UUID | None = None
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def support_and_decision_state(self) -> Self:
        if not self.bid_support and self.absence_search is None:
            raise ValueError("finding requires bid support or a bounded absence search")
        if any(
            isinstance(support, BidPageRegion) and support.page.role != "tender"
            for support in self.tender_support
        ):
            raise ValueError("tender image basis must address a tender page")
        if any(
            isinstance(support, BidPageRegion) and support.page.role != "bid"
            for support in self.bid_support
        ):
            raise ValueError("bid image support must address a bid page")
        if (self.revision == 1) != (self.latest_decision_id is None):
            raise ValueError("decision pointer must match the finding revision")
        if (self.review_domain is None) != (self.classification_id is None):
            raise ValueError("classified finding needs the exact classification event")
        if self.status in {"dismissed", "confirmed"} and (
            self.latest_decision_id is None or self.classification_id is None
        ):
            raise ValueError("closed disposition requires a recorded human decision")
        return self


class BidReviewDecisionRequest(FindingDecisionRequest):
    action: Literal["dismiss", "reopen", "confirm"]
    request_id: UUID
    expected_decision_id: UUID | None


class BidReviewDecisionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    finding_id: UUID
    prior_decision_id: UUID | None
    revision: int = Field(strict=True, ge=2)
    action: Literal["dismiss", "reopen", "confirm", "classify"]
    reason: NonBlank
    reason_sha256: Sha256
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True

    @model_validator(mode="after")
    def linked_history(self) -> Self:
        if (self.revision == 2) != (self.prior_decision_id is None):
            raise ValueError("first decision has no predecessor; later ones require it")
        return self


class BidReviewClassificationRequest(Contract):
    request_id: UUID
    expected_decision_id: UUID | None
    expected_revision: Revision
    expected_input_hash: Sha256
    review_domain: ReviewDomain
    reason: NonBlank


class BidReviewClassificationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    subject_kind: Literal["finding", "evidence_check", "signature_requirement"]
    subject_id: UUID
    revision: Revision
    prior_classification_id: UUID | None
    review_domain: ReviewDomain
    classified_by: UUID
    classified_at: AwareDatetime
    reason_sha256: Sha256
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True


class SignatureRequirement(Contract):
    id: UUID
    tender_support: list[TenderSupport] = Field(min_length=1, max_length=20)
    required: Literal[
        "company_seal",
        "legal_representative_signature",
        "authorized_agent_signature",
        "personal_seal",
        "date",
        "seam_seal",
        "every_page_electronic_seal",
        "pdf_digital_signature",
    ]
    required_owner_ref: str | None = Field(default=None, min_length=1, max_length=100)
    location_description: NonBlank
    location_rule: Literal["specified_location", "each_document", "every_page", "seam_group"]
    applicability: Literal["applicable", "not_applicable", "unknown"]
    date_required: bool
    authorized_agent_signature_allowed: bool = False
    seam_group_ref: str | None = Field(default=None, min_length=1, max_length=100)
    mapping_state: Literal["mapped", "unmapped"]
    mapping_reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    target_pages: list[BidPageRef] = Field(default_factory=list, max_length=PAGE_LIMIT)
    checklist_state: Literal["candidate", "human_reviewed"] = "candidate"
    checklist_decision_id: UUID | None = None

    @model_validator(mode="after")
    def reviewed_checklist(self) -> Self:
        if (self.checklist_state == "human_reviewed") != (self.checklist_decision_id is not None):
            raise ValueError("reviewed signing requirements need the human decision")
        if self.mapping_state == "mapped" and not self.target_pages:
            raise ValueError("mapped signing requirement needs the required target pages")
        if self.mapping_state == "unmapped" and (
            self.target_pages or self.mapping_reason_code is None
        ):
            raise ValueError("unmapped signing requirement retains a reason and no invented page")
        if self.location_rule == "seam_group" and self.seam_group_ref is None:
            raise ValueError("seam signing must identify its exact document/page group")
        if len({(page.document_id, page.page) for page in self.target_pages}) != len(
            self.target_pages
        ):
            raise ValueError("signature target pages must be unique")
        if any(page.role != "bid" for page in self.target_pages):
            raise ValueError("signature targets must be bid pages")
        return self


class PDFSignatureValidation(Contract):
    """Protected local cryptographic observation; offline trust is explicitly limited."""

    document_id: UUID
    original_sha256: Sha256
    field_name_sha256: Sha256
    signed_revision_sha256: Sha256 | None
    signed_revision: int | None = Field(default=None, strict=True, ge=1)
    byte_range_coverage: Literal[
        "complete_current_revision", "signed_revision_only", "invalid", "unknown", "not_signed"
    ]
    claimed_signing_time: AwareDatetime | None = None
    trusted_timestamp_time: AwareDatetime | None = None
    trusted_timestamp_status: Literal["verified", "invalid", "absent", "unknown"]
    post_signing_changes: Literal[
        "none", "allowed_by_pdf_policy", "disallowed", "unknown", "not_signed"
    ]
    signature_present: bool
    cryptographic_validity: Literal["valid", "invalid", "unknown", "not_signed"]
    document_unmodified_after_signing: Literal["yes", "no", "unknown", "not_signed"]
    certificate_sha256: Sha256 | None
    certificate_subject: str | None = Field(default=None, max_length=2000)
    certificate_issuer: str | None = Field(default=None, max_length=2000)
    certificate_not_before: AwareDatetime | None = None
    certificate_not_after: AwareDatetime | None = None
    certificate_chain_trust: Literal["trusted", "untrusted", "unknown", "not_signed"]
    revocation_status: Literal["good", "revoked", "unknown", "not_signed"]
    validation_time: AwareDatetime
    validator_identity: NonBlank
    trust_store_sha256: Sha256
    validation_network: Literal["disabled"] = "disabled"
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def signed_or_unsigned(self) -> Self:
        states = (
            self.cryptographic_validity,
            self.document_unmodified_after_signing,
            self.certificate_chain_trust,
            self.revocation_status,
            self.byte_range_coverage,
            self.post_signing_changes,
        )
        if self.signature_present:
            if "not_signed" in states:
                raise ValueError("present signatures require signed states")
            if self.certificate_sha256 is None and (
                self.cryptographic_validity not in {"unknown", "invalid"}
                or not self.limitation_codes
            ):
                raise ValueError(
                    "missing certificate identity requires an explicit validation failure"
                )
        elif any(state != "not_signed" for state in states) or self.certificate_sha256 is not None:
            raise ValueError("unsigned observations must not assert signature validation")
        if (self.trusted_timestamp_status == "verified") != (
            self.trusted_timestamp_time is not None
        ):
            raise ValueError("trusted time needs an independently verified timestamp")
        if self.revocation_status == "unknown" and not self.limitation_codes:
            raise ValueError("unknown revocation must be reported as a limitation")
        if (
            self.certificate_not_before is not None
            and self.certificate_not_after is not None
            and self.certificate_not_before > self.certificate_not_after
        ):
            raise ValueError("certificate validity interval is reversed")
        return self


class SignatureCheck(Contract):
    id: UUID
    report_id: UUID
    requirement_id: UUID
    target: BidPageRef
    presence: Observation
    owner_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    date_filled: Observation
    position_match: Literal["matched", "misplaced", "unknown", "not_applicable"]
    basis: FindingBasis
    visual_support: list[BidPageRegion] = Field(default_factory=list, max_length=20)
    absence_search: AbsenceSearch | None = None
    pdf_signature: PDFSignatureValidation | None = None
    outcome: Literal["complete", "incomplete", "unknown", "not_applicable"]
    finding_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def exact_location_and_crypto_boundary(self) -> Self:
        if self.target.role != "bid":
            raise ValueError("signature check must address a bid page")
        if any(region.page != self.target for region in self.visual_support):
            raise ValueError("signature regions must address the required target page")
        if self.presence == "missing" and (
            not isinstance(self.absence_search, BidAbsenceSearch)
            or self.target not in self.absence_search.searched_pages
        ):
            raise ValueError("missing signature needs the exact target in its search record")
        if self.presence == "present" and not self.visual_support and self.pdf_signature is None:
            raise ValueError("present signature needs a visual region or local digital validation")
        if self.pdf_signature is not None and (
            self.pdf_signature.document_id != self.target.document_id
            or self.pdf_signature.original_sha256 != self.target.original_sha256
        ):
            raise ValueError("digital validation must bind the unchanged target document")
        if self.outcome == "complete" and (
            self.presence != "present"
            or self.owner_match not in {"matched", "not_applicable"}
            or self.date_filled not in {"present", "not_applicable"}
            or self.position_match not in {"matched", "not_applicable"}
        ):
            raise ValueError("complete signing needs every applicable visual check satisfied")
        if (
            self.outcome == "complete"
            and self.pdf_signature is not None
            and (
                self.pdf_signature.cryptographic_validity != "valid"
                or self.pdf_signature.document_unmodified_after_signing != "yes"
                or self.pdf_signature.byte_range_coverage != "complete_current_revision"
                or self.pdf_signature.post_signing_changes != "none"
                or self.pdf_signature.certificate_chain_trust != "trusted"
                or self.pdf_signature.revocation_status != "good"
            )
        ):
            raise ValueError("unknown or failed digital validation cannot become complete")
        return self


class EvidenceReviewDecisionRequest(Contract):
    request_id: UUID
    expected_input_hash: Sha256
    expected_review_id: UUID | None
    decision: Literal["confirm_support", "reject_support", "reopen"]
    reason: NonBlank


class EvidenceReviewDecision(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    evidence_check_id: UUID
    expected_input_hash: Sha256
    prior_decision_id: UUID | None
    decision: Literal["confirm_support", "reject_support", "reopen"]
    reason: NonBlank
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True
    # Review confirmation never confirms Evidence for draft/export.
    eligible_for_draft_export: Literal[False] = False


class ClaimEvidenceCheck(Contract):
    id: UUID
    report_id: UUID
    tender_support: list[TenderSupport] = Field(min_length=1, max_length=20)
    claim: UploadedBidCitation
    evidence_regions: list[BidPageRegion] = Field(min_length=1, max_length=20)
    parameter_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    product_model_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    holder_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    manufacturer_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    assessment_date: date
    validity_covers_date: Literal["yes", "no", "unknown", "not_applicable"]
    basis: FindingBasis
    support_status: Literal["supports", "contradicts", "insufficient", "unknown"]
    review_state: Literal["pending", "confirmed_support", "rejected_support", "reopened"] = (
        "pending"
    )
    latest_review_id: UUID | None = None
    existing_evidence_citations: list[VerifiedCitation] = Field(default_factory=list, max_length=20)
    finding_ids: list[UUID] = Field(default_factory=list, max_length=20)
    eligible_for_draft_export: Literal[False] = False

    @model_validator(mode="after")
    def evidence_confirmation_is_separate(self) -> Self:
        if any(region.page.role != "bid" for region in self.evidence_regions):
            raise ValueError("evidence regions must address uploaded bid pages")
        if (self.review_state == "pending") != (self.latest_review_id is None):
            raise ValueError("reviewed evidence support needs the exact decision")
        if any(citation.kind != "evidence" for citation in self.existing_evidence_citations):
            raise ValueError("existing chain references allow only verified Evidence citations")
        if self.review_state == "confirmed_support" and self.support_status != "supports":
            raise ValueError("confirmed support requires a supporting observation")
        if self.support_status == "supports" and (
            "mismatched"
            in (
                self.parameter_match,
                self.product_model_match,
                self.holder_match,
                self.manufacturer_match,
            )
            or "unknown"
            in (
                self.parameter_match,
                self.product_model_match,
                self.holder_match,
                self.manufacturer_match,
            )
            or self.validity_covers_date in {"no", "unknown"}
        ):
            raise ValueError("mutated, unknown or out-of-date evidence cannot support the claim")
        return self


class CompetitorPrice(Contract):
    """Private human-supplied opening data; neutral ref avoids competitor identities."""

    ref: str = Field(pattern=r"^competitor-[1-9][0-9]{0,3}$")
    amount: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source: NonBlank
    eligibility: Literal["valid", "invalid", "unknown"] = "unknown"
    included_in_formula: bool = False
    inclusion_rule: Source | None = None
    inclusion_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def explicit_price_eligibility(self) -> Self:
        if self.included_in_formula and (
            self.eligibility != "valid" or self.inclusion_rule is None
        ):
            raise ValueError("included competitor price needs valid eligibility and cited rule")
        if not self.included_in_formula and self.inclusion_reason is None:
            raise ValueError("excluded or unknown price needs its explicit reason")
        return self


class OpeningPriceCreate(Contract):
    request_id: UUID
    expected_submission_manifest_sha256: Sha256
    opened_at: AwareDatetime
    own_price: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    competitors: list[CompetitorPrice] = Field(min_length=1, max_length=1000)
    reason: NonBlank

    @model_validator(mode="after")
    def same_currency_and_unique_refs(self) -> Self:
        if any(price.currency != self.currency for price in self.competitors):
            raise ValueError("opening prices must have one currency without conversion")
        if len({price.ref for price in self.competitors}) != len(self.competitors):
            raise ValueError("competitor price refs must be unique")
        return self


class PriceReleaseRequest(Contract):
    request_id: UUID
    expected_revision: Revision
    expected_submission_manifest_sha256: Sha256
    allow_external_price_pages: bool
    pages: list[BidPageRef] = Field(min_length=1, max_length=PAGE_LIMIT)
    provider_bindings_sha256: Sha256
    purpose: Literal["price_compliance", "price_score"]
    sanitized_price_context_sha256: Sha256
    image_privacy_manifest_sha256: Sha256
    reason: NonBlank


class PriceReleaseView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    submission_manifest_sha256: Sha256
    revision: Revision
    prior_release_id: UUID | None
    allow_external_price_pages: bool
    pages: list[BidPageRef] = Field(min_length=1, max_length=PAGE_LIMIT)
    provider_bindings_sha256: Sha256
    purpose: Literal["price_compliance", "price_score"]
    sanitized_price_context_sha256: Sha256
    image_privacy_manifest_sha256: Sha256
    released_by: UUID
    released_at: AwareDatetime
    reason_sha256: Sha256
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True


class OpeningPriceInput(Contract):
    opened_at: AwareDatetime
    own_price: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    competitors: list[CompetitorPrice] = Field(min_length=1, max_length=1000)
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    supplied_by: UUID
    human_submission_receipt_id: UUID
    input_sha256: Sha256

    @model_validator(mode="after")
    def same_currency_and_unique_refs(self) -> Self:
        if any(price.currency != self.currency for price in self.competitors):
            raise ValueError("opening prices must have one currency without conversion")
        if len({price.ref for price in self.competitors}) != len(self.competitors):
            raise ValueError("competitor price refs must be unique")
        return self


class BidReviewScorePart(Contract):
    key: NonBlank
    part: Literal["technical", "commercial", "price"]
    maximum: Points
    estimate_low: Points | None
    estimate_high: Points | None
    status: Literal["assessed", "unavailable", "not_requested"]
    basis: list[TenderSupport] = Field(default_factory=list, max_length=20)
    bid_support: list[BidSupport] = Field(default_factory=list, max_length=20)
    deduction_reasons: list[NonBlank] = Field(default_factory=list, max_length=100)
    unassessable_codes: list[str] = Field(default_factory=list, max_length=100)
    requires_competitor_prices: bool = False
    opening_price_input_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def bounded_cited_score(self) -> Self:
        if self.status == "assessed":
            if (
                self.estimate_low is None
                or self.estimate_high is None
                or not 0 <= self.estimate_low <= self.estimate_high <= self.maximum
                or not self.basis
                or not self.bid_support
            ):
                raise ValueError("assessed score needs bounded range and tender/bid basis")
            if self.requires_competitor_prices and self.opening_price_input_sha256 is None:
                raise ValueError("comparative scoring requires explicit opening prices")
            if self.estimate_low < self.maximum and not self.deduction_reasons:
                raise ValueError("a below-maximum estimate needs its deduction basis")
        elif self.estimate_low is not None or self.estimate_high is not None:
            raise ValueError("unavailable or unrequested scores cannot invent points")
        if self.status == "unavailable" and not self.unassessable_codes:
            raise ValueError("unavailable scores must state why")
        if (
            self.part == "price"
            and self.status == "assessed"
            and self.opening_price_input_sha256 is None
        ):
            raise ValueError("price scoring requires human-supplied opening prices")
        return self


class BidReviewScoreSummary(Contract):
    parts: list[BidReviewScorePart] = Field(default_factory=list, max_length=100)
    scope: Literal["not_requested", "technical_commercial", "all_parts"]
    total_low: Points | None
    total_high: Points | None
    assessed_subtotal_low: Points
    assessed_subtotal_high: Points
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def no_partial_total(self) -> Self:
        if (self.total_low is None) != (self.total_high is None):
            raise ValueError("total range endpoints must be paired")
        if self.total_low is not None and (
            self.scope != "all_parts"
            or not self.parts
            or any(part.status != "assessed" for part in self.parts)
        ):
            raise ValueError("total requires every included part assessed")
        if (
            self.total_low is not None
            and self.total_high is not None
            and self.total_low > self.total_high
        ):
            raise ValueError("total range is reversed")
        if self.assessed_subtotal_low > self.assessed_subtotal_high:
            raise ValueError("assessed subtotal range is reversed")
        if self.scope == "not_requested" and (
            self.parts
            or self.total_low is not None
            or self.assessed_subtotal_low != 0
            or self.assessed_subtotal_high != 0
        ):
            raise ValueError("unrequested scoring has no invented score parts")
        return self


class BidReviewLimits(Contract):
    files: int = Field(default=FILE_LIMIT, strict=True, ge=2, le=FILE_LIMIT)
    file_bytes: int = Field(default=FILE_BYTE_LIMIT, strict=True, ge=1, le=FILE_BYTE_LIMIT)
    submission_bytes: int = Field(
        default=SUBMISSION_BYTE_LIMIT, strict=True, ge=1, le=SUBMISSION_BYTE_LIMIT
    )
    pages: int = Field(default=PAGE_LIMIT, strict=True, ge=1, le=PAGE_LIMIT)
    external_calls: int = Field(default=CALL_LIMIT, strict=True, ge=0, le=CALL_LIMIT)
    llm_calls: int = Field(default=100, strict=True, ge=0, le=100)
    ocr_calls: int = Field(default=60, strict=True, ge=0, le=60)
    vision_calls: int = Field(default=40, strict=True, ge=0, le=40)
    retries_count_toward_caps: Literal[True] = True


class BidReviewRequest(Contract):
    clef_enabled: bool = True
    presence_authorization_id: UUID | None = None
    request_id: UUID
    submission_id: UUID
    assessment_date: date
    scope: Literal["uploaded_bid"] = "uploaded_bid"
    review_slice: ReviewSlice = "compliance"
    rubric_id: UUID | None = None
    opening_price_input_id: UUID | None = None
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    preflight_token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    max_charge: (
        Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8, allow_inf_nan=False)] | None
    ) = None

    @model_validator(mode="after")
    def explicit_submit_and_scoring_scope(self) -> Self:
        if not self.dry_run and (self.expected_input_hash is None or self.preflight_token is None):
            raise ValueError("submission requires expected_input_hash from preflight")
        if self.dry_run and (
            self.retry or self.expected_input_hash is not None or self.preflight_token is not None
        ):
            raise ValueError("dry run cannot carry submit-only fields")
        if self.review_slice == "compliance" and (
            self.rubric_id is not None or self.opening_price_input_id is not None
        ):
            raise ValueError("compliance slice does not accept scoring inputs")
        if self.review_slice == "with_scoring" and self.rubric_id is None:
            raise ValueError("scoring requires an explicitly confirmed rubric")
        return self


class OutboundAuthorizationRequest(Contract):
    request_id: UUID
    expected_revision: Revision
    expected_authorization_id: UUID | None
    expected_submission_manifest_sha256: Sha256
    expected_preparation_input_hash: Sha256
    expected_redaction_manifest_sha256: Sha256
    authorized_sanitized_context_sha256: Sha256
    provider_bindings_sha256: Sha256
    allow_external: bool
    purposes: list[Literal["extraction", "mapping", "compliance", "evidence", "scoring"]] = Field(
        min_length=1, max_length=5
    )
    allowed_capabilities: list[Literal["llm", "ocr", "vision"]] = Field(min_length=1, max_length=3)
    reason: NonBlank

    @model_validator(mode="after")
    def unique_capabilities(self) -> Self:
        if len(set(self.allowed_capabilities)) != len(self.allowed_capabilities):
            raise ValueError("outbound capabilities must be unique")
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("outbound purposes must be unique")
        return self


class OutboundAuthorizationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    submission_manifest_sha256: Sha256
    preparation_input_hash: Sha256
    redaction_manifest_sha256: Sha256
    authorized_sanitized_context_sha256: Sha256
    provider_bindings_sha256: Sha256
    revision: Revision
    prior_authorization_id: UUID | None
    allow_external: bool
    purposes: list[Literal["extraction", "mapping", "compliance", "evidence", "scoring"]] = Field(
        min_length=1, max_length=5
    )
    allowed_capabilities: list[Literal["llm", "ocr", "vision"]] = Field(min_length=1, max_length=3)
    authorized_by: UUID
    authorized_at: AwareDatetime
    reason_sha256: Sha256
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True
    price_pages_included: Literal[False] = False


class BidReviewManifest(Contract):
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    submission_revision: Revision
    submission_manifest_sha256: Sha256
    input_hash: Sha256
    scope: Literal["uploaded_bid"] = "uploaded_bid"
    review_slice: ReviewSlice
    assessment_date: date
    tender_document_ids: list[UUID] = Field(min_length=1, max_length=FILE_LIMIT - 1)
    bid_document_ids: list[UUID] = Field(min_length=1, max_length=FILE_LIMIT - 1)
    page_count: PageNumber
    rule_version: NonBlank
    prompt_version: NonBlank
    schema_version: NonBlank
    parser_identity: NonBlank
    pdf_signature_validator_identity: NonBlank
    trust_store_sha256: Sha256
    redaction_revision: Revision
    redaction_rule_version: NonBlank
    confidential_binding_sha256: Sha256
    derived_name_lists_sha256: Sha256
    image_privacy_manifest_sha256: Sha256
    provider_bindings_sha256: Sha256
    outbound_authorization_id: UUID
    authorized_sanitized_context_sha256: Sha256
    price_revision_sha256: Sha256
    rubric_id: UUID | None = None
    rubric_input_hash: Sha256 | None = None
    opening_price_input_sha256: Sha256 | None = None
    price_release_id: UUID | None = None
    clef_enabled: bool = True
    limits: BidReviewLimits = Field(default_factory=BidReviewLimits)

    @model_validator(mode="after")
    def distinct_documents_and_pinned_scoring(self) -> Self:
        ids = self.tender_document_ids + self.bid_document_ids
        if len(ids) > FILE_LIMIT or len(set(ids)) != len(ids):
            raise ValueError("document IDs must be unique and within the file limit")
        if (self.rubric_id is None) != (self.rubric_input_hash is None):
            raise ValueError("rubric ID and fixed input hash must be paired")
        if self.review_slice == "with_scoring" and self.rubric_id is None:
            raise ValueError("scoring manifest requires the confirmed rubric binding")
        if self.review_slice == "compliance" and (
            self.rubric_id is not None or self.opening_price_input_sha256 is not None
        ):
            raise ValueError("compliance manifest has no scoring inputs")
        return self


class BidReviewPreview(Contract):
    dry_run: Literal[True] = True
    input: BidReviewManifest
    budget: BudgetPreflightData
    expires_at: AwareDatetime
    preflight_token: str = Field(min_length=1, max_length=4096, repr=False)
    preflight_ttl_seconds: Literal[900] = PREFLIGHT_TTL_SECONDS
    external_price_pages: bool = False
    redacted_counts: dict[str, int] = Field(default_factory=dict)
    image_redaction_count: int = Field(default=0, strict=True, ge=0)
    admission_blockers: list[str] = Field(default_factory=list, max_length=100)
    uncovered_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def same_budget_input_and_expiry(self) -> Self:
        if (
            self.budget.task_id != self.input.task_id
            or self.budget.input_hash != self.input.input_hash
        ):
            raise ValueError("budget preflight must bind the review input and task")
        if (self.expires_at - self.budget.as_of).total_seconds() != PREFLIGHT_TTL_SECONDS:
            raise ValueError("preflight expiry must be 15 minutes after its budget snapshot")
        if any(count < 0 for count in self.redacted_counts.values()):
            raise ValueError("redaction counts cannot be negative")
        if self.external_price_pages and self.input.price_release_id is None:
            raise ValueError("external price pages require the human release")
        return self


class BidReviewRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    run_id: UUID
    input: BidReviewManifest
    created_at: AwareDatetime
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    invalidation_codes: list[str] = Field(default_factory=list, max_length=100)
    uncovered_codes: list[str] = Field(default_factory=list, max_length=100)
    usage_record_ids: list[UUID] = Field(default_factory=list, max_length=CALL_LIMIT)
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def same_org_task_and_stale_codes(self) -> Self:
        if (self.org_id, self.task_id) != (self.input.org_id, self.input.task_id):
            raise ValueError("run must bind its manifest org and task")
        if (self.validity == "stale") != bool(self.invalidation_codes):
            raise ValueError("stale validity and reasons must agree")
        return self


class BidReviewOverallConclusion(Contract):
    rejection_items: Literal["found", "none_found", "unassessable"]
    biggest_risk: NonBlank
    remediation_summary: NonBlank
    final_authority: Literal["评标委员会决定最终评审结果；本报告仅供辅助审查。"] = (
        "评标委员会决定最终评审结果；本报告仅供辅助审查。"
    )


class BidReviewReportArtifact(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    format: Literal["docx", "console"]
    sha256: Sha256
    size_bytes: int = Field(strict=True, gt=0, le=FILE_BYTE_LIMIT)
    report_input_hash: Sha256
    decisions_snapshot_sha256: Sha256
    renderer_identity: NonBlank
    created_at: AwareDatetime
    human_only: Literal[True] = True
    advisory_only: Literal[True] = True


class BidReviewBasicInformation(Contract):
    task_id: UUID
    submission_id: UUID
    tender_number: str | None = Field(default=None, max_length=200)
    bidder_name: str | None = Field(default=None, max_length=200)
    assessment_date: date
    bid_deadline: AwareDatetime | None = None
    tender_documents: list[UUID] = Field(min_length=1, max_length=FILE_LIMIT - 1)
    bid_documents: list[UUID] = Field(min_length=1, max_length=FILE_LIMIT - 1)


class BidComplianceCheck(Contract):
    id: UUID
    report_id: UUID
    clause_kind: Literal["qualification", "conformity", "starred", "triangle", "quotation"]
    outcome: Literal["responded", "deviation", "missing", "unknown"]
    tender_support: list[TenderSupport] = Field(min_length=1, max_length=20)
    bid_support: list[BidSupport] = Field(default_factory=list, max_length=20)
    absence_search: AbsenceSearch | None = None
    basis: FindingBasis
    finding_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def coverage_never_implies_missing_quote(self) -> Self:
        if self.outcome in {"responded", "deviation"} and not self.bid_support:
            raise ValueError("response assessment needs original bid support")
        if self.outcome == "missing" and self.absence_search is None:
            raise ValueError("missing response needs the bounded bid search record")
        if self.outcome == "unknown" and not self.limitation_codes:
            raise ValueError("unknown compliance must state the coverage limitation")
        return self


class BidReviewReport(Contract):
    """Protected report; never use this full model for the token read projection."""

    run: BidReviewRunView
    sections: list[ReportSection] = Field(min_length=9, max_length=9)
    overall: BidReviewOverallConclusion
    basic_information: BidReviewBasicInformation
    compliance_checks: list[BidComplianceCheck] = Field(default_factory=list, max_length=2000)
    findings: list[BidReviewFinding] = Field(default_factory=list, max_length=10_000)
    signing_requirements: list[SignatureRequirement] = Field(default_factory=list, max_length=2000)
    signature_checks: list[SignatureCheck] = Field(default_factory=list, max_length=10_000)
    evidence_checks: list[ClaimEvidenceCheck] = Field(default_factory=list, max_length=2000)
    scores: BidReviewScoreSummary
    models_used: list[ProviderUsage] = Field(default_factory=list, max_length=CALL_LIMIT)
    cost: Cost
    artifact_status: Literal["pending", "ready", "failed"] = "pending"
    artifacts: list[BidReviewReportArtifact] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def fixed_sections_artifacts_and_parents(self) -> Self:
        expected = [
            "一、总体结论",
            "二、基本信息",
            "三、废标判定",
            "签章校验",
            "四、高风险缺陷",
            "五、得分预估",
            "六、证据核对",
            "七、补救清单",
            "八、检验说明",
        ]
        if self.sections != expected:
            raise ValueError("report must retain the fixed section order")
        if self.artifact_status == "ready":
            if len(self.artifacts) != 2 or {item.format for item in self.artifacts} != {
                "docx",
                "console",
            }:
                raise ValueError("published report artifacts require Word and console together")
        elif self.artifacts:
            raise ValueError("incomplete artifact jobs cannot claim successful artifacts")
        if (self.basic_information.task_id, self.basic_information.submission_id) != (
            self.run.task_id,
            self.run.input.submission_id,
        ):
            raise ValueError("basic information must bind the exact report submission")
        for finding in self.findings:
            if (finding.org_id, finding.task_id, finding.report_id) != (
                self.run.org_id,
                self.run.task_id,
                self.run.id,
            ):
                raise ValueError("finding must bind the report org, task and ID")
        for child in [*self.compliance_checks, *self.signature_checks, *self.evidence_checks]:
            if child.report_id != self.run.id:
                raise ValueError("check must bind the same report")
        if len({artifact.decisions_snapshot_sha256 for artifact in self.artifacts}) > 1:
            raise ValueError("Word and console must use the same decision snapshot")
        if len({artifact.renderer_identity for artifact in self.artifacts}) > 1:
            raise ValueError("Word and console must use the same report renderer identity")
        for artifact in self.artifacts:
            if (
                artifact.org_id,
                artifact.task_id,
                artifact.report_id,
                artifact.report_input_hash,
            ) != (
                self.run.org_id,
                self.run.task_id,
                self.run.id,
                self.run.input.input_hash,
            ):
                raise ValueError("artifact must bind the exact report input")
        if self.run.input.review_slice == "compliance" and self.scores.scope != "not_requested":
            raise ValueError("compliance report cannot claim scores")
        return self


class BidReportRenderRequest(Contract):
    request_id: UUID
    report_id: UUID
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    preflight_token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    expected_decisions_snapshot_sha256: Sha256

    @model_validator(mode="after")
    def render_preview_binding(self) -> Self:
        if not self.dry_run and (self.expected_input_hash is None or self.preflight_token is None):
            raise ValueError("report rendering requires the preflight hash")
        if self.dry_run and (
            self.retry or self.expected_input_hash is not None or self.preflight_token is not None
        ):
            raise ValueError("report dry-run cannot carry submission fields")
        return self


class BidReportRenderPreview(Contract):
    dry_run: Literal[True] = True
    report_id: UUID
    input_hash: Sha256
    report_input_hash: Sha256
    decisions_snapshot_sha256: Sha256
    renderer_identity: NonBlank
    budget: BudgetPreflightData
    expires_at: AwareDatetime
    preflight_token: str = Field(min_length=1, max_length=4096, repr=False)
    formats: tuple[Literal["docx"], Literal["console"]] = ("docx", "console")

    @model_validator(mode="after")
    def exact_render_and_zero_calls(self) -> Self:
        if self.budget.input_hash != self.input_hash or self.budget.planned_calls != 0:
            raise ValueError("render preview needs the exact hash and zero calls")
        if self.budget.next_call is not None or self.budget.estimate.task_amount != 0:
            raise ValueError("local report rendering has no paid call liability")
        if (self.expires_at - self.budget.as_of).total_seconds() != PREFLIGHT_TTL_SECONDS:
            raise ValueError("report preview expires after 15 minutes")
        return self


class BidReviewListQuery(Contract):
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class BidReviewSafeProjection(Contract):
    """Token-safe metadata, with no names, text, prices, source boxes or download URLs."""

    report_id: UUID
    task_id: UUID
    submission_id: UUID
    scope: Literal["uploaded_bid"] = "uploaded_bid"
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    finding_counts: dict[Literal["fatal", "high", "medium"], int]
    uncovered_codes: list[str] = Field(default_factory=list, max_length=100)
    input_hash: Sha256
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def nonnegative_counts(self) -> Self:
        if any(count < 0 for count in self.finding_counts.values()):
            raise ValueError("finding counts cannot be negative")
        return self


class BidReviewJobResult(AssessmentJobResult):
    scope: Literal["uploaded_bid"] = "uploaded_bid"
    submission_id: UUID
    findings: int = Field(strict=True, ge=0)
    uncovered_items: int = Field(strict=True, ge=0)
    protected_report_available: bool
    budget: BudgetJobResult


class ClefNoulQuestion(Contract):
    type: Literal["noul"] = "noul"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank


class ClefChoiceQuestion(Contract):
    type: Literal["choice"] = "choice"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank
    choices: list[NonBlank] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_choices(self) -> Self:
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("choice alternatives must be unique")
        return self


class ClefScoreQuestion(Contract):
    type: Literal["score"] = "score"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    question: NonBlank
    # Ordered criterion labels define the ordinal scale 0 .. len(criteria)-1.
    criteria: list[NonBlank] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_criteria(self) -> Self:
        if len(set(self.criteria)) != len(self.criteria):
            raise ValueError("score criterion labels must be unique and ordered")
        return self


ClefQuestion = Annotated[
    ClefNoulQuestion | ClefChoiceQuestion | ClefScoreQuestion, Field(discriminator="type")
]


class ClefRedactedImage(Contract):
    ref: str = Field(pattern=r"^image[1-4]$")
    sha256: Sha256
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(strict=True, gt=0, le=CLEF_IMAGE_BYTE_LIMIT)
    width_px: int = Field(strict=True, ge=1, le=8192)
    height_px: int = Field(strict=True, ge=1, le=8192)
    privacy_receipt_sha256: Sha256
    identifying_qr_redacted: Literal[True] = True
    identity_cards_redacted: Literal[True] = True
    seal_text_redacted_for_presence: Literal[True] = True
    price_page: Literal[False] = False

    @model_validator(mode="after")
    def clef_pixel_bound(self) -> Self:
        if self.width_px * self.height_px > CLEF_IMAGE_PIXEL_LIMIT:
            raise ValueError("Clef image exceeds its 16 MP limit")
        return self


class ClefTriageRequest(Contract):
    """Internal request; adapter emits local refs/state and redacted image bytes only."""

    model: Literal["@cf/cloudflare/clef"] = "@cf/cloudflare/clef"
    purpose: Literal[
        "seal_present",
        "signature_present",
        "date_filled",
        "page_document_type",
        "certificate_or_not",
        "image_supports_claim",
    ]
    state: (
        Annotated[str, Field(min_length=1, max_length=200_000)]
        | dict[str, JsonValue]
        | list[JsonValue]
    )
    questions: list[ClefQuestion] = Field(min_length=1, max_length=64)
    images: list[ClefRedactedImage] = Field(default_factory=list, max_length=CLEF_IMAGE_LIMIT)
    outbound_sha256: Sha256
    text_redaction_sha256: Sha256
    triage_only: Literal[True] = True

    @model_validator(mode="after")
    def unique_refs_and_image_budget(self) -> Self:
        try:
            state_bytes = json.dumps(self.state, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, RecursionError) as error:
            raise ValueError("triage state must be finite bounded JSON") from error
        if len(state_bytes) > 200_000:
            raise ValueError("triage state exceeds the serialized text budget")
        refs = [question.ref for question in self.questions]
        images = [image.ref for image in self.images]
        if len(set(refs)) != len(refs) or len(set(images)) != len(images):
            raise ValueError("triage refs must be unique")
        if sum(image.size_bytes for image in self.images) > CLEF_IMAGES_BYTE_LIMIT:
            raise ValueError("Clef decoded images exceed the aggregate 8 MiB limit")
        return self


class ClefNoulAnswer(Contract):
    type: Literal["noul"] = "noul"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    # Normalized internal projection of the vendor's single yes probability.
    probability_yes: Probability


class ClefChoiceAnswer(Contract):
    type: Literal["choice"] = "choice"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    probabilities: list[Probability] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def normalized_probabilities(self) -> Self:
        if abs(sum(self.probabilities) - 1) > 1e-6:
            raise ValueError("choice probabilities must sum to one")
        return self


class ClefScoreAnswer(Contract):
    type: Literal["score"] = "score"
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,2}$")
    criteria_probabilities: list[Probability] = Field(min_length=2, max_length=20)
    mean: float = Field(ge=0, le=19, allow_inf_nan=False)

    @model_validator(mode="after")
    def probability_weighted_mean(self) -> Self:
        if abs(sum(self.criteria_probabilities) - 1) > 1e-6:
            raise ValueError("score criterion probabilities must sum to one")
        expected = sum(
            index * probability for index, probability in enumerate(self.criteria_probabilities)
        )
        if abs(self.mean - expected) > 1e-6:
            raise ValueError("score mean must derive from ordered criterion probabilities")
        return self


ClefAnswer = Annotated[
    ClefNoulAnswer | ClefChoiceAnswer | ClefScoreAnswer, Field(discriminator="type")
]


class ClefFixedCallQuote(Contract):
    """Fixed catalog charge, settled per dispatched call independent of usage tokens."""

    quote: BudgetCallQuote
    charge_basis: Literal["fixed_per_call"] = "fixed_per_call"
    platform_config_revision: Revision
    # Returned input_tokens remain telemetry and never recalculate this amount.
    returned_tokens_affect_charge: Literal[False] = False

    @model_validator(mode="after")
    def pinned_platform_quote(self) -> Self:
        if (
            self.quote.capability != "vision"
            or self.quote.payer != "org_platform"
            or self.quote.model != "@cf/cloudflare/clef"
            or self.quote.platform_model_id is None
            or self.quote.reserved_task_amount is None
            or self.quote.image_count > CLEF_IMAGE_LIMIT
            or self.quote.unknown_reason is not None
        ):
            raise ValueError("Clef requires a known explicit platform vision call quote")
        return self


class ClefGatewayCheck(Contract):
    """AI Gateway state read through its API at configuration/test time; dispatch requires it."""

    gateway_id: str = Field(min_length=1, max_length=64)
    authentication: Literal[True]
    collect_logs: Literal[False]
    logpush: Literal[False]
    cache_ttl: Literal[0]
    # The application owns retries; each retry is a separately admitted call.
    gateway_retries: Literal[False]
    rate_limit_requests: int = Field(gt=0)
    rate_limit_seconds: int = Field(gt=0)
    workers_ai_billing_mode: Literal["unified"]
    checked_at: AwareDatetime


class ClefTriageResult(Contract):
    """Normalized internal response, not Cloudflare vendor wire JSON."""

    request_sha256: Sha256
    answers: list[ClefAnswer] = Field(min_length=1, max_length=64)
    usage: ProviderUsage
    fixed_quote: ClefFixedCallQuote
    triage_only: Literal[True] = True
    contains_reasons_or_citations: Literal[False] = False

    @model_validator(mode="after")
    def quote_matches_result(self) -> Self:
        if self.request_sha256 != self.fixed_quote.quote.request_sha256:
            raise ValueError("triage result must bind the admitted request")
        if len({answer.ref for answer in self.answers}) != len(self.answers):
            raise ValueError("duplicate triage answers are invalid")
        return self


class ReviewProviderText(Contract):
    """Outbound local refs; source IDs and original text are resolved only server-side."""

    ref: str = Field(pattern=r"^[tb][1-9][0-9]{0,5}$")
    role: DocumentRole
    text: str = Field(min_length=1, max_length=200_000)
    sent_sha256: Sha256


class ReviewProviderQuestion(Contract):
    ref: str = Field(pattern=r"^q[1-9][0-9]{0,5}$")
    instruction: NonBlank
    tender_refs: list[str] = Field(default_factory=list, max_length=2000)
    bid_refs: list[str] = Field(default_factory=list, max_length=2000)
    rubric_ref: str | None = Field(default=None, min_length=1, max_length=100)


class BidReviewProviderRequest(Contract):
    assessment_date: date
    operation: Literal["extract_requirements", "map_responses", "compliance", "evidence", "score"]
    texts: list[ReviewProviderText] = Field(min_length=1, max_length=2000)
    questions: list[ReviewProviderQuestion] = Field(min_length=1, max_length=2000)
    input_sha256: Sha256

    @model_validator(mode="after")
    def unique_local_refs(self) -> Self:
        refs = [text.ref for text in self.texts]
        question_refs = [question.ref for question in self.questions]
        if len(set(refs)) != len(refs) or len(set(question_refs)) != len(question_refs):
            raise ValueError("request refs must be unique")
        allowed = {text.ref: text.role for text in self.texts}
        for question in self.questions:
            if any(allowed.get(ref) != "tender" for ref in question.tender_refs):
                raise ValueError("question tender refs must bind sent tender text")
            if any(allowed.get(ref) != "bid" for ref in question.bid_refs):
                raise ValueError("question bid refs must bind sent bid text")
        return self


class ReviewModelReference(Contract):
    ref: str = Field(pattern=r"^[tb][1-9][0-9]{0,5}$")
    quote: NonBlank


class ReviewModelObservation(Contract):
    question_ref: NonBlank
    outcome: Literal["satisfied", "deviation", "missing", "unknown"]
    confidence: Probability
    explanation: NonBlank
    references: list[ReviewModelReference] = Field(default_factory=list, max_length=40)


class ReviewRequirementProposal(Contract):
    ref: str = Field(pattern=r"^requirement-[1-9][0-9]{0,5}$")
    category: Category
    text: NonBlank
    starred: bool = False
    triangle: bool = False
    tender_references: list[ReviewModelReference] = Field(min_length=1, max_length=20)


class ReviewSigningProposal(Contract):
    ref: str = Field(pattern=r"^signing-[1-9][0-9]{0,5}$")
    requirement_ref: str = Field(pattern=r"^requirement-[1-9][0-9]{0,5}$")
    required: Literal[
        "company_seal",
        "legal_representative_signature",
        "authorized_agent_signature",
        "personal_seal",
        "date",
        "seam_seal",
        "every_page_electronic_seal",
        "pdf_digital_signature",
    ]
    tender_references: list[ReviewModelReference] = Field(min_length=1, max_length=20)
    location_description: NonBlank
    location_rule: Literal["specified_location", "each_document", "every_page", "seam_group"]
    bid_target_refs: list[str] = Field(default_factory=list, max_length=PAGE_LIMIT)
    mapping_status: Literal["mapped", "unknown"]
    date_required: bool
    confidence: Probability


class ReviewResponseMappingProposal(Contract):
    requirement_ref: str = Field(pattern=r"^requirement-[1-9][0-9]{0,5}$")
    outcome: Literal["mapped", "missing", "unknown"]
    bid_references: list[ReviewModelReference] = Field(default_factory=list, max_length=20)
    confidence: Probability


class ReviewEvidenceProposal(Contract):
    question_ref: NonBlank
    claim_ref: str = Field(pattern=r"^b[1-9][0-9]{0,5}$")
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    support_status: Literal["supports", "contradicts", "insufficient", "unknown"]
    parameter_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    product_model_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    holder_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    manufacturer_match: Literal["matched", "mismatched", "unknown", "not_applicable"]
    validity_covers_date: Literal["yes", "no", "unknown", "not_applicable"]
    references: list[ReviewModelReference] = Field(min_length=1, max_length=40)
    explanation: NonBlank
    confidence: Probability


class ReviewScoreProposal(Contract):
    question_ref: NonBlank
    rubric_ref: NonBlank
    status: Literal["assessed", "unavailable"]
    estimate_low: Points | None
    estimate_high: Points | None
    references: list[ReviewModelReference] = Field(default_factory=list, max_length=40)
    deduction_reasons: list[NonBlank] = Field(default_factory=list, max_length=20)
    unavailable_codes: list[str] = Field(default_factory=list, max_length=100)
    confidence: Probability

    @model_validator(mode="after")
    def proposed_range(self) -> Self:
        if self.status == "assessed":
            if (
                self.estimate_low is None
                or self.estimate_high is None
                or self.estimate_low > self.estimate_high
                or not self.references
            ):
                raise ValueError("proposed score needs ordered endpoints and references")
        elif (
            self.estimate_low is not None
            or self.estimate_high is not None
            or not self.unavailable_codes
        ):
            raise ValueError("unavailable score retains codes and no invented points")
        return self


class BidReviewProviderResult(Contract):
    operation: Literal["extract_requirements", "map_responses", "compliance", "evidence", "score"]
    observations: list[ReviewModelObservation] = Field(default_factory=list, max_length=2000)
    requirements: list[ReviewRequirementProposal] = Field(default_factory=list, max_length=2000)
    signing: list[ReviewSigningProposal] = Field(default_factory=list, max_length=2000)
    mappings: list[ReviewResponseMappingProposal] = Field(default_factory=list, max_length=2000)
    evidence: list[ReviewEvidenceProposal] = Field(default_factory=list, max_length=2000)
    scores: list[ReviewScoreProposal] = Field(default_factory=list, max_length=2000)
    usage: ProviderUsage

    @model_validator(mode="after")
    def outputs_match_operation(self) -> Self:
        permitted = {
            "extract_requirements": {"requirements", "signing"},
            "map_responses": {"mappings", "signing"},
            "compliance": {"observations"},
            "evidence": {"evidence"},
            "score": {"scores"},
        }[self.operation]
        for field in {"requirements", "signing", "mappings", "observations", "evidence", "scores"}:
            if field not in permitted and getattr(self, field):
                raise ValueError("provider output branch does not match the requested operation")
        return self


class BidReviewProvider(Protocol):
    """Configurable LLM boundary; local ref/quote acceptance precedes persistence.

    Adapters re-use pinned resolve_llm configuration and accounted_call. Complete
    requests, retries and usage participate in existing admission and fixed caps.
    """

    def quote(self, request: BidReviewProviderRequest) -> BudgetCallQuote: ...

    async def review(self, request: BidReviewProviderRequest) -> BidReviewProviderResult: ...


class ClefTriageProvider(Protocol):
    """Default-on platform-configured triage; never a source of final findings.

    Verify every byte/hash/privacy receipt before dispatch. Reject full serialized
    requests above 13 MiB or conservative context+output above 65,536 tokens, without
    vendor truncation. Strip internal IDs/receipts; no URL images or outbound fetches.
    Validate answer refs/types against the request and score/choice bounds. Admit and
    settle the fixed platform call amount; variable returned tokens are telemetry.
    """

    def quote(
        self, request: ClefTriageRequest, redacted_images: Sequence[bytes]
    ) -> ClefFixedCallQuote: ...

    async def triage(
        self, request: ClefTriageRequest, redacted_images: Sequence[bytes]
    ) -> ClefTriageResult: ...


class LocalPDFSignatureValidator(Protocol):
    """Bounded local inspection of unchanged PDF bytes; no signing or network access.

    Use a trusted explicitly approved cryptographic verifier and local trust store.
    Unknown certificates, algorithms, revocation or malformed structures fail closed
    as unknown/invalid; visual seal presence never establishes digital validity.
    """

    async def validate(
        self, document: BidDocumentView, original: bytes, *, at: AwareDatetime
    ) -> list[PDFSignatureValidation]: ...


class BidReviewReportRenderer(Protocol):
    """Local immutable DOCX/console render; no model calls or external document fetches.

    Rendering snapshots exact findings, quotes/regions and decision history. Validate
    bytes/hash/format/size independently before publishing both artifacts atomically.
    Returned bytes are internal and never serialized in Result. Report output is not
    the export command, and cannot change response/Evidence confirmation permissions.
    """

    async def render(
        self,
        report: BidReviewReport,
        decisions: Sequence[BidReviewDecisionView],
        *,
        decisions_snapshot_sha256: Sha256,
    ) -> tuple[bytes, bytes]: ...


class BidReviewService(Protocol):
    """Authenticated HTTP/CLI service; methods recheck live org/task authorization.

    Preflight is write-free and call-free. Submission fixes the input and enqueues
    transactionally; workers recheck hashes/grants/budget before calls/publication.
    Source and report downloads stay human-only, including inherited generic routes.
    Decisions append; no card/Evidence confirmation or bid/export mutation occurs.
    Preflight tokens are signed service receipts bound to actor/org/task/hash/options
    and issued/expiry time; submitting revalidates them without persisted previews.
    Tokens may run only the exact human-authorized sanitized outbound snapshot. A
    worker decrypts fixed inputs under its job grant, never an arbitrary caller scope.
    """

    async def upload(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        body: BidSubmissionCreate,
        files: Sequence[bytes],
        storage: Storage,
    ) -> BidSubmissionUploaded | BidUploadPreview: ...

    async def prepare_preflight(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        body: BidPrepareRequest,
    ) -> BidPreparePreview: ...

    async def prepare_submit(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        body: BidPrepareRequest,
    ) -> AssessmentJobAccepted: ...

    async def submission(
        self,
        session: AsyncSession,
        actor: Identity,
        submission_id: UUID,
    ) -> BidSubmissionUploaded | BidSubmissionView: ...

    async def list_submissions(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        query: BidReviewListQuery,
    ) -> tuple[AssessmentListData, list[BidSubmissionUploaded | BidSubmissionView]]: ...

    async def list_runs(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        query: BidReviewListQuery,
    ) -> tuple[AssessmentListData, list[BidReviewSafeProjection]]: ...

    async def history(
        self,
        session: AsyncSession,
        actor: Identity,
        report_id: UUID,
        finding_id: UUID,
        query: BidReviewListQuery,
    ) -> tuple[AssessmentListData, list[BidReviewDecisionView]]: ...

    async def report_preflight(
        self,
        session: AsyncSession,
        actor: Identity,
        body: BidReportRenderRequest,
    ) -> BidReportRenderPreview: ...

    async def report_submit(
        self,
        session: AsyncSession,
        actor: Identity,
        body: BidReportRenderRequest,
    ) -> AssessmentJobAccepted: ...

    async def preflight(
        self, session: AsyncSession, actor: Identity, task_id: UUID, body: BidReviewRequest
    ) -> BidReviewPreview: ...

    async def submit(
        self, session: AsyncSession, actor: Identity, task_id: UUID, body: BidReviewRequest
    ) -> AssessmentJobAccepted: ...

    async def show(
        self, session: AsyncSession, actor: Identity, report_id: UUID
    ) -> BidReviewReport | BidReviewSafeProjection: ...

    async def decide(
        self,
        session: AsyncSession,
        actor: Identity,
        report_id: UUID,
        finding_id: UUID,
        body: BidReviewDecisionRequest,
    ) -> BidReviewDecisionView: ...

    async def evidence_review(
        self,
        session: AsyncSession,
        actor: Identity,
        report_id: UUID,
        evidence_check_id: UUID,
        body: EvidenceReviewDecisionRequest,
    ) -> EvidenceReviewDecision: ...

    async def evidence_history(
        self,
        session: AsyncSession,
        actor: Identity,
        report_id: UUID,
        evidence_check_id: UUID,
        query: BidReviewListQuery,
    ) -> tuple[AssessmentListData, list[EvidenceReviewDecision]]: ...

    async def authorize_outbound(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        submission_id: UUID,
        body: OutboundAuthorizationRequest,
    ) -> OutboundAuthorizationView: ...

    async def classify(
        self,
        session: AsyncSession,
        actor: Identity,
        report_id: UUID,
        subject_kind: Literal["finding", "evidence_check", "signature_requirement"],
        subject_id: UUID,
        body: BidReviewClassificationRequest,
    ) -> BidReviewClassificationView: ...

    async def opening_prices(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        submission_id: UUID,
        body: OpeningPriceCreate,
    ) -> OpeningPriceInput: ...

    async def release_price_pages(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        submission_id: UUID,
        body: PriceReleaseRequest,
    ) -> PriceReleaseView: ...

    async def read_original(
        self,
        session: AsyncSession,
        actor: Identity,
        document_id: UUID,
        storage: Storage,
    ) -> bytes: ...

    async def read_source(
        self,
        session: AsyncSession,
        actor: Identity,
        page_id: UUID,
        storage: Storage,
    ) -> bytes: ...

    async def read_report_artifact(
        self,
        session: AsyncSession,
        actor: Identity,
        artifact_id: UUID,
        storage: Storage,
    ) -> tuple[bytes, BidReviewReportArtifact]: ...


BidReviewResult = Result
BidReviewCost = Cost
BidReviewJobAccepted = AssessmentJobAccepted
RESULT_VERSION = CONTRACT_VERSION
