"""Uploaded-bid submission metadata and explicit local preparation routes."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from app.api.bid_upload import receive
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.schemas import bid_review_findings as findings_contracts
from app.schemas import bid_review_privacy as privacy_contracts
from app.schemas import bid_review_report as report_contracts
from app.schemas import bid_review_run as run_contracts
from app.schemas.bid_review import (
    BidPreparePreview,
    BidPrepareRequest,
    BidReviewListQuery,
    BidSubmissionCreate,
)
from app.schemas.contracts import Cost, Result
from app.services import (
    bid_review,
    bid_review_findings,
    bid_review_privacy,
    bid_review_report,
    bid_review_run,
)


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

    @router.get("/bid-reviews/{review_id}/report", response_model=Result)
    async def report(
        review_id: UUID,
        section: report_contracts.ReportSectionKey = "overall",
        snapshot_id: UUID | None = None,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_report.show(
            ctx[0],
            ctx[1],
            review_id,
            settings,
            section=section,
            snapshot_id=snapshot_id,
            cursor=cursor,
            limit=limit,
        )
        response = result("review report show", data)
        response.items = items
        response.ok = data.completion != "partial"
        return response

    @router.get("/bid-reviews/{review_id}/reports", response_model=Result)
    async def report_history(
        review_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_report.history(
            ctx[0], ctx[1], review_id, settings, cursor=cursor, limit=limit
        )
        response = result("review report list", data)
        response.items = items
        return response

    @router.post("/bid-reviews/{review_id}/artifacts", response_model=Result)
    async def render_report(
        review_id: UUID,
        body: report_contracts.BidReportRenderRequest,
        ctx=Depends(context, scope="function"),
    ):
        data = await bid_review_report.submit(ctx[0], ctx[1], review_id, body, queue, settings)
        response = result("review report", data)
        if isinstance(data, report_contracts.BidReportRenderPreview):
            response.cost = data.budget.estimate
        return response

    @router.get("/bid-review-artifacts/{artifact_id}/download-link", response_model=Result)
    async def report_link(artifact_id: UUID, ctx=Depends(context, scope="function")):
        from app.api.common import signed_link
        from app.core.security import TokenSigner
        from app.services.versioned import audit

        row, snapshot = await bid_review_report.download_gate(ctx[0], ctx[1], artifact_id)
        path = f"/bid-review-artifacts/{artifact_id}/download"
        link = signed_link(
            TokenSigner.for_tokens(settings),
            path,
            "bid-review-report",
            ctx[1].org_id,
            id=row.id,
            sha256=row.sha256,
            actor_user_id=ctx[1].user_id,
        )
        audit(
            ctx[0],
            ctx[1],
            "bid_review.report_download_issued",
            row.id,
            {"task_id": str(row.task_id), "snapshot_id": str(snapshot.id), "sha256": row.sha256},
        )
        return result(
            "review report download",
            report_contracts.BidReportDownloadLink(
                **link, artifact=bid_review_report.artifact_view(row, snapshot)
            ),
        )

    @router.get("/bid-review-artifacts/{artifact_id}/download")
    async def report_bytes(
        artifact_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        from app.api.common import attachment, check_signature
        from app.core.security import TokenSigner

        row, _ = await bid_review_report.download_gate(ctx[0], ctx[1], artifact_id)
        check_signature(
            TokenSigner.for_tokens(settings),
            signature,
            "bid-review-report",
            ctx[1].org_id,
            id=row.id,
            sha256=row.sha256,
            actor_user_id=ctx[1].user_id,
        )
        content, descriptor = await bid_review_report.read_artifact(
            ctx[0], ctx[1], artifact_id, storage
        )
        media = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if descriptor.format == "docx"
            else "application/json"
        )
        response = attachment(
            content,
            media,
            "bid-review-report.docx" if descriptor.format == "docx" else "bid-review-report.json",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @router.get("/bid-reviews/{review_id}/findings", response_model=Result)
    async def findings(
        review_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        severity: findings_contracts.FindingSeverity | None = None,
        state: findings_contracts.FindingState | None = None,
        outcome: findings_contracts.FindingOutcome | None = None,
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_findings.list_findings(
            ctx[0],
            ctx[1],
            review_id,
            settings,
            cursor=cursor,
            limit=limit,
            severity=severity,
            state=state,
            outcome=outcome,
        )
        return result("review findings", data, items)

    @router.post("/bid-reviews/{review_id}/findings/{finding_id}/decisions", response_model=Result)
    async def decide_finding(
        review_id: UUID,
        finding_id: UUID,
        body: findings_contracts.BidReviewDecisionRequest,
        ctx=Depends(context, scope="function"),
    ):
        return result(
            "review decide",
            await bid_review_findings.decide(
                ctx[0],
                ctx[1],
                review_id,
                finding_id,
                body,
                settings,
            ),
        )

    @router.post(
        "/bid-reviews/{review_id}/findings/{finding_id}/classification", response_model=Result
    )
    async def classify_finding(
        review_id: UUID,
        finding_id: UUID,
        body: findings_contracts.BidReviewClassificationRequest,
        ctx=Depends(context, scope="function"),
    ):
        return result(
            "review classify",
            await bid_review_findings.decide(
                ctx[0],
                ctx[1],
                review_id,
                finding_id,
                body,
                settings,
                classify=True,
            ),
        )

    @router.get("/bid-reviews/{review_id}/findings/{finding_id}/decisions", response_model=Result)
    async def finding_history(
        review_id: UUID,
        finding_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_findings.history(
            ctx[0],
            ctx[1],
            review_id,
            finding_id,
            settings,
            cursor=cursor,
            limit=limit,
        )
        return result("review history", data, items)

    @router.get(
        "/bid-reviews/{review_id}/findings/{finding_id}/classification", response_model=Result
    )
    async def classification_history(
        review_id: UUID,
        finding_id: UUID,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await bid_review_findings.history(
            ctx[0],
            ctx[1],
            review_id,
            finding_id,
            settings,
            cursor=cursor,
            limit=limit,
            classification=True,
        )
        return result("review history", data, items)

    return router
