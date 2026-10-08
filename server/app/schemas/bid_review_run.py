"""Privacy-gated uploaded tender extraction and signing confirmation contracts."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256


class BidReviewRequest(Contract):
    request_id: UUID
    submission_id: UUID
    assessment_date: date
    scope: Literal["uploaded_bid"] = "uploaded_bid"
    review_slice: Literal["compliance"] = "compliance"
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    clef_enabled: bool = True
    presence_authorization_id: UUID | None = None
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    preflight_token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    max_charge: (
        Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8, allow_inf_nan=False)] | None
    ) = None

    @model_validator(mode="after")
    def explicit_submit(self):
        if not self.dry_run and (self.expected_input_hash is None or self.preflight_token is None):
            raise ValueError("submission requires the preview hash and receipt")
        if self.dry_run and (self.retry or self.expected_input_hash or self.preflight_token):
            raise ValueError("preview cannot carry submit-only fields")
        return self


class BidReviewPreview(Contract):
    dry_run: Literal[True] = True
    input: dict
    budget: BudgetPreflightData
    expires_at: AwareDatetime
    preflight_token: str = Field(repr=False)
    preflight_ttl_seconds: Literal[900] = 900
    admission_blockers: list[str] = Field(default_factory=list)
    uncovered_codes: list[str] = Field(default_factory=list)
    external_price_pages: Literal[False] = False


class BidReviewCitation(Contract):
    document_id: UUID
    page_id: UUID
    page: int = Field(ge=1, le=1000)
    quote: str = Field(min_length=1, max_length=20000)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=1)
    location: dict | None = None
    page_label: Literal["original_pdf", "rendered_docx"] = "original_pdf"


class BidReviewObligation(Contract):
    id: UUID
    text: str
    category: Literal["qualification", "commercial", "technical", "substantive", "scoring"]
    starred: bool
    triangle: bool = False
    star_marker: bool = False
    rejection_trigger: bool
    citation: BidReviewCitation


class BidRequiredLocation(Contract):
    page_id: UUID | None = None
    document_id: UUID | None = None
    page: int | None = None
    status: Literal["unresolved", "triage_present", "triage_absent", "triage_uncertain"] = (
        "unresolved"
    )
    probability_yes: float | None = Field(default=None, ge=0, le=1)
    presence_probabilities: dict[str, float] = Field(default_factory=dict)
    needs_human_confirmation: Literal[True] = True
    human_escalation: bool = False
    owner_status: Literal["unresolved"] = "unresolved"
    date_status: Literal["unresolved"] = "unresolved"
    position_status: Literal["unresolved"] = "unresolved"
    group_id: str | None = None
    reason_code: str = "presence_not_checked"


class BidConfirmedSigningRequirement(Contract):
    id: UUID
    candidate_id: UUID | None = None
    applicability: Literal["applies", "not_applicable", "alternative", "unknown"]
    mark_types: list[str]
    owner_roles: list[str]
    date_required: bool
    location_rule: Literal["every_page", "seam_group", "specified", "unknown"]
    citation: BidReviewCitation | None = None
    required_locations: list[BidRequiredLocation]
    reason_code: str | None = None


class BidReviewRunView(Contract):
    id: UUID
    task_id: UUID
    submission_id: UUID
    job_id: UUID
    status: str
    completion: Literal["complete", "partial"] | None = None
    input_hash: Sha256
    created_at: AwareDatetime
    coverage: dict = Field(default_factory=dict)
    uncovered_codes: list[str] = Field(default_factory=list)
    validity: Literal["current", "stale"] = "current"
    advisory_only: Literal[True] = True
    review_slice: Literal["compliance"] = "compliance"


class BidReviewDetail(Contract):
    run: BidReviewRunView
    obligations: list[BidReviewObligation] = Field(default_factory=list)
    signing_requirements: list[BidConfirmedSigningRequirement] = Field(default_factory=list)
    next_cursor: str | None = None


class BidReviewListData(Contract):
    task_id: UUID
    next_cursor: str | None = None


class BidReviewJobResult(Contract):
    review_id: UUID
    completion: Literal["complete", "partial"]
    coverage: dict
    uncovered_codes: list[str]
    stop_reason: str | None = None
    usage_record_ids: list[UUID] = Field(default_factory=list)
