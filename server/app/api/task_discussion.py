"""Assignment and human discussion routes; shared CLI Result and service gates."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common import result
from app.core.config import Settings
from app.schemas.contracts import Result
from app.schemas.team_workflow import (
    CommentReplyCreate,
    CommentThreadCreate,
    RequirementAssignmentSet,
)
from app.services import task_discussion


def create_router(context: Callable[..., Any], settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.put("/tasks/{task_id}/requirements/{requirement_id}/assignment", response_model=Result)
    async def card_assign(
        task_id: UUID,
        requirement_id: UUID,
        extraction_job_id: UUID,
        body: RequirementAssignmentSet,
        ctx=Depends(context, scope="function"),
    ):
        data = await task_discussion.set_assignment(
            ctx[0], ctx[1], task_id, requirement_id, extraction_job_id, body, settings
        )
        return result("card assign", data.model_dump(mode="json"))

    @router.get("/cards/{card_id}/threads", response_model=Result)
    async def card_thread_list(
        card_id: UUID,
        cursor: str | None = Query(None, max_length=1024),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        page = await task_discussion.list_threads(
            ctx[0], ctx[1], card_id, settings, cursor=cursor, limit=limit
        )
        return result(
            "card thread list",
            page.data.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in page.items],
        )

    @router.post("/cards/{card_id}/threads", response_model=Result)
    async def card_thread_create(
        card_id: UUID, body: CommentThreadCreate, ctx=Depends(context, scope="function")
    ):
        data = await task_discussion.create_thread(ctx[0], ctx[1], card_id, body, settings)
        return result("card thread create", data.model_dump(mode="json"))

    @router.get("/cards/{card_id}/threads/{thread_id}/comments", response_model=Result)
    async def card_comment_list(
        card_id: UUID,
        thread_id: UUID,
        cursor: str | None = Query(None, max_length=1024),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        page = await task_discussion.list_comments(
            ctx[0], ctx[1], card_id, thread_id, settings, cursor=cursor, limit=limit
        )
        return result(
            "card comment list",
            page.data.model_dump(mode="json"),
            [item.model_dump(mode="json") for item in page.items],
        )

    @router.post("/cards/{card_id}/threads/{thread_id}/comments", response_model=Result)
    async def card_comment_add(
        card_id: UUID,
        thread_id: UUID,
        body: CommentReplyCreate,
        ctx=Depends(context, scope="function"),
    ):
        data = await task_discussion.add_comment(ctx[0], ctx[1], card_id, thread_id, body, settings)
        return result("card comment add", data.model_dump(mode="json"))

    return router
