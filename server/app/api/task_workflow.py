"""Human task membership and lifecycle routes using the shared Result contract."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common import result
from app.core.config import Settings
from app.schemas.contracts import Result
from app.schemas.team_workflow import TaskMemberSet, TaskOwnerHandover, WorkflowMutation
from app.services import task_workflow


def create_router(context: Callable[..., Any], settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task_id}/workflow", response_model=Result)
    async def show(task_id: UUID, ctx=Depends(context, scope="function")):
        data = await task_workflow.show(ctx[0], ctx[1], task_id, settings)
        return result("task workflow", {"workflow": data.workflow.model_dump(mode="json")})

    @router.get("/tasks/{task_id}/members", response_model=Result)
    async def members(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await task_workflow.list_members(
            ctx[0], ctx[1], task_id, settings, cursor=cursor, limit=limit
        )
        return result(
            "task member list",
            data.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in items],
        )

    @router.get("/tasks/{task_id}/member-candidates", response_model=Result)
    async def candidates(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await task_workflow.list_members(
            ctx[0], ctx[1], task_id, settings, cursor=cursor, limit=limit, candidates=True
        )
        return result(
            "task member candidates",
            data.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in items],
        )

    @router.put("/tasks/{task_id}/members/{user_id}", response_model=Result)
    async def set_member(
        task_id: UUID, user_id: UUID, body: TaskMemberSet, ctx=Depends(context, scope="function")
    ):
        data = await task_workflow.set_member(ctx[0], ctx[1], task_id, user_id, body, settings)
        return result("task member set", data.model_dump(mode="json"))

    @router.post("/tasks/{task_id}/members/{user_id}/remove", response_model=Result)
    async def remove(
        task_id: UUID, user_id: UUID, body: WorkflowMutation, ctx=Depends(context, scope="function")
    ):
        data = await task_workflow.remove_member(ctx[0], ctx[1], task_id, user_id, body, settings)
        return result("task member remove", data.model_dump(mode="json"))

    @router.post("/tasks/{task_id}/handover", response_model=Result)
    async def handover(
        task_id: UUID, body: TaskOwnerHandover, ctx=Depends(context, scope="function")
    ):
        data = await task_workflow.handover(ctx[0], ctx[1], task_id, body, settings)
        return result("task handover", data.model_dump(mode="json"))

    @router.post("/tasks/{task_id}/archive", response_model=Result)
    async def archive(
        task_id: UUID, body: WorkflowMutation, ctx=Depends(context, scope="function")
    ):
        data = await task_workflow.archive(ctx[0], ctx[1], task_id, body, settings)
        return result("task archive", data.model_dump(mode="json"))

    @router.post("/tasks/{task_id}/unarchive", response_model=Result)
    async def unarchive(
        task_id: UUID, body: WorkflowMutation, ctx=Depends(context, scope="function")
    ):
        data = await task_workflow.archive(ctx[0], ctx[1], task_id, body, settings, restore=True)
        return result("task unarchive", data.model_dump(mode="json"))

    return router
