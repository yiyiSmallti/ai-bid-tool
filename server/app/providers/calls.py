"""Job-scoped call hooks, shared by extraction and future model capabilities."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.providers.base import ProviderFailure
from app.schemas.budget_contracts import BudgetCallQuote
from app.schemas.contracts import ProviderUsage


class CallAccounting(Protocol):
    def plan(self, first_pass_calls: int) -> None: ...

    async def admit(self, quote: BudgetCallQuote) -> UUID: ...

    async def complete(self, call_id: UUID, usage: ProviderUsage) -> None: ...

    async def unknown(self, call_id: UUID) -> None: ...

    async def not_sent(self, call_id: UUID) -> None: ...


# Context-local, so concurrent jobs can safely share an adapter instance. Child batch
# tasks inherit the attempt; neither a model nor a reasoning copy owns mutable job state.
current_accounting: ContextVar[CallAccounting | None] = ContextVar(
    "current_call_accounting", default=None
)


current_drafting_input: ContextVar[dict | None] = ContextVar("current_drafting_input", default=None)
last_admitted_call: ContextVar[UUID | None] = ContextVar("last_admitted_call", default=None)
evaluation_calls: ContextVar[bool] = ContextVar("evaluation_calls", default=False)


@contextmanager
def standalone_evaluation():
    """Opt in only at standalone evaluation entrypoints that save their own receipts."""
    token = evaluation_calls.set(True)
    try:
        yield
    finally:
        evaluation_calls.reset(token)


def _fixed_usage(quote: BudgetCallQuote, usage: ProviderUsage) -> ProviderUsage:
    return usage.model_copy(
        update={
            "capability": quote.capability,
            "payer": quote.payer,
            "billing_currency": quote.currency,
            "price_revision": quote.price_revision,
            "provider": quote.provider,
            # The quote retains the requested model and fixed billing identity.
            # Keep the provider's validated/sanitized actual model in its receipt.
            "version": quote.version,
            "platform_model_id": quote.platform_model_id,
            "provider_config_id": quote.provider_config_id,
            "charge": usage.charge if quote.payer == "org_platform" else 0,
            "task_amount": usage.task_amount
            if quote.payer == "org_platform"
            else usage.task_amount
            if quote.payer == "org_direct" and quote.currency == "USD"
            else None
            if quote.payer == "org_direct"
            else Decimal(0),
        }
    )


def plan_calls(first_pass_calls: int) -> None:
    """Tell the active job how many calls a full first pass needs, to size its call ceiling."""
    accounting = current_accounting.get()
    if accounting is not None:
        accounting.plan(first_pass_calls)


async def accounted_call[T](
    quote: BudgetCallQuote | Decimal,
    operation: Callable[[], Awaitable[tuple[T, ProviderUsage]]] | bool,
    legacy_operation: Callable[[], Awaitable[tuple[T, ProviderUsage]]] | None = None,
    *,
    before_send: Callable[[], Awaitable[None]] | None = None,
) -> tuple[T, ProviderUsage]:
    legacy = not isinstance(quote, BudgetCallQuote)
    if legacy:
        if legacy_operation is None or not isinstance(operation, bool):
            raise TypeError("Legacy call accounting requires reserve, payer and operation")
        invoke = legacy_operation
    else:
        if isinstance(operation, bool):
            raise TypeError("A quote requires an operation")
        invoke = operation
    accounting = current_accounting.get()
    if accounting is None:
        if not evaluation_calls.get():
            raise ProviderFailure(
                "Provider calls require an active accounting context",
                code="provider_accounting_required",
            )
        if before_send is not None:
            await before_send()
        result, usage = await invoke()
        return result, _fixed_usage(quote, usage) if isinstance(quote, BudgetCallQuote) else usage
    if legacy:
        # Compatibility is restricted to old in-process callers. Production adapters
        # always construct a fixed quote before reaching this boundary.
        call_id = await accounting.admit(quote, operation)  # type: ignore[call-arg]
    else:
        call_id = await accounting.admit(quote)
    last_admitted_call.set(call_id)

    async def finish():
        settled = False
        prepared = False
        try:
            if before_send is not None:
                await before_send()
            prepared = True
            result, usage = await invoke()
            if isinstance(quote, BudgetCallQuote):
                usage = _fixed_usage(quote, usage)
            await accounting.complete(call_id, usage)
            settled = True
            return result, usage
        finally:
            if not settled:
                # No response/usage is not proof of no vendor charge. Keep the hold.
                if prepared:
                    await accounting.unknown(call_id)
                else:
                    await accounting.not_sent(call_id)

    pending = asyncio.create_task(finish())
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError as cancelled:
        # Finish only the already admitted request and its accounting, never the next
        # batch. The vendor deadline bounds this drain during graceful cancellation.
        try:
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            if not pending.cancelled():
                pending.result()
        finally:
            raise cancelled
