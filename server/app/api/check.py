"""Confirmed-draft check routes and durable queue dispatch."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError

from app.models.entities import Job
from app.schemas.check_contracts import CheckRequest, FindingDecisionRequest
from app.schemas.contracts import Result
from app.services import check


def create_router(context, db, storage, queue, settings) -> APIRouter:
    router = APIRouter()

    def result(command, data=None, items=None, warnings=None, *, partial=False):
        return Result(
            ok=not partial,
            command=command,
            data=data if data is not None else {},
            items=items if items is not None else [],
            warnings=warnings if warnings is not None else [],
        )

    async def dispatch(session, actor, job: Job | None) -> JSONResponse | None:
        if job is None or job.status != "queued" or job.queue_id is not None:
            return None
        # The submission and its authorization snapshot are durable before queue I/O.
        await session.commit()
        try:
            queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
        except (OSError, ConnectorException, OperationalError):
            body = Result(
                ok=False,
                command="check run",
                data={
                    "error": {
                        "code": "queue_unavailable",
                        "message": "Check job saved; repeat request to schedule it",
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
        return None

    @router.post("/tasks/{task_id}/checks", name="check_run", response_model=Result)
    async def check_run(task_id: UUID, body: CheckRequest, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data, job = await check.submit_check(session, actor, task_id, body, storage, settings)
        dispatch_error = await dispatch(session, actor, job)
        if dispatch_error is not None:
            return dispatch_error
        warnings = data.get("limitations", []) if body.dry_run else []
        return result("check run", data, warnings=warnings)

    @router.get("/tasks/{task_id}/checks", name="check_list", response_model=Result)
    async def check_list(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await check.list_checks(
            ctx[0], ctx[1], task_id, storage, settings, cursor=cursor, limit=limit
        )
        warnings = (
            ["check_input_changed"]
            if any(item.get("validity") == "stale" for item in items)
            else []
        )
        return result("check list", data, items, warnings)

    @router.get("/checks/{report_id}", name="check_show", response_model=Result)
    async def check_show(report_id: UUID, ctx=Depends(context, scope="function")):
        data, items = await check.show_check(ctx[0], ctx[1], report_id, storage, settings)
        report = data["report"]
        warnings = []
        if report["validity"] == "stale":
            warnings.append("check_input_changed")
        warnings.extend(value for value in report.get("limitations", []) if value not in warnings)
        return result(
            "check show",
            data,
            items,
            warnings,
            partial=report["completion"] == "partial",
        )

    @router.post(
        "/checks/{report_id}/findings/{finding_id}/decisions",
        name="check_decide",
        response_model=Result,
    )
    async def check_decide(
        report_id: UUID,
        finding_id: UUID,
        body: FindingDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await check.decide_finding(
            ctx[0], ctx[1], report_id, finding_id, body, storage, settings
        )
        return result("check decide", data)

    @router.get(
        "/checks/{report_id}/findings/{finding_id}/decisions",
        name="check_history",
        response_model=Result,
    )
    async def check_history(
        report_id: UUID,
        finding_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await check.decision_history(
            ctx[0],
            ctx[1],
            report_id,
            finding_id,
            storage,
            settings,
            cursor=cursor,
            limit=limit,
        )
        return result("check history", data, items)

    return router
