from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.core.errors import not_found
from app.models.entities import Document, Job, Task
from app.schemas.console_assessments import AssessmentJobQuery, CitationRequest
from app.schemas.contracts import Result
from app.services import assessment_reads
from app.services.assessment_bounds import bounded_result, query_model
from app.services.task_workflow import access as task_access


def _serial(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return jsonable_encoder({field: getattr(row, field) for field in fields})


def _result(command: str, data=None, items=None) -> dict[str, Any]:
    return Result(ok=True, command=command, data=data or {}, items=items or []).model_dump(
        mode="json"
    )


def create_router(
    context: Callable[..., Any], storage: Any = None, settings: Any = None
) -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/{task_id}", name="task_get", response_model=Result)
    async def task_get(task_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        await task_access(session, actor, task_id)
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
        return _result(
            "task get",
            _serial(
                task,
                (
                    "id",
                    "org_id",
                    "name",
                    "tender_number",
                    "deadline",
                    "budget_usd",
                    "created_by",
                    "created_at",
                    "model_redaction_enabled",
                    "model_redaction_revision",
                    "model_redaction_by",
                ),
            ),
        )

    @router.get("/tasks/{task_id}/documents", name="task_document_list", response_model=Result)
    async def task_document_list(task_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        await task_access(session, actor, task_id)
        if await session.get(Task, task_id) is None:
            raise not_found()
        documents = (
            await session.scalars(
                select(Document)
                .where(Document.task_id == task_id)
                .order_by(Document.created_at, Document.id)
            )
        ).all()
        return _result(
            "task document list",
            {"task_id": str(task_id)},
            [
                _serial(
                    document,
                    (
                        "id",
                        "task_id",
                        "name",
                        "sha256",
                        "media_type",
                        "page_count",
                        "status",
                        "citation_mode",
                        "created_at",
                    ),
                )
                for document in documents
            ],
        )

    @router.get("/tasks/{task_id}/jobs", name="task_job_list", response_model=Result)
    async def task_job_list(
        task_id: UUID,
        kind: Literal["parse", "check", "score_rubric", "score"],
        extraction_job_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        document: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        await task_access(session, actor, task_id)
        if kind != "parse":
            if document is not None:
                from app.core.errors import ServiceError

                raise ServiceError("invalid_input", "Assessment jobs use extraction_job_id", 400, 2)
            page = await assessment_reads.jobs(
                session,
                actor,
                task_id,
                AssessmentJobQuery(
                    kind=kind, extraction_job_id=extraction_job_id, cursor=cursor, limit=limit
                ),
                storage,
                settings,
            )
            return bounded_result(
                Result(
                    ok=True,
                    command="assessment jobs",
                    data=page.data.model_dump(mode="json"),
                    items=[row.model_dump(mode="json") for row in page.items],
                )
            )
        actor.require("job:read")
        if await session.get(Task, task_id) is None:
            raise not_found()
        if document is not None:
            selected_document = await session.get(Document, document)
            if selected_document is None or selected_document.task_id != task_id:
                raise not_found()
        query = select(Job).where(Job.task_id == task_id, Job.kind == kind)
        if document is not None:
            query = query.where(Job.document_id == document)
        jobs = (await session.scalars(query.order_by(Job.created_at.desc(), Job.id.desc()))).all()
        return _result(
            "task job list",
            {
                "task_id": str(task_id),
                "kind": kind,
                "document_id": str(document) if document is not None else None,
            },
            [
                _serial(
                    job,
                    (
                        "id",
                        "task_id",
                        "document_id",
                        "kind",
                        "status",
                        "result",
                        "error",
                        "attempts",
                        "reasoning",
                        "created_at",
                        "finished_at",
                    ),
                )
                for job in jobs
            ],
        )

    @router.get(
        "/tasks/{task_id}/assessment-inputs", name="assessment_inputs", response_model=Result
    )
    async def assessment_inputs(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        data = await assessment_reads.inputs(ctx[0], ctx[1], task_id, job, storage, settings)
        return bounded_result(
            Result(ok=True, command="assessment inputs", data=data.model_dump(mode="json"))
        )

    @router.get(
        "/tasks/{task_id}/assessment-citation", name="assessment_citation", response_model=Result
    )
    async def assessment_citation(
        task_id: UUID,
        parent_kind: Literal["check", "rubric", "score"],
        parent_id: UUID,
        part: Literal["finding", "coverage", "rubric_section", "rubric_item", "score_item"],
        entry_id: UUID,
        origin: Literal["source", "citations"] = "source",
        citation_index: int = Query(0, ge=0, le=1000),
        text: Literal["quote", "context"] = "context",
        offset: int = Query(0, ge=0),
        limit: int = Query(4000, ge=1, le=8000),
        ctx=Depends(context, scope="function"),
    ):
        query = query_model(
            CitationRequest,
            parent_kind=parent_kind,
            parent_id=parent_id,
            part=part,
            entry_id=entry_id,
            origin=origin,
            citation_index=citation_index,
            text=text,
            offset=offset,
            limit=limit,
        )
        data = await assessment_reads.citation(ctx[0], ctx[1], task_id, query, storage, settings)
        return bounded_result(
            Result(ok=True, command="assessment citation", data=data.model_dump(mode="json"))
        )

    return router
