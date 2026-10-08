"""Uploaded-bid submission metadata and explicit local preparation routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from app.api.bid_upload import receive
from app.core.errors import ServiceError
from app.schemas.bid_review import (
    BidPreparePreview,
    BidPrepareRequest,
    BidReviewListQuery,
    BidSubmissionCreate,
)
from app.schemas.contracts import Cost, Result
from app.services import bid_review


def create_router(context, settings, storage, queue):
    router = APIRouter()

    def result(command, data, items=None):
        return Result(
            ok=True,
            command=command,
            data=data.model_dump(mode="json"),
            items=[item.model_dump(mode="json") for item in items or []],
            cost=Cost(billing_currency=settings.billing_currency),
        )

    @router.post("/tasks/{task_id}/bid-submissions", name="review_upload", response_model=Result)
    async def upload(task_id: UUID, request: Request, ctx=Depends(context, scope="function")):
        # Reject unauthorized identities before retaining multipart bytes.
        await bid_review.access(
            ctx[0], ctx[1], task_id, "bid-review:upload", write=True, lock=False
        )
        metadata, files = await receive(request, settings.max_upload_bytes)
        try:
            body = BidSubmissionCreate.model_validate_json(metadata)
        except ValidationError:
            raise ServiceError("invalid_input", "Invalid submission metadata", 422, 2) from None
        data = await bid_review.upload(ctx[0], ctx[1], task_id, body, files, storage, settings)
        return result("review upload", data)

    @router.post(
        "/tasks/{task_id}/bid-submissions/{submission_id}/prepare",
        name="review_prepare",
        response_model=Result,
    )
    async def prepare(
        task_id: UUID,
        submission_id: UUID,
        body: BidPrepareRequest,
        ctx=Depends(context, scope="function"),
    ):
        if body.submission_id != submission_id:
            raise ServiceError(
                "invalid_input", "Preparation submission does not match route", 422, 2
            )
        data = await bid_review.prepare(ctx[0], ctx[1], task_id, body, queue, settings)
        response = result("review prepare", data)
        if isinstance(data, BidPreparePreview):
            response.cost = data.budget.estimate
        return response

    @router.get(
        "/tasks/{task_id}/bid-submissions", name="review_submission_list", response_model=Result
    )
    async def list_submissions(
        task_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review.list_submissions(
            ctx[0], ctx[1], task_id, BidReviewListQuery(cursor=cursor, limit=limit), settings
        )
        return result("review submission list", data, items)

    @router.get(
        "/bid-submissions/{submission_id}", name="review_submission_show", response_model=Result
    )
    async def show(submission_id: UUID, ctx=Depends(context, scope="function")):
        return result(
            "review submission show", await bid_review.show(ctx[0], ctx[1], submission_id)
        )

    return router
