"""Immutable uploaded-bid findings and append-only human disposition contracts."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.bid_review import NonBlank, Revision
from app.schemas.bid_review_run import BidReviewCitation
from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256

type FindingState = Literal["open", "dismissed", "confirmed"]
type FindingOutcome = Literal["responded", "deviation", "missing", "unknown"]
type FindingSeverity = Literal["fatal", "high", "medium"]
type ReviewDomain = Literal["commercial", "technical"]


class FindingCitation(BidReviewCitation):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)

    @model_validator(mode="after")
    def offsets(self) -> Self:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("citation offsets must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("citation end must follow its start")
        return self


class FindingPage(Contract):
    page_id: UUID
    document_id: UUID
    page: int = Field(ge=1, le=1000)
    role: Literal["bid"] = "bid"


class FindingAbsenceSearch(Contract):
    kind: Literal["locations", "submission_inventory"]
    searched_pages: list[FindingPage] = Field(default_factory=list, max_length=1000)
    searched_regions: list[dict] = Field(default_factory=list, max_length=0)
    submission_id: UUID | None = None
    submission_manifest_sha256: Sha256 | None = None
    inspected_bid_document_ids: list[UUID] = Field(default_factory=list, max_length=19)
    required_document_description: NonBlank | None = None
    method: Literal[
        "local_text", "local_pdf", "ocr", "llm", "human", "manifest_rule", "llm_mapping"
    ]
    coverage: Literal["required_locations", "all_bid_pages", "partial", "complete_inventory"]
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def bounded_inventory(self) -> Self:
        if self.coverage == "partial" and not self.limitation_codes:
            raise ValueError("partial absence search must state limitations")
        if self.kind == "locations":
            if (
                self.submission_id
                or self.submission_manifest_sha256
                or self.inspected_bid_document_ids
                or self.required_document_description
            ):
                raise ValueError("page search cannot invent an absent document")
            if self.coverage == "complete_inventory" or (
                not self.searched_pages and self.coverage != "partial"
            ):
                raise ValueError("complete page search requires inspected pages")
            if len({p.page_id for p in self.searched_pages}) != len(self.searched_pages):
                raise ValueError("searched pages must be unique")
        elif (
            self.searched_pages
            or not self.submission_id
            or not self.submission_manifest_sha256
            or not self.required_document_description
            or not self.inspected_bid_document_ids
            or self.coverage not in {"complete_inventory", "partial"}
        ):
            raise ValueError("inventory absence needs an exact submitted document inventory")
        if len(set(self.inspected_bid_document_ids)) != len(self.inspected_bid_document_ids):
            raise ValueError("inspected document IDs must be unique")
        return self


class FindingBasis(Contract):
    kind: Literal["rule", "model"]
    rule_or_prompt_version: NonBlank
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    provider_config_id: UUID | None = None
    platform_model_id: str | None = Field(default=None, max_length=100)
    provider_revision: int | None = Field(default=None, ge=1)
    model: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def attributable(self) -> Self:
        if self.kind == "model" and (
            self.confidence is None
            or not self.model
            or self.provider_revision is None
            or not (self.provider_config_id or self.platform_model_id)
        ):
            raise ValueError("model basis requires confidence and pinned model identity")
        if self.kind == "rule" and any(
            v is not None
            for v in (
                self.confidence,
                self.provider_config_id,
                self.platform_model_id,
                self.provider_revision,
                self.model,
            )
        ):
            raise ValueError("rule basis cannot inherit model identity")
        return self


class FindingRuleEvidence(Contract):
    kind: Literal["pdf_signature_validation"]
    document_ids: list[UUID] = Field(min_length=1, max_length=19)
    validation_ids: list[UUID] = Field(min_length=1, max_length=19)

    @model_validator(mode="after")
    def paired_validations(self) -> Self:
        if (
            len(self.document_ids) != len(self.validation_ids)
            or len(set(self.document_ids)) != len(self.document_ids)
            or len(set(self.validation_ids)) != len(self.validation_ids)
        ):
            raise ValueError("local PDF evidence needs unique document and validation pairs")
        return self


class BidReviewMachineFinding(Contract):
    id: UUID
    obligation_id: UUID
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    title: NonBlank
    outcome: FindingOutcome
    severity: FindingSeverity
    impact: Literal["rejection", "lost_points", "both", "uncertain"]
    basis: FindingBasis
    rule_evidence: FindingRuleEvidence | None = None
    tender_support: list[FindingCitation] = Field(min_length=1, max_length=20)
    bid_support: list[FindingCitation] = Field(default_factory=list, max_length=20)
    absence_search: FindingAbsenceSearch | None = None
    explanation: NonBlank
    remediation: NonBlank
    limitation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def supported(self) -> Self:
        if self.outcome in {"missing", "unknown"} and self.absence_search is None:
            raise ValueError("missing and unknown outcomes need a bounded absence inventory")
        if self.outcome == "missing" and self.bid_support:
            raise ValueError("missing response cannot fabricate a response quotation")
        if self.rule_evidence and (
            self.outcome != "deviation"
            or self.basis.kind != "rule"
            or self.code != "signature_validation_invalid"
        ):
            raise ValueError("local PDF rule evidence requires the exact signature validation rule")
        if self.outcome == "responded" and not self.bid_support:
            raise ValueError("responded finding requires a bid quotation")
        if self.outcome == "deviation" and not self.bid_support and self.rule_evidence is None:
            raise ValueError("deviation requires bid support or local PDF validation")
        if not self.bid_support and self.absence_search is None and self.rule_evidence is None:
            raise ValueError(
                "finding needs bid support, local PDF validation or a bounded absence search"
            )
        if (
            self.absence_search
            and self.absence_search.coverage == "partial"
            and self.outcome != "unknown"
        ):
            raise ValueError("partial absence search is unknown")
        if self.outcome == "unknown" and not self.limitation_codes:
            raise ValueError("unknown outcome requires limitations")
        return self


class FindingReviewedBasis(Contract):
    kind: Literal["human_reviewed"] = "human_reviewed"
    human_decision_id: UUID


class BidReviewFindingView(BidReviewMachineFinding):
    review_id: UUID
    task_id: UUID
    state: FindingState = "open"
    revision: Revision = 1
    review_domain: ReviewDomain | None = None
    classification_id: UUID | None = None
    latest_decision_id: UUID | None = None
    reviewed_basis: FindingReviewedBasis | None = None
    advisory_only: Literal[True] = True


class BidReviewSafeFinding(Contract):
    id: UUID
    review_id: UUID
    task_id: UUID
    obligation_id: UUID
    code: str
    outcome: FindingOutcome
    severity: FindingSeverity
    impact: Literal["rejection", "lost_points", "both", "uncertain"]
    state: FindingState
    revision: Revision
    review_domain: ReviewDomain | None
    classification_id: UUID | None
    latest_decision_id: UUID | None
    advisory_only: Literal[True] = True


class BidReviewDecisionRequest(Contract):
    request_id: UUID
    action: Literal["dismiss", "reopen", "confirm"]
    reason: NonBlank = Field(max_length=20000, repr=False)
    expected_revision: Revision
    expected_input_hash: Sha256
    expected_decision_id: UUID | None


class BidReviewClassificationRequest(Contract):
    request_id: UUID
    review_domain: ReviewDomain
    reason: NonBlank = Field(max_length=20000, repr=False)
    expected_revision: Revision
    expected_input_hash: Sha256
    expected_decision_id: UUID | None


class BidReviewEventView(Contract):
    id: UUID
    review_id: UUID
    task_id: UUID
    finding_id: UUID
    prior_decision_id: UUID | None
    revision: int = Field(ge=2)
    action: Literal["dismiss", "reopen", "confirm", "classify"]
    review_domain: ReviewDomain
    state: FindingState
    reason: NonBlank
    reason_sha256: Sha256
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True


class BidReviewFindingsData(Contract):
    review_id: UUID
    input_hash: Sha256
    validity: Literal["current", "stale"]
    next_cursor: str | None = None


class BidReviewFindingsQuery(Contract):
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=50, ge=1, le=100)
    severity: FindingSeverity | None = None
    state: FindingState | None = None
    outcome: FindingOutcome | None = None
