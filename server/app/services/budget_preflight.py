"""Read-only quote aggregation and current admission hints; never reserves funds."""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, true

from app.models.entities import Job, OrgBalance, Task, VendorCall
from app.providers.base import ProviderFailure
from app.schemas.budget_contracts import BudgetCallQuote, BudgetCost, BudgetPreflightData
from app.services import budgets


def estimate_quotes(quotes: list[BudgetCallQuote], currency: str) -> BudgetCost:
    unknown = sum(quote.reserved_task_amount is None for quote in quotes)
    usd = (
        None
        if any(q.vendor_usd_upper_bound is None for q in quotes)
        else sum((q.vendor_usd_upper_bound or Decimal(0) for q in quotes), Decimal(0))
    )
    return BudgetCost(
        llm_tokens=sum(q.input_tokens_upper_bound + q.output_tokens_upper_bound for q in quotes),
        ocr_pages=sum(q.ocr_pages_upper_bound for q in quotes),
        usd=float(usd) if usd is not None else None,
        basis="unknown" if unknown else "first_pass_upper_bound" if quotes else "zero",
        charge=sum((q.reserved_charge for q in quotes), Decimal(0)),
        billing_currency=currency,
        task_amount=None
        if unknown
        else sum((q.reserved_task_amount or Decimal(0) for q in quotes), Decimal(0)),
        unpriced_calls=unknown,
    )


async def attach(
    session,
    data: dict,
    *,
    command: str,
    task_id: UUID | None,
    input_hash: str,
    currency: str = "USD",
    settings=None,
    quotes: list[BudgetCallQuote] | None = None,
    planned_calls: int | None = None,
    dynamic: bool = False,
    cached_job: Job | None = None,
    max_charge: Decimal | None = None,
    check_balance: bool = True,
    quote_sources: list[Callable[[], BudgetCallQuote]] | None = None,
) -> dict:
    """Attach an estimate only after the command has validated access and inputs."""
    task = await session.get(Task, task_id) if task_id is not None else None
    budget = await budgets.view(session, task, currency) if task is not None else None
    if quote_sources is not None:
        try:
            quotes = [source() for source in quote_sources]
        except ProviderFailure as error:
            if error.code not in {"billing_price_unavailable", "billing_bound_unavailable"}:
                raise
            quotes = []
            data = {
                **data,
                "admission_blocker": "billing_price_unavailable",
                "cost_basis": "unknown",
            }
    if cached_job is None and task_id is not None:
        kinds = {
            "check run": "check",
            "score run": "score",
            "score rubric generate": "score_rubric",
            "draft": "draft",
            "screenshot annotate": "screenshot_render",
            "screenshot analyze": "screenshot_analyze",
            "ui mock": "prototype_generate",
            "evidence search": "screenshot_search",
            "product simulate": "product_simulation",
        }
        kind = kinds.get(command)
        if kind is not None:
            actor = session.info.get("actor")
            cached_job = await session.scalar(
                select(Job)
                .where(
                    Job.task_id == task_id,
                    Job.kind == kind,
                    Job.result["submission"]["input_hash"].astext == input_hash,
                    Job.actor_user_id == actor.user_id
                    if actor is not None and kind != "draft"
                    else true(),
                    Job.actor_token_id == actor.token_id
                    if actor is not None and kind != "draft"
                    else true(),
                    Job.actor_kind == actor.actor_kind
                    if actor is not None and kind in {"check", "score", "score_rubric"}
                    else true(),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
    quotes = quotes if quotes is not None else []
    quotes = [
        quote.model_copy(update={"currency": currency})
        if quote.payer in {"local_free", "platform_absorbed"}
        else quote
        for quote in quotes
    ]
    estimate = estimate_quotes(quotes, currency)
    supported_blockers = {
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
    }
    existing_blocker = data.get("admission_blocker")
    blocker = (
        existing_blocker
        if existing_blocker in supported_blockers
        else (
            "spend_cap_reached"
            if existing_blocker == "spend_cap_below_first_call"
            else "provider_unavailable"
            if existing_blocker is not None
            else None
        )
    )
    uncertainty = ["concurrent_spending"] if quotes else []
    next_call = quotes[0] if quotes else None
    if planned_calls is None or dynamic:
        estimate = estimate.model_copy(update={"basis": "unknown"})
        uncertainty += ["dynamic_search" if dynamic else "retry_or_split"]
    if planned_calls != 0 and not quotes:
        estimate = estimate.model_copy(
            update={
                "basis": "unknown",
                "usd": None,
                "charge": None,
                "task_amount": None,
            }
        )
        if data.get("input_blocker") != "not_parsed":
            blocker = blocker or "provider_unavailable"
    if blocker == "billing_price_unavailable" and not quotes:
        estimate = estimate.model_copy(
            update={
                "unpriced_calls": max(1, len(quote_sources)) if quote_sources is not None else 1,
            }
        )
    if any(q.unknown_reason for q in quotes) or blocker == "billing_price_unavailable":
        uncertainty.append("unknown_price")
    if budget is not None:
        if not budget.history_complete:
            uncertainty.append("history_incomplete")
        if budget.unresolved_calls:
            uncertainty.append("unresolved_usage")
        if next_call is not None and next_call.reserved_task_amount != 0:
            if budget.state != "active" or budget.currency != next_call.currency:
                blocker = "task_budget_currency_review_required"
            elif budget.limit is not None and (
                budget.unpriced_calls
                or not budget.history_complete
                or next_call.reserved_task_amount is None
            ):
                blocker = "task_budget_unpriced"
            elif (
                budget.available is not None
                and next_call.reserved_task_amount is not None
                and next_call.reserved_task_amount > budget.available
            ):
                blocker = "task_budget_exceeded"
    first_pass_fits = None
    if not dynamic and planned_calls is not None and estimate.task_amount is not None:
        first_pass_fits = (
            estimate.task_amount == 0
            or budget is None
            or budget.limit is None
            or (budget.available is not None and estimate.task_amount <= budget.available)
        )
    if settings is not None and planned_calls is not None and estimate.charge is not None:
        if estimate.charge > settings.job_max_charge or (
            max_charge is not None and estimate.charge > max_charge
        ):
            first_pass_fits = False
    if cached_job is not None and cached_job.status != "succeeded" and settings is not None:
        calls = (
            await session.scalars(
                select(VendorCall).where(
                    VendorCall.job_id == cached_job.id, VendorCall.state != "not_sent"
                )
            )
        ).all()
        exposure = sum(
            (call.charge if call.state == "completed" else call.reserved_charge for call in calls),
            Decimal(0),
        )
        ceiling = max(
            settings.job_max_vendor_calls,
            math.ceil((planned_calls or 0) * settings.job_vendor_calls_per_batch),
        )
        if next_call is not None:
            if len(calls) >= ceiling:
                blocker = blocker or "job_call_limit_exceeded"
            elif exposure + next_call.reserved_charge > settings.job_max_charge:
                blocker = blocker or "job_charge_limit_exceeded"
            elif max_charge is not None and exposure + next_call.reserved_charge > max_charge:
                blocker = blocker or "spend_cap_reached"
        if (
            planned_calls is not None
            and estimate.charge is not None
            and (
                len(calls) + planned_calls > ceiling
                or exposure + estimate.charge > settings.job_max_charge
                or (max_charge is not None and exposure + estimate.charge > max_charge)
            )
        ):
            first_pass_fits = False
    if next_call is not None and blocker is None:
        if settings is not None and next_call.reserved_charge > settings.job_max_charge:
            blocker = "job_charge_limit_exceeded"
        elif max_charge is not None and next_call.reserved_charge > max_charge:
            blocker = "spend_cap_reached"
        elif next_call.reserved_charge and check_balance:
            balance = await session.scalar(select(OrgBalance))
            held = await session.scalar(
                select(func.coalesce(func.sum(VendorCall.reserved_charge), 0)).where(
                    VendorCall.state != "completed"
                )
            )
            if balance is not None and balance.currency != currency:
                blocker = "billing_currency_mismatch"
            elif (
                balance is None
                or balance.balance - (held or Decimal(0)) < next_call.reserved_charge
            ):
                blocker = "insufficient_balance"
            if estimate.charge is not None and (
                balance is None or balance.balance - (held or Decimal(0)) < estimate.charge
            ):
                first_pass_fits = False
    if blocker is not None and first_pass_fits is not None:
        first_pass_fits = False
    if not check_balance and any(quote.reserved_charge for quote in quotes):
        first_pass_fits = None
    cached_cost = None
    if cached_job is not None and cached_job.status == "succeeded":
        from app.jobs.execution import job_cost

        cached_cost = BudgetCost.model_validate(await job_cost(session, cached_job.id, currency))
        estimate = estimate_quotes([], currency).model_copy(update={"basis": "cache_hit"})
        blocker, next_call, first_pass_fits, planned_calls, uncertainty = None, None, True, 0, []
    preflight = BudgetPreflightData(
        command=command,
        task_id=task_id,
        input_hash=input_hash,
        as_of=datetime.now(UTC),
        task_budget=budget,
        planned_calls=planned_calls,
        maximum_calls=max(
            settings.job_max_vendor_calls,
            math.ceil(planned_calls * settings.job_vendor_calls_per_batch),
        )
        if settings is not None and planned_calls is not None
        else None,
        estimate=estimate,
        next_call=next_call,
        admission_blocker=blocker,
        first_pass_fits=first_pass_fits,
        cached_job_id=cached_job.id if cached_job is not None and cached_cost is not None else None,
        cached_result_cost=cached_cost,
        uncertainty=list(dict.fromkeys(uncertainty)),  # pyright: ignore[reportArgumentType]
    )
    return {**data, "budget_preflight": preflight.model_dump(mode="json")}
