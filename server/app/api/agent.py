"""Authenticated, owner-scoped agent management commands."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.schemas.agent_contracts import (
    AgentCancelRequest,
    AgentListRequest,
    AgentMessageRequest,
    AgentResumeRequest,
    AgentStartRequest,
)
from app.schemas.contracts import Result


def create_router(context, settings, queue, llm, resolve, storage):
    from app.services import agents

    router = APIRouter()

    def resources(ctx):
        session, _ = ctx
        session.info["agent_storage"] = storage
        session.info["agent_llm"] = llm
        session.info["agent_resolve"] = resolve

    @router.post("/tasks/{task_id}/agent-sessions", name="agent_start", response_model=Result)
    async def start(task_id: UUID, body: AgentStartRequest, ctx=Depends(context, scope="function")):
        resources(ctx)
        return await agents.start(ctx[0], ctx[1], task_id, body, settings, queue)

    @router.get("/tasks/{task_id}/agent-sessions", name="agent_list", response_model=Result)
    async def listing(
        task_id: UUID,
        cursor: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await agents.list_sessions(
            ctx[0], ctx[1], task_id, AgentListRequest(cursor=cursor, limit=limit)
        )

    @router.get("/agent-sessions/{session_id}", name="agent_show", response_model=Result)
    async def show(session_id: UUID, ctx=Depends(context, scope="function")):
        return await agents.show(ctx[0], ctx[1], session_id)

    @router.get(
        "/agent-sessions/{session_id}/messages", name="agent_messages", response_model=Result
    )
    async def messages(
        session_id: UUID,
        cursor: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await agents.messages(
            ctx[0], ctx[1], session_id, AgentListRequest(cursor=cursor, limit=limit), settings
        )

    @router.get("/agent-sessions/{session_id}/steps", name="agent_steps", response_model=Result)
    async def steps(
        session_id: UUID,
        cursor: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await agents.steps(
            ctx[0], ctx[1], session_id, AgentListRequest(cursor=cursor, limit=limit), settings
        )

    @router.post(
        "/agent-sessions/{session_id}/messages", name="agent_message", response_model=Result
    )
    async def message(
        session_id: UUID, body: AgentMessageRequest, ctx=Depends(context, scope="function")
    ):
        return await agents.message(ctx[0], ctx[1], session_id, body, settings)

    @router.post("/agent-sessions/{session_id}/resume", name="agent_resume", response_model=Result)
    async def resume(
        session_id: UUID, body: AgentResumeRequest, ctx=Depends(context, scope="function")
    ):
        resources(ctx)
        return await agents.resume(ctx[0], ctx[1], session_id, body, settings, queue)

    @router.post("/agent-sessions/{session_id}/cancel", name="agent_cancel", response_model=Result)
    async def cancel(
        session_id: UUID, body: AgentCancelRequest, ctx=Depends(context, scope="function")
    ):
        return await agents.cancel(ctx[0], ctx[1], session_id, body, settings)

    return router
