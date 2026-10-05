"""Memory API: human approval and durable candidate job submission."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.errors import ServiceError
from app.memory import candidates, crud, feedback, retrieval
from app.schemas.contracts import Result
from app.schemas.memory_contracts import (
    MemoryCandidateJobRequest,
    MemoryCreate,
    MemoryDecision,
    MemoryDelete,
    MemoryDisable,
    MemoryEvalReview,
    MemoryListRequest,
    MemoryRetrievalRequest,
    MemoryTarget,
    MemoryUpdate,
)


def create_router(context, db, queue, settings, storage):
    router = APIRouter()

    @router.post("/memories", name="memory_add", response_model=Result)
    async def add(body: MemoryCreate, ctx=Depends(context, scope="function")):
        return await crud.create_memory(ctx[0], ctx[1], body, settings)

    @router.get("/memories", name="memory_list", response_model=Result)
    async def listing(
        scope: Literal["global", "org", "user", "project"] = Query(...),
        user_id: UUID | None = None,
        task_id: UUID | None = None,
        status: Literal["candidate", "active", "disabled"] | None = None,
        include_deleted: bool = False,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        try:
            request = MemoryListRequest(
                target=MemoryTarget(scope=scope, user_id=user_id, task_id=task_id),
                status=status,
                include_deleted=include_deleted,
                cursor=cursor,
                limit=limit,
            )  # pyright: ignore[reportArgumentType]
        except ValueError:
            raise ServiceError(
                "invalid_input", "Memory target does not match its scope", 422, 2
            ) from None
        return await crud.list_memories(ctx[0], ctx[1], request, settings)

    # Register static retrieval before the UUID resource route.
    @router.post("/memories/retrieve", name="memory_retrieve", response_model=Result)
    async def retrieve(
        body: MemoryRetrievalRequest, preview: bool = False, ctx=Depends(context, scope="function")
    ):
        output = await retrieval.retrieve(ctx[0], ctx[1], body, settings, preview=preview)
        return Result(
            ok=True,
            command="memory retrieve",
            data=output.data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in output.items],
            warnings=output.warnings,
        )

    @router.get("/memories/{memory_id}", name="memory_show", response_model=Result)
    async def show(memory_id: UUID, ctx=Depends(context, scope="function")):
        return await crud.show_memory(ctx[0], ctx[1], memory_id)

    @router.put("/memories/{memory_id}", name="memory_update", response_model=Result)
    async def update(memory_id: UUID, body: MemoryUpdate, ctx=Depends(context, scope="function")):
        return await crud.update_memory(ctx[0], ctx[1], memory_id, body, settings)

    @router.get("/memories/{memory_id}/history", name="memory_history", response_model=Result)
    async def history(
        memory_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await crud.history(ctx[0], ctx[1], memory_id, settings, cursor=cursor, limit=limit)

    @router.post("/memories/{memory_id}/decisions", name="memory_decision", response_model=Result)
    async def decide(memory_id: UUID, body: MemoryDecision, ctx=Depends(context, scope="function")):
        return await crud.decide_memory(ctx[0], ctx[1], memory_id, body, settings)

    @router.post("/memories/{memory_id}/disable", name="memory_disable", response_model=Result)
    async def disable(memory_id: UUID, body: MemoryDisable, ctx=Depends(context, scope="function")):
        return await crud.disable_memory(ctx[0], ctx[1], memory_id, body, settings)

    @router.delete("/memories/{memory_id}", name="memory_delete", response_model=Result)
    async def delete(memory_id: UUID, body: MemoryDelete, ctx=Depends(context, scope="function")):
        return await crud.delete_memory(ctx[0], ctx[1], memory_id, body, settings)

    @router.get(
        "/memory-retrievals/{retrieval_id}", name="memory_retrieval_show", response_model=Result
    )
    async def retrieval_show(retrieval_id: UUID, ctx=Depends(context, scope="function")):
        output = await retrieval.show_retrieval(ctx[0], ctx[1], retrieval_id, settings)
        return Result(
            ok=True,
            command="memory retrieval show",
            data=output.data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in output.items],
            warnings=output.warnings,
        )

    @router.get("/jobs/{job_id}/memory", name="memory_used", response_model=Result)
    async def used(job_id: UUID, ctx=Depends(context, scope="function")):
        data = await retrieval.used(ctx[0], ctx[1], job_id, storage)
        return Result(ok=True, command="memory used", data=data.model_dump(mode="json"))

    @router.post(
        "/tasks/{task_id}/memory-candidates", name="memory_candidates_run", response_model=Result
    )
    async def run(
        task_id: UUID, body: MemoryCandidateJobRequest, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        output, job = await candidates.submit_candidates(session, actor, task_id, body, settings)
        if job is not None:
            await session.commit()
            warnings = await candidates.dispatch(db, queue, actor.org_id, job)
            output.warnings.extend(warnings)
            if warnings:
                failed = Result(
                    ok=False,
                    command="memory candidates run",
                    data={
                        "error": {
                            "code": "queue_unavailable",
                            "message": "Candidate job saved; repeat request to schedule it",
                            "exit_code": 3,
                        },
                        "job_id": str(job.id),
                    },
                    warnings=warnings,
                )
                return JSONResponse(status_code=503, content=failed.model_dump(mode="json"))
        return output

    @router.get(
        "/tasks/{task_id}/memory-feedback", name="memory_feedback_list", response_model=Result
    )
    async def feedback_list(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await feedback.list_feedback(ctx[0], ctx[1], task_id, cursor=cursor, limit=limit)

    @router.get(
        "/tasks/{task_id}/memory-evaluations", name="memory_samples_list", response_model=Result
    )
    async def samples_list(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        return await feedback.list_samples(ctx[0], ctx[1], task_id, cursor=cursor, limit=limit)

    @router.get(
        "/memory-evaluations/{sample_id}", name="memory_samples_show", response_model=Result
    )
    async def samples_show(sample_id: UUID, ctx=Depends(context, scope="function")):
        return await feedback.show_sample(ctx[0], ctx[1], sample_id, settings)

    @router.post(
        "/memory-evaluations/{sample_id}/review",
        name="memory_samples_review",
        response_model=Result,
    )
    async def samples_review(
        sample_id: UUID, body: MemoryEvalReview, ctx=Depends(context, scope="function")
    ):
        return await feedback.review_sample(ctx[0], ctx[1], sample_id, body, settings)

    return router
