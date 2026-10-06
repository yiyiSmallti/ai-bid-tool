"""Task review policies and human co-sign routes over the shared Result contract."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common import result
from app.core.config import Settings
from app.providers.storage import Storage
from app.schemas.contracts import Result
from app.schemas.team_workflow import (
    CoSignOpen,
    CoSignSignRequest,
    RequirementCoSignPolicySet,
    TaskRuleSet,
)
from app.services import task_cosign


def create_router(context: Callable[..., Any], settings: Settings, storage: Storage) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task_id}/review-rule", response_model=Result, name="task_review-rule_show")
    async def rule_show(task_id: UUID, ctx=Depends(context, scope="function")):
        data = await task_cosign.rule(ctx[0], ctx[1], task_id, settings)
        return result("task review-rule show", data.model_dump(mode="json"))

    @router.put("/tasks/{task_id}/review-rule", response_model=Result, name="task_review-rule_set")
    async def rule_set(task_id: UUID, body: TaskRuleSet, ctx=Depends(context, scope="function")):
        data = await task_cosign.set_rule(ctx[0], ctx[1], task_id, body, settings)
        return result("task review-rule set", data.model_dump(mode="json"))

    @router.get(
        "/tasks/{task_id}/requirements/{requirement_id}/review-policy",
        response_model=Result,
        name="card_policy_show",
    )
    async def policy_show(
        task_id: UUID,
        requirement_id: UUID,
        extraction_job_id: UUID,
        ctx=Depends(context, scope="function"),
    ):
        data = await task_cosign.policy(
            ctx[0], ctx[1], task_id, requirement_id, extraction_job_id, settings
        )
        return result("card policy show", data.model_dump(mode="json"))

    @router.put(
        "/tasks/{task_id}/requirements/{requirement_id}/review-policy",
        response_model=Result,
        name="card_policy_set",
    )
    async def policy_set(
        task_id: UUID,
        requirement_id: UUID,
        extraction_job_id: UUID,
        body: RequirementCoSignPolicySet,
        ctx=Depends(context, scope="function"),
    ):
        data = await task_cosign.set_policy(
            ctx[0], ctx[1], task_id, requirement_id, extraction_job_id, body, settings
        )
        return result("card policy set", data.model_dump(mode="json"))

    @router.get("/cards/{card_id}/signoffs", response_model=Result, name="card_signoff_list")
    async def signoff_list(
        card_id: UUID,
        cursor: str | None = Query(None, max_length=1024),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        page = await task_cosign.signoffs(
            ctx[0], ctx[1], card_id, settings, cursor=cursor, limit=limit
        )
        return result(
            "card signoff list",
            page.data.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in page.items],
        )

    @router.post(
        "/cards/{card_id}/review-rounds", response_model=Result, name="card_review-round_open"
    )
    async def review_round_open(
        card_id: UUID, body: CoSignOpen, ctx=Depends(context, scope="function")
    ):
        data = await task_cosign.open_round(
            ctx[0], ctx[1], card_id, body, settings, storage=storage
        )
        return result("card review-round open", data.model_dump(mode="json"))

    @router.post("/cards/{card_id}/signoffs", response_model=Result, name="card_signoff_add")
    async def signoff_add(
        card_id: UUID, body: CoSignSignRequest, ctx=Depends(context, scope="function")
    ):
        data = await task_cosign.sign(ctx[0], ctx[1], card_id, body, settings, storage=storage)
        return result("card signoff add", data.model_dump(mode="json"))

    return router
