"""Tenant budget administration and persistent balance notices."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common import result
from app.core.config import Settings
from app.schemas.budget_contracts import LowBalancePolicySet, TaskBudgetSet
from app.schemas.contracts import Result
from app.services import budgets


def create_router(context: Callable[..., Any], settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task_id}/budget", response_model=Result)
    async def show(task_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data = await budgets.show(session, actor, task_id, settings.billing_currency)
        return result("task budget show", data.model_dump(mode="json"))

    @router.put("/tasks/{task_id}/budget", response_model=Result)
    async def change(task_id: UUID, body: TaskBudgetSet, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data = await budgets.set_budget(session, actor, task_id, body, settings.billing_currency)
        return result("task budget set", data.model_dump(mode="json"))

    @router.get("/tasks/{task_id}/budget/history", response_model=Result)
    async def history(
        task_id: UUID,
        before_revision: int | None = Query(None, ge=1),
        limit: int = Query(100, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await budgets.history(session, actor, task_id, before_revision, limit)
        return result(
            "task budget history",
            data.model_dump(mode="json"),
            [row.model_dump(mode="json") for row in items],
        )

    @router.get("/billing/low-balance-policy", response_model=Result)
    async def policy_show(ctx=Depends(context, scope="function")):
        session, actor = ctx
        data = await budgets.policy_show(session, actor, settings.billing_currency)
        return result("billing alert show", data.model_dump(mode="json"))

    @router.put("/billing/low-balance-policy", response_model=Result)
    async def policy_set(body: LowBalancePolicySet, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data = await budgets.policy_set(session, actor, body, settings.billing_currency)
        return result("billing alert set", data.model_dump(mode="json"))

    @router.get("/billing/notices", response_model=Result)
    async def notices(
        before: UUID | None = None,
        limit: int = Query(100, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await budgets.notices(session, actor, before, limit)
        return result(
            "billing notices",
            data.model_dump(mode="json"),
            [row.model_dump(mode="json") for row in items],
        )

    return router
