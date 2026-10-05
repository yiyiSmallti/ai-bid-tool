"""Human-only export API; a render candidate has no download route."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError
from sqlalchemy import select

from app.core.errors import ServiceError, not_found
from app.models.entities import Job, Task
from app.models.exports import Export
from app.schemas.contracts import Result
from app.schemas.export_contracts import (
    ExportBindingCreate,
    ExportDownloadLink,
    ExportPrepare,
    ExportRelease,
)
from app.services import exports, page_previews
from app.services.versioned import audit


def create_router(context, db, storage, queue, settings, crypto):
    router = APIRouter()
    storage = exports.ExportStorage(storage)

    def result(command, data=None, items=None, *, partial=False):
        return Result(
            ok=not partial,
            command=command,
            data=data if data is not None else {},
            items=items if items is not None else [],
        )

    @router.post("/export-template-bindings", name="export_binding_create", response_model=Result)
    async def binding_create(body: ExportBindingCreate, ctx=Depends(context, scope="function")):
        return result(
            "export binding create", await exports.create_binding(ctx[0], ctx[1], body, storage)
        )

    @router.get("/export-template-bindings", name="export_binding_list", response_model=Result)
    async def binding_list(template_revision_id: UUID, ctx=Depends(context, scope="function")):
        items = await exports.list_bindings(ctx[0], ctx[1], template_revision_id)
        return result(
            "export binding list", {"template_revision_id": str(template_revision_id)}, items
        )

    @router.post("/tasks/{task_id}/export-runs", name="export_prepare", response_model=Result)
    async def prepare(task_id: UUID, body: ExportPrepare, ctx=Depends(context, scope="function")):
        session, actor = ctx
        data, job = await exports.submit_export(session, actor, task_id, body, storage, settings)
        if body.dry_run and not data["ready"]:
            payload = result("export prepare", data, partial=True).model_dump(mode="json")
            payload["data"]["error"] = {
                "code": "export_blocked",
                "message": "Export preflight has blocking issues",
                "exit_code": 2,
            }
            return JSONResponse(status_code=400, content=payload)
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                raise ServiceError(
                    "queue_unavailable", "Export job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result("export prepare", data)

    @router.get("/export-runs/{run_id}", name="export_run_show", response_model=Result)
    async def run_show(run_id: UUID, ctx=Depends(context, scope="function")):
        actor, run = await exports.get_run(ctx[0], ctx[1], run_id)
        return result("export run show", await exports.run_view(ctx[0], actor, run, storage))

    @router.post("/export-runs/{run_id}/release", name="export_release", response_model=Result)
    async def release(run_id: UUID, body: ExportRelease, ctx=Depends(context, scope="function")):
        view = await exports.release(ctx[0], ctx[1], run_id, body, storage)
        return result("export release", view, partial=view["completion"] == "partial")

    @router.get("/tasks/{task_id}/exports", name="export_list", response_model=Result)
    async def list_exports(task_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        actor = await exports.human_access(session, actor)
        if await session.get(Task, task_id) is None:
            raise not_found()
        rows = await session.scalars(
            select(Export).where(Export.task_id == task_id).order_by(Export.released_at, Export.id)
        )
        items = [await exports.export_view(session, actor, row, storage) for row in rows]
        return result("export list", {"task_id": str(task_id)}, items)

    @router.get("/exports/{export_id}", name="export_show", response_model=Result)
    async def show(export_id: UUID, ctx=Depends(context, scope="function")):
        actor, row = await exports.get_export(ctx[0], ctx[1], export_id)
        return result("export show", await exports.export_view(ctx[0], actor, row, storage))

    @router.post("/exports/{export_id}/preview", name="export_preview_open", response_model=Result)
    async def open_preview(
        export_id: UUID, retry: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, job = await page_previews.submit_export_preview(
            session, actor, export_id, storage, settings, retry=retry
        )
        if job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                raise ServiceError(
                    "queue_unavailable", "Preview job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result("export preview", data)

    @router.get("/exports/{export_id}/preview", name="export_preview_show", response_model=Result)
    async def show_preview(export_id: UUID, ctx=Depends(context, scope="function")):
        return result(
            "export preview",
            await page_previews.show_export_preview(ctx[0], ctx[1], export_id, storage),
        )

    @router.get("/exports/{export_id}/preview/pages/{page}", name="export_preview_page")
    async def preview_page(
        export_id: UUID,
        page: int,
        zoom: int = Query(1, ge=1, le=2),
        ctx=Depends(context, scope="function"),
    ):
        png = await page_previews.export_page(
            ctx[0], ctx[1], export_id, page, zoom, storage, settings
        )
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.get("/exports/{export_id}/provenance", name="export_provenance", response_model=Result)
    async def show_provenance(export_id: UUID, ctx=Depends(context, scope="function")):
        return result("export provenance", await exports.provenance(ctx[0], ctx[1], export_id))

    @router.get(
        "/exports/{export_id}/download-link", name="export_download_link", response_model=Result
    )
    async def download_link(export_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        actor, row = await exports.get_export(session, actor, export_id)
        actor, _ = await exports.download_gate(session, actor, row, storage)
        signature = crypto.issue(
            {
                "kind": "export_docx",
                "org_id": str(actor.org_id),
                "export_id": str(row.id),
                "file_sha256": row.file_sha256,
            },
            300,
        )
        data = ExportDownloadLink.model_validate(
            {
                "export_id": row.id,
                "file": exports.file_view(row),
                "url": f"/exports/{row.id}/download?signature={signature}",
            }
        ).model_dump(mode="json")
        audit(
            session,
            actor,
            "export.download_link_issued",
            row.id,
            {
                "actor_kind": actor.actor_kind,
                "export_id": str(row.id),
                "run_id": str(row.run_id),
                "file_sha256": row.file_sha256,
            },
        )
        return result("export download", data)

    @router.get("/exports/{export_id}/download", name="export_download")
    async def download(export_id: UUID, signature: str, ctx=Depends(context, scope="function")):
        session, actor = ctx
        actor, row = await exports.get_export(session, actor, export_id)
        try:
            signed = crypto.open(signature)
        except ServiceError:
            raise not_found() from None
        if (
            signed.get("kind") != "export_docx"
            or signed.get("org_id") != str(actor.org_id)
            or signed.get("export_id") != str(row.id)
            or signed.get("file_sha256") != row.file_sha256
        ):
            raise not_found()
        actor, content = await exports.download_gate(session, actor, row, storage)
        audit(
            session,
            actor,
            "export.download_served",
            row.id,
            {
                "actor_kind": actor.actor_kind,
                "export_id": str(row.id),
                "run_id": str(row.run_id),
                "file_sha256": row.file_sha256,
                "delivery": "attempt",
            },
        )
        return Response(
            content,
            media_type=row.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{exports.file_view(row)["name"]}"'
            },
        )

    return router
