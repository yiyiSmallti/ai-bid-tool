"""Authenticated cloud annotation routes; source/render bytes never enter CLI plans."""

import json
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import TypeAdapter, ValidationError

from app.core.errors import ServiceError, not_found
from app.models.screenshots import ScreenshotAsset, ScreenshotRendition
from app.providers.base import ProviderFailure
from app.schemas import annotation_contracts as schema
from app.schemas.contracts import Cost, Result
from app.services import annotations, screenshots
from app.services.versioned import audit

BODY = TypeAdapter(schema.AnnotationPreflightRequest | schema.AnnotationSubmit)


async def bounded_input(request, adapter=BODY):
    if hasattr(request.state, "annotation_input"):
        return request.state.annotation_input
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > schema.HTTP_INPUT_LIMIT:
            raise ServiceError("annotation_input_limit", "Annotation JSON exceeds 128 KiB", 413, 2)
    try:
        raw = json.loads(content)
        if isinstance(raw, dict) and isinstance(raw.get("input"), dict):
            source = raw["input"].get("source")
            if isinstance(source, dict) and source.get("kind") == "attachment_page":
                raise ServiceError(
                    "annotation_adapter_not_enabled",
                    "Attachment annotation adapter is not enabled",
                    409,
                    2,
                )
        return adapter.validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise ServiceError(
            "invalid_annotation_input",
            "Invalid annotation input; unknown fields and noninteger coordinates are forbidden",
            422,
            2,
        ) from exc


def create_router(context, storage, queue, settings, tokens, processor):
    from app.providers.storage import annotation_storage

    storage = annotation_storage(storage, settings)
    router = APIRouter()

    def result(command, data=None, items=None, started=None):
        cost = Cost(billing_currency=settings.billing_currency, basis="zero")
        return Result(
            ok=True,
            command=command,
            data=data.model_dump(mode="json")
            if data is not None and hasattr(data, "model_dump")
            else data or {},
            items=[
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in items or []
            ],
            cost=cost,
            duration_ms=max(1, int((monotonic() - started) * 1000)) if started else 1,
        )

    @router.post("/tasks/{task_id}/annotations", name="evidence_stamp", response_model=Result)
    async def stamp(task_id: UUID, request: Request, ctx=Depends(context, scope="function")):
        started = monotonic()
        session, actor = ctx
        # Resolve task visibility before validating user-controlled source IDs.
        await annotations.access(session, actor, task_id, create=True)
        body = await bounded_input(request)
        renderer = annotations.renderer_for(processor)
        try:
            if isinstance(body, schema.AnnotationPreflightRequest):
                data = await annotations.preflight(
                    session, actor, task_id, body, storage, settings, renderer
                )
            else:
                data = await annotations.submit(
                    session, actor, task_id, body, storage, queue, settings, renderer
                )
        except ProviderFailure as exc:
            configuration = exc.code in {
                "annotation_renderer_unavailable",
                "unsupported_protocol",
                "annotation_sandbox_unavailable",
            }
            raise ServiceError(
                exc.code,
                str(exc),
                413
                if exc.code == "output_limit_exceeded"
                else 503
                if configuration or exc.retryable
                else 422,
                2 if exc.code == "output_limit_exceeded" else 3 if exc.retryable else 4,
            ) from exc
        return result("evidence stamp", data, started=started)

    @router.get(
        "/tasks/{task_id}/annotations", name="evidence_annotation_list", response_model=Result
    )
    async def listing(
        task_id: UUID,
        card_id: UUID | None = None,
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await annotations.list_candidates(
            ctx[0],
            ctx[1],
            task_id,
            schema.AnnotationListQuery(card_id=card_id, cursor=cursor, limit=limit),
            settings,
        )
        return result("evidence annotation list", data, items)

    @router.get(
        "/annotations/{annotation_id}", name="evidence_annotation_show", response_model=Result
    )
    async def show(annotation_id: UUID, ctx=Depends(context, scope="function")):
        return result(
            "evidence annotation show", await annotations.show(ctx[0], ctx[1], annotation_id)
        )

    async def preview_link(session, actor, row, *, release=False):
        material = await annotations.material_row(
            session, actor, row.annotation_id if release else row.id
        )
        if release:
            await annotations.validate_release(session, actor, row, storage=storage)
        rendition = await session.get(ScreenshotRendition, row.rendition_id)
        asset = await session.get(ScreenshotAsset, row.asset_id)
        if rendition is None or asset is None:
            raise not_found()
        await screenshots.read_rendition(storage, asset, rendition)
        kind = "release" if release else "candidate"
        signature = tokens.issue(
            {
                "purpose": "annotation-preview",
                "org_id": str(actor.org_id),
                "object_id": str(row.id),
                "rendition_id": str(row.rendition_id),
                "sha256": rendition.image_sha256,
                "variant": kind,
            },
            300,
        )
        prefix = "annotation-releases" if release else "annotations"
        audit(
            session,
            actor,
            "annotation.preview.issue",
            row.id,
            {"task_id": str(row.task_id), "variant": kind, "image_sha256": rendition.image_sha256},
        )
        return schema.AnnotationPreviewLink(
            annotation_id=material.id,
            rendition_id=row.rendition_id,
            kind=kind,
            url=f"/{prefix}/{row.id}/content?signature={signature}",
            image=rendition.image,
            mapping=rendition.mapping,
        )

    @router.get(
        "/annotations/{annotation_id}/preview",
        name="evidence_annotation_preview",
        response_model=Result,
    )
    async def preview(annotation_id: UUID, ctx=Depends(context, scope="function")):
        row = await annotations.material_row(ctx[0], ctx[1], annotation_id)
        return result("evidence annotation preview", await preview_link(ctx[0], ctx[1], row))

    @router.get(
        "/annotations/{annotation_id}/releases",
        name="evidence_annotation_releases",
        response_model=Result,
    )
    async def releases(
        annotation_id: UUID,
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        data, items = await annotations.list_releases(
            ctx[0],
            ctx[1],
            annotation_id,
            schema.AnnotationListQuery(cursor=cursor, limit=limit),
            settings,
        )
        return result("evidence annotation releases", data, items)

    @router.post(
        "/annotations/{annotation_id}/releases",
        name="evidence_annotation_release_retry",
        response_model=Result,
    )
    async def retry(annotation_id: UUID, request: Request, ctx=Depends(context, scope="function")):
        await annotations.material_row(ctx[0], ctx[1], annotation_id)
        body = await bounded_input(request, TypeAdapter(schema.AnnotationReleaseRetry))
        return result(
            "evidence annotation release retry",
            await annotations.retry_release(ctx[0], ctx[1], annotation_id, body, queue),
        )

    @router.get(
        "/annotation-releases/{release_id}/preview",
        name="evidence_annotation_release_preview",
        response_model=Result,
    )
    async def release_preview(release_id: UUID, ctx=Depends(context, scope="function")):
        row = await annotations.release_row(ctx[0], ctx[1], release_id)
        return result(
            "evidence annotation release preview",
            await preview_link(ctx[0], ctx[1], row, release=True),
        )

    async def read_content(session, actor, ident, signature, *, release=False):
        row = (
            await annotations.release_row(session, actor, ident)
            if release
            else await annotations.material_row(session, actor, ident)
        )
        kind = "release" if release else "candidate"
        rendition = await session.get(ScreenshotRendition, row.rendition_id)
        if rendition is None:
            raise not_found()
        try:
            value = tokens.open(signature)
        except ServiceError:
            raise not_found() from None
        expected = {
            "purpose": "annotation-preview",
            "org_id": str(actor.org_id),
            "object_id": str(row.id),
            "rendition_id": str(row.rendition_id),
            "sha256": rendition.image_sha256,
            "variant": kind,
        }
        if any(value.get(key) != item for key, item in expected.items()):
            raise not_found()
        if release:
            await annotations.validate_release(session, actor, row, storage=storage)
        asset = await session.get(ScreenshotAsset, row.asset_id)
        png = await screenshots.read_rendition(storage, asset, rendition)
        audit(
            session,
            actor,
            "annotation.preview.read",
            row.id,
            {"task_id": str(row.task_id), "variant": kind, "image_sha256": rendition.image_sha256},
        )
        return Response(
            png,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/annotations/{annotation_id}/content", name="evidence_annotation_content")
    async def content(annotation_id: UUID, signature: str, ctx=Depends(context, scope="function")):
        return await read_content(ctx[0], ctx[1], annotation_id, signature)

    @router.get(
        "/annotation-releases/{release_id}/content", name="evidence_annotation_release_content"
    )
    async def release_content(
        release_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        return await read_content(ctx[0], ctx[1], release_id, signature, release=True)

    return router
