"""Rubric generation and task-bound human review routes."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError
from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.entities import Job
from app.models.score import ScoreRubricSet
from app.schemas.console_assessments import (
    AssessmentHistoryQuery,
    RubricPageRequest,
    ScorePageRequest,
)
from app.schemas.contracts import Result
from app.schemas.score_contracts import (
    RubricClassifyRequest,
    RubricCoverageDecisionRequest,
    RubricGenerateRequest,
    RubricItemDecisionRequest,
    RubricReviseRequest,
    RubricSectionDecisionRequest,
    RubricSetDecisionRequest,
    ScoreRequest,
)
from app.services import (
    assessment_rubrics,
    assessment_scores,
    score,
    score_execution,
    score_generation,
)
from app.services.assessment_bounds import bounded_result, query_model
from app.services.versioned import audit


def create_router(context, db, storage, queue, settings) -> APIRouter:
    router = APIRouter()

    def result(command, data=None, items=None, *, console=False, partial=False):
        payload = Result(
            ok=not partial,
            command=f"score rubric {command}",
            data=data if data is not None else {},
            items=items if items is not None else [],
        )
        return bounded_result(payload) if console else payload

    def score_result(
        command, data=None, items=None, warnings=None, *, partial=False, console=False
    ):
        payload = Result(
            ok=not partial,
            command=f"score {command}",
            data=data if data is not None else {},
            items=items if items is not None else [],
            warnings=warnings if warnings is not None else [],
        )
        return bounded_result(payload) if console else payload

    async def rubric_console_result(command, session, data, items=None):
        identifiers = [entry["id"] for entry in items or [] if "id" in entry]
        identifier = data.get("parent_id") or data.get("id") or data.get("rubric_id")
        if identifier is not None:
            identifiers.append(identifier)
        jobs = (
            await session.scalars(
                select(Job.result)
                .join(ScoreRubricSet, ScoreRubricSet.job_id == Job.id)
                .where(ScoreRubricSet.id.in_([UUID(str(value)) for value in identifiers]))
            )
            if identifiers
            else []
        )
        partial = any(value.get("completion") == "partial" for value in jobs)
        return result(command, data, items, console=True, partial=partial)

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

    async def invoke_score(operation, session, actor, *args, **kwargs):
        try:
            return await operation(session, actor, *args, **kwargs)
        except ServiceError as error:
            details = getattr(error, "score_audit", None)
            if details is not None:
                await session.rollback()
                details = dict(details)
                object_id = UUID(details.pop("object_id"))
                action = details.pop("action", "score.failed")
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

    async def execute(task_id, body, ctx):
        session, actor = ctx
        data, job = await invoke_score(
            score_execution.submit_score,
            session,
            actor,
            task_id,
            body,
            settings,
            storage,
        )
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError):
                response = Result(
                    ok=False,
                    command="score run",
                    data={
                        "error": {
                            "code": "queue_unavailable",
                            "message": "Score job saved; repeat request to schedule it",
                            "exit_code": 3,
                        },
                        "job_id": str(job.id),
                    },
                )
                return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        warnings = data.get("limitations", []) if body.dry_run else []
        return score_result("run", data, warnings=warnings)

    @router.post(
        "/tasks/{task_id}/scores/preview",
        name="score_run",
        response_model=Result,
    )
    async def score_preview(
        task_id: UUID, body: ScoreRequest, ctx=Depends(context, scope="function")
    ):
        if not body.dry_run:
            raise ServiceError("invalid_input", "Score preview requires dry_run=true", 422, 2)
        return await execute(task_id, body, ctx)

    @router.post("/tasks/{task_id}/scores", name="score_run", response_model=Result)
    async def score_submit(
        task_id: UUID, body: ScoreRequest, ctx=Depends(context, scope="function")
    ):
        if body.dry_run:
            raise ServiceError("invalid_input", "Use the score preview route for dry-run", 422, 2)
        return await execute(task_id, body, ctx)

    @router.get("/tasks/{task_id}/scores", name="score_list", response_model=Result)
    async def score_list(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        view: Literal["console"] | None = None,
        extraction_job_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        if view == "console":
            query = query_model(
                AssessmentHistoryQuery,
                cursor=cursor,
                limit=limit,
                extraction_job_id=extraction_job_id,
            )
            data, rows = await assessment_scores.history(ctx[0], ctx[1], task_id, query, settings)
            return score_result(
                "list",
                data.model_dump(mode="json"),
                [row.model_dump(mode="json") for row in rows],
                partial=any(
                    row.report.completion == "partial"
                    or row.unassessable_items > 0
                    or row.total_status != "estimated"
                    for row in rows
                ),
                console=True,
            )
        if extraction_job_id is not None:
            raise ServiceError("invalid_input", "Extraction filter requires view=console", 422, 2)
        data, items = await score_execution.list_scores(
            ctx[0], ctx[1], task_id, settings, storage, cursor=cursor, limit=limit
        )
        warnings = (
            ["score_input_changed"]
            if any(item.get("validity") == "stale" for item in items)
            else []
        )
        return score_result("list", data, items, warnings)

    @router.get(
        "/tasks/{task_id}/scores/{report_id}",
        name="score_show",
        response_model=Result,
    )
    async def score_show(
        task_id: UUID,
        report_id: UUID,
        view: Literal["console"] | None = None,
        part: Literal["summary", "sections", "items", "notices"] | None = None,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        section_key: str | None = None,
        outcome: Literal["assessed", "unassessable"] | None = None,
        requirement_id: UUID | None = None,
        entry_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        if view == "console":
            if part is None or part == "summary":
                if any(
                    value is not None
                    for value in (cursor, section_key, outcome, requirement_id, entry_id)
                ):
                    raise ServiceError(
                        "invalid_input", "Summary does not accept page filters", 422, 2
                    )
                summary = await assessment_scores.summary(
                    ctx[0], ctx[1], task_id, report_id, settings
                )
                return score_result(
                    "show",
                    summary.model_dump(mode="json"),
                    warnings=summary.report.invalidation_codes,
                    partial=summary.report.completion == "partial"
                    or summary.unassessable_items > 0
                    or summary.total_status != "estimated",
                    console=True,
                )
            query = query_model(
                ScorePageRequest,
                part=part,
                cursor=cursor,
                limit=limit,
                section_key=section_key,
                outcome=outcome,
                requirement_id=requirement_id,
                entry_id=entry_id,
            )
            page = await assessment_scores.page(ctx[0], ctx[1], task_id, report_id, query, settings)
            run = await ctx[0].get(assessment_scores.ScoreReport, report_id)
            partial = (
                run.completion == "partial"
                or run.summary.get("unassessable_items", 0) > 0
                or run.summary.get("total_status") != "estimated"
            )
            return score_result(
                "show",
                page.data.model_dump(mode="json"),
                [row.model_dump(mode="json") for row in page.items],
                warnings=["score_input_changed"] if page.data.validity == "stale" else [],
                partial=partial,
                console=True,
            )
        if part is not None or any(
            value is not None for value in (cursor, section_key, outcome, requirement_id, entry_id)
        ):
            raise ServiceError("invalid_input", "Projection options require view=console", 422, 2)
        data = await score_execution.show_score(
            ctx[0], ctx[1], task_id, report_id, settings, storage
        )
        report = data["report"]
        warnings = list(report.get("limitations", []))
        if report.get("validity") == "stale" and "score_input_changed" not in warnings:
            warnings.insert(0, "score_input_changed")
        partial = (
            report.get("completion") == "partial"
            or report.get("unassessable_items", 0) > 0
            or report.get("total_status") != "estimated"
        )
        return score_result("show", data, warnings=warnings, partial=partial)

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
        view: Literal["console"] | None = None,
        extraction_job_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        if view == "console":
            query = query_model(
                AssessmentHistoryQuery,
                cursor=cursor,
                limit=limit,
                extraction_job_id=extraction_job_id,
            )
            data, rows = await assessment_rubrics.history(ctx[0], ctx[1], task_id, query, settings)
            return await rubric_console_result(
                "list",
                ctx[0],
                data.model_dump(mode="json"),
                [row.model_dump(mode="json") for row in rows],
            )
        if extraction_job_id is not None:
            raise ServiceError("invalid_input", "Extraction filter requires view=console", 422, 2)
        data, items = await score.list_rubrics(
            ctx[0], ctx[1], task_id, storage, settings, cursor=cursor, limit=limit
        )
        return result("list", data, items)

    @router.get(
        "/tasks/{task_id}/score-rubrics/{rubric_id}",
        name="score_rubric_show",
        response_model=Result,
    )
    async def show(
        task_id: UUID,
        rubric_id: UUID,
        view: Literal["console"] | None = None,
        part: Literal["summary", "sections", "items", "coverage", "blockers", "replacement"]
        | None = None,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        state: Literal["candidate", "confirmed", "rejected"] | None = None,
        domain: Literal["commercial", "technical", "unclassified"] | None = None,
        section_id: UUID | None = None,
        requirement_id: UUID | None = None,
        entry_id: UUID | None = None,
        group_id: str | None = None,
        ctx=Depends(context, scope="function"),
    ):
        if view == "console":
            if part is None or part in {"summary", "replacement"}:
                if any(
                    value is not None
                    for value in (
                        cursor,
                        state,
                        domain,
                        section_id,
                        requirement_id,
                        entry_id,
                        group_id,
                    )
                ):
                    raise ServiceError(
                        "invalid_input", "Summary/replacement do not accept page filters", 422, 2
                    )
                data = await (
                    assessment_rubrics.replacement(ctx[0], ctx[1], task_id, rubric_id)
                    if part == "replacement"
                    else assessment_rubrics.summary(ctx[0], ctx[1], task_id, rubric_id)
                )
                return await rubric_console_result("show", ctx[0], data.model_dump(mode="json"))
            query = query_model(
                RubricPageRequest,
                part=part,
                cursor=cursor,
                limit=limit,
                state=state,
                domain=domain,
                section_id=section_id,
                requirement_id=requirement_id,
                entry_id=entry_id,
                group_id=group_id,
            )
            page = await assessment_rubrics.page(
                ctx[0], ctx[1], task_id, rubric_id, query, settings
            )
            return await rubric_console_result(
                "show",
                ctx[0],
                page.data.model_dump(mode="json"),
                [row.model_dump(mode="json") for row in page.items],
            )
        if part is not None or any(
            value is not None
            for value in (cursor, state, domain, section_id, requirement_id, entry_id, group_id)
        ):
            raise ServiceError("invalid_input", "Projection options require view=console", 422, 2)
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
        view: Literal["console"] | None = None,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.revise_rubric,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            body,
            storage,
            settings,
            console=view == "console",
        )
        return result("revise", data, console=view == "console")

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
        view: Literal["console"] | None = None,
        ctx=Depends(context, scope="function"),
    ):
        data = await invoke(
            score.decide_rubric,
            ctx[0],
            ctx[1],
            task_id,
            rubric_id,
            body,
            storage,
            settings,
            console=view == "console",
        )
        return result("decide", data, console=view == "console")

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
