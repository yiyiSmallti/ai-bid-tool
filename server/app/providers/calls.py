"""Job-scoped call hooks, shared by extraction and future model capabilities."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.schemas.contracts import ProviderUsage


class CallAccounting(Protocol):
    def plan(self, first_pass_calls: int) -> None: ...

    async def admit(self, reserved_charge: Decimal, platform_billed: bool) -> UUID: ...

    async def complete(self, call_id: UUID, usage: ProviderUsage) -> None: ...

    async def unknown(self, call_id: UUID) -> None: ...


# Context-local, so concurrent jobs can safely share an adapter instance. Child batch
# tasks inherit the attempt; neither a model nor a reasoning copy owns mutable job state.
current_accounting: ContextVar[CallAccounting | None] = ContextVar(
    "current_call_accounting", default=None
)


current_drafting_input: ContextVar[dict | None] = ContextVar("current_drafting_input", default=None)
last_admitted_call: ContextVar[UUID | None] = ContextVar("last_admitted_call", default=None)


def plan_calls(first_pass_calls: int) -> None:
    """Tell the active job how many calls a full first pass needs, to size its call ceiling."""
    accounting = current_accounting.get()
    if accounting is not None:
        accounting.plan(first_pass_calls)


async def accounted_call[T](
    reserved_charge: Decimal,
    platform_billed: bool,
    operation: Callable[[], Awaitable[tuple[T, ProviderUsage]]],
) -> tuple[T, ProviderUsage]:
    accounting = current_accounting.get()
    if accounting is None:
        return await operation()
    call_id = await accounting.admit(reserved_charge, platform_billed)
    last_admitted_call.set(call_id)

    async def finish():
        settled = False
        try:
            result, usage = await operation()
            await accounting.complete(call_id, usage)
            settled = True
            return result, usage
        finally:
            if not settled:
                # No response/usage is not proof of no vendor charge. Keep the hold.
                await accounting.unknown(call_id)

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
