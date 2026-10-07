"""B03 parameter assessment: approved, not implemented.

Payload validation and interface declarations only. Importing this module registers
no routes, jobs, tables, unit tables or comparison implementations. Database-backed
source verification, live authorization and freshness remain service obligations.
"""

from decimal import Decimal
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from app.schemas.budget_contracts import BudgetJobResult, BudgetPreflightData, BudgetProviderUsage
from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Result, Source
from app.schemas.feature_contracts import FeatureRevision
from app.schemas.requirement_confirmation import (
    RequirementConsumptionEntry,
    VerifiedRequirementSource,
)
from app.schemas.resource_contracts import ProductRevision
from app.schemas.response_card_contracts import Deviation, ReviewDomain
from app.schemas.screenshot_contracts import Sha256
from app.services.auth import Identity
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

RESULT_CONTRACT_VERSION = CONTRACT_VERSION
type ParameterResult = Result
type ParameterCost = Cost
type Revision = Annotated[int, Field(strict=True, ge=1)]
type ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
type Quote = Annotated[str, StringConstraints(min_length=1, max_length=20000)]
type Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")]
type DecimalText = Annotated[
    str, StringConstraints(pattern=r"^-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,12})?$")
]
type Unit = Literal[
    "1",
    "count",
    "B",
    "kB",
    "MB",
    "GB",
    "TB",
    "KiB",
    "MiB",
    "GiB",
    "TiB",
    "bit/s",
    "Mbit/s",
    "Gbit/s",
    "Hz",
    "MHz",
    "GHz",
    "mm",
    "cm",
    "m",
    "ms",
    "s",
    "degC",
    "%",
]
type Verdict = Literal["meets", "does_not_meet", "unknown", "ambiguous"]
type ReviewState = Literal["unconfirmed", "confirmed", "rejected", "invalidated"]
type NextAction = Literal[
    "confirm_requirement",
    "resolve_condition",
    "supply_product_source",
    "review_assessment",
    "review_response",
    "recompute",
    "none",
]
type IssueCode = Literal[
    "unsupported_parameter",
    "unsupported_expression",
    "ambiguous_unit",
    "ambiguous_scope",
    "missing_unit",
    "missing_fact",
    "conflicting_facts",
    "invalid_source",
    "model_mismatch",
    "configuration_mismatch",
    "simulated_resource",
    "prototype_only",
    "unverified_declaration",
    "unsupported_tolerance",
    "stale_input",
]


class NumericValue(Contract):
    kind: Literal["number"] = "number"
    value: DecimalText
    unit: Unit


class RangeValue(Contract):
    kind: Literal["range"] = "range"
    lower: DecimalText
    upper: DecimalText
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    unit: Unit

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if Decimal(self.lower) > Decimal(self.upper):
            raise ValueError("range lower bound exceeds upper bound")
        if Decimal(self.lower) == Decimal(self.upper) and not (
            self.lower_inclusive and self.upper_inclusive
        ):
            raise ValueError("a singleton range must include its endpoint")
        return self


class EnumerationValue(Contract):
    kind: Literal["enumeration"] = "enumeration"
    values: list[ShortText] = Field(min_length=1, max_length=32)
    unit: Literal["1"] = "1"

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.values)) != len(self.values):
            raise ValueError("enumeration members must be unique")
        return self


class BooleanValue(Contract):
    kind: Literal["boolean"] = "boolean"
    value: bool = Field(strict=True)
    unit: Literal["1"] = "1"


type Value = Annotated[
    NumericValue | RangeValue | EnumerationValue | BooleanValue, Field(discriminator="kind")
]


class Parameter(Contract):
    key: Code
    label: ShortText
    subject: Literal["device", "module", "port", "feature"]
    basis: Literal["configured", "supported", "maximum", "minimum"]
    configuration: ShortText | None = None


class QuoteSpan(Contract):
    """Half-open Python character offsets into one immutable citation quote."""

    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, ge=1)
    text: Quote

    @model_validator(mode="after")
    def length(self) -> Self:
        if self.end - self.start != len(self.text):
            raise ValueError("span must delimit literal text")
        return self


class FieldSupport(Contract):
    field: Literal[
        "parameter", "operator", "value", "unit", "scope", "tolerance", "starred", "connective"
    ]
    span: QuoteSpan


class Tolerance(Contract):
    kind: Literal["absolute", "relative_percent"]
    amount: DecimalText
    unit: Unit
    support: QuoteSpan

    @model_validator(mode="after")
    def positive_amount(self) -> Self:
        if Decimal(self.amount) < 0:
            raise ValueError("tolerance must be nonnegative")
        if self.kind == "relative_percent" and self.unit != "%":
            raise ValueError("relative tolerance uses percent")
        return self


class Atom(Contract):
    id: Code
    parameter: Parameter
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "within", "covers", "contains_all", "contains_any"
    ]
    value: Value
    tolerance: Tolerance | None = None
    support: list[FieldSupport] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def compatible_shape(self) -> Self:
        permitted = {
            "number": {"eq", "ne", "gt", "gte", "lt", "lte"},
            "range": {"within", "covers"},
            "enumeration": {"eq", "contains_all", "contains_any"},
            "boolean": {"eq"},
        }
        if self.operator not in permitted[self.value.kind]:
            raise ValueError("operator does not apply to this value shape")
        if self.tolerance is not None and (self.operator != "eq" or self.value.kind != "number"):
            raise ValueError("v1 tolerance applies only to numeric equality")
        return self


class ResolvedCondition(Contract):
    kind: Literal["resolved"] = "resolved"
    connective: Literal["all", "any"] = "all"
    atoms: list[Atom] = Field(min_length=1, max_length=16)
    connective_support: QuoteSpan | None = None

    @model_validator(mode="after")
    def identifiers_and_connective(self) -> Self:
        if len({atom.id for atom in self.atoms}) != len(self.atoms):
            raise ValueError("atom ids must be unique")
        if len(self.atoms) > 1 and self.connective_support is None:
            raise ValueError("compound conditions need literal connective support")
        return self


class Ambiguity(Contract):
    code: IssueCode
    message: ShortText
    span: QuoteSpan | None = None


class AmbiguousCondition(Contract):
    kind: Literal["ambiguous"] = "ambiguous"
    issues: list[Ambiguity] = Field(min_length=1, max_length=16)
    suggested_interpretations: list[ResolvedCondition] = Field(default_factory=list, max_length=3)


type Condition = Annotated[ResolvedCondition | AmbiguousCondition, Field(discriminator="kind")]


class RuleVersions(Contract):
    parameter_registry: Code
    unit_table: Code
    extraction_rules: Code
    comparator: Code
    manifest_sha256: Sha256


class UnitRule(Contract):
    """Reviewed release entry, not an API for model-authored conversions."""

    source: Unit
    target: Unit
    dimension: Code
    numerator: int = Field(strict=True, gt=0)
    denominator: int = Field(strict=True, gt=0)
    offset: DecimalText = "0"


class ConversionTrace(Contract):
    unit_table: Code
    rule_id: Code
    original: NumericValue | RangeValue
    normalized: NumericValue | RangeValue


class ProductPin(Contract):
    kind: Literal["product"] = "product"
    selection_id: UUID
    lot: ShortText | None = None
    revision: ProductRevision


class FeaturePin(Contract):
    kind: Literal["feature"] = "feature"
    selection_id: UUID
    lot: ShortText | None = None
    revision: FeatureRevision
    product_selection_id: UUID
    product_revision: ProductRevision

    @model_validator(mode="after")
    def parent_product(self) -> Self:
        if self.revision.org_id != self.product_revision.org_id or (
            self.revision.data.product_id != self.product_revision.product_id
        ):
            raise ValueError("feature and parent product revisions must belong together")
        return self


type ResourcePin = Annotated[ProductPin | FeaturePin, Field(discriminator="kind")]


class DocumentCitation(Contract):
    kind: Literal["document"] = "document"
    verified: VerifiedRequirementSource


class ArchiveCitation(Contract):
    """Verified literal text of a genuine archive; never a live URL/search excerpt."""

    kind: Literal["vendor_archive"] = "vendor_archive"
    archive_id: UUID
    source_text_id: UUID
    archive_artifact_id: UUID
    artifact_sha256: Sha256
    content_sha256: Sha256
    text_sha256: Sha256
    binding_sha256: Sha256
    locator: Literal["pdf_page", "html_text"]
    page: int | None = Field(default=None, strict=True, ge=1)
    text_block: Code
    quote: Quote
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, ge=1)
    captured_at: AwareDatetime
    verifier_version: Code
    extractor_version: Code

    @model_validator(mode="after")
    def archive_location(self) -> Self:
        if (self.locator == "pdf_page") != (self.page is not None):
            raise ValueError("only PDF archive citations carry a page")
        if self.end - self.start != len(self.quote):
            raise ValueError("archive span must delimit the literal quote")
        return self


type ProductCitation = Annotated[DocumentCitation | ArchiveCitation, Field(discriminator="kind")]


class FactInput(Contract):
    id: Code
    parameter: Parameter
    value: Value
    citation: ProductCitation
    model_identity_support: QuoteSpan
    value_support: list[FieldSupport] = Field(min_length=1, max_length=16)


class FactExclusion(Contract):
    previous_fact_id: Code
    reason: ShortText
    applicable_source: ProductCitation


class FactConflictResolution(Contract):
    previous_fact_set_id: UUID
    previous_fact_set_sha256: Sha256
    retained_fact_ids: list[Code] = Field(min_length=1, max_length=64)
    excluded: list[FactExclusion] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def disjoint(self) -> Self:
        retained = set(self.retained_fact_ids)
        excluded = {item.previous_fact_id for item in self.excluded}
        if len(retained) != len(self.retained_fact_ids) or len(excluded) != len(self.excluded):
            raise ValueError("conflict disposition ids must be unique")
        if retained & excluded:
            raise ValueError("a fact cannot be both retained and excluded")
        return self


class FactSetCreate(Contract):
    selection_id: UUID
    resource_kind: Literal["product", "feature"]
    expected_resource_revision_id: UUID
    expected_fact_revision: int = Field(strict=True, ge=0)
    previous_fact_set_id: UUID | None = None
    reason: ShortText
    conflict_resolution: FactConflictResolution | None = None
    facts: list[FactInput] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def fact_replacement(self) -> Self:
        if (self.expected_fact_revision == 0) != (self.previous_fact_set_id is None):
            raise ValueError("fact replacements must bind their previous set")
        if len({fact.id for fact in self.facts}) != len(self.facts):
            raise ValueError("fact ids must be unique within a set")
        if self.conflict_resolution is not None:
            resolution = self.conflict_resolution
            if resolution.previous_fact_set_id != self.previous_fact_set_id:
                raise ValueError("conflict resolution must bind the replaced fact set")
            if not set(resolution.retained_fact_ids).issubset({fact.id for fact in self.facts}):
                raise ValueError("retained facts must appear in the new set")
        return self


class FactSetView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    revision: Revision
    resource: ResourcePin
    previous_fact_set_id: UUID | None = None
    conflict_resolution: FactConflictResolution | None = None
    resolution_review_receipt_id: UUID | None = None
    facts: list[FactInput] = Field(min_length=1, max_length=64)
    content_sha256: Sha256
    provenance: Literal["declared", "simulated"]
    verification: Literal["verified_source", "needs_review"]


class SourceTextView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    archive_id: UUID
    archive_artifact_id: UUID
    artifact_sha256: Sha256
    content_sha256: Sha256
    text_sha256: Sha256
    extractor_version: Code
    binding_sha256: Sha256
    byte_count: int = Field(strict=True, ge=0, le=8 * 1024 * 1024)
    block_count: int = Field(strict=True, ge=0, le=5000)


class ConditionCreate(Contract):
    expected_requirement_review_revision: Revision
    expected_requirement_review_hash: Sha256
    expected_condition_revision: int = Field(strict=True, ge=0)
    condition: Condition
    reason: ShortText


class ConditionView(Contract):
    """id identifies the immutable revision; condition_id identifies its head."""

    id: UUID
    condition_id: UUID
    org_id: UUID
    task_id: UUID
    requirement_id: UUID
    extraction_job_id: UUID
    revision: Revision
    requirement: RequirementConsumptionEntry
    source: VerifiedRequirementSource
    starred: bool
    condition: Condition
    rules: RuleVersions
    content_sha256: Sha256
    review_state: ReviewState
    review_revision: Revision
    review_receipt_id: UUID | None = None
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def confirmation_shape(self) -> Self:
        if self.requirement.requirement_id != self.requirement_id:
            raise ValueError("requirement pin mismatch")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("confirmation actor and time must travel together")
        if self.review_state == "confirmed" and (
            self.confirmed_by is None
            or self.review_receipt_id is None
            or self.condition.kind != "resolved"
            or self.requirement.state != "confirmed"
        ):
            raise ValueError("confirmation needs a resolved condition and human receipt")
        return self


class Decision(Contract):
    action: Literal["confirm", "reject", "reopen"]
    expected_revision: Revision
    expected_input_hash: Sha256
    reason: ShortText


class ReviewReceipt(Contract):
    """Immutable historical decision, separate from current effective eligibility."""

    id: UUID
    org_id: UUID
    task_id: UUID
    subject_kind: Literal["condition", "assessment", "fact_conflict"]
    subject_id: UUID
    subject_revision: Revision
    review_revision: Revision
    input_hash: Sha256
    action: Literal["confirm", "reject", "reopen"]
    actor_user_id: UUID
    actor_membership_id: UUID
    actor_kind: Literal["session"] = "session"
    reason_sha256: Sha256
    occurred_at: AwareDatetime


class InputManifest(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_set_revision: Revision
    requirement: RequirementConsumptionEntry
    condition_revision_id: UUID
    condition_sha256: Sha256
    condition_review_receipt_id: UUID | None
    condition_review_revision: Revision
    fact_set_id: UUID | None
    fact_set_sha256: Sha256 | None
    resource: ResourcePin
    rules: RuleVersions
    task_membership_sha256: Sha256
    review_policy_sha256: Sha256
    provenance_sha256: Sha256
    input_hash: Sha256

    @model_validator(mode="after")
    def fact_pin(self) -> Self:
        if (self.fact_set_id is None) != (self.fact_set_sha256 is None):
            raise ValueError("fact id and hash must travel together")
        return self


class AtomResult(Contract):
    atom_id: Code
    verdict: Verdict
    reason: ShortText
    tender_support: list[QuoteSpan] = Field(min_length=1, max_length=16)
    product_citations: list[ProductCitation] = Field(default_factory=list, max_length=8)
    conversions: list[ConversionTrace] = Field(default_factory=list, max_length=8)
    issues: list[Ambiguity] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def conclusive_has_both_sides(self) -> Self:
        if self.verdict in {"meets", "does_not_meet"} and not self.product_citations:
            raise ValueError("a conclusive comparison needs cited product facts")
        if self.verdict in {"unknown", "ambiguous"} and not self.issues:
            raise ValueError("an unresolved comparison needs an explicit issue")
        return self


class AssessmentView(Contract):
    """review_state is effective now; historical acceptance is a separate receipt."""

    id: UUID
    org_id: UUID
    task_id: UUID
    run_id: UUID
    revision: Revision
    manifest: InputManifest
    tender_source: VerifiedRequirementSource
    verdict: Verdict
    atoms: list[AtomResult] = Field(default_factory=list, max_length=16)
    issues: list[Ambiguity] = Field(default_factory=list, max_length=16)
    review_state: ReviewState
    review_revision: Revision
    review_receipt_id: UUID | None = None
    validity: Literal["current", "stale"]
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None
    owner_user_id: UUID | None
    next_action: NextAction
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def no_confirmation_shortcut(self) -> Self:
        if (self.org_id, self.task_id) != (self.manifest.org_id, self.manifest.task_id):
            raise ValueError("assessment and input manifest must share org/task")
        if self.manifest.resource.revision.org_id != self.org_id:
            raise ValueError("resource revision must belong to the assessment org")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("confirmation actor and time must travel together")
        if self.verdict in {"meets", "does_not_meet"} and not self.atoms:
            raise ValueError("a conclusive assessment needs per-atom proof")
        if self.verdict in {"unknown", "ambiguous"} and not self.issues:
            raise ValueError("unresolved assessments require issue codes")
        if self.review_state == "confirmed" and (
            self.verdict not in {"meets", "does_not_meet"}
            or self.validity != "current"
            or self.confirmed_by is None
            or self.review_receipt_id is None
            or self.manifest.condition_review_receipt_id is None
            or self.manifest.requirement.state != "confirmed"
        ):
            raise ValueError("only a current conclusive result with B02 approval can be confirmed")
        return self


class AssessmentTarget(Contract):
    requirement_id: UUID
    condition_revision_id: UUID
    resource_kind: Literal["product", "feature"]
    selection_id: UUID
    resource_revision_id: UUID
    fact_set_id: UUID | None


class RunRequest(Contract):
    extraction_job_id: UUID
    targets: list[AssessmentTarget] = Field(min_length=1, max_length=100)
    expected_input_hash: Sha256 | None = None
    dry_run: bool = False
    retry: bool = False

    @model_validator(mode="after")
    def exact_preflight(self) -> Self:
        if self.dry_run and self.retry:
            raise ValueError("dry-run cannot retry")
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("submission needs the preview input hash")
        if len({(t.requirement_id, t.selection_id) for t in self.targets}) != len(self.targets):
            raise ValueError("duplicate assessment target")
        return self


class ExtractionRequest(Contract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1, max_length=100)
    expected_input_hash: Sha256 | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def preflight_and_unique(self) -> Self:
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("submission needs the preview input hash")
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("duplicate requirement")
        return self


class PageQuery(Contract):
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=25, strict=True, ge=1, le=100)


class AssessmentQuery(PageQuery):
    extraction_job_id: UUID
    verdict: Verdict | None = None
    review_state: ReviewState | None = None
    requirement_id: UUID | None = None


class PageData(Contract):
    task_id: UUID
    returned: int = Field(strict=True, ge=0, le=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    has_more: bool
    as_of: AwareDatetime

    @model_validator(mode="after")
    def continuation(self) -> Self:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("continuation metadata disagrees")
        return self


class JobAccepted(Contract):
    task_id: UUID
    job_id: UUID
    input_hash: Sha256
    kind: Literal["parameter_extract", "parameter_assess"]
    state: Literal["queued", "running", "succeeded"]
    cached: bool = False


class RunSummary(Contract):
    task_id: UUID
    job_id: UUID
    run_id: UUID
    processed: int = Field(strict=True, ge=0, le=100)
    failed: int = Field(strict=True, ge=0, le=100)
    input_hash: Sha256
    next_action: NextAction
    budget: BudgetJobResult | None = None


class TargetReceipt(Contract):
    requirement_id: UUID
    selection_id: UUID | None = None
    status: Literal["published", "failed", "remaining"]
    condition_revision_id: UUID | None = None
    assessment_id: UUID | None = None
    error_code: Code | None = None
    reason: ShortText | None = None

    @model_validator(mode="after")
    def receipt_shape(self) -> Self:
        subjects = int(self.condition_revision_id is not None) + int(self.assessment_id is not None)
        if self.status == "published" and (subjects != 1 or self.error_code is not None):
            raise ValueError("published receipt identifies exactly one output and no error")
        if self.status != "published" and (subjects != 0 or self.error_code is None):
            raise ValueError("unpublished receipt identifies its blocker and no output")
        return self


class AuditEvent(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    action: Literal[
        "condition_proposed",
        "condition_revised",
        "condition_decided",
        "facts_created",
        "facts_conflict_resolved",
        "source_text_created",
        "assessment_started",
        "assessment_completed",
        "assessment_decided",
        "assessment_invalidated",
        "assessment_attached",
        "assessment_detached",
        "assessment_failed",
    ]
    object_id: UUID
    revision: Revision
    actor_kind: Literal["session", "token", "worker", "migration"]
    actor_user_id: UUID | None
    actor_membership_id: UUID | None
    token_id: UUID | None
    job_id: UUID | None
    run_id: UUID | None
    invocation_id: UUID | None
    request_id: UUID
    input_hash: Sha256
    rule_manifest_sha256: Sha256
    previous_state: ReviewState | None
    effective_state: ReviewState | None
    decision: ReviewReceipt | None
    reason_sha256: Sha256 | None
    occurred_at: AwareDatetime


class CardAttach(Contract):
    assessment_id: UUID
    expected_assessment_revision: Revision
    expected_input_hash: Sha256
    expected_card_version: Revision
    proposed_deviation: Deviation


class CardAttachment(Contract):
    card_id: UUID
    card_version: Revision
    assessment: AssessmentView
    review_domain: ReviewDomain
    response_state: Literal["draft"] = "draft"


class CandidateInput(Contract):
    requirement_id: UUID
    source: Source
    starred: bool
    rules: RuleVersions

    @model_validator(mode="after")
    def bounded_literal_input(self) -> Self:
        if not self.source.quote.strip() or len(self.source.quote) > 20000:
            raise ValueError("provider source quote must be nonblank and bounded")
        return self


class ExtractionCandidate(Contract):
    requirement_id: UUID
    condition: Condition


class ProviderBatch(Contract):
    items: list[ExtractionCandidate] = Field(max_length=20)
    usage: BudgetProviderUsage


class ConditionProvider(Protocol):
    """LLM proposes only; at most 20 inputs / 64 KiB serialized text per call."""

    async def propose(self, inputs: list[CandidateInput]) -> ProviderBatch: ...


class ConditionVerifier(Protocol):
    """Deterministic grammar, literal Source and registry checks; no model conversions."""

    async def verify(
        self, source: VerifiedRequirementSource, candidate: Condition, rules: RuleVersions
    ) -> Condition: ...


class ProductSourceVerifier(Protocol):
    """Loads and verifies actual org-scoped archives; never trusts client hashes."""

    async def project_archive(
        self, actor: Identity, task_id: UUID, archive_id: UUID
    ) -> SourceTextView: ...

    async def verify_fact(
        self, actor: Identity, task_id: UUID, resource: ResourcePin, fact: FactInput
    ) -> FactInput: ...


class ParameterComparator(Protocol):
    """Pure, versioned comparison; authorization/publication belongs to the service."""

    def compare(
        self, condition: ConditionView, facts: FactSetView | None, manifest: InputManifest
    ) -> list[AtomResult]: ...


class ParameterAssessmentService(Protocol):
    """Live Identity checks precede reads; writes use one task-locked transaction."""

    async def extract(
        self, actor: Identity, task_id: UUID, request: ExtractionRequest
    ) -> BudgetPreflightData | JobAccepted: ...

    async def revise_condition(
        self, actor: Identity, task_id: UUID, requirement_id: UUID, request: ConditionCreate
    ) -> ConditionView: ...

    async def condition(
        self, actor: Identity, task_id: UUID, requirement_id: UUID
    ) -> ConditionView: ...

    async def decide_condition(
        self, actor: Identity, task_id: UUID, condition_id: UUID, request: Decision
    ) -> ConditionView: ...

    async def create_facts(
        self, actor: Identity, task_id: UUID, request: FactSetCreate
    ) -> FactSetView: ...

    async def facts(self, actor: Identity, task_id: UUID, fact_set_id: UUID) -> FactSetView: ...

    async def run(
        self, actor: Identity, task_id: UUID, request: RunRequest
    ) -> BudgetPreflightData | JobAccepted: ...

    async def list_assessments(
        self, actor: Identity, task_id: UUID, query: AssessmentQuery
    ) -> tuple[PageData, list[AssessmentView]]: ...

    async def show(self, actor: Identity, task_id: UUID, assessment_id: UUID) -> AssessmentView: ...

    async def history(
        self, actor: Identity, task_id: UUID, assessment_id: UUID, query: PageQuery
    ) -> tuple[PageData, list[AuditEvent]]: ...

    async def decide(
        self, actor: Identity, task_id: UUID, assessment_id: UUID, request: Decision
    ) -> AssessmentView: ...

    async def attach(
        self, actor: Identity, task_id: UUID, card_id: UUID, request: CardAttach
    ) -> CardAttachment: ...
