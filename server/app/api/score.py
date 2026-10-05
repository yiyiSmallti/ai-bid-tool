"""Rubric generation and task-bound human review routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError

from app.core.errors import ServiceError
from app.models.entities import Job
from app.schemas.contracts import Result
from app.schemas.score_contracts import (
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricGenerateRequest,
    RubricItemDecisionRequest,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
)
from app.services import score, score_generation
from app.services.versioned import audit


def create_router(context, db, storage, queue, settings) -> APIRouter:
    router = APIRouter()

    def result(command, data=None, items=None):
        return Result(
            ok=True,
            command=f"score rubric {command}",
            data=data if data is not None else {},
            items=items if items is not None else [],
        )

    async def invoke(operation, session, actor, *args, **kwargs):
        try:
            return await operation(session, actor, *args, **kwargs)
        except ServiceError as error:
            details = getattr(error, "rubric_audit", None)
            if details is not None:
                # The service attaches only hashes and IDs after authorizing the
                # actual parent graph. Roll back business writes before recording
                # the rejection independently of the failed request transaction.
                await session.rollback()
                details = dict(details)
                object_id = UUID(details.pop("object_id"))
                action = details.pop("action", "score_rubric.decision_denied")
                async with db.transaction(actor.org_id) as audit_session:
                    audit(audit_session, actor, action, object_id, details)
            raise

    async def generate(task_id, body, ctx):
        session, actor = ctx
        data, job = await invoke(
            score_generation.submit_rubric, session, actor, task_id, body, settings
        )
        if job is not None and job.status == "queued" and job.queue_id is None:
            # A queue outage leaves an authorized durable job that the same request
            # can redispatch without another provider call or submitted audit.
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError):
                body = Result(
                    ok=False,
                    command="score rubric generate",
                    data={
                        "error": {
                            "code": "queue_unavailable",
                            "message": "Rubric job saved; repeat request to schedule it",
                            "exit_code": 3,
                        },
                        "job_id": str(job.id),
                    },
                )
                return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result("generate", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/preview",
        name="score_rubric_generate",
        response_model=Result,
    )
    async def preview(
        task_id: UUID, body: RubricGenerateRequest, ctx=Depends(context, scope="function")
    ):
        if not body.dry_run:
            raise ServiceError("invalid_input", "Rubric preview requires dry_run=true", 422, 2)
        return await generate(task_id, body, ctx)

    @router.post(
        "/tasks/{task_id}/score-rubrics", name="score_rubric_generate", response_model=Result
    )
    async def submit(
        task_id: UUID, body: RubricGenerateRequest, ctx=Depends(context, scope="function")
    ):
        if body.dry_run:
            raise ServiceError("invalid_input", "Use the rubric preview route for dry-run", 422, 2)
        return await generate(task_id, body, ctx)

    @router.get("/tasks/{task_id}/score-rubrics", name="score_rubric_list", response_model=Result)
    async def list_rubrics(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await score.list_rubrics(
            ctx[0], ctx[1], task_id, storage, settings, cursor=cursor, limit=limit
        )
        return result("list", data, items)

    @router.get(
        "/tasks/{task_id}/score-rubrics/{rubric_id}",
        name="score_rubric_show",
        response_model=Result,
    )
    async def show(task_id: UUID, rubric_id: UUID, ctx=Depends(context, scope="function")):
        data, items = await score.show_rubric(ctx[0], ctx[1], task_id, rubric_id, storage, settings)
        return result("show", data, items)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/revisions",
        name="score_rubric_revise",
        response_model=Result,
    )
    async def revise(
        task_id: UUID,
        rubric_id: UUID,
        body: RubricReviseRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.revise_rubric, ctx[0], ctx[1], task_id, rubric_id, body, storage, settings
        )
        return result("revise", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/classification",
        name="score_rubric_classify",
        response_model=Result,
    )
    async def classify_section(
        task_id: UUID,
        rubric_id: UUID,
        section_id: UUID,
        body: RubricClassifyRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.classify_rubric,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            body,
            storage,
            settings,
            section_id=section_id,
        )
        return result("classify", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/classification",
        name="score_rubric_classify",
        response_model=Result,
    )
    async def classify_item(
        task_id: UUID,
        rubric_id: UUID,
        item_id: UUID,
        body: RubricClassifyRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.classify_rubric,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            body,
            storage,
            settings,
            item_id=item_id,
        )
        return result("classify", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/decisions",
        name="score_rubric_section_decide",
        response_model=Result,
    )
    async def decide_section(
        task_id: UUID,
        rubric_id: UUID,
        section_id: UUID,
        body: RubricSectionDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.decide_section,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            section_id,
            body,
            storage,
            settings,
        )
        return result("section decide", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/decisions",
        name="score_rubric_item_decide",
        response_model=Result,
    )
    async def decide_item(
        task_id: UUID,
        rubric_id: UUID,
        item_id: UUID,
        body: RubricItemDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.decide_item, ctx[0], ctx[1], task_id, rubric_id, item_id, body, storage, settings
        )
        return result("item decide", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/coverage/{requirement_id}/decisions",
        name="score_rubric_coverage_decide",
        response_model=Result,
    )
    async def decide_coverage(
        task_id: UUID,
        rubric_id: UUID,
        requirement_id: UUID,
        body: RubricCoverageDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.decide_coverage,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            requirement_id,
            body,
            storage,
            settings,
        )
        return result("coverage decide", data)

    @router.post(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/decisions",
        name="score_rubric_decide",
        response_model=Result,
    )
    async def decide_set(
        task_id: UUID,
        rubric_id: UUID,
        body: RubricSetDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.decide_rubric, ctx[0], ctx[1], task_id, rubric_id, body, storage, settings
        )
        return result("decide", data)

    @router.get(
        "/tasks/{task_id}/score-rubrics/{rubric_id}/history",
        name="score_rubric_history",
        response_model=Result,
    )
    async def history(
        task_id: UUID,
        rubric_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await score.rubric_history(
            ctx[0], ctx[1], task_id, rubric_id, storage, settings, cursor=cursor, limit=limit
        )
        return result("history", data, items)

    return router
