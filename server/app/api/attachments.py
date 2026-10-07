"""Bounded, authenticated attachment archive routes; bytes never use bearer links."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import TypeAdapter, ValidationError

from app.api.common import attachment, check_signature, result, signed_link
from app.core.errors import ServiceError
from app.schemas import attachment_contracts as c
from app.schemas.contracts import Result
from app.services import attachments as service
from app.services.versioned import audit

PRIVACY = TypeAdapter(c.AttachmentPrivacyInput)


def create_router(context, settings, storage, crypto, queue=None):
    router = APIRouter()

    async def configured(request: Request, ctx=Depends(context, scope="function")):
        from app.core.errors import not_found

        if getattr(
            request.state, "contract_version", None
        ) != "4.0" and not request.url.path.endswith(("/download", "/preview")):
            raise not_found()
        session, actor = ctx
        session.info["attachment_settings"] = settings
        session.info["attachment_queue"] = queue
        return session, actor

    def output(command, data=None, items=None):
        value = result(command, data, items, service.WARNINGS)
        value["cost"]["billing_currency"] = settings.billing_currency
        if len(json.dumps(value, ensure_ascii=True).encode()) > c.LIST_JSON_LIMIT:
            raise ServiceError(
                "attachment_response_limit", "Response exceeds metadata limit", 413, 2
            )
        return value

    async def body(request, model):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > c.HTTP_JSON_LIMIT:
                raise ServiceError("attachment_input_limit", "Metadata exceeds input limit", 413, 2)
        try:
            return (
                model.model_validate_json(content)
                if hasattr(model, "model_validate_json")
                else model.validate_json(content)
            )
        except (ValidationError, ValueError):
            raise ServiceError("invalid_input", "Invalid attachment metadata", 422, 2) from None

    async def upload(request, ctx, identifier=None):
        session, actor = ctx
        await service.access(session, actor, "attachment:write")
        from app.api.attachment_upload import receive

        metadata, name, content = await receive(request, settings.max_upload_bytes)
        try:
            parsed = (c.AttachmentRevise if identifier else c.AttachmentCreate).model_validate_json(
                metadata
            )
            uploads = [c.AttachmentUploadBytes(name=name, content=content)]
        except ValidationError:
            raise ServiceError(
                "invalid_input", "Invalid attachment metadata or PDF", 422, 2
            ) from None
        receipt = await service.upload(
            session, actor, parsed, uploads, storage, attachment_id=identifier
        )
        return output(
            "resource attachment revise" if identifier else "resource attachment upload", receipt
        )

    def page_query(
        cursor: str | None = Query(None, min_length=1, max_length=2048),
        limit: int = Query(50, ge=1, le=100),
        history: bool = False,
    ):
        # Query strings need transport coercion before the strict runtime model.
        return c.PageQuery(cursor=cursor, limit=limit, history=history)

    def archive_query(
        kind: c.AttachmentKind | None = None, page: c.PageQuery = Depends(page_query)
    ):
        return c.AttachmentListQuery(**page.model_dump(), kind=kind)

    def source_query(selection_id: UUID | None = None, page: c.PageQuery = Depends(page_query)):
        return c.AttachmentSourceListQuery(**page.model_dump(), selection_id=selection_id)

    @router.post(
        "/management/resources/attachments/query",
        response_model=Result,
        name="resource_attachment_browse",
    )
    async def browse_route(request: Request, ctx=Depends(configured)):
        from app.core.errors import not_found

        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        query = await body(request, c.AttachmentBrowseQuery)
        return output(
            "resource attachment browse", *await service.list_archives(ctx[0], ctx[1], query)
        )

    @router.post("/resources/attachments", response_model=Result, name="resource_attachment_upload")
    async def upload_route(request: Request, ctx=Depends(configured)):
        return await upload(request, ctx)

    @router.post(
        "/resources/attachments/{attachment_id}/revisions",
        response_model=Result,
        name="resource_attachment_revise",
    )
    async def revise_route(attachment_id: UUID, request: Request, ctx=Depends(configured)):
        return await upload(request, ctx, attachment_id)

    @router.get("/resources/attachments", response_model=Result, name="resource_attachment_list")
    async def list_route(
        query: c.AttachmentListQuery = Depends(archive_query), ctx=Depends(configured)
    ):
        return output(
            "resource attachment list", *await service.list_archives(ctx[0], ctx[1], query)
        )

    @router.get(
        "/resources/attachments/revisions/{revision_id}",
        response_model=Result,
        name="resource_attachment_revision_show",
    )
    async def revision_route(revision_id: UUID, ctx=Depends(configured)):
        return output(
            "resource attachment revision show", await service.revision(ctx[0], ctx[1], revision_id)
        )

    @router.get(
        "/resources/attachments/{attachment_id}",
        response_model=Result,
        name="resource_attachment_show",
    )
    async def show_route(attachment_id: UUID, ctx=Depends(configured)):
        return output("resource attachment show", await service.show(ctx[0], ctx[1], attachment_id))

    @router.get(
        "/resources/attachments/{attachment_id}/revisions",
        response_model=Result,
        name="resource_attachment_history",
    )
    async def history_route(
        attachment_id: UUID, query: c.PageQuery = Depends(page_query), ctx=Depends(configured)
    ):
        return output(
            "resource attachment history",
            *await service.revisions(ctx[0], ctx[1], attachment_id, query),
        )

    @router.post(
        "/resources/attachments/{attachment_id}/assignment",
        response_model=Result,
        name="resource_attachment_assign",
    )
    async def assignment_route(attachment_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "resource attachment assign",
            await service.assign(
                ctx[0], ctx[1], attachment_id, await body(request, c.AttachmentAssignment)
            ),
        )

    @router.post(
        "/resources/attachments/{attachment_id}/deactivate",
        response_model=Result,
        name="resource_attachment_deactivate",
    )
    async def deactivate_route(attachment_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "resource attachment deactivate",
            await service.deactivate_archive(
                ctx[0], ctx[1], attachment_id, await body(request, c.AttachmentDeactivate)
            ),
        )

    @router.post(
        "/resources/attachments/revisions/{revision_id}/reviews",
        response_model=Result,
        name="resource_attachment_review",
    )
    async def review_route(revision_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "resource attachment review",
            await service.review(
                ctx[0], ctx[1], revision_id, await body(request, c.AttachmentReviewInput)
            ),
        )

    @router.get(
        "/resources/attachments/revisions/{revision_id}/reviews",
        response_model=Result,
        name="resource_attachment_reviews",
    )
    async def reviews_route(
        revision_id: UUID, query: c.PageQuery = Depends(page_query), ctx=Depends(configured)
    ):
        return output(
            "resource attachment reviews",
            *await service.reviews(ctx[0], ctx[1], revision_id, query),
        )

    @router.post(
        "/resources/profiles/revisions/{profile_revision_id}/attachments",
        response_model=Result,
        name="resource_profile_attachment_link",
    )
    async def link_route(profile_revision_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "resource profile attachment link",
            await service.link_profile(
                ctx[0],
                ctx[1],
                profile_revision_id,
                await body(request, c.ProfileAttachmentLinkInput),
            ),
        )

    @router.get(
        "/resources/profiles/revisions/{profile_revision_id}/attachments",
        response_model=Result,
        name="resource_profile_attachment_list",
    )
    async def links_route(
        profile_revision_id: UUID, query: c.PageQuery = Depends(page_query), ctx=Depends(configured)
    ):
        return output(
            "resource profile attachment list",
            *await service.profile_links(ctx[0], ctx[1], profile_revision_id, query),
        )

    @router.post(
        "/profile-attachment-links/{link_id}/deactivate",
        response_model=Result,
        name="resource_profile_attachment_deactivate",
    )
    async def unlink_route(link_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "resource profile attachment deactivate",
            await service.deactivate_link(
                ctx[0], ctx[1], link_id, await body(request, c.AttachmentDeactivate)
            ),
        )

    @router.post(
        "/tasks/{task_id}/attachments", response_model=Result, name="task_attachment_select"
    )
    async def select_route(task_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "task attachment select",
            await service.select(
                ctx[0], ctx[1], task_id, await body(request, c.TaskAttachmentSelect)
            ),
        )

    @router.get("/tasks/{task_id}/attachments", response_model=Result, name="task_attachment_list")
    async def selections_route(
        task_id: UUID, query: c.PageQuery = Depends(page_query), ctx=Depends(configured)
    ):
        return output(
            "task attachment list", *await service.selections(ctx[0], ctx[1], task_id, query)
        )

    @router.post(
        "/task-attachments/{selection_id}/deactivate",
        response_model=Result,
        name="task_attachment_deactivate",
    )
    async def deselect_route(selection_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "task attachment deactivate",
            await service.deactivate_selection(
                ctx[0], ctx[1], selection_id, await body(request, c.AttachmentDeactivate)
            ),
        )

    @router.post(
        "/tasks/{task_id}/attachment-sources",
        response_model=Result,
        name="evidence_attachment_source_create",
    )
    async def source_create_route(task_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "evidence attachment source create",
            await service.create_source(
                ctx[0], ctx[1], task_id, await body(request, c.AttachmentSourceCreate), storage
            ),
        )

    @router.get(
        "/tasks/{task_id}/attachment-sources",
        response_model=Result,
        name="evidence_attachment_source_list",
    )
    async def sources_route(
        task_id: UUID,
        query: c.AttachmentSourceListQuery = Depends(source_query),
        ctx=Depends(configured),
    ):
        return output(
            "evidence attachment source list",
            *await service.sources(ctx[0], ctx[1], task_id, query),
        )

    @router.get(
        "/attachment-sources/{source_id}",
        response_model=Result,
        name="evidence_attachment_source_show",
    )
    async def source_route(source_id: UUID, ctx=Depends(configured)):
        return output(
            "evidence attachment source show", await service.source(ctx[0], ctx[1], source_id)
        )

    @router.post(
        "/attachment-sources/{source_id}/privacy",
        response_model=Result,
        name="evidence_attachment_privacy_review",
    )
    async def privacy_route(source_id: UUID, request: Request, ctx=Depends(configured)):
        return output(
            "evidence attachment privacy review",
            await service.privacy_review(
                ctx[0], ctx[1], source_id, await body(request, PRIVACY), storage
            ),
        )

    def private_response(content, media_type, name):
        response = attachment(content, media_type, name)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        return response

    async def issue(ctx, path, kind, identifier, *, source=False, history=False, **ids):
        session, actor = ctx
        if source:
            from app.services.attachment_pages import source_row

            row = await source_row(session, actor, identifier, raw=True)
            if not history:
                await service.selection_context(session, actor, row.task_attachment_id)
            ids["history"] = int(history)
        else:
            await service.revision(session, actor, identifier)
            root, _, _, _ = await service.revision_parts(session, identifier)
            ids["state_version"] = root.state_version
        audit(session, actor, "attachment.download.issue", identifier, {"kind": kind})
        await session.flush()
        return signed_link(crypto, path, kind, actor.org_id, id=identifier, **ids)

    @router.get(
        "/resources/attachments/revisions/{revision_id}/file/download-link",
        response_model=Result,
        name="resource_attachment_file_download",
    )
    async def original_link(revision_id: UUID, ctx=Depends(configured)):
        path = f"/resources/attachments/revisions/{revision_id}/file/download"
        return output(
            "resource attachment file download",
            await issue(ctx, path, "attachment-original", revision_id),
        )

    @router.get(
        "/resources/attachments/revisions/{revision_id}/file/download",
        name="resource_attachment_file_bytes",
    )
    async def original_bytes(revision_id: UUID, signature: str, ctx=Depends(configured)):
        root, _, _, _ = await service.revision_parts(ctx[0], revision_id)
        check_signature(
            crypto,
            signature,
            "attachment-original",
            ctx[1].org_id,
            id=revision_id,
            state_version=root.state_version,
        )
        content, file = await service.read_original(ctx[0], ctx[1], revision_id, storage)
        return private_response(content, file.media_type, file.name)

    @router.get(
        "/resources/attachments/revisions/{revision_id}/parts/{ordinal}/download-link",
        response_model=Result,
        name="resource_attachment_part_download",
    )
    async def part_link(revision_id: UUID, ordinal: int, ctx=Depends(configured)):
        await service.revision(ctx[0], ctx[1], revision_id)
        service.fail(
            "attachment_upload_mode_not_enabled", "Part uploads and downloads are not enabled"
        )

    @router.get(
        "/resources/attachments/revisions/{revision_id}/parts/{ordinal}/download",
        name="resource_attachment_part_bytes",
    )
    async def part_bytes(revision_id: UUID, ordinal: int, signature: str, ctx=Depends(configured)):
        check_signature(
            crypto, signature, "attachment-part", ctx[1].org_id, id=revision_id, part=ordinal
        )
        await service.read_original(ctx[0], ctx[1], revision_id, storage, part_ordinal=ordinal)

    @router.get(
        "/resources/attachments/revisions/{revision_id}/pages/{page}/preview-link",
        response_model=Result,
        name="resource_attachment_page_preview",
    )
    async def page_link(
        revision_id: UUID, page: int, zoom: int = Query(1, ge=1, le=2), ctx=Depends(configured)
    ):
        revision = await service.revision(ctx[0], ctx[1], revision_id)
        if not 1 <= page <= revision["page_count"]:
            service.fail("invalid_source_page", "Page is outside the original", 400)
        path = f"/resources/attachments/revisions/{revision_id}/pages/{page}/preview"
        value = await issue(ctx, path, "attachment-page", revision_id, page=page, zoom=zoom)
        value["url"] += f"&zoom={zoom}"
        return output("resource attachment page preview", value)

    @router.get(
        "/resources/attachments/revisions/{revision_id}/pages/{page}/preview",
        name="resource_attachment_page_bytes",
    )
    async def page_bytes(
        revision_id: UUID,
        page: int,
        signature: str,
        zoom: int = Query(1, ge=1, le=2),
        ctx=Depends(configured),
    ):
        root, _, _, _ = await service.revision_parts(ctx[0], revision_id)
        check_signature(
            crypto,
            signature,
            "attachment-page",
            ctx[1].org_id,
            id=revision_id,
            page=page,
            zoom=zoom,
            state_version=root.state_version,
        )
        png = await service.preview_page(ctx[0], ctx[1], revision_id, page, zoom, storage)
        return private_response(png, "image/png", f"attachment-page-{page}.png")

    @router.get(
        "/attachment-sources/{source_id}/preview/download-link",
        response_model=Result,
        name="evidence_attachment_source_preview",
    )
    async def source_link(source_id: UUID, history: bool = False, ctx=Depends(configured)):
        path = f"/attachment-sources/{source_id}/preview/download"
        value = await issue(ctx, path, "attachment-source", source_id, source=True, history=history)
        if history:
            value["url"] += "&history=true"
        return output("evidence attachment source preview", value)

    @router.get(
        "/attachment-sources/{source_id}/preview/download", name="evidence_attachment_source_bytes"
    )
    async def source_bytes(
        source_id: UUID, signature: str, history: bool = False, ctx=Depends(configured)
    ):
        check_signature(
            crypto,
            signature,
            "attachment-source",
            ctx[1].org_id,
            id=source_id,
            history=int(history),
        )
        png, descriptor = await service.read_source(
            ctx[0], ctx[1], source_id, storage, history=history
        )
        return private_response(png, "image/png", descriptor.name)

    return router
