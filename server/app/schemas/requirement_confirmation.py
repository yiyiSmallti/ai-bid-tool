"""Requirement review and manual-entry contracts; authority is checked by services."""

import json
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.contracts import CONTRACT_VERSION, Category, Contract, Cost, Result, Source
from app.schemas.screenshot_contracts import Sha256
from app.schemas.team_workflow import (
    BoardBlocker,
    BoardBucket,
    BoardCounts,
    BoardData,
    BoardNextAction,
    BoardRow,
    Cursor,
    Reason,
    Revision,
    TaskProgressData,
)
from app.services.auth import Identity

RESULT_CONTRACT_VERSION = CONTRACT_VERSION
type Nonblank = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
type ReviewState = Literal["unconfirmed", "legacy_unconfirmed", "confirmed", "invalidated"]
type EntryOrigin = Literal["extracted", "legacy", "manual_rejected", "manual_missing"]
type ScopeOrigin = Literal["model", "manual"]
type DecisionAction = Literal["confirm", "reopen"]
type ReviewBucket = BoardBucket | Literal["requirement_review"]
type ReviewNextAction = BoardNextAction | Literal["confirm_requirement", "repair_requirement"]
type CitationFailure = Literal[
    "quote_not_at_position", "ambiguous_quote", "nonverbatim_quote", "unverified_location"
]


class RejectedItemRef(Contract):
    """A receipt association, never a complete candidate or a verified Source."""

    job_id: UUID
    index: int = Field(strict=True, ge=0)
    summary_sha256: Sha256


class RejectedItemView(RejectedItemRef):
    position: str
    quote: str = Field(max_length=200)
    reason: str
    complete_candidate: Literal[False] = False
    entered_requirement_ids: list[UUID] = Field(default_factory=list, max_length=100)
    entered_requirement_count: int = Field(ge=0)


class VerifiedRequirementSource(Contract):
    source: Source
    document_sha256: Sha256
    chunk_sha256: Sha256
    location_sha256: Sha256
    quote_sha256: Sha256
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, ge=1)
    verifier_version: str = Field(min_length=1, max_length=100)
    binding_sha256: Sha256

    @model_validator(mode="after")
    def span_length(self):
        if self.end - self.start != len(self.source.quote):
            raise ValueError("offsets must delimit the literal quote in Python character units")
        return self


class RequirementContent(Contract):
    category: Category
    starred: bool = False
    text: Nonblank
    condition: dict[str, Any] = Field(default_factory=dict)
    source: Source

    @field_validator("condition")
    @classmethod
    def json_condition(cls, value):
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("condition must contain finite JSON values") from None
        return value

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain non-whitespace characters")
        return value

    @field_validator("source")
    @classmethod
    def bounded_quote(cls, value: Source) -> Source:
        if not value.quote.strip() or len(value.quote) > 20_000:
            raise ValueError("quote must be nonblank and at most 20000 characters")
        return value


class ManualRequirementInput(Contract):
    # Null creates an explicitly manual extraction scope after successful verification.
    extraction_job_id: UUID | None = None
    expected_set_revision: Revision | None = None
    content: RequirementContent
    rejected_item: RejectedItemRef | None = None
    reason: Reason

    @model_validator(mode="after")
    def existing_scope_revision(self):
        if (self.extraction_job_id is None) != (self.expected_set_revision is None):
            raise ValueError("an existing extraction scope requires its observed set revision")
        return self


class ManualEntryCreate(ManualRequirementInput):
    request_id: UUID
    expected_preview_hash: Sha256


class ManualEntryPreview(Contract):
    task_id: UUID
    extraction_job_id: UUID | None
    creates_manual_scope: bool
    expected_set_revision: Revision | None
    preview_hash: Sha256
    verified_source: VerifiedRequirementSource
    duplicate_requirement_id: UUID | None = None
    estimated_cost: Cost = Field(default_factory=Cost)


class RequirementActorHint(Contract):
    user_id: UUID | None
    basis: Literal["assignee", "owner", "owner_recovery"]
    action: Literal["confirm_requirement", "repair_requirement", "assign", "unarchive"]
    can_current_actor_act: bool


class RequirementReviewView(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    origin: EntryOrigin
    content: RequirementContent
    model_quote: str | None = None
    revision: Revision
    review_hash: Sha256
    state: ReviewState
    citation_valid: bool
    source_pin: VerifiedRequirementSource | None
    confirmed_by_user_id: UUID | None = None
    confirmed_at: AwareDatetime | None = None
    rejected_item: RejectedItemRef | None = None
    next_actor: RequirementActorHint | None

    @model_validator(mode="after")
    def confirmation_metadata(self):
        confirmed = self.state == "confirmed"
        if confirmed != (self.confirmed_by_user_id is not None) or confirmed != (
            self.confirmed_at is not None
        ):
            raise ValueError("only current confirmations expose a current confirmer and time")
        if confirmed and (not self.citation_valid or self.source_pin is None):
            raise ValueError("confirmation requires a currently verified source binding")
        if not confirmed and self.next_actor is None:
            raise ValueError("outstanding requirement review must name a next actor or recovery")
        if self.source_pin is not None and self.source_pin.source != self.content.source:
            raise ValueError("source pin must bind this requirement's source")
        return self


class ReviewSummary(Contract):
    total: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    unconfirmed: int = Field(ge=0)
    legacy_unconfirmed: int = Field(ge=0)
    invalidated: int = Field(ge=0)
    invalid_citations: int = Field(ge=0)
    rejected_reported: int | None = Field(default=None, ge=0)
    manual_added: int = Field(ge=0)
    extraction_completeness: Literal["not_asserted"] = "not_asserted"

    @model_validator(mode="after")
    def partition(self):
        if self.total != (
            self.confirmed + self.unconfirmed + self.legacy_unconfirmed + self.invalidated
        ):
            raise ValueError("review states must partition saved requirements exactly once")
        if self.invalid_citations > self.total or self.manual_added > self.total:
            raise ValueError("overlapping diagnostic counts cannot exceed saved requirements")
        return self


class RequirementSetView(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    origin: ScopeOrigin
    revision: Revision
    membership_sha256: Sha256
    confirmation_sha256: Sha256
    summary: ReviewSummary
    last_event_cursor: Cursor


class PageQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class ReviewPageQuery(PageQuery):
    state: ReviewState | None = None
    category: Category | None = None
    starred: bool | None = None
    origin: EntryOrigin | None = None
    rejected_job_id: UUID | None = None
    rejected_index: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def rejected_filter_pair(self):
        if (self.rejected_job_id is None) != (self.rejected_index is None):
            raise ValueError("rejected-job and index filters must be provided together")
        return self


class PageData(Contract):
    task_id: UUID
    extraction_job_id: UUID
    next_cursor: Cursor | None = None
    total: int = Field(ge=0)


class ReviewPageData(PageData):
    scope: RequirementSetView


class RequirementReviewData(Contract):
    requirement: RequirementReviewView
    scope: RequirementSetView
    request_id: UUID | None = None
    event_ids: list[UUID] = Field(default_factory=list, max_length=100)
    replayed: bool = False


class ManualEntryData(RequirementReviewData):
    created_scope: bool


class RequirementDecision(Contract):
    request_id: UUID
    action: DecisionAction
    expected_revision: Revision
    expected_review_hash: Sha256
    reason: Reason


class ConfirmationTarget(Contract):
    requirement_id: UUID
    expected_revision: Revision
    expected_review_hash: Sha256


class RequirementConfirmBatch(Contract):
    request_id: UUID
    expected_set_revision: Revision
    items: list[ConfirmationTarget] = Field(min_length=1, max_length=100)
    reviewed_each: Literal[True]
    reason: Reason

    @field_validator("items")
    @classmethod
    def unique_targets(cls, value: list[ConfirmationTarget]) -> list[ConfirmationTarget]:
        if len({item.requirement_id for item in value}) != len(value):
            raise ValueError("requirements must be unique")
        return value


class ConfirmationBatchData(Contract):
    scope: RequirementSetView
    request_id: UUID
    event_ids: list[UUID] = Field(min_length=1, max_length=100)
    changed: int = Field(ge=0, le=100)
    replayed: bool = False


class ConfirmationReceiptItem(Contract):
    requirement_id: UUID
    event_id: UUID
    confirmed_revision: Revision
    current_revision: Revision
    current_state: ReviewState


class RequirementReviewEvent(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    revision: Revision
    action: Literal["seed", "manual_add", "confirm", "reopen", "invalidate", "source_repair"]
    state_after: ReviewState
    content: RequirementContent
    review_hash: Sha256
    source_pin: VerifiedRequirementSource | None
    actor_kind: Literal["session", "worker", "migration"]
    actor_user_id: UUID | None
    reason_sha256: Sha256 | None
    request_id: UUID | None
    occurred_at: AwareDatetime


class RequirementConsumptionEntry(Contract):
    requirement_id: UUID
    review_revision: Revision
    review_hash: Sha256
    state: ReviewState
    source_binding_sha256: Sha256 | None
    disposition: Literal["accepted", "gap", "preparation_only"]


class RequirementConsumptionManifest(Contract):
    task_id: UUID
    extraction_job_id: UUID
    set_revision: Revision
    membership_sha256: Sha256
    confirmation_sha256: Sha256
    entries: list[RequirementConsumptionEntry] = Field(max_length=2000)
    policy_version: Literal["requirement-review-v1"] = "requirement-review-v1"


class RequirementBoardQuery(Contract):
    extraction_job_id: UUID
    bucket: ReviewBucket | None = None
    category: Category | None = None
    starred: bool | None = None
    owner_user_id: UUID | None = None
    unassigned: bool = False
    review_domain: Literal["commercial", "technical"] | None = None
    blocker: BoardBlocker | Literal["requirement_unconfirmed", "requirement_invalidated"] | None = (
        None
    )
    mine: bool = False
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def assignee_filter(self):
        if self.unassigned and self.owner_user_id is not None:
            raise ValueError("unassigned and owner_user_id filters are mutually exclusive")
        return self


class RequirementBoardOverlay(Contract):
    requirement_id: UUID
    state: ReviewState
    bucket: ReviewBucket
    next_action: ReviewNextAction
    next_actor: RequirementActorHint | None
    blockers: list[
        Literal["requirement_unconfirmed", "requirement_invalidated", "invalid_citation"]
    ] = Field(default_factory=list, max_length=3)
    counts_as_response_complete: bool


class RequirementBoardCounts(Contract):
    total: int = Field(ge=0, le=5000)
    requirement_review: int = Field(ge=0)
    # Only currently confirmed requirements enter these six response buckets.
    responses: BoardCounts

    @model_validator(mode="after")
    def partition(self):
        if self.total != self.requirement_review + self.responses.total:
            raise ValueError("requirement review and response buckets must partition the scope")
        return self


class RequirementActivityView(Contract):
    id: UUID
    actor_user_id: UUID | None
    action: Literal[
        "requirement.manual_added",
        "requirement.confirmed",
        "requirement.reopened",
        "requirement.invalidated",
        "requirement.repair_citation",
    ]
    requirement_id: UUID
    extraction_job_id: UUID
    created_at: AwareDatetime


class RequirementBoardData(Contract):
    # Preserve the existing bounded board metadata and its legacy-safe projection.
    board: BoardData
    scope: RequirementSetView
    buckets: RequirementBoardCounts
    requirement_activity: list[RequirementActivityView] = Field(max_length=20)

    @model_validator(mode="after")
    def same_scope(self):
        if (self.board.org_id, self.board.task_id, self.board.extraction_job_id) != (
            self.scope.org_id,
            self.scope.task_id,
            self.scope.extraction_job_id,
        ):
            raise ValueError("board and requirement scope must match")
        if (
            self.buckets.total != self.scope.summary.total
            or self.buckets.total != self.board.counts.total
        ):
            raise ValueError("board projections must count the same complete scope")
        return self


class RequirementBoardItem(Contract):
    response: BoardRow
    requirement: RequirementBoardOverlay

    @model_validator(mode="after")
    def same_requirement(self):
        if self.response.requirement_id != self.requirement.requirement_id:
            raise ValueError("board projections must refer to the same requirement")
        return self


class RequirementProgressData(Contract):
    progress: TaskProgressData
    scope: RequirementSetView

    @model_validator(mode="after")
    def same_task(self):
        if (self.progress.org_id, self.progress.task_id) != (self.scope.org_id, self.scope.task_id):
            raise ValueError("progress and requirement scope must belong to one task")
        return self


class RequirementCitationVerifier(Protocol):
    """Local deterministic provider; no vendor SDK, network, OCR or model calls."""

    async def verify(
        self, session: AsyncSession, actor: Identity, task_id: UUID, source: Source
    ) -> VerifiedRequirementSource: ...


class RequirementReviewService(Protocol):
    """All mutations share the caller's transaction, task lock and live authorization."""

    async def list_reviews(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        extraction_job_id: UUID,
        query: ReviewPageQuery,
    ) -> tuple[ReviewPageData, list[RequirementReviewView]]: ...

    async def show(
        self, session: AsyncSession, actor: Identity, requirement_id: UUID
    ) -> RequirementReviewData: ...

    async def history(
        self,
        session: AsyncSession,
        actor: Identity,
        requirement_id: UUID,
        query: PageQuery,
    ) -> tuple[PageData, list[RequirementReviewEvent]]: ...

    async def rejected(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        job_id: UUID,
        query: PageQuery,
    ) -> tuple[PageData, list[RejectedItemView]]: ...

    async def preview_manual(
        self, session: AsyncSession, actor: Identity, task_id: UUID, request: ManualRequirementInput
    ) -> ManualEntryPreview: ...

    async def add_manual(
        self, session: AsyncSession, actor: Identity, task_id: UUID, request: ManualEntryCreate
    ) -> ManualEntryData: ...

    async def decide(
        self,
        session: AsyncSession,
        actor: Identity,
        requirement_id: UUID,
        request: RequirementDecision,
    ) -> RequirementReviewData: ...

    async def confirm_batch(
        self,
        session: AsyncSession,
        actor: Identity,
        task_id: UUID,
        extraction_job_id: UUID,
        request: RequirementConfirmBatch,
    ) -> tuple[ConfirmationBatchData, list[ConfirmationReceiptItem]]: ...

    async def consumption_manifest(
        self, session: AsyncSession, actor: Identity, task_id: UUID, extraction_job_id: UUID
    ) -> RequirementConsumptionManifest: ...


# Keep the transport shape in the shared runtime schema; payload types above fill
# Result.data / Result.items and do not invent a second envelope or version field.
RequirementResult = Result
