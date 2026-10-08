"""Uploaded-bid submission metadata and explicit local preparation routes."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from app.api.bid_upload import receive
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.schemas import bid_review_privacy as privacy_contracts
from app.schemas import bid_review_run as run_contracts
from app.schemas.bid_review import (
    BidPreparePreview,
    BidPrepareRequest,
    BidReviewListQuery,
    BidSubmissionCreate,
)
from app.schemas.contracts import Cost, Result
from app.services import bid_review, bid_review_privacy, bid_review_run


def create_router(context, settings, storage, queue):
    router = APIRouter()

    def result(command, data, items=None):
        return Result(
            ok=True,
            command=command,
            data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
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
            "review submission show", await bid_review.show(ctx[0], ctx[1], submission_id, settings)
        )

    @router.get("/bid-submissions/{submission_id}/signing-candidates", response_model=Result)
    async def signing_candidates(
        submission_id: UUID,
        cursor: int = Query(0, ge=0, le=2000),
        limit: int = Query(20, ge=1, le=50),
        ctx=Depends(context, scope="function"),
    ):
        from app.services import bid_signature_views

        root = await bid_review.required(ctx[0], submission_id)
        await bid_review.access(ctx[0], ctx[1], root.task_id)
        return result(
            "review signing-candidates",
            await bid_signature_views.candidates(
                ctx[0],
                ctx[1],
                submission_id,
                settings,
                cursor=cursor,
                limit=limit,
            ),
        )

    async def binding(ctx):
        try:
            _, value = await bid_review_run.resolve_binding(ctx[0], settings)
            return value
        except (ServiceError, ProviderFailure):
            return None

    @router.get("/bid-submissions/{submission_id}/redaction", response_model=Result)
    async def redaction(
        submission_id: UUID,
        cursor: int = Query(0, ge=0, le=1000),
        limit: int = Query(25, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        await bid_review_privacy.human_access(ctx[0], ctx[1], submission_id)
        return result(
            "review redaction",
            await bid_review_privacy.preview(
                ctx[0],
                ctx[1],
                submission_id,
                settings,
                await binding(ctx),
                cursor=cursor,
                limit=limit,
            ),
        )

    @router.post("/bid-submissions/{submission_id}/redaction/names", response_model=Result)
    async def names(
        submission_id: UUID,
        body: privacy_contracts.BidNameListRequest,
        ctx=Depends(context, scope="function"),
    ):
        return result(
            "review redaction names",
            await bid_review_privacy.add_names(ctx[0], ctx[1], submission_id, body, settings),
        )

    @router.get("/bid-submissions/{submission_id}/outbound-authorizations", response_model=Result)
    async def authorizations(
        submission_id: UUID,
        cursor: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        await bid_review_privacy.human_access(ctx[0], ctx[1], submission_id)
        data, items = await bid_review_privacy.list_authorizations(
            ctx[0], ctx[1], submission_id, settings, await binding(ctx), cursor=cursor, limit=limit
        )
        return result("review outbound list", data, items)

    @router.post("/bid-submissions/{submission_id}/outbound-authorizations", response_model=Result)
    async def authorize(
        submission_id: UUID,
        body: privacy_contracts.OutboundAuthorizationRequest,
        ctx=Depends(context, scope="function"),
    ):
        await bid_review_privacy.human_access(ctx[0], ctx[1], submission_id, write=True)
        return result(
            "review outbound authorize",
            await bid_review_privacy.authorize(
                ctx[0], ctx[1], submission_id, body, settings, await binding(ctx)
            ),
        )

    @router.post(
        "/bid-submissions/{submission_id}/outbound-authorizations/revoke", response_model=Result
    )
    async def revoke(
        submission_id: UUID,
        body: privacy_contracts.OutboundRevokeRequest,
        ctx=Depends(context, scope="function"),
    ):
        return result(
            "review outbound revoke",
            await bid_review_privacy.revoke(ctx[0], ctx[1], submission_id, body, settings),
        )

    @router.post("/tasks/{task_id}/bid-reviews", response_model=Result)
    async def run(
        task_id: UUID, body: run_contracts.BidReviewRequest, ctx=Depends(context, scope="function")
    ):
        data = await bid_review_run.submit(ctx[0], ctx[1], task_id, body, queue, settings)
        response = result("review run", data)
        if isinstance(data, run_contracts.BidReviewPreview):
            response.cost = data.budget.estimate
        return response

    @router.get("/tasks/{task_id}/bid-reviews", response_model=Result)
    async def list_reviews(
        task_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_run.list_runs(
            ctx[0], ctx[1], task_id, settings, cursor=cursor, limit=limit
        )
        return result("review list", data, items)

    @router.get("/bid-reviews/{review_id}", response_model=Result)
    async def show_review(
        review_id: UUID,
        section: Literal["obligations", "signing_requirements"] = "obligations",
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data = await bid_review_run.show(
            ctx[0], ctx[1], review_id, settings, section=section, cursor=cursor, limit=limit
        )
        response = result("review show", data)
        response.ok = data.run.completion != "partial"
        response.warnings = data.run.uncovered_codes
        return response

    return router
