"""The nine broker commands' public Result output contracts.

Business payloads reuse their owning domain contracts. Job status has a common
record for the unrestricted CLI and a separate, closed broker profile for the
four job kinds which the agent is authorized to inspect.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, TypeAdapter, create_model

from app.schemas.agent_contracts import AgentJobResult
from app.schemas.agent_provenance import AgentProvenance
from app.schemas.contracts import Contract, Cost, ExtractedRequirement, Result
from app.schemas.preflight_contracts import PreflightData
from app.schemas.requirement_confirmation import ReviewState
from app.schemas.response_card_contracts import (
    CardGeneratePreview,
    CardGenerateResult,
    CardSlot,
    CardView,
    DraftPreview,
    DraftView,
)
from app.schemas.source_contracts import SourceMatch, SourceReadData, SourceSearchData

JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
EmptyItems = Annotated[list[JsonValue], Field(max_length=0)]


class EmptyData(Contract):
    pass


class CommandError(Contract):
    code: str
    message: str
    exit_code: Literal[2, 3, 4, 5]


class CommandFailure(Contract):
    error: CommandError
    # CLI wait timeouts can identify the accepted job without changing Result.
    job_id: UUID | None = None


class RequirementReviewBinding(Contract):
    policy_version: Literal["requirement-review-v1"]
    revision: int = Field(ge=0)
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ReviewState


class RequirementListItem(ExtractedRequirement):
    id: UUID
    job_id: UUID
    reasoning: str | None
    review_requirement_id: UUID
    requirement_review: RequirementReviewBinding


class ExtractionScope(Contract):
    task_id: UUID
    extraction_job_id: UUID


class CardHistory(Contract):
    card: CardView
    history: list[CardView]


class JobAccepted(Contract):
    job_id: UUID
    generation_job_id: UUID
    status: JobState
    cached: bool


class CardGeneratePreflight(CardGeneratePreview):
    preflight: PreflightData


class DraftPreflight(DraftPreview):
    preflight: PreflightData


class PendingJobResult(Contract):
    """Public worker state before publishing a domain result, including failures."""

    cost: Cost | None = None
    rejected: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GeneratedCards(CardGenerateResult):
    warnings: list[str]
    exit_code: Literal[0, 5]
    cost: Cost


class AssembledDraft(Contract):
    draft_id: UUID
    completion: Literal["complete", "partial"]
    warnings: list[str]
    cost: Cost
    exit_code: Literal[0, 5]
    agent_provenance: AgentProvenance | None


class GapFill(Contract):
    segments: int = Field(ge=0)
    calls: int = Field(ge=0)
    added: int = Field(ge=0)
    remaining: int = Field(ge=0)


class ExtractedJob(Contract):
    created: int = Field(ge=0)
    starred: int = Field(ge=0)
    rejected: list[dict[str, str]]
    model: str
    reasoning: str | None
    warnings: list[str]
    gap_fill: GapFill
    cost: Cost


class ManualExtraction(Contract):
    origin: Literal["manual"]
    created: int = Field(ge=0)
    rejected: list[dict[str, str]]


class JobRecord(Contract):
    id: UUID
    kind: str
    status: JobState
    error: CommandError | None
    attempts: int = Field(ge=0)
    reasoning: str | None
    agent_provenance: AgentProvenance | None = None


class PublicJobStatus(JobRecord):
    result: dict[str, JsonValue] = Field(
        description="Kind-specific public result; broker kinds are defined by agent_result."
    )


class ExtractionJobStatus(JobRecord):
    kind: Literal["extract"]
    result: ExtractedJob | ManualExtraction | PendingJobResult


class CardGenerationJobStatus(JobRecord):
    kind: Literal["card_generate"]
    result: GeneratedCards | PendingJobResult


class DraftJobStatus(JobRecord):
    kind: Literal["draft"]
    result: AssembledDraft | PendingJobResult


class ControllerJobStatus(JobRecord):
    kind: Literal["agent"]
    result: AgentJobResult | PendingJobResult


BrokerJobStatus = Annotated[
    ExtractionJobStatus | CardGenerationJobStatus | DraftJobStatus | ControllerJobStatus,
    Field(discriminator="kind"),
]


class CardGenerateWait(GeneratedCards, JobAccepted):
    """CLI --wait retains the receipt and merges the published domain result."""


class DraftWait(AssembledDraft, JobAccepted):
    pass


class FailedWait(PendingJobResult, JobAccepted):
    status: Literal["failed", "cancelled"]
    error: CommandError | None = None


def result_adapter(command: str, data_type, item_type) -> TypeAdapter:
    # Reuse Result's accounting and seven-key envelope, replacing only the
    # command-specific data/items slots. Existing runtime Results remain intact.
    return TypeAdapter(
        create_model(
            "".join(word.title() for word in command.split()) + "Result",
            __base__=Result,
            command=(Literal[command], ...),
            data=(data_type | CommandFailure, ...),
            items=(item_type, ...),
        )
    )


DATA_TYPES = {
    "req list": EmptyData | CommandFailure,
    "card list": ExtractionScope | CommandFailure,
    "card show": CardView | CardHistory | CommandFailure,
    "card generate": (
        CardGeneratePreflight | JobAccepted | CardGenerateWait | FailedWait | CommandFailure
    ),
    "draft": (DraftPreflight | JobAccepted | DraftWait | FailedWait | DraftView | CommandFailure),
    "draft show": DraftView | CommandFailure,
    "job status": PublicJobStatus | CommandFailure,
    "source search": SourceSearchData | CommandFailure,
    "source read": SourceReadData | CommandFailure,
}
ITEM_TYPES = {
    name: (
        list[RequirementListItem]
        if name == "req list"
        else list[CardSlot]
        if name == "card list"
        else list[dict[str, JsonValue]]
        if name == "job status"
        else list[SourceMatch]
        if name == "source search"
        else EmptyItems
    )
    for name in DATA_TYPES
}
TOOL_DATA = {name: TypeAdapter(data_type) for name, data_type in DATA_TYPES.items()}
TOOL_ITEMS = {name: TypeAdapter(item_type) for name, item_type in ITEM_TYPES.items()}
TOOL_RESULTS = {
    name: result_adapter(name, data_type, ITEM_TYPES[name])
    for name, data_type in DATA_TYPES.items()
}
AGENT_JOB_RESULT = result_adapter("job status", BrokerJobStatus, EmptyItems)
