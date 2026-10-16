"""Job-scoped call hooks, shared by extraction and future model capabilities."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.providers.base import NotDispatched, ProviderFailure
from app.schemas.contracts import ProviderUsage
from app.schemas.preflight_contracts import CallPlan


class CallAccounting(Protocol):
    def plan(self, first_pass_calls: int) -> None: ...

    async def admit(self, quote: CallPlan) -> UUID: ...

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


def _fixed_usage(quote: CallPlan, usage: ProviderUsage) -> ProviderUsage:
    return usage.model_copy(
        update={
            "capability": quote.capability,
            "provider": quote.provider,
            "version": quote.version,
            "provider_config_id": quote.provider_config_id,
        }
    )


def plan_calls(first_pass_calls: int) -> None:
    """Tell the active job how many calls a full first pass needs, to size its call ceiling."""
    accounting = current_accounting.get()
    if accounting is not None:
        accounting.plan(first_pass_calls)


async def accounted_call[T](
    quote: CallPlan,
    operation: Callable[[], Awaitable[tuple[T, ProviderUsage]]],
    *,
    before_send: Callable[[], Awaitable[None]] | None = None,
) -> tuple[T, ProviderUsage]:
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
        return result, _fixed_usage(quote, usage) if isinstance(quote, CallPlan) else usage
    call_id = await accounting.admit(quote)
    last_admitted_call.set(call_id)

    async def finish():
        settled = False
        prepared = False
        try:
            if before_send is not None:
                await before_send()
            prepared = True
            deadline = getattr(accounting, "call_deadline", None)
            if deadline is None:
                result, usage = await invoke()
            else:
                # Drain/record remains outside this deadline. Only the actual
                # dispatch is bounded by the session's remaining active lifetime.
                async with asyncio.timeout(max(0, (deadline - datetime.now(UTC)).total_seconds())):
                    result, usage = await invoke()
            if isinstance(quote, CallPlan):
                usage = _fixed_usage(quote, usage)
            await accounting.complete(call_id, usage)
            settled = True
            return result, usage
        except NotDispatched:
            # No connection opened, so the vendor cannot have received the request.
            prepared = False
            raise
        finally:
            if not settled:
                # An absent response does not prove no dispatch. Retain the unknown outcome.
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
