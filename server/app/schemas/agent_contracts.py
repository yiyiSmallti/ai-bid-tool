"""Public A01 agent management, orchestration and provider contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from app.providers.base import LLMProvider
from app.schemas.agent_provenance import AgentProvenance as AgentProvenance
from app.schemas.contracts import Contract, Cost, ProviderUsage, Result
from app.schemas.response_card_contracts import CardGenerateRequest, DraftRequest

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8)]
AgentScope = Literal[
    "task:read",
    "job:read",
    "card:read",
    "card:generate",
    "draft:read",
    "draft:run",
    "resource:read",
    "certificate:read",
    "certificate:file:read",
    "profile:read",
    "evidence:source:read",
]
ToolCommand = Literal[
    "req list", "card list", "card show", "card generate", "draft", "draft show", "job status"
]
SessionState = Literal[
    "queued", "running", "waiting_job", "paused", "completed", "partial", "failed", "cancelled"
]
StepState = Literal["planned", "submitted", "waiting_job", "completed", "failed", "uncertain"]
ExitCode = Literal[0, 2, 3, 4, 5]


class AgentLimits(Contract):
    max_steps: int = Field(default=24, strict=True, ge=1, le=64)
    max_active_seconds: int = Field(default=900, strict=True, ge=30, le=3600)
    max_lifetime_seconds: int = Field(default=86400, strict=True, ge=60, le=86400)
    max_vendor_calls: int = Field(default=32, strict=True, ge=1, le=64)
    max_vendor_usd: Money
    max_platform_charge: Money
    billing_currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def ordered_deadlines(self):
        if self.max_active_seconds > self.max_lifetime_seconds:
            raise ValueError("active duration exceeds session lifetime")
        if self.max_vendor_usd == 0:
            raise ValueError("a positive vendor cost bound is required")
        return self


class AgentStartRequest(Contract):
    extraction_job_id: UUID
    message: str = Field(min_length=1, max_length=8000)
    requested_scopes: list[AgentScope] = Field(min_length=1, max_length=12)
    limits: AgentLimits
    idempotency_key: UUID
    dry_run: bool = False


class AgentMessageRequest(Contract):
    message: str = Field(min_length=1, max_length=8000)
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID


class BudgetDependencyRef(Contract):
    """Opaque references owned by contract-budget; they never authorize expenditure."""

    contract: Literal["docs/plan/budget.md"] = "docs/plan/budget.md"
    question_ref: str = Field(min_length=1, max_length=200)
    revision_ref: str = Field(min_length=1, max_length=200)


class AgentResumeRequest(Contract):
    expected_revision: int = Field(strict=True, ge=1)
    pause_id: UUID
    idempotency_key: UUID
    budget_ref: BudgetDependencyRef | None = None


class AgentCancelRequest(Contract):
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID
    reason: Literal["user_request", "wrong_input", "no_longer_needed"] = "user_request"


class AgentListRequest(Contract):
    cursor: UUID | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class ExtractionArguments(Contract):
    task: UUID
    job: UUID


class CardShowArguments(Contract):
    id: UUID
    history: bool = False


class CardGenerateArguments(Contract):
    task: UUID
    input: CardGenerateRequest


class DraftArguments(Contract):
    task: UUID
    input: DraftRequest


class DraftShowArguments(Contract):
    id: UUID


class JobStatusArguments(Contract):
    job_id: UUID


class ReqListCall(Contract):
    command: Literal["req list"]
    arguments: ExtractionArguments


class CardListCall(Contract):
    command: Literal["card list"]
    arguments: ExtractionArguments


class CardShowCall(Contract):
    command: Literal["card show"]
    arguments: CardShowArguments


class CardGenerateCall(Contract):
    command: Literal["card generate"]
    arguments: CardGenerateArguments


class DraftCall(Contract):
    command: Literal["draft"]
    arguments: DraftArguments


class DraftShowCall(Contract):
    command: Literal["draft show"]
    arguments: DraftShowArguments


class JobStatusCall(Contract):
    command: Literal["job status"]
    arguments: JobStatusArguments


ToolCall = Annotated[
    ReqListCall
    | CardListCall
    | CardShowCall
    | CardGenerateCall
    | DraftCall
    | DraftShowCall
    | JobStatusCall,
    Field(discriminator="command"),
]


class AgentInputRef(Contract):
    kind: Literal["requirement", "card", "draft", "job", "message"]
    id: UUID
    revision_ref: str = Field(min_length=1, max_length=100)
    sha256: Sha256


class AgentToolDefinition(Contract):
    command: ToolCommand
    contract_version: str = Field(min_length=1, max_length=30)
    schema_sha256: Sha256
    invocation_input: dict[str, JsonValue]
    result_schema: dict[str, JsonValue]
    required_scopes: list[AgentScope]
    effect: Literal["read", "job"]
    supports_dry_run: bool


class ToolDecision(Contract):
    kind: Literal["tool"]
    call: ToolCall
    input_refs: list[AgentInputRef] = Field(max_length=100)


class HumanActionNeeded(Contract):
    kind: Literal["human_action"]
    action: Literal["review_cards", "export", "confidential_reveal", "clarify"]
    resource_ids: list[UUID] = Field(max_length=100)
    question: str = Field(min_length=1, max_length=2000)


class CompletionDecision(Contract):
    kind: Literal["complete"]
    summary: str = Field(min_length=1, max_length=4000)
    output_refs: list[AgentInputRef] = Field(max_length=100)


class AgentDecision(Contract):
    action: Annotated[
        ToolDecision | HumanActionNeeded | CompletionDecision, Field(discriminator="kind")
    ]


class AgentPrincipalView(Contract):
    id: UUID
    org_id: UUID
    user_id: UUID
    membership_id: UUID
    actor_kind: Literal["agent"] = "agent"
    scopes: list[AgentScope]
    authority_expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    created_at: AwareDatetime


class AgentCostView(Contract):
    cost: Cost
    platform_charge: Money
    reserved_platform_charge: Money
    reserved_vendor_usd: Money | None
    billing_currency: str = Field(pattern=r"^[A-Z]{3}$")
    unpriced_calls: int = Field(strict=True, ge=0)
    unresolved_calls: int = Field(strict=True, ge=0)


class AgentSessionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    document_id: UUID
    extraction_job_id: UUID
    principal_id: UUID
    owner_user_id: UUID
    state: SessionState
    revision: int = Field(strict=True, ge=1)
    limits: AgentLimits
    steps_used: int = Field(strict=True, ge=0)
    active_seconds_used: int = Field(strict=True, ge=0)
    vendor_calls_used: int = Field(strict=True, ge=0)
    current_job_id: UUID | None = None
    current_run_id: UUID | None = None
    pause_id: UUID | None = None
    cost: AgentCostView
    tool_schema_sha256: Sha256
    created_at: AwareDatetime
    expires_at: AwareDatetime
    updated_at: AwareDatetime


class AgentMessageView(Contract):
    id: UUID
    org_id: UUID
    session_id: UUID
    ordinal: int = Field(strict=True, ge=1)
    role: Literal["human", "assistant", "tool", "system"]
    author_user_id: UUID | None = None
    step_id: UUID | None = None
    content: str = Field(max_length=8000)
    content_sha256: Sha256
    created_at: AwareDatetime


class AgentStepView(Contract):
    id: UUID
    org_id: UUID
    session_id: UUID
    ordinal: int = Field(strict=True, ge=1)
    kind: Literal["decision", "tool"]
    state: StepState
    command: ToolCommand | None = None
    created_by_job_id: UUID
    created_by_run_id: UUID
    revision: int = Field(strict=True, ge=1)
    last_transition_job_id: UUID
    last_transition_run_id: UUID
    invocation_id: UUID | None = None
    arguments_sha256: Sha256 | None = None
    input_refs: list[AgentInputRef] = Field(default_factory=list, max_length=100)
    child_job_id: UUID | None = None
    result_sha256: Sha256 | None = None
    exit_code: ExitCode | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    usage_ids: list[UUID] = Field(default_factory=list, max_length=64)
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class AgentPauseView(Contract):
    id: UUID
    org_id: UUID
    session_id: UUID
    step_id: UUID | None = None
    kind: Literal["budget", "human_action", "authority", "recovery"]
    status: Literal["pending", "resolved", "cancelled", "expired"]
    question: str = Field(min_length=1, max_length=2000)
    action: Literal["review_cards", "export", "confidential_reveal", "clarify"] | None = None
    resource_ids: list[UUID] = Field(default_factory=list, max_length=100)
    budget_ref: BudgetDependencyRef | None = None
    input_sha256: Sha256
    resolved_by: UUID | None = None
    created_at: AwareDatetime
    resolved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def budget_reference_matches_kind(self):
        if (self.kind == "budget") != (self.budget_ref is not None):
            raise ValueError("only budget pauses carry a contract-budget reference")
        return self


class AgentJobLinkView(Contract):
    id: UUID
    org_id: UUID
    session_id: UUID
    job_id: UUID
    role: Literal["controller", "tool"]
    step_id: UUID | None = None
    owned: bool
    created_at: AwareDatetime


class AgentPreviewData(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    effective_scopes: list[AgentScope]
    tools: list[AgentToolDefinition] = Field(max_length=7)
    limits: AgentLimits
    estimated_cost: Cost
    estimate_basis: Literal["first_decision_upper_bound", "unknown"]
    blockers: list[str] = Field(default_factory=list, max_length=20)


class AgentMutationData(Contract):
    session: AgentSessionView
    job_id: UUID | None = None
    message_id: UUID | None = None
    pause: AgentPauseView | None = None
    deduplicated: bool = False


class AgentShowData(Contract):
    session: AgentSessionView
    principal: AgentPrincipalView
    pause: AgentPauseView | None = None


class AgentPageData(Contract):
    next_cursor: UUID | None = None


class AgentErrorData(Contract):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class AgentFailureData(Contract):
    error: AgentErrorData
    session_id: UUID | None = None
    job_id: UUID | None = None


class AgentJobResult(Contract):
    session_id: UUID
    checkpoint_revision: int = Field(strict=True, ge=1)
    disposition: Literal["continue", "waiting_job", "paused", "terminal"]
    session_state: SessionState
    step_id: UUID | None = None
    child_job_id: UUID | None = None
    pause_id: UUID | None = None
    completion: Literal["complete", "partial"] | None = None
    stop_reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    output_refs: list[AgentInputRef] = Field(default_factory=list, max_length=100)
    usage_ids: list[UUID] = Field(default_factory=list, max_length=64)
    cost: Cost

    @model_validator(mode="after")
    def completion_describes_session_only(self):
        expected = {"completed": "complete", "partial": "partial"}.get(self.session_state)
        if self.completion != expected:
            raise ValueError("completion applies only to completed or partial sessions")
        if (self.disposition == "terminal") != (
            self.session_state in {"completed", "partial", "failed", "cancelled"}
        ):
            raise ValueError("checkpoint disposition disagrees with the session state")
        return self


class AgentDecisionRequest(Contract):
    session_id: UUID
    step_id: UUID
    messages: list[AgentMessageView] = Field(max_length=40)
    tools: list[AgentToolDefinition] = Field(max_length=7)
    input_refs: list[AgentInputRef] = Field(max_length=100)
    remaining_steps: int = Field(strict=True, ge=1, le=64)
    deadline: AwareDatetime


class AgentDecisionOutput(Contract):
    """Adapter result; only decision is model-generated, usage comes from HTTP accounting."""

    decision: AgentDecision
    usage: ProviderUsage


class AgentInvocationContext(Contract):
    principal: AgentPrincipalView
    session_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    step_id: UUID
    job_id: UUID
    run_id: UUID
    invocation_id: UUID
    expected_session_revision: int = Field(strict=True, ge=1)
    expected_step_revision: int = Field(strict=True, ge=1)
    expected_step_state: StepState
    schema_sha256: Sha256
    input_refs: list[AgentInputRef] = Field(max_length=100)
    deadline: AwareDatetime


class AgentToolOutput(Contract):
    result: Result
    exit_code: ExitCode
    child_job_id: UUID | None = None
    reused_job: bool = False


class AgentReasoningProvider(LLMProvider, Protocol):
    async def decide(self, request: AgentDecisionRequest) -> AgentDecisionOutput: ...


class AgentToolProvider(Protocol):
    async def definitions(self, principal: AgentPrincipalView) -> list[AgentToolDefinition]: ...

    async def invoke(self, context: AgentInvocationContext, call: ToolCall) -> AgentToolOutput: ...

    async def recover(self, context: AgentInvocationContext) -> AgentToolOutput | None: ...


class AgentRecoveryProvider(Protocol):
    """Internal org-scoped queue recovery; not a model tool or a cross-org scan."""

    async def wake(self, org_id: UUID, session_id: UUID, now: datetime) -> AgentJobResult: ...
