"""Bounded, read-only assessment projections shared by console and CLI."""

from datetime import date
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.providers.base import CheckProvider as CheckProvider
from app.schemas.budget_contracts import TaskBudgetView
from app.schemas.check_contracts import (
    AssessmentJobAccepted,
    AssessmentListData,
    CheckCertificateView,
    CheckItemView,
    CheckPreview,
    CheckRequest,
    FindingDecisionRequest,
    FindingView,
    Money,
    NonBlank,
    RiskLevel,
    Sha256,
)
from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Location, Result
from app.schemas.response_card_contracts import ReviewDomain
from app.schemas.score_contracts import (
    AggregationRule,
    CandidateScoreNumber,
    CandidateScoreRange,
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricGenerateRequest,
    RubricItemDecisionRequest,
    RubricItemView,
    RubricPreview,
    RubricRequirementCoverageView,
    RubricRequirementReadiness,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSectionView,
    RubricSetDecisionRequest,
    RubricSetState,
    ScoreItemView,
    ScorePreview,
    ScoreRange,
    ScoreRequest,
    ScoreSectionSummary,
    VerbatimRule,
)
from app.schemas.score_contracts import (
    RubricProvider as RubricProvider,
)
from app.schemas.score_contracts import (
    ScoreProvider as ScoreProvider,
)
from app.services.auth import Identity

RESULT_CONTRACT_VERSION = CONTRACT_VERSION
PAGE_BYTE_LIMIT = 2 * 1024 * 1024
type ConsoleAssessmentResult = Result
type ConsoleAssessmentCost = Cost
type ConsoleAssessmentAccepted = AssessmentJobAccepted
type ConsoleAssessmentPreview = CheckPreview | RubricPreview | ScorePreview
type ConsoleAssessmentWrite = (
    CheckRequest
    | FindingDecisionRequest
    | RubricGenerateRequest
    | RubricReviseRequest
    | RubricClassifyRequest
    | RubricCoverageDecisionRequest
    | RubricSectionDecisionRequest
    | RubricItemDecisionRequest
    | RubricSetDecisionRequest
    | ScoreRequest
)
type Count = Annotated[int, Field(strict=True, ge=0)]
type Revision = Annotated[int, Field(strict=True, ge=1)]
type Cursor = Annotated[str, Field(min_length=1, max_length=2048)]
type Code = Annotated[str, Field(pattern=r"^[a-z0-9_:-]{1,100}$")]
type ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
type AssessmentKind = Literal["check", "rubric", "score"]
type JobKind = Literal["check", "score_rubric", "score"]
type Validity = Literal["current", "stale"]
type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
type Action = Literal[
    "check_run",
    "finding_dismiss",
    "finding_reopen",
    "rubric_generate",
    "rubric_classify",
    "rubric_coverage_decide",
    "rubric_section_decide",
    "rubric_item_decide",
    "rubric_revise",
    "rubric_confirm",
    "rubric_reopen",
    "score_run",
    "job_cancel",
]


class ActionAvailability(Contract):
    """Read-time guidance only; every write rechecks the live service/DB gates."""

    action: Action
    allowed: bool
    required_role: Literal["admin", "bidder", "technical"] | None = None
    review_domain: ReviewDomain | None = None
    blocker_codes: list[Code] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def blocked_actions_explain_why(self) -> Self:
        if self.allowed == bool(self.blocker_codes):
            raise ValueError("allowed actions have no blockers; denied actions need a reason")
        return self


class DraftChoice(Contract):
    draft_id: UUID
    extraction_job_id: UUID
    created_at: AwareDatetime
    validity: Validity
    completion: Literal["complete", "partial"]
    input_hash: Sha256


class AssessmentInputsData(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    latest_draft: DraftChoice | None
    current_draft: DraftChoice | None
    redaction_enabled: bool
    redaction_revision: Revision
    task_budget: TaskBudgetView
    actions: list[ActionAvailability] = Field(max_length=3)

    @model_validator(mode="after")
    def selected_draft_belongs_to_extraction(self) -> Self:
        for draft in (self.latest_draft, self.current_draft):
            if draft is not None and draft.extraction_job_id != self.extraction_job_id:
                raise ValueError("draft choices must belong to the selected extraction")
        if self.current_draft is not None and self.current_draft.validity != "current":
            raise ValueError("a stale draft cannot be advertised as current")
        return self


class PageRequest(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class CheckPageRequest(PageRequest):
    part: Literal["findings", "coverage", "certificates", "notices"] = "findings"
    severity: RiskLevel | None = None
    domain: ReviewDomain | Literal["unclassified"] | None = None
    status: Literal["open", "dismissed"] | None = None
    requirement_id: UUID | None = None
    entry_id: UUID | None = None

    @model_validator(mode="after")
    def filters_belong_to_part(self) -> Self:
        if self.part != "findings" and any(
            value is not None for value in (self.severity, self.domain, self.status)
        ):
            raise ValueError("severity, domain and status filter findings only")
        if self.part == "notices" and (
            self.requirement_id is not None or self.entry_id is not None
        ):
            raise ValueError("report notices have no requirement or entry filter")
        return self


class RubricPageRequest(PageRequest):
    part: Literal["sections", "items", "coverage", "blockers"] = "sections"
    state: Literal["candidate", "confirmed", "rejected"] | None = None
    domain: ReviewDomain | Literal["unclassified"] | None = None
    section_id: UUID | None = None
    requirement_id: UUID | None = None
    entry_id: UUID | None = None
    group_id: Sha256 | None = None

    @model_validator(mode="after")
    def filters_belong_to_part(self) -> Self:
        if self.part not in {"sections", "items"} and (
            self.state is not None or self.domain is not None
        ):
            raise ValueError("state/domain filter sections or items only")
        if self.section_id is not None and self.part != "items":
            raise ValueError("section_id filters rubric items only")
        if self.group_id is not None and self.part != "blockers":
            raise ValueError("group_id filters blockers only")
        return self


class AssessmentHistoryQuery(PageRequest):
    extraction_job_id: UUID | None = None


class ScorePageRequest(PageRequest):
    part: Literal["sections", "items", "notices"] = "items"
    section_key: NonBlank | None = None
    outcome: Literal["assessed", "unassessable"] | None = None
    requirement_id: UUID | None = None
    entry_id: UUID | None = None

    @model_validator(mode="after")
    def filters_belong_to_part(self) -> Self:
        if self.part != "items" and (
            self.outcome is not None or self.requirement_id is not None or self.entry_id is not None
        ):
            raise ValueError("outcome/requirement/entry filter score items only")
        if self.part == "notices" and self.section_key is not None:
            raise ValueError("report notices have no section filter")
        return self


class SubjectActions(Contract):
    subject_id: UUID
    actions: list[ActionAvailability] = Field(max_length=4)


class PageData(Contract):
    """Counts describe the whole query; items describe only this page."""

    task_id: UUID
    parent_id: UUID
    part: str
    snapshot: Cursor
    total: Count
    filtered_total: Count
    returned: Count
    next_cursor: Cursor | None
    validity: Validity
    parent_revision: Revision | None = None
    subject_actions: list[SubjectActions] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def counts_are_ordered(self) -> Self:
        if not self.returned <= self.filtered_total <= self.total:
            raise ValueError("returned <= filtered_total <= total is required")
        return self


class ProjectionPage[RowT: Contract](Contract):
    """Service return parts, flattened into Result.data/items by HTTP and CLI adapters."""

    data: PageData
    items: list[RowT] = Field(max_length=100)

    @model_validator(mode="after")
    def page_count_matches_rows(self) -> Self:
        if self.data.returned != len(self.items):
            raise ValueError("returned must match this page, never the full set")
        return self


class Notice(Contract):
    code: Code
    message: ShortText
    requirement_id: UUID | None = None
    subject_id: UUID | None = None
    group_id: Sha256 | None = None
    group_member_count: Count | None = None

    @model_validator(mode="after")
    def grouped_notices_identify_one_member(self) -> Self:
        if (self.group_id is None) != (self.group_member_count is None):
            raise ValueError("group identity and member count must be paired")
        if self.group_id is not None and (self.subject_id is None or self.group_member_count == 0):
            raise ValueError("grouped notices identify one member of a nonempty group")
        return self


class ReportHeader(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    draft_id: UUID
    input_hash: Sha256
    assessment_date: date
    created_at: AwareDatetime
    completion: Literal["complete", "partial"]
    validity: Validity
    invalidation_codes: list[Code] = Field(max_length=30)
    notice_count: Count
    advisory_only: Literal[True] = True


class FindingGroup(Contract):
    severity: RiskLevel
    review_domain: ReviewDomain | None
    status: Literal["open", "dismissed"]
    count: Count


class CheckSummaryData(Contract):
    report: ReportHeader
    mode: Literal["rules", "combined"]
    item_count: Count
    finding_count: Count
    unassessed_count: Count
    certificate_count: Count
    groups: list[FindingGroup] = Field(max_length=18)

    @model_validator(mode="after")
    def group_counts_cover_all_findings(self) -> Self:
        keys = [(g.severity, g.review_domain, g.status) for g in self.groups]
        if len(set(keys)) != len(keys) or sum(g.count for g in self.groups) != self.finding_count:
            raise ValueError("groups must partition all findings without duplicates")
        return self


class RubricCompletenessSummary(Contract):
    """Bounded projection of RubricCompletenessView; blocker IDs are separately paged."""

    scoring_requirement_count: Count
    covered_requirement_count: Count
    pending_requirements: Count
    duplicate_groups: Count
    unconfirmed_sections: Count
    unconfirmed_items: Count
    normalization_errors: Count
    section_aggregation_rules_confirmed: bool
    overall_aggregation_rule_confirmed: bool
    complete: bool

    @model_validator(mode="after")
    def completeness_matches_all_checks(self) -> Self:
        if self.covered_requirement_count > self.scoring_requirement_count:
            raise ValueError("covered requirements cannot exceed scoring requirements")
        blocked = (
            self.covered_requirement_count != self.scoring_requirement_count
            or self.pending_requirements > 0
            or self.duplicate_groups > 0
            or self.unconfirmed_sections > 0
            or self.unconfirmed_items > 0
            or self.normalization_errors > 0
            or not self.section_aggregation_rules_confirmed
            or not self.overall_aggregation_rule_confirmed
        )
        if self.complete == blocked:
            raise ValueError("complete must reflect all normalization and human review gates")
        return self


class RubricSummaryData(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    input_hash: Sha256
    prior_rubric_id: UUID | None
    version: Revision
    revision: Revision
    state: RubricSetState
    validity: Validity
    created_at: AwareDatetime
    section_count: Count
    item_count: Count
    completeness: RubricCompletenessSummary
    requirement_review: RubricRequirementReadiness | None = None
    overall_aggregation: AggregationRule
    overall_aggregation_assessable: bool
    overall_rule_text: VerbatimRule | None
    overall_score_range: CandidateScoreRange | None
    overall_cap: CandidateScoreNumber | None
    actions: list[ActionAvailability] = Field(max_length=3)


class RubricReplacementData(Contract):
    rubric_id: UUID
    prior_rubric_id: UUID
    snapshot_sha256: Sha256
    replacement: RubricReviseRequest


class ConsoleRubricSectionView(RubricSectionView):
    """Complete source bindings inherited for bounded review and replacement."""


class ScoreSummaryData(Contract):
    report: ReportHeader
    rubric_id: UUID
    rubric_version: Revision
    assessed_items: Count
    unassessable_items: Count
    section_count: Count
    overall_aggregation: AggregationRule
    overall_aggregation_assessable: bool
    overall_rule_text: NonBlank | None
    overall_cap: Money | None
    assessed_subtotal: Money
    total_status: Literal["estimated", "range_only", "unavailable"]
    possible_range: ScoreRange | None
    estimated_total: Money | None

    @model_validator(mode="after")
    def never_promote_a_subtotal_to_a_total(self) -> Self:
        if (self.total_status == "estimated") != (self.estimated_total is not None):
            raise ValueError("only estimated status may carry estimated_total")
        if self.total_status == "estimated" and (
            self.report.completion != "complete" or self.unassessable_items > 0
        ):
            raise ValueError("partial or unassessable reports cannot have a total estimate")
        return self


class AssessmentHistoryPage[SummaryT: Contract](Contract):
    data: AssessmentListData
    items: list[SummaryT] = Field(max_length=100)


type CheckPage = ProjectionPage[FindingView | CheckItemView | CheckCertificateView | Notice]
type RubricPage = ProjectionPage[
    ConsoleRubricSectionView | RubricItemView | RubricRequirementCoverageView | Notice
]
type ScorePage = ProjectionPage[ScoreSectionSummary | ScoreItemView | Notice]
type AssessmentHistory = (
    AssessmentHistoryPage[CheckSummaryData]
    | AssessmentHistoryPage[RubricSummaryData]
    | AssessmentHistoryPage[ScoreSummaryData]
)


class AssessmentJobQuery(PageRequest):
    kind: JobKind
    extraction_job_id: UUID | None = None


class StageProgress(Contract):
    """Optional worker evidence, never a percentage inferred from elapsed time."""

    scheme: Literal["single_pass", "two_stage"]
    strategy_version: Code
    stage: Literal["whole_table", "sections", "items", "validation", "publication"]
    completed_batches: Count | None = None
    total_batches: Annotated[int, Field(strict=True, ge=1)] | None = None
    sections_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def progress_is_observed_and_consistent(self) -> Self:
        if self.scheme == "single_pass" and self.stage in {"sections", "items"}:
            raise ValueError("single-pass jobs cannot claim two-stage progress")
        if self.scheme == "two_stage" and self.stage == "whole_table":
            raise ValueError("two-stage jobs identify sections or items explicitly")
        if self.scheme == "two_stage" and self.stage == "items" and self.sections_sha256 is None:
            raise ValueError("stage two requires the fixed section snapshot hash")
        if (self.completed_batches is None) != (self.total_batches is None):
            raise ValueError("batch counts must be known together or both absent")
        if (
            self.total_batches is not None
            and self.completed_batches is not None
            and self.completed_batches > self.total_batches
        ):
            raise ValueError("completed batches cannot exceed the total")
        return self


class AssessmentJobView(Contract):
    id: UUID
    task_id: UUID
    extraction_job_id: UUID
    kind: JobKind
    status: JobState
    created_at: AwareDatetime
    finished_at: AwareDatetime | None
    attempts: Count
    result_id: UUID | None
    completion: Literal["complete", "partial"] | None
    progress: StageProgress | None = None
    error_code: Code | None = None
    stop_reason: Code | None = None
    cancel: ActionAvailability


class AssessmentJobPageData(Contract):
    task_id: UUID
    kind: JobKind
    total: Count
    next_cursor: Cursor | None


class AssessmentJobPage(Contract):
    data: AssessmentJobPageData
    items: list[AssessmentJobView] = Field(max_length=100)


class CitationRequest(Contract):
    """Parent traversal identifies a saved citation; no arbitrary document or text input."""

    parent_kind: AssessmentKind
    parent_id: UUID
    part: Literal["finding", "coverage", "rubric_section", "rubric_item", "score_item"]
    entry_id: UUID
    origin: Literal["source", "sources", "citations"] = "source"
    citation_index: int = Field(default=0, strict=True, ge=0)
    text: Literal["quote", "context"] = "context"
    offset: Count = 0
    limit: int = Field(default=4000, strict=True, ge=1, le=8000)

    @model_validator(mode="after")
    def parent_and_entry_match(self) -> Self:
        permitted = {
            "check": {"finding", "coverage"},
            "rubric": {"rubric_section", "rubric_item", "coverage"},
            "score": {"score_item"},
        }
        if self.part not in permitted[self.parent_kind]:
            raise ValueError("entry kind does not belong to this assessment parent")
        if self.origin == "source" and self.citation_index != 0:
            raise ValueError("source selects the one canonical tender source")
        if self.part == "rubric_section" and self.origin != "sources":
            raise ValueError("rubric sections select an indexed saved source")
        if self.origin == "sources" and self.part != "rubric_section":
            raise ValueError("indexed sources belong only to rubric sections")
        if (
            self.parent_kind == "rubric"
            and self.part != "rubric_section"
            and self.origin != "source"
        ):
            raise ValueError("rubric items and coverage expose their canonical Source")
        if self.parent_kind == "score" and self.origin != "citations":
            raise ValueError("score item context selects a verified citation")
        return self


class TextWindow(Contract):
    text: str = Field(max_length=8000)
    offset: Count
    total_characters: Count
    next_offset: Count | None

    @model_validator(mode="after")
    def continuation_never_hides_text(self) -> Self:
        end = self.offset + len(self.text)
        if end > self.total_characters or self.offset > self.total_characters:
            raise ValueError("window exceeds the saved text")
        expected = end if end < self.total_characters else None
        if self.next_offset != expected or (expected is not None and not self.text):
            raise ValueError("incomplete windows require a forward continuation offset")
        return self


class FixTarget(Contract):
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    current_card_id: UUID | None
    historical_card_revision_id: UUID | None


class CitationContextData(Contract):
    parent_id: UUID
    entry_id: UUID
    kind: Literal["tender", "draft", "evidence"]
    verified: Literal[True] = True
    document_id: UUID | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    location: Location | None = None
    draft_id: UUID | None = None
    response_item_id: UUID | None = None
    card_revision_id: UUID | None = None
    evidence_id: UUID | None = None
    field: Literal["response_text", "deviation_note"] | None = None
    text_kind: Literal["quote", "context"]
    window: TextWindow
    quote_start: Count
    quote_end: Count
    fix: FixTarget

    @model_validator(mode="after")
    def location_matches_citation_kind(self) -> Self:
        if self.quote_end <= self.quote_start:
            raise ValueError("verified quote offsets must identify nonempty text")
        if self.text_kind == "context" and self.quote_end > self.window.total_characters:
            raise ValueError("quote must be contained by the saved context")
        if self.kind == "tender":
            if self.document_id is None or (self.page is None) == (self.location is None):
                raise ValueError("tender citations require the PDF page or Word location")
        if self.kind == "draft" and any(
            value is None
            for value in (self.draft_id, self.response_item_id, self.card_revision_id, self.field)
        ):
            raise ValueError("draft context must bind the exact saved response revision")
        if self.kind == "evidence" and self.evidence_id is None:
            raise ValueError("evidence context must bind saved evidence")
        return self


class ConsoleAssessmentReads(Protocol):
    """Trusted Identity comes from authentication, never a JSON body.

    Implementations own read-only org transactions, parent/dependency authorization,
    source verification, stable cursors and the encoded Result byte limit. They must
    page before materializing child text. Summary reads never invoke a Provider.
    """

    async def inputs(
        self, actor: Identity, task_id: UUID, extraction_job_id: UUID
    ) -> AssessmentInputsData: ...

    async def jobs(
        self, actor: Identity, task_id: UUID, query: AssessmentJobQuery
    ) -> AssessmentJobPage: ...

    async def history(
        self,
        actor: Identity,
        task_id: UUID,
        kind: AssessmentKind,
        query: AssessmentHistoryQuery,
    ) -> AssessmentHistory: ...

    async def check_summary(self, actor: Identity, report_id: UUID) -> CheckSummaryData: ...

    async def check_page(
        self, actor: Identity, report_id: UUID, query: CheckPageRequest
    ) -> CheckPage: ...

    async def rubric_summary(
        self, actor: Identity, task_id: UUID, rubric_id: UUID
    ) -> RubricSummaryData: ...

    async def rubric_page(
        self, actor: Identity, task_id: UUID, rubric_id: UUID, query: RubricPageRequest
    ) -> RubricPage: ...

    async def rubric_replacement(
        self, actor: Identity, task_id: UUID, rubric_id: UUID
    ) -> RubricReplacementData: ...

    async def score_summary(
        self, actor: Identity, task_id: UUID, report_id: UUID
    ) -> ScoreSummaryData: ...

    async def score_page(
        self, actor: Identity, task_id: UUID, report_id: UUID, query: ScorePageRequest
    ) -> ScorePage: ...

    async def citation(
        self, actor: Identity, task_id: UUID, query: CitationRequest
    ) -> CitationContextData: ...
