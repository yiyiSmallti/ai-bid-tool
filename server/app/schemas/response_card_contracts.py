"""Contracts for reviewed response cards and deterministic draft assembly."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Category, Contract, Cost, Source
from app.schemas.evidence_source_contracts import EvidenceSourceArchive

type CardState = Literal["draft", "pending_review", "confirmed", "rejected", "needs_material"]
type ReviewDomain = Literal["commercial", "technical"]
type ResponseKind = Literal["evidence", "commitment"]
type Disposition = Literal["respond", "comply_only"]
type Deviation = Literal["none", "positive", "negative"]
type TableKind = Literal["substantive", "commercial", "technical"]
type GapReason = Literal[
    "missing_card",
    "unconfirmed",
    "rejected",
    "needs_material",
    "unclassified",
    "stale_material",
    "invalid_citation",
]

RESOURCE_FIELD_PATHS = {
    "product": frozenset(
        {"name", "vendor", "model", "model_version", "official_url", "whitepaper_url"}
    ),
    "feature": frozenset({"product_id", "name", "description", "status"}),
    "certificate": frozenset({"kind", "name", "number", "valid_from", "valid_until"}),
    "org_profile": frozenset(
        {"name", "registration_details", "performance_summary", "standard_wording"}
    ),
}


class _TrimmedContract(Contract):
    @field_validator("*", mode="after")
    @classmethod
    def trim_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("strings must contain non-whitespace characters")
        return value


class _TimestampContract(_TrimmedContract):
    @field_validator("*", mode="after")
    @classmethod
    def require_aware_timestamps(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value


class ResourceEvidenceInput(_TrimmedContract):
    kind: Literal["product", "feature", "certificate", "org_profile"]
    selection_id: UUID
    field_path: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def known_field_path(self):
        if self.field_path not in RESOURCE_FIELD_PATHS[self.kind]:
            raise ValueError(f"field_path is not available for {self.kind}")
        return self


class PageEvidenceInput(_TrimmedContract):
    kind: Literal["certificate_pdf_page"]
    evidence_source_id: UUID
    quote: str = Field(min_length=1, max_length=20000)


type EvidenceInput = Annotated[
    ResourceEvidenceInput | PageEvidenceInput, Field(discriminator="kind")
]


class CardContent(_TrimmedContract):
    response_kind: ResponseKind | None = None
    response_text: str | None = Field(default=None, min_length=1, max_length=20000)
    deviation: Deviation | None = None
    deviation_note: str | None = Field(default=None, min_length=1, max_length=10000)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def evidence_matches_response_kind(self):
        if self.evidence and self.response_kind != "evidence":
            raise ValueError("only an evidence response may link evidence")
        if self.response_kind == "commitment" and self.evidence:
            raise ValueError("commitment responses cannot link evidence")
        return self


class CardCreate(_TrimmedContract):
    extraction_job_id: UUID
    requirement_id: UUID
    content: CardContent


class CardUpdate(_TrimmedContract):
    expected_revision: int = Field(ge=1)
    content: CardContent


class CardAction(_TrimmedContract):
    expected_revision: int = Field(ge=1)
    action: Literal["submit", "withdraw", "confirm", "reject", "needs_material", "reopen"]
    reviewed_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    reviewed_warning_codes: list[str] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=10000)

    @field_validator("reviewed_warning_codes")
    @classmethod
    def nonblank_warning_codes(cls, value: list[str]):
        value = [code.strip() for code in value]
        if any(not code for code in value):
            raise ValueError("reviewed warning codes cannot be blank")
        return value

    @model_validator(mode="after")
    def action_fields_match(self):
        if len(set(self.reviewed_evidence_ids)) != len(self.reviewed_evidence_ids):
            raise ValueError("reviewed_evidence_ids cannot contain duplicates")
        if len(set(self.reviewed_warning_codes)) != len(self.reviewed_warning_codes):
            raise ValueError("reviewed_warning_codes cannot contain duplicates")
        if self.action != "confirm" and (self.reviewed_evidence_ids or self.reviewed_warning_codes):
            raise ValueError("only confirm accepts reviewed evidence or warnings")
        if self.action in {"withdraw", "reject", "needs_material", "reopen"} and not self.reason:
            raise ValueError(f"{self.action} requires a reason")
        if self.reviewed_warning_codes and not self.reason:
            raise ValueError("reviewed warnings require a reason")
        return self


class CardClassify(_TrimmedContract):
    expected_revision: int = Field(ge=1)
    review_domain: ReviewDomain
    reason: str = Field(min_length=1, max_length=10000)


class DispositionItem(_TrimmedContract):
    requirement_id: UUID
    expected_revision: int | None = Field(default=None, ge=1)
    disposition: Disposition
    reason: str = Field(min_length=1, max_length=10000)


class DispositionBatch(_TrimmedContract):
    extraction_job_id: UUID
    items: list[DispositionItem] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_requirements(self):
        ids = [item.requirement_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("a disposition batch cannot repeat a requirement")
        return self


class TaskRedactionSet(_TrimmedContract):
    expected_revision: int = Field(ge=1)
    model_redaction_enabled: bool


class TaskRedactionView(_TimestampContract):
    task_id: UUID
    revision: int = Field(ge=1)
    model_redaction_enabled: bool
    changed_by: UUID


class ModelEvidenceRef(_TrimmedContract):
    ref: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=20000)


class ModelCardProposal(_TrimmedContract):
    requirement_id: UUID
    response_kind: ResponseKind
    suggested_disposition: Disposition
    response_text: str = Field(min_length=1, max_length=20000)
    deviation: Deviation
    deviation_note: str = Field(min_length=1, max_length=10000)
    evidence: list[ModelEvidenceRef] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def evidence_matches_response_kind(self):
        if self.response_kind == "commitment" and self.evidence:
            raise ValueError("commitment proposals cannot cite evidence")
        return self


class CardGenerateRequest(_TrimmedContract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] | None = None
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    dry_run: bool = False

    @field_validator("requirement_ids")
    @classmethod
    def explicit_requirements_are_nonempty_and_unique(cls, value: list[UUID] | None):
        if value is not None and (not value or len(set(value)) != len(value)):
            raise ValueError("requirement_ids must be nonempty and unique when supplied")
        return value


class CardGeneratePreview(_TrimmedContract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    selected_requirements: list[UUID]
    skipped: dict[UUID, str]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    platform_model_id: str = Field(min_length=1)
    model_revision: int = Field(ge=1)
    reasoning: str | None = None
    model_redaction_enabled: bool
    input_refs: list[str]
    redacted_counts: dict[str, int]
    estimated_cost: Cost
    estimated_charge: Decimal | None = Field(default=None, ge=0)
    billing_currency: str = Field(min_length=3, max_length=3)
    cost_basis: Literal["known", "unknown"]
    estimated_duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def preview_collections_are_consistent(self):
        if len(set(self.selected_requirements)) != len(self.selected_requirements):
            raise ValueError("selected_requirements cannot contain duplicates")
        if set(self.selected_requirements) & set(self.skipped):
            raise ValueError("selected and skipped requirements must be disjoint")
        if any(count < 0 for count in self.redacted_counts.values()):
            raise ValueError("redacted counts cannot be negative")
        if self.cost_basis == "unknown" and self.estimated_charge is not None:
            raise ValueError("unknown cost cannot include an estimated charge")
        return self


class CardGenerateResult(_TrimmedContract):
    generation_job_id: UUID
    completion: Literal["complete", "partial"]
    created_revision_ids: list[UUID]
    skipped: dict[UUID, str]
    rejected_references: dict[UUID, list[str]]
    needs_material: list[UUID]
    usage_record_ids: list[UUID]
    charge: Decimal | None = Field(default=None, ge=0)
    billing_currency: str = Field(min_length=3, max_length=3)


class EvidenceView(_TimestampContract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    input: EvidenceInput
    selection_id: UUID
    resource_revision_id: UUID
    material_kind: Literal["declaration", "user_supplied_pdf_page"]
    quote_check: Literal["exact_field_match", "unreviewed_page", "human_page_review"]
    source_archive: EvidenceSourceArchive | None = None
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    active_selection: bool

    @model_validator(mode="after")
    def page_archive_matches_material(self):
        is_page = isinstance(self.input, PageEvidenceInput)
        if is_page != (self.material_kind == "user_supplied_pdf_page"):
            raise ValueError("evidence material_kind must match its input kind")
        if is_page != (self.source_archive is not None):
            raise ValueError("certificate page evidence requires its source archive")
        if is_page and self.quote_check == "exact_field_match":
            raise ValueError("certificate page evidence cannot use field quote checks")
        if not is_page and self.quote_check != "exact_field_match":
            raise ValueError("resource evidence requires an exact field quote check")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("evidence confirmation actor and time must be set together")
        return self


class CardView(_TimestampContract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    revision: int = Field(ge=1)
    revision_id: UUID
    state: CardState
    review_domain: ReviewDomain | None = None
    disposition: Disposition | None = None
    disposition_by: UUID | None = None
    disposition_at: datetime | None = None
    suggested_disposition: Disposition | None = None
    origin: Literal["human", "agent", "model"]
    actor_kind: Literal["session", "token", "agent", "worker"]
    model_job_id: UUID | None = None
    review_hint: Literal["needs_material"] | None = None
    source: Source
    content: CardContent
    evidence: list[EvidenceView]
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=10000)
    reviewed_warning_codes: list[str] = Field(default_factory=list, max_length=100)
    warning_codes: list[str]
    eligibility: Literal[
        "eligible",
        "comply_only",
        "unconfirmed",
        "unclassified",
        "stale_material",
        "invalid_citation",
    ]

    @model_validator(mode="after")
    def decision_metadata_is_consistent(self):
        if (self.disposition is None) != (self.disposition_by is None) or (
            self.disposition is None
        ) != (self.disposition_at is None):
            raise ValueError("disposition actor and time must accompany a disposition")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("card confirmation actor and time must be set together")
        if (self.state == "confirmed") != (self.confirmed_by is not None):
            raise ValueError("only the confirmed state may carry current confirmation metadata")
        if self.disposition == "comply_only" and self.state == "confirmed":
            raise ValueError("comply-only decisions do not confirm a response")
        if len(set(self.warning_codes)) != len(self.warning_codes):
            raise ValueError("warning_codes cannot contain duplicates")
        if len(set(self.reviewed_warning_codes)) != len(self.reviewed_warning_codes):
            raise ValueError("reviewed_warning_codes cannot contain duplicates")
        if any(not code.strip() for code in [*self.warning_codes, *self.reviewed_warning_codes]):
            raise ValueError("warning codes cannot contain blanks")
        if self.content.evidence != [item.input for item in self.evidence]:
            raise ValueError("card content evidence must match resolved evidence")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("evidence IDs cannot be repeated")
        if {item.id for item in self.evidence} != {
            item.id for item in self.evidence if item.card_id == self.id
        }:
            raise ValueError("all evidence must belong to the card")
        return self


class CardSlot(_TrimmedContract):
    requirement_id: UUID
    source: Source
    status: CardState | Literal["missing_card"]
    card: CardView | None = None

    @model_validator(mode="after")
    def status_matches_card(self):
        if (self.status != "missing_card") != (self.card is not None):
            raise ValueError("card slots must match status and card presence")
        if self.card is not None and self.status != self.card.state:
            raise ValueError("card slot status must match the current card state")
        return self


class DraftRequest(_TrimmedContract):
    extraction_job_id: UUID
    dry_run: bool = False
    retry: bool = False


class DraftPreview(_TrimmedContract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_requirements: int = Field(ge=0)
    comply_only_requirements: int = Field(ge=0)
    gap_requirements: int = Field(ge=0)
    table_rows: dict[TableKind, int]
    gap_reasons: dict[GapReason, int]
    negative_deviations: int = Field(ge=0)
    estimated_cost: Cost
    estimated_duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self):
        table_keys = {"substantive", "commercial", "technical"}
        if set(self.table_rows) != table_keys:
            raise ValueError("table_rows must contain all three table kinds")
        if any(value < 0 for value in (*self.table_rows.values(), *self.gap_reasons.values())):
            raise ValueError("draft preview counts cannot be negative")
        if sum(self.table_rows.values()) != self.response_requirements:
            raise ValueError("table row counts must equal response_requirements")
        if self.negative_deviations > self.response_requirements:
            raise ValueError("negative deviations cannot exceed response requirements")
        return self


class ResponseRow(_TrimmedContract):
    requirement_id: UUID
    category: Category
    starred: bool
    card_id: UUID
    card_revision_id: UUID
    table: TableKind
    tender_clause: Source
    location_label: str = Field(min_length=1)
    response_kind: ResponseKind
    response_text: str = Field(min_length=1, max_length=20000)
    deviation: Deviation
    deviation_note: str = Field(min_length=1, max_length=10000)
    evidence: list[EvidenceView] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def evidence_matches_response_kind(self):
        if self.response_kind == "evidence" and not self.evidence:
            raise ValueError("evidence response rows require evidence")
        if self.response_kind == "commitment" and self.evidence:
            raise ValueError("commitment response rows cannot contain evidence")
        if any(item.confirmed_by is None for item in self.evidence):
            raise ValueError("response row evidence must be human-confirmed")
        if any(item.card_id != self.card_id for item in self.evidence):
            raise ValueError("response row evidence must belong to its card")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("response row evidence IDs cannot be repeated")
        return self


class ComplyOnlyEntry(_TimestampContract):
    requirement_id: UUID
    card_id: UUID
    card_revision_id: UUID
    tender_clause: Source
    location_label: str = Field(min_length=1)
    disposition_by: UUID
    disposition_at: datetime


class DraftGap(_TrimmedContract):
    requirement_id: UUID
    card_id: UUID | None = None
    card_revision_id: UUID | None = None
    tender_clause: Source
    location_label: str = Field(min_length=1)
    reasons: list[GapReason] = Field(min_length=1)

    @model_validator(mode="after")
    def gap_card_reference_is_complete(self):
        if (self.card_id is None) != (self.card_revision_id is None):
            raise ValueError("gap card and revision IDs must be set together")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("gap reasons cannot contain duplicates")
        if "missing_card" in self.reasons and self.card_id is not None:
            raise ValueError("missing_card gaps cannot reference a card")
        return self


class DraftView(_TrimmedContract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    generation_job_id: UUID
    status: Literal["draft"] = "draft"
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: dict[TableKind, list[ResponseRow]]
    comply_only: list[ComplyOnlyEntry]
    gaps: list[DraftGap]
    invalidated_requirements: list[UUID]

    @model_validator(mode="after")
    def complete_partition(self):
        table_keys = {"substantive", "commercial", "technical"}
        if set(self.tables) != table_keys:
            raise ValueError("tables must contain all three table kinds")
        row_ids: list[UUID] = []
        for table, rows in self.tables.items():
            if any(row.table != table for row in rows):
                raise ValueError("response row table must match its containing table")
            row_ids.extend(row.requirement_id for row in rows)
        comply_ids = [entry.requirement_id for entry in self.comply_only]
        gap_ids = [gap.requirement_id for gap in self.gaps]
        all_ids = [*row_ids, *comply_ids, *gap_ids]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("rows, comply-only entries and gaps must be disjoint")
        if (self.completion == "partial") != bool(self.gaps):
            raise ValueError("draft completion is partial exactly when gaps exist")
        if (self.validity == "stale") != bool(self.invalidated_requirements):
            raise ValueError("draft validity is stale exactly when requirements are invalidated")
        if len(set(self.invalidated_requirements)) != len(self.invalidated_requirements):
            raise ValueError("invalidated_requirements cannot contain duplicates")
        if not set(self.invalidated_requirements).issubset(set(all_ids)):
            raise ValueError("invalidated requirements must belong to the draft")
        return self


class DraftSummary(_TimestampContract):
    id: UUID
    task_id: UUID
    extraction_job_id: UUID
    generation_job_id: UUID
    status: Literal["draft"] = "draft"
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    invalidated_requirements: list[UUID]
    summary: dict[Literal["rows", "comply_only", "gaps", "negative_deviations"], int]

    @model_validator(mode="after")
    def summary_is_complete(self):
        if set(self.summary) != {"rows", "comply_only", "gaps", "negative_deviations"}:
            raise ValueError("draft summary must contain all count fields")
        if any(value < 0 for value in self.summary.values()):
            raise ValueError("draft summary counts cannot be negative")
        if (self.completion == "partial") != bool(self.summary["gaps"]):
            raise ValueError("draft completion is partial exactly when its summary has gaps")
        if (self.validity == "stale") != bool(self.invalidated_requirements):
            raise ValueError("draft validity is stale exactly when requirements are invalidated")
        if len(set(self.invalidated_requirements)) != len(self.invalidated_requirements):
            raise ValueError("invalidated_requirements cannot contain duplicates")
        return self
