"""Independent requirement review and manual-entry endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.errors import ServiceError
from app.schemas.contracts import Cost, Result
from app.schemas.requirement_confirmation import (
    ManualEntryCreate,
    ManualRequirementInput,
    PageQuery,
    RequirementConfirmBatch,
    RequirementDecision,
    ReviewPageQuery,
)
from app.services import requirement_confirmation as service


def create_router(context, settings):
    async def v4_only(request: Request):
        if getattr(request.state, "contract_version", "3.0") != "4.0":
            raise ServiceError("not_found", "Not found", 404, 4)

    router = APIRouter(dependencies=[Depends(v4_only)])

    def result(command, data=None, items=None):
        value = Result(
            ok=True,
            command=command,
            data=data or {},
            items=items or [],
            cost=Cost(billing_currency=settings.billing_currency),
        )
        if len(value.model_dump_json().encode()) > 512 * 1024:
            raise ServiceError("legacy_item_too_large", "Review response exceeds 512 KiB", 422, 2)
        return value

    def parsed_query(request, model):
        try:
            return model.model_validate(dict(request.query_params), strict=False)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from None

    async def review_query(request: Request):
        return parsed_query(request, ReviewPageQuery)

    async def page_query(request: Request):
        return parsed_query(request, PageQuery)

    async def manual_size(request: Request):
        if len(await request.body()) > 256 * 1024:
            raise ServiceError("input_too_large", "Manual request exceeds 256 KiB", 413, 2)

    @router.get("/tasks/{task_id}/extractions/{job_id}/requirement-reviews", response_model=Result)
    async def reviews(
        task_id: UUID,
        job_id: UUID,
        query: ReviewPageQuery = Depends(review_query),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await service.list_reviews(
            ctx[0], ctx[1], task_id, job_id, query, settings=settings
        )
        return result(
            "req review-list",
            data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items],
        )

    @router.get("/requirements/{requirement_id}/review", response_model=Result)
    async def show(requirement_id: UUID, ctx=Depends(context, scope="function")):
        data = await service.show(ctx[0], ctx[1], requirement_id, settings=settings)
        return result("req show", data.model_dump(mode="json"))

    @router.get("/requirements/{requirement_id}/review-history", response_model=Result)
    async def history(
        requirement_id: UUID,
        query: PageQuery = Depends(page_query),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await service.history(
            ctx[0], ctx[1], requirement_id, query, settings=settings
        )
        return result(
            "req review-history",
            data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items],
        )

    @router.get("/tasks/{task_id}/extractions/{job_id}/rejected-items", response_model=Result)
    async def rejected(
        task_id: UUID,
        job_id: UUID,
        query: PageQuery = Depends(page_query),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await service.rejected(
            ctx[0], ctx[1], task_id, job_id, query, settings=settings
        )
        return result(
            "req rejected",
            data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items],
        )

    @router.post(
        "/tasks/{task_id}/requirements/manual-preview",
        response_model=Result,
        dependencies=[Depends(manual_size)],
    )
    async def preview(
        task_id: UUID, body: ManualRequirementInput, ctx=Depends(context, scope="function")
    ):
        data = await service.preview_manual(ctx[0], ctx[1], task_id, body, settings=settings)
        return result("req add", data.model_dump(mode="json"))

    @router.post(
        "/tasks/{task_id}/requirements/manual",
        response_model=Result,
        status_code=201,
        dependencies=[Depends(manual_size)],
    )
    async def add(
        task_id: UUID,
        body: ManualEntryCreate,
        response: Response,
        ctx=Depends(context, scope="function"),
    ):
        data = await service.add_manual(ctx[0], ctx[1], task_id, body, settings=settings)
        response.status_code = 200 if data.replayed else 201
        return result("req add", data.model_dump(mode="json"))

    @router.post("/requirements/{requirement_id}/review-decisions", response_model=Result)
    async def decide(
        requirement_id: UUID, body: RequirementDecision, ctx=Depends(context, scope="function")
    ):
        data = await service.decide(ctx[0], ctx[1], requirement_id, body, settings=settings)
        return result(f"req {body.action}", data.model_dump(mode="json"))

    @router.post(
        "/tasks/{task_id}/extractions/{job_id}/requirement-confirmations", response_model=Result
    )
    async def batch(
        task_id: UUID,
        job_id: UUID,
        body: RequirementConfirmBatch,
        ctx=Depends(context, scope="function"),
    ):
        data, items = await service.confirm_batch(
            ctx[0], ctx[1], task_id, job_id, body, settings=settings
        )
        return result(
            "req confirm-batch",
            data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items],
        )

    return router
