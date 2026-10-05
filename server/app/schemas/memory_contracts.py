"""Memory HTTP, CLI, retrieval and provider contracts."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.providers.base import LLMProvider
from app.schemas.contracts import Contract, Cost, JobAction, ProviderUsage, Result
from app.schemas.response_card_contracts import ReviewDomain

if TYPE_CHECKING:
    from app.providers.drafting import DraftingOutput

type MemoryScope = Literal["global", "org", "user", "project"]
type TenantMemoryScope = Literal["org", "user", "project"]
type MemoryKind = Literal["rule", "preference"]
type MemoryStatus = Literal["candidate", "active", "disabled"]
type MemoryOrigin = Literal["human", "system", "platform_release"]
type RetrievalMode = Literal["keyword", "vector", "hybrid"]
type FeedbackKind = Literal["card_rejected", "card_edited", "card_confirmed"]
type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type ShortText = Annotated[str, Field(min_length=1, max_length=200)]
type Tag = Annotated[str, Field(min_length=1, max_length=40)]
type Revision = Annotated[int, Field(strict=True, ge=1)]


class MemoryTarget(Contract):
    scope: MemoryScope
    user_id: UUID | None = None
    task_id: UUID | None = None

    @model_validator(mode="after")
    def scope_owner(self):
        if (self.scope == "user") != (self.user_id is not None):
            raise ValueError("user scope requires exactly one user owner")
        if (self.scope == "project") != (self.task_id is not None):
            raise ValueError("project scope requires exactly one task owner")
        return self


class MemoryContent(Contract):
    kind: MemoryKind
    conflict_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    text: str = Field(min_length=1, max_length=2000)
    tags: list[Tag] = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory text cannot be blank")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        tags = [tag.strip().casefold() for tag in value]
        if any(not tag for tag in tags) or len(tags) != len(set(tags)):
            raise ValueError("tags must be nonblank and unique")
        return sorted(tags)


class MemorySourceInput(Contract):
    task_id: UUID
    card_id: UUID | None = None
    card_revision_id: UUID | None = None

    @model_validator(mode="after")
    def card_binding(self):
        if (self.card_id is None) != (self.card_revision_id is None):
            raise ValueError("card and revision must be supplied together")
        return self


class MemoryCreate(Contract):
    target: MemoryTarget
    content: MemoryContent
    source: MemorySourceInput | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def kind_and_scope(self):
        if self.target.scope == "user" and self.content.kind != "preference":
            raise ValueError("user memory contains preferences only")
        if self.target.scope == "project" and self.content.kind != "rule":
            raise ValueError("project memory contains facts and rules only")
        if self.target.scope == "project" and self.source is not None:
            if self.source.task_id != self.target.task_id:
                raise ValueError("project source must belong to the target task")
        return self


class MemoryUpdate(Contract):
    expected_revision: Revision
    content: MemoryContent
    expires_at: AwareDatetime | None = None


class MemoryDecision(Contract):
    expected_revision: Revision
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a decision reason cannot be blank")
        return value.strip()


class MemoryDisable(Contract):
    expected_revision: Revision
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a management reason cannot be blank")
        return value.strip()


class MemoryDelete(MemoryDisable):
    """Logical deletion preserves revisions and every historical model input."""


class MemorySourceView(Contract):
    origin: MemoryOrigin
    task_id: UUID | None = None
    card_id: UUID | None = None
    card_revision_id: UUID | None = None
    feedback_event_id: UUID | None = None
    proposal_job_id: UUID | None = None
    proposal_run_id: UUID | None = None
    platform_release_id: UUID | None = None

    @model_validator(mode="after")
    def provenance(self):
        if (self.card_id is None) != (self.card_revision_id is None):
            raise ValueError("card provenance requires a fixed revision")
        if self.card_id is not None and self.task_id is None:
            raise ValueError("card provenance requires a task")
        if (self.proposal_job_id is None) != (self.proposal_run_id is None):
            raise ValueError("proposal job and run must be bound together")
        if self.origin == "platform_release":
            if self.platform_release_id is None or any(
                value is not None
                for value in (
                    self.task_id,
                    self.card_id,
                    self.feedback_event_id,
                    self.proposal_job_id,
                )
            ):
                raise ValueError("global provenance cannot originate from tenant data")
        elif self.platform_release_id is not None:
            raise ValueError("tenant provenance cannot reference a platform release")
        if self.origin == "system" and (
            self.feedback_event_id is None or self.proposal_job_id is None or self.card_id is None
        ):
            raise ValueError("system candidates require the feedback event and producing job")
        return self


class MemoryRevisionView(Contract):
    id: UUID
    org_id: UUID
    memory_id: UUID
    revision: Revision
    target: MemoryTarget
    content: MemoryContent
    status: MemoryStatus
    source: MemorySourceView
    content_sha256: Digest
    created_at: AwareDatetime
    created_by: UUID
    actor_kind: Literal["session", "token", "agent", "worker"]
    confirmed_by: UUID | None = None
    confirmed_at: AwareDatetime | None = None
    decision: Literal["approve", "reject", "disable", "delete"] | None = None
    decision_reason_sha256: Digest | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def confirmation_gate(self):
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("confirmation identity and time must be paired")
        if self.status == "active" and (
            self.confirmed_by is None or self.actor_kind != "session" or self.decision != "approve"
        ):
            raise ValueError("active memory requires a human approval revision")
        if self.status == "candidate" and self.confirmed_by is not None:
            raise ValueError("candidate memory cannot carry confirmation")
        if self.source.origin == "platform_release":
            raise ValueError("global storage requires a separately approved contract")
        if self.target.scope == "user" and self.content.kind != "preference":
            raise ValueError("user memory contains preferences only")
        if self.target.scope == "project" and self.content.kind != "rule":
            raise ValueError("project memory contains facts and rules only")
        return self


class MemoryView(Contract):
    id: UUID
    org_id: UUID
    current: MemoryRevisionView
    effective_status: Literal["candidate", "active", "disabled", "expired", "deleted"]
    deleted_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def current_binding(self):
        if self.id != self.current.memory_id or self.org_id != self.current.org_id:
            raise ValueError("current revision must belong to this memory and org")
        if self.effective_status == "active" and self.current.status != "active":
            raise ValueError("only approved memory can be effective")
        if (self.effective_status == "deleted") != (self.deleted_at is not None):
            raise ValueError("deleted state requires its tombstone")
        if self.effective_status in {"candidate", "disabled"}:
            if self.effective_status != self.current.status:
                raise ValueError("effective state must match the current revision")
        if self.effective_status == "expired" and (
            self.current.status != "active" or self.current.expires_at is None
        ):
            raise ValueError("only an active revision with an expiry can become expired")
        return self


class MemoryData(Contract):
    memory: MemoryView


class MemoryListRequest(Contract):
    target: MemoryTarget
    status: MemoryStatus | None = None
    include_deleted: bool = False
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class MemoryPageData(Contract):
    next_cursor: str | None = None
    returned: int = Field(ge=0)


class MemoryRetrievalRequest(Contract):
    org_id: UUID
    scopes: list[MemoryScope] = Field(min_length=1, max_length=4)
    user_id: UUID | None = None
    task_id: UUID | None = None
    query: str = Field(min_length=1, max_length=2000)
    keywords: list[Tag] = Field(default_factory=list, max_length=20)
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    mode: RetrievalMode = "keyword"
    top_k: int = Field(default=12, strict=True, ge=1, le=50)
    max_context_chars: int = Field(default=8000, strict=True, ge=2000, le=20000)

    @model_validator(mode="after")
    def explicit_boundaries(self):
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("scopes cannot contain duplicates")
        if "user" in self.scopes and self.user_id is None:
            raise ValueError("user retrieval requires its authenticated owner")
        if "project" in self.scopes and self.task_id is None:
            raise ValueError("project retrieval requires an authorized task")
        if not self.query.strip():
            raise ValueError("retrieval query cannot be blank")
        if any(not item.strip() for item in (*self.tags, *self.keywords)):
            raise ValueError("keywords and tags cannot be blank")
        return self


class MemoryEpochView(Contract):
    org_id: UUID
    scope: MemoryScope
    owner_id: UUID
    epoch: int = Field(strict=True, ge=0)


class MemoryRef(Contract):
    memory_id: UUID
    revision_id: UUID
    revision: Revision
    scope: MemoryScope
    content_sha256: Digest
    sent_sha256: Digest
    expires_at: AwareDatetime | None = None


class MemoryHit(Contract):
    memory: MemoryRef
    kind: MemoryKind
    conflict_key: str
    text: str = Field(min_length=1, max_length=2000)
    rank: int = Field(ge=1)
    priority: int = Field(ge=0, le=3)
    relevance: float = Field(ge=0, allow_inf_nan=False)
    matched_by: list[Literal["exact", "keyword", "tag", "vector"]] = Field(min_length=1)


class MemoryOmission(Contract):
    memory_id: UUID
    revision_id: UUID
    reason: Literal["shadowed", "same_priority_conflict", "context_limit", "top_k_limit"]


class MemoryRetrievalData(Contract):
    retrieval_id: UUID | None
    org_id: UUID
    mode: RetrievalMode
    retrieval_version: str
    priority_version: str
    query_sha256: Digest
    manifest_sha256: Digest
    scopes: list[MemoryScope] = Field(min_length=1, max_length=4)
    epochs: list[MemoryEpochView]
    valid_until: AwareDatetime | None
    omitted: list[MemoryOmission] = Field(default_factory=list)
    context_chars: int = Field(ge=0, le=20000)
    preview: bool
    currently_valid: bool
    stale_reasons: list[Literal["epoch_changed", "expired", "policy_changed", "memory_changed"]] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def preview_has_no_row(self):
        if self.preview != (self.retrieval_id is None):
            raise ValueError("only previews omit the persisted retrieval ID")
        if any(epoch.org_id != self.org_id for epoch in self.epochs):
            raise ValueError("retrieval epochs cannot cross orgs")
        if self.currently_valid == bool(self.stale_reasons):
            raise ValueError("stale retrievals require explicit reasons")
        return self


class MemoryRetrievalOutput(Contract):
    data: MemoryRetrievalData
    items: list[MemoryHit] = Field(max_length=50)
    warnings: list[str] = Field(default_factory=list)


class MemoryRetrievalView(Contract):
    id: UUID
    org_id: UUID
    user_id: UUID
    task_id: UUID | None
    actor_token_id: UUID | None
    created_at: AwareDatetime
    data: MemoryRetrievalData


class MemoryRetrievalItemView(Contract):
    id: UUID
    org_id: UUID
    retrieval_id: UUID
    memory: MemoryRef
    rank: int = Field(ge=1)
    selected: bool
    omission_reason: (
        Literal["shadowed", "same_priority_conflict", "context_limit", "top_k_limit"] | None
    )

    @model_validator(mode="after")
    def selection_reason(self):
        if self.selected != (self.omission_reason is None):
            raise ValueError("only excluded items have an omission reason")
        return self


class MemoryCallInputView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    run_id: UUID
    call_id: UUID
    retrieval_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1)
    memories: list[MemoryRef] = Field(max_length=50)
    manifest_sha256: Digest
    prompt_sha256: Digest
    prompt_version: str
    state: Literal["admitted", "completed", "unknown"]
    usage_record_id: UUID | None = None


class MemoryCallData(Contract):
    job_id: UUID
    calls: list[MemoryCallInputView]


class MemoryPromptContext(Contract):
    org_id: UUID
    retrieval_id: UUID
    manifest_sha256: Digest
    rules: list[MemoryHit] = Field(default_factory=list, max_length=50)
    preferences: list[MemoryHit] = Field(default_factory=list, max_length=50)
    usable_as_evidence: Literal[False] = False

    @model_validator(mode="after")
    def separate_rules_and_preferences(self):
        if any(item.kind != "rule" for item in self.rules):
            raise ValueError("rules must contain rule memories")
        if any(item.kind != "preference" for item in self.preferences):
            raise ValueError("preferences must contain preference memories")
        if len(self.rules) + len(self.preferences) > 50:
            raise ValueError("memory context exceeds the retrieval limit")
        return self


class MemoryFeedbackView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    before_revision_id: UUID
    after_revision_id: UUID
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    kind: FeedbackKind
    review_domain: ReviewDomain | None
    created_at: AwareDatetime
    sanitized_sha256: Digest
    sanitizer_version: str


class MemoryCandidateInput(Contract):
    org_id: UUID
    event: MemoryFeedbackView
    target: MemoryTarget
    sanitized_summary: str = Field(min_length=1, max_length=2000)
    generator_version: str

    @model_validator(mode="after")
    def event_boundary(self):
        if self.event.org_id != self.org_id:
            raise ValueError("feedback cannot cross orgs")
        if self.event.kind == "card_confirmed":
            raise ValueError("confirmation creates an evaluation sample, not a candidate")
        if self.target.scope == "project" and self.target.task_id != self.event.task_id:
            raise ValueError("project candidates stay in the source task")
        if self.target.scope == "user" and self.target.user_id != self.event.actor_user_id:
            raise ValueError("personal candidates belong to the editing human")
        return self


class MemoryCandidateProposal(Contract):
    content: MemoryContent
    status: Literal["candidate"] = "candidate"
    confirmed_by: Literal[None] = None
    origin: Literal["system"] = "system"


class MemoryCandidateOutput(Contract):
    proposal: MemoryCandidateProposal | None
    skip_reason: Literal["no_reusable_feedback", "sensitive_only", "duplicate"] | None = None
    usages: list[ProviderUsage] = Field(default_factory=list)

    @model_validator(mode="after")
    def proposal_or_skip(self):
        if (self.proposal is None) == (self.skip_reason is None):
            raise ValueError("supply a proposal or an explicit skip reason")
        return self


class MemoryCandidateJobRequest(Contract):
    event_ids: list[UUID] = Field(min_length=1, max_length=100)
    action: JobAction = Field(default_factory=JobAction)

    @model_validator(mode="after")
    def deterministic_job(self):
        if self.action.reasoning is not None:
            raise ValueError("deterministic candidate jobs have no reasoning option")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event IDs cannot contain duplicates")
        return self


class MemoryJobSubmissionData(Contract):
    job_id: UUID | None
    task_id: UUID
    reused: bool
    dry_run: bool
    event_count: int = Field(ge=0)

    @model_validator(mode="after")
    def dry_run_has_no_job(self):
        if self.dry_run != (self.job_id is None):
            raise ValueError("only dry runs omit the durable job ID")
        return self


class MemoryCandidateItemResult(Contract):
    event_id: UUID
    outcome: Literal["created", "duplicate", "skipped", "failed"]
    memory_id: UUID | None = None
    sample_id: UUID | None = None
    error_code: str | None = None


class MemoryCandidateJobResult(Contract):
    job_id: UUID
    run_id: UUID
    completion: Literal["complete", "partial"]
    items: list[MemoryCandidateItemResult]
    usage_record_ids: list[UUID] = Field(default_factory=list)
    cost: Cost = Field(default_factory=Cost)
    generator_version: str
    stop_reason: str | None = None


class MemoryEvalSampleView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    feedback_event_id: UUID
    card_id: UUID
    before_revision_id: UUID
    after_revision_id: UUID
    label: FeedbackKind
    actor_user_id: UUID
    generator_version: str
    sanitized_sha256: Digest
    created_at: AwareDatetime
    review_state: Literal["unreviewed", "accepted", "excluded"]
    review_revision: Revision
    reviewed_by: UUID | None = None
    reviewed_at: AwareDatetime | None = None


class MemoryEvalReview(Contract):
    expected_revision: Revision
    action: Literal["accept", "exclude"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a sample review reason cannot be blank")
        return value.strip()


class MemoryEvalData(Contract):
    sample: MemoryEvalSampleView


class MemoryEvalDetailData(MemoryEvalData):
    sanitized_summary: str = Field(min_length=1, max_length=2000)


class MemoryEvalCase(Contract):
    case_id: ShortText
    request: MemoryRetrievalRequest
    expected_memory_ids: list[UUID]
    forbidden_memory_ids: list[UUID]
    expected_order: list[UUID]
    expected_error: str | None = None


class EmbeddingIdentity(Contract):
    provider: ShortText
    model: ShortText
    version: ShortText
    model_revision: ShortText
    price_revision: ShortText
    dimensions: int = Field(strict=True, ge=1, le=16000)
    metric: Literal["cosine"] = "cosine"
    provider_config_id: UUID
    platform_model_id: str | None = None


class EmbeddingRequest(Contract):
    org_id: UUID
    scope: MemoryScope
    user_id: UUID | None = None
    task_id: UUID | None = None
    job_id: UUID
    run_id: UUID
    purpose: Literal["memory_index", "memory_query"]
    identity: EmbeddingIdentity
    texts: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def owner_boundaries(self):
        if self.scope == "user" and self.user_id is None:
            raise ValueError("user embedding requires its owner")
        if self.scope == "project" and self.task_id is None:
            raise ValueError("project embedding requires its task")
        return self


class EmbeddingOutput(Contract):
    identity: EmbeddingIdentity
    vectors: list[list[Annotated[float, Field(allow_inf_nan=False)]]] = Field(
        min_length=1, max_length=100
    )
    usage: ProviderUsage

    @model_validator(mode="after")
    def vector_shape(self):
        if any(len(vector) != self.identity.dimensions for vector in self.vectors):
            raise ValueError("embedding dimension differs from its model identity")
        if any(not any(component != 0 for component in vector) for vector in self.vectors):
            raise ValueError("cosine retrieval requires nonzero vectors")
        if (
            self.usage.provider != self.identity.provider
            or self.usage.model != self.identity.model
            or self.usage.version != self.identity.version
            or self.usage.provider_config_id != self.identity.provider_config_id
            or self.usage.platform_model_id != self.identity.platform_model_id
        ):
            raise ValueError("usage must describe the invoked embedding model")
        return self


class MemoryEmbeddingView(Contract):
    id: UUID
    org_id: UUID
    memory_revision_id: UUID
    scope: MemoryScope
    user_id: UUID | None = None
    task_id: UUID | None = None
    identity: EmbeddingIdentity
    text_sha256: Digest
    job_id: UUID
    run_id: UUID
    usage_record_id: UUID
    created_at: AwareDatetime


class MemoryIndexRequest(Contract):
    org_id: UUID
    target: MemoryTarget
    revision_ids: list[UUID] = Field(min_length=1, max_length=100)
    action: JobAction = Field(default_factory=JobAction)


class MemoryIndexItemResult(Contract):
    revision_id: UUID
    outcome: Literal["indexed", "unchanged", "stale", "failed"]
    error_code: str | None = None


class MemoryIndexJobResult(Contract):
    job_id: UUID
    run_id: UUID
    identity: EmbeddingIdentity
    completion: Literal["complete", "partial"]
    items: list[MemoryIndexItemResult]
    usage_record_ids: list[UUID]
    cost: Cost


class MemoryError(Contract):
    code: str
    message: str
    exit_code: Literal[2, 3, 4, 5]


class MemoryErrorData(Contract):
    error: MemoryError


type MemoryDataPayload = (
    MemoryData
    | MemoryPageData
    | MemoryRetrievalData
    | MemoryCallData
    | MemoryJobSubmissionData
    | MemoryCandidateJobResult
    | MemoryEvalData
    | MemoryEvalDetailData
    | MemoryIndexJobResult
    | MemoryErrorData
)

# The envelope is the existing seven-key contract; payloads above go into data/items.
MemoryResult = Result


class MemoryRetrievalProvider(Protocol):
    async def retrieve(
        self, request: MemoryRetrievalRequest, *, preview: bool = False
    ) -> MemoryRetrievalOutput: ...


class MemoryCandidateProvider(Protocol):
    name: str
    version: str

    async def propose(self, request: MemoryCandidateInput) -> MemoryCandidateOutput: ...


class MemoryAwareLLMProvider(LLMProvider, Protocol):
    async def draft(
        self,
        requirements: list[dict],
        materials: list[dict],
        fields: Sequence[dict] = (),
        *,
        memory: MemoryPromptContext | None = None,
    ) -> "DraftingOutput": ...


class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity

    def reservation(self, request: EmbeddingRequest) -> Decimal: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingOutput: ...


class MemoryClock(Protocol):
    """Expiry uses server time; a caller cannot choose a past retrieval instant."""

    def now(self) -> datetime: ...
