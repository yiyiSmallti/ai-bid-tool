"""Approved budget interface snapshot; runtime types are in app.schemas.budget_contracts."""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from app.schemas.contracts import Contract, Cost, ProviderUsage, Result, TaskCreate
from app.schemas.provider_contracts import ProviderTest
from app.schemas.screenshot_contracts import Sha256
from pydantic import AwareDatetime, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=8, allow_inf_nan=False)]
SignedMoney = Annotated[Decimal, Field(max_digits=18, decimal_places=8, allow_inf_nan=False)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Capability = Literal["llm", "vision", "ocr", "search", "embedding", "browser"]
Payer = Literal["org_platform", "org_direct", "platform_absorbed", "local_free"]
BudgetBlocker = Literal[
    "task_budget_exceeded",
    "task_budget_unpriced",
    "task_budget_currency_review_required",
    "insufficient_balance",
    "job_charge_limit_exceeded",
    "job_call_limit_exceeded",
    "spend_cap_reached",
    "billing_price_unavailable",
    "billing_currency_mismatch",
    "provider_unavailable",
]


class TaskBudgetInput(Contract):
    limit: Money | None
    currency: Currency


class BudgetTaskCreate(TaskCreate):
    budget: TaskBudgetInput | None = None

    @model_validator(mode="after")
    def one_budget_input(self):
        if self.budget_usd is not None and not Decimal(str(self.budget_usd)).is_finite():
            raise ValueError("legacy budget_usd must be finite")
        if self.budget is not None and self.budget_usd is not None:
            raise ValueError("budget and legacy budget_usd are mutually exclusive")
        return self


class TaskBudgetSet(TaskBudgetInput):
    expected_revision: int = Field(strict=True, ge=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def nonblank_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("budget change reason cannot be blank")
        return self


class TaskBudgetView(Contract):
    org_id: UUID
    task_id: UUID
    revision: int = Field(ge=1)
    limit: Money | None
    currency: Currency
    state: Literal["active", "currency_review_required"]
    spent: Money
    reserved: Money
    available: SignedMoney | None
    unpriced_calls: int = Field(ge=0)
    unresolved_calls: int = Field(ge=0)
    history_complete: bool
    as_of: AwareDatetime

    @model_validator(mode="after")
    def consistent_available(self):
        known = (
            self.state == "active"
            and self.limit is not None
            and self.history_complete
            and self.unpriced_calls == 0
        )
        if known and self.limit is not None:
            if self.available != self.limit - self.spent - self.reserved:
                raise ValueError("available must subtract both settled and reserved exposure")
        elif self.available is not None:
            raise ValueError("an unlimited or unpriced budget has no known available amount")
        return self


class TaskBudgetRevisionView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    revision: int = Field(ge=1)
    limit: Money | None
    currency: Currency
    state: Literal["active", "currency_review_required"]
    actor_user_id: UUID | None
    origin: Literal["migration", "create", "human_update"]
    reason_sha256: Sha256 | None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def human_update_has_actor(self):
        if self.origin == "human_update" and (
            self.actor_user_id is None or self.reason_sha256 is None
        ):
            raise ValueError("human budget changes require actor and reason hash")
        return self


class TaskBudgetData(Contract):
    budget: TaskBudgetView


class BudgetHistoryData(Contract):
    task_id: UUID
    next_before_revision: int | None = Field(default=None, ge=1)


class BudgetCost(Cost):
    basis: Literal["actual", "first_pass_upper_bound", "unknown", "zero", "cache_hit"]
    charge: Money | None
    billing_currency: Currency
    task_amount: Money | None
    unpriced_calls: int = Field(default=0, ge=0)
    unresolved_calls: int = Field(default=0, ge=0)


class BudgetResult(Result):
    cost: BudgetCost = Field(...)


class BudgetCallQuote(Contract):
    capability: Capability
    payer: Payer
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    provider_config_id: UUID | None = None
    platform_model_id: str | None = None
    price_revision: str = Field(min_length=1, max_length=100)
    request_sha256: Sha256
    currency: Currency
    reserved_charge: Money
    reserved_task_amount: Money | None
    vendor_usd_upper_bound: Money | None
    input_tokens_upper_bound: int = Field(default=0, ge=0)
    output_tokens_upper_bound: int = Field(default=0, ge=0)
    ocr_pages_upper_bound: int = Field(default=0, ge=0)
    search_requests: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0, le=20)
    image_price_revision: str | None = None
    unknown_reason: Literal["missing_price", "currency_conversion_required", "unbounded"] | None = (
        None
    )

    @model_validator(mode="after")
    def payer_bounds(self):
        if (self.payer == "org_platform") != (self.platform_model_id is not None):
            raise ValueError("platform charges require the fixed catalog model identity")
        if self.payer != "org_platform" and self.reserved_charge != 0:
            raise ValueError("only org_platform calls reserve prepaid funds")
        if self.payer == "org_platform" and self.reserved_task_amount != self.reserved_charge:
            raise ValueError("platform task exposure equals its sale-price charge bound")
        if self.payer in {"local_free", "platform_absorbed"} and self.reserved_task_amount != 0:
            raise ValueError("free or platform-absorbed calls have explicit zero task liability")
        if (self.reserved_task_amount is None) != (self.unknown_reason is not None):
            raise ValueError("unknown task exposure requires an explicit reason")
        if self.payer == "org_direct" and self.reserved_task_amount is not None:
            if self.currency != "USD" or self.reserved_task_amount != self.vendor_usd_upper_bound:
                raise ValueError("direct vendor costs require known USD prices without conversion")
        return self


class BudgetCallView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID | None
    job_id: UUID
    run_id: UUID
    budget_revision: int | None = Field(default=None, ge=1)
    state: Literal["pending", "unknown", "completed"]
    quote: BudgetCallQuote
    task_amount: Money | None
    usage_record_id: UUID | None
    created_at: AwareDatetime


class BudgetProviderUsage(ProviderUsage):
    capability: Capability
    payer: Payer
    billing_currency: Currency
    task_amount: Money | None
    price_revision: str = Field(min_length=1, max_length=100)
    search_requests: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def payer_amounts(self):
        if self.payer == "org_platform":
            if self.platform_model_id is None or self.charge is None or self.task_amount is None:
                raise ValueError("platform settlement requires identity and both known amounts")
        elif self.platform_model_id is not None or self.charge not in (None, 0):
            raise ValueError("non-platform settlement cannot debit prepaid funds")
        if self.payer in {"local_free", "platform_absorbed"} and self.task_amount != 0:
            raise ValueError("free or platform-absorbed usage has explicit zero task liability")
        if self.payer == "org_direct" and self.task_amount is not None:
            if self.billing_currency != "USD" or self.usd is None:
                raise ValueError("direct task amounts require a known USD vendor cost")
        return self


class BudgetUsageView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID | None
    job_id: UUID
    run_id: UUID
    call_id: UUID
    usage: BudgetProviderUsage
    created_at: AwareDatetime


class BudgetPreflightData(Contract):
    dry_run: Literal[True] = True
    command: str
    task_id: UUID | None
    input_hash: Sha256
    as_of: AwareDatetime
    task_budget: TaskBudgetView | None
    planned_calls: int | None = Field(ge=0)
    maximum_calls: int | None = Field(default=None, ge=0)
    estimate: BudgetCost
    next_call: BudgetCallQuote | None
    admission_blocker: BudgetBlocker | None
    first_pass_fits: bool | None
    full_run_guaranteed: Literal[False] = False
    cached_job_id: UUID | None = None
    cached_result_cost: Cost | None = None
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    duration_basis: Literal["unknown", "measured_profile"] = "unknown"
    uncertainty: list[
        Literal[
            "retry_or_split",
            "dynamic_search",
            "unknown_price",
            "currency_conversion",
            "history_incomplete",
            "concurrent_spending",
            "unresolved_usage",
        ]
    ] = Field(default_factory=list)


class BudgetProviderTest(ProviderTest):
    dry_run: bool = False


class BudgetPlatformModelTest(Contract):
    dry_run: bool = False
    test_org_id: UUID | None = None

    @model_validator(mode="after")
    def live_probe_has_tenant(self):
        if not self.dry_run and self.test_org_id is None:
            raise ValueError(
                "live platform probes require an authorized internal test organization"
            )
        return self


class BudgetIntervention(Contract):
    code: BudgetBlocker
    org_id: UUID
    task_id: UUID | None
    job_id: UUID | None
    budget_revision: int | None = Field(default=None, ge=1)
    currency: Currency
    available: SignedMoney | None
    required_next_call: Money | None
    minimum_new_limit: Money | None
    action: Literal[
        "raise_task_budget",
        "recharge_org",
        "configure_price",
        "review_currency",
        "review_job_limit",
    ]
    authorized_roles: list[Literal["admin", "bidder"]]
    human_required: Literal[True] = True
    auto_retry: Literal[False] = False


class BudgetJobResult(Contract):
    """Attached as jobs.result.budget; domain results keep their existing schemas."""

    job_id: UUID
    run_id: UUID
    task_id: UUID | None
    completion: Literal["complete", "partial", "failed"]
    stop_reason: str | None
    intervention: BudgetIntervention | None
    cost: BudgetCost
    task_budget: TaskBudgetView | None
    published_ids: list[UUID] = Field(default_factory=list)
    remaining_ids: list[UUID] = Field(default_factory=list)
    usage_record_ids: list[UUID] = Field(default_factory=list)
    unresolved_call_ids: list[UUID] = Field(default_factory=list)
    continuation: Literal["none", "retry_job", "submit_remaining", "reconcile_first"]


class LowBalancePolicySet(Contract):
    threshold: Money | None
    currency: Currency
    expected_revision: int = Field(strict=True, ge=1)


class LowBalancePolicyView(Contract):
    org_id: UUID
    threshold: Money | None
    currency: Currency
    revision: int = Field(ge=1)
    available_balance: SignedMoney
    low: bool
    cycle: int = Field(ge=0)


class LowBalancePolicyData(Contract):
    policy: LowBalancePolicyView


class LowBalanceNoticeView(Contract):
    id: UUID
    org_id: UUID
    kind: Literal["billing.low_balance"] = "billing.low_balance"
    policy_revision: int = Field(ge=1)
    cycle: int = Field(ge=1)
    threshold: Money
    currency: Currency
    available_balance: SignedMoney
    created_at: AwareDatetime


class LowBalanceNoticesData(Contract):
    next_before: UUID | None = None


class BudgetQuoteProvider(Protocol):
    def quote(self, request: bytes, *, capability: Capability) -> BudgetCallQuote: ...


class BudgetCallAccounting(Protocol):
    def plan(self, first_pass_calls: int) -> None: ...

    async def admit(self, quote: BudgetCallQuote) -> UUID: ...

    async def complete(self, call_id: UUID, usage: BudgetProviderUsage) -> None: ...

    async def unknown(self, call_id: UUID) -> None: ...


class BudgetedCallProvider(Protocol):
    async def call[T](
        self,
        quote: BudgetCallQuote,
        operation: Callable[[], Awaitable[tuple[T, BudgetProviderUsage]]],
    ) -> tuple[T, BudgetProviderUsage]: ...
