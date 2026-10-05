"""Approved rubric review, confirmed-draft scoring and report contracts."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.check_contracts import (
    AssessmentFailure,
    AssessmentInput,
    AssessmentJobAccepted,
    AssessmentJobResult,
    AssessmentListData,
    AssessmentPreview,
    AssessmentRequest,
    Money,
    NonBlank,
    OutboundContext,
    Sha256,
    VerifiedCitation,
)
from app.schemas.contracts import Category, Contract, ProviderUsage, Result, Source
from app.schemas.response_card_contracts import ModelEvidenceRef, ReviewDomain

type ScoreCLIResult = Result
type RubricJobAccepted = AssessmentJobAccepted
type ScoreJobAccepted = AssessmentJobAccepted
type ScoreListData = AssessmentListData
type RubricItemState = Literal["candidate", "confirmed", "rejected"]
type RubricSetState = Literal["candidate", "confirmed", "superseded"]
type RubricDecision = Literal["confirm", "reject", "reopen"]
type ScorePartition = Literal["response", "comply_only", "gap"]
type ScoreOutcome = Literal["assessed", "unassessable"]
type AssessmentMode = Literal[
    "model_assessable",
    "ambiguous",
    "price_comparison",
    "external_comparison",
    "manual_only",
    "unsupported_formula",
]
type AggregationRule = Literal["sum", "weighted_sum", "capped_sum", "formula", "non_additive"]
type Weight = Annotated[Decimal, Field(gt=0, le=1, max_digits=18, decimal_places=8)]


class ScoreRange(Contract):
    minimum: Money
    maximum: Money

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("score range minimum cannot exceed maximum")
        return self


class RubricGenerateRequest(Contract):
    extraction_job_id: UUID
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    max_charge: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8)] | None = None

    @model_validator(mode="after")
    def submission_binds_preview(self) -> Self:
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("submission requires expected_input_hash")
        if self.dry_run and self.retry:
            raise ValueError("dry_run cannot retry a job")
        return self


class RubricInput(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    input_hash: Sha256
    scope: Literal["scoring_requirements"] = "scoring_requirements"


class RubricPreview(AssessmentPreview[RubricInput]):
    scoring_requirement_ids: list[UUID]
    prompt_version: NonBlank
    schema_version: NonBlank
    normalization_rule_version: NonBlank

    @model_validator(mode="after")
    def unique_scoring_requirements(self) -> Self:
        if len(set(self.scoring_requirement_ids)) != len(self.scoring_requirement_ids):
            raise ValueError("scoring_requirement_ids must be unique")
        if set(self.scoring_requirement_ids) != set(self.selected_item_ids):
            raise ValueError("selected_item_ids must be exactly the scoring requirements")
        return self


class RubricGenerateResult(Contract):
    rubric_id: UUID
    job_id: UUID
    completion: Literal["complete", "partial"]
    version: int = Field(ge=1)
    scoring_requirements: int = Field(ge=0)
    candidate_items: int = Field(ge=0)
    unresolved_requirements: int = Field(ge=0)
    usage_record_ids: list[UUID]
    charge: Money | None
    billing_currency: str = Field(min_length=3, max_length=3)
    stop_reason: str | None = None


class RubricSectionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    key: NonBlank
    title: NonBlank
    order: int = Field(ge=1)
    aggregation: AggregationRule
    aggregation_assessable: bool
    aggregation_rule_text: NonBlank | None = None
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    cap: Money | None = None
    included_in_overall_total: bool
    ambiguity_reason: NonBlank | None = None
    review_domain: ReviewDomain | None = None
    source: Source
    state: RubricItemState
    revision: int = Field(ge=1)
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def confirmation_is_paired(self) -> Self:
        executable = self.aggregation in {"sum", "weighted_sum", "capped_sum"}
        if self.aggregation_assessable != executable:
            raise ValueError("aggregation_assessable must reflect the supported algorithms")
        if self.aggregation in {"formula", "non_additive"} and self.aggregation_rule_text is None:
            raise ValueError("unsupported aggregation rules require their fixed original wording")
        if (self.aggregation == "capped_sum") != (self.cap is not None):
            raise ValueError("only capped_sum requires a cap")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("rubric section confirmation actor and time must be paired")
        if self.state == "confirmed" and (self.confirmed_by is None or self.review_domain is None):
            raise ValueError("confirmed rubric sections require a domain and human actor")
        return self


class RubricItemView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    section_id: UUID
    requirement_id: UUID
    category: Literal[Category.scoring] = Category.scoring
    key: NonBlank
    title: NonBlank
    rule_text: NonBlank
    order: int = Field(ge=1)
    assessment_mode: AssessmentMode
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    ambiguity_reason: NonBlank | None = None
    source: Source
    fingerprint: Sha256
    review_domain: ReviewDomain | None = None
    state: RubricItemState
    revision: int = Field(ge=1)
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def rubric_item_is_coherent(self) -> Self:
        if self.assessment_mode == "model_assessable" and self.score_range is None:
            raise ValueError("model-assessable rubric items require score bounds")
        if self.assessment_mode != "model_assessable" and self.ambiguity_reason is None:
            raise ValueError("non-model assessment modes require an explicit reason")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("rubric item confirmation actor and time must be paired")
        if self.state == "confirmed" and (self.confirmed_by is None or self.review_domain is None):
            raise ValueError("confirmed rubric items require a domain and human actor")
        return self


class RubricRequirementCoverageView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    requirement_id: UUID
    source: Source
    disposition: Literal["mapped", "duplicate", "excluded", "pending"]
    rubric_item_ids: list[UUID]
    canonical_requirement_id: UUID | None = None
    reason: NonBlank | None = None
    decided_by: UUID | None = None
    decided_at: AwareDatetime | None = None
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def disposition_is_explicit(self) -> Self:
        if self.disposition == "mapped" and not self.rubric_item_ids:
            raise ValueError("mapped requirements require at least one rubric item")
        if self.disposition != "mapped" and self.rubric_item_ids:
            raise ValueError("only mapped requirements may name rubric items")
        if self.disposition == "duplicate" and self.canonical_requirement_id is None:
            raise ValueError("duplicate requirements require a canonical requirement")
        if self.disposition != "duplicate" and self.canonical_requirement_id is not None:
            raise ValueError("only duplicate requirements may name a canonical requirement")
        if self.disposition in {"duplicate", "excluded"} and self.reason is None:
            raise ValueError("duplicate and excluded requirements require a reason")
        if (self.decided_by is None) != (self.decided_at is None):
            raise ValueError("coverage decision actor and time must be paired")
        if (self.disposition == "pending") != (self.decided_by is None):
            raise ValueError("only pending coverage may lack a human decision")
        return self


class RubricCompletenessView(Contract):
    scoring_requirement_count: int = Field(ge=0)
    covered_requirement_count: int = Field(ge=0)
    pending_requirement_ids: list[UUID]
    unresolved_duplicate_fingerprint_groups: list[list[UUID]]
    unconfirmed_section_ids: list[UUID]
    unconfirmed_item_ids: list[UUID]
    normalization_errors: list[str]
    section_aggregation_rules_confirmed: bool
    overall_aggregation_rule_confirmed: bool
    complete: bool

    @model_validator(mode="after")
    def complete_means_no_open_normalization_work(self) -> Self:
        if self.covered_requirement_count > self.scoring_requirement_count:
            raise ValueError(
                "covered requirement count cannot exceed the scoring requirement count"
            )
        blockers = (
            self.covered_requirement_count != self.scoring_requirement_count
            or bool(self.pending_requirement_ids)
            or bool(self.unresolved_duplicate_fingerprint_groups)
            or bool(self.unconfirmed_section_ids)
            or bool(self.unconfirmed_item_ids)
            or bool(self.normalization_errors)
            or not self.section_aggregation_rules_confirmed
            or not self.overall_aggregation_rule_confirmed
        )
        if self.complete == blockers:
            raise ValueError("complete must exactly reflect rubric normalization blockers")
        return self


class RubricSetView(Contract):
    id: UUID
    prior_rubric_id: UUID | None = None
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    input_hash: Sha256
    normalization_rule_version: NonBlank
    prompt_version: NonBlank
    schema_version: NonBlank
    state: RubricSetState
    revision: int = Field(ge=1)
    overall_aggregation: AggregationRule
    overall_aggregation_assessable: bool
    overall_rule_text: NonBlank | None = None
    overall_score_range: ScoreRange | None = None
    overall_cap: Money | None = None
    completeness: RubricCompletenessView
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def confirmed_set_is_complete(self) -> Self:
        executable = self.overall_aggregation in {"sum", "weighted_sum", "capped_sum"}
        if self.overall_aggregation_assessable != executable:
            raise ValueError("overall aggregation assessability must use supported algorithms")
        if (
            self.overall_aggregation in {"formula", "non_additive"}
            and self.overall_rule_text is None
        ):
            raise ValueError("unsupported overall aggregation requires its fixed original wording")
        if (self.overall_aggregation == "capped_sum") != (self.overall_cap is not None):
            raise ValueError("only capped_sum requires an overall cap")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("rubric set confirmation actor and time must be paired")
        if self.state == "confirmed" and (
            not self.completeness.complete or self.confirmed_by is None
        ):
            raise ValueError("confirmed rubric sets must be complete and human-confirmed")
        return self


class RubricReportData(Contract):
    rubric: RubricSetView
    sections: list[RubricSectionView]
    items: list[RubricItemView]
    coverage: list[RubricRequirementCoverageView]


class RubricSectionRevisionInput(Contract):
    source_section_id: UUID | None = None
    requirement_id: UUID
    key: NonBlank
    title: NonBlank
    order: int = Field(ge=1)
    aggregation: AggregationRule
    aggregation_rule_text: NonBlank | None = None
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    cap: Money | None = None
    included_in_overall_total: bool
    ambiguity_reason: NonBlank | None = None

    @model_validator(mode="after")
    def cap_matches_aggregation(self) -> Self:
        if (self.aggregation == "capped_sum") != (self.cap is not None):
            raise ValueError("only capped_sum requires a cap")
        if self.aggregation in {"formula", "non_additive"} and self.aggregation_rule_text is None:
            raise ValueError("unsupported aggregation rules require their fixed original wording")
        return self


class RubricItemRevisionInput(Contract):
    source_item_id: UUID | None = None
    requirement_id: UUID
    section_key: NonBlank
    key: NonBlank
    title: NonBlank
    rule_text: NonBlank
    order: int = Field(ge=1)
    assessment_mode: AssessmentMode
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    ambiguity_reason: NonBlank | None = None

    @model_validator(mode="after")
    def assessment_fields_are_explicit(self) -> Self:
        if self.assessment_mode == "model_assessable" and self.score_range is None:
            raise ValueError("model-assessable items require score bounds")
        if self.assessment_mode != "model_assessable" and self.ambiguity_reason is None:
            raise ValueError("non-model items require an explicit reason")
        return self


class RubricCoverageRevisionInput(Contract):
    requirement_id: UUID
    disposition: Literal["mapped", "duplicate", "excluded", "pending"]
    rubric_item_keys: list[NonBlank] = Field(default_factory=list)
    canonical_requirement_id: UUID | None = None
    reason: NonBlank | None = None

    @model_validator(mode="after")
    def disposition_is_explicit(self) -> Self:
        if self.disposition == "mapped" and not self.rubric_item_keys:
            raise ValueError("mapped coverage requires rubric item keys")
        if self.disposition != "mapped" and self.rubric_item_keys:
            raise ValueError("only mapped coverage may name rubric item keys")
        if self.disposition == "duplicate" and self.canonical_requirement_id is None:
            raise ValueError("duplicate coverage requires a canonical requirement")
        if self.disposition != "duplicate" and self.canonical_requirement_id is not None:
            raise ValueError("only duplicate coverage may name a canonical requirement")
        if self.disposition in {"duplicate", "excluded"} and self.reason is None:
            raise ValueError("duplicate and excluded coverage require a reason")
        return self


class RubricReviseRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    sections: list[RubricSectionRevisionInput] = Field(min_length=1)
    items: list[RubricItemRevisionInput] = Field(min_length=1)
    coverage: list[RubricCoverageRevisionInput] = Field(min_length=1)
    overall_aggregation: AggregationRule
    overall_rule_text: NonBlank | None = None
    overall_score_range: ScoreRange | None = None
    overall_cap: Money | None = None
    reason: NonBlank

    @model_validator(mode="after")
    def replacement_snapshot_is_unique(self) -> Self:
        section_keys = [section.key for section in self.sections]
        item_keys = [item.key for item in self.items]
        requirements = [entry.requirement_id for entry in self.coverage]
        if len(set(section_keys)) != len(section_keys):
            raise ValueError("section keys must be unique")
        if len(set(item_keys)) != len(item_keys):
            raise ValueError("item keys must be unique")
        if len(set(requirements)) != len(requirements):
            raise ValueError("coverage requirements must be unique")
        if not {item.section_key for item in self.items} <= set(section_keys):
            raise ValueError("every rubric item must name a section in this revision")
        if not {key for entry in self.coverage for key in entry.rubric_item_keys} <= set(item_keys):
            raise ValueError("coverage may only name rubric item keys in this revision")
        if (self.overall_aggregation == "capped_sum") != (self.overall_cap is not None):
            raise ValueError("only capped_sum requires an overall cap")
        if (
            self.overall_aggregation in {"formula", "non_additive"}
            and self.overall_rule_text is None
        ):
            raise ValueError("unsupported overall aggregation requires its fixed original wording")
        return self


class RubricClassifyRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    review_domain: ReviewDomain
    reason: NonBlank


class RubricItemDecisionRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    action: RubricDecision
    reason: NonBlank


class RubricSectionDecisionRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    action: RubricDecision
    reason: NonBlank


class RubricCoverageDecisionRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    action: Literal["mapped", "duplicate", "excluded", "reopen"]
    rubric_item_ids: list[UUID] = Field(default_factory=list)
    canonical_requirement_id: UUID | None = None
    reason: NonBlank

    @model_validator(mode="after")
    def action_fields_match(self) -> Self:
        if len(set(self.rubric_item_ids)) != len(self.rubric_item_ids):
            raise ValueError("rubric_item_ids must be unique")
        if self.action == "mapped" and not self.rubric_item_ids:
            raise ValueError("mapped coverage requires rubric items")
        if self.action != "mapped" and self.rubric_item_ids:
            raise ValueError("only mapped coverage may name rubric items")
        if self.action == "duplicate" and self.canonical_requirement_id is None:
            raise ValueError("duplicate coverage requires a canonical requirement")
        if self.action != "duplicate" and self.canonical_requirement_id is not None:
            raise ValueError("only duplicate coverage may name a canonical requirement")
        return self


class RubricSetDecisionRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    action: Literal["confirm", "reopen"]
    reason: NonBlank


class RubricDecisionView(Contract):
    kind: Literal["decision"] = "decision"
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    section_id: UUID | None = None
    item_id: UUID | None = None
    revision: int = Field(ge=2)
    action: RubricDecision
    reason: NonBlank
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"

    @model_validator(mode="after")
    def exactly_one_subject(self) -> Self:
        subjects = (self.section_id is not None, self.item_id is not None)
        if sum(subjects) > 1:
            raise ValueError("a rubric decision can name at most one section or item")
        return self


class RubricCoverageDecisionView(Contract):
    kind: Literal["coverage_decision"] = "coverage_decision"
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    requirement_id: UUID
    revision: int = Field(ge=2)
    action: Literal["mapped", "duplicate", "excluded", "reopen"]
    rubric_item_ids: list[UUID]
    canonical_requirement_id: UUID | None = None
    reason: NonBlank
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"

    @model_validator(mode="after")
    def action_fields_match(self) -> Self:
        if len(set(self.rubric_item_ids)) != len(self.rubric_item_ids):
            raise ValueError("rubric_item_ids must be unique")
        if self.action == "mapped" and not self.rubric_item_ids:
            raise ValueError("mapped coverage requires rubric items")
        if self.action != "mapped" and self.rubric_item_ids:
            raise ValueError("only mapped coverage may name rubric items")
        if self.action == "duplicate" and self.canonical_requirement_id is None:
            raise ValueError("duplicate coverage requires a canonical requirement")
        if self.action != "duplicate" and self.canonical_requirement_id is not None:
            raise ValueError("only duplicate coverage may name a canonical requirement")
        return self


class RubricClassificationView(Contract):
    kind: Literal["classification"] = "classification"
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    section_id: UUID | None = None
    item_id: UUID | None = None
    revision: int = Field(ge=2)
    review_domain: ReviewDomain
    reason: NonBlank
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"

    @model_validator(mode="after")
    def exactly_one_subject(self) -> Self:
        if (self.section_id is None) == (self.item_id is None):
            raise ValueError("classification requires exactly one section or item")
        return self


class RubricRevisionView(Contract):
    kind: Literal["revision"] = "revision"
    id: UUID
    org_id: UUID
    task_id: UUID
    rubric_id: UUID
    prior_rubric_id: UUID
    version: int = Field(ge=2)
    input_hash: Sha256
    reason: NonBlank
    revised_by: UUID
    revised_at: AwareDatetime
    actor_kind: Literal["session"] = "session"


type RubricHistoryItem = Annotated[
    RubricDecisionView | RubricCoverageDecisionView | RubricClassificationView | RubricRevisionView,
    Field(discriminator="kind"),
]


class ScoreRequest(AssessmentRequest):
    rubric_id: UUID


class ScorePreview(AssessmentPreview[AssessmentInput]):
    rubric_id: UUID
    rubric_version: int = Field(ge=1)
    rubric_input_hash: Sha256
    prompt_version: NonBlank
    schema_version: NonBlank
    scoring_rule_version: NonBlank
    preflight_unassessable_item_ids: list[UUID]
    limitations: list[str]

    @model_validator(mode="after")
    def preflight_items_are_selected(self) -> Self:
        if len(set(self.preflight_unassessable_item_ids)) != len(
            self.preflight_unassessable_item_ids
        ):
            raise ValueError("preflight_unassessable_item_ids must be unique")
        if not set(self.preflight_unassessable_item_ids) <= set(self.selected_item_ids):
            raise ValueError("preflight unassessable items must be selected rubric items")
        return self


class ScoreItemView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    rubric_item_id: UUID
    requirement_id: UUID
    anchor_response_item_id: UUID
    response_item_ids: list[UUID]
    section_key: NonBlank
    anchor_partition: ScorePartition
    outcome: ScoreOutcome
    score_range: ScoreRange | None = None
    estimated_score: Money | None = None
    reason_code: NonBlank
    reason: NonBlank
    deduction_reasons: list[NonBlank]
    strengthening_actions: list[NonBlank]
    citations: list[VerifiedCitation]
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def assessment_is_bounded_and_cited(self) -> Self:
        if len(set(self.response_item_ids)) != len(self.response_item_ids):
            raise ValueError("response_item_ids must be unique")
        if self.outcome == "assessed":
            citation_kinds = {citation.kind for citation in self.citations}
            if (
                self.estimated_score is None
                or self.score_range is None
                or not self.response_item_ids
                or not {"tender", "draft"} <= citation_kinds
            ):
                raise ValueError(
                    "assessed score items require bounds, a score, supporting response rows, "
                    "and tender plus draft citations"
                )
            if not self.score_range.minimum <= self.estimated_score <= self.score_range.maximum:
                raise ValueError("estimated score must stay inside confirmed rubric bounds")
            if self.estimated_score < self.score_range.maximum and not self.deduction_reasons:
                raise ValueError("a score below the confirmed maximum requires deduction reasons")
        elif self.estimated_score is not None or self.response_item_ids:
            raise ValueError("unassessable score items cannot contain a score or supporting rows")
        return self


class ScoreSectionSummary(Contract):
    section_key: NonBlank
    title: NonBlank
    aggregation: AggregationRule
    aggregation_assessable: bool
    aggregation_rule_text: NonBlank | None = None
    cap: Money | None = None
    configured_range: ScoreRange | None = None
    assessed_items: int = Field(ge=0)
    unassessable_items: int = Field(ge=0)
    assessed_subtotal: Money
    possible_range: ScoreRange | None = None
    status: Literal["estimated", "range_only", "unavailable"]
    estimated_score: Money | None = None

    @model_validator(mode="after")
    def subtotal_is_not_an_estimate(self) -> Self:
        executable = self.aggregation in {"sum", "weighted_sum", "capped_sum"}
        if self.aggregation_assessable != executable:
            raise ValueError("aggregation_assessable must reflect the supported algorithms")
        if self.aggregation in {"formula", "non_additive"} and self.aggregation_rule_text is None:
            raise ValueError("unsupported aggregation rules require their fixed original wording")
        if (self.aggregation == "capped_sum") != (self.cap is not None):
            raise ValueError("only capped_sum requires a cap")
        if (self.status == "estimated") != (self.estimated_score is not None):
            raise ValueError("only an estimated section may expose estimated_score")
        if self.status == "estimated" and self.unassessable_items:
            raise ValueError("a section with unassessable items cannot expose an estimate")
        return self


class ScoreRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    run_id: UUID
    input: AssessmentInput
    rubric_id: UUID
    rubric_version: int = Field(ge=1)
    rubric_input_hash: Sha256
    prompt_version: NonBlank
    schema_version: NonBlank
    scoring_rule_version: NonBlank
    assessment_date: date
    created_at: AwareDatetime
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    invalidation_codes: list[str]
    assessed_items: int = Field(ge=0)
    unassessable_items: int = Field(ge=0)
    assessed_subtotal: Money
    overall_aggregation: AggregationRule
    overall_aggregation_assessable: bool
    overall_rule_text: NonBlank | None = None
    overall_cap: Money | None = None
    possible_range: ScoreRange | None = None
    total_status: Literal["estimated", "range_only", "unavailable"]
    estimated_total: Money | None = None
    limitations: list[str]
    usage_record_ids: list[UUID]
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def total_is_complete_or_absent(self) -> Self:
        executable = self.overall_aggregation in {"sum", "weighted_sum", "capped_sum"}
        if self.overall_aggregation_assessable != executable:
            raise ValueError("overall aggregation assessability must use supported algorithms")
        if (
            self.overall_aggregation in {"formula", "non_additive"}
            and self.overall_rule_text is None
        ):
            raise ValueError("unsupported overall aggregation requires its fixed original wording")
        if (self.overall_aggregation == "capped_sum") != (self.overall_cap is not None):
            raise ValueError("only capped_sum requires an overall cap")
        if (self.total_status == "estimated") != (self.estimated_total is not None):
            raise ValueError("only a complete estimated report may expose estimated_total")
        if self.total_status == "estimated" and (
            self.completion != "complete" or self.unassessable_items
        ):
            raise ValueError("partial or unassessable reports cannot expose estimated_total")
        return self


class ScoreReportData(Contract):
    report: ScoreRunView
    sections: list[ScoreSectionSummary]
    items: list[ScoreItemView]


class ScoreJobResult(AssessmentJobResult):
    assessed_items: int = Field(ge=0)
    unassessable_items: int = Field(ge=0)
    assessed_subtotal: Money
    total_status: Literal["estimated", "range_only", "unavailable"]
    estimated_total: Money | None = None

    @model_validator(mode="after")
    def total_is_complete_or_absent(self) -> Self:
        if (self.total_status == "estimated") != (self.estimated_total is not None):
            raise ValueError("only an estimated job result may expose estimated_total")
        if self.total_status == "estimated" and (
            self.completion != "complete" or self.unassessable_items
        ):
            raise ValueError("partial or unassessable job results cannot expose estimated_total")
        return self


class RubricProviderRequirement(Contract):
    requirement_id: UUID
    tender_ref: NonBlank


class RubricProviderRequest(Contract):
    requirements: list[RubricProviderRequirement] = Field(min_length=1)
    context: OutboundContext


class ProposedRubricSection(Contract):
    key: NonBlank
    title: NonBlank
    order: int = Field(ge=1)
    aggregation: AggregationRule
    aggregation_rule_text: NonBlank | None = None
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    cap: Money | None = None
    included_in_overall_total: bool
    ambiguity_reason: NonBlank | None = None
    citations: list[ModelEvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def cap_matches_aggregation(self) -> Self:
        if (self.aggregation == "capped_sum") != (self.cap is not None):
            raise ValueError("only capped_sum requires a cap")
        if self.aggregation in {"formula", "non_additive"} and self.aggregation_rule_text is None:
            raise ValueError("unsupported aggregation rules require their fixed original wording")
        return self


class ProposedRubricItem(Contract):
    requirement_id: UUID
    section_key: NonBlank
    key: NonBlank
    title: NonBlank
    rule_text: NonBlank
    order: int = Field(ge=1)
    assessment_mode: AssessmentMode
    score_range: ScoreRange | None = None
    weight: Weight | None = None
    ambiguity_reason: NonBlank | None = None
    citations: list[ModelEvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def proposal_is_explicit(self) -> Self:
        if self.assessment_mode == "model_assessable" and self.score_range is None:
            raise ValueError("model-assessable proposals require score bounds")
        if self.assessment_mode != "model_assessable" and self.ambiguity_reason is None:
            raise ValueError("non-model proposals require an explicit reason")
        return self


class RubricWireOutput(Contract):
    sections: list[ProposedRubricSection]
    items: list[ProposedRubricItem]
    overall_aggregation: AggregationRule
    overall_rule_text: NonBlank | None = None
    overall_score_range: ScoreRange | None = None
    overall_cap: Money | None = None

    @model_validator(mode="after")
    def cap_matches_aggregation(self) -> Self:
        if (self.overall_aggregation == "capped_sum") != (self.overall_cap is not None):
            raise ValueError("only capped_sum requires an overall cap")
        if (
            self.overall_aggregation in {"formula", "non_additive"}
            and self.overall_rule_text is None
        ):
            raise ValueError("unsupported overall aggregation requires its fixed original wording")
        return self


class RubricAnsweredBatch(Contract):
    requested_requirement_ids: list[UUID]
    sent_refs: list[str]
    output: RubricWireOutput


class RubricProviderResult(Contract):
    batches: list[RubricAnsweredBatch]
    usages: list[ProviderUsage]
    failure: AssessmentFailure | None = None


class RubricProvider(Protocol):
    name: str
    model: str
    version: str
    test_only: bool

    async def extract_rubric(self, request: RubricProviderRequest) -> RubricProviderResult: ...


class ScoreProviderItem(Contract):
    rubric_item_id: UUID
    requirement_id: UUID
    section_key: NonBlank
    tender_ref: NonBlank
    rule_ref: NonBlank
    draft_refs: list[NonBlank] = Field(min_length=1)
    anchor_response_item_id: UUID
    anchor_partition: ScorePartition
    anchor_gap_reason_codes: list[NonBlank]
    assessment_mode: Literal["model_assessable"] = "model_assessable"
    score_range: ScoreRange | None = None

    @model_validator(mode="after")
    def refs_are_unique(self) -> Self:
        if len(set(self.draft_refs)) != len(self.draft_refs):
            raise ValueError("draft_refs must be unique")
        if self.tender_ref == self.rule_ref or self.tender_ref in self.draft_refs:
            raise ValueError("tender, rule and draft refs must be distinct")
        if self.rule_ref in self.draft_refs:
            raise ValueError("rubric rule refs cannot be draft refs")
        return self


class ScoreProviderRequest(Contract):
    assessment_date: date
    items: list[ScoreProviderItem] = Field(min_length=1)
    context_only_refs: list[NonBlank] = Field(default_factory=list)
    context: OutboundContext

    @model_validator(mode="after")
    def context_only_refs_are_disjoint(self) -> Self:
        item_refs = {
            ref for item in self.items for ref in (item.tender_ref, item.rule_ref, *item.draft_refs)
        }
        if len(set(self.context_only_refs)) != len(self.context_only_refs):
            raise ValueError("context-only refs must be unique")
        if item_refs & set(self.context_only_refs):
            raise ValueError("context-only refs cannot be item refs")
        return self


class ProposedScoreAssessment(Contract):
    rubric_item_id: UUID
    outcome: ScoreOutcome
    estimated_score: Money | None = None
    reason_code: NonBlank
    reason: NonBlank
    deduction_reasons: list[NonBlank]
    strengthening_actions: list[NonBlank]
    tender_citations: list[ModelEvidenceRef]
    draft_citations: list[ModelEvidenceRef]

    @model_validator(mode="after")
    def outcome_is_explicit(self) -> Self:
        if self.outcome == "assessed" and (
            self.estimated_score is None or not self.tender_citations or not self.draft_citations
        ):
            raise ValueError("assessed output requires a score and tender plus draft citations")
        if self.outcome == "unassessable" and self.estimated_score is not None:
            raise ValueError("unassessable output cannot contain an estimated score")
        return self


class ScoreWireOutput(Contract):
    items: list[ProposedScoreAssessment]


class ScoreAnsweredBatch(Contract):
    requested_rubric_item_ids: list[UUID]
    sent_refs: list[str]
    output: ScoreWireOutput


class ScoreProviderResult(Contract):
    batches: list[ScoreAnsweredBatch]
    usages: list[ProviderUsage]
    failure: AssessmentFailure | None = None


class ScoreProvider(Protocol):
    name: str
    model: str
    version: str
    test_only: bool

    async def score(self, request: ScoreProviderRequest) -> ScoreProviderResult: ...
