"""Image-only, memory-bound ingest and authenticated signed previews."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError
from pydantic import ValidationError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job
from app.models.screenshots import PrototypeEvidenceDecision, ScreenshotAsset, ScreenshotRendition
from app.providers.base import ProviderFailure
from app.schemas.contracts import Result
from app.schemas.screenshot_contracts import (
    PrototypeDecisionBatch,
    PrototypeDecisionPreviewInput,
    PrototypeGenerateInput,
    ScreenshotAnalyzeInput,
    ScreenshotAnnotate,
    ScreenshotIngest,
    ScreenshotWithdraw,
)
from app.services import prototype_decisions, prototype_generation, screenshot_jobs, screenshots
from app.services import response_cards as cards
from app.services.resources import audit


async def memory_upload(request: Request, max_bytes: int = screenshots.MAX_BYTES):
    """Avoid UploadFile/SpooledTemporaryFile: no pre-release image may touch disk."""
    media, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if media != b"multipart/form-data" or not boundary or len(boundary) > 200:
        screenshots.fail("invalid_upload", "Expected bounded multipart PNG and input")
    fields: dict[str, bytes] = {}
    current = bytearray()
    headers: dict[bytes, bytes] = {}
    header_name, header_value = bytearray(), bytearray()
    state = {"name": "", "headers_size": 0, "ended": False}

    def data(chunk, start, end):
        limit = min(max_bytes, screenshots.MAX_BYTES) if state["name"] == "file" else 128 * 1024
        if len(current) + end - start > limit:
            screenshots.fail("upload_too_large", "Upload exceeds limit", 413)
        current.extend(chunk[start:end])

    def header_field(chunk, start, end):
        state["headers_size"] += end - start
        if state["headers_size"] > 8192:
            screenshots.fail("invalid_upload", "Multipart headers exceed limit")
        header_name.extend(chunk[start:end])

    def header_data(chunk, start, end):
        state["headers_size"] += end - start
        if state["headers_size"] > 8192:
            screenshots.fail("invalid_upload", "Multipart headers exceed limit")
        header_value.extend(chunk[start:end])

    def header_end():
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        _, values = parse_options_header(headers.get(b"content-disposition", b""))
        name = values.get(b"name", b"")
        if name not in {b"file", b"input"} or name.decode() in fields:
            screenshots.fail("invalid_upload", "Only one PNG and one input receipt are accepted")
        state["name"] = name.decode()
        headers.clear()

    def part_end():
        fields[state["name"]] = bytes(current)
        current.clear()

    def end():
        state["ended"] = True

    parser = MultipartParser(
        boundary,
        {
            "on_header_field": header_field,
            "on_header_value": header_data,
            "on_header_end": header_end,
            "on_headers_finished": headers_finished,
            "on_part_data": data,
            "on_part_end": part_end,
            "on_end": end,
        },
    )
    total = 0
    from python_multipart.exceptions import MultipartParseError

    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > min(max_bytes, screenshots.MAX_BYTES) + 256 * 1024:
                screenshots.fail("upload_too_large", "Upload exceeds limit", 413)
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        screenshots.fail("invalid_upload", "Invalid multipart body")
    if set(fields) != {"file", "input"} or not state["ended"]:
        screenshots.fail("invalid_upload", "Complete PNG and input receipt required")
    try:
        body = ScreenshotIngest.model_validate_json(fields["input"])
    except ValidationError:
        screenshots.fail("invalid_screenshot_input", "Invalid screenshot receipt")
    return body, fields["file"]


class ScreenshotRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except ProviderFailure as exc:
                raise ServiceError(
                    exc.code,
                    "Screenshot processing failed",
                    503 if exc.retryable else 400,
                    3 if exc.retryable else 4,
                ) from None

        return bounded


def create_router(context, db, storage, queue, settings, llm, resolve, processor=None):
    router = APIRouter(route_class=ScreenshotRoute)
    crypto = Secrets(settings.encryption_key.get_secret_value())

    def result(command, data=None, items=None):
        return Result(ok=True, command=command, data=data or {}, items=items or [])

    async def dispatch(session, actor, job):
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                raise ServiceError(
                    "queue_unavailable", "Job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id

    async def discard_staged(keys, org_id):
        from app.providers.screenshot_objects import discard

        for key in keys:
            # Resolve even an ambiguous commit before deleting an attempt-owned object.
            async with db.transaction(org_id) as check:
                referenced = await check.scalar(
                    select(ScreenshotRendition.id).where(ScreenshotRendition.storage_key == key)
                )
            if referenced is None:
                await discard(storage, org_id, key)

    @router.post("/tasks/{task_id}/screenshots", name="screenshot_add", response_model=Result)
    async def ingest(task_id: UUID, request: Request, ctx=Depends(context, scope="function")):
        actor = await cards.access(ctx[0], ctx[1], "screenshot:ingest")
        screenshots.ingest_human(actor)
        body, png = await memory_upload(request, settings.max_upload_bytes)
        staged: list[str] = []
        try:
            data = await screenshots.ingest(
                ctx[0],
                actor,
                task_id,
                body,
                png,
                storage,
                staged,
                max_bytes=settings.max_upload_bytes,
                crypto=crypto,
            )
            await ctx[0].commit()
        except (ServiceError, ProviderFailure, SQLAlchemyError, OSError):
            await ctx[0].rollback()
            await discard_staged(staged, actor.org_id)
            raise
        await discard_staged(staged, actor.org_id)
        return result("screenshot add", data)

    @router.get("/tasks/{task_id}/screenshots", name="screenshot_list", response_model=Result)
    async def listing(
        task_id: UUID,
        job: UUID,
        history: bool = False,
        cursor: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        actor = await cards.access(session, actor, "screenshot:read")
        await cards.extraction_scope(session, task_id, job)
        query = select(ScreenshotAsset).where(
            ScreenshotAsset.task_id == task_id, ScreenshotAsset.extraction_job_id == job
        )
        if cursor:
            anchor = await session.get(ScreenshotAsset, cursor)
            if anchor is None or anchor.task_id != task_id or anchor.extraction_job_id != job:
                raise not_found()
            query = query.where(
                tuple_(ScreenshotAsset.created_at, ScreenshotAsset.id)
                > tuple_(anchor.created_at, anchor.id)
            )
        rows = (
            await session.scalars(
                query.order_by(ScreenshotAsset.created_at, ScreenshotAsset.id).limit(101)
            )
        ).all()
        items = []
        for row in rows[:100]:
            view = await screenshots.asset_view(session, actor, row)
            if history or (view["active_selection"] and not view["withdrawn"]):
                items.append(view)
        return result(
            "screenshot list",
            {
                "task_id": str(task_id),
                "extraction_job_id": str(job),
                "history": history,
                "next_cursor": str(rows[99].id) if len(rows) > 100 else None,
            },
            items,
        )

    @router.get("/screenshots/{asset_id}", name="screenshot_show", response_model=Result)
    async def show(asset_id: UUID, ctx=Depends(context, scope="function")):
        return result("screenshot show", await screenshots.show(ctx[0], ctx[1], asset_id))

    @router.post(
        "/screenshots/{asset_id}/withdrawals", name="screenshot_withdraw", response_model=Result
    )
    async def withdraw(
        asset_id: UUID, body: ScreenshotWithdraw, ctx=Depends(context, scope="function")
    ):
        return result(
            "screenshot withdraw", await screenshots.withdraw(ctx[0], ctx[1], asset_id, body.reason)
        )

    @router.post(
        "/screenshot-renditions/{rendition_id}/preview-link",
        name="screenshot_preview",
        response_model=Result,
    )
    async def preview_link(rendition_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        asset, row = await screenshots.rendition_access(
            session, actor, rendition_id, storage=storage
        )
        token = crypto.issue(
            {
                "purpose": "screenshot-preview",
                "org_id": str(actor.org_id),
                "rendition_id": str(row.id),
                "sha256": row.image_sha256,
            },
            300,
        )
        audit(
            session, actor, "screenshot.preview.issue", row.id, {"image_sha256": row.image_sha256}
        )
        return result(
            "screenshot preview",
            {
                "url": f"/screenshot-renditions/{row.id}/content?signature={token}",
                "expires_in": 300,
                "rendition_id": str(row.id),
                "image": row.image,
            },
        )

    @router.get("/screenshot-renditions/{rendition_id}/content", name="screenshot_content")
    async def content(rendition_id: UUID, signature: str, ctx=Depends(context, scope="function")):
        session, actor = ctx
        asset, row = await screenshots.rendition_access(session, actor, rendition_id)
        try:
            signed = crypto.open(signature)
        except ServiceError:
            raise not_found() from None
        if any(
            signed.get(k) != v
            for k, v in {
                "purpose": "screenshot-preview",
                "org_id": str(actor.org_id),
                "rendition_id": str(row.id),
                "sha256": row.image_sha256,
            }.items()
        ):
            raise not_found()
        png = await screenshots.read_rendition(storage, asset, row)
        audit(session, actor, "screenshot.preview.read", row.id, {"image_sha256": row.image_sha256})
        return Response(
            png,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post(
        "/screenshots/{asset_id}/renditions", name="screenshot_annotate", response_model=Result
    )
    async def annotate(
        asset_id: UUID, body: ScreenshotAnnotate, ctx=Depends(context, scope="function")
    ):
        data, job = await screenshot_jobs.submit_render(
            ctx[0], ctx[1], asset_id, body, storage, settings.billing_currency
        )
        await dispatch(ctx[0], ctx[1], job)
        return result("screenshot annotate", data)

    @router.post(
        "/tasks/{task_id}/screenshot-analyses", name="screenshot_analyze", response_model=Result
    )
    async def analyze(
        task_id: UUID, body: ScreenshotAnalyzeInput, ctx=Depends(context, scope="function")
    ):
        provider = await resolve(ctx[0]) if resolve else llm
        data, job = await screenshot_jobs.submit_analysis(
            ctx[0], ctx[1], task_id, body, storage, provider, settings
        )
        await dispatch(ctx[0], ctx[1], job)
        return result("screenshot analyze", data)

    @router.post("/tasks/{task_id}/prototype-generations", name="ui_mock", response_model=Result)
    async def generate_prototype(
        task_id: UUID, body: PrototypeGenerateInput, ctx=Depends(context, scope="function")
    ):
        from app.services.sandbox import browser_for

        provider = await resolve(ctx[0]) if resolve else llm
        data, job = await prototype_generation.submit(
            ctx[0], ctx[1], task_id, body, provider, settings, browser_for(processor)
        )
        await dispatch(ctx[0], ctx[1], job)
        return result("ui mock", data)

    @router.get("/prototype-runs/{prototype_id}", name="prototype_show", response_model=Result)
    async def show_prototype(prototype_id: UUID, ctx=Depends(context, scope="function")):
        row = await prototype_generation.show(ctx[0], ctx[1], prototype_id)
        return result("prototype show", prototype_generation.view(row))

    @router.get("/prototype-runs/{prototype_id}/source", name="prototype_source")
    async def prototype_source(prototype_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        row, png = await prototype_generation.source(session, actor, prototype_id, storage)
        audit(
            session, actor, "screenshot.prototype.read", row.id, {"sha256": row.source_image_sha256}
        )
        return Response(
            png,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get(
        "/screenshot-analyses/{analysis_id}/suggestions",
        name="screenshot_suggestions",
        response_model=Result,
    )
    async def suggestions(
        analysis_id: UUID, cursor: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        data, items = await screenshot_jobs.suggestions(ctx[0], ctx[1], analysis_id, cursor)
        return result("screenshot suggestions", data, items)

    @router.post(
        "/tasks/{task_id}/prototype-decisions/preview",
        name="prototype_decisions_preview",
        response_model=Result,
    )
    async def decision_preview(
        task_id: UUID, body: PrototypeDecisionPreviewInput, ctx=Depends(context, scope="function")
    ):
        return result(
            "screenshot prototype-decisions preview",
            await prototype_decisions.preview(ctx[0], ctx[1], task_id, body, storage),
        )

    @router.post(
        "/tasks/{task_id}/prototype-decisions",
        name="prototype_decisions_apply",
        response_model=Result,
    )
    async def decision_apply(
        task_id: UUID, body: PrototypeDecisionBatch, ctx=Depends(context, scope="function")
    ):
        return result(
            "screenshot prototype-decisions apply",
            await prototype_decisions.apply(ctx[0], ctx[1], task_id, body, storage),
        )

    @router.get(
        "/tasks/{task_id}/prototype-decisions",
        name="prototype_decisions_list",
        response_model=Result,
    )
    async def decision_list(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        actor = await cards.access(session, actor, "card:read")
        actor.require("screenshot:read")
        await cards.extraction_scope(session, task_id, job)
        rows = (
            await session.scalars(
                select(PrototypeEvidenceDecision)
                .where(
                    PrototypeEvidenceDecision.task_id == task_id,
                    PrototypeEvidenceDecision.extraction_job_id == job,
                )
                .order_by(PrototypeEvidenceDecision.created_at, PrototypeEvidenceDecision.id)
            )
        ).all()
        return result(
            "screenshot prototype-decisions list",
            {"task_id": str(task_id), "extraction_job_id": str(job)},
            [await prototype_decisions.decision_view(session, actor, row) for row in rows],
        )

    return router
