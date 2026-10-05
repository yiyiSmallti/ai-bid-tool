"""待批准的 B09 契约；不注册到 API、CLI 或作业处理器。"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from app.schemas.confidential_contracts import ConfidentialKind
from app.schemas.contracts import Contract, Cost, ProviderUsage, Result, Source
from app.schemas.response_card_contracts import ModelEvidenceRef, ReviewDomain
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type NonBlank = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
]
type Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8)]
type CheckCLIResult = Result
type RiskLevel = Literal["disqualification_risk", "deduction_risk", "info"]
type RuleCode = Literal[
    "mandatory_response_missing",
    "negative_deviation",
    "unconfirmed_evidence",
    "certificate_expired",
    "certificate_not_yet_valid",
    "certificate_date_unknown",
    "semantic_contradiction",
    "insufficient_support",
    "obligation_coverage_uncertain",
]


class AssessmentRequest(Contract):
    draft_id: UUID
    assessment_date: date
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


class AssessmentInput(Contract):
    org_id: UUID
    task_id: UUID
    draft_id: UUID
    extraction_job_id: UUID
    document_id: UUID
    input_hash: Sha256
    draft_input_hash: Sha256
    assessment_date: date
    scope: Literal["confirmed_draft"] = "confirmed_draft"


class AssessmentPreview[InputT: Contract](Contract):
    dry_run: Literal[True] = True
    input: InputT
    selected_item_ids: list[UUID]
    estimated_cost: Cost
    estimated_charge: Money | None
    billing_currency: str = Field(min_length=3, max_length=3)
    cost_basis: Literal["known", "unknown"]
    cost_basis_reason: NonBlank
    estimate_kind: Literal["first_pass_upper_bound"] = "first_pass_upper_bound"
    admission_blocker: str | None = None
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    provider_config_id: UUID | None = None
    provider_source: Literal["org", "platform"] | None = None
    platform_model_id: str | None = None
    model_revision: int | None = Field(default=None, ge=1)
    model: str | None = None
    reasoning: str | None = None
    redaction_revision: int = Field(ge=1)
    redaction_rule_version: NonBlank
    redacted_counts: dict[str, int]
    max_charge: Money | None = None

    @model_validator(mode="after")
    def coherent_preview(self) -> Self:
        if len(set(self.selected_item_ids)) != len(self.selected_item_ids):
            raise ValueError("selected_item_ids must be unique")
        if any(value < 0 for value in self.redacted_counts.values()):
            raise ValueError("redaction counts cannot be negative")
        if self.cost_basis == "unknown" and self.estimated_charge is not None:
            raise ValueError("unknown price cannot produce an estimated charge")
        return self


class AssessmentJobAccepted(Contract):
    job_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    cached: bool


class AssessmentJobResult(Contract):
    report_id: UUID
    job_id: UUID
    completion: Literal["complete", "partial"]
    usage_record_ids: list[UUID]
    charge: Money | None
    billing_currency: str = Field(min_length=3, max_length=3)
    stop_reason: str | None = None


class AssessmentListData(Contract):
    task_id: UUID
    total: int = Field(ge=0)
    next_cursor: str | None = None


class TenderCitation(Contract):
    """已由服务端对固定原文核验；Pydantic 不替代文本与父对象查询。"""

    kind: Literal["tender"] = "tender"
    source: Source


class DraftCitation(Contract):
    kind: Literal["draft"] = "draft"
    draft_id: UUID
    response_item_id: UUID
    card_revision_id: UUID
    field: Literal["response_text", "deviation_note"]
    quote: str = Field(min_length=1, max_length=20000)


class EvidenceCitation(Contract):
    kind: Literal["evidence"] = "evidence"
    evidence_id: UUID
    quote: str = Field(min_length=1, max_length=20000)


type VerifiedCitation = Annotated[
    TenderCitation | DraftCitation | EvidenceCitation, Field(discriminator="kind")
]


class OutboundText(Contract):
    ref: NonBlank
    text: str = Field(min_length=1, max_length=200000)


class ConfidentialHint(Contract):
    placeholder: NonBlank
    label: NonBlank
    kind: ConfidentialKind


class OutboundContext(Contract):
    texts: list[OutboundText]
    confidential_fields: list[ConfidentialHint] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_refs(self) -> Self:
        refs = [item.ref for item in self.texts]
        if len(set(refs)) != len(refs):
            raise ValueError("outbound refs must be unique")
        return self


class AssessmentFailure(Contract):
    code: NonBlank
    retryable: bool
    refused: bool = False


class CheckRequest(AssessmentRequest):
    mode: Literal["rules", "combined"] = "combined"

    @model_validator(mode="after")
    def rules_have_no_model_options(self) -> Self:
        if self.mode == "rules" and (self.reasoning is not None or self.max_charge is not None):
            raise ValueError("rules mode cannot accept model reasoning or a charge cap")
        return self


class CheckPreview(AssessmentPreview[AssessmentInput]):
    mode: Literal["rules", "combined"]
    rule_version: NonBlank
    prompt_version: str | None
    schema_version: NonBlank
    rules_applicable: int = Field(ge=0)
    semantic_items: int = Field(ge=0)
    gap_requirements: int = Field(ge=0)
    limitations: list[str]


class RuleObservation(Contract):
    code: RuleCode
    outcome: Literal["risk", "clear", "unknown", "not_applicable"]
    reason_code: NonBlank
    task_certificate_id: UUID | None = None
    certificate_revision_id: UUID | None = None

    @model_validator(mode="after")
    def certificate_binding_is_complete(self) -> Self:
        if (self.task_certificate_id is None) != (self.certificate_revision_id is None):
            raise ValueError("certificate selection and revision must be paired")
        return self


class CheckItemView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    requirement_id: UUID
    response_item_id: UUID
    card_revision_id: UUID | None
    partition: Literal["response", "comply_only", "gap"]
    source: Source
    rules: list[RuleObservation]
    semantic_status: Literal["assessed", "unassessed", "not_requested"]
    semantic_reason_code: str | None = None
    finding_ids: list[UUID]


class CheckCertificateView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    task_certificate_id: UUID
    certificate_revision_id: UUID
    requirement_ids: list[UUID]
    assessment_date: date
    date_status: Literal["valid", "expired", "not_yet_valid", "unknown"]


class FindingCitationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    finding_id: UUID
    citation: VerifiedCitation


class FindingView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    check_item_id: UUID
    requirement_id: UUID
    method: Literal["deterministic", "semantic"]
    code: RuleCode
    severity: RiskLevel
    review_domain: ReviewDomain | None
    reason: NonBlank
    source: Source
    citations: list[FindingCitationView] = Field(max_length=20)
    status: Literal["open", "dismissed"]
    revision: int = Field(ge=1)
    latest_decision_id: UUID | None
    advisory_only: Literal[True] = True


class FindingDecisionRequest(Contract):
    expected_revision: int = Field(ge=1)
    expected_input_hash: Sha256
    action: Literal["dismiss", "reopen"]
    reason: NonBlank


class FindingDecisionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    finding_id: UUID
    revision: int = Field(ge=2)
    action: Literal["dismiss", "reopen"]
    reason: NonBlank
    reason_sha256: Sha256
    decided_by: UUID
    decided_at: AwareDatetime
    actor_kind: Literal["session"] = "session"


class FindingDecisionData(Contract):
    finding: FindingView
    decision: FindingDecisionView


class CheckRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    run_id: UUID
    input: AssessmentInput
    mode: Literal["rules", "combined"]
    rule_version: NonBlank
    prompt_version: str | None
    schema_version: NonBlank
    created_at: AwareDatetime
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    invalidation_codes: list[str]
    item_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    unassessed_count: int = Field(ge=0)
    limitations: list[str]
    usage_record_ids: list[UUID]
    advisory_only: Literal[True] = True


class CheckReportData(Contract):
    report: CheckRunView
    coverage: list[CheckItemView]
    certificates: list[CheckCertificateView]


class CheckJobResult(AssessmentJobResult):
    checked_requirements: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    unassessed_requirements: int = Field(ge=0)


class CheckProviderItem(Contract):
    requirement_id: UUID
    tender_ref: NonBlank
    bid_refs: list[NonBlank]
    coverage: Literal["response", "comply_only", "gap"]


class CheckProviderRequest(Contract):
    items: list[CheckProviderItem] = Field(min_length=1, max_length=2000)
    context: OutboundContext

    @model_validator(mode="after")
    def items_use_sent_context(self) -> Self:
        ids = [item.requirement_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("request requirement IDs must be unique")
        refs = {text.ref for text in self.context.texts}
        for item in self.items:
            if not {item.tender_ref, *item.bid_refs}.issubset(refs):
                raise ValueError("request refs must belong to outbound context")
            if len(item.bid_refs) != len(set(item.bid_refs)):
                raise ValueError("bid refs must be unique")
            if item.coverage != "response" and item.bid_refs:
                raise ValueError("gap and comply-only items cannot supply proposed response text")
        return self


class ProposedFinding(Contract):
    code: Literal["semantic_contradiction", "insufficient_support", "obligation_coverage_uncertain"]
    severity: RiskLevel
    reason: NonBlank
    citations: list[ModelEvidenceRef] = Field(min_length=1, max_length=20)


class CheckProviderAssessment(Contract):
    requirement_id: UUID
    outcome: Literal["no_risk_found", "risk", "unknown"]
    findings: list[ProposedFinding] = Field(max_length=20)
    reason_code: str | None = None
    supporting_references: list[ModelEvidenceRef]

    @model_validator(mode="after")
    def explicit_outcome(self) -> Self:
        if (self.outcome == "risk") != bool(self.findings):
            raise ValueError("only risk assessments contain findings")
        if self.outcome == "unknown" and not self.reason_code:
            raise ValueError("unknown requires a reason code")
        if self.outcome == "no_risk_found" and not self.supporting_references:
            raise ValueError("a clear semantic result requires supporting references")
        return self


class CheckWireOutput(Contract):
    items: list[CheckProviderAssessment]


class CheckAnsweredBatch(Contract):
    """保留 wire 候选以逐项拒绝漏答/串答；不代表输出已经通过引用验证。"""

    requested_requirement_ids: list[UUID]
    sent_refs: list[str]
    output: CheckWireOutput


class CheckProviderResult(Contract):
    batches: list[CheckAnsweredBatch]
    usages: list[ProviderUsage]
    failure: AssessmentFailure | None = None


class CheckProvider(Protocol):
    name: str
    model: str
    version: str
    test_only: bool

    async def check(self, request: CheckProviderRequest) -> CheckProviderResult: ...
